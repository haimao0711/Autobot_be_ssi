"""Xử lý chữ ký số RSA-SHA256 cho SSI FastConnect API v3.

Header `X-Signature` yêu cầu ký RSA-SHA256 trên chuỗi:
    `SecretKey + JSON_Body`
Dùng Private Key RSA do SSI cấp (định dạng XML `<RSAKeyValue>...</RSAKeyValue>`
hoặc base64 XML hoặc chuẩn PEM). Chữ ký trả về dưới dạng chuỗi Base64.
"""
import base64
import logging
import os
import xml.etree.ElementTree as ET
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256

logger = logging.getLogger(__name__)

_KEY_CACHE = {}


def load_rsa_key(key_input: str) -> RSA.RsaKey:
    """Đọc private key RSA từ đường dẫn file hoặc chuỗi trực tiếp."""
    if not key_input:
        raise ValueError("Private key không được để trống")

    if key_input in _KEY_CACHE:
        return _KEY_CACHE[key_input]

    content = key_input.strip()
    if os.path.exists(content):
        with open(content, "r", encoding="utf-8") as f:
            content = f.read().strip()

    # Kiểm tra nếu là base64 XML
    if not content.startswith("<") and not content.startswith("-----"):
        try:
            decoded = base64.b64decode(content.encode("utf-8")).decode("utf-8").strip()
            if decoded.startswith("<") or decoded.startswith("-----"):
                content = decoded
        except Exception:
            pass

    # Nếu là chuẩn PEM
    if "BEGIN" in content:
        key = RSA.import_key(content)
        _KEY_CACHE[key_input] = key
        return key

    # Định dạng XML <RSAKeyValue> của SSI
    try:
        root = ET.fromstring(content)
        tags = {child.tag: child.text.strip() for child in root if child.text}
        n = int.from_bytes(base64.b64decode(tags["Modulus"]), byteorder="big")
        e = int.from_bytes(base64.b64decode(tags["Exponent"]), byteorder="big")
        d = int.from_bytes(base64.b64decode(tags["D"]), byteorder="big")
        p = int.from_bytes(base64.b64decode(tags["P"]), byteorder="big")
        q = int.from_bytes(base64.b64decode(tags["Q"]), byteorder="big")
        key = RSA.construct((n, e, d, p, q))
        _KEY_CACHE[key_input] = key
        return key
    except Exception as err:
        logger.error("Không thể parse RSA private key: %s", err)
        raise ValueError(f"Định dạng RSA private key không hợp lệ: {err}") from err


def sign_payload(secret_key: str, payload_str: str, key_input: str or RSA.RsaKey) -> str:
    """Tạo chữ ký X-Signature bằng RSA-SHA256 trên chuỗi (secret_key + payload_str).

    Args:
        secret_key: API Secret của SSI (chuỗi text).
        payload_str: Chuỗi JSON body nguyên bản (hoặc rỗng nếu không có body).
        key_input: RSA.RsaKey hoặc đường dẫn/chuỗi key để load.

    Returns:
        Chuỗi Base64 của chữ ký số RSA-SHA256.
    """
    if isinstance(key_input, RSA.RsaKey):
        rsa_key = key_input
    else:
        rsa_key = load_rsa_key(key_input)

    to_sign = f"{secret_key}{payload_str}".encode("utf-8")
    hash_obj = SHA256.new(to_sign)
    signature = pkcs1_15.new(rsa_key).sign(hash_obj)
    return base64.b64encode(signature).decode("utf-8")
