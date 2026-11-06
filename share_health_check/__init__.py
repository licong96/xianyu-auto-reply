"""网盘分享内容存活巡检 - 独立可运行（DrissionPage 子进程模式）。

DrissionPage 只装在 paper_crawler 独立 venv（与其他服务隔离的既定模式），
scheduler 的 share_health_check 任务以子进程方式调用本包：

    paper_crawler/.venv/bin/python -m share_health_check check --batch-size 50

职责（只读 + 记录，不做任何自动处理）：
1. 从 xy_material_pool_pan_links 取一批待巡检链接（续期后的新地址优先插队），
   真实浏览器逐个打开分享页，判断 valid / invalid / empty / error / risk；
2. 回写 last_check_status / last_check_at，每条结果落 xy_share_check_logs；
3. invalid / empty / risk 进入通知清单（由调用方 scheduler 任务发通知，人工处理）；
4. 条目之间随机等待 2~3 秒，单页最长 15 秒，防网盘风控。

退出前输出一行 RESULT_JSON: 前缀的 JSON 摘要，供父进程解析。

# CUSTOM: 网盘分享自动续期与内容存活巡检（新增包，合并作者更新零冲突）
"""
