"""Core engine for pricepulse — competitor pricing/feature-change monitor.

Standard library only. The pipeline is:

    add(url, ...)   -> register a tracked page + its extractors in the store
    snapshot(...)   -> fetch each page over urllib, run the extractors, persist
    diff(...)       -> compare the two most recent snapshots and classify changes

Extraction is deliberately dependency-free. Rather than pull in a full HTML
parser we support two extractor kinds that cover real pricing pages well:

  * "regex"    — a Python regular expression; the first capture group (or the
                 whole match) is the extracted value. Best for prices.
  * "css-ish"  — a light tag/class selector ("span.price", ".plan-name", "h2")
                 implemented over the stdlib ``html.parser``. The text content
                 of every matching element is returned. Best for plan/feature
                 lists.

A *page* tracks one URL and a set of named *fields*. Each field has a kind
(price | feature) plus an extractor. Prices are parsed to floats so diffs can
report up/down deltas; features are treated as a set so diffs can report
plan/feature added/removed.

The store is a single SQLite database (stdlib ``sqlite3``) so snapshots persist
across runs and the whole tool stays a self-contained file.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

TOOL_NAME = "pricepulse"
TOOL_VERSION = "0.1.0"

DEFAULT_STORE = "pricepulse.db"
USER_AGENT = f"{TOOL_NAME}/{TOOL_VERSION} (+https://cognis.digital)"

FIELD_KINDS = ("price", "feature")
EXTRACTOR_KINDS = ("regex", "css-ish")


class PricePulseError(Exception):
    """Raised for user-facing errors (bad selector, missing page, etc.)."""


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

@dataclass
class _SimpleSel:
    """One compound selector token: optional tag + optional class + optional id."""
    tag: Optional[str]
    klass: Optional[str]
    idsel: Optional[str]

    def matches(self, tag: str, attr: Dict[str, str]) -> bool:
        if self.tag and tag != self.tag:
            return False
        if self.klass and self.klass not in attr.get("class", "").split():
            return False
        if self.idsel and attr.get("id", "") != self.idsel:
            return False
        return True


class _SelectorParser(HTMLParser):
    """Collect text content of elements matching a tiny CSS-ish selector.

    Supported selector forms (single token):
        "span"          — every <span>
        ".price"        — any element with class "price"
        "span.price"    — <span class="price">
        "#hero"         — element with id "hero"
        "span#hero"     — <span id="hero">

    Plus a descendant combinator (space-separated tokens):
        ".plan-pro .price"  — any ".price" inside a ".plan-pro"

    With a descendant selector, the *final* token's elements are captured but
    only while inside an ancestor matching every preceding token in order.
    """

    def __init__(self, selectors: List[_SimpleSel]):
        super().__init__(convert_charrefs=True)
        self._sels = selectors           # ancestors..., last = target
        self._target = selectors[-1]
        self._ancestors = selectors[:-1]
        self._stack: List[Tuple[str, Dict[str, str]]] = []
        self._capture_depth = 0          # nesting inside a captured target
        self._buf: List[str] = []
        self.matches: List[str] = []

    def _ancestors_satisfied(self) -> bool:
        """True if the open-tag stack contains the ancestor tokens in order."""
        idx = 0
        for tag, attr in self._stack:
            if idx < len(self._ancestors) and self._ancestors[idx].matches(tag, attr):
                idx += 1
        return idx == len(self._ancestors)

    def handle_starttag(self, tag, attrs):
        attr = {k: (v or "") for k, v in attrs}
        if self._capture_depth > 0:
            self._capture_depth += 1
            self._stack.append((tag, attr))
            return
        if self._target.matches(tag, attr) and self._ancestors_satisfied():
            self._capture_depth = 1
            self._buf = []
        self._stack.append((tag, attr))

    def handle_startendtag(self, tag, attrs):
        attr = {k: (v or "") for k, v in attrs}
        if self._capture_depth == 0 and self._target.matches(tag, attr) \
                and self._ancestors_satisfied():
            self.matches.append("")

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()
        if self._capture_depth > 0:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                self.matches.append(text)

    def handle_data(self, data):
        if self._capture_depth > 0:
            self._buf.append(data)


def _parse_token(token: str) -> _SimpleSel:
    m = re.fullmatch(r"([a-zA-Z0-9]+)?((?:[.#][\w\-]+)*)", token)
    if not m:
        raise PricePulseError(
            f"unsupported css-ish selector token: {token!r} "
            "(use tag, .class, #id, or tag.class)")
    tag = m.group(1)
    klass = idsel = None
    for part in re.findall(r"[.#][\w\-]+", m.group(2) or ""):
        if part[0] == ".":
            klass = part[1:]
        else:
            idsel = part[1:]
    if not tag and not klass and not idsel:
        raise PricePulseError(f"selector matches everything: {token!r}")
    return _SimpleSel(tag.lower() if tag else None, klass, idsel)


def _parse_selector(selector: str) -> List[_SimpleSel]:
    """Parse a (possibly descendant) css-ish selector into ordered tokens."""
    sel = selector.strip()
    if not sel:
        raise PricePulseError("empty css-ish selector")
    return [_parse_token(t) for t in sel.split()]


def extract(html: str, kind: str, pattern: str) -> List[str]:
    """Run an extractor against HTML and return the raw matched strings."""
    if kind == "regex":
        try:
            rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise PricePulseError(f"bad regex {pattern!r}: {exc}") from exc
        out: List[str] = []
        for m in rx.finditer(html):
            out.append(m.group(1) if m.groups() else m.group(0))
        return out
    if kind == "css-ish":
        selectors = _parse_selector(pattern)
        parser = _SelectorParser(selectors)
        parser.feed(html)
        return [m for m in parser.matches if m != ""]
    raise PricePulseError(f"unknown extractor kind: {kind!r}")


_PRICE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_price(text: str) -> Optional[float]:
    """Pull the first number out of a price string ("$29.00/mo" -> 29.0)."""
    if text is None:
        return None
    m = _PRICE_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def extract_field(html: str, kind: str, extractor_kind: str, pattern: str) -> Any:
    """Extract a single field's value from HTML.

    Price fields return a float (or None). Feature fields return a sorted,
    de-duplicated list of strings.
    """
    raw = extract(html, extractor_kind, pattern)
    if kind == "price":
        for r in raw:
            p = parse_price(r)
            if p is not None:
                return p
        return None
    # feature
    seen: Dict[str, None] = {}
    for r in raw:
        v = re.sub(r"\s+", " ", r).strip()
        if v:
            seen.setdefault(v, None)
    return sorted(seen)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def fetch(url: str, timeout: float = 15.0) -> str:
    """Fetch a URL (or a local file:// path) and return decoded text.

    Raises :class:`PricePulseError` on network failure so callers can degrade
    gracefully when offline.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise PricePulseError(f"fetch failed for {url}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class FieldDef:
    name: str
    kind: str               # price | feature
    extractor_kind: str     # regex | css-ish
    pattern: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "extractor_kind": self.extractor_kind,
            "pattern": self.pattern,
        }


@dataclass
class Snapshot:
    page_url: str
    taken_at: float
    values: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_url": self.page_url,
            "taken_at": self.taken_at,
            "values": self.values,
        }


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

class Store:
    """SQLite-backed persistence for pages, field defs, and snapshots."""

    def __init__(self, path: str = DEFAULT_STORE):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pages (
                url   TEXT PRIMARY KEY,
                label TEXT,
                added_at REAL
            );
            CREATE TABLE IF NOT EXISTS fields (
                url   TEXT,
                name  TEXT,
                kind  TEXT,
                extractor_kind TEXT,
                pattern TEXT,
                PRIMARY KEY (url, name)
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                taken_at REAL,
                values_json TEXT
            );
            """
        )
        self.conn.commit()

    # -- pages / fields ---------------------------------------------------- #

    def add_page(self, url: str, fields: List[FieldDef], label: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pages(url, label, added_at) VALUES (?,?,?)",
            (url, label, time.time()),
        )
        for f in fields:
            self.conn.execute(
                "INSERT OR REPLACE INTO fields"
                "(url, name, kind, extractor_kind, pattern) VALUES (?,?,?,?,?)",
                (url, f.name, f.kind, f.extractor_kind, f.pattern),
            )
        self.conn.commit()

    def pages(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT url, label, added_at FROM pages ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def fields_for(self, url: str) -> List[FieldDef]:
        rows = self.conn.execute(
            "SELECT name, kind, extractor_kind, pattern FROM fields WHERE url=?"
            " ORDER BY name",
            (url,),
        ).fetchall()
        return [FieldDef(r["name"], r["kind"], r["extractor_kind"], r["pattern"])
                for r in rows]

    # -- snapshots --------------------------------------------------------- #

    def save_snapshot(self, snap: Snapshot) -> int:
        cur = self.conn.execute(
            "INSERT INTO snapshots(url, taken_at, values_json) VALUES (?,?,?)",
            (snap.page_url, snap.taken_at, json.dumps(snap.values)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def last_snapshots(self, url: str, n: int = 2) -> List[Snapshot]:
        rows = self.conn.execute(
            "SELECT url, taken_at, values_json FROM snapshots WHERE url=?"
            " ORDER BY taken_at DESC, id DESC LIMIT ?",
            (url, n),
        ).fetchall()
        return [
            Snapshot(r["url"], r["taken_at"], json.loads(r["values_json"]))
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------- #
# High-level operations
# --------------------------------------------------------------------------- #

def make_field(name: str, kind: str, extractor_kind: str, pattern: str) -> FieldDef:
    if kind not in FIELD_KINDS:
        raise PricePulseError(f"field kind must be one of {FIELD_KINDS}, got {kind!r}")
    if extractor_kind not in EXTRACTOR_KINDS:
        raise PricePulseError(
            f"extractor kind must be one of {EXTRACTOR_KINDS}, got {extractor_kind!r}")
    if not name:
        raise PricePulseError("field name is required")
    # validate the extractor compiles / parses up front
    extract("", extractor_kind, pattern)
    return FieldDef(name, kind, extractor_kind, pattern)


def take_snapshot(store: Store, url: str, html: Optional[str] = None,
                  timeout: float = 15.0) -> Snapshot:
    """Fetch (unless ``html`` supplied), extract all fields, persist a snapshot."""
    fields = store.fields_for(url)
    if not fields:
        raise PricePulseError(f"no tracked page: {url} (add it first)")
    content = html if html is not None else fetch(url, timeout=timeout)
    values: Dict[str, Any] = {}
    for f in fields:
        values[f.name] = extract_field(content, f.kind, f.extractor_kind, f.pattern)
    snap = Snapshot(page_url=url, taken_at=time.time(), values=values)
    store.save_snapshot(snap)
    return snap


def diff_values(old: Dict[str, Any], new: Dict[str, Any],
                fields: List[FieldDef]) -> List[Dict[str, Any]]:
    """Classify changes between two snapshot value maps.

    Returns a list of change records. Change ``type`` is one of:
      price_up, price_down, price_set, price_cleared,
      feature_added, feature_removed, value_changed.
    """
    changes: List[Dict[str, Any]] = []
    kinds = {f.name: f.kind for f in fields}
    names = list(dict.fromkeys(list(old) + list(new)))

    for name in names:
        kind = kinds.get(name, "feature")
        o = old.get(name)
        n = new.get(name)
        if o == n:
            continue

        if kind == "price":
            if o is None and n is not None:
                changes.append({"field": name, "type": "price_set",
                                "old": None, "new": n})
            elif o is not None and n is None:
                changes.append({"field": name, "type": "price_cleared",
                                "old": o, "new": None})
            elif o is not None and n is not None:
                delta = round(n - o, 4)
                pct = round((delta / o) * 100, 2) if o else None
                changes.append({
                    "field": name,
                    "type": "price_up" if n > o else "price_down",
                    "old": o, "new": n, "delta": delta, "pct": pct,
                })
        else:  # feature set
            old_set = set(o or [])
            new_set = set(n or [])
            for added in sorted(new_set - old_set):
                changes.append({"field": name, "type": "feature_added",
                                "value": added})
            for removed in sorted(old_set - new_set):
                changes.append({"field": name, "type": "feature_removed",
                                "value": removed})
            if not isinstance(o, (list, type(None))) or not isinstance(n, (list, type(None))):
                changes.append({"field": name, "type": "value_changed",
                                "old": o, "new": n})
    return changes


def diff_page(store: Store, url: str) -> Dict[str, Any]:
    """Diff the two most recent snapshots for a page."""
    snaps = store.last_snapshots(url, n=2)
    fields = store.fields_for(url)
    if not snaps:
        raise PricePulseError(f"no snapshots for {url}")
    if len(snaps) == 1:
        return {
            "page_url": url,
            "changed": False,
            "reason": "only one snapshot — nothing to compare yet",
            "changes": [],
            "current": snaps[0].values,
        }
    new, old = snaps[0], snaps[1]
    changes = diff_values(old.values, new.values, fields)
    return {
        "page_url": url,
        "changed": bool(changes),
        "old_taken_at": old.taken_at,
        "new_taken_at": new.taken_at,
        "changes": changes,
        "current": new.values,
    }


def summarize_change(change: Dict[str, Any]) -> str:
    """One-line human description of a single change record."""
    t = change["type"]
    fld = change.get("field", "?")
    if t in ("price_up", "price_down"):
        arrow = "UP" if t == "price_up" else "DOWN"
        pct = change.get("pct")
        pct_s = f" ({pct:+.2f}%)" if pct is not None else ""
        return f"{fld}: price {arrow} {change['old']} -> {change['new']}{pct_s}"
    if t == "price_set":
        return f"{fld}: price set to {change['new']}"
    if t == "price_cleared":
        return f"{fld}: price removed (was {change['old']})"
    if t == "feature_added":
        return f"{fld}: + plan/feature added: {change['value']}"
    if t == "feature_removed":
        return f"{fld}: - plan/feature removed: {change['value']}"
    return f"{fld}: changed {change.get('old')} -> {change.get('new')}"
