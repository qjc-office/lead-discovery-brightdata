#!/usr/bin/env python3
"""Collect public YouTube signals for the QJC content radar.

Two axes:
  demand  - what people actually search for  (ytsearch results + view counts)
  supply  - what already broke out on rival channels (recent uploads + subs)

Sources and how each row is labelled in data_origin:
  yt-dlp:search         search hit, view count only (no detail call spent)
  yt-dlp:search+detail  search hit enriched with upload date, subs, likes
  yt-dlp:channel+detail recent upload from a rival or own channel
  brightdata:mock       synthetic fixture, never a real measurement
  brightdata:live       Bright Data Web Scraper API

Standard library plus the yt-dlp CLI. No pip install.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import bd_adapter
import yt_analytics

HERE = Path(__file__).resolve().parent
FIELDS = [
    "video_id", "title", "channel", "channel_id", "subscribers", "view_count",
    "like_count", "comment_count", "upload_date", "days_since_upload",
    "duration_sec", "source_keyword", "source_type", "search_rank", "url",
    "data_origin", "collected_at",
]
STAMP = date.today().strftime("%Y%m%d")


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def run_ytdlp(args: list[str], timeout: int = 300) -> str:
    """One yt-dlp call. Returns stdout even when some items failed."""
    cmd = ["yt-dlp", "--no-update", "--no-warnings", "--ignore-errors", *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"  yt-dlp timeout after {timeout}s: {' '.join(args[-1:])}")
        return ""
    if done.returncode != 0 and not done.stdout.strip():
        log(f"  yt-dlp failed rc={done.returncode}: {done.stderr.strip().splitlines()[-1:]}")
    return done.stdout


def days_since(upload_date: str):
    if not upload_date:
        return ""
    try:
        return (date.today() - datetime.strptime(upload_date, "%Y%m%d").date()).days
    except ValueError:
        return ""


def blank_row(**over) -> dict:
    row = {key: "" for key in FIELDS}
    row["collected_at"] = datetime.now().isoformat(timespec="seconds")
    row.update(over)
    return row


def search_keyword(keyword: str, count: int) -> list[dict]:
    """Demand axis. Search ranking plus view count, one call per keyword."""
    raw = run_ytdlp(["--flat-playlist", "-J", f"ytsearch{count}:{keyword}"])
    if not raw.strip():
        return []
    try:
        entries = json.loads(raw).get("entries") or []
    except json.JSONDecodeError:
        log(f"  search JSON unreadable for '{keyword}'")
        return []
    rows = []
    for rank, entry in enumerate(entries, start=1):
        if not entry.get("id"):
            continue
        rows.append(blank_row(
            video_id=entry["id"], title=entry.get("title") or "",
            channel=entry.get("channel") or "", channel_id=entry.get("channel_id") or "",
            subscribers=0, view_count=int(entry.get("view_count") or 0),
            like_count=0, comment_count=0, upload_date="", days_since_upload="",
            duration_sec=int(entry.get("duration") or 0), source_keyword=keyword,
            source_type="search", search_rank=rank,
            url=entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
            data_origin="yt-dlp:search",
        ))
    log(f"  '{keyword}': {len(rows)} search result(s)")
    return rows


def channel_recent_ids(channel_url: str, limit: int) -> tuple[dict, list[str]]:
    """Supply axis. Recent uploads of one channel plus its subscriber count."""
    raw = run_ytdlp(["--flat-playlist", "--playlist-end", str(limit), "-J", channel_url])
    if not raw.strip():
        return {}, []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}, []
    meta = {
        "channel": payload.get("channel") or payload.get("title") or "",
        "channel_id": payload.get("channel_id") or "",
        "subscribers": int(payload.get("channel_follower_count") or 0),
    }
    ids = [e.get("id") for e in (payload.get("entries") or []) if e.get("id")]
    return meta, ids


def fetch_details(video_ids: list[str], batch_size: int, pause: float) -> dict[str, dict]:
    """Upload date, subscriber count and likes. Batched to amortise startup."""
    details: dict[str, dict] = {}
    for start in range(0, len(video_ids), batch_size):
        chunk = video_ids[start:start + batch_size]
        urls = [f"https://www.youtube.com/watch?v={vid}" for vid in chunk]
        raw = run_ytdlp(["--skip-download", "-j", *urls], timeout=420)
        got = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("id"):
                details[item["id"]] = item
                got += 1
        log(f"  detail batch {start // batch_size + 1}: {got}/{len(chunk)} video(s)")
        if start + batch_size < len(video_ids):
            time.sleep(pause)
    return details


def apply_detail(row: dict, item: dict, origin: str) -> dict:
    upload = str(item.get("upload_date") or "")
    return {**row,
            "title": item.get("title") or row["title"],
            "channel": item.get("channel") or row["channel"],
            "channel_id": item.get("channel_id") or row["channel_id"],
            "subscribers": int(item.get("channel_follower_count") or 0),
            "view_count": int(item.get("view_count") or row["view_count"] or 0),
            "like_count": int(item.get("like_count") or 0),
            "comment_count": int(item.get("comment_count") or 0),
            "upload_date": upload, "days_since_upload": days_since(upload),
            "duration_sec": int(item.get("duration") or row["duration_sec"] or 0),
            "data_origin": origin}


def pick_enrich_targets(rows: list[dict], top_n: int) -> list[str]:
    """Top viewed hits per keyword, so detail calls go where the signal is."""
    by_keyword: dict[str, list[dict]] = {}
    for row in rows:
        by_keyword.setdefault(row["source_keyword"], []).append(row)
    targets: list[str] = []
    for keyword_rows in by_keyword.values():
        ranked = sorted(keyword_rows, key=lambda r: int(r["view_count"] or 0), reverse=True)
        targets.extend(r["video_id"] for r in ranked[:top_n])
    return list(dict.fromkeys(targets))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote {path.name}: {len(rows)} row(s)")


def collect_channels(cfg: dict, search_rows: list[dict], details: dict) -> tuple[list[dict], list[str]]:
    """Own channel plus the rival channels that show up most in search."""
    coll = cfg["collection"]
    own_meta, own_ids = channel_recent_ids(cfg["channel"]["url"], coll["channel_recent_videos"])
    log(f"  own channel '{own_meta.get('channel', '')}': {len(own_ids)} recent upload(s), "
        f"{own_meta.get('subscribers', 0)} subscriber(s)")
    counts: dict[str, tuple[str, int]] = {}
    for row in search_rows:
        name, cid = row["channel"], row["channel_id"]
        if not cid or cid == own_meta.get("channel_id"):
            continue
        prev = counts.get(cid, (name, 0))
        counts[cid] = (name, prev[1] + 1)
    rivals = sorted(counts.items(), key=lambda kv: kv[1][1], reverse=True)[:coll["competitor_channels_max"]]
    collected = [("own_channel", own_meta, own_ids)]
    for cid, (name, hits) in rivals:
        meta, ids = channel_recent_ids(f"https://www.youtube.com/channel/{cid}/videos",
                                       coll["channel_recent_videos"])
        log(f"  rival '{meta.get('channel', name)}' ({hits} search hit(s)): {len(ids)} recent upload(s), "
            f"{meta.get('subscribers', 0)} subscriber(s)")
        collected.append(("channel", meta, ids))
    rows, pending = [], []
    for source_type, meta, ids in collected:
        for vid in ids:
            rows.append(blank_row(video_id=vid, channel=meta.get("channel", ""),
                                  channel_id=meta.get("channel_id", ""),
                                  subscribers=meta.get("subscribers", 0), source_keyword="",
                                  source_type=source_type,
                                  url=f"https://www.youtube.com/watch?v={vid}",
                                  data_origin="yt-dlp:channel"))
            if vid not in details:
                pending.append(vid)
    return rows, list(dict.fromkeys(pending))


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect YouTube topic signals for QJC")
    parser.add_argument("--config", default=str(HERE / "radar_config.json"))
    parser.add_argument("--outdir", default=str(HERE / "results"))
    parser.add_argument("--bd", choices=["off", "probe", "mock", "live"], default="probe",
                        help="Bright Data path: probe tests credentials, mock replays fixtures")
    parser.add_argument("--no-own-analytics", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    coll = cfg["collection"]

    log(f"collect start, {len(cfg['keywords'])} keyword(s), channel {cfg['channel']['handle']}")
    search_rows: list[dict] = []
    for keyword in cfg["keywords"]:
        search_rows.extend(search_keyword(keyword, coll["search_results_per_keyword"]))
    log(f"demand axis: {len(search_rows)} search row(s), "
        f"{len({r['video_id'] for r in search_rows})} unique video(s)")

    targets = pick_enrich_targets(search_rows, coll["enrich_top_n_per_keyword"])
    log(f"enriching {len(targets)} search video(s) with upload date and subscriber count")
    details = fetch_details(targets, coll["detail_batch_size"], coll["sleep_between_batches_sec"])

    channel_rows, pending = collect_channels(cfg, search_rows, details)
    log(f"supply axis: {len(channel_rows)} channel upload row(s), enriching {len(pending)}")
    details.update(fetch_details(pending, coll["detail_batch_size"], coll["sleep_between_batches_sec"]))

    rows = []
    for row in search_rows:
        item = details.get(row["video_id"])
        rows.append(apply_detail(row, item, "yt-dlp:search+detail") if item else row)
    for row in channel_rows:
        item = details.get(row["video_id"])
        rows.append(apply_detail(row, item, "yt-dlp:channel+detail") if item else row)

    if args.bd == "probe":
        bd_adapter.probe_live(cfg["brightdata"]["dataset_id"], cfg["keywords"][0], log)
    elif args.bd in ("mock", "live"):
        rows.extend(bd_adapter.collect_bd(cfg["brightdata"]["dataset_id"],
                                          cfg["brightdata"]["dataset_key"],
                                          cfg["keywords"], mock=args.bd == "mock", log=log))

    write_csv(outdir / f"videos_{STAMP}.csv", rows, FIELDS)
    if not args.no_own_analytics:
        own = yt_analytics.own_channel_rows(log=log)
        if own:
            write_csv(outdir / f"own_channel_{STAMP}.csv", own, yt_analytics.FIELDS)
    log("collect done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
