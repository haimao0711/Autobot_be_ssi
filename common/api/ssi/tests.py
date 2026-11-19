"""Unit tests cho SSI FastConnect API v3 client, signature và handler."""
import base64
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from Crypto.PublicKey import RSA

from common.api.ssi import client as ssi_client
from common.api.ssi import handler
from common.api.ssi import signature


# Sinh 1 key RSA mẫu để test
_RSA_TEST = RSA.generate(1024)
_N_B64 = base64.b64encode(_RSA_TEST.n.to_bytes((_RSA_TEST.n.bit_length() + 7) // 8, "big")).decode()
_E_B64 = base64.b64encode(_RSA_TEST.e.to_bytes((_RSA_TEST.e.bit_length() + 7) // 8, "big")).decode()
_D_B64 = base64.b64encode(_RSA_TEST.d.to_bytes((_RSA_TEST.d.bit_length() + 7) // 8, "big")).decode()
_P_B64 = base64.b64encode(_RSA_TEST.p.to_bytes((_RSA_TEST.p.bit_length() + 7) // 8, "big")).decode()
_Q_B64 = base64.b64encode(_RSA_TEST.q.to_bytes((_RSA_TEST.q.bit_length() + 7) // 8, "big")).decode()
_XML_KEY = f"<RSAKeyValue><Modulus>{_N_B64}</Modulus><Exponent>{_E_B64}</Exponent><D>{_D_B64}</D><P>{_P_B64}</P><Q>{_Q_B64}</Q></RSAKeyValue>"


ACCOUNT = SimpleNamespace(
    name="user_test",
    account_num="0901351",
    broker="ssi",
    ssi_consumer_id="test_api_key",
    ssi_consumer_secret="test_api_secret",
    ssi_private_key_path=_XML_KEY,
    ssi_two_fa_type=1,
    ssi_pin="",
)


class SSISignatureTest(TestCase):
    def test_load_and_sign_xml_key(self):
        sig = signature.sign_payload(ACCOUNT.ssi_consumer_secret, '{"symbol":"SSI"}', _XML_KEY)
        self.assertTrue(len(sig) > 0)
        # Verify base64
        decoded_sig = base64.b64decode(sig)
        self.assertEqual(len(decoded_sig), 128)  # 1024-bit key = 128 bytes


class SSIClientTest(TestCase):
    def test_ensure_token_fails_without_login(self):
        with patch.object(ssi_client.cache, "get", return_value=None):
            with self.assertRaises(ssi_client.SSIAuthError):
                ssi_client.ensure_token(ACCOUNT)

    def test_ensure_token_success_when_cached(self):
        with patch.object(ssi_client.cache, "get", return_value="jwt_test_token"):
            token = ssi_client.ensure_token(ACCOUNT)
            self.assertEqual(token, "jwt_test_token")

    @patch("requests.post")
    def test_get_access_token_saves_to_cache(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": 200,
            "data": {
                "accessToken": "new_jwt_token",
                "refreshToken": "new_refresh_token",
            },
        }
        mock_post.return_value = mock_resp

        with patch.object(ssi_client.cache, "set") as mock_cache_set:
            token = ssi_client.get_access_token(ACCOUNT, otp="123456")
            self.assertEqual(token, "new_jwt_token")
            self.assertTrue(mock_cache_set.called)


class SSIHandlerTest(TestCase):
    def setUp(self):
        self.patches = [
            patch("common.api.ssi.client.get_account", return_value=ACCOUNT),
            patch("common.api.ssi.client.ensure_token", return_value="fake_jwt_token"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @patch("common.api.ssi.client.send_request")
    def test_handle_buy_service(self, mock_send):
        mock_send.return_value = {
            "status": 200,
            "data": {"orderId": "123456", "clientRequestId": "req-01"},
        }
        res = handler.handle_buy_service("u", "0901351", "", "SSI", "", "", 21.5, 100, "req-01")
        self.assertEqual(res["order_num"], "123456")
        self.assertEqual(res["symbol"], "SSI")
        self.assertEqual(res["price"], 21.5)
        self.assertEqual(res["volume"], 100)

        # Kiểm tra body gửi đi đổi giá 21.5 thành "21500" (string) và buySell="B"
        call_kwargs = mock_send.call_args[1]
        self.assertEqual(call_kwargs["json_body"]["price"], "21500")
        self.assertEqual(call_kwargs["json_body"]["buySell"], "B")
        self.assertEqual(call_kwargs["json_body"]["quantity"], 100)

    @patch("common.api.ssi.client.send_request")
    def test_handle_orders_not_matched(self, mock_send):
        mock_send.return_value = {
            "status": 200,
            "data": {
                "orders": [
                    {
                        "orderId": "O1",
                        "symbol": "SSI",
                        "side": "B",
                        "price": 21500,
                        "quantity": 100,
                        "filledQuantity": 0,
                        "orderStatus": "NEW",
                    },
                    {
                        "orderId": "O2",
                        "symbol": "SSI",
                        "side": "B",
                        "price": 21500,
                        "quantity": 100,
                        "filledQuantity": 100,
                        "orderStatus": "FILLED",
                    },
                ]
            },
        }
        orders = handler.handle_orders_not_matched("u", "0901351", "SSI", "", "", "", "B")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNo"], "O1")
        self.assertEqual(orders[0]["showPrice"], 21.5)
        self.assertEqual(orders[0]["volume"], 100)

    @patch("common.api.ssi.client.send_request")
    def test_handle_cash_balance_service(self, mock_send):
        mock_send.return_value = {
            "status": 200,
            "data": {
                "equity": {
                    "accountBalance": 100000000,
                    "withdrawable": 50000000,
                    "purchasingPower": 80000000,
                }
            },
        }
        res = handler.handle_cash_balance_service("u", "0901351", "", "", "")
        self.assertEqual(res["cash_balance"], 50000000)
        self.assertEqual(res["total_assets"], 100000000)
        self.assertEqual(res["cash_available"], 80000000)
