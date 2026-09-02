#!/usr/bin/env bash
# ============ EHS 知识库 Linux 一键安装（CentOS 7+/Rocky/Ubuntu 20.04+）============
# 用法： sudo bash deploy/install_linux.sh
set -euo pipefail

APP_DIR=/opt/ehs-kb            # 程序目录
DOCS_ROOT=/srv/ehs/docs        # 知识库根目录（数据）
PORT=8080
ADMIN_TOKEN="${EHS_ADMIN_TOKEN:-Change-Me-2026-EHS}"

echo "[1/7] 安装系统依赖"
if command -v apt-get >/dev/null; then
  apt-get update -y && apt-get install -y python3 python3-venv python3-pip rsync
else
  yum install -y python3 python3-pip rsync
fi

echo "[2/7] 创建服务账号与目录"
id ehs >/dev/null 2>&1 || useradd -r -s /sbin/nologin ehs
mkdir -p "$APP_DIR" "$DOCS_ROOT"/{危化品,特种设备,危废,应急,法规,SOP} "$DOCS_ROOT/.trash"

echo "[3/7] 复制程序（从当前源码目录）"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --exclude '.git' --exclude '.venv' --exclude 'docs' "$SRC"/ "$APP_DIR"/
# 首次安装把示例文档拷进数据目录；已有内容则不覆盖
[ -z "$(ls -A "$DOCS_ROOT" | grep -v '^\.trash$' || true)" ] && cp -r "$SRC/docs/." "$DOCS_ROOT/" || true

echo "[4/7] 创建虚拟环境并安装依赖"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip setuptools wheel -q
# 内网无外网时改用离线包目录： pip install --no-index --find-links=/opt/pkgs -r requirements.txt
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "[5/7] 写入环境变量文件 /etc/ehs-kb.env"
cat > /etc/ehs-kb.env <<ENV
EHS_DOCS_ROOT=$DOCS_ROOT
EHS_ADMIN_TOKEN=$ADMIN_TOKEN
EHS_HOST=0.0.0.0
EHS_PORT=$PORT
EHS_TRASH_RETAIN_DAYS=90
EHS_REVIEW_WARN_DAYS=30
ENV
chmod 600 /etc/ehs-kb.env
chown -R ehs:ehs "$APP_DIR" "$DOCS_ROOT"
touch /var/log/ehs-kb.log && chown ehs:ehs /var/log/ehs-kb.log

echo "[6/7] 注册 systemd 服务并开机自启"
install -m 644 "$APP_DIR/deploy/ehs-kb.service" /etc/systemd/system/ehs-kb.service
systemctl daemon-reload
systemctl enable --now ehs-kb
sleep 3 && systemctl --no-pager -l status ehs-kb | head -12

echo "[7/7] 放行防火墙端口"
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=${PORT}/tcp && firewall-cmd --reload
elif command -v ufw >/dev/null; then
  ufw allow ${PORT}/tcp || true
fi

IP=$(hostname -I | awk '{print $1}')
echo "===================================================="
echo " 部署完成：http://${IP}:${PORT}"
echo " 知识库目录：$DOCS_ROOT"
echo " 管理口令：$ADMIN_TOKEN （请立即修改 /etc/ehs-kb.env 后 systemctl restart ehs-kb）"
echo "===================================================="
