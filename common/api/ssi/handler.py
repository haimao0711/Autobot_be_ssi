"""Handler SSI FastConnect API v3 - tương thích giao diện `common.api.smartone.handler`.

Các hàm giữ nguyên tên, tham số và định dạng trả về của SmartOne để `apps.trading`
chuyển broker mà không cần sửa logic trading cốt lõi.
"""
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


def _from_vnd(price_vnd) -> float:
    """Đổi giá đồng (vd 21500) sang nghìn đồng (21.5)."""
    p = _num(price_vnd)
    if p > 1000:
        return round(p / PRICE_UNIT, 2)
    return round(p, 2)


def _request_id(ref_id: str = None) -> str:
    if ref_id:
        import re
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', str(ref_id))
        return sanitized[:32]
    return f"{time.time_ns() // 1000}{random.randint(0, 99):02d}"


def _account(user_account, trade_account):
    account = ssi_client.get_account(user_account, trade_account)
    if account is None:
        raise SSIAuthError(f"Không tìm thấy tài khoản SSI {trade_account or user_account}")
    return account


def _get_order_book(account, start_date=None, end_date=None) -> list:
    """Lấy danh sách lệnh trong ngày hoặc khoảng thời gian từ /api/v3/trading/orderBook."""
    fmt = "%Y/%m/%d"
    today_str = datetime.now().strftime(fmt)
    from_date = start_date or today_str
    to_date = end_date or today_str

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
        logger.error("SSI get_order_book lỗi: %s", e)
        return []

    if not isinstance(res, (dict, list)):
        return []

    orders = None
    for container in (res, res.get("data") if isinstance(res, dict) else None, res.get("result") if isinstance(res, dict) else None):
        if isinstance(container, list):
            orders = container
            break
        elif isinstance(container, dict):
            for k in ("orders", "orderList", "orderBook", "data"):
                val = container.get(k)
                if isinstance(val, list):
                    orders = val
                    break
            if orders is not None:
                break

    return _as_list(orders if orders is not None else res)


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
            "buySell": side,
            "orderType": ORDER_TYPE_LIMIT,
            "quantity": int(volume),
            "price": int(price_vnd),
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
    order_num = _first(data if isinstance(data, dict) else {}, "orderId", "orderNo", "uniqueID")
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
            "price": int(_to_vnd(update_price)),
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


def handle_cancel_order_service(user_account, url, session, asp_net_session, order_num, ref_id):
    """Hủy lệnh qua DELETE /api/v3/trading/order."""
    try:
        account = _account(user_account, None)
        ssi_client.ensure_token(account)

        body = {
            "accountNo": account.account_num,
            "orderId": str(order_num),
            "deviceId": ssi_client.get_device_id(),
        }

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

    if not _ok(res):
        logger.error("SSI hủy lệnh thất bại: %s", res)
        return None

    return res or {"status": "success", "orderNo": str(order_num)}


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
    is_all_sides = not side or str(side).upper() == "ALL"

    for o in orders:
        order_status = str(_first(o, "orderStatus", "status", default="")).upper()
        if order_status in FINAL_ORDER_STATUSES:
            continue

        o_side = str(_first(o, "side", "buySell", default="")).upper()
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

        item = {
            "orderNo": str(_first(o, "orderId", "orderNo", "uniqueID")),
            "side": o_side,
            "symbol": o_symbol,
            "showPrice": _from_vnd(_first(o, "price", default=0)),
            "volume": remain_qty,
            "status": "Chưa khớp",
        }

        symbol_match = is_all_symbols or (o_symbol == str(symbol).upper())
        side_match = is_all_sides or (o_side == str(side).upper())

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
    is_all_sides = not side or str(side).upper() == "ALL"

    for o in orders:
        o_side = str(_first(o, "side", "buySell", default="")).upper()
        o_symbol = str(_first(o, "symbol", "instrumentID", default="")).upper()
        o_filled = int(_num(_first(o, "filledQuantity", "filledQty", default=0)))

        if o_filled <= 0:
            continue

        symbol_match = is_all_symbols or (o_symbol == str(symbol).upper())
        side_match = is_all_sides or (o_side == str(side).upper())

        if symbol_match and side_match:
            matched.append({
                "orderNo": str(_first(o, "orderId", "orderNo", "uniqueID")),
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
                for k in ("purchasingPower", "marginRatio", "totalAssets", "totalEquity", "ee", "ee50"):
                    if pp_equity.get(k) is not None:
                        equity[k] = pp_equity[k]
    except Exception as e:
        logger.warning("Lỗi lấy ppmmrAccount: %s", e)

    return equity


def _get_positions(account) -> list:
    """Lấy danh sách vị thế cổ phiếu từ /api/v3/trading/position."""
    params = {"accountNo": account.account_num}
    res = ssi_client.send_request(
        account,
        method="GET",
        path=ENDPOINT_TRADING_POSITION,
        params=params,
    )
    if not isinstance(res, (dict, list)):
        return []
    if isinstance(res, list):
        return [d for d in res if isinstance(d, dict)]

    for container in (res, res.get("data"), res.get("result")):
        if isinstance(container, dict):
            for k in ("equity", "positions", "stockPositions", "holdings"):
                val = container.get(k)
                if isinstance(val, list):
                    return [d for d in val if isinstance(d, dict)]
        elif isinstance(container, list):
            return [d for d in container if isinstance(d, dict)]
    return _as_list(res)


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

    return {
        "symbol": "VND",
        "cash_balance": withdrawable,
        "total_assets": account_balance,
        "cash_available": purchasing_power,
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

    def vol(p):
        return _num(_first(p, "quantity", "onHand", "balance", default=0))

    def avail(p):
        return _num(_first(p, "sellableQuantity", "sellableQty", "available", default=0))

    existing = [p for p in positions if vol(p) > 0]
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

    symbol_upper = str(symbol).upper()
    for p in positions:
        p_symbol = str(_first(p, "symbol", "instrumentID", default="")).upper()
        if p_symbol == symbol_upper:
            avg = _num(_first(p, "avgPrice", default=0))
            market = _num(_first(p, "marketPrice", default=0))
            gain_pct = _num(_first(p, "gainLossPercentage", "percentage_loss", default=0))
            gain_val = _num(_first(p, "gainLoss", "value_loss", default=0))

            stock_balance = {
                "symbol": symbol,
                "account": trade_account,
                "avg_price": _from_vnd(avg),
                "ceil_price": UNKNOWN_CEIL_PRICE,
                "floor_price": UNKNOWN_FLOOR_PRICE,
                "actual_vol": vol(p),
                "available_vol": avail(p),
                "value": vol(p) * market if market else 0,
                "percentage_loss": gain_pct,
                "value_loss": gain_val,
            }
            break

    return {
        "number_stock_existing": len(existing),
        "stock_balance": stock_balance,
        "symbols_existing": [str(_first(p, "symbol", "instrumentID")) for p in existing],
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

    try:
        positions = _get_positions(account) or []
        market_value = sum(
            _num(_first(p, "quantity", default=0)) * _num(_first(p, "marketPrice", default=0))
            for p in positions
        )
    except Exception:
        market_value = 0

    withdrawable = _num(_first(cash, "withdrawable", "cashBalance", "cashBal", "eeCash", default=0))
    account_bal = _num(_first(cash, "accountBalance", "totalAssets", "totalAsset", "totalEquity", "cashBal", default=0))
    purchasing = _num(_first(cash, "purchasingPower", "ee50", "withdrawable", default=withdrawable))

    return True, {
        "total_equity": int(account_bal + market_value),
        "cash_balance": int(withdrawable),
        "total_market_value": int(market_value),
        "cash_available": int(purchasing),
        "gain_loss_value": 0,
        "gain_loss_oneday_value": 0,
    }
