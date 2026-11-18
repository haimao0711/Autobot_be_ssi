"""Module giao dịch chính của hệ thống - kết nối trực tiếp SSI FastConnect v3.

Đã chuyển đổi hoàn toàn 100% sang sàn SSI FastConnect v3, loại bỏ hoàn toàn SmartOne.
"""
import logging
from common.api.ssi import handler as ssi_handler

logger = logging.getLogger(__name__)

BROKER_SSI = "ssi"


def _is_ssi(user_account=None, trade_account=None) -> bool:
    """Luôn trả về True vì toàn bộ hệ thống đã chuyển 100% sang SSI."""
    return True


handle_buy_service = ssi_handler.handle_buy_service
handle_sell_service = ssi_handler.handle_sell_service
handle_update_order_service = ssi_handler.handle_update_order_service
handle_cancel_order_service = ssi_handler.handle_cancel_order_service
handle_orders_not_matched = ssi_handler.handle_orders_not_matched
handle_orders_matched = ssi_handler.handle_orders_matched
handle_transaction_service = ssi_handler.handle_transaction_service
handle_cash_balance_service = ssi_handler.handle_cash_balance_service
handle_stock_balance_service = ssi_handler.handle_stock_balance_service
validate_session = ssi_handler.validate_session
