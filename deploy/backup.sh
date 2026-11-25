#!/usr/bin/env bash
# Daily backup of the database and downloaded pictures. Keeps the last 14 days.
# Usage (CloudPanel cron job, as the site user):  bash /home/SITEUSER/htdocs/YOUR-DOMAIN/deploy/backup.sh
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/backups"
STAMP="$(date +%Y-%m-%d)"
mkdir -p "$OUT"

# Safe copy of the SQLite database, even while the app is running
"$APP/.venv/bin/python" - <<PY
import sqlite3
src = sqlite3.connect("$APP/news.db")
dst = sqlite3.connect("$OUT/news-$STAMP.db")
src.backup(dst)
dst.close()
src.close()
PY

# Pictures and screenshots
if [ -d "$APP/media" ]; then
  tar -czf "$OUT/media-$STAMP.tar.gz" -C "$APP" media
fi

# Delete backups older than 14 days
find "$OUT" -type f \( -name 'news-*.db' -o -name 'media-*.tar.gz' \) -mtime +14 -delete
echo "Backup finished: $OUT"
