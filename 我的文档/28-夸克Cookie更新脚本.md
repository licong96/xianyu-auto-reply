# 夸克 Cookie 更新脚本（扫码登录自动写回）

- 状态：**已实施并实测通过**（2026-10-21，Windows 本机）
- 依赖：[08-夸克网盘桥](./08-夸克网盘桥.md)、[27-网盘互传同步方案](./27-网盘互传同步方案.md)
- 脚本位置：`scripts/update_quark_cookie.py`

## 本文档解决什么

夸克网盘的下载走 **OSS 回调鉴权**：CDN 边缘节点会回调夸克服务器验证请求携带的登录态
（Cookie 里的 `__pus` / `__puus` 等核心令牌）。这些令牌有效期很短，过期后会出现一种
很迷惑的现象：

- 列表、上传等操作**全部正常**（普通 API 鉴权宽松）；
- 同步任务一启动，下载第一步就全军覆没，日志里报
  `download failed: status 412`（部分边缘节点报 403 + `require login [auth fail]`）。

2026-10-21 真实案例：网盘互传同步 4 个文件全部 412，排查确认是 `kuake_cli\.env` 里的
`KUAKE_COOKIE` 登录态过期。重新扫码登录后立刻恢复。

**以后遇到同步日志大面积 412 / 403，第一反应先跑脚本的 `--check` 验证 Cookie。**

## 使用方式

```bash
# 1. 更新 Cookie：弹浏览器，手机夸克 App 扫码登录，自动写回并验证
backend-web\.venv\Scripts\python.exe scripts\update_quark_cookie.py

# 2. 只检查当前 Cookie 是否还有效（不打开浏览器）
backend-web\.venv\Scripts\python.exe scripts\update_quark_cookie.py --check

# 可选参数
#   --kuake-dir <路径>   指定 kuake_cli 目录（默认本机路径）
#   --timeout <秒>       扫码等待时长（默认 180）
```

## 行为说明

1. **浏览器登录态保留**：浏览器 profile 存在 `kuake_cli\.quark_cookie_browser`，
   Cookie 再次过期时重跑脚本，只要还没退出登录就自动秒过，不用重复扫码；
2. **写入前自动备份**：原 `.env` 备份为 `.env.bak-<时间戳>`；
3. **写入后自动验证**：上传探针文件 → 取签名下载链接 → 真实请求 → 清理。
   探针返回 200 才算通过（只看 API 通不通没有意义，列表接口对过期 Cookie 照样返回 200）；
4. **无需重启服务**：backend-web 每次同步都实时调用 `kuake` 二进制、由它读 `.env`，
   Cookie 更新立即生效。

## 验证通过 / 失败的对照

| 现象 | 含义 |
| --- | --- |
| `--check` 报 `403 require login [auth not found]` | 请求里根本没带 Cookie（Cookie 为空或没写入） |
| `--check` 报 `403 require login [auth fail]` | Cookie 在，但登录态已过期——跑更新模式重新扫码 |
| `--check` 报 `403 require login [auth expired]` | 同上，过期时间更明确 |
| 探针返回 `200` | Cookie 健康，同步功能可用 |

## 故障排查记录（2026-10-21 案例）

- 表象：夸克→百度同步 4 文件全部 `status 412`，3.6 秒快速失败；
- 排查：换请求头组合、换文件、新上传文件全部复现 → 排除代码与文件因素；
- 对照实验：新文件下载返回 403 + `RequestDeniedByCallback / require login [auth fail]`，
  定位到下载回调鉴权失败（电子书文件 412 是 `dl-pc-zb.drive` 节点对同一鉴权失败的
  不同报错页面，本质相同）；
- 处理：扫码更新 Cookie 后探针下载返回 200，同步恢复。
