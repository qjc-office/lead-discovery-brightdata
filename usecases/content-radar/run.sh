#!/usr/bin/env bash
# Content radar: collect public YouTube signals, then rank topic candidates.
# Everything printed here also lands in results/run-log.txt.
#
#   bash run.sh                 # collect + rank, Bright Data credential probe
#   bash run.sh --bd mock       # add the Bright Data fixture path to the run
#   bash run.sh --bd off        # skip Bright Data entirely
#   bash run.sh --rank-only     # re-score today's CSV without collecting again

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$HERE/results"
LOG="$RESULTS/run-log.txt"
BD_MODE="probe"
RANK_ONLY="no"
TOP="10"

while [ $# -gt 0 ]; do
  case "$1" in
    --bd) BD_MODE="$2"; shift 2 ;;
    --rank-only) RANK_ONLY="yes"; shift ;;
    --top) TOP="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$RESULTS"

run_all() {
  echo "=== content radar run: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo "python3: $(python3 --version 2>&1)"
  echo "yt-dlp:  $(yt-dlp --version 2>&1)"
  echo "credentials present (presence only, values never printed):"
  for name in BRIGHTDATA_API_KEY BRIGHTDATA_API_TOKEN YOUTUBE_QJC_REFRESH_TOKEN; do
    if [ -n "${!name:-}" ]; then echo "  $name: present"; else echo "  $name: missing"; fi
  done
  echo "bright data mode: $BD_MODE"
  echo

  if [ "$RANK_ONLY" = "no" ]; then
    echo "--- step 1: collect ---"
    python3 "$HERE/collect.py" --outdir "$RESULTS" --bd "$BD_MODE"
    collect_rc=$?
    echo "collect exit code: $collect_rc"
    if [ "$collect_rc" -ne 0 ]; then
      echo "collect failed, stopping before ranking"
      return "$collect_rc"
    fi
    echo
  fi

  echo "--- step 2: rank ---"
  python3 "$HERE/rank.py" --outdir "$RESULTS" --top "$TOP"
  rank_rc=$?
  echo "rank exit code: $rank_rc"
  echo
  echo "--- output files ---"
  ls -la "$RESULTS"
  echo "=== done: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  return "$rank_rc"
}

run_all 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
