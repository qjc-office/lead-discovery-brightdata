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

load_env() {
  # .env 를 "실행"하지 않고 한 줄씩 읽어 KEY=VALUE 만 받는다.
  # `. .env` 로 소스하면 값에 공백이 섞였을 때(키를 복붙하다 흔히 생긴다)
  # 뒷부분을 명령어로 실행하려 들어서 ".env: line 1: def: command not found"
  # 같은 엉뚱한 에러가 뜬다. 키를 잘못 붙여넣은 사람에게 줄 메시지가 아니다.
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in
      *[!A-Za-z0-9_]*|'') continue ;;   # KEY 자리에 이상한 글자가 있으면 무시
    esac
    val=${val%%[$'\t' ]#*}              # 값 뒤 " # 주석" 제거
    val=${val#[\"\']}; val=${val%[\"\']}   # 감싼 따옴표 한 겹 제거
    val=${val%"${val##*[![:space:]]}"}  # 뒤쪽 공백 제거
    if [ -n "$val" ]; then
      export "$key=$val"
    fi
  done < "$1"
}

load_env "$DIR/.env"

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
