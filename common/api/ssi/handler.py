"""Handler SSI FastConnect - tương thích chữ ký với `common.api.smartone.handler`.

Các hàm giữ nguyên tên/tham số/định dạng trả về của SmartOne để `apps.trading`
chuyển broker mà không phải sửa logic. Tham số `url`, `session`, `asp_net_session`
được giữ lại cho tương thích nhưng SSI không dùng (xác thực qua token/PIN/OTP).

Lưu ý đơn vị: handler cũ dùng giá theo nghìn đồng, SSI dùng đồng -> nhân/chia PRICE_UNIT.
Tên trường response của SSI được đọc "dung sai" (nhiều tên có thể) vì cần đối chiếu
với dữ liệu thật khi test; xem các TODO.
"""
import logging
import time
import random
from datetime import datetime, timedelta

from common.api.ssi import client as ssi_client
from common.api.ssi.client import SSIAuthError
from common.api.ssi.constants import (CHANNEL_ID, FINAL_ORDER_STATUSES, MARKET_VN,
                                      ORDER_TYPE_LIMIT, PRICE_UNIT, SIDE_BUY,
                                      UNKNOWN_CEIL_PRICE, UNKNOWN_FLOOR_PRICE)
from common.api.smartone.enums import SideOrderEnum, StatusOrderEnum

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- helpers
def _models():
    from ssi_fctrading.models import fcmodel_requests
    return fcmodel_requests


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first(item, *keys, default=None):
    for k in keys:
        if isinstance(item, dict) and item.get(k) not in (None, ""):
            return item[k]
    return default


def _as_list(data):
    """SSI có thể trả list hoặc dict bọc list; luôn trả về list các dict."""
    if data is None:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
        return [data]
    return []


def _ok(res) -> bool:
    return isinstance(res, dict) and res.get("status") == 200


def _tick(price_vnd: float) -> int:
    """Làm tròn giá theo bước giá HOSE. TODO: phân biệt HNX/UPCOM (bước 100)."""
    if price_vnd < 10000:
        step = 10
    elif price_vnd < 50000:
        step = 50
    else:
        step = 100
    return int(round(price_vnd / step) * step)


def _to_vnd(price_k: float) -> int:
    return _tick(float(price_k) * PRICE_UNIT)


def _from_vnd(price_vnd) -> float:
    return round(_num(price_vnd) / PRICE_UNIT, 2)


def _request_id() -> str:
    return f"{time.time_ns() // 1000}{random.randint(0, 99):02d}"


def _account(user_account, trade_account):
    account = ssi_client.get_account(user_account, trade_account)
    if account is None:
        raise SSIAuthError(f"Không tìm thấy tài khoản SSI {trade_account or user_account}")
    return account


def _status_text(quantity, filled):
    if filled > 0:
        return "Khớp" if filled >= quantity else "Khớp một phần"
    return "Chưa khớp"


def _order_book(client, trade_account):
    res = client.get_order_book(_models().OrderBook(trade_account))
    if not _ok(res):
        logger.error("SSI orderBook lỗi: %s", res)
        return None
    return _as_list(res.get("data"))


def _find_order(client, trade_account, order_num=None, request_id=None, retries=3):
    for attempt in range(retries):
        orders = _order_book(client, trade_account) or []
        for o in orders:
            if order_num and str(order_num) in (str(o.get("orderID")), str(o.get("uniqueID"))):
                return o
            if request_id and str(o.get("requestID")) == str(request_id):
                return o
        time.sleep(0.5)
    return None


def _order_detail(o):
    return {
        "orderNo": _first(o, "orderID", "uniqueID"),
        "side": _first(o, "buySell"),
        "symbol": _first(o, "instrumentID"),
        "showPrice": _from_vnd(_first(o, "price", default=0)),
        "volume": int(_num(_first(o, "quantity", default=0))),
    }


def _is_pending(o) -> bool:
    status = str(_first(o, "orderStatus", default="")).upper()
    return status not in FINAL_ORDER_STATUSES


# ------------------------------------------------------------------ orders
def _place(user_account, trade_account, symbol, price, volume, ref_id, side):
    account = client = request_id = None
    try:
        account = _account(user_account, trade_account)
        client = ssi_client.ensure_writer(account)
        request_id = _request_id()
        order = _models().NewOrder(
            account=account.account_num, requestID=request_id, instrumentID=symbol,
            market=MARKET_VN, buySell=side, orderType=ORDER_TYPE_LIMIT,
            price=_to_vnd(price), quantity=int(volume), channelID=CHANNEL_ID,
            code=account.ssi_pin or "", deviceId=client.get_deviceid(),
            userAgent=client.get_user_agent())
        res = client.new_order(order)
    except SSIAuthError as e:
        logger.error("SSI newOrder lỗi xác thực %s: %s", symbol, e)
        return {}
    except Exception as e:
        # Có thể lệnh đã tới sàn nhưng mất phản hồi: tra sổ lệnh theo requestID để tránh đặt trùng
        logger.error("SSI newOrder lỗi %s: %s", symbol, e)
        try:
            found = (_find_order(client, account.account_num, request_id=request_id, retries=2)
                     if client and request_id else None)
        except Exception:
            found = None
        if not found:
            return {}
        logger.warning("SSI newOrder %s: lệnh đã có trong sổ lệnh dù lỗi phản hồi", symbol)
        res = {"status": 200, "data": found}

    if not _ok(res):
        logger.error("SSI newOrder thất bại %s: %s", symbol, res)
        return {"error": (res or {}).get("message"), "rc": (res or {}).get("status")}

    data = res.get("data") or {}
    order_num = _first(data if isinstance(data, dict) else {}, "orderID", "uniqueID")
    if not order_num:
        found = _find_order(client, account.account_num, request_id=request_id)
        order_num = _first(found or {}, "orderID", "uniqueID", default="")
    return {
        "symbol": symbol,
        "volume": int(volume),
        "price": float(price),
        "account": account.account_num,
        "order_num": order_num,
        "time": datetime.now().strftime("%H:%M:%S"),
        "ref_id": ref_id,
        "status": "Chưa khớp",
        "type": SideOrderEnum.BUY if side == SIDE_BUY else SideOrderEnum.SELL,
        "channel": CHANNEL_ID,
    }


def handle_buy_service(user_account, trade_account, url, symbol, session,
                       asp_net_session, price, volume, ref_id):
    return _place(user_account, trade_account, symbol, price, volume, ref_id, "B")


def handle_sell_service(user_account, trade_account, url, symbol, session,
                        asp_net_session, price, volume, ref_id):
    return _place(user_account, trade_account, symbol, price, volume, ref_id, "S")


def handle_update_order_service(user_account, trade_account, url, symbol, session,
                                asp_net_session, order_num, price, update_price,
                                update_volume, ref_id, side):
    try:
        account = _account(user_account, trade_account)
        client = ssi_client.ensure_writer(account)
        order = _find_order(client, account.account_num, order_num=order_num, retries=1)
        if order is None:
            logger.error("SSI modify: không thấy lệnh %s", order_num)
            return []
        modify = _models().ModifyOrder(
            account=account.account_num, requestID=_request_id(),
            orderID=str(_first(order, "orderID")), marketID=_first(order, "marketID", default=MARKET_VN),
            instrumentID=symbol, price=_to_vnd(update_price), quantity=int(update_volume),
            buySell=side, orderType=_first(order, "orderType", default=ORDER_TYPE_LIMIT),
            code=account.ssi_pin or "", deviceId=client.get_deviceid(),
            userAgent=client.get_user_agent())
        res = client.modify_order(modify)
    except Exception as e:
        logger.error("SSI modifyOrder lỗi %s: %s", symbol, e)
        return []
    if not _ok(res):
        logger.error("SSI modifyOrder thất bại %s: %s", symbol, res)
        return []
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    return {"orderNo": _first(data, "orderID", "uniqueID", default=order_num),
            "status": StatusOrderEnum.UPDATE}


def handle_cancel_order_service(user_account, url, session, asp_net_session, order_num, ref_id):
    try:
        account = _account(user_account, None)
        client = ssi_client.ensure_writer(account)
        order = _find_order(client, account.account_num, order_num=order_num, retries=1)
        if order is None:
            logger.error("SSI cancel: không thấy lệnh %s", order_num)
            return {}
        cancel = _models().CancelOrder(
            account=account.account_num, requestID=_request_id(),
            orderID=str(_first(order, "orderID")), marketID=_first(order, "marketID", default=MARKET_VN),
            instrumentID=_first(order, "instrumentID"), buySell=_first(order, "buySell"),
            code=account.ssi_pin or "", deviceId=client.get_deviceid(),
            userAgent=client.get_user_agent())
        res = client.cancle_order(cancel)  # SDK đặt tên "cancle_order"
    except Exception as e:
        logger.error("SSI cancelOrder lỗi %s: %s", order_num, e)
        return {}
    return res if _ok(res) else {}


# ----------------------------------------------------------------- queries
def handle_orders_not_matched(user_account, trade_account, symbol, url, valid_session,
                              asp_net_session, side):
    try:
        account = _account(user_account, trade_account)
        orders = _order_book(ssi_client.get_client(account), account.account_num)
    except Exception as e:
        logger.error("SSI orderBook lỗi: %s", e)
        return None
    if orders is None:
        return None
    result = []
    for o in orders:
        if not _is_pending(o):
            continue
        d = _order_detail(o)
        if side != "All":
            if d["side"] != side:
                continue
            if symbol not in ("all_order", "", None) and d["symbol"] != symbol:
                continue
        d["status"] = "Chưa khớp"
        result.append(d)
    return result


def handle_orders_matched(user_account, trade_account, symbol, url, valid_session,
                          asp_net_session, side):
    try:
        account = _account(user_account, trade_account)
        orders = _order_book(ssi_client.get_client(account), account.account_num)
    except Exception as e:
        logger.error("SSI orderBook lỗi: %s", e)
        return []
    result = []
    for o in orders or []:
        d = _order_detail(o)
        if _num(_first(o, "filledQty", default=0)) <= 0:
            continue
        if d["symbol"] != symbol or d["side"] != side:
            continue
        d["orderTime"] = _first(o, "modifiedTime", "inputTime", default="")
        d["status"] = "Đã khớp"
        result.append(d)
    return result


def handle_transaction_service(user_account, trade_account, url, valid_session,
                               asp_net_session, start_date=None, end_date=None):
    """Trả về danh sách lệnh đã khớp, giữ khóa C_MATCH_VOL như SmartOne."""
    fmt = "%d/%m/%Y"
    end = end_date or datetime.now().strftime(fmt)
    start = start_date or (datetime.now() - timedelta(days=30)).strftime(fmt)
    try:
        account = _account(user_account, trade_account)
        res = ssi_client.get_client(account).get_order_history(
            _models().OrderHistory(account.account_num, start, end))
    except Exception as e:
        logger.error("SSI orderHistory lỗi: %s", e)
        return []
    if not _ok(res):
        return []
    matched = []
    for o in _as_list(res.get("data")):
        filled = _num(_first(o, "filledQty", default=0))
        if filled <= 0:
            continue
        matched.append({
            **o,
            "C_MATCH_VOL": filled,
            "C_MATCH_PRICE": _num(_first(o, "avgPrice", "price", default=0)),
            "C_ORDER_VOLUME": _num(_first(o, "quantity", default=0)),
            "C_SHARE_CODE": _first(o, "instrumentID"),
            "C_ORDER_TIME": _first(o, "inputTime", default=""),
        })
    return matched


def _cash(account):
    res = ssi_client.get_client(account).get_stock_account_balance(
        _models().StockAccountBalance(account.account_num))
    if not _ok(res):
        logger.error("SSI cashAcctBal lỗi: %s", res)
        return None
    data = res.get("data")
    items = _as_list(data)
    return items[0] if items else {}


def _positions(account):
    res = ssi_client.get_client(account).get_stock_position(
        _models().StockPosition(account.account_num))
    if not _ok(res):
        logger.error("SSI stockPosition lỗi: %s", res)
        return None
    data = res.get("data")
    if isinstance(data, dict):
        for key in ("stockPositions", "stockPosition", "positions"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return _as_list(data)


def _market_value(p) -> float:
    value = _first(p, "marketValue", "value")
    if value is not None:
        return _num(value)
    return _num(_first(p, "onHand", "balance", default=0)) * _num(_first(p, "marketPrice", default=0))


def handle_cash_balance_service(user_account, trade_account, url, session, asp_net_session):
    try:
        cash = _cash(_account(user_account, trade_account))
    except Exception as e:
        logger.error("SSI cash balance lỗi: %s", e)
        return {}
    if cash is None:
        return {}
    # TODO: đối chiếu tên trường cashAcctBal với dữ liệu thật
    return {
        "symbol": "VND",
        "cash_balance": _num(_first(cash, "withdrawable", "cashBal")),
        "total_assets": _num(_first(cash, "totalAsset", "netAssetValue", "cashBal")),
        "cash_available": _num(_first(cash, "purchasingPower", "ppAvailable", "cashBal")),
    }


def handle_stock_balance_service(user_account, trade_account, symbol, url, session,
                                 asp_net_session, side):
    try:
        positions = _positions(_account(user_account, trade_account))
    except Exception as e:
        logger.error("SSI stock balance lỗi: %s", e)
        return {}
    if positions is None:
        return {}

    # TODO: đối chiếu tên trường stockPosition (onHand/sellableQty/avgPrice/marketPrice)
    def vol(p):
        return _num(_first(p, "onHand", "balance", default=0))

    def avail(p):
        return _num(_first(p, "sellableQty", "available", "balance", default=0))

    existing = [p for p in positions if vol(p) > 0]
    total_buy = sum(vol(p) for p in existing)
    total_trade = sum(avail(p) for p in existing)
    percent_buy_trade = (total_trade / total_buy * 100) if total_buy else 0

    stock_balance = {
        "available_vol": 0, "actual_vol": 0, "percentage_loss": 0,
        "ceil_price": UNKNOWN_CEIL_PRICE, "floor_price": UNKNOWN_FLOOR_PRICE,
    }
    for p in positions:
        if _first(p, "instrumentID") == symbol:
            avg = _num(_first(p, "avgPrice", default=0))
            market = _num(_first(p, "marketPrice", default=0))
            stock_balance = {
                "symbol": symbol, "account": trade_account,
                "avg_price": _from_vnd(avg),
                "ceil_price": UNKNOWN_CEIL_PRICE, "floor_price": UNKNOWN_FLOOR_PRICE,
                "actual_vol": vol(p), "available_vol": avail(p),
                "value": _market_value(p),
                "percentage_loss": round((market - avg) / avg * 100, 2) if avg and market else 0,
                "value_loss": (market - avg) * vol(p) if avg and market else 0,
            }
            break

    return {
        "number_stock_existing": len(existing),
        "stock_balance": stock_balance,
        "symbols_existing": [_first(p, "instrumentID") for p in existing],
        "percent_buy_trade": percent_buy_trade,
    }


def validate_session(user_account, trade_account, url, session, asp_net_session, retries=2):
    """Kiểm tra xác thực SSI và trả (hợp lệ, thông tin tài khoản) như SmartOne."""
    account = ssi_client.get_account(user_account, trade_account)
    if account is None:
        logger.error("SSI: không tìm thấy tài khoản %s", trade_account or user_account)
        return False, {}

    cash = None
    for attempt in range(retries + 1):
        try:
            ssi_client.ensure_writer(account)
            cash = _cash(account)
            if cash is not None:
                break
        except SSIAuthError as e:
            logger.error("SSI auth lỗi: %s", e)
            return False, {}
        except Exception as e:
            logger.warning("SSI validate lỗi (lần %s/%s): %s", attempt + 1, retries + 1, e)
            ssi_client.reset_client(account)
        time.sleep(1)
    if cash is None:
        return False, {}

    try:
        market_value = sum(_market_value(p) for p in (_positions(account) or []))
    except Exception:
        market_value = 0
    # TODO: đối chiếu tên trường cashAcctBal với dữ liệu thật
    return True, {
        "total_equity": int(_num(_first(cash, "totalAsset", "netAssetValue", "cashBal"))),
        "cash_balance": int(_num(_first(cash, "withdrawable", "cashBal"))),
        "total_market_value": int(market_value),
        "cash_available": int(_num(_first(cash, "purchasingPower", "ppAvailable", "cashBal"))),
        "gain_loss_value": 0,
        "gain_loss_oneday_value": 0,
    }
