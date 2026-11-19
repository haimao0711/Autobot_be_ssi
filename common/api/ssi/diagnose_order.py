"""Công cụ chẩn đoán lỗi 400 khi đặt lệnh SSI (KHÔNG đặt lệnh thật).

Gửi lệnh với mã chứng khoán giả `ZZZZ` (không tồn tại) nên không thể khớp/đặt thật.
Mỗi biến thể về chữ ký / định dạng giá được gửi một lần, rồi in phản hồi để so sánh:
  - Nếu phản hồi là lỗi "mã chứng khoán không hợp lệ"... -> chữ ký + định dạng đã ĐÚNG.
  - Nếu vẫn là "Request sai định dạng..." -> biến thể đó chưa đúng.

Chạy:
  docker exec -it <container-api> python manage.py shell -c "from common.api.ssi.diagnose_order import run; run(3)"
(3 = id user)
"""
import base64
import json
import time

import requests

from common.api.ssi import client as ssi_client
from common.api.ssi.constants import SSI_BASE_URL, ENDPOINT_TRADING_ORDER
from common.api.ssi.signature import sign_payload


def _sig_variants(account, payload_str):
    secret = account.ssi_consumer_secret
    key = account.ssi_private_key_path
    b64_full = sign_payload(secret, payload_str, key)
    b64_body = sign_payload("", payload_str, key)
    hex_full = base64.b64decode(b64_full).hex()
    hex_body = base64.b64decode(b64_body).hex()
    return [
        ("A. base64(secret+body)  [hiện tại]", b64_full),
        ("B. sha256=base64(secret+body)", "sha256=" + b64_full),
        ("C. base64(body only)", b64_body),
        ("D. sha256=base64(body only)", "sha256=" + b64_body),
        ("E. hex(secret+body)", hex_full),
        ("F. sha256=hex(secret+body)", "sha256=" + hex_full),
        ("G. hex(body only)", hex_body),
        ("H. KHÔNG gửi X-Signature", None),
    ]


def run(user_id, symbol="ZZZZ"):
    from apps.authencation.user.models import User
    from apps.account.detail.services import AccountService

    user = User.objects.get(id=user_id)
    account = AccountService.get_account_by_user(user)
    token = ssi_client.ensure_token(account)

    for price in ("26.20", "26200"):
        print(f"\n===== price={price!r}")
        for idx in range(8):
            body = {
                "accountNo": account.account_num,
                "symbol": symbol,
                "side": "B",
                "orderType": "LO",
                "quantity": 100,
                "price": price,
                "deviceId": ssi_client.get_device_id(),
                "clientRequestId": f"diag_{int(time.time() * 1000)}_{idx}",
                "isReturnOrderId": True,
            }
            payload_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            name, sig = _sig_variants(account, payload_str)[idx]
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept-Language": "vi",
            }
            if sig:
                headers["X-Signature"] = sig
            try:
                r = requests.post(
                    f"{SSI_BASE_URL}{ENDPOINT_TRADING_ORDER}",
                    headers=headers,
                    data=payload_str.encode("utf-8"),
                    timeout=12,
                )
                print(f"[{name}] HTTP {r.status_code}: {r.text[:300]}")
            except Exception as e:
                print(f"[{name}] LỖI: {e}")
            time.sleep(0.5)
