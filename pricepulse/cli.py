"""Command-line interface for pricepulse.

Subcommands:
  add <url>      register a page + extractors (--price NAME:KIND:PATTERN,
                 --feature NAME:KIND:PATTERN)
  snapshot       fetch tracked pages, extract, persist
  diff           compare the two most recent snapshots of a page
  watch          loop snapshot+diff on an interval, fire a webhook on change
  list           list tracked pages
  mcp            run as an MCP server over stdio
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

from . import TOOL_NAME, TOOL_VERSION, DEFAULT_STORE
from .core import (
    PricePulseError,
    Store,
    diff_page,
    fetch,
    make_field,
    summarize_change,
    take_snapshot,
)


# --------------------------------------------------------------------------- #
# spec parsing:  NAME:EXTRACTORKIND:PATTERN
# --------------------------------------------------------------------------- #

def _parse_field_spec(kind: str, spec: str):
    """Parse a "name:extractor_kind:pattern" spec into a FieldDef.

    The pattern may itself contain ':' — only the first two ':' split.
    """
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise PricePulseError(
            f"--{kind} expects NAME:EXTRACTORKIND:PATTERN, got {spec!r}")
    name, ekind, pattern = parts[0].strip(), parts[1].strip(), parts[2]
    return make_field(name, kind, ekind, pattern)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _render_diff_table(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"{TOOL_NAME} diff — {result['page_url']}")
    lines.append("=" * 68)
    if not result["changed"]:
        lines.append(result.get("reason", "No changes since last snapshot."))
    else:
        for ch in result["changes"]:
            lines.append("  " + summarize_change(ch))
    lines.append("-" * 68)
    cur = result.get("current") or {}
    lines.append("current values:")
    for k in sorted(cur):
        lines.append(f"  {k} = {cur[k]}")
    lines.append("RESULT: " + ("CHANGED" if result["changed"] else "UNCHANGED"))
    return "\n".join(lines)


def _render_snapshot_table(snaps: List[Dict[str, Any]]) -> str:
    lines: List[str] = [f"{TOOL_NAME} snapshot — {len(snaps)} page(s)", "=" * 68]
    for s in snaps:
        lines.append(s["page_url"])
        if "error" in s:
            lines.append(f"  ERROR: {s['error']}")
            continue
        for k in sorted(s["values"]):
            lines.append(f"  {k} = {s['values'][k]}")
    return "\n".join(lines)


def _emit(text: str, out: Optional[str]) -> None:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)


def _post_webhook(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"webhook -> {r.status}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — best-effort, never crash a watch loop
        print(f"webhook error: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def _cmd_add(args: argparse.Namespace) -> int:
    try:
        fields = []
        for spec in (args.price or []):
            fields.append(_parse_field_spec("price", spec))
        for spec in (args.feature or []):
            fields.append(_parse_field_spec("feature", spec))
        if not fields:
            raise PricePulseError(
                "add at least one --price or --feature extractor")
        store = Store(args.store)
        store.add_page(args.url, fields, label=args.label or "")
        store.close()
    except PricePulseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"tracking {args.url} with {len(fields)} field(s): "
          + ", ".join(f.name for f in fields))
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        urls = [args.url] if args.url else [p["url"] for p in store.pages()]
        if not urls:
            print("error: no tracked pages (use `add` first)", file=sys.stderr)
            return 2
        results: List[Dict[str, Any]] = []
        any_error = False
        for url in urls:
            html = None
            if args.html:
                with open(args.html, "r", encoding="utf-8") as fh:
                    html = fh.read()
            try:
                snap = take_snapshot(store, url, html=html, timeout=args.timeout)
                results.append(snap.to_dict())
            except PricePulseError as exc:
                any_error = True
                results.append({"page_url": url, "error": str(exc)})
    finally:
        store.close()

    if args.format == "json":
        _emit(json.dumps(results, indent=2), args.out)
    else:
        _emit(_render_snapshot_table(results), args.out)
    # graceful offline: a fetch failure is reported, exit 1 (not a crash)
    return 1 if any_error else 0


def _cmd_diff(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        result = diff_page(store, args.url)
    except PricePulseError as exc:
        store.close()
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store.close()

    if args.format == "json":
        _emit(json.dumps(result, indent=2), args.out)
    else:
        _emit(_render_diff_table(result), args.out)

    if result["changed"] and args.webhook:
        _post_webhook(args.webhook, result)
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    iterations = 0
    try:
        while True:
            store = Store(args.store)
            try:
                urls = [args.url] if args.url else [p["url"] for p in store.pages()]
                for url in urls:
                    try:
                        take_snapshot(store, url, timeout=args.timeout)
                        result = diff_page(store, url)
                    except PricePulseError as exc:
                        print(f"[{time.strftime('%H:%M:%S')}] {url}: {exc}",
                              file=sys.stderr)
                        continue
                    stamp = time.strftime("%H:%M:%S")
                    if result["changed"]:
                        print(f"[{stamp}] CHANGED {url}")
                        for ch in result["changes"]:
                            print("    " + summarize_change(ch))
                        if args.webhook:
                            _post_webhook(args.webhook, result)
                    else:
                        print(f"[{stamp}] unchanged {url}")
            finally:
                store.close()
            iterations += 1
            if args.once or (args.max_iterations and iterations >= args.max_iterations):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("watch stopped.", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store = Store(args.store)
    pages = store.pages()
    rows = []
    for p in pages:
        fields = store.fields_for(p["url"])
        rows.append({**p, "fields": [f.to_dict() for f in fields]})
    store.close()
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("no tracked pages.")
        for r in rows:
            print(f"{r['url']}  ({r.get('label') or 'no label'})")
            for f in r["fields"]:
                print(f"    [{f['kind']}] {f['name']} <- "
                      f"{f['extractor_kind']}: {f['pattern']}")
    return 0


def _cmd_mcp(_args: argparse.Namespace) -> int:
    from .mcp_server import run_mcp_server
    run_mcp_server()
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def _add_common(sp: argparse.ArgumentParser, fmt: bool = True) -> None:
    # --store is accepted both before the subcommand (top-level) and after it,
    # so `pricepulse --store X add ...` and `pricepulse add ... --store X` both work.
    sp.add_argument("--store", default=None,
                    help=f"SQLite store path (default: {DEFAULT_STORE}).")
    if fmt:
        sp.add_argument("--format", choices=("table", "json"), default="table",
                        help="Output format (default: table).")
        sp.add_argument("--out", help="Write output to this file instead of stdout.")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Monitor competitor pricing/feature pages for changes "
                    "with diff alerts.",
    )
    p.add_argument("--version", action="version",
                   version=f"{TOOL_NAME} {TOOL_VERSION}")
    p.add_argument("--store", dest="store_top", default=None,
                   help=f"SQLite store path (default: {DEFAULT_STORE}).")
    sub = p.add_subparsers(dest="command")

    add = sub.add_parser("add", help="Track a page + price/feature extractors.")
    add.add_argument("url", help="Page URL (or file:// path) to track.")
    add.add_argument("--price", action="append", metavar="NAME:KIND:PATTERN",
                     help="Price extractor, e.g. pro:css-ish:.plan-pro .price")
    add.add_argument("--feature", action="append", metavar="NAME:KIND:PATTERN",
                     help="Feature extractor, e.g. plans:css-ish:.plan-name")
    add.add_argument("--label", help="Human label for the page.")
    _add_common(add, fmt=False)

    snap = sub.add_parser("snapshot", help="Fetch + extract + store a snapshot.")
    snap.add_argument("url", nargs="?", help="Specific page; default = all pages.")
    snap.add_argument("--html", help="Use this local HTML file instead of fetching.")
    snap.add_argument("--timeout", type=float, default=15.0)
    _add_common(snap)

    df = sub.add_parser("diff", help="Show changes vs the previous snapshot.")
    df.add_argument("url", help="Page URL to diff.")
    df.add_argument("--webhook", help="POST the diff JSON here if changed.")
    _add_common(df)

    w = sub.add_parser("watch", help="Loop snapshot+diff on an interval.")
    w.add_argument("url", nargs="?", help="Specific page; default = all pages.")
    w.add_argument("--interval", type=float, default=300.0,
                   help="Seconds between polls (default: 300).")
    w.add_argument("--timeout", type=float, default=15.0)
    w.add_argument("--webhook", help="POST the diff JSON here on change.")
    w.add_argument("--once", action="store_true", help="Run a single pass.")
    w.add_argument("--max-iterations", type=int, default=0,
                   help="Stop after N passes (0 = forever).")
    w.add_argument("--store", default=None,
                   help=f"SQLite store path (default: {DEFAULT_STORE}).")

    lst = sub.add_parser("list", help="List tracked pages and extractors.")
    _add_common(lst)

    mcp = sub.add_parser("mcp", help="Run as an MCP server (stdio JSON-RPC).")
    mcp.add_argument("--host", default=None, help="Reserved; stdio transport only.")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Resolve --store from either the top-level or the subcommand-level flag.
    sub_store = getattr(args, "store", None)
    top_store = getattr(args, "store_top", None)
    if hasattr(args, "store") or top_store is not None:
        args.store = sub_store or top_store or DEFAULT_STORE
    dispatch = {
        "add": _cmd_add,
        "snapshot": _cmd_snapshot,
        "diff": _cmd_diff,
        "watch": _cmd_watch,
        "list": _cmd_list,
        "mcp": _cmd_mcp,
    }
    fn = dispatch.get(args.command)
    if not fn:
        parser.print_help(sys.stderr)
        return 2
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
