#!/usr/bin/env bash
# ============ 每日备份：把 docs 目录打包到指定路径，保留 30 份 ============
# 用法： bash /opt/ehs-kb/deploy/backup.sh
# 定时： crontab -e  ->  30 1 * * * /bin/bash /opt/ehs-kb/deploy/backup.sh >> /var/log/ehs-backup.log 2>&1
set -euo pipefail

DOCS_ROOT="${EHS_DOCS_ROOT:-/srv/ehs/docs}"     # 要备份的知识库目录
BACKUP_DIR="${EHS_BACKUP_DIR:-/backup/ehs}"     # 备份存放路径（建议挂网络盘/NAS）
KEEP=30                                          # 保留天数（份数）

STAMP=$(date +%Y%m%d-%H%M%S)
TARGET="$BACKUP_DIR/ehs-docs-$STAMP.tar.gz"
mkdir -p "$BACKUP_DIR"

echo "[$(date '+%F %T')] 开始备份 $DOCS_ROOT -> $TARGET"
tar -czf "$TARGET" -C "$(dirname "$DOCS_ROOT")" "$(basename "$DOCS_ROOT")"

# 校验并记录大小
SIZE=$(du -h "$TARGET" | cut -f1)
tar -tzf "$TARGET" >/dev/null && echo "[$(date '+%F %T')] 备份成功，大小 $SIZE"

# 清理超出保留份数的旧备份
ls -1t "$BACKUP_DIR"/ehs-docs-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  echo "[$(date '+%F %T')] 清理旧备份 $old" && rm -f "$old"
done
