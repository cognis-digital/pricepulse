"""pricepulse MCP server.

Exposes pricing/feature change detection as an MCP capability over stdio using
newline-delimited JSON-RPC 2.0. Standard library only — no SDK — so it runs
anywhere Python does and wires into Cognis.Studio, Claude Desktop, or Cursor:

    {"command": "python", "args": ["-m", "pricepulse", "mcp"]}

Implemented methods:
  * initialize  — handshake, advertises the tools capability
  * tools/list  — describes the `extract` and `diff_html` tools
  * tools/call  — runs a tool and returns JSON text content

`extract` runs a single extractor against supplied HTML; `diff_html` extracts a
field from two HTML documents (old/new) and classifies the change. Neither
touches the network, which keeps the MCP surface deterministic and offline-safe.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import (
    FieldDef,
    PricePulseError,
    diff_values,
    extract,
    extract_field,
)

PROTOCOL_VERSION = "2024-11-05"

_TOOLS = [
    {
        "name": "extract",
        "description": "Extract pricing or feature values from an HTML document "
                       "using a regex or css-ish (tag.class/#id) extractor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "HTML document text."},
                "kind": {"type": "string", "enum": ["price", "feature"]},
                "extractor_kind": {"type": "string", "enum": ["regex", "css-ish"]},
                "pattern": {"type": "string", "description": "Regex or selector."},
            },
            "required": ["html", "kind", "extractor_kind", "pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "diff_html",
        "description": "Extract one field from an old and a new HTML document and "
                       "classify the change (price up/down, feature added/removed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_html": {"type": "string"},
                "new_html": {"type": "string"},
                "name": {"type": "string", "description": "Field name."},
                "kind": {"type": "string", "enum": ["price", "feature"]},
                "extractor_kind": {"type": "string", "enum": ["regex", "css-ish"]},
                "pattern": {"type": "string"},
            },
            "required": ["old_html", "new_html", "name", "kind",
                         "extractor_kind", "pattern"],
            "additionalProperties": False,
        },
    },
]


def _result(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "extract":
        for key in ("html", "kind", "extractor_kind", "pattern"):
            if not isinstance(arguments.get(key), str):
                raise ValueError(f"`{key}` (string) is required")
        value = extract_field(
            arguments["html"], arguments["kind"],
            arguments["extractor_kind"], arguments["pattern"])
        payload: Dict[str, Any] = {"value": value}
    elif name == "diff_html":
        for key in ("old_html", "new_html", "name", "kind",
                    "extractor_kind", "pattern"):
            if not isinstance(arguments.get(key), str):
                raise ValueError(f"`{key}` (string) is required")
        fd = FieldDef(arguments["name"], arguments["kind"],
                      arguments["extractor_kind"], arguments["pattern"])
        old_v = extract_field(arguments["old_html"], fd.kind,
                              fd.extractor_kind, fd.pattern)
        new_v = extract_field(arguments["new_html"], fd.kind,
                              fd.extractor_kind, fd.pattern)
        changes = diff_values({fd.name: old_v}, {fd.name: new_v}, [fd])
        payload = {"old": old_v, "new": new_v, "changed": bool(changes),
                   "changes": changes}
    else:
        raise ValueError(f"unknown tool: {name}")

    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "isError": False,
    }


def handle_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch a single JSON-RPC request. Returns None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}
    is_notification = "id" not in req

    if method == "initialize":
        res = _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": TOOL_NAME, "version": TOOL_VERSION},
        })
        return None if is_notification else res

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return None if is_notification else _result(req_id, {})

    if method == "tools/list":
        return _result(req_id, {"tools": _TOOLS})

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            return _result(req_id, _call_tool(name, arguments))
        except (ValueError, PricePulseError) as exc:
            return _error(req_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return _error(req_id, -32603, f"internal error: {exc}")

    if is_notification:
        return None
    return _error(req_id, -32601, f"method not found: {method}")


def run_mcp_server(stdin=None, stdout=None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle_request(req)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":
    run_mcp_server()
