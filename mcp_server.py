#!/usr/bin/env python3
"""Minimal stdio MCP server exposing this repo's monitoring scripts as agent tools.

Protocol: JSON-RPC 2.0 over newline-delimited stdin/stdout (MCP stdio transport).
Methods implemented: initialize, notifications/initialized, tools/list, tools/call, ping.

Register it with Claude Code via .mcp.json (see .mcp.json.example), then ask the
agent things like "any new AI engineer postings today?" and it will call
fetch_postings -> diff_check -> notify_slack on its own.

This server is separate from Bright Data's own MCP server. Use both:
  * brightdata  : Bright Data's hosted tools (search, scrape, 60+ tools)
  * qjc-monitor : this repo's pipeline (collect, diff, notify)

Standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "qjc-brightdata-monitor", "version": "0.1.0"}
TIMEOUT_SEC = 900

TOOLS = [
    {
        "name": "list_targets",
        "description": "List the monitoring targets defined in scraper_config.json, "
                       "with their Bright Data dataset_id and documentation link.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "fetch_postings",
        "description": "Collect the latest records for a target through the Bright Data "
                       "Web Scraper API and save them as a dated CSV. Set mock=true to replay "
                       "bundled sample fixtures instead of calling the API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "target key, e.g. amazon_products"},
                "mock": {"type": "boolean", "description": "replay fixtures instead of calling the API",
                         "default": True},
                "date": {"type": "string", "description": "output date stamp, YYYYMMDD"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "diff_check",
        "description": "Compare the two most recent CSV snapshots of a target and report "
                       "which records are new, which disappeared, and which changed. "
                       "Changed items include field-level moves such as a price drop "
                       "(final_price), availability, rating, or review count. Use this to "
                       "answer questions like 'did any price change today?'.",
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "notify_slack",
        "description": "Turn the newest diff into a Slack message. dry_run=true prints the "
                       "payload without sending. Sending needs SLACK_WEBHOOK_URL in the environment.",
        "inputSchema": {
            "type": "object",
            "properties": {"dry_run": {"type": "boolean", "default": True}},
            "additionalProperties": False,
        },
    },
]


def run_script(script: str, args: list[str]) -> tuple[int, str]:
    """Run one repo script and capture its combined output."""
    cmd = [sys.executable, str(ROOT / script), *args]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {TIMEOUT_SEC}s: {' '.join(cmd)}"
    except OSError as exc:
        return 1, f"could not start {script}: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def tool_list_targets(_: dict) -> tuple[bool, str]:
    cfg = json.loads((ROOT / "scraper_config.json").read_text(encoding="utf-8"))
    lines = [f"default_target: {cfg.get('default_target')}"]
    for key, target in (cfg.get("targets") or {}).items():
        lines.append(f"- {key}: {target.get('label')}")
        lines.append(f"    dataset_id: {target.get('dataset_id')}")
        lines.append(f"    docs: {target.get('doc_url')}")
    return False, "\n".join(lines)


def tool_fetch_postings(args: dict) -> tuple[bool, str]:
    argv = ["--mock"] if args.get("mock", True) else ["--live"]
    if args.get("target"):
        argv += ["--target", str(args["target"])]
    if args.get("date"):
        argv += ["--date", str(args["date"])]
    code, out = run_script("fetch_postings.py", argv)
    return code != 0, out


def tool_diff_check(args: dict) -> tuple[bool, str]:
    argv = ["--target", str(args["target"])] if args.get("target") else []
    code, out = run_script("diff_checker.py", argv)
    # exit 1 means "no changes", which is a valid answer rather than a failure.
    return code not in (0, 1), out


def tool_notify_slack(args: dict) -> tuple[bool, str]:
    argv = ["--mock"]
    if args.get("dry_run", True):
        argv.append("--dry-run")
    code, out = run_script("notify.py", argv)
    return code not in (0, 1), out


HANDLERS = {
    "list_targets": tool_list_targets,
    "fetch_postings": tool_fetch_postings,
    "diff_check": tool_diff_check,
    "notify_slack": tool_notify_slack,
}


def call_tool(params: dict) -> dict:
    name = params.get("name")
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        is_error, text = handler(params.get("arguments") or {})
    except Exception as exc:  # surfaced to the agent instead of killing the server
        return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": text or "(no output)"}], "isError": is_error}


def dispatch(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(message.get("params") or {})
    else:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }) + "\n")
            sys.stdout.flush()
            continue
        response = dispatch(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
