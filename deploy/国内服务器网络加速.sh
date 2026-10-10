#!/usr/bin/env bash
# ============================================================
# 国内服务器网络加速一键配置
# 覆盖：apt 软件源 + Docker Hub 镜像加速
# 用法（root 执行）：bash deploy/国内服务器网络加速.sh
# 说明：playwright/patchright 浏览器源已在 Dockerfile 内处理
#       （PLAYWRIGHT_DOWNLOAD_HOST=npmmirror），无需本脚本
# ============================================================
set -e

# ---------- 1. apt 镜像：腾讯云内网 > 阿里云公网 > 保持原样 ----------
echo "==> [1/3] 配置 apt 软件源镜像"
APT_MIRROR=""
if curl -sI --max-time 3 http://mirrors.tencentyun.com >/dev/null 2>&1; then
  APT_MIRROR="mirrors.tencentyun.com"        # 腾讯云内网，仅腾讯云机器可用
elif curl -sI --max-time 3 http://mirrors.aliyun.com >/dev/null 2>&1; then
  APT_MIRROR="mirrors.aliyun.com"            # 公网通用
fi

if [ -n "$APT_MIRROR" ]; then
  # Ubuntu 24.04+ 的 deb822 格式
  if ls /etc/apt/sources.list.d/*.sources >/dev/null 2>&1; then
    sed -i "s|archive.ubuntu.com|$APT_MIRROR|g; s|security.ubuntu.com|$APT_MIRROR|g" /etc/apt/sources.list.d/*.sources
  fi
  # 传统 one-line 格式
  [ -f /etc/apt/sources.list ] && sed -i "s|archive.ubuntu.com|$APT_MIRROR|g; s|security.ubuntu.com|$APT_MIRROR|g" /etc/apt/sources.list
  echo "    apt 镜像已切换: $APT_MIRROR"
else
  echo "    警告: 两个候选镜像都不可达，apt 保持原配置"
fi
apt-get update -qq && echo "    apt update 完成"

# ---------- 2. Docker Hub 镜像加速 ----------
echo "==> [2/3] 配置 Docker 镜像加速器"
[ -f /etc/docker/daemon.json ] && cp /etc/docker/daemon.json /etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M)
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io",
    "https://docker.xuanyuan.me",
    "https://hub.rat.dev"
  ]
}
EOF
systemctl restart docker
docker info 2>/dev/null | grep -A6 'Registry Mirrors' || true
echo "    Docker 加速器已生效（若上面无输出，执行 docker info 查看）"

# ---------- 3. 验证 ----------
echo "==> [3/3] 拉取测试"
if docker pull hello-world >/dev/null 2>&1; then
  docker rmi hello-world >/dev/null 2>&1
  echo "    ✓ hello-world 拉取成功，网络已通"
else
  echo "    ✗ 仍拉取失败，可能所有加速器都已失效，需搜索最新可用地址替换"
  exit 1
fi

echo ""
echo "全部完成。接下来："
echo "  git pull origin custom        # 拉取 Dockerfile 国内下载源修复"
echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml \\"
echo "    --env-file .env.production up -d --build"
