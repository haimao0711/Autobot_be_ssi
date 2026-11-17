"""Điều phối broker: chọn SmartOne hoặc SSI theo `Account.broker`.

Module này xuất cùng tên hàm với `common.api.smartone.handler`, nên chỉ cần đổi
import trong `apps/trading` và `apps/authencation_exchange` từ
`common.api.smartone.handler` sang `common.api.broker`.
"""
import logging
from functools import wraps

from common.api.smartone import handler as smartone_handler
from common.api.ssi import handler as ssi_handler

logger = logging.getLogger(__name__)

BROKER_SSI = "ssi"


def _is_ssi(user_account=None, trade_account=None) -> bool:
    from apps.account.detail.models import Account
    qs = Account.objects.filter(broker=BROKER_SSI)
    for val in (trade_account, user_account):
        if val and (qs.filter(account_num=str(val)).exists() or qs.filter(name=str(val)).exists()):
            return True
    return False


def _dispatch(name, user_idx=0, trade_idx=1):
    smart_fn = getattr(smartone_handler, name)
    ssi_fn = getattr(ssi_handler, name)

    @wraps(smart_fn)
    def wrapper(*args, **kwargs):
        user_account = args[user_idx] if len(args) > user_idx else kwargs.get("user_account")
        trade_account = (args[trade_idx] if trade_idx is not None and len(args) > trade_idx
                         else kwargs.get("trade_account"))
        impl = ssi_fn if _is_ssi(user_account, trade_account) else smart_fn
        return impl(*args, **kwargs)
    return wrapper


handle_buy_service = _dispatch("handle_buy_service")
handle_sell_service = _dispatch("handle_sell_service")
handle_update_order_service = _dispatch("handle_update_order_service")
handle_cancel_order_service = _dispatch("handle_cancel_order_service", trade_idx=None)
handle_orders_not_matched = _dispatch("handle_orders_not_matched")
handle_orders_matched = _dispatch("handle_orders_matched")
handle_transaction_service = _dispatch("handle_transaction_service")
handle_cash_balance_service = _dispatch("handle_cash_balance_service")
handle_stock_balance_service = _dispatch("handle_stock_balance_service")
validate_session = _dispatch("validate_session")

