#!/usr/bin/env bash
# Demo: detect a competitor price change across two bundled HTML snapshots.
# Fully offline — uses --html to feed local files instead of fetching.
set -e
cd "$(dirname "$0")/../.."

PY="${PYTHON:-python}"
STORE="$(mktemp -t pricepulse.XXXXXX.db)"
URL="https://acme.example/pricing"   # placeholder identity; never fetched

cleanup() { rm -f "$STORE"; }
trap cleanup EXIT

echo "== add tracked page + extractors =="
"$PY" -m pricepulse add "$URL" --store "$STORE" \
  --label "Acme Cloud pricing" \
  --price "pro:css-ish:.plan-pro .price" \
  --price "team:css-ish:.plan-team .price" \
  --feature "plans:css-ish:.plan-name" \
  --feature "pro_features:css-ish:.plan-pro .feature"

echo
echo "== snapshot OLD page =="
"$PY" -m pricepulse snapshot "$URL" --store "$STORE" --html demos/01-basic/competitor-old.html

echo
echo "== snapshot NEW page =="
"$PY" -m pricepulse snapshot "$URL" --store "$STORE" --html demos/01-basic/competitor-new.html

echo
echo "== diff (should show Pro price up 49 -> 59, Team plan added, Audit logs added) =="
"$PY" -m pricepulse diff "$URL" --store "$STORE"
