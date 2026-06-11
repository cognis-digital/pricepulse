# Demo 01 — Detecting a competitor price change

This scenario runs `pricepulse` over two bundled local HTML snapshots of a
competitor pricing page (`competitor-old.html` and `competitor-new.html`) and
shows the detected differences. It is fully **offline** — `snapshot --html`
feeds local files instead of fetching, so the demo never touches the network.

## Run it

```bash
bash demos/01-basic/run.sh
# or on Windows:
set PYTHON=C:\Python314\python.exe && bash demos/01-basic/run.sh
```

## What it should catch

Between the old and new snapshots, the competitor:

| Field          | Change                                        | Type           |
|----------------|-----------------------------------------------|----------------|
| `pro`          | Pro plan price **$49 → $59** (+20.41%)        | price_up       |
| `team`         | New **Team** plan at $99 appears              | price_set      |
| `plans`        | "Team" plan name added                        | feature_added  |
| `pro_features` | "Audit logs" added to the Pro feature list    | feature_added  |

The `diff` output ends with `RESULT: CHANGED`. Wire `--webhook URL` onto
`diff` or `watch` to forward the change record to Slack/Teams/a SIEM.

## Extractors

Two extractor kinds are supported (standard library only — no parser deps):

* **css-ish** — `tag.class`, `.class`, `#id`, `tag#id`, or bare `tag`
  (e.g. `.plan-pro .price` is supported via the trailing token; use the most
  specific single selector your page allows).
* **regex** — first capture group, or whole match (best for prices).
