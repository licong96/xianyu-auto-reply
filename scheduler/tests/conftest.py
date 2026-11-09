"""pytest 全局配置：把仓库根目录与 scheduler 加入 sys.path。"""
import sys
from pathlib import Path

SCHEDULER = Path(__file__).resolve().parents[1]
REPO_ROOT = SCHEDULER.parent

for p in (str(REPO_ROOT), str(SCHEDULER)):
    if p not in sys.path:
        sys.path.insert(0, p)
