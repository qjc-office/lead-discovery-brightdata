#!/usr/bin/env python3
"""Level 3 (part 2): turn a diff JSON into a Slack message.

Usage
  python3 notify.py --dry-run                       # print the payload, send nothing
  python3 notify.py --diff data/diff_x.json --dry-run
  python3 notify.py                                 # POST to $SLACK_WEBHOOK_URL

The webhook URL is read from the SLACK_WEBHOOK_URL environment variable only.
Never hardcode it. See .env.example.

Exit codes
  0  sent (or printed in dry-run)
  1  nothing to report
  2  input or transport error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MAX_ITEMS_IN_MESSAGE = 10


def newest_diff(data_dir: Path) -> Path | None:
    """가장 최근에 만들어진 diff 파일.

    파일명 정렬로 고르면 안 된다. diff_amazon_products_20260827.json 과
    diff_public_job_postings_20260803.json 이 같이 있으면 알파벳순으로는 항상
    뒤엣것이 이겨서, 오늘 만든 결과 대신 며칠 전 결과를 알림으로 보내게 된다.
    """
    files = sorted(data_dir.glob("diff_*.json"), key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


def _item_lines(items: list[dict], marker: str) -> list[str]:
    lines = []
    for item in items[:MAX_ITEMS_IN_MESSAGE]:
        title = item.get("title") or item.get("id") or "(untitled)"
        subtitle = item.get("subtitle") or ""
        url = item.get("url") or ""
        label = f"{title} ({subtitle})" if subtitle else title
        lines.append(f"{marker} <{url}|{label}>" if url else f"{marker} {label}")
    if len(items) > MAX_ITEMS_IN_MESSAGE:
        lines.append(f"_...and {len(items) - MAX_ITEMS_IN_MESSAGE} more_")
    return lines


def _change_lines(items: list[dict]) -> list[str]:
    """One line per record whose watched values moved, e.g. a price drop."""
    lines = []
    for item in items[:MAX_ITEMS_IN_MESSAGE]:
        title = item.get("title") or item.get("id") or "(untitled)"
        url = item.get("url") or ""
        label = title if len(title) <= 60 else title[:57] + "..."
        moves = ", ".join(
            f"{field} {c.get('before') or '-'} → {c.get('after') or '-'}"
            for field, c in (item.get("changes") or {}).items()
        )
        head = f"<{url}|{label}>" if url else label
        lines.append(f"• {head}\n  {moves}")
    if len(items) > MAX_ITEMS_IN_MESSAGE:
        lines.append(f"_...and {len(items) - MAX_ITEMS_IN_MESSAGE} more_")
    return lines


def build_payload(diff: dict, mock: bool) -> dict:
    """Slack Block Kit payload. https://api.slack.com/block-kit"""
    label = diff.get("label", diff.get("target", "monitor"))
    changed_count = diff.get("changed_count", 0)
    if changed_count and not diff["new_count"]:
        header = f"Values changed: {changed_count}"
    elif changed_count:
        header = f"New items: {diff['new_count']}, changed: {changed_count}"
    else:
        header = f"New items detected: {diff['new_count']}"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": False}},
        {"type": "context", "elements": [
            {"type": "mrkdwn",
             "text": f"*{label}* | checked {diff['current_count']} record(s) "
                     f"| removed {diff['removed_count']} | {diff['generated_at']}"}
        ]},
    ]
    if mock:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": ":test_tube: *MOCK RUN*. Sample data, not a live collection."}
        ]})
    sections = []
    if diff["new_items"]:
        sections.append("*New*\n" + "\n".join(_item_lines(diff["new_items"], "•")))
    if diff["removed_items"]:
        sections.append("*No longer listed*\n" + "\n".join(_item_lines(diff["removed_items"], "•")))
    if diff.get("changed_items"):
        sections.append("*Changed*\n" + "\n".join(_change_lines(diff["changed_items"])))
    for text in sections:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    return {"text": f"{label}: {header}", "blocks": blocks}


def post(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "replace").strip()
        print(f"[notify] slack responded {resp.status} {body}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Send a Bright Data monitoring diff to Slack")
    ap.add_argument("--diff", help="path to a diff JSON (default: newest in data/)")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--dry-run", action="store_true", help="print the payload instead of sending it")
    ap.add_argument("--mock", action="store_true", help="tag the message as a mock run")
    ap.add_argument("--always", action="store_true", help="send even when nothing changed")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    diff_path = Path(args.diff) if args.diff else newest_diff(Path(args.data_dir))
    if diff_path is None or not diff_path.exists():
        print("[notify] ERROR no diff JSON found. Run diff_checker.py first.", file=sys.stderr)
        return 2
    diff = json.loads(diff_path.read_text(encoding="utf-8"))
    print(f"[notify] source={diff_path}")

    touched = diff.get("new_count") or diff.get("removed_count") or diff.get("changed_count")
    if not touched and not args.always:
        print("[notify] no changes, nothing to send")
        return 1

    payload = build_payload(diff, mock=args.mock)
    if args.dry_run:
        print("[notify] DRY RUN, payload below (nothing sent)")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print("[notify] ERROR SLACK_WEBHOOK_URL is not set. Use --dry-run to preview.", file=sys.stderr)
        return 2
    try:
        post(webhook, payload)
    except urllib.error.HTTPError as exc:
        print(f"[notify] ERROR slack returned {exc.code}: "
              f"{exc.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
        return 2
    except urllib.error.URLError as exc:
        print(f"[notify] ERROR could not reach slack: {exc.reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
