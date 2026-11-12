"""Test offline cho common.api.ssi.handler bằng client SSI giả (không gọi mạng)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from common.api.ssi import handler


class FakeClient:
    def __init__(self):
        self.orders = []
        self.sent = []

    @staticmethod
    def get_deviceid():
        return "dev"

    @staticmethod
    def get_user_agent():
        return "ua"

    def new_order(self, m):
        self.sent.append(m)
        self.orders.append({"orderID": "O1", "requestID": m.requestID, "buySell": m.buySell,
                            "instrumentID": m.instrumentID, "price": m.price,
                            "quantity": m.quantity, "filledQty": 0, "orderStatus": "SD",
                            "marketID": "VN", "orderType": "LO"})
        return {"status": 200, "message": "Success", "data": {"requestID": m.requestID}}

    def modify_order(self, m):
        self.sent.append(m)
        return {"status": 200, "data": {"orderID": m.orderID}}

    def cancle_order(self, m):
        self.sent.append(m)
        return {"status": 200, "data": {}}

    def get_order_book(self, m):
        return {"status": 200, "data": self.orders}

    def get_stock_position(self, m):
        return {"status": 200, "data": {"stockPositions": [
            {"instrumentID": "SSI", "onHand": 300, "sellableQty": 200,
             "avgPrice": 20000, "marketPrice": 22000}]}}

    def get_stock_account_balance(self, m):
        return {"status": 200, "data": [{"withdrawable": 5000, "totalAsset": 100000,
                                         "purchasingPower": 7000}]}


ACCOUNT = SimpleNamespace(account_num="0901351", ssi_pin="123456", ssi_two_fa_type=0)


class SSIOtpModeTest(TestCase):
    def test_otp_mode_requires_token_in_store_even_if_in_memory(self):
        from common.api.ssi import client as ssi_client
        account = SimpleNamespace(ssi_two_fa_type=1, ssi_consumer_id="c1")
        fake = SimpleNamespace(_write_access_token=object())
        with patch.object(ssi_client, "get_client", return_value=fake), \
                patch.object(ssi_client, "_load_cached_writer_token", return_value=False):
            with self.assertRaises(ssi_client.SSIAuthError):
                ssi_client.ensure_writer(account)
        self.assertIsNone(fake._write_access_token)

    def test_otp_mode_ok_when_store_has_token(self):
        from common.api.ssi import client as ssi_client
        account = SimpleNamespace(ssi_two_fa_type=1, ssi_consumer_id="c1")
        fake = SimpleNamespace(_write_access_token=None)
        with patch.object(ssi_client, "get_client", return_value=fake), \
                patch.object(ssi_client, "_load_cached_writer_token", return_value=True):
            self.assertIs(ssi_client.ensure_writer(account), fake)


class SSIHandlerTest(TestCase):
    def setUp(self):
        self.client = FakeClient()
        patches = [
            patch("common.api.ssi.client.get_account", return_value=ACCOUNT),
            patch("common.api.ssi.client.get_client", return_value=self.client),
            patch("common.api.ssi.client.ensure_writer", return_value=self.client),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_buy_converts_price_to_vnd_and_returns_order(self):
        res = handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "ref")
        self.assertEqual(self.client.sent[0].price, 21500)
        self.assertEqual(res["order_num"], "O1")
        self.assertEqual(res["symbol"], "SSI")

    def test_pending_orders_use_thousand_vnd(self):
        handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "ref")
        pending = handler.handle_orders_not_matched("u", "0901351", "SSI", "", "", "", "B")
        self.assertEqual(pending[0]["showPrice"], 21.5)
        self.assertEqual(pending[0]["orderNo"], "O1")

    def test_update_and_cancel_find_order_in_book(self):
        handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "ref")
        upd = handler.handle_update_order_service("u", "0901351", "", "SSI", "", "", "O1", 21.5, 21.55, 100, "r", "B")
        self.assertEqual(upd["orderNo"], "O1")
        self.assertEqual(self.client.sent[-1].price, 21550)
        self.assertTrue(handler.handle_cancel_order_service("u", "", "", "", "O1", "r"))

    def test_buy_network_error_but_order_exists_is_not_duplicated(self):
        def flaky(m):
            self.client.orders.append({"orderID": "O9", "requestID": m.requestID, "buySell": "B",
                                       "instrumentID": m.instrumentID, "price": m.price,
                                       "quantity": m.quantity, "filledQty": 0, "orderStatus": "SD"})
            raise ConnectionError("timeout")
        self.client.new_order = flaky
        res = handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "ref")
        self.assertEqual(res["order_num"], "O9")
        self.assertEqual(len(self.client.orders), 1)

    def test_buy_network_error_without_order_returns_empty(self):
        def broken(m):
            raise ConnectionError("down")
        self.client.new_order = broken
        with patch("common.api.ssi.handler.time.sleep"):
            res = handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "ref")
        self.assertEqual(res, {})

    def test_validate_session_and_stock_balance(self):
        ok, data = handler.validate_session("u", "0901351", "", "", "")
        self.assertTrue(ok)
        self.assertEqual(data["total_market_value"], 300 * 22000)
        bal = handler.handle_stock_balance_service("u", "0901351", "SSI", "", "", "", "B")
        self.assertEqual(bal["stock_balance"]["actual_vol"], 300)
        self.assertEqual(bal["stock_balance"]["available_vol"], 200)
        self.assertEqual(bal["number_stock_existing"], 1)

