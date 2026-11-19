"""Hằng số cho SSI FastConnect API v3."""

# Base URL chính thức của SSI FastConnect API v3
SSI_BASE_URL = "https://api.ssi.com.vn"

# Endpoints Authentication
ENDPOINT_AUTH_TOKEN = "/api/v3/auth/token"
ENDPOINT_AUTH_REQUEST_OTP = "/api/v3/auth/requestOtp"
ENDPOINT_AUTH_REFRESH = "/api/v3/auth/refresh"

# Endpoints Trading & Query
ENDPOINT_TRADING_ORDER = "/api/v3/trading/order"
ENDPOINT_TRADING_ORDER_BATCH = "/api/v3/trading/order/batch"
ENDPOINT_TRADING_ORDER_BOOK = "/api/v3/trading/orderBook"
ENDPOINT_TRADING_POSITION = "/api/v3/trading/position"
ENDPOINT_TRADING_ACCOUNT_BALANCE = "/api/v3/trading/accountBalance"
ENDPOINT_TRADING_PPMMR = "/api/v3/trading/ppmmrAccount"
ENDPOINT_TRADING_MAX_BUY_SELL = "/api/v3/trading/maxBuySell"

# Handler cũ (SmartOne) làm việc với giá theo đơn vị "nghìn đồng" (vd 21.5),
# còn SSI dùng đơn vị đồng (vd 21500). Chuyển đổi tại biên của lớp SSI.
PRICE_UNIT = 1000

MARKET_VN = "VN"
ORDER_TYPE_LIMIT = "LO"
CHANNEL_ID = "SSI FastConnect v3"

# Mã lệnh mua/bán
SIDE_BUY = "B"
SIDE_SELL = "S"

# Trạng thái lệnh đã kết thúc (không còn chờ khớp)
FINAL_ORDER_STATUSES = {
    "FF", "CL", "RJ", "EX",
    "FILLED", "CANCELLED", "REJECTED", "EXPIRED",
    "DA_HUY", "KHOP_HET", "TU_CHOI"
}

# Giá chặn trần / sàn mặc định
UNKNOWN_CEIL_PRICE = 10 ** 9
UNKNOWN_FLOOR_PRICE = 0

# Cache keys & TTL cho Access Token / Refresh Token
ACCESS_TOKEN_CACHE_KEY = "ssi-access-token:{consumer_id}"
REFRESH_TOKEN_CACHE_KEY = "ssi-refresh-token:{consumer_id}"
WRITER_TOKEN_CACHE_KEY = "ssi-writer-token:{consumer_id}"  # Tương thích ngược
TOKEN_CACHE_TTL = 86400  # 24 giờ
