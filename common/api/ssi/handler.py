"""Handler SSI FastConnect API v3 - tương thích giao diện `common.api.smartone.handler`.

Các hàm giữ nguyên tên, tham số và định dạng trả về của SmartOne để `apps.trading`
chuyển broker mà không cần sửa logic trading cốt lõi.
"""
import json
import logging
import random
import time
from datetime import datetime, timedelta

from common.api.smartone.enums import SideOrderEnum, StatusOrderEnum
from common.api.ssi import client as ssi_client
from common.api.ssi.client import SSIAuthError
from common.api.ssi.constants import (
    CHANNEL_ID,
    ENDPOINT_TRADING_ACCOUNT_BALANCE,
    ENDPOINT_TRADING_ORDER,
    ENDPOINT_TRADING_ORDER_BOOK,
    ENDPOINT_TRADING_POSITION,
    ENDPOINT_TRADING_PPMMR,
    FINAL_ORDER_STATUSES,
    ORDER_TYPE_LIMIT,
    PRICE_UNIT,
    SSI_ORDER_PRICE_IN_THOUSAND,
    SIDE_BUY,
    UNKNOWN_CEIL_PRICE,
    UNKNOWN_FLOOR_PRICE,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- helpers
def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first(item, *keys, default=None):
    if not isinstance(item, dict):
        return default
    for k in keys:
        if item.get(k) not in (None, ""):
            return item[k]
    return default


def _as_list(data):
    """Đảm bảo luôn trả về danh sách các dict."""
    if data is None:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        # Nếu dict bọc list (orders, data, equity, positions...)
        for v in data.values():
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
        return [data]
    return []


def _ok(res) -> bool:
    if not isinstance(res, dict):
        return False
    if res.get("error") or res.get("errors"):
        return False
    code = res.get("code")
    if code is not None and code not in (200, "200", 0, "0", "SUCCESS", "success"):
        return False
    status = res.get("status")
    if status is not None and status not in (200, "200", 0, "0", "SUCCESS", "success"):
        return False
    msg = str(res.get("msg") or res.get("message") or "").lower()
    if any(k in msg for k in ("lỗi", "error", "fail", "không hợp lệ", "sai định dạng", "từ chối", "hết hạn")):
        return False
    return True


def _tick(price_vnd: float) -> int:
    """Làm tròn giá theo bước giá HOSE (hoặc HNX 100đ)."""
    if price_vnd < 10000:
        step = 10
    elif price_vnd < 50000:
        step = 50
    else:
        step = 100
    return int(round(price_vnd / step) * step)


def _to_vnd(price_k: float) -> int:
    """Đổi giá nghìn đồng (vd 21.5) sang đồng (21500).

    Nếu giá đã lớn hơn 1000 (đã là đồng), giữ nguyên.
    """
    p = float(price_k)
    if p > 1000:
        return _tick(p)
    return _tick(p * PRICE_UNIT)


def _price_str(price_k) -> str:
    """Chuỗi giá gửi SSI khi đặt/sửa lệnh (theo SSI_ORDER_PRICE_IN_THOUSAND)."""
    vnd = _to_vnd(price_k)
    if SSI_ORDER_PRICE_IN_THOUSAND:
        return f"{vnd / PRICE_UNIT:.2f}"
    return str(vnd)


def _from_vnd(price_vnd) -> float:
    """Đổi giá đồng (vd 21500) sang nghìn đồng (21.5)."""
    p = _num(price_vnd)
    if p > 1000:
        return round(p / PRICE_UNIT, 2)
    return round(p, 2)


def _request_id(ref_id: str = None) -> str:
    """Sinh clientRequestId ngắn, chỉ gồm chữ số (16 ký tự) để tránh vi phạm giới hạn độ dài/định dạng của SSI.

    ref_id nội bộ (vd '059191.I.test.1795...') dài 27+ ký tự nên KHÔNG dùng trực tiếp.
    """
    return f"{int(time.time() * 1000)}{random.randint(0, 999):03d}"


def _account(user_account, trade_account):
    account = ssi_client.get_account(user_account, trade_account)
    if account is None:
        raise SSIAuthError(f"Không tìm thấy tài khoản SSI {trade_account or user_account}")
    return account


# Biến lưu định dạng ngày đã xác nhận hoạt động với SSI orderBook: 'iso' | 'slash' | 'dash'
_ORDER_BOOK_DATE_MODE = "iso"


def _format_order_book_dates(start_date=None, end_date=None, mode="iso"):
    now = datetime.now()
    if mode == "iso":
        # Chuẩn ISO 8601 (OpenAPI spec: string($date-time), vd: 2026-11-20T00:00:00Z)
        d_start = str(start_date)[:10].replace("/", "-") if start_date else now.strftime("%Y-%m-%d")
        d_end = str(end_date)[:10].replace("/", "-") if end_date else now.strftime("%Y-%m-%d")
        return f"{d_start}T00:00:00Z", f"{d_end}T23:59:59Z"
    elif mode == "slash":
        # YYYY/MM/DD
        d_start = str(start_date)[:10].replace("-", "/") if start_date else now.strftime("%Y/%m/%d")
        d_end = str(end_date)[:10].replace("-", "/") if end_date else now.strftime("%Y/%m/%d")
        return d_start, d_end
    else:
        # YYYY-MM-DD
        d_start = str(start_date)[:10].replace("/", "-") if start_date else now.strftime("%Y-%m-%d")
        d_end = str(end_date)[:10].replace("/", "-") if end_date else now.strftime("%Y-%m-%d")
        return d_start, d_end


def _get_order_book(account, start_date=None, end_date=None) -> list:
    """Lấy danh sách lệnh trong ngày hoặc khoảng thời gian từ /api/v3/trading/orderBook."""
    global _ORDER_BOOK_DATE_MODE

    modes_to_try = [_ORDER_BOOK_DATE_MODE]
    for m in ("iso", "slash", "dash"):
        if m not in modes_to_try:
            modes_to_try.append(m)

    res = None
    for mode in modes_to_try:
        from_date, to_date = _format_order_book_dates(start_date, end_date, mode)
        params = {
            "accountNo": account.account_num,
            "from": from_date,
            "to": to_date,
            "pageSize": 100,
            "pageIndex": 1,
        }

        try:
            res = ssi_client.send_request(
                account,
                method="GET",
                path=ENDPOINT_TRADING_ORDER_BOOK,
                params=params,
                need_signature=False,
            )
        except Exception as e:
            logger.error("SSI get_order_book lỗi (mode=%s): %s", mode, e)
            continue

        if _ok(res):
            _ORDER_BOOK_DATE_MODE = mode
            break
        else:
            logger.warning("SSI get_order_book (mode=%s) phản hồi không thành công: %s", mode, res)

    if not isinstance(res, (dict, list)) or not _ok(res):
        return []

    orders = None
    if isinstance(res, list):
        orders = res
    elif isinstance(res, dict):
        for k in ("orderList", "orders", "orderBook", "items", "data", "result"):
            val = res.get(k)
            if isinstance(val, list):
                orders = val
                break
            elif isinstance(val, dict):
                for sub_k in ("orderList", "orders", "orderBook", "items", "data"):
                    sub_val = val.get(sub_k)
                    if isinstance(sub_val, list):
                        orders = sub_val
                        break
            if orders is not None:
                break

    if orders is None:
        return []
    return [d for d in orders if isinstance(d, dict)]


# ------------------------------------------------------------------ orders
def _place(user_account, trade_account, symbol, price, volume, ref_id, side):
    account = None
    client_req_id = _request_id(ref_id)
    try:
        account = _account(user_account, trade_account)
        ssi_client.ensure_token(account)

        price_vnd = _to_vnd(price)
        body = {
            "accountNo": account.account_num,
            "symbol": str(symbol).upper(),
            "side": side,
            "orderType": ORDER_TYPE_LIMIT,
            "quantity": int(volume),
            "price": _price_str(price),
            "deviceId": ssi_client.get_device_id(),
            "clientRequestId": client_req_id,
            "isReturnOrderId": True,
        }

        res = ssi_client.send_request(
            account,
            method="POST",
            path=ENDPOINT_TRADING_ORDER,
            json_body=body,
            need_signature=True,
        )
    except SSIAuthError as e:
        logger.error("SSI đặt lệnh lỗi xác thực %s: %s", symbol, e)
        return {"error": str(e), "rc": 401}
    except Exception as e:
        logger.error("SSI đặt lệnh ngoại lệ %s: %s", symbol, e)
        return {"error": str(e), "rc": 500}

    if not _ok(res):
        status_code = res.get("status") or res.get("code") or 400
        err_msg = res.get("msg") or res.get("message") or res.get("error") or "Lỗi đặt lệnh SSI"
        logger.error("SSI đặt lệnh thất bại %s: %s", symbol, res)
        return {"error": err_msg, "rc": status_code}

    data = res.get("data") or res.get("result") or {}
    if not isinstance(data, dict):
        data = {}
    # Response 200 của SSI v3 trả orderId ở root: {"clientRequestId","orderId","orderStatus",...}
    order_num = _first(data, "orderId", "orderNo", "uniqueID") or _first(res, "orderId", "orderNo", "uniqueID")
    if not order_num:
        err_msg = res.get("msg") or res.get("message") or res.get("error") or "Không nhận được Order ID từ SSI"
        logger.error("SSI đặt lệnh %s không trả về orderId hợp lệ: %s", symbol, res)
        return {"error": err_msg, "rc": 400}

    return {
        "symbol": symbol,
        "volume": int(volume),
        "price": float(price),
        "account": account.account_num,
        "order_num": str(order_num),
        "time": datetime.now().strftime("%H:%M:%S"),
        "ref_id": ref_id,
        "status": "Chưa khớp",
        "type": SideOrderEnum.BUY if side == SIDE_BUY else SideOrderEnum.SELL,
        "channel": CHANNEL_ID,
    }


def handle_buy_service(user_account, trade_account, url, symbol, session,
                       asp_net_session, price, volume, ref_id):
    """Đặt lệnh mua cổ phiếu qua SSI API v3."""
    return _place(user_account, trade_account, symbol, price, volume, ref_id, "B")


def handle_sell_service(user_account, trade_account, url, symbol, session,
                        asp_net_session, price, volume, ref_id):
    """Đặt lệnh bán cổ phiếu qua SSI API v3."""
    return _place(user_account, trade_account, symbol, price, volume, ref_id, "S")


def handle_update_order_service(user_account, trade_account, url, symbol, session,
                                asp_net_session, order_num, price, update_price,
                                update_volume, ref_id, side):
    """Sửa lệnh qua PUT /api/v3/trading/order."""
    try:
        account = _account(user_account, trade_account)
        ssi_client.ensure_token(account)

        body = {
            "accountNo": account.account_num,
            "orderId": str(order_num),
            "price": _price_str(update_price),
            "quantity": int(update_volume),
            "deviceId": ssi_client.get_device_id(),
        }

        res = ssi_client.send_request(
            account,
            method="PUT",
            path=ENDPOINT_TRADING_ORDER,
            json_body=body,
            need_signature=True,
        )
    except Exception as e:
        logger.error("SSI sửa lệnh lỗi: %s", e)
        return None

    if not _ok(res):
        logger.error("SSI sửa lệnh thất bại: %s", res)
        return None

    data = res.get("data") or res.get("result") or res
    new_order_no = str(_first(data if isinstance(data, dict) else {}, "orderId", "orderNo", default=order_num))
    return {
        "orderNo": new_order_no,
        "status": StatusOrderEnum.UPDATE,
    }


def handle_cancel_order_service(user_account, url, session, asp_net_session, order_num, ref_id, trade_account=None):
    """Hủy lệnh qua DELETE /api/v3/trading/order."""
    if not order_num or str(order_num).strip() in ("", "None", "0"):
        logger.error("SSI hủy lệnh thất bại: order_num không hợp lệ (%s)", order_num)
        return None

    try:
        account = _account(user_account, trade_account)
        ssi_client.ensure_token(account)

        client_cancel_id = _request_id(ref_id)
        clean_order_num = str(order_num).strip()

        body = {
            "accountNo": account.account_num,
            "orderId": clean_order_num,
            "clientCancelId": client_cancel_id,
            "deviceId": ssi_client.get_device_id(),
        }

        logger.info("SSI gửi yêu cầu hủy lệnh: accountNo=%s, orderId=%s, clientCancelId=%s",
                    account.account_num, clean_order_num, client_cancel_id)

        res = ssi_client.send_request(
            account,
            method="DELETE",
            path=ENDPOINT_TRADING_ORDER,
            json_body=body,
            need_signature=True,
        )
    except Exception as e:
        logger.error("SSI hủy lệnh ngoại lệ: %s", e)
        return None

    logger.info("SSI kết quả phản hồi hủy lệnh %s: %s", clean_order_num, res)

    if not _ok(res):
        logger.error("SSI hủy lệnh thất bại %s: %s", clean_order_num, res)
        return None

    return res or {"status": "success", "orderNo": clean_order_num}


# ------------------------------------------------------------------ queries
def handle_orders_not_matched(user_account, trade_account, symbol, url, valid_session,
                              asp_net_session, side):
    """Lấy danh sách lệnh chưa khớp trong ngày."""
    try:
        account = _account(user_account, trade_account)
        orders = _get_order_book(account)
    except Exception as e:
        logger.error("SSI orders_not_matched lỗi: %s", e)
        return None

    not_matched = []
    is_all_symbols = not symbol or str(symbol).lower() in ("all_order", "all", "calendar_cancel", "cancel_all_buy_orders", "cancel_all_sell_orders")

    # Chuẩn hóa chiều lệnh: "BUY"/"B" -> "B", "SELL"/"S" -> "S", "ALL" -> "ALL"
    side_req = str(side).upper() if side else "ALL"
    if side_req in ("BUY", "B"):
        target_side = "B"
    elif side_req in ("SELL", "S"):
        target_side = "S"
    else:
        target_side = "ALL"

    for o in orders:
        order_status = str(_first(o, "orderStatus", "status", default="")).upper()
        if order_status in FINAL_ORDER_STATUSES:
            continue

        o_side = str(_first(o, "side", "buySell", default="")).upper()
        if o_side in ("BUY", "B"):
            o_side = "B"
        elif o_side in ("SELL", "S"):
            o_side = "S"

        o_symbol = str(_first(o, "symbol", "instrumentID", default="")).upper()
        o_qty = int(_num(_first(o, "quantity", default=0)))
        o_filled = int(_num(_first(o, "filledQuantity", "filledQty", default=0)))
        o_cancel = int(_num(_first(o, "cancelQuantity", "cancelQty", default=0)))

        os_qty = _first(o, "osQty", "remainQty", default=None)
        if os_qty is not None:
            remain_qty = int(_num(os_qty))
        else:
            remain_qty = o_qty - o_filled - o_cancel

        if remain_qty <= 0:
            continue

        order_id = str(_first(o, "orderId", "orderNo", "uniqueID", default=""))
        if not order_id:
            continue

        item = {
            "orderNo": order_id,
            "side": o_side,
            "symbol": o_symbol,
            "showPrice": _from_vnd(_first(o, "price", default=0)),
            "volume": remain_qty,
            "status": "Chưa khớp",
        }

        symbol_match = is_all_symbols or (o_symbol == str(symbol).upper())
        side_match = (target_side == "ALL") or (o_side == target_side)

        if symbol_match and side_match:
            not_matched.append(item)

    return not_matched


def handle_orders_matched(user_account, trade_account, symbol, url, valid_session,
                          asp_net_session, side):
    """Lấy danh sách lệnh đã khớp trong ngày theo mã và chiều."""
    try:
        account = _account(user_account, trade_account)
        orders = _get_order_book(account)
    except Exception as e:
        logger.error("SSI orders_matched lỗi: %s", e)
        return []

    matched = []
    is_all_symbols = not symbol or str(symbol).lower() in ("all_order", "all")

    # Chuẩn hóa chiều lệnh
    side_req = str(side).upper() if side else "ALL"
    if side_req in ("BUY", "B"):
        target_side = "B"
    elif side_req in ("SELL", "S"):
        target_side = "S"
    else:
        target_side = "ALL"

    for o in orders:
        o_side = str(_first(o, "side", "buySell", default="")).upper()
        if o_side in ("BUY", "B"):
            o_side = "B"
        elif o_side in ("SELL", "S"):
            o_side = "S"

        o_symbol = str(_first(o, "symbol", "instrumentID", default="")).upper()
        o_filled = int(_num(_first(o, "filledQuantity", "filledQty", default=0)))

        if o_filled <= 0:
            continue

        order_id = str(_first(o, "orderId", "orderNo", "uniqueID", default=""))
        if not order_id:
            continue

        symbol_match = is_all_symbols or (o_symbol == str(symbol).upper())
        side_match = (target_side == "ALL") or (o_side == target_side)

        if symbol_match and side_match:
            matched.append({
                "orderNo": order_id,
                "side": o_side,
                "symbol": o_symbol,
                "showPrice": _from_vnd(_first(o, "avgPrice", "price", default=0)),
                "volume": o_filled,
                "orderTime": str(_first(o, "orderTime", "time", default="")),
                "status": "Đã khớp",
            })

    return matched


def handle_transaction_service(user_account, trade_account, url, valid_session,
                               asp_net_session, start_date=None, end_date=None):
    """Lấy danh sách giao dịch khớp lệnh trong khoảng thời gian."""
    try:
        account = _account(user_account, trade_account)
        orders = _get_order_book(account, start_date, end_date)
    except Exception as e:
        logger.error("SSI transaction_service lỗi: %s", e)
        return []

    transactions = []
    for o in orders:
        filled = int(_num(_first(o, "filledQuantity", "filledQty", default=0)))
        if filled <= 0:
            continue
        transactions.append({
            "C_ORDER_NO": str(_first(o, "orderId", "orderNo")),
            "C_MATCH_VOL": filled,
            "C_MATCH_PRICE": _from_vnd(_first(o, "avgPrice", "price", default=0)),
            "C_ORDER_VOLUME": int(_num(_first(o, "quantity", default=0))),
            "C_SHARE_CODE": _first(o, "symbol", "instrumentID"),
            "C_ORDER_TIME": str(_first(o, "orderTime", "time", default="")),
            "C_SIDE": _first(o, "side", "buySell"),
        })
    return transactions


def _get_cash(account) -> dict:
    """Lấy số dư tài khoản từ /api/v3/trading/accountBalance và ppmmrAccount."""
    params = {"accountNo": account.account_num}
    res = ssi_client.send_request(
        account,
        method="GET",
        path=ENDPOINT_TRADING_ACCOUNT_BALANCE,
        params=params,
    )
    if not _ok(res):
        return None

    # SSI REST v3 trả về dạng:
    # 1. Root dict: res.get("equity")
    # 2. Hoặc bọc trong: res.get("data", {}).get("equity") / res.get("result", {}).get("equity")
    equity = {}
    if isinstance(res, dict):
        if isinstance(res.get("equity"), dict):
            equity = dict(res["equity"])
        elif isinstance(res.get("data"), dict) and isinstance(res["data"].get("equity"), dict):
            equity = dict(res["data"]["equity"])
        elif isinstance(res.get("result"), dict) and isinstance(res["result"].get("equity"), dict):
            equity = dict(res["result"]["equity"])
        elif isinstance(res.get("data"), dict):
            equity = dict(res["data"])
        else:
            equity = dict(res)

    if not isinstance(equity, dict):
        equity = {}

    # Thử lấy thêm purchasing power và totalAssets từ ppmmrAccount
    try:
        res_pp = ssi_client.send_request(
            account,
            method="GET",
            path=ENDPOINT_TRADING_PPMMR,
            params=params,
        )
        if _ok(res_pp) and isinstance(res_pp, dict):
            pp_equity = res_pp.get("equity")
            if not isinstance(pp_equity, dict) and isinstance(res_pp.get("data"), dict):
                pp_equity = res_pp["data"].get("equity") or res_pp["data"]
            if not isinstance(pp_equity, dict) and isinstance(res_pp.get("result"), dict):
                pp_equity = res_pp["result"].get("equity") or res_pp["result"]
            if isinstance(pp_equity, dict):
                for k in ("purchasingPower", "marginRatio", "totalAssets", "totalEquity", "ee", "ee50",
                          "lmv", "lmvMargin", "lmvNonMarginable", "liability", "netLiability"):
                    if pp_equity.get(k) is not None:
                        equity[k] = pp_equity[k]
    except Exception as e:
        logger.warning("Lỗi lấy ppmmrAccount: %s", e)

    return equity


def _get_positions_and_summary(account) -> tuple:
    """Lấy danh sách vị thế cổ phiếu và summary từ /api/v3/trading/position."""
    # Thử gọi với querySummary="true" để SSI tính toán và trả về đầy đủ giá, lãi lỗ
    params = {"accountNo": account.account_num, "querySummary": "true"}
    res = ssi_client.send_request(
        account,
        method="GET",
        path=ENDPOINT_TRADING_POSITION,
        params=params,
    )
    if not isinstance(res, (dict, list)) or not _ok(res):
        params = {"accountNo": account.account_num}
        res = ssi_client.send_request(
            account,
            method="GET",
            path=ENDPOINT_TRADING_POSITION,
            params=params,
        )

    if not isinstance(res, (dict, list)):
        return [], {}

    positions = []
    summary = {}

    if isinstance(res, list):
        return [d for d in res if isinstance(d, dict)], {}

    for container in (res, res.get("data") if isinstance(res, dict) else None, res.get("result") if isinstance(res, dict) else None):
        if isinstance(container, dict):
            if isinstance(container.get("summary"), dict):
                summary.update(container["summary"])
            for k in ("equity", "positions", "stockPositions", "holdings", "items"):
                val = container.get(k)
                if isinstance(val, list):
                    positions = [d for d in val if isinstance(d, dict)]
                    break
            if positions:
                break
        elif isinstance(container, list):
            positions = [d for d in container if isinstance(d, dict)]
            break

    # Lấy các trường summary ở root dict nếu có
    if isinstance(res, dict):
        for k in ("gainLoss", "gainLossValue", "gainLossOneDay", "gainLossOneday", "gainLossToday",
                  "dailyPnL", "totalMarketValue", "marketValue", "unrealizedPnL", "totalGainLoss"):
            if res.get(k) is not None and k not in summary:
                summary[k] = res[k]

    return positions, summary


def _get_positions(account) -> list:
    """Lấy danh sách vị thế cổ phiếu từ /api/v3/trading/position."""
    positions, _ = _get_positions_and_summary(account)
    return positions


def handle_cash_balance_service(user_account, trade_account, url, session, asp_net_session):
    """Lấy số dư tiền và sức mua tài khoản."""
    try:
        account = _account(user_account, trade_account)
        cash = _get_cash(account)
    except Exception as e:
        logger.error("SSI cash balance lỗi: %s", e)
        return {}

    if not cash:
        return {}

    withdrawable = _num(_first(cash, "withdrawable", "cashBalance", "cashBal", "eeCash", default=0))
    account_balance = _num(_first(cash, "accountBalance", "totalAssets", "totalAsset", "totalEquity", "cashBal", default=0))
    purchasing_power = _num(_first(cash, "purchasingPower", "ee50", "withdrawable", default=withdrawable))

    result = {
        "symbol": "VND",
        "cash_balance": withdrawable,
        "total_assets": account_balance,
        "cash_available": purchasing_power,
    }
    return result


def _unit_vnd(value) -> float:
    """Chuẩn hoá giá về đồng (SSI có thể trả đồng hoặc nghìn đồng)."""
    p = _num(value)
    if 0 < p < 1000:
        return p * PRICE_UNIT
    return p


def _lookup_stock_prices(symbol: str) -> tuple:
    """Tra cứu (giá hiện tại, giá vốn ước tính, giá tham chiếu đầu ngày) theo VND.

    Trả về: (current_price_vnd, cost_price_vnd, ref_price_vnd)
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return 0.0, 0.0, 0.0

    current_price = 0.0
    cost_price = 0.0
    ref_price = 0.0

    # 1. Tra cứu giá hiện tại từ Redis socket cache
    try:
        from apps.stockSocket.services.cache_service import get_price
        p = get_price(sym)
        if p and p > 0:
            current_price = _unit_vnd(p)
    except Exception:
        pass

    # 2. Tra cứu từ database (Stock, StockM1, StockD1, Configuration)
    try:
        from apps.stock.models import Stock, StockM1, StockD1
        from apps.stock.services import StockService
        stock_obj = Stock.objects.filter(name__iexact=sym).first()
        if stock_obj:
            if current_price <= 0:
                last_m1 = StockService.get_last_stock_chart_item_by_time(StockM1, stock_obj.id)
                if last_m1 and last_m1.get('close'):
                    current_price = _unit_vnd(last_m1['close'])
            # Lấy giá đóng cửa phiên gần nhất làm giá tham chiếu đầu ngày (hoặc giá mở cửa)
            last_d1 = StockD1.objects.filter(stock=stock_obj).order_by('-time').first()
            if last_d1:
                if last_d1.close and last_d1.close > 0:
                    ref_price = _unit_vnd(last_d1.close)
                elif last_d1.open and last_d1.open > 0:
                    ref_price = _unit_vnd(last_d1.open)

            from apps.configuration.details.models import Configuration
            from apps.configuration.type.services import ConfigurationTypeServices
            overview_type = ConfigurationTypeServices.get_overview()
            cfg = Configuration.objects.filter(stock=stock_obj, config_type=overview_type.id).first()
            if cfg and cfg.price_to_buy_now and cfg.price_to_buy_now > 0:
                cost_price = _unit_vnd(cfg.price_to_buy_now)
    except Exception:
        pass

    if ref_price <= 0 and cost_price > 0:
        ref_price = cost_price

    return current_price, cost_price, ref_price


def _position_metrics(p: dict) -> dict:
    """Tính các chỉ số của một vị thế (đồng), chịu được nhiều tên trường khác nhau của SSI.

    Trả về: quantity, sellable, price, avg_price, market_value, gain_loss, gain_loss_oneday, gain_loss_pct.
    """
    symbol = str(_first(p, "symbol", "instrumentID", "stock_name", default="")).strip().upper()

    # 1. Khối lượng đã về tài khoản (on_hand / quantity)
    on_hand = _num(_first(p, 
        "quantity", "onHand", "balance", "settledQuantity", "settledQty", 
        "tradableQuantity", "tradableQty", "usableQuantity", "usable", default=0
    ))
    sellable = _num(_first(p, 
        "sellableQuantity", "sellableQty", "sellable", "available", "availableQty", 
        "availableQuantity", "avaiable_vol", "volume_trade", "tradableQuantity", 
        "tradableQty", "usableQuantity", "usable", default=0
    ))

    # 2. Khối lượng đã bán đang chờ chuyển giao thanh toán (chưa bị trừ khỏi quantity tại VSD)
    sold_pending = _num(_first(p, "soldQuantity", "soldQty", default=0)) + \
                   _num(_first(p, "t1SellQuantity", "t1SellQty", default=0)) + \
                   _num(_first(p, "t2SellQuantity", "t2SellQty", default=0))

    settled_actual = max(0.0, on_hand - sold_pending)

    # 3. Khối lượng mua mới trong ngày hôm nay (T0)
    t0 = _num(_first(p, "boughtQuantity", "buyingQuantity", "buyT0", "t0", "t0Quantity", "t0Qty", default=0))

    # 4. Khối lượng mua chờ về T1, T2, T3
    t1 = _num(_first(p, "t1BuyQuantity", "buyT1", "t1", "t1Quantity", "t1Qty", default=0))
    t2 = _num(_first(p, "t2BuyQuantity", "buyT2", "t2", "t2Quantity", "t2Qty", default=0))
    t3 = _num(_first(p, "t3BuyQuantity", "buyT3", "t3", "t3Quantity", "t3Qty", default=0))
    t_waiting = t1 + t2 + t3

    # Xử lý tránh cộng trùng khi hàng vừa về trong ngày (quantity đã có nhưng t1BuyQuantity/t2BuyQuantity chưa kịp xóa)
    if on_hand > 0 and t_waiting > 0:
        if t_waiting == on_hand or t_waiting == sellable:
            # Lượng t_waiting này chính là lượng vừa về tài khoản thành on_hand/sellable
            waiting_actual = 0.0
        elif t_waiting > on_hand:
            # Có mua thêm nhưng có 1 phần trùng với on_hand vừa về
            waiting_actual = t_waiting - on_hand
        else:
            # Nếu on_hand > t_waiting: kiểm tra nếu t1 hoặc t2 trùng với sellable thì là hàng vừa về
            waiting_actual = t_waiting if (t1 != sellable and t2 != sellable) else 0.0
    else:
        waiting_actual = t_waiting

    qty = settled_actual + t0 + waiting_actual

    # Fallback khối lượng từ sàn SSI nếu chưa có trong các trường trên
    if qty <= 0:
        total_qty_raw = _first(p, 
            "totalQuantity", "totalQty", "holdingQuantity", "holdingQty", "total", 
            "totalHold", "holding", "holdQty", "volume", "totalVol", "vol", 
            "actual_vol", "volume_buy", "longQty", "net", "secQty", "shareQty",
            "shares", "totalShares", "qtty", "matchQty", "execQty", default=None
        )
        if total_qty_raw is not None and _num(total_qty_raw) > 0:
            qty = _num(total_qty_raw)
        elif qty <= 0:
            qty = _num(_first(p, "longQty", "net", "actual_vol", "volume_buy", "secBalance", "shareBalance", default=0))

    sellable = _num(_first(p, 
        "sellableQuantity", "sellableQty", "sellable", "available", "availableQty", 
        "availableQuantity", "avaiable_vol", "volume_trade", "tradableQuantity", 
        "tradableQty", "usableQuantity", "usable", default=0
    ))
    price = _unit_vnd(_first(p, "marketPrice", "lastPrice", "currentPrice", "closePrice",
                             "matchPrice", "price", default=0))
    avg = _unit_vnd(_first(p, "avgPrice", "averagePrice", "costPrice", "avgCost", "tradePrice", "buyPrice", "cost", default=0))

    symbol = str(_first(p, "symbol", "instrumentID", "stock_name", default="")).strip().upper()
    cur_p, cost_p, ref_p = 0.0, 0.0, 0.0
    if symbol:
        cur_p, cost_p, ref_p = _lookup_stock_prices(symbol)
        if price <= 0 and cur_p > 0:
            price = cur_p
        if avg <= 0 and cost_p > 0:
            avg = cost_p
        if ref_p <= 0:
            ref_p = avg if avg > 0 else price

    mv_raw = _first(p, "marketValue", "mktValue", "currentValue", "totalMarketValue", default=None)
    market_value = _num(mv_raw) if mv_raw is not None else qty * price

    gl_raw = _first(p, "gainLoss", "gainLossValue", "unrealizedPnL", "unrealizedPL",
                    "floatingPL", "tradingPL", "profitLoss", "pnl", "value_loss", "totalGainLoss", default=None)
    if gl_raw is not None:
        gain_loss = _num(gl_raw)
    elif avg and price:
        gain_loss = (price - avg) * qty
    else:
        gain_loss = 0.0

    gl1_raw = _first(p, "gainLossOneDay", "gainLossOneday", "gainLossToday", "dailyPnL",
                     "todayGainLoss", "oneDayGainLoss", "dayPnL", "dailyGainLoss", "todayPnL", default=None)
    if gl1_raw is not None and _num(gl1_raw) != 0:
        gain_loss_oneday = _num(gl1_raw)
    elif ref_p > 0 and price > 0 and qty > 0 and abs(price - ref_p) > 0.001:
        gain_loss_oneday = (price - ref_p) * qty
    elif avg > 0 and price > 0 and qty > 0 and abs(price - avg) > 0.001:
        gain_loss_oneday = (price - avg) * qty
    else:
        gain_loss_oneday = 0.0

    pct_raw = _first(p, "gainLossPercentage", "gainLossPercent", "gainLossPct",
                     "profitPercentage", "percentage_loss", "gain_loss_per", default=None)
    if pct_raw is not None and _num(pct_raw) != 0:
        gain_pct = _num(pct_raw)
    elif avg > 0 and price > 0:
        gain_pct = (price - avg) / avg * 100
    else:
        gain_pct = 0.0

    return {
        "quantity": qty, "sellable": sellable, "price": price, "avg_price": avg,
        "market_value": market_value, "gain_loss": gain_loss,
        "gain_loss_oneday": gain_loss_oneday, "gain_loss_pct": gain_pct,
        "t1_buy_vol": t1, "t2_buy_vol": t2, "t0_buy_vol": t0, "on_hand": on_hand,
    }


def handle_stock_balance_service(user_account, trade_account, symbol, url, session,
                                 asp_net_session, side):
    """Lấy thông tin danh mục cổ phiếu và số lượng khả dụng của mã symbol."""
    try:
        account = _account(user_account, trade_account)
        positions = _get_positions(account)
    except Exception as e:
        logger.error("SSI stock balance lỗi: %s", e)
        return {}

    metrics = {id(p): _position_metrics(p) for p in positions}

    def vol(p):
        return metrics[id(p)]["quantity"]

    def avail(p):
        return metrics[id(p)]["sellable"]

    existing = [p for p in positions if vol(p) > 0 or avail(p) > 0 or metrics[id(p)]["avg_price"] > 0]
    total_buy = sum(vol(p) for p in existing)
    total_trade = sum(avail(p) for p in existing)
    percent_buy_trade = (total_trade / total_buy * 100) if total_buy else 0

    stock_balance = {
        "available_vol": 0,
        "actual_vol": 0,
        "percentage_loss": 0,
        "ceil_price": UNKNOWN_CEIL_PRICE,
        "floor_price": UNKNOWN_FLOOR_PRICE,
    }

    symbol_upper = str(symbol or "").strip().upper()
    for p in positions:
        p_symbol = str(_first(p, "symbol", "instrumentID", "stock_name", default="")).strip().upper()
        if p_symbol == symbol_upper:
            m = metrics[id(p)]
            stock_balance = {
                "symbol": symbol,
                "account": trade_account,
                "avg_price": _from_vnd(m["avg_price"]),
                "ceil_price": UNKNOWN_CEIL_PRICE,
                "floor_price": UNKNOWN_FLOOR_PRICE,
                "actual_vol": m["quantity"],
                "available_vol": m["sellable"],
                "value": m["market_value"],
                "percentage_loss": m["gain_loss_pct"],
                "value_loss": m["gain_loss"],
                "t1_buy_vol": m.get("t1_buy_vol", 0),
                "t2_buy_vol": m.get("t2_buy_vol", 0),
            }
            logger.info(
                f"[SSI STOCK BALANCE] {symbol} (TK {trade_account}): actual_vol={stock_balance['actual_vol']}, "
                f"available_vol={stock_balance['available_vol']}, t1BuyQuantity={stock_balance['t1_buy_vol']}, "
                f"t2BuyQuantity={stock_balance['t2_buy_vol']}"
            )
            break

    return {
        "number_stock_existing": len(existing),
        "stock_balance": stock_balance,
        "symbols_existing": [str(_first(p, "symbol", "instrumentID", "stock_name")).strip() for p in existing if _first(p, "symbol", "instrumentID", "stock_name")],
        "percent_buy_trade": percent_buy_trade,
    }


def validate_session(user_account, trade_account, url, session, asp_net_session, retries=2):
    """Kiểm tra xác thực SSI và trả (hợp lệ, thông tin tài khoản) như SmartOne."""
    account = ssi_client.get_account(user_account, trade_account)
    if account is None:
        logger.error("SSI validate_session: không tìm thấy tài khoản %s", trade_account or user_account)
        return False, {}

    try:
        ssi_client.ensure_token(account)
    except SSIAuthError as e:
        logger.error("SSI auth lỗi: %s", e)
        return False, {}
    except Exception:
        return False, {}

    cash = None
    for attempt in range(retries + 1):
        try:
            cash = _get_cash(account)
            if cash is not None:
                break
        except SSIAuthError as e:
            logger.error("SSI auth lỗi: %s", e)
            return False, {}
        except Exception as e:
            logger.warning("SSI validate lỗi (lần %s/%s): %s", attempt + 1, retries + 1, e)
        time.sleep(1)

    if cash is None:
        return False, {}

    positions = []
    summary = {}
    try:
        positions, summary = _get_positions_and_summary(account)
        pm = [_position_metrics(p) for p in positions]
        market_value = sum(m["market_value"] for m in pm)
        gain_loss_total = sum(m["gain_loss"] for m in pm)
        gain_loss_oneday_total = sum(m["gain_loss_oneday"] for m in pm)

        # Bổ sung từ summary nếu từng vị thế chưa có hoặc summary có giá trị
        if summary:
            gl_summary = _first(summary, "gainLoss", "gainLossValue", "unrealizedPnL", "totalGainLoss", "floatingPL", "pnl", default=None)
            if gl_summary is not None and not gain_loss_total:
                gain_loss_total = _num(gl_summary)
            gl1_summary = _first(summary, "gainLossOneDay", "gainLossOneday", "gainLossToday", "dailyPnL", "todayGainLoss", "dayPnL", default=None)
            if gl1_summary is not None and not gain_loss_oneday_total:
                gain_loss_oneday_total = _num(gl1_summary)
            mv_summary = _first(summary, "totalMarketValue", "marketValue", default=None)
            if mv_summary is not None and not market_value:
                market_value = _num(mv_summary)

        # if positions and not market_value:
        #     logger.warning("SSI position không có giá thị trường, các khóa nhận được: %s",
        #                    sorted(positions[0].keys()))
    except Exception as e:
        logger.warning("SSI validate: lỗi tính vị thế: %s", e)
        market_value = gain_loss_total = gain_loss_oneday_total = 0

    # accountBalance (SSI) = tiền mặt trong tài khoản; withdrawable = tiền rút được.
    # lmv (từ ppmmrAccount) = giá trị thị trường các mã đang nắm giữ -> dùng khi /position không có giá.
    if not market_value:
        market_value = _num(_first(cash, "lmv", default=0))

    # Fallback lãi lỗ từ tổng vốn chờ về (buyT0, buyT1, buyT2) nếu SSI position không trả
    if not gain_loss_total and market_value:
        wait_cost = _num(_first(cash, "buyT0", default=0)) + _num(_first(cash, "buyT1", default=0)) + _num(_first(cash, "buyT2", default=0))
        if wait_cost > 0:
            gain_loss_total = market_value - wait_cost

    # Fallback lãi lỗ ngày nếu chưa có mà đã có lãi lỗ tổng
    if not gain_loss_oneday_total and gain_loss_total:
        gain_loss_oneday_total = gain_loss_total

    withdrawable = _num(_first(cash, "withdrawable", "cashBalance", "cashBal", "eeCash", default=0))
    account_bal = _num(_first(cash, "accountBalance", "totalAssets", "totalAsset", "totalEquity", "cashBal", default=0))
    purchasing = _num(_first(cash, "purchasingPower", "ee50", "withdrawable", default=withdrawable))
    total_equity_api = _first(cash, "totalEquity", "totalAssets", default=None)
    total_equity = _num(total_equity_api) if total_equity_api is not None else account_bal + market_value

    result = {
        "total_equity": int(total_equity),
        "cash_balance": int(withdrawable),
        "total_market_value": int(market_value),
        "cash_available": int(purchasing),
        "gain_loss_value": int(gain_loss_total),
        "gain_loss_oneday_value": int(gain_loss_oneday_total),
    }
    return True, result
