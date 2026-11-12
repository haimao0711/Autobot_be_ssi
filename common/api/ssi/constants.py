"""Hằng số cho SSI FastConnect Trading."""

# Base URL của FastConnect Trading (đã gồm dấu "/" cuối vì SDK nối chuỗi trực tiếp)
SSI_TRADING_URL = "https://fc-tradeapi.ssi.com.vn/"

# Handler cũ (SmartOne) làm việc với giá theo đơn vị "nghìn đồng" (vd 21.5),
# còn SSI dùng đơn vị đồng (vd 21500). Chuyển đổi tại biên của lớp SSI.
PRICE_UNIT = 1000

MARKET_VN = "VN"
ORDER_TYPE_LIMIT = "LO"
CHANNEL_ID = "TA"

# Mã lệnh mua/bán dùng chung với SmartOne
SIDE_BUY = "B"
SIDE_SELL = "S"

# Trạng thái lệnh đã kết thúc (không còn "chưa khớp").
# FF: khớp hết, CL: đã hủy, RJ: bị từ chối, EX: hết hạn.
# TODO: đối chiếu lại với bảng orderStatus của tài liệu SSI.
FINAL_ORDER_STATUSES = {"FF", "CL", "RJ", "EX"}

# FastConnect không trả giá trần/sàn trong stockPosition. Handler cũ dùng ceil_price
# để chặn giá đặt; dùng giá trị rất lớn để việc chặn này không làm hỏng giá đặt
# (sàn sẽ tự từ chối giá ngoài biên độ).
UNKNOWN_CEIL_PRICE = 10 ** 9
UNKNOWN_FLOOR_PRICE = 0

# Cache key/ TTL cho writer token (token có quyền đặt lệnh sau khi verify PIN/OTP)
WRITER_TOKEN_CACHE_KEY = "ssi-writer-token:{consumer_id}"
WRITER_TOKEN_CACHE_TTL = 6 * 3600

