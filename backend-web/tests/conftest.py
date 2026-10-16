"""pytest 全局配置：把仓库根目录与 backend-web 加入 sys.path。

backend-web 的服务代码以 `app.*` 为包名、`common.*` 为仓库根目录的共享包，
测试从 backend-web 目录运行时默认两者都不可导入，这里统一补齐。
"""
import sys
from pathlib import Path

BACKEND_WEB = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_WEB.parent

for p in (str(REPO_ROOT), str(BACKEND_WEB)):
    if p not in sys.path:
        sys.path.insert(0, p)
