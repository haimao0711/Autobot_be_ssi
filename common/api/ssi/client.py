"""Client HTTP kết nối SSI FastConnect API v3.

Quản lý Access Token (qua OTP / Refresh Token) và thực hiện các cuộc gọi REST API
kèm xác thực JWT và chữ ký số RSA-SHA256 (header X-Signature).
"""
import json
import logging
import os
import threading
import uuid
import requests
from django.core.cache import cache as _django_cache

from common.api.ssi.constants import (
    ACCESS_TOKEN_CACHE_KEY,
    ENDPOINT_AUTH_REFRESH,
    ENDPOINT_AUTH_REQUEST_OTP,
    ENDPOINT_AUTH_TOKEN,
    REFRESH_TOKEN_CACHE_KEY,
    SSI_BASE_URL,
    TOKEN_CACHE_TTL,
    WRITER_TOKEN_CACHE_KEY,
)
from common.api.ssi.signature import sign_payload_hex

logger = logging.getLogger(__name__)

_lock = threading.Lock()


class SSIAuthError(Exception):
    """Không lấy được token / xác thực giao dịch (PIN/OTP) thất bại."""


class _TokenStore:
    """Lưu token dùng chung giữa các container (api, celery-worker...) qua Redis."""
    _mem = {}

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
        try:
            _django_cache.set(key, value, timeout=timeout)
        except Exception:
            self._mem[key] = value

    def get(self, key):
        try:
            value = self._redis().get(key)
            if value:
                return value
        except Exception as e:
            logger.warning("SSI token store: Redis lỗi (%s), dùng Django cache", e)
        try:
            val = _django_cache.get(key)
            if val is not None:
                return val
        except Exception:
            pass
        return self._mem.get(key)

    def delete(self, key):
        try:
            self._redis().delete(key)
        except Exception as e:
            logger.warning("SSI token store: Redis lỗi khi xóa (%s)", e)
        try:
            _django_cache.delete(key)
        except Exception:
            pass
        self._mem.pop(key, None)


cache = _TokenStore()


def get_account(user_account=None, trade_account=None):
    """Tìm Account SSI theo số tài khoản giao dịch, fallback theo tên và first()."""
    from apps.account.detail.models import Account
    if isinstance(user_account, Account):
        return user_account
    if isinstance(trade_account, Account):
        return trade_account
    qs = Account.objects.all()
    account = None
    if trade_account:
        account = qs.filter(account_num=str(trade_account)).first() or qs.filter(name=str(trade_account)).first()
    if account is None and user_account:
        account = (
            qs.filter(name=str(user_account)).first()
            or qs.filter(account_num=str(user_account)).first()
            or (qs.filter(name__icontains=str(user_account)).first() if str(user_account).strip() else None)
        )
    if account is None:
        account = qs.filter(broker="ssi").first() or qs.first()
    return account


def request_otp(account) -> dict:
    """Yêu cầu SSI gửi OTP qua SMS/Email hoặc nhận notification duyệt SmartOTP trên iBoard."""
    url = f"{SSI_BASE_URL}{ENDPOINT_AUTH_REQUEST_OTP}"
    body = {
        "apiKey": account.ssi_consumer_id,
        "apiSecret": account.ssi_consumer_secret,
    }
    headers = {"Content-Type": "application/json"}
    logger.info("Yêu cầu gửi OTP SSI cho apiKey %s...", account.ssi_consumer_id[:8])
    res = requests.post(url, json=body, headers=headers, timeout=10)
    data = res.json()
    if res.status_code != 200:
        err_msg = data.get("message") or data.get("error") or str(data)
        logger.error("Yêu cầu gửi OTP thất bại: %s", err_msg)
        raise SSIAuthError(f"Gửi OTP thất bại: {err_msg}")

    tx_id = data.get("transactionId") or (data.get("data") or {}).get("transactionId")
    if tx_id:
        cache.set(f"ssi:tx_id:{account.ssi_consumer_id}", tx_id, timeout=300)
    return data


def get_access_token(account, otp: str = None, transaction_id: str = None) -> str:
    """Lấy JWT AccessToken từ SSI v3 (/api/v3/auth/token) và lưu vào Redis."""
    url = f"{SSI_BASE_URL}{ENDPOINT_AUTH_TOKEN}"
    body = {
        "apiKey": account.ssi_consumer_id,
        "apiSecret": account.ssi_consumer_secret,
    }
    if otp:
        body["otp"] = str(otp).strip()
    if not transaction_id:
        transaction_id = cache.get(f"ssi:tx_id:{account.ssi_consumer_id}")
    if transaction_id:
        body["transactionId"] = str(transaction_id).strip()

    headers = {"Content-Type": "application/json"}
    res = requests.post(url, json=body, headers=headers, timeout=10)
    data = res.json()
    if res.status_code != 200:
        err_msg = data.get("message") or data.get("error") or str(data)
        logger.error("Lấy AccessToken SSI v3 thất bại: %s", err_msg)
        raise SSIAuthError(f"Xác thực SSI thất bại: {err_msg}")

    # SSI v3 trả về dạng { accessToken, refreshToken, expiresAt, ... } hoặc bọc trong data
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    access_token = payload.get("accessToken")
    refresh_token = payload.get("refreshToken")
    expires_at = payload.get("expiresAt")

    if not access_token:
        raise SSIAuthError(f"Phản hồi không chứa accessToken: {data}")

    ttl = TOKEN_CACHE_TTL
    if expires_at:
        try:
            diff = int(expires_at / 1000 - time.time())
            if diff > 60:
                ttl = diff
        except Exception:
            pass

    # Lưu vào Redis
    cid = account.ssi_consumer_id
    cache.set(ACCESS_TOKEN_CACHE_KEY.format(consumer_id=cid), access_token, timeout=ttl)
    cache.set(WRITER_TOKEN_CACHE_KEY.format(consumer_id=cid), access_token, timeout=ttl)
    if refresh_token:
        cache.set(REFRESH_TOKEN_CACHE_KEY.format(consumer_id=cid), refresh_token, timeout=ttl * 7)

    cache.delete(f"ssi:tx_id:{cid}")
    logger.info("Đã lưu SSI AccessToken thành công cho apiKey %s (TTL: %ss)", cid[:8], ttl)
    return access_token


def refresh_access_token(account) -> str:
    """Làm mới AccessToken bằng RefreshToken (/api/v3/auth/refresh)."""
    cid = account.ssi_consumer_id
    refresh_token = cache.get(REFRESH_TOKEN_CACHE_KEY.format(consumer_id=cid))
    if not refresh_token:
        clear_writer_token(account)
        raise SSIAuthError("Không có RefreshToken để làm mới phiên SSI")

    url = f"{SSI_BASE_URL}{ENDPOINT_AUTH_REFRESH}"
    body = {"refreshToken": refresh_token}
    headers = {"Content-Type": "application/json"}
    res = requests.post(url, json=body, headers=headers, timeout=10)
    data = res.json()
    if res.status_code != 200:
        logger.warning("Refresh token SSI thất bại: %s", data)
        clear_writer_token(account)
        raise SSIAuthError("Phiên SSI đã hết hạn, cần xác thực OTP mới")

    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    access_token = payload.get("accessToken")
    new_refresh = payload.get("refreshToken")

    if access_token:
        cache.set(ACCESS_TOKEN_CACHE_KEY.format(consumer_id=cid), access_token, timeout=TOKEN_CACHE_TTL)
        cache.set(WRITER_TOKEN_CACHE_KEY.format(consumer_id=cid), access_token, timeout=TOKEN_CACHE_TTL)
    if new_refresh:
        cache.set(REFRESH_TOKEN_CACHE_KEY.format(consumer_id=cid), new_refresh, timeout=TOKEN_CACHE_TTL * 7)

    return access_token


def verify_code(account, code=None) -> str:
    """Xác thực mã PIN/OTP để lấy token lưu vào cache."""
    code = code or getattr(account, "ssi_pin", None)
    return get_access_token(account, otp=code)


def ensure_token(account) -> str:
    """Lấy token hợp lệ từ cache, tự động refresh nếu cần."""
    cid = account.ssi_consumer_id
    token = cache.get(ACCESS_TOKEN_CACHE_KEY.format(consumer_id=cid))
    if token:
        return token

    # Thử refresh bằng refresh token
    try:
        return refresh_access_token(account)
    except Exception:
        pass

    # Nếu tài khoản dùng PIN thì thử verify tự động
    if getattr(account, "ssi_two_fa_type", 1) == 0 and getattr(account, "ssi_pin", None):
        return verify_code(account, account.ssi_pin)

    raise SSIAuthError("SSI token đã hết hạn hoặc chưa đăng nhập OTP")


def ensure_writer(account):
    """Đảm bảo có token ghi (tương thích chữ ký hàm cũ)."""
    return ensure_token(account)


def clear_writer_token(account):
    """Xóa token trong cache (dùng khi đăng xuất / hết ngày)."""
    cid = account.ssi_consumer_id
    cache.delete(ACCESS_TOKEN_CACHE_KEY.format(consumer_id=cid))
    cache.delete(WRITER_TOKEN_CACHE_KEY.format(consumer_id=cid))
    cache.delete(REFRESH_TOKEN_CACHE_KEY.format(consumer_id=cid))


def get_device_id() -> str:
    """Sinh định danh deviceId duy nhất cho máy/container."""
    node = uuid.getnode()
    # Định dạng theo tài liệu SSI: "MAC-AABBCCDDEEFF" (không có dấu ':')
    return f"MAC-{node:012X}"


def send_request(account, method: str, path: str, params: dict = None,
                 json_body: dict = None, need_signature: bool = False, retries: int = 1) -> dict:
    """Gửi HTTP request tới SSI FastConnect API v3.

    Tự động gắn Authorization Bearer và ký X-Signature nếu need_signature=True.
    Tự làm mới token nếu gặp lỗi 401 Unauthorized.
    """
    token = ensure_token(account)
    url = f"{SSI_BASE_URL}{path}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept-Language": "vi",
        "User-Agent": "AutoBotCK/2.0 (SSI FastConnect v3)",
    }

    payload_str = ""
    if json_body is not None:
        payload_str = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)
        logger.info("SSI API %s %s Payload: %s", method, path, payload_str)

    if need_signature:
        sig = sign_payload_hex(payload_str, account.ssi_private_key_path)
        headers["X-Signature"] = sig

    for attempt in range(retries + 1):
        try:
            res = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=payload_str.encode("utf-8") if payload_str else None,
                timeout=12,
            )

            # Nếu token hết hạn (401), thử refresh token và thử lại 1 lần
            if res.status_code == 401:
                if attempt < retries:
                    logger.warning("SSI 401 Unauthorized, thử làm mới token...")
                    try:
                        token = refresh_access_token(account)
                    except Exception as e:
                        clear_writer_token(account)
                        raise SSIAuthError(f"Phiên SSI đã hết hạn, cần xác thực OTP mới: {e}")
                    headers["Authorization"] = f"Bearer {token}"
                    if need_signature:
                        sig = sign_payload_hex(payload_str, account.ssi_private_key_path)
                        headers["X-Signature"] = sig
                    continue
                else:
                    clear_writer_token(account)
                    raise SSIAuthError("Phiên SSI đã hết hạn, cần xác thực OTP mới")

            try:
                data = res.json()
            except Exception:
                data = {"status": res.status_code, "text": res.text}

            # Kiểm tra nếu HTTP status không thành công
            if res.status_code != 200:
                logger.error("SSI API %s %s trả về mã HTTP %s: %s", method, path, res.status_code, data)
                if res.status_code in (401, 403):
                    clear_writer_token(account)
                    raise SSIAuthError(f"Phiên SSI đã hết hạn hoặc không có quyền: {data}")
                if isinstance(data, dict):
                    data.setdefault("status", res.status_code)
                    data.setdefault("error", data.get("msg") or data.get("message") or f"HTTP {res.status_code}")
                return data

            return data

        except SSIAuthError:
            raise
        except Exception as e:
            logger.error("Lỗi gọi SSI API %s %s: %s", method, path, e)
            if attempt >= retries:
                raise

    return {}
