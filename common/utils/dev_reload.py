"""
开发态热重载参数。

仅当环境变量 XY_DEV_RELOAD=1 时返回 uvicorn reload 配置。
日常启动脚本不设置该变量，行为与原来一致。
"""
from __future__ import annotations

import os
from pathlib import Path


def uvicorn_reload_kwargs(service_file: Path) -> dict:
    """根据环境变量生成 uvicorn.run 的 reload 参数。"""
    flag = os.getenv("XY_DEV_RELOAD", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return {"reload": False}

    service_dir = service_file.resolve().parent
    project_root = service_dir.parent
    # 这些目录里的文件变化不应触发重启（虚拟环境、日志）
    excludes = [
        str(path)
        for path in (service_dir / ".venv", service_dir / "logs")
        if path.is_dir()
    ]
    return {
        "reload": True,
        "reload_dirs": [str(service_dir), str(project_root / "common")],
        "reload_excludes": excludes,
    }
