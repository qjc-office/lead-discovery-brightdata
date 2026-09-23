#!/usr/bin/env python3
"""Level 3 (part 1): compare the two most recent CSV snapshots of a target and
report what appeared and what disappeared.

Usage
  python3 diff_checker.py                              # default target, two latest files
  python3 diff_checker.py --target amazon_products
  python3 diff_checker.py --current data/a.csv --previous data/b.csv
  python3 diff_checker.py --out data/diff_20260804.json

Exit codes
  0  changes found (or no baseline yet)
  1  no changes
  2  input error
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "scraper_config.json"
DATA_DIR = ROOT / "data"


def load_target(name: str | None) -> tuple[str, dict]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = cfg.get("targets") or {}
    name = name or cfg.get("default_target")
    if name not in targets:
        raise SystemExit(f"unknown target '{name}'. available: {', '.join(sorted(targets))}")
    return name, targets[name]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_two(data_dir: Path, target_name: str) -> tuple[Path | None, Path | None]:
    """Return (current, previous) by filename sort, newest first."""
    files = sorted(data_dir.glob(f"{target_name}_*.csv"))
    if not files:
        return None, None
    if len(files) == 1:
        return files[-1], None
    return files[-1], files[-2]


def recent_snapshots(data_dir: Path, target_name: str, limit: int,
                     upto: Path | None = None) -> list[Path]:
    """Return up to `limit` snapshots ending at `upto`, oldest first.

    `upto` matters when diffing an older pair explicitly: without it the streak
    would count snapshots taken *after* the one being diffed and call a
    one-off miss a removal.
    """
    files = sorted(data_dir.glob(f"{target_name}_*.csv"))
    if upto is not None:
        files = [f for f in files if f.name <= upto.name]
    return files[-limit:]


def absence_streak(indexed: list[dict], key: str) -> int:
    """How many consecutive most-recent snapshots lack this id.

    A single miss usually means the scraper failed on that page, not that the
    product is gone. Out-of-stock Coupang pages drop out intermittently
    (2026-09-23: 4 of 8 vanished at 09:24 and all came back at 13:07).
    """
    streak = 0
    for idx in reversed(indexed):
        if key in idx:
            break
        streak += 1
    return streak


def index_by(rows: list[dict], id_field: str) -> dict[str, dict]:
    return {str(r.get(id_field)): r for r in rows if r.get(id_field)}


def summarize(row: dict, target: dict) -> dict:
    return {
        "id": row.get(target["id_field"], ""),
        "title": row.get(target["title_field"], ""),
        "subtitle": row.get(target["subtitle_field"], ""),
        "url": row.get(target["url_field"], ""),
    }


def field_changes(previous_row: dict, current_row: dict, fields: list[str]) -> dict:
    """Values that moved on a record that exists in both snapshots."""
    changes = {}
    for field in fields:
        before = str(previous_row.get(field) or "").strip()
        after = str(current_row.get(field) or "").strip()
        if before != after:
            changes[field] = {"before": before, "after": after}
    return changes


def collect_changed(cur_idx: dict, prev_idx: dict, target: dict) -> list[dict]:
    """Records that stayed but whose watched fields moved, e.g. a price drop.

    Set difference alone misses this: a product that is listed today and was
    listed yesterday looks unchanged even when its price halved.
    """
    watched = target.get("watch_fields") or []
    if not watched:
        return []
    changed = []
    for key, current_row in cur_idx.items():
        previous_row = prev_idx.get(key)
        if not previous_row:
            continue
        moved = field_changes(previous_row, current_row, watched)
        if moved:
            entry = summarize(current_row, target)
            entry["changes"] = moved
            changed.append(entry)
    return changed


def build_diff(current: list[dict], previous: list[dict] | None, target: dict, target_name: str,
               current_path: Path, previous_path: Path | None,
               history: list[Path] | None = None) -> dict:
    cur_idx = index_by(current, target["id_field"])
    prev_idx = index_by(previous or [], target["id_field"])
    baseline = previous is not None
    new_ids: list[str] = []  # known_idx 산출 뒤 아래에서 채운다
    # 한 번 빠졌다고 "내려갔다"고 단정하지 않는다. 수집 실패와 실제 삭제를
    # 구분하려면 연속 결석을 봐야 한다 (2026-09-23 쿠팡 오탐 4건).
    #
    # 결석 후보는 "어제 있었는데 오늘 없음"으로 잡으면 안 된다. 그러면 어제
    # 이미 빠진 항목이 후보에서 제외돼 연속 결석이 2에 닿지 못하고, removed
    # 판정이 영원히 나오지 않는다. 그래서 직전 한 장이 아니라 최근 이력
    # 전체에서 한 번이라도 본 id를 후보로 둔다.
    # 기본 2는 오탐을 줄일 뿐 없애지 못한다. 같은 페이지가 이틀 연속 실패하면
    # 멀쩡한 상품도 removed로 간다. 품절 페이지처럼 반복 실패가 잦은 대상은
    # 타겟별 absence_threshold를 올려 잡는다.
    threshold = int(target.get("absence_threshold", 2))
    id_field = target["id_field"]
    # 경로 객체로 비교하면 같은 파일이라도 상대/절대 표기가 다를 때 어긋난다.
    # 그러면 오늘 스냅샷이 past에 남아 결석이 두 번 세어지고, 1회 누락이
    # removed로 튄다(=고치려던 그 오탐). 파일명으로 비교한다.
    past = [index_by(read_csv(p), id_field)
            for p in (history or []) if p.name != current_path.name]
    known_idx: dict[str, dict] = {}
    for idx in past:
        known_idx.update(idx)
    absent_ids = [k for k in known_idx if k not in cur_idx] if baseline else []
    # 신규 판정도 같은 창을 본다. 직전 한 장만 보면 어제 누락됐다 오늘 돌아온
    # 항목이 매번 "신규"로 잡힌다(removed 오탐의 거울상).
    if baseline:
        new_ids = [k for k in cur_idx if k not in known_idx and k not in prev_idx]

    removed_ids, missing_ids = [], []
    for key in absent_ids:
        streak = absence_streak(past + [cur_idx], key)
        (removed_ids if streak >= threshold else missing_ids).append(key)

    changed_items = collect_changed(cur_idx, prev_idx, target) if baseline else []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target_name,
        "label": target.get("label", target_name),
        "baseline_present": baseline,
        "current_file": str(current_path),
        "previous_file": str(previous_path) if previous_path else None,
        "current_count": len(cur_idx),
        "previous_count": len(prev_idx),
        "new_count": len(new_ids),
        "removed_count": len(removed_ids),
        "missing_count": len(missing_ids),
        "absence_threshold": threshold if baseline else None,
        "changed_count": len(changed_items),
        "watch_fields": target.get("watch_fields") or [],
        "new_items": [summarize(cur_idx[k], target) for k in new_ids],
        "removed_items": [summarize(known_idx[k], target) for k in removed_ids],
        "missing_items": [summarize(known_idx[k], target) for k in missing_ids],
        "changed_items": changed_items,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Diff two Bright Data collection snapshots")
    ap.add_argument("--target", help="target key in scraper_config.json")
    ap.add_argument("--current", help="explicit path to the newer CSV")
    ap.add_argument("--previous", help="explicit path to the older CSV")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--out", help="where to write the diff JSON (default: data/diff_<target>_<date>.json)")
    return ap.parse_args(argv)


def resolve_inputs(args, target_name: str) -> tuple[Path, Path | None]:
    data_dir = Path(args.data_dir)
    if args.current:
        cur = Path(args.current)
        prev = Path(args.previous) if args.previous else None
    else:
        cur, prev = latest_two(data_dir, target_name)
    if cur is None or not cur.exists():
        raise SystemExit(f"no current CSV found for '{target_name}' in {data_dir}. Run fetch_postings.py first.")
    if prev is not None and not prev.exists():
        raise SystemExit(f"previous CSV not found: {prev}")
    return cur, prev


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    target_name, target = load_target(args.target)
    try:
        cur_path, prev_path = resolve_inputs(args, target_name)
    except SystemExit as exc:
        print(f"[diff] ERROR {exc}", file=sys.stderr)
        return 2

    current = read_csv(cur_path)
    previous = read_csv(prev_path) if prev_path else None
    history = recent_snapshots(Path(args.data_dir), target_name,
                               int(target.get("absence_threshold", 2)) + 1, upto=cur_path)
    diff = build_diff(current, previous, target, target_name, cur_path, prev_path, history)

    out_path = Path(args.out) if args.out else Path(args.data_dir) / f"diff_{target_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[diff] current={cur_path.name} ({diff['current_count']} rows)")
    print(f"[diff] previous={prev_path.name if prev_path else 'NONE (first run, baseline saved)'}"
          f" ({diff['previous_count']} rows)")
    print(f"[diff] new={diff['new_count']} removed={diff['removed_count']} changed={diff['changed_count']}")
    for item in diff["new_items"]:
        print(f"[diff]   + {item['id']} | {item['title']} | {item['subtitle']}")
    for item in diff["removed_items"]:
        print(f"[diff]   - {item['id']} | {item['title']} | {item['subtitle']}")
    for item in diff["changed_items"]:
        moves = ", ".join(f"{f} {c['before']} -> {c['after']}" for f, c in item["changes"].items())
        print(f"[diff]   ~ {item['id']} | {item['title'][:40]} | {moves}")
    print(f"[diff] wrote {out_path}")

    if not diff["baseline_present"]:
        return 0
    touched = (diff["new_count"] or diff["removed_count"]
               or diff["changed_count"] or diff["missing_count"])
    return 0 if touched else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
