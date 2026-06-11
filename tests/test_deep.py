"""Deep behavior tests for pricepulse — extractors, diff classes, MCP, offline.

Standard library only, no network.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricepulse import (  # noqa: E402
    FieldDef,
    PricePulseError,
    Store,
    diff_values,
    extract,
    extract_field,
    fetch,
    make_field,
    summarize_change,
)
from pricepulse import mcp_server  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(REPO_ROOT, "demos", "01-basic")


def _read(name):
    with open(os.path.join(DEMO, name), "r", encoding="utf-8") as fh:
        return fh.read()


class TestSelectors(unittest.TestCase):
    def test_descendant_selector_scopes_match(self):
        html = _read("competitor-old.html")
        # .price alone matches every plan price; scoping to .plan-starter narrows it
        all_prices = extract(html, "css-ish", ".price")
        self.assertGreater(len(all_prices), 1)
        starter = extract(html, "css-ish", ".plan-starter .price")
        self.assertEqual(starter, ["$19/mo"])

    def test_tag_and_class_combo(self):
        html = '<div class="x">no</div><h2 class="plan-name">Pro</h2>'
        self.assertEqual(extract(html, "css-ish", "h2.plan-name"), ["Pro"])

    def test_id_selector(self):
        html = '<div id="hero">Hello <b>World</b></div>'
        self.assertEqual(extract(html, "css-ish", "#hero"), ["Hello World"])

    def test_selector_matching_everything_rejected(self):
        with self.assertRaises(PricePulseError):
            extract("<p>x</p>", "css-ish", "")

    def test_bad_regex_rejected(self):
        with self.assertRaises(PricePulseError):
            extract("x", "regex", "(")


class TestDiffClassification(unittest.TestCase):
    FIELDS = [
        FieldDef("price", "price", "regex", r"\$(\d+)"),
        FieldDef("plans", "feature", "css-ish", ".plan-name"),
    ]

    def test_price_up_down(self):
        up = diff_values({"price": 49.0}, {"price": 59.0}, self.FIELDS)
        self.assertEqual(up[0]["type"], "price_up")
        self.assertEqual(up[0]["delta"], 10.0)
        down = diff_values({"price": 59.0}, {"price": 49.0}, self.FIELDS)
        self.assertEqual(down[0]["type"], "price_down")

    def test_price_set_and_cleared(self):
        s = diff_values({"price": None}, {"price": 99.0}, self.FIELDS)
        self.assertEqual(s[0]["type"], "price_set")
        c = diff_values({"price": 99.0}, {"price": None}, self.FIELDS)
        self.assertEqual(c[0]["type"], "price_cleared")

    def test_feature_added_removed(self):
        ch = diff_values({"plans": ["A", "B"]}, {"plans": ["A", "C"]}, self.FIELDS)
        types = {(c["type"], c["value"]) for c in ch}
        self.assertIn(("feature_added", "C"), types)
        self.assertIn(("feature_removed", "B"), types)

    def test_no_change_no_records(self):
        self.assertEqual(diff_values({"price": 1.0}, {"price": 1.0}, self.FIELDS), [])

    def test_summarize_lines(self):
        ch = diff_values({"price": 49.0}, {"price": 59.0}, self.FIELDS)[0]
        self.assertIn("UP", summarize_change(ch))


class TestOfflineGraceful(unittest.TestCase):
    def test_fetch_failure_raises_pricepulse_error(self):
        # Unroutable / unresolvable host — confirms graceful error, not a crash.
        with self.assertRaises(PricePulseError):
            fetch("http://invalid.invalid.invalid./x", timeout=2)

    def test_file_url_fetch(self):
        path = os.path.join(DEMO, "competitor-old.html")
        url = "file:///" + path.replace("\\", "/").lstrip("/")
        text = fetch(url, timeout=5)
        self.assertIn("Acme Cloud", text)


class TestStorePersistence(unittest.TestCase):
    def test_reopen_store_keeps_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "persist.db")
            s1 = Store(db)
            s1.add_page("u", [make_field("p", "price", "regex", r"(\d+)")])
            s1.close()
            s2 = Store(db)
            pages = s2.pages()
            fields = s2.fields_for("u")
            s2.close()
            self.assertEqual([p["url"] for p in pages], ["u"])
            self.assertEqual(fields[0].name, "p")


class TestMcpServer(unittest.TestCase):
    def _rpc(self, req):
        return mcp_server.handle_request(req)

    def test_initialize_and_list(self):
        init = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "pricepulse")
        lst = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in lst["result"]["tools"]}
        self.assertEqual(names, {"extract", "diff_html"})

    def test_tools_call_extract(self):
        resp = self._rpc({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "extract", "arguments": {
                "html": _read("competitor-new.html"),
                "kind": "price", "extractor_kind": "css-ish",
                "pattern": ".plan-pro .price"}},
        })
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["value"], 59.0)

    def test_tools_call_diff_html(self):
        resp = self._rpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "diff_html", "arguments": {
                "old_html": _read("competitor-old.html"),
                "new_html": _read("competitor-new.html"),
                "name": "pro", "kind": "price",
                "extractor_kind": "css-ish", "pattern": ".plan-pro .price"}},
        })
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["changes"][0]["type"], "price_up")

    def test_unknown_method(self):
        resp = self._rpc({"jsonrpc": "2.0", "id": 9, "method": "nope"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_run_loop_over_stringio(self):
        reqs = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            "",
        ])
        out = io.StringIO()
        mcp_server.run_mcp_server(stdin=io.StringIO(reqs), stdout=out)
        lines = [l for l in out.getvalue().splitlines() if l]
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
