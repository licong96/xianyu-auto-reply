# paper_crawler — 学科网试卷爬虫（资料池流水线第一步）

独立可运行的采集工具：DrissionPage 驱动真实浏览器，人工登录学科网会员后自动翻页采集试卷，
写入项目 MySQL `xy_material_pool` 表（status=crawled），供后续网盘上传、一键上架流水线接力。

## 环境准备（只需一次）

```bash
cd paper_crawler
python3 -m venv .venv
.venv/bin/pip install -r requirements-crawler.txt
```

## 使用

```bash
# 首次运行：浏览器窗口弹出 → 人工完成学科网会员登录 → 自动开始采集
../paper_crawler/.venv/bin/python -m paper_crawler --keyword 学军中学

# 常用参数
--keyword 学军中学    # 搜索关键词（默认取 config.yaml）
--pages 5            # 最多翻页数（每页约 30-37 条）
--no-download        # 只采元数据不下载，可稍后补下载
--headless           # 无头模式（首次登录不要用）
--interval 3         # 条间隔秒数（反爬缓冲，默认 2）
```

运行目录不限（建议项目根目录）：工具会自动定位项目根、加载 `.env` 连接 MySQL，
配置在 `config.yaml`（浏览器端口、用户目录、学校关键词、下载目录等）。

## 机制说明

- **登录态持久化**：独立浏览器端口（默认 9333）+ 独立用户目录（`browser_data/zxxk`），
  首次登录后长期复用；未检测到登录会每 5 秒轮询等待，最多 10 分钟
- **两道去重**：`source_url` 唯一约束 + 文件 md5；重跑自动跳过已采，中断可断点续爬
- **每页流程**：列表页 SSR 解析（标题/属性标签/发布日/下载量）→ 详情页补全年份 →
  已登录态点击下载 → md5 → 立即入库
- **下载失败不阻塞**：`local_path` 留空，之后可用补下载流程回填

## 下载校准（如站点改版）

下载链路已按真实页面实测校准（2026-10-10）：

1. 详情页主按钮 `#btnSoftDownload`（VIP会员 免费下载）
2. 弹出 layui iframe 二次确认，点击 `.balance-payment-btn`（确认）
3. 自动跳转下载页并完成下载，文件落在 `~/Downloads/zxxk`（Chrome 下载目录，
   在 config.yaml 的 `zxxk_download_dir` 配置），爬虫监控该目录并归档到 `data/papers/`

注意节奏：详情页加载后 +3 秒再点下载；点击主按钮后 +3 秒再点确认（弹窗渲染等待）。

## 补下载（本地文件缺失时）

学科网「我的下载」列表页 https://user.zxxk.com/download/list 有每条资源的
「重新下载」入口（`.again-down`，指向详情页如 /soft/{id}.html）。
补下载流程：打开下载列表 → 点重新下载进详情页 → 点 #btnSoftDownload →
自动跳转 /soft/softdownload?softid={id} 完成下载。后续可做补下载服务批量回填 local_path。
