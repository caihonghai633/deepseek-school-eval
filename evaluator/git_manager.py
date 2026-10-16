"""AE-01 评测产物的 Git/GitHub 归档适配器。

该模块只通过 ``subprocess.run`` 调用本地 Git，不依赖 GitPython。所有命令均以
参数数组执行（不经过 shell），并捕获 Git 未安装、目录未初始化、身份未配置、
网络离线和 push 失败等异常。归档失败只返回结构化状态，绝不向 Runner/Analyzer
抛出未处理异常。

配置优先级为：调用方 ``config['git']`` > 环境变量 > 安全默认值。默认自动提交
开启、自动 push 关闭；需要推送时显式设置 ``git.auto_push: true`` 或
``AE01_GIT_AUTO_PUSH=1``。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("runs/", "reports/")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _settings(config: dict[str, Any] | None) -> dict[str, Any]:
    configured = dict((config or {}).get("git", {}) or {})
    paths = configured.get("paths", DEFAULT_PATHS)
    if isinstance(paths, str):
        paths = [paths]
    return {
        "enabled": bool(configured.get("enabled", _env_bool("AE01_GIT_ENABLED", True))),
        "auto_commit": bool(configured.get("auto_commit", _env_bool("AE01_GIT_AUTO_COMMIT", True))),
        "auto_push": bool(configured.get("auto_push", _env_bool("AE01_GIT_AUTO_PUSH", False))),
        "remote": str(configured.get("remote", os.getenv("AE01_GIT_REMOTE", "origin"))),
        "branch": str(configured.get("branch", os.getenv("AE01_GIT_BRANCH", "main"))),
        "paths": [str(path) for path in paths],
        "timeout_seconds": float(configured.get("timeout_seconds", os.getenv("AE01_GIT_TIMEOUT_SECONDS", "30"))),
    }


def _command(root: Path, args: Sequence[str], *, timeout_seconds: float) -> dict[str, Any]:
    """执行一条 Git 命令并统一返回，不让任何系统异常冒泡。"""

    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_seconds),
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError:
        return {"command": command, "returncode": None, "ok": False, "stdout": "", "stderr": "git_not_found"}
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "ok": False,
            "stdout": str(exc.stdout or "").strip(),
            "stderr": "git_command_timeout",
        }
    except (OSError, ValueError) as exc:
        return {"command": command, "returncode": None, "ok": False, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def build_commit_message(*, version: str, pass_rate: str | float, run_id: str) -> str:
    """生成稳定、可检索的结构化评测提交信息。"""

    if isinstance(pass_rate, (int, float)):
        rendered_rate = f"{float(pass_rate) * 100:.2f}%"
    else:
        rendered_rate = str(pass_rate).replace("\n", " ").strip()
    safe_version = str(version).replace("\n", " ").strip() or "unknown"
    safe_run_id = str(run_id).replace("\n", " ").strip() or "unknown-run"
    return f"chore(eval): run evaluation for [{safe_version}] - pass_rate: {rendered_rate} (run_id: {safe_run_id})"


def archive_evaluation(
    *,
    root: str | Path = ROOT,
    version: str = "unknown",
    pass_rate: str | float = "n/a",
    run_id: str = "unknown-run",
    config: dict[str, Any] | None = None,
    paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """暂存评测产物、提交并按开关推送；任何失败均降级为结构化结果。"""

    repo_root = Path(root).resolve()
    settings = _settings(config)
    result: dict[str, Any] = {
        "enabled": settings["enabled"],
        "auto_commit": settings["auto_commit"],
        "auto_push": settings["auto_push"],
        "status": "disabled" if not settings["enabled"] else "pending",
        "root": repo_root.as_posix(),
        "version": version,
        "run_id": run_id,
        "commit_message": build_commit_message(version=version, pass_rate=pass_rate, run_id=run_id),
        "commands": [],
        "errors": [],
        "committed": False,
        "pushed": False,
        "commit_hash": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not settings["enabled"]:
        return result

    probe = _command(repo_root, ["rev-parse", "--show-toplevel"], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(probe)
    if not probe["ok"]:
        error = probe.get("stderr") or "not_a_git_repository"
        result["errors"].append(error)
        result["status"] = "not_a_git_repository" if error != "git_not_found" else "git_unavailable"
        return result
    result["git_root"] = probe.get("stdout") or repo_root.as_posix()

    selected_paths = [str(path) for path in (paths or settings["paths"])]
    status = _command(repo_root, ["status", "--porcelain", "--", *selected_paths], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(status)
    if not status["ok"]:
        result["errors"].append(status.get("stderr") or "git_status_failed")
        result["status"] = "status_failed"
        return result
    result["changed_files"] = [line[3:] if len(line) >= 4 else line for line in status.get("stdout", "").splitlines() if line.strip()]
    if not result["changed_files"]:
        result["status"] = "no_changes"
        return result
    if not settings["auto_commit"]:
        result["status"] = "changes_detected_commit_disabled"
        return result

    add = _command(repo_root, ["add", "--", *selected_paths], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(add)
    if not add["ok"]:
        result["errors"].append(add.get("stderr") or "git_add_failed")
        result["status"] = "add_failed"
        return result

    staged = _command(repo_root, ["diff", "--cached", "--name-only", "--", *selected_paths], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(staged)
    if not staged["ok"] or not staged.get("stdout"):
        if not staged["ok"]:
            result["errors"].append(staged.get("stderr") or "git_staged_diff_failed")
        result["status"] = "no_staged_changes"
        return result
    result["staged_files"] = staged["stdout"].splitlines()

    commit = _command(repo_root, ["commit", "-m", result["commit_message"]], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(commit)
    if not commit["ok"]:
        result["errors"].append(commit.get("stderr") or "git_commit_failed")
        result["status"] = "commit_failed"
        return result
    result["committed"] = True
    result["status"] = "committed"
    revision = _command(repo_root, ["rev-parse", "HEAD"], timeout_seconds=settings["timeout_seconds"])
    result["commands"].append(revision)
    if revision["ok"]:
        result["commit_hash"] = revision.get("stdout")

    if settings["auto_push"]:
        push = _command(
            repo_root,
            ["push", settings["remote"], settings["branch"]],
            timeout_seconds=settings["timeout_seconds"],
        )
        result["commands"].append(push)
        if push["ok"]:
            result["pushed"] = True
            result["status"] = "pushed"
        else:
            result["errors"].append(push.get("stderr") or "git_push_failed")
            result["status"] = "push_failed"
    return result


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="归档 AE-01 评测产物到 Git")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--version", default="unknown")
    parser.add_argument("--pass-rate", default="n/a")
    parser.add_argument("--run-id", default="manual-archive")
    parser.add_argument("--push", action="store_true", help="本次调用允许执行 git push")
    args = parser.parse_args()
    config = {"git": {"auto_push": args.push}}
    print(json.dumps(archive_evaluation(root=args.root, version=args.version, pass_rate=args.pass_rate, run_id=args.run_id, config=config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
