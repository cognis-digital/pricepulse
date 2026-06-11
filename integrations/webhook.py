#!/usr/bin/env python3
"""Minimal, dependency-free webhook forwarder for Cognis findings.

Reads JSON on stdin and POSTs it to a URL (Slack/Teams/SIEM/Jira bridge).
Usage:  pricepulse diff <url> --format json | python integrations/webhook.py --url URL
"""
from __future__ import annotations
import argparse, sys, urllib.request

def post(url: str, payload: bytes, headers: dict) -> int:
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"posted {len(payload)} bytes -> {r.status}")
        return 0
    except Exception as e:  # noqa: BLE001 — best-effort forwarder
        print(f"webhook error: {e}", file=sys.stderr)
        return 1

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--header", action="append", default=[], help="Key: Value")
    args = ap.parse_args()
    headers = {}
    for h in args.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    payload = sys.stdin.read().encode("utf-8")
    return post(args.url, payload, headers)

if __name__ == "__main__":
    sys.exit(main())
