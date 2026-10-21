"""手动实测网盘互传服务。

用法：cd backend-web && .venv/bin/python test_sync_manual.py <远程路径> [baidu|quark] [dir]
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common.core.config  # noqa: F401  加载根 .env（KUAKE_BIN / BAIDUPCS_BIN 等）

from app.services.pan import pan_sync_service


async def main() -> None:
    remote = sys.argv[1] if len(sys.argv) > 1 else "/闲鱼/资料池/多盘测试/多盘合并测试.txt"
    src_type = sys.argv[2] if len(sys.argv) > 2 else "baidu"
    is_dir = len(sys.argv) > 3 and sys.argv[3] == "dir"

    task = pan_sync_service.start_sync(src_type, remote, is_dir)
    print(f"任务已启动: {task['task_id']}  {task['src_type']} -> {task['dst_type']}  {remote}")
    while True:
        await asyncio.sleep(1)
        t = pan_sync_service.get_task(task["task_id"])
        print(
            f"  status={t['status']} processed={t['processed']}/{t['total']} "
            f"uploaded={t['uploaded']} skipped={t['skipped']} failed={t['failed']} current={t['current']}"
        )
        if t["status"] in ("done", "failed"):
            print("errors:", t["errors"])
            break

    staging = Path(task["staging_dir"])
    print(f"暂存目录已清理: {not staging.exists()} ({staging})")


if __name__ == "__main__":
    asyncio.run(main())
