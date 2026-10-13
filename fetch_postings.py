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
import contextlib
import csv
import os
import tempfile

try:  # POSIX
    import fcntl
except ImportError:  # Windows (python.org installs are supported, see README)
    fcntl = None
    import msvcrt
import json
import re
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


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(rows: list[dict], fields: list[str], path: Path) -> Path:
    """Write to a per-process temp file, then atomically replace the target.
    A run killed mid-write, or two runs on the same day, never leave a
    half-written snapshot behind for diff_checker to pick up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
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
    """One collection per target/date at a time.

    If a cron run and a manual run overlap, the one that finishes last would
    replace the other's CSV even when its data is older or partial. Holding a
    lock for the whole collect→write span makes the second run stop instead.
    """
    args = parse_args(argv)
    target_name, _ = load_target(args.target)
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir / f".{target_name}_{stamp}.lock"
    with open(lock_path, "a+") as lock:
        if not _try_lock(lock):
            print(f"[fetch] ERROR another collection for {target_name} {stamp} is running, nothing written",
                  file=sys.stderr)
            return 5
        return _run(argv)


def _try_lock(fh) -> bool:
    """Non-blocking exclusive lock, released when the file is closed."""
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:  # BlockingIOError is an OSError
        return False
    return True


def _run(argv: list[str]) -> int:
    args = parse_args(argv)
    use_mock = not args.live  # mock is the default so the repo runs with no credentials
    target_name, target = load_target(args.target)
    stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")

    collector_id = target.get("collector_id", "")
    print(f"[fetch] target={target_name} mode={'mock' if use_mock else 'live'} date={stamp}")
    if collector_id:
        print(f"[fetch] collector_id={collector_id} (Scraper Studio)  docs={target['doc_url']}")
    else:
        print(f"[fetch] dataset_id={target['dataset_id']}  docs={target['doc_url']}")
    if use_mock:
        print("[fetch] MOCK MODE: records come from mock/ fixtures and are NOT real collection results.")

    try:
        client = BrightDataClient.from_env(
            mock=use_mock,
            mock_dir=MOCK_DIR,
            dataset_key=args.mock_key or target["mock_key"],
            collector_id=collector_id,
        )
        snapshot_id, records = client.collect(
            dataset_id=target.get("dataset_id", ""),
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
    usable, coverage_rc = check_variant_coverage(target, usable)
    if coverage_rc:
        return coverage_rc
    if target_name.endswith("_tracked"):
        # A product that came back twice cannot be trusted either way (and the
        # diff would keep only the last row), so drop every copy of it.
        ids = [str(r.get(id_field) or "").strip() for r in usable]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            print(f"[fetch] WARNING duplicate {id_field} dropped: {dupes}", file=sys.stderr)
            usable = [r for r in usable if str(r.get(id_field) or "").strip() not in dupes]
            if not usable:
                print("[fetch] ERROR every record was a duplicate, nothing written", file=sys.stderr)
                return 4
    rows = [flatten(r, fields) for r in usable]
    out_path = Path(args.out_dir) / f"{target_name}_{stamp}.csv"
    if target_name.endswith("_tracked") and out_path.exists():
        # Tracked targets collect the same fixed URLs every run, so a same-day
        # rerun may only replace today's snapshot when it still covers every
        # product the existing one has. Otherwise prices already collected today
        # would silently disappear.
        existing = [str(r.get(id_field) or "").strip() for r in read_rows(out_path)]
        if len(existing) != len(set(existing)) or "" in existing:
            print(f"[fetch] ERROR today's snapshot {out_path.name} has blank or duplicate ids; "
                  "fix or remove it before rerunning", file=sys.stderr)
            return 6
        kept = set(existing)
        new = {str(r.get(id_field) or "").strip() for r in rows} - {""}
        lost = sorted(kept - new)
        if lost:
            print(f"[fetch] ERROR this run lacks {len(lost)} product(s) today's snapshot already has "
                  f"({', '.join(lost[:5])}{'…' if len(lost) > 5 else ''}); keeping the existing file",
                  file=sys.stderr)
            return 6
    write_csv(rows, fields, out_path)

    print(f"[fetch] snapshot_id={snapshot_id}")
    print(f"[fetch] wrote {len(rows)} row(s) -> {out_path}")
    print(f"[fetch] sample id(s): {', '.join(str(r.get(target['id_field'])) for r in rows[:3])}")
    return 0


def check_variant_coverage(target: dict, records: list[dict]) -> tuple[list[dict], int]:
    """Tracked targets keyed by variant_id pin each input to one itemId. Compare
    what came back against what was asked for.

    Returns (records to write, rc). Only requested itemIds are written: extra
    variants are dropped, and a variant that came back twice is dropped
    entirely because we cannot tell which row is right. Partial results are kept
    on purpose (downstream marks the missing ones as not collected today), but
    they are reported. A result that shares no itemId with the inputs is not the
    data we asked for, so it is refused (rc 4).
    """
    if target.get("id_field") != "variant_id":
        return records, 0
    expected = set()
    vendor_of: dict[str, str] = {}
    for item in target.get("inputs") or []:
        url = str(item.get("url", ""))
        found = re.search(r"[?&]itemId=(\d+)", url)
        vendor = re.search(r"[?&]vendorItemId=(\d+)", url)
        if not found:
            # One unpinned URL in a variant target would be silently ignored below.
            print(f"[fetch] ERROR variant_id target input has no itemId: {url}", file=sys.stderr)
            return [], 4
        if found and not vendor:
            print(f"[fetch] ERROR input pins itemId without vendorItemId: {url}", file=sys.stderr)
            return [], 4
        if found:
            if found.group(1) in expected:
                print(f"[fetch] ERROR itemId {found.group(1)} is pinned twice in inputs", file=sys.stderr)
                return [], 4
            expected.add(found.group(1))
            vendor_of[found.group(1)] = vendor.group(1)
    if not expected:
        # A variant_id-keyed target whose inputs carry no itemId would accept any
        # configuration that comes back. That is a config error, not a result.
        print("[fetch] ERROR variant_id target has no itemId in its input URLs, nothing written", file=sys.stderr)
        return [], 4
    # The Coupang dataset has no vendor item field of its own (verified
    # 2026-10-13); its url/input fields echo the input URL. Comparing the
    # vendorItemId found there still catches rows paired with the wrong input.
    seen: set[str] = set()
    dupes: set[str] = set()
    for record in records:
        key = str(record.get("variant_id") or "").strip()
        if key in seen:
            dupes.add(key)
        seen.add(key)
    extra = sorted(seen - expected)
    wrong_vendor: list[str] = []

    def vendor_ok(record: dict) -> bool:
        key = str(record.get("variant_id") or "").strip()
        want = vendor_of.get(key)
        source = record.get("input") if isinstance(record.get("input"), dict) else {}
        pairs = set()
        for url in (str(record.get("url") or ""), str(source.get("url") or "")):
            if not url:
                continue
            items = re.findall(r"[?&]itemId=(\d+)", url)
            vendors = re.findall(r"[?&]vendorItemId=(\d+)", url)
            pairs.add((tuple(items), tuple(vendors)))
        # Keep the row only when every URL in it names exactly this itemId and
        # its configured vendorItemId (missing, mismatched or multiple → drop).
        if pairs != {((key,), (want,))}:
            wrong_vendor.append(key)
            return False
        return True

    kept = [
        r for r in records
        if str(r.get("variant_id") or "").strip() in expected
        and str(r.get("variant_id") or "").strip() not in dupes
        and vendor_ok(r)
    ]
    kept_ids = {str(r.get("variant_id")).strip() for r in kept}
    missing = sorted(expected - kept_ids)
    print(f"[fetch] variant coverage {len(kept_ids)}/{len(expected)}")
    if missing or extra or dupes or wrong_vendor:
        print(
            f"[fetch] WARNING variant mismatch missing={missing} extra(dropped)={extra} "
            f"duplicate(dropped)={sorted(dupes)} vendor_mismatch(dropped)={sorted(wrong_vendor)}",
            file=sys.stderr,
        )
    if not kept:
        print("[fetch] ERROR no returned variant matches the configured itemIds, nothing written", file=sys.stderr)
        return [], 4
    return kept, 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
