"""Bright Data path for content-radar.

Reuses the existing ../../bd_client.py at the repo root (official Web Scraper API spec:
POST /datasets/v3/trigger, poll /progress, download /snapshot).
This module only maps Bright Data records onto the content-radar video row
schema, so the ranking code does not care where a row came from.

Two entry points:
  probe_live()   -> attempts one real trigger and reports what happened
  collect_bd()   -> full collect cycle (mock fixtures or live API)

Every row carries data_origin so real and synthetic rows never blend.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO_ROOT = HERE.parent.parent
sys.path.insert(0, str(DEMO_ROOT))

from bd_client import BrightDataClient, BrightDataError, LiveTransport, MockTransport  # noqa: E402


def _token() -> str:
    """Bright Data credentials come from the environment only."""
    return os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN") or ""


def _first(record: dict, keys: tuple[str, ...], default=""):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_yyyymmdd(value) -> str:
    text = str(value or "")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


def map_record(record: dict, keyword: str, origin: str) -> dict:
    """Bright Data YouTube record -> content-radar video row."""
    upload = _to_yyyymmdd(_first(record, ("date_posted", "upload_date", "published_at")))
    days = ""
    if upload:
        try:
            days = (date.today() - datetime.strptime(upload, "%Y%m%d").date()).days
        except ValueError:
            days = ""
    return {
        "video_id": str(_first(record, ("video_id", "id"))),
        "title": str(_first(record, ("title", "video_title"))),
        "channel": str(_first(record, ("youtuber", "channel_name", "channel"))),
        "channel_id": str(_first(record, ("channel_id", "youtuber_id"))),
        "subscribers": _to_int(_first(record, ("subscribers", "channel_subscribers"), 0)),
        "view_count": _to_int(_first(record, ("views", "view_count"), 0)),
        "like_count": _to_int(_first(record, ("likes", "like_count"), 0)),
        "comment_count": _to_int(_first(record, ("num_comments", "comment_count"), 0)),
        "upload_date": upload,
        "days_since_upload": days,
        "duration_sec": _to_int(_first(record, ("video_length", "duration"), 0)),
        "source_keyword": keyword,
        "source_type": "brightdata",
        "search_rank": "",
        "url": str(_first(record, ("url", "video_url"))),
        "data_origin": origin,
    }


def probe_live(dataset_id: str, keyword: str, log=print) -> dict:
    """One real API call, so the run log records the actual credential state."""
    token = _token()
    if not token:
        log("[bd] live probe skipped: no BRIGHTDATA_API_KEY / BRIGHTDATA_API_TOKEN in environment")
        return {"ok": False, "reason": "no_credentials"}
    log(f"[bd] live probe: POST /datasets/v3/trigger dataset_id={dataset_id} (token length {len(token)})")
    try:
        client = BrightDataClient(LiveTransport(token), logger=log)
        snapshot_id = client.transport.trigger(dataset_id, [{"keyword": keyword}], {})
        log(f"[bd] live probe accepted, snapshot_id={snapshot_id}")
        return {"ok": True, "snapshot_id": snapshot_id}
    except BrightDataError as exc:
        log(f"[bd] live probe rejected: {exc}")
        return {"ok": False, "reason": str(exc)}


def collect_bd(dataset_id: str, dataset_key: str, keywords: list[str], mock: bool, log=print) -> list[dict]:
    """Same pipeline shape for mock fixtures and the live API."""
    mode = "mock" if mock else "live"
    origin = f"brightdata:{mode}"
    if mock:
        client = BrightDataClient(MockTransport(HERE / "mock", dataset_key), logger=log)
    else:
        client = BrightDataClient(LiveTransport(_token()), logger=log)
    inputs = [{"keyword": word, "country": "KR"} for word in keywords]
    _snapshot_id, records = client.collect(dataset_id, inputs, {"include_errors": "true"})
    rows = [map_record(rec, keywords[0] if keywords else "", origin) for rec in records]
    log(f"[bd:{mode}] mapped {len(rows)} row(s) onto the video schema")
    return rows
