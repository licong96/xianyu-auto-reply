"""验证 11-04 以来部署涉及的库表变更是否到位"""
import asyncio

from sqlalchemy import text

from common.db.session import async_session_maker


async def main():
    async with async_session_maker() as s:
        # 1. 新表
        r = await s.execute(text("SHOW TABLES LIKE 'xy_share_check_logs'"))
        print("xy_share_check_logs 表:", "存在" if r.first() else "缺失")

        # 2. 新字段
        for table, cols in {
            "xy_material_pool": ["item_live_status", "item_live_checked_at", "item_live_error"],
            "xy_material_pool_pan_links": ["last_check_status", "last_check_at", "last_renew_at", "check_priority"],
            "xy_auto_reply_message_logs": ["prompt_snapshot"],
        }.items():
            r = await s.execute(text(f"SHOW COLUMNS FROM {table}"))
            existing = {row[0] for row in r.fetchall()}
            missing = [c for c in cols if c not in existing]
            print(f"{table}: ", "OK" if not missing else f"缺失 {missing}")

        # 3. 新定时任务种子
        r = await s.execute(text("SELECT task_code, task_name, interval_seconds, enabled FROM xy_scheduled_tasks WHERE task_code IN ('material_pool_live','share_renew','share_health_check')"))
        rows = r.fetchall()
        for row in rows:
            print("定时任务:", row)


asyncio.run(main())
