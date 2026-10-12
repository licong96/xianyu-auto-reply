# 闲鱼自动回复系统 — Windows 11 本机部署清单

> 对应 macOS 源码模式（非 Docker）。四个后端服务 + 前端，端口：backend-web 8089 / websocket 8090 / scheduler 8091 / frontend 9000（promotion 返佣子系统 8092/9001 如不用可跳过）。

## 一、需要复制到 Windows 的内容

| 内容 | 来源（Mac） | 去向（Windows） | 说明 |
|-|-|-|-|
| 代码 | git 仓库（推荐克隆自己的 fork `licong96/xianyu-auto-reply` 的 `custom` 分支） | `C:\xianyu\xianyu-auto-reply` | 不要直接拷 `.venv`、`node_modules`、`__pycache__`、日志 |
| 配置文件 | `.env` | 项目根目录 | **必拷**，拷过去后按第三节改 |
| 数据库 | `mysqldump xianyu_data`（或直接用 `backups/` 里最新的 .sql.gz） | 导入 Windows 的 MySQL | 想全新开始可不拷，服务启动会自动建表 |
| 商品图片 | `backend-web/static/uploads/`（约 1.3 MB） | 同路径 | 已上架商品的图片 URL 依赖它 |
| 本地试卷文件 | `data/papers/`（约 46 MB） | 同路径 | 资料池已下载文件的本地副本，不想迁移可跳过（网盘里还有） |
| 夸克 CLI | ❌ 不拷 | 需另备 Windows 版 | 现用的 `kuake_cli/kuake` 是 macOS arm64 二进制，Windows 跑不了 |
| 浏览器登录态 `browser_data/` | ❌ 建议不拷（319 MB） | — | 跨系统拷贝易出问题，到 Windows 后重新扫码登录即可 |
| macOS 脚本 | ❌ 不拷 | — | `启动.command / 停止.command / build*.sh / deploy*.sh` 是 mac/Linux 专用 |

## 二、Windows 11 环境准备

1. **Python 3.12**：python.org 安装包，勾选 “Add python.exe to PATH”。（项目 venv 实测 3.12.14）
2. **Node.js**：LTS 版（≥ 18，建议 20/22）。
3. **Git**：用于克隆代码。
4. **MySQL 8.0+** 和 **Redis**，两种方式任选：
   - **方式 A（推荐，版本与现网一致）**：装 Docker Desktop，只跑这两个容器：
     ```powershell
     docker run -d --name xianyu-mysql -p 3306:3306 -e MYSQL_ROOT_PASSWORD=你的密码 mysql:8.0
     docker run -d --name xianyu-redis -p 6379:6379 redis:7-alpine
     ```
   - 方式 B：MySQL Installer 装 MySQL 8/9，Redis 用 Memurai 或微软 Redis-windows 移植版。
5. 建库（库名必须一致）：
   ```sql
   CREATE DATABASE xianyu_data DEFAULT CHARSET utf8mb4;
   ```

## 三、拷贝后必须修改的地方

### 1. `.env`（项目根目录）

- `MYSQL_HOST` 改成 Windows 实际地址（Docker 方式填 `127.0.0.1`）、`MYSQL_PASSWORD` 改成新密码
- `KUAKE_BIN`：夸克 CLI 在 Windows 上没有可用二进制，先留空或指向 Windows 版路径；**留空则资料池「上传网盘」功能不可用**（其余功能不受影响）
- 检查所有路径类配置为 Windows 格式

### 2. 数据库导入（若迁移数据）

Mac 上导出（在 Mac 执行一次）：
```bash
mysqldump -u root -p xianyu_data > xianyu_data_win.sql
```
Windows 上导入：
```powershell
mysql -u root -p xianyu_data < xianyu_data_win.sql
```
> 导入旧库的好处：管理员改过的密码、已配的关键词/卡券/素材/续售规则、JWT 密钥全部保留。注意 `backups/` 里的定时备份只有最近一个月的。

### 3. 爬虫配置（如要在 Windows 跑 paper_crawler）

`paper_crawler/config.yaml` 里的下载目录、`zxxk.py` 中写死的 `/Users/liangluyang/Downloads/zxxk` 路径都要改成 Windows 路径。按知识库记录，爬虫原计划只在本机（Mac）运行，可以暂不迁移。

## 四、安装依赖与启动

每个 Python 服务独立 venv（在 PowerShell 中，项目根目录）：

```powershell
# backend-web
cd backend-web
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
python -m playwright install chromium
python -m patchright install chromium

# websocket、scheduler 重复以上四步
# paper_crawler（可选）：pip install -r requirements-crawler.txt
```

前端：
```powershell
cd frontend
npm install
```

**启动顺序**（MySQL/Redis → backend-web → websocket → scheduler → frontend）：

```powershell
.\backend-web\.venv\Scripts\python backend-web\main.py      # 8089
.\websocket\.venv\Scripts\python websocket\main.py          # 8090
.\scheduler\.venv\Scripts\python scheduler\main.py          # 8091
cd frontend; npm run dev                                     # 9000
```

> 注意：各服务目录里有作者自带的 `启动.bat`，但它调的是系统 `python main.py`。要么把依赖装进系统 Python，要么把 bat 里的 `python` 改成 `.\.venv\Scripts\python`，否则会缺包。

## 五、验证清单

- [ ] `http://127.0.0.1:8089/health`、`8090/health`、`8091/health` 都返回“服务运行正常”
- [ ] `http://localhost:9000` 打开管理后台，能登录（迁移了数据库则密码与原系统一致，否则 admin / admin123，登录后立刻改密）
- [ ] 账号管理 → 扫码登录一个闲鱼小号（会弹真实 Chrome 窗口，触发验证人工处理）
- [ ] 用小号拍一单，验证自动发货 + 自动续售链路
- [ ] Windows 防火墙确认 8089/8090/8091/9000 未对公网开放

## 六、重要提醒

1. **不要两台机器同时在线跑同一批闲鱼账号**——两边都会连同一个账号的消息通道，自动回复/自动发货会重复触发，还容易触发闲鱼风控。迁移期先在 Mac 上 `./停止.command` 停干净再启动 Windows 侧。
2. `browser_data` 不迁移，Windows 上所有闲鱼账号需重新扫码登录。
3. 夸克网盘上传功能在 Windows 侧暂不可用（无 Windows 版 kuake 二进制），已生成的分享卡券不受影响。
4. 定时备份任务（`backups/` 里的 mysqldump）是 Mac 上的 cron/调度产生的，Windows 侧如需备份要另行配置。

## 七、Tailscale 远程访问：在公司打开家里的管理后台

> 目标：家里电脑跑项目，公司电脑/手机通过 Tailscale 虚拟局域网访问后台。项目代码零改动（后端 `HOST=0.0.0.0`、前端 vite 已监听 `0.0.0.0:9000` 并代理 `/api` 到 8089）。
> 原理：Tailscale 基于 WireGuard 组建虚拟局域网（tailnet），每台设备分配固定 `100.x.x.x` IP，NAT 穿透直连，不需要公网 IP 和路由器端口转发，流量全程加密。

### 1. 组网（10 分钟）

1. 家里电脑、公司电脑都装 Tailscale（官网下载），**登录同一账号**
2. 家里电脑开启「开机自启 / Run unattended」，并关闭 Windows 休眠（电源设置），否则重启/睡眠后无法连接
3. 家里电脑查自己的 Tailscale IP（如 `100.101.102.103`）；可在 Tailscale 管理后台开启 MagicDNS，用设备名代替 IP

### 2. Windows 防火墙收紧（重要）

项目端口默认对所在局域网开放。只允许 Tailscale 网段访问后台，公网和家里其他设备都摸不到：

```powershell
# 只允许 Tailscale 网段（100.64.0.0/10）访问 9000 端口，入站规则示例
New-NetFirewallRule -DisplayName "Xianyu Admin (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 9000 -RemoteAddress 100.64.0.0/10 -Action Allow
# 如之前放行过 9000 的宽松规则，删除之
```

（管理后台只需开 9000；8089/8090/8091 是服务间端口，不要对局域网开放。）

### 3. 访问

- 公司电脑浏览器打开 `http://100.101.102.103:9000`（或 MagicDNS 域名）即可登录管理后台
- 手机装 Tailscale App 登录后也能随时看后台

### 4. 注意事项

- **先确认公司合规**：部分公司禁止办公电脑装个人 VPN 软件。若有限制，改用 **Cloudflare Tunnel** 方案——家里电脑只需 outbound 连接，公司侧零安装、浏览器访问域名（可加 Cloudflare Access 登录验证），更适合"只用浏览器看后台"的场景
- 后台账号用强密码；Tailscale 管理后台可用 ACL 限制只有指定设备能访问家里电脑的端口
- 体验受家里**上行带宽**限制，日常管后台、看消息足够，传大文件较慢

## 八、家里 Windows 电脑安装 Tailscale 详细步骤

> 家里电脑是"被访问方"，安装后的三项关键设置决定你公司能否随时连上，不要跳步。

### 1. 安装（三种方式任选）

| 方式 | 操作 |
|-|-|
| 官网安装包（推荐） | 打开 https://tailscale.com/download ，下载 Windows 版 MSI，双击安装 |
| winget 命令行 | `winget install tailscale.tailscale` |
| 微软商店 | 搜 "Tailscale" 安装 |

要求：Windows 10 1809 或更高（Win11 没问题），安装需要管理员权限，装完任务栏右下角出现 Tailscale 图标。

### 2. 登录组网

1. 点任务栏 Tailscale 图标 → **Log in**，浏览器弹出登录页
2. 用与公司电脑共用的账号登录（建议微软或 Google 账号）；免费 Personal 计划支持 100 台设备，个人够用
3. 登录成功后图标变彩色，悬停显示本机 Tailscale IP（100.x.x.x），记下来

### 3. 三项关键设置（漏了就会"公司连不上"）

1. **Run unattended（无人值守）**：图标 → Preferences → 勾选。用户未登录桌面、锁屏时 Tailscale 也保持在线——家里的服务是后台跑的，必须开
2. **关闭休眠**：设置 → 系统 → 电源 → 屏幕和睡眠 → "接通电源时进入睡眠"改为**从不**。休眠后网络全断
3. **Windows Update 活跃时段**（可选）：设为上班时间，避免白天自动重启导致服务中断

### 4. 验证

家里电脑 PowerShell：

```powershell
tailscale status     # 应显示本机 100.x IP，online
tailscale ip -4      # 只看 IPv4 地址
```

公司电脑装好 Tailscale 登录同一账号后：

```powershell
ping 100.x.x.x       # 家里电脑的 Tailscale IP，通则组网成功
```

浏览器打开 `http://100.x.x.x:9000` 能进后台，全链路即通。

### 5. 常见坑

- 两台设备必须登录**同一个账号**（或同一 tailnet）；不同账号要在管理后台互相邀请
- 家里装过 Docker Desktop/虚拟机（虚拟网卡）一般不影响；连不上先到 https://login.tailscale.com/admin/machines 看两台设备是否都 online
- 国内网络首次打开 login.tailscale.com 较慢属正常，换手机热点可完成首次登录

