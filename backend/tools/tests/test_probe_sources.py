"""本文件离线验证数据源探测的预算、冷却、证据缓存与错误判定，不访问网络。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timezone
from email.message import Message
from os import utime
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError


SPEC = importlib.util.spec_from_file_location("probe_sources", Path(__file__).parents[1] / "probe_sources.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
TARGET = date(2026, 10, 29)


class FakeClock:
    """推进虚拟时间，避免测试实际等待或触发网络。"""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 30, 8, tzinfo=timezone.utc).timestamp()
        self.delays: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.value += seconds


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.clock = FakeClock()
        self.guard = self.new_guard()
        self.sample = probe.Probe("catalog", "eastmoney", "catalog", "https://example.test/catalog", "em_catalog")

    def new_guard(self) -> probe.Guard:
        return probe.Guard(self.directory, self.clock.now, self.clock.sleep, lambda: 0)

    def test_rate_is_shared_across_sources_and_restart(self) -> None:
        self.guard.reserve("sina")
        self.new_guard().reserve("eastmoney")
        self.assertEqual(self.clock.delays, [10.0])

    def test_failed_request_consumes_budget_and_network_error_cools_source(self) -> None:
        opener = Mock()
        opener.open.side_effect = URLError("connection reset")
        self.assertEqual(probe.fetch(self.sample, self.guard, TARGET, opener)["status"], "network_error")
        self.assertEqual(probe.fetch(self.sample, self.guard, TARGET, opener)["status"], "deferred")
        opener.open.assert_called_once()
        self.assertEqual(sum(next(iter(self.guard.state["daily"].values())).values()), 1)

    def test_daily_provider_budget_survives_restart(self) -> None:
        for _ in range(2):
            guard = self.new_guard()
            for _ in range(10):
                guard.reserve("sina")
        with self.assertRaises(probe.Deferred):
            self.new_guard().reserve("sina")

    def test_global_daily_budget_applies_across_sources(self) -> None:
        for source in ("sina", "eastmoney", "tencent"):
            guard = self.new_guard()
            for _ in range(10):
                guard.reserve(source)
        with self.assertRaises(probe.Deferred):
            self.new_guard().reserve("sina")

    def test_limit_resets_at_shanghai_midnight(self) -> None:
        for _ in range(10):
            self.guard.reserve("sina")
        self.clock.value += 86400
        self.new_guard().reserve("sina")
        self.assertEqual(len(self.new_guard().state["daily"]), 2)

    def test_http_rate_limit_obeys_longer_retry_after(self) -> None:
        headers = Message()
        headers["Retry-After"] = "172800"
        opener = Mock()
        opener.open.side_effect = HTTPError(self.sample.url, 429, "limited", headers, None)
        record = probe.fetch(self.sample, self.guard, TARGET, opener)
        self.assertEqual(record["httpStatus"], 429)
        self.assertEqual(self.guard.state["cooldown"]["eastmoney"], self.clock.now() + 172800)
        with self.assertRaises(probe.Deferred):
            self.new_guard().reserve("eastmoney")

    def test_retry_after_http_date(self) -> None:
        self.assertEqual(probe.retry_delay("Sun, 01 Nov 2026 08:00:00 GMT", self.clock.now()), 172800)
        self.assertEqual(probe.retry_delay("invalid", self.clock.now()), 86400)

    def test_success_is_cached_without_another_upstream_request(self) -> None:
        response = Mock()
        response.read.return_value = b'{"data":{"total":100,"diff":[{"f12":"920001","f14":"sample"}]}}'
        response.headers = Message()
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        first = probe.fetch(self.sample, self.guard, TARGET, opener)
        utime(self.directory / first["evidence"], (self.clock.now(), self.clock.now()))
        second = probe.fetch(self.sample, self.guard, TARGET, opener)
        self.assertEqual(first["status"], "sample_received")
        self.assertTrue(second["cached"])
        self.assertEqual(second["summary"]["rows"], 1)
        opener.open.assert_called_once()

    def test_http_200_html_is_not_success(self) -> None:
        response = Mock()
        response.read.return_value = b"<html>captcha</html>"
        response.headers = Message()
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        record = probe.fetch(self.sample, self.guard, TARGET, opener)
        self.assertEqual(record["status"], "unverified_response")
        self.assertFalse(list(self.directory.glob("*.txt")))
        self.assertIn("eastmoney", self.guard.state["cooldown"])

    def test_unexpected_json_shape_does_not_crash_run(self) -> None:
        response = Mock()
        response.read.return_value = b'{"data":{"diff":[123]}}'
        response.headers = Message()
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        self.assertEqual(probe.fetch(self.sample, self.guard, TARGET, opener)["status"], "unverified_response")

    def test_empty_catalog_does_not_prove_unsupported(self) -> None:
        with self.assertRaises(ValueError):
            probe.inspect_payload(self.sample, '{"data":{"diff":[],"total":0}}', TARGET)

    def test_wrong_date_and_missing_fields_do_not_pass(self) -> None:
        history = probe.Probe("history", "eastmoney", "history", "https://example.test/", "em_history")
        for row in ("2026-10-30,1,1,1,1,1,1,1,1,1,1", "2026-10-29,1"):
            with self.assertRaises(ValueError):
                probe.inspect_payload(history, json.dumps({"data": {"klines": [row]}}), TARGET)
        summary = probe.inspect_payload(history, '{"data":{"klines":["2026-10-28,1,1,1,1,1,1,1,1,1,1"]}}', TARGET)
        self.assertFalse(summary["reachesTarget"])

    def test_corrupt_state_does_not_reset_budget(self) -> None:
        (self.directory / "state.json").write_text("corrupt")
        with self.assertRaises(json.JSONDecodeError):
            self.new_guard()

    def test_redirect_is_not_followed(self) -> None:
        self.assertIsNone(probe.NoRedirect().redirect_request(Mock(), None, 302, "redirect", Message(), "https://example.test/next"))

    def test_plan_covers_five_boards_without_guessing_bse_code(self) -> None:
        plan = probe.make_plan(TARGET)
        self.assertEqual({item.board for item in plan if item.group == "history"}, {"SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR", "BSE"})
        self.assertTrue(any("BSE_CODE" in item.url for item in plan))
        self.assertFalse(any("BSE_CODE" in item.url for item in probe.make_plan(TARGET, "920001")))


if __name__ == "__main__":
    unittest.main()
