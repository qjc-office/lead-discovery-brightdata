#!/usr/bin/env python3
"""Level 2: collect one monitoring target through the Bright Data Web Scraper API
and write the result to data/<target>_YYYYMMDD.csv.

Usage
  python3 fetch_postings.py --mock                 # fixtures, no API key needed
  python3 fetch_postings.py --live                 # real API, needs BRIGHTDATA_API_KEY
  python3 fetch_postings.py --mock --target amazon_products
  python3 fetch_postings.py --mock --mock-key public_job_postings_prev --date 20260803

Switching from mock to live changes one flag. The request shape is identical.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bd_client import BrightDataClient, BrightDataError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "scraper_config.json"
MOCK_DIR = ROOT / "mock"
DATA_DIR = ROOT / "data"


def load_target(name: str | None) -> tuple[str, dict]:
    """Read scraper_config.json and return (target_name, target_config)."""
    if not CONFIG_PATH.exists():
        raise SystemExit(f"config not found: {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = cfg.get("targets") or {}
    name = name or cfg.get("default_target")
    if name not in targets:
        raise SystemExit(f"unknown target '{name}'. available: {', '.join(sorted(targets))}")
    return name, targets[name]


def flatten(record: dict, fields: list[str]) -> dict:
    """Project a record onto the configured CSV columns, stringifying nested values."""
    row = {}
    for field in fields:
        value = record.get(field)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        row[field] = "" if value is None else value
    return row


def write_csv(rows: list[dict], fields: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Bright Data Scraper API collector")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="replay fixtures in mock/ (no API key)")
    mode.add_argument("--live", action="store_true", help="call the real Bright Data API")
    ap.add_argument("--target", help="target key in scraper_config.json")
    ap.add_argument("--mock-key", help="override which fixture set to replay")
    ap.add_argument("--date", help="date stamp for the output file (YYYYMMDD, default: today UTC)")
    ap.add_argument("--batch-size", type=int, default=1000,
                    help="records per delivery part, Bright Data minimum is 1000")
    ap.add_argument("--no-batch", action="store_true", help="download in a single request")
    ap.add_argument("--out-dir", default=str(DATA_DIR))
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    use_mock = not args.live  # mock is the default so the repo runs with no credentials
    target_name, target = load_target(args.target)
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")

    print(f"[fetch] target={target_name} mode={'mock' if use_mock else 'live'} date={stamp}")
    print(f"[fetch] dataset_id={target['dataset_id']}  docs={target['doc_url']}")
    if use_mock:
        print("[fetch] MOCK MODE: records come from mock/ fixtures and are NOT real collection results.")

    try:
        client = BrightDataClient.from_env(
            mock=use_mock,
            mock_dir=MOCK_DIR,
            dataset_key=args.mock_key or target["mock_key"],
        )
        snapshot_id, records = client.collect(
            dataset_id=target["dataset_id"],
            inputs=target["inputs"],
            params=target.get("trigger_params", {}),
            fmt="json",
            batch_size=None if args.no_batch else args.batch_size,
        )
    except BrightDataError as exc:
        print(f"[fetch] ERROR {exc}", file=sys.stderr)
        return 2

    if not records:
        print("[fetch] WARNING empty result set, nothing written", file=sys.stderr)
        return 3

    fields = target["csv_fields"]
    # Records that carry no id are collection errors, not results. Writing them
    # produces blank CSV rows, so drop them and say how many were dropped.
    id_field = target["id_field"]
    usable = [r for r in records if str(r.get(id_field) or "").strip()]
    if len(usable) < len(records):
        print(f"[fetch] dropped {len(records) - len(usable)} record(s) without {id_field}")
    if not usable:
        print("[fetch] WARNING every record lacked an id, nothing written", file=sys.stderr)
        return 3
    rows = [flatten(r, fields) for r in usable]
    out_path = Path(args.out_dir) / f"{target_name}_{stamp}.csv"
    write_csv(rows, fields, out_path)

    print(f"[fetch] snapshot_id={snapshot_id}")
    print(f"[fetch] wrote {len(rows)} row(s) -> {out_path}")
    print(f"[fetch] sample id(s): {', '.join(str(r.get(target['id_field'])) for r in rows[:3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
