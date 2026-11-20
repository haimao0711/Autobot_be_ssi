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


def check_signature(user_id, symbol="ZZZZ"):
    """Kiểm tra nhanh chữ ký theo đúng luồng production (client.send_request).

    Dùng mã giả nên KHÔNG đặt lệnh thật. Nếu chữ ký ĐÚNG: lỗi trả về là lỗi nghiệp vụ
    (vd 500 'Cannot determine stock price'), KHÔNG phải 401006/401013.

    Chạy:
      docker exec -it <container-api> python manage.py shell -c "from common.api.ssi.diagnose_order import check_signature; check_signature(3)"
    """
    from apps.authencation.user.models import User
    from apps.account.detail.services import AccountService

    user = User.objects.get(id=user_id)
    account = AccountService.get_account_by_user(user)
    body = {
        "accountNo": account.account_num,
        "symbol": symbol,
        "side": "B",
        "orderType": "LO",
        "quantity": 100,
        "price": "26.20",
        "deviceId": ssi_client.get_device_id(),
        "clientRequestId": str(int(time.time() * 1000)),
        "isReturnOrderId": True,
    }
    res = ssi_client.send_request(
        account, method="POST", path=ENDPOINT_TRADING_ORDER,
        json_body=body, need_signature=True,
    )
    code = res.get("code") if isinstance(res, dict) else None
    print("Phản hồi:", res)
    if code in (401006, 401013):
        print(">>> CHỮ KÝ CHƯA ĐÚNG")
    else:
        print(">>> CHỮ KÝ ĐÃ ĐƯỢC SSI CHẤP NHẬN (lỗi còn lại, nếu có, là nghiệp vụ do dùng mã giả)")


def check_order_book(user_id=3):
    """Kiểm tra truy vấn sổ lệnh /api/v3/trading/orderBook với các định dạng ngày.

    Chạy:
      docker exec -it <container-api> python manage.py shell -c "from common.api.ssi.diagnose_order import check_order_book; check_order_book(3)"
    """
    from datetime import datetime
    from apps.authencation.user.models import User
    from apps.account.detail.services import AccountService
    from common.api.ssi.constants import ENDPOINT_TRADING_ORDER_BOOK
    from common.api.ssi.handler import _get_order_book, handle_orders_not_matched

    user = User.objects.get(id=user_id)
    account = AccountService.get_account_by_user(user)
    today = datetime.now()

    print(f"\n===== KIỂM TRA SỔ LỆNH (/api/v3/trading/orderBook) - TK {account.account_num} =====")
    formats = [
        ("ISO 8601 (OpenAPI spec)", f"{today.strftime('%Y-%m-%d')}T00:00:00Z", f"{today.strftime('%Y-%m-%d')}T23:59:59Z"),
        ("Dashes (YYYY-MM-DD)", today.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')),
        ("Slashes (YYYY/MM/DD)", today.strftime('%Y/%m/%d'), today.strftime('%Y/%m/%d')),
    ]

    for label, d_from, d_to in formats:
        params = {
            "accountNo": account.account_num,
            "from": d_from,
            "to": d_to,
            "pageSize": 100,
            "pageIndex": 1,
        }
        print(f"\n--- Thử định dạng: {label} (from={d_from}, to={d_to}) ---")
        try:
            raw_res = ssi_client.send_request(
                account,
                method="GET",
                path=ENDPOINT_TRADING_ORDER_BOOK,
                params=params,
                need_signature=False,
            )
            print(f"Phản hồi raw: {raw_res}")
        except Exception as e:
            print(f"Lỗi: {e}")

    print("\n--- Kiểm tra kết quả qua handler (_get_order_book & handle_orders_not_matched) ---")
    all_orders = _get_order_book(account)
    print(f"Tổng số lệnh trong sổ: {len(all_orders)}")
    for idx, o in enumerate(all_orders[:5]):
        print(f"  Lệnh #{idx+1}: orderId={o.get('orderId') or o.get('orderNo')} symbol={o.get('symbol')} side={o.get('side')} status={o.get('orderStatus')} qty={o.get('quantity')}")

    unmatched = handle_orders_not_matched(account.name, account.account_num, "ALL", "", "", "", "ALL")
    print(f"\nDanh sách lệnh CHƯA KHỚP (unmatched): {len(unmatched) if unmatched else 0}")
    if unmatched:
        for idx, u in enumerate(unmatched):
            print(f"  Unmatched #{idx+1}: orderNo={u.get('orderNo')} symbol={u.get('symbol')} side={u.get('side')} volume={u.get('volume')} price={u.get('showPrice')}")
    else:
        print("  (Hiện không có lệnh chưa khớp nào treo trên sàn)")


def check_cancel(user_id=3, order_id=None):
    """Kiểm tra gửi yêu cầu hủy lệnh qua DELETE /api/v3/trading/order.

    Nếu order_id không truyền:
      - Tự tìm lệnh chưa khớp đầu tiên trên sàn để hủy.
      - Nếu không có lệnh nào chưa khớp, dùng orderId giả '9999999999' để test chữ ký & body schema.

    Chạy:
      docker exec -it <container-api> python manage.py shell -c "from common.api.ssi.diagnose_order import check_cancel; check_cancel(3)"
    """
    from apps.authencation.user.models import User
    from apps.account.detail.services import AccountService
    from common.api.ssi.handler import handle_orders_not_matched, handle_cancel_order_service

    user = User.objects.get(id=user_id)
    account = AccountService.get_account_by_user(user)

    print(f"\n===== KIỂM TRA HỦY LỆNH (DELETE /api/v3/trading/order) - TK {account.account_num} =====")

    target_order_id = order_id
    if not target_order_id:
        unmatched = handle_orders_not_matched(account.name, account.account_num, "ALL", "", "", "", "ALL")
        if unmatched and len(unmatched) > 0:
            target_order_id = unmatched[0].get("orderNo")
            print(f"Tìm thấy lệnh chưa khớp thực tế để test hủy: orderId={target_order_id} ({unmatched[0].get('symbol')})")
        else:
            target_order_id = "9999999999"
            print(f"Không có lệnh chưa khớp trên sàn, dùng orderId giả để test chữ ký: {target_order_id}")

    ref_id = f"{account.name}.I.test.{int(time.time()*1000)}"
    res = handle_cancel_order_service(
        user_account=account.name,
        url="",
        session="",
        asp_net_session="",
        order_num=target_order_id,
        ref_id=ref_id,
        trade_account=account.account_num,
    )
    print(f"\nKết quả gọi handle_cancel_order_service: {res}")
    if res and res.get("status") == "success":
        print(">>> HỦY LỆNH THÀNH CÔNG!")
    elif isinstance(res, dict) and res.get("code") in (401006, 401013):
        print(">>> CHỮ KÝ HỦY LỆNH CHƯA ĐÚNG")
    else:
        print(">>> Phản hồi nhận được từ SSI (kiểm tra nội dung phía trên)")
