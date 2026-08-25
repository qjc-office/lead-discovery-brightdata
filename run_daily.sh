#!/usr/bin/env bash
# Daily monitoring run: collect, diff, alert.
#
# Install (09:00 every day):
#   crontab -e
#   0 9 * * * /full/path/to/repo/run_daily.sh >> /full/path/to/repo/data/cron.log 2>&1
#
# Environment: put BRIGHTDATA_API_KEY and SLACK_WEBHOOK_URL in the repo root's .env.
# cron starts with a bare environment, so this script sources .env itself.

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi

MODE="${1:---mock}"   # pass --live once your API key is in place
TARGET="${2:-public_job_postings}"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) run_daily target=$TARGET mode=$MODE ==="

python3 fetch_postings.py "$MODE" --target "$TARGET"
fetch_rc=$?
if [ $fetch_rc -ne 0 ]; then
  echo "fetch_postings failed (rc=$fetch_rc), stopping"
  exit $fetch_rc
fi

python3 diff_checker.py --target "$TARGET"
diff_rc=$?
if [ $diff_rc -eq 1 ]; then
  echo "no changes today, no alert sent"
  exit 0
fi
if [ $diff_rc -ne 0 ]; then
  echo "diff_checker failed (rc=$diff_rc), stopping"
  exit $diff_rc
fi

NOTIFY_ARGS=()
[ "$MODE" = "--mock" ] && NOTIFY_ARGS+=("--mock")
[ -z "${SLACK_WEBHOOK_URL:-}" ] && NOTIFY_ARGS+=("--dry-run") && echo "SLACK_WEBHOOK_URL unset, falling back to dry-run"

python3 notify.py "${NOTIFY_ARGS[@]}"
echo "=== done ==="
