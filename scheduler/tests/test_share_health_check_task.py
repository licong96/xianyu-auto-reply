"""分享存活巡检 scheduler 任务的纯函数单测。

只测不依赖数据库/浏览器的解析逻辑：_parse_result 从子进程 stdout
取最后一行 RESULT_JSON: 摘要。

运行：cd scheduler && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json

from app.services.scheduler.share_health_check_task import _parse_result


def test_parse_success_result():
    stats = {"success": True, "checked": 3, "valid": 2, "invalid": 1, "notify": []}
    stdout = "一些过程日志\nRESULT_JSON:" + json.dumps(stats, ensure_ascii=False) + "\n"
    assert _parse_result(stdout) == stats


def test_parse_takes_last_result_line():
    first = {"success": True, "checked": 1}
    last = {"success": True, "checked": 2}
    stdout = (
        "RESULT_JSON:" + json.dumps(first) + "\n"
        "后续日志\n"
        "RESULT_JSON:" + json.dumps(last) + "\n"
    )
    assert _parse_result(stdout) == last


def test_no_result_line():
    result = _parse_result("只有普通日志\n没有结果行")
    assert result["success"] is False
    assert "RESULT_JSON" in result["error"]


def test_broken_json():
    result = _parse_result("RESULT_JSON:{not-valid-json")
    assert result["success"] is False
    assert "解析失败" in result["error"]
