#!/usr/bin/env bash
# 경쟁사 모니터링 1회 실행: 수집 -> 비교표 생성
#
#   ./run.sh                  공개 페이지만 수집
#   ./run.sh --bd             막힌 소스를 Bright Data 어댑터(mock)로 보완
#   ./run.sh --bd --bd-live   같은 경로를 실제 API 키로 실행
#   ./run.sh --only nova      한 소스만 (targets.json 의 key)
#
# 실행 로그 전문은 results/run-log.txt 에 누적된다.

set -euo pipefail

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


HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/results"

# 0행이면 collect.py 가 1 로 끝난다(cron 이 알아채라고 일부러 그렇게 뒀다).
# 그래도 비교표와 차단 목록은 남겨야 무엇이 막혔는지 볼 수 있으니 여기서 끊지 않는다.
rc=0
"$PY" "$HERE/collect.py" --out-dir "$HERE/results" "$@" || rc=$?
"$PY" "$HERE/compare.py" --out-dir "$HERE/results"

echo
echo "산출물:"
ls -1 "$HERE/results"

if [ "$rc" -ne 0 ]; then
  echo
  echo "collect.py 가 $rc 로 끝났습니다. 수집 0행이면 정상적인 실패 신호입니다."
  echo "targets.json 이 아직 example.com 자리표시자라면 먼저 감시 대상부터 바꾸세요."
fi
exit "$rc"
