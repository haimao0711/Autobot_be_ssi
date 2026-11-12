"""Quản lý FCTradingClient (SDK chính thức `ssi-fctrading`) cho từng tài khoản."""
import logging
import threading

import os

from django.core.cache import cache as _django_cache

from common.api.ssi.constants import (SSI_TRADING_URL, WRITER_TOKEN_CACHE_KEY,
                                      WRITER_TOKEN_CACHE_TTL)

logger = logging.getLogger(__name__)

_clients = {}
_lock = threading.Lock()


class SSIAuthError(Exception):
    """Không lấy được token / xác thực giao dịch (PIN/OTP) thất bại."""


class _TokenStore:
    """Lưu writer token dùng chung giữa các container (api, celery-worker...).

    Production đang dùng cache theo từng tiến trình (LocMem/memcached không tồn tại),
    nên lưu bằng Redis trực tiếp; nếu Redis không dùng được thì fallback Django cache.
    """

    def _redis(self):
        import redis
        url = os.getenv("REDIS_URL") or "redis://redis:6379/0"
        return redis.Redis.from_url(url, socket_timeout=3, decode_responses=True)

    def set(self, key, value, timeout):
        try:
            self._redis().set(key, value, ex=timeout)
            return
        except Exception as e:
            logger.warning("SSI token store: Redis lỗi (%s), dùng Django cache", e)
        _django_cache.set(key, value, timeout=timeout)

    def get(self, key):
        try:
            value = self._redis().get(key)
            if value:
                return value
        except Exception as e:
            logger.warning("SSI token store: Redis lỗi (%s), dùng Django cache", e)
        return _django_cache.get(key)

    def delete(self, key):
        try:
            self._redis().delete(key)
        except Exception as e:
            logger.warning("SSI token store: Redis lỗi khi xóa (%s)", e)
        _django_cache.delete(key)


cache = _TokenStore()


def _read_private_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def get_account(user_account=None, trade_account=None):
    """Tìm Account SSI theo số tài khoản giao dịch, fallback theo tên."""
    from apps.account.detail.models import Account
    qs = Account.objects.filter(broker="ssi")
    account = None
    if trade_account:
        account = qs.filter(account_num=trade_account).first()
    if account is None and user_account:
        account = qs.filter(name=user_account).first()
    return account


def get_client(account):
    """Trả về FCTradingClient (có token đọc). Mỗi consumer_id dùng chung một client."""
    from ssi_fctrading import FCTradingClient

    key = account.ssi_consumer_id
    with _lock:
        client = _clients.get(key)
        if client is None:
            client = FCTradingClient(
                SSI_TRADING_URL,
                account.ssi_consumer_id,
                account.ssi_consumer_secret,
                _read_private_key(account.ssi_private_key_path),
                account.ssi_two_fa_type,
            )
            _clients[key] = client
        return client


def reset_client(account):
    with _lock:
        _clients.pop(getattr(account, "ssi_consumer_id", None), None)


def request_otp(account):
    """Yêu cầu SSI gửi OTP (chỉ dùng khi ssi_two_fa_type = 1)."""
    from ssi_fctrading.models import fcmodel_requests
    client = get_client(account)
    return client.get_otp(fcmodel_requests.GetOTP(account.ssi_consumer_id, account.ssi_consumer_secret))


def verify_code(account, code=None):
    """Xác thực PIN/OTP để lấy writer token, lưu vào cache dùng chung giữa các worker."""
    client = get_client(account)
    code = code or account.ssi_pin
    if not code:
        raise SSIAuthError("Thiếu mã PIN/OTP để xác thực giao dịch SSI")
    try:
        token = client.verifyCode(code)
    except Exception as e:  # SDK ném NameError(message) khi sai mã
        raise SSIAuthError(str(e))
    cache.set(WRITER_TOKEN_CACHE_KEY.format(consumer_id=account.ssi_consumer_id),
              token, timeout=WRITER_TOKEN_CACHE_TTL)
    return token


def _load_cached_writer_token(client, account):
    from ssi_fctrading.models import AccessTokenModel, fcmodel_responses
    token = cache.get(WRITER_TOKEN_CACHE_KEY.format(consumer_id=account.ssi_consumer_id))
    if not token:
        return False
    try:
        model = AccessTokenModel(fcmodel_responses.AccessToken(accessToken=token))
    except Exception:
        return False
    if model.is_expired():
        return False
    client._write_access_token = model
    return True


def ensure_writer(account):
    """Đảm bảo client có writer token hợp lệ trước khi đặt/sửa/hủy lệnh.

    - twoFA = 0 (PIN): tự verify bằng ssi_pin.
    - twoFA = 1 (OTP): cần người dùng verify OTP qua API đăng nhập; nếu chưa có
      token hợp lệ trong cache thì ném SSIAuthError.
    """
    client = get_client(account)
    if account.ssi_two_fa_type != 0:
        # OTP: Redis là nguồn sự thật duy nhất. Nếu token đã bị xóa (đăng xuất hằng ngày)
        # hoặc hết hạn thì mọi tiến trình phải dừng đặt lệnh, dù còn token trong bộ nhớ.
        if _load_cached_writer_token(client, account):
            return client
        client._write_access_token = None
        raise SSIAuthError("Writer token SSI hết hạn, cần xác thực OTP lại")
    current = getattr(client, "_write_access_token", None)
    if current is not None and not current.is_expired():
        return client
    if _load_cached_writer_token(client, account):
        return client
    verify_code(account)
    return client


def clear_writer_token(account):
    """Xóa writer token (dùng khi đăng xuất hằng ngày) để buộc nhập OTP mới."""
    key = WRITER_TOKEN_CACHE_KEY.format(consumer_id=account.ssi_consumer_id)
    cache.delete(key)
    client = _clients.get(account.ssi_consumer_id)
    if client is not None:
        client._write_access_token = None
