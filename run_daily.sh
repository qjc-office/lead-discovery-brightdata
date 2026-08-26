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

# python3 라는 이름이 없는 환경이 있다. 윈도우에 python.org 설치본을 깔면
# python.exe 만 생기고 python3.exe 는 안 만들어진다. 이름 때문에 막히지 않도록
# 여기서 한 번 찾아 둔다.
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "파이썬을 찾을 수 없습니다. python.org 에서 3.10 이상을 설치해 주세요." >&2
  exit 1
fi

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi

MODE="${1:---mock}"   # pass --live once your API key is in place
TARGET="${2:-public_job_postings}"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) run_daily target=$TARGET mode=$MODE ==="

"$PY" fetch_postings.py "$MODE" --target "$TARGET"
fetch_rc=$?
if [ $fetch_rc -ne 0 ]; then
  echo "fetch_postings failed (rc=$fetch_rc), stopping"
  exit $fetch_rc
fi

"$PY" diff_checker.py --target "$TARGET"
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

"$PY" notify.py "${NOTIFY_ARGS[@]}"
echo "=== done ==="
