# pricepulse — competitor pricing/feature-change monitor with diff alerts

> Part of the **[Cognis Neural Suite](https://github.com/cognis-digital)** by [Cognis Digital](https://cognis.digital)
> Cognis Open Collaboration License (COCL) v1.0 · domain: `business`

[![PyPI](https://img.shields.io/pypi/v/cognis-pricepulse.svg)](https://pypi.org/project/cognis-pricepulse/)
[![CI](https://github.com/cognis-digital/pricepulse/actions/workflows/ci.yml/badge.svg)](https://github.com/cognis-digital/pricepulse/actions)
[![License: COCL 1.0](https://img.shields.io/badge/License-COCL%201.0-2b6cb0.svg)](LICENSE)
[![Suite](https://img.shields.io/badge/Cognis-Neural%20Suite-6b46c1.svg)](https://github.com/cognis-digital)

**Monitor competitor pricing and feature pages for changes, and get diff alerts when a price moves or a plan/feature appears or disappears.**

*Business intelligence — track the market without standing up heavyweight infrastructure.*

## Usage — step by step

1. **Install** from source (Python 3.9+):
   ```bash
   pip install .
   ```
2. **Add** a page to track with price/feature extractors (`NAME:KIND:PATTERN`):
   ```bash
   pricepulse add https://example.com/pricing --price "pro:regex:[0-9]+/mo" --label "Competitor Pro"
   ```
3. **Snapshot** all tracked pages (fetch + extract + store):
   ```bash
   pricepulse snapshot
   ```
4. **Diff** a page vs its previous snapshot and read the change set:
   ```bash
   pricepulse diff https://example.com/pricing --format json
   ```
5. **Automate** — loop on an interval and POST changes to a webhook (or a single `--once` pass in CI):
   ```bash
   pricepulse watch --interval 300 --webhook https://hooks.example.com/pricepulse
   ```
   Also: `pricepulse list` and `pricepulse mcp` (MCP stdio server).

## Why

Pricing pages are public, change quietly, and matter a lot. `pricepulse` tracks
the URLs you care about, extracts the prices and plan/feature lists with
dependency-free extractors, snapshots them to a local SQLite store, and tells
you exactly what changed since last time — price up/down with deltas, plans
added/removed, features added/removed. It is single-purpose, scriptable,
CI-friendly, self-hostable, and **standard library only** (no pip installs).
Fire a webhook on any change, or expose it to an agent over MCP.

<!-- cognis:domains:start -->
## Domains

**Primary domain:** Cyber & Security  ·  **JTF MERIDIAN division:** NULLBYTE · SPECTER

**Topics:** `cognis` `security` `infosec` `cybersecurity` `blue-team`

Part of the **Cognis Neural Suite** — 300+ source-available tools organized across 12 domains under the JTF MERIDIAN command structure. See the [suite on GitHub](https://github.com/cognis-digital) and [jtf-meridian](https://github.com/cognis-digital/jtf-meridian) for how the pieces fit together.
<!-- cognis:domains:end -->

## Install

```bash
pip install cognis-pricepulse
# or, from this repo:
pip install -e ".[dev]"
```

No dependencies are required to run — Python 3.10+ is enough.

## Quick start

```bash
pricepulse --version

# 1. track a page + extractors (price + feature, regex or css-ish)
pricepulse add https://acme.example/pricing \
  --label "Acme pricing" \
  --price   "pro:css-ish:.plan-pro .price" \
  --feature "plans:css-ish:.plan-name"

# 2. snapshot it (fetched over urllib, or --html for a local file)
pricepulse snapshot https://acme.example/pricing

# 3. see what changed vs the previous snapshot
pricepulse diff https://acme.example/pricing
pricepulse diff https://acme.example/pricing --format json --webhook "$SLACK_WEBHOOK_URL"

# 4. or just watch on an interval and alert on change
pricepulse watch https://acme.example/pricing --interval 3600 --webhook "$SLACK_WEBHOOK_URL"

# expose as an MCP server (Cognis.Studio / Claude Desktop / Cursor)
pricepulse mcp
```

## Subcommands

| Command    | What it does                                                            |
|------------|-------------------------------------------------------------------------|
| `add`      | Register a page + named price/feature extractors.                       |
| `snapshot` | Fetch (or read `--html`), extract every field, persist to the store.    |
| `diff`     | Compare the two most recent snapshots; classify changes; optional webhook. |
| `watch`    | Loop `snapshot`+`diff` every `--interval` seconds, webhook on change.    |
| `list`     | List tracked pages and their extractors.                                |
| `mcp`      | Run as a stdio JSON-RPC MCP server (`extract`, `diff_html` tools).       |

## Extractors

Each field has a **kind** (`price` or `feature`) and an **extractor**:

* **regex** — a Python regex; the first capture group (or whole match) is taken.
  Prices are parsed to floats, e.g. `r"\$(\d+)"`.
* **css-ish** — a light selector over the stdlib HTML parser. Supports
  `tag`, `.class`, `#id`, `tag.class`, and a descendant combinator
  (`.plan-pro .price` = a `.price` inside a `.plan-pro`).

Field specs on the CLI use `NAME:EXTRACTORKIND:PATTERN`, e.g.
`--price "pro:css-ish:.plan-pro .price"`.

Price fields diff as `price_up` / `price_down` (with `delta` and `pct`),
`price_set`, `price_cleared`. Feature fields diff as `feature_added` /
`feature_removed`.

## Built-in demo

A fully offline demo over two bundled HTML snapshots showing a detected price
change. See [`demos/01-basic/SCENARIO.md`](demos/01-basic/SCENARIO.md).

```bash
bash demos/01-basic/run.sh
```

It detects: Pro price **$49 → $59 (+20.41%)**, a new **Team** plan at $99, and
an **Audit logs** feature added to the Pro plan.

## Storage

Snapshots persist to a single SQLite file (`pricepulse.db` by default, override
with `--store PATH`). The schema is tiny: `pages`, `fields`, `snapshots`.

## Graceful offline

A failed fetch never crashes a run: `snapshot` reports the error per-page and
exits non-zero; `watch` logs the error and keeps polling the other pages.

## Interoperability

`{}` composes with the 300+ tool Cognis suite — JSON in/out and a shared
OpenAI-compatible `/v1` backbone. See **[INTEROP.md](INTEROP.md)** for the
suite map, composition patterns, and reference stacks.

## License

Cognis Open Collaboration License (COCL) v1.0 — see [LICENSE](LICENSE).
