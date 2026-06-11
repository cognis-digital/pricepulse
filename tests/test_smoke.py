"""Smoke tests for pricepulse. Standard library only, no network."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricepulse import (  # noqa: E402
    TOOL_NAME,
    TOOL_VERSION,
    Store,
    extract_field,
    make_field,
    parse_price,
    take_snapshot,
    diff_page,
)
from pricepulse.cli import main  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(REPO_ROOT, "demos", "01-basic")
OLD = os.path.join(DEMO, "competitor-old.html")
NEW = os.path.join(DEMO, "competitor-new.html")
URL = "https://acme.example/pricing"


def _read(p):
    with open(p, "r", encoding="utf-8") as fh:
        return fh.read()


class TestMetadata(unittest.TestCase):
    def test_metadata(self):
        self.assertEqual(TOOL_NAME, "pricepulse")
        self.assertTrue(TOOL_VERSION)


class TestExtraction(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(parse_price("$49/mo"), 49.0)
        self.assertEqual(parse_price("USD 1,299.50"), 1299.50)
        self.assertIsNone(parse_price("Contact us"))

    def test_css_ish_price(self):
        html = _read(OLD)
        val = extract_field(html, "price", "css-ish", ".plan-pro .price")
        self.assertEqual(val, 49.0)

    def test_css_ish_feature_set(self):
        html = _read(OLD)
        plans = extract_field(html, "feature", "css-ish", ".plan-name")
        self.assertIn("Starter", plans)
        self.assertIn("Pro", plans)
        self.assertIn("Enterprise", plans)

    def test_regex_price(self):
        val = extract_field("Pro plan is $59 a month", "price", "regex",
                            r"\$(\d+)")
        self.assertEqual(val, 59.0)


class TestStoreAndDiff(unittest.TestCase):
    def _store(self, tmp):
        store = Store(os.path.join(tmp, "p.db"))
        store.add_page(URL, [
            make_field("pro", "price", "css-ish", ".plan-pro .price"),
            make_field("team", "price", "css-ish", ".plan-team .price"),
            make_field("plans", "feature", "css-ish", ".plan-name"),
        ])
        return store

    def test_demo_price_change_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            take_snapshot(store, URL, html=_read(OLD))
            take_snapshot(store, URL, html=_read(NEW))
            result = diff_page(store, URL)
            store.close()

        self.assertTrue(result["changed"])
        by_field = {}
        for ch in result["changes"]:
            by_field.setdefault(ch["field"], []).append(ch)
        # Pro price went up 49 -> 59
        pro = by_field["pro"][0]
        self.assertEqual(pro["type"], "price_up")
        self.assertEqual(pro["old"], 49.0)
        self.assertEqual(pro["new"], 59.0)
        # Team plan price appeared
        self.assertEqual(by_field["team"][0]["type"], "price_set")
        # Team plan name added to the plans feature set
        added = {c["value"] for c in by_field["plans"]
                 if c["type"] == "feature_added"}
        self.assertIn("Team", added)

    def test_single_snapshot_no_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            take_snapshot(store, URL, html=_read(OLD))
            result = diff_page(store, URL)
            store.close()
        self.assertFalse(result["changed"])


class TestCli(unittest.TestCase):
    def test_full_flow_via_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "cli.db")
            self.assertEqual(main([
                "--store", db, "add", URL,
                "--price", "pro:css-ish:.plan-pro .price",
                "--feature", "plans:css-ish:.plan-name",
            ]), 0)
            self.assertEqual(main([
                "--store", db, "snapshot", URL, "--html", OLD]), 0)
            self.assertEqual(main([
                "--store", db, "snapshot", URL, "--html", NEW]), 0)
            self.assertEqual(main(["--store", db, "diff", URL]), 0)

    def test_diff_json_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "sub.db")
            base = [sys.executable, "-m", "pricepulse", "--store", db]
            subprocess.run(base + ["add", URL,
                                   "--price", "pro:css-ish:.plan-pro .price"],
                           cwd=REPO_ROOT, check=True, capture_output=True)
            subprocess.run(base + ["snapshot", URL, "--html", OLD],
                           cwd=REPO_ROOT, check=True, capture_output=True)
            subprocess.run(base + ["snapshot", URL, "--html", NEW],
                           cwd=REPO_ROOT, check=True, capture_output=True)
            proc = subprocess.run(base + ["diff", URL, "--format", "json"],
                                  cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertTrue(data["changed"])

    def test_no_command_exits_2(self):
        self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
