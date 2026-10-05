import json
import threading
import unittest
import urllib.error
import urllib.request

from src.server import make_server
from tests.util import HC, MR, PUBLIC, Builder, biz, plat


def _request(url: str, method: str = "GET", body: dict | None = None,
             headers: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = make_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls._seed()

    @classmethod
    def _seed(cls) -> None:
        svc = cls.httpd.service
        b = Builder(svc)
        for bid in ("B-FARM", "B-CAFE"):
            b.registered_business(bid)
        b.rule("R1", "v1")
        b.method("M1")
        b.harvest("B-FARM", "L1", 10)
        b.emit("LICENSE_ISSUED", "food_business", "B-CAFE",
               {"license_no": "J1", "premises_id": "P1", "scope": "餐饮"}, MR)
        b.emit("SHOP_LISTED", "platform_shop", "S1",
               {"platform": "PF", "business_id": "B-CAFE", "license_no": "J1"},
               plat("PF"))
        b.emit("SAMPLE_DRAWN", "inspection_sample", "SP",
               {"lot_id": "L1", "sampled_by": "health"}, HC)
        b.emit("SAMPLE_TESTED", "inspection_sample", "SP",
               {"kind": "rapid", "result": "positive", "tested_by": "health",
                "method_code": "M1"}, HC)
        scope = svc.emergency_scope("SP", MR)
        control = b.emit("RISK_CONTROLLED", "inspection_sample", "SP",
                         {"trigger_sample_id": "SP",
                          "decisions": scope["recommended_decisions"],
                          "authority": "market_regulation",
                          "rule_code": "R1", "rule_version": "v1"}, MR)
        b.emit("RECALL_OPENED", "recall_case", "RC1",
               {"control_event_id": control["event_id"], "root_lot_id": "L1",
                "authority": "market_regulation"}, MR)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_alerts(self) -> None:
        status, body = _request(f"{self.base}/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, alerts = _request(f"{self.base}/alerts")
        self.assertEqual(status, 200)
        self.assertEqual(alerts, [])

    def test_post_event_dedup_and_validation(self) -> None:
        event = {
            "event_id": "HTTP-1", "event_type": "RULE_PUBLISHED",
            "aggregate_type": "risk_rule", "aggregate_id": "RH",
            "occurred_at": "2026-09-20T10:00:00+08:00", "version": 1,
            "summary": "HTTP 发布规则",
            "payload": {"rule_code": "RH", "version": "v1",
                        "content_ref": "r.md",
                        "effective_at": "2026-01-01T00:00:00+08:00"}}
        hdr = {"X-Actor-Kind": "department", "X-Actor-Id": "market_regulation"}
        s1, _ = _request(f"{self.base}/events", "POST", event, hdr)
        s2, _ = _request(f"{self.base}/events", "POST", event, hdr)
        self.assertEqual((s1, s2), (201, 201))
        bad = dict(event, event_id="HTTP-BAD", payload={})
        status, body = _request(f"{self.base}/events", "POST", bad, hdr)
        self.assertEqual(status, 422)
        self.assertIn("detail", body)

    def test_scope_access_control(self) -> None:
        url = f"{self.base}/samples/SP/scope"
        status, body = _request(
            url, headers={"X-Actor-Kind": "department",
                          "X-Actor-Id": "market_regulation"})
        self.assertEqual(status, 200)
        self.assertIn("L1", body["affected_lot_ids"])
        status, _ = _request(url)  # 公众
        self.assertEqual(status, 403)

    def test_trace_denied_for_unrelated_business(self) -> None:
        status, _ = _request(
            f"{self.base}/lots/L1/trace",
            headers={"X-Actor-Kind": "business", "X-Actor-Id": "B-CAFE"})
        self.assertEqual(status, 403)
        status, body = _request(
            f"{self.base}/lots/L1/trace",
            headers={"X-Actor-Kind": "business", "X-Actor-Id": "B-FARM"})
        self.assertEqual(status, 200)
        self.assertEqual(body["lot_id"], "L1")

    def test_frozen_lot_blocks_platform_fulfillment_with_423(self) -> None:
        # 先下单（控制令后下单本身允许），再尝试用受控批次出餐
        place = {
            "event_id": "HTTP-ORDER", "event_type": "ORDER_PLACED",
            "aggregate_type": "food_order", "aggregate_id": "OH1",
            "occurred_at": "2026-09-20T11:00:00+08:00", "version": 1,
            "summary": "下单",
            "payload": {"shop_id": "S1", "consumer_ref": "顾客", "items": []}}
        status, _ = _request(
            f"{self.base}/events", "POST", place,
            {"X-Actor-Kind": "platform", "X-Actor-Id": "PF"})
        self.assertEqual(status, 201)
        fulfill = {
            "event_id": "HTTP-ORDER-F", "event_type": "ORDER_FULFILLED",
            "aggregate_type": "food_order", "aggregate_id": "OH1",
            "occurred_at": "2026-09-20T11:05:00+08:00", "version": 2,
            "summary": "出餐",
            "payload": {"lots": [{"lot_id": "L1", "quantity": 1}]}}
        status, body = _request(
            f"{self.base}/events", "POST", fulfill,
            {"X-Actor-Kind": "platform", "X-Actor-Id": "PF"})
        self.assertEqual(status, 423)
        self.assertEqual(body["error"], "RiskLotFrozenError")


if __name__ == "__main__":
    unittest.main()
