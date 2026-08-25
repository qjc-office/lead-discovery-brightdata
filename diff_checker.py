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
               current_path: Path, previous_path: Path | None) -> dict:
    cur_idx = index_by(current, target["id_field"])
    prev_idx = index_by(previous or [], target["id_field"])
    baseline = previous is not None
    new_ids = [k for k in cur_idx if k not in prev_idx] if baseline else []
    removed_ids = [k for k in prev_idx if k not in cur_idx] if baseline else []
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
        "changed_count": len(changed_items),
        "watch_fields": target.get("watch_fields") or [],
        "new_items": [summarize(cur_idx[k], target) for k in new_ids],
        "removed_items": [summarize(prev_idx[k], target) for k in removed_ids],
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
    diff = build_diff(current, previous, target, target_name, cur_path, prev_path)

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
    touched = diff["new_count"] or diff["removed_count"] or diff["changed_count"]
    return 0 if touched else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
