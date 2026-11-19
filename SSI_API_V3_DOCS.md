# Tài liệu SSI FastConnect API v3 (Lưu trữ từ Developer Portal)

## 1. Authentication (Xác thực)

### 1.1. Lấy Token (`POST /api/v3/auth/token`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/auth/token`
- **Method:** `POST`
- **Headers:** `Content-Type: application/json`
- **Body:**
  ```json
  {
    "apiKey": "string (required)",
    "apiSecret": "string (required)",
    "otp": "string | null (optional: cần khi muốn cấp quyền giao dịch đặt/sửa/hủy lệnh)",
    "transactionId": "string | null (optional: trả về từ auth/requestOtp khi dùng SmartOTP)"
  }
  ```
- **Response mẫu (200):**
  ```json
  {
    "tokenType": "Bearer",
    "accessToken": "eyJhbGciOi...",
    "expiresAt": 1772075293023,
    "refreshToken": "...",
    "refreshExpiresAt": 1772075293023
  }
  ```

### 1.2. Yêu cầu OTP (`POST /api/v3/auth/requestOtp`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/auth/requestOtp`
- **Method:** `POST`
- **Headers:** `Content-Type: application/json`
- **Body:**
  ```json
  {
    "apiKey": "string (required)",
    "apiSecret": "string (required)"
  }
  ```
- **Mô tả:** 
  - Với tài khoản 2FA SMS/Email: OTP gửi về điện thoại/email.
  - Với tài khoản 2FA SmartOTP: Nhận thông báo duyệt trên app iBoard, trả về `transactionId` để truyền vào `auth/token`.

### 1.3. Làm mới Token (`POST /api/v3/auth/refresh`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/auth/refresh`
- **Method:** `POST`
- **Headers:** `Content-Type: application/json`
- **Body:**
  ```json
  {
    "refreshToken": "string (required)"
  }
  ```

---

## 2. Trading API (Đặt/Sửa/Hủy lệnh) & Query
*(Đang chờ cập nhật từ tài liệu bạn dán tiếp theo: Đặt lệnh, Sửa lệnh, Hủy lệnh, Sổ lệnh, Số dư tiền, Danh mục cổ phiếu)*


### 2.1. Lấy thông tin danh sách tài khoản (`GET /api/v3/account/info`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/account/info`
- **Method:** `GET`
- **Headers:** 
  - `Authorization: Bearer <token>` (required)
  - `Content-Type: application/json`
  - `Accept-Language: vi` (hoặc `en`)
- **Mô tả:** Lấy danh sách tài khoản giao dịch (CASH, MARGIN, DERIVATIVE).

### 2.2. Đặt lệnh mới (`POST /api/v3/trading/order`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/order`
- **Method:** `POST`
- **Headers:** 
  - `Authorization: Bearer <token>` (required, token phải được cấp quyền qua OTP)
  - `Content-Type: application/json`
  - `X-Signature: <string>` (required: Chữ ký RSA + SHA256 dùng Private Key, input = SecretKey + JSON body)
  - `Accept-Language: vi` (hoặc `en`)
  - `User-Agent: <string>` (optional)
- **Body:**
  ```json
  {
    "accountNo": "string (required, vd: T017081)",
    "symbol": "string (required, vd: SSI)",
    "side": "string (required: 'B' - Mua, 'S' - Bán)",
    "orderType": "string (required: LO, ATO, ATC, MP, MAK, MOK, MTL, PLO)",
    "quantity": "number (required, vd: 100)",
    "price": "string (required với LO, vd: '21500')",
    "deviceId": "string (required: MAC address hoặc định danh thiết bị)",
    "clientRequestId": "string | null (optional: mã lệnh do client tự sinh)",
    "userAgent": "string (optional)",
    "ipAddress": "string (optional)",
    "isReturnOrderId": true
  }
  ```
- **Lưu ý quan trọng về X-Signature:** Input để ký RSA SHA256 là chuỗi ghép: `SecretKey + JSON body`. Ví dụ cURL trên trang tài liệu hiển thị `X-Signature: sha256=abc123...` (chỉ là placeholder, chưa rõ có tiền tố `sha256=` hay không).
- **Mẫu trên trang hướng dẫn (đã đối chiếu):**
  - Header: `Content-Type` (required), `User-Agent` (optional), `Accept-Language` (optional, mặc định `en`), `X-Signature` (required), `Authorization: Bearer <token>`.
  - Tên trường chiều lệnh là **`side`** (`B`/`S`), KHÔNG phải `buySell`.
  - `quantity`: number; `price`: **string** (bắt buộc khi `orderType = LO`); `deviceId`: string required (MAC address hoặc IMEI); `userAgent`, `ipAddress`, `clientRequestId`: optional; `isReturnOrderId`: boolean | null (tài khoản cơ sở mặc định `true`).
  - Response Headers: `X-RATELIMIT-LIMIT`, `X-RATELIMIT-REMAINING`, `X-RATELIMIT-RESET`.
  - cURL mẫu:
    ```bash
    curl -X POST "https://api.ssi.com.vn/api/v3/trading/order" \
      -H "Content-Type: application/json" \
      -H "X-Signature: sha256=abc123..." \
      -H "Authorization: Bearer <token>" \
      -d '{"accountNo":"string","symbol":"string","side":"B","orderType":"LO","quantity":0.1,"price":"string","deviceId":"string"}'
    ```
  - Response 200 (các trường nằm ở **root**, không bọc trong `data`):
    ```json
    {
      "clientRequestId": "string",
      "orderId": "string",
      "orderStatus": "string",
      "clientModifyId": "string",
      "clientCancelId": "string"
    }
    ```
  - Trang tài liệu không nêu đơn vị của `price` (đồng hay nghìn đồng).

### 2.3. Sửa lệnh (`PUT /api/v3/trading/order`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/order`
- **Method:** `PUT`
- **Headers:** 
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
  - `X-Signature: <string>` (RSA + SHA256, input = SecretKey + JSON body)
- **Body:**
  ```json
  {
    "accountNo": "string (required)",
    "orderId": "string | null (optional nếu có clientRequestId)",
    "clientRequestId": "string | null (optional nếu có orderId)",
    "clientModifyId": "string | null (optional)",
    "symbol": "string | null (optional)",
    "side": "string | null (optional: 'B' / 'S')",
    "orderType": "string | null (optional: 'LO', ...)",
    "price": "string | null (optional, vd: '21600')",
    "quantity": "number | null (optional)",
    "deviceId": "string (required)",
    "userAgent": "string | null (optional)",
    "ipAddress": "string | null (optional)"
  }
  ```

### 2.4. Hủy lệnh (`DELETE /api/v3/trading/order`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/order`
- **Method:** `DELETE`
- **Headers:** 
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
  - `X-Signature: <string>` (RSA + SHA256, input = SecretKey + JSON body)
- **Body:**
  ```json
  {
    "accountNo": "string (required)",
    "orderId": "string | null (optional nếu có clientRequestId)",
    "clientRequestId": "string | null (optional)",
    "clientCancelId": "string | null (optional)",
    "deviceId": "string (required)",
    "userAgent": "string | null (optional)",
    "ipAddress": "string | null (optional)"
  }
  ```

### 2.5. Đặt nhiều lệnh cùng lúc (Batch Order) (`POST /api/v3/trading/order/batch`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/order/batch`
- **Method:** `POST`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`, `X-Signature: <string>` (Input = SecretKey + JSON body)
- **Body:**
  ```json
  {
    "batchRequestId": "string (required, duy nhất trong ngày)",
    "batchRequestTime": 1772075293023, // epoch ms UTC
    "orders": [
      // mảng các lệnh (tối đa 50 lệnh, cấu trúc giống lệnh đơn)
    ]
  }
  ```

### 2.6. Hủy nhiều lệnh cùng lúc (Batch Cancel) (`DELETE /api/v3/trading/order/batch`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/order/batch`
- **Method:** `DELETE`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`, `X-Signature: <string>` (Input = SecretKey + JSON body)
- **Body:**
  ```json
  {
    "batchRequestId": "string (required)",
    "batchRequestTime": 1772075293023,
    "orders": [
      // mảng các lệnh cần hủy (tối đa 50 lệnh, cấu trúc giống hủy lệnh đơn)
    ]
  }
  ```

### 2.7. Lấy thông tin vị thế / danh mục cổ phiếu (`GET /api/v3/trading/position`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/position`
- **Method:** `GET`
- **Headers:** 
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required, vd: T017081)
  - `clientId`: string (optional, 6 số tài khoản khách hàng)
  - `querySummary`: boolean (optional)

### 2.8. Lấy khối lượng mua/bán tối đa (`GET /api/v3/trading/maxBuySell`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/maxBuySell`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required)
  - `symbol`: string (required)
  - `price`: string (required)

### 2.9. Lấy số dư tài khoản giao dịch (`GET /api/v3/trading/accountBalance`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/accountBalance`
- **Method:** `GET`
- **Headers:** 
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required, vd: T017081)
  - `clientId`: string (optional, 6 số định danh)

### 2.10. Lấy thông tin PP/MMR (`GET /api/v3/trading/ppmmrAccount`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/ppmmrAccount`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required)
  - `clientId`: string (optional)

### 2.11. Lấy lịch sử sổ lệnh (`GET /api/v3/trading/orderBook`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/orderBook`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required)
  - `from`: string (required, ISO 8601, vd: `2026-11-16T00:00:00Z`)
  - `to`: string (required, ISO 8601, vd: `2026-11-16T23:59:59Z`)
  - `symbol`: string (optional)
  - `orderStatus`: string (optional)
  - `pageSize`: integer (optional, mặc định 20, có thể lấy 100)
  - `pageIndex`: integer (optional, bắt đầu từ 1)

---

## 3. Lệnh điều kiện (Conditional Orders - FCO)

### 3.1. Đặt lệnh điều kiện mới (`POST /api/v3/trading/fco/order`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/fco/order`
- **Method:** `POST`
- **Headers:** 
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
  - `X-Signature: <string>` (RSA + SHA256, input = SecretKey + JSON body)
- **Hỗ trợ các loại:** `stop`, `stop_limit`, `gtd`, `bullbear`, `oco`, `trailing_stop`, `trailing_stop_limit`.
- **Body:**
  ```json
  {
    "accountNo": "string (required)",
    "symbol": "string (required)",
    "side": "string (required: 'B' / 'S')",
    "type": "string (required: loại lệnh điều kiện)",
    "price": "string (required: giá số hoặc MP, MOK, MTL)",
    "quantity": 100, // integer (required)
    "deviceId": "string (required)",
    "clientRequestId": "string | null (optional)",
    "from": "string | null (optional, YYYY/MM/DD hh:mm:ss)",
    "to": "string | null (optional, YYYY/MM/DD hh:mm:ss)",
    "code": "string | null (optional: OTP/PIN nếu token chưa xác thực)",
    "priceSlip": "number (optional)",
    "stopPrice": "number (optional)",
    "activePrice": "number (optional)",
    "trailingAmount": "number (optional)",
    "tpActivePrice": "number (optional: chốt lời)",
    "slActivePrice": "number (optional: cắt lỗ)",
    "tpPrice": "string | null (optional)",
    "slPrice": "string | null (optional)",
    "tpSlip": "number (optional)",
    "slSlip": "number (optional)",
    "operator": "string | null (optional)",
    "userAgent": "string | null (optional)",
    "ipAddress": "string (optional)"
  }
  ```

### 3.2. Hủy lệnh điều kiện (`DELETE /api/v3/trading/fco/order`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/fco/order`
- **Method:** `DELETE`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`, `X-Signature: <string>`
- **Body:**
  ```json
  {
    "fcoId": "string (required: ID lệnh điều kiện)",
    "code": "string | null (optional: OTP/PIN)",
    "deviceId": "string (optional)",
    "userAgent": "string | null (optional)"
  }
  ```

### 3.3. Lấy danh sách lệnh điều kiện (`GET /api/v3/trading/fco/list`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/fco/list`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `accountNo`: string (required)
  - `fcoId`: string (optional)
  - `type`: string (optional)
  - `processStatus`: string (optional)
  - `symbol`: string (optional)
  - `side`: string (optional)
  - `from`: string (optional, YYYY/MM/DD)
  - `to`: string (optional, YYYY/MM/DD)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional, default: 10)

### 3.4. Lấy sổ lệnh phát sinh từ lệnh điều kiện (`GET /api/v3/trading/fco/orderbook`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/fco/orderbook`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `fcoId`: string (required)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional, default: 10)

### 3.5. Lịch sử thay đổi trạng thái lệnh điều kiện (`GET /api/v3/trading/fco/statusHistory`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/trading/fco/statusHistory`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `fcoId`: string (required)

---

## 4. Market Data API (Dữ liệu thị trường)

### 4.1. Lấy danh sách chứng khoán theo bảng/sàn (`GET /api/v3/data/securitiesByBoard`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/securitiesByBoard`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:** (Cung cấp 1 trong 3)
  - `symbol`: string (optional, mã CK)
  - `board`: string (optional: `HOSE`, `HNX`, `UPCOM`)
  - `index`: string (optional, mã chỉ số)

### 4.2. Tổng hợp giao dịch mã CK/chỉ số theo ngày (`GET /api/v3/data/securitiesSummary`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/securitiesSummary`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `symbol`: string (optional, mã CK)
  - `index`: string (optional, mã chỉ số vd VNINDEX, VN30)
  - `from`: string (required, YYYY/MM/DD)
  - `to`: string (required, YYYY/MM/DD)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional, default: 10)

### 4.3. Danh sách các chỉ số thị trường (`GET /api/v3/data/indexList`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/indexList`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `board`: string (optional: `HOSE`, `HNX`, `UPCOM`)

### 4.4. Tổng hợp của một chỉ số theo ngày (`GET /api/v3/data/indexSummary`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/indexSummary`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:** (Cung cấp 1 trong 2: board hoặc index)
  - `board`: string (optional: `HOSE`, `HNX`, `UPCOM`)
  - `index`: string (optional, vd: VNINDEX, VN30, HNX30)
  - `tradingDate`: string (optional, YYYY/MM/DD)

### 4.5. Lấy dữ liệu nến OHLCV (`GET /api/v3/data/ohlc`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/ohlc`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `symbol`: string (required)
  - `from`: string (required, `YYYY/MM/DD` hoặc `YYYY/MM/DD HH:mm:ss`)
  - `to`: string (required, `YYYY/MM/DD` hoặc `YYYY/MM/DD HH:mm:ss`)
  - `timeFrame`: string (optional: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `1d`, default: `1d`)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional, default: 10)

### 4.6. Giá trần, sàn, tham chiếu (`GET /api/v3/data/masterdata`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/masterdata`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `from`: string (optional, YYYY/MM/DD)
  - `to`: string (optional, YYYY/MM/DD)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional, default: 10)
- **Mô tả:** Lấy thông tin giá trần, giá sàn, giá tham chiếu của chứng khoán theo ngày giao dịch.

### 4.7. Danh sách sự kiện quyền doanh nghiệp (`GET /api/v3/data/corporateActions`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/corporateActions`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `symbol`: string (optional)
  - `from`: string (required, YYYY/MM/DD)
  - `to`: string (required, YYYY/MM/DD, khoảng cách <= 5 ngày)
  - `language`: string (optional: `vn` hoặc `en`)
  - `pageIndex`: integer (optional, default: 1)
  - `pageSize`: integer (optional: 10, 20, 50, 100)

### 4.8. Tải file dữ liệu lịch sử Parquet (`GET /api/v3/data/file`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/file`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Query Parameters:**
  - `symbol`: string (required)
  - `timeFrame`: string (optional: `1m`, `15m`, `1d`)
  - `fromDate`: string (optional: `YYYY/MM/DD`, bắt buộc với 1m/15m)
  - `toDate`: string (optional: `YYYY/MM/DD`, bắt buộc với 1m/15m)
- **Mô tả:** Trả về link tải file dữ liệu lịch sử nén dạng Parquet (`dataLink`). Link có hiệu lực 30 phút.

### 4.9. Download file Parquet (`GET /api/v3/data/download`)
- **Server URL:** `https://api.ssi.com.vn`
- **Path:** `/api/v3/data/download`
- **Method:** `GET`
- **Headers:** `Authorization: Bearer <token>`
- **Query Parameters:**
  - `token`: string (required, token nhận từ dataLink)

---

## 5. WebSocket Streaming (Real-time)

### 5.1. Kết nối & Xác thực WebSocket
- **Endpoint:** `wss://stream.ssi.com.vn`
- **Điều kiện:** Access token còn hiệu lực, duy trì ping/pong heartbeat.
- **Xác thực:** Gửi thông tin kèm token sau khi kết nối:
  ```json
  {
    "client_id": "string",
    "api_key": "<api-key>",
    "api_secret": "<api-secret>",
    "access_token": "<access-token>"
  }
  ```
- **Cấu trúc request tổng quát:**
  ```json
  {
    "method": "SUBSCRIBE | UNSUBSCRIBE | LIST_SUBSCRIPTION | PING | PONG",
    "channel": "TRADING | DATA | HEARTBEAT",
    "topics": ["trade.SSI@tick", "trade.SSI@1m", "room.*"]
  }
  ```
- **Các Event type chính:**
  - `orderEvent`: Sự kiện cập nhật lệnh
  - `orderMatchEvent`: Sự kiện lệnh khớp
  - `clientPortfolioEvent`: Cập nhật danh mục / số dư
  - `fcoEvent`: Sự kiện lệnh điều kiện
  - `trade`, `quote`, `room`, `put`, `oddlot`, `market`, `trade.index`, `indexsummary`.

### 5.2. WebSocket Heartbeat & Topic Convention
- **Heartbeat Ping/Pong:**
  - Server gửi: `{"method": "PING", "channel": "HEARTBEAT", "time": "dd-mm-yyyy hh-mm-ss"}`
  - Client phản hồi: `{"method": "PONG", "channel": "HEARTBEAT", "time": "dd-mm-yyyy hh-mm-ss"}`
- **Quy ước Topic:**
  - Lệnh: `order.<accountNo>`
  - Danh mục / số dư: `portfolio.<accountNo>`
  - Khớp lệnh mã CK: `trade.<symbol>@tick|1m|5m`
  - Giá khớp chỉ số: `trade.index.<index_code>@tick|1m|5m`
  - Bảng giá / Quote: `quote.<symbol>@tick`

### 5.3. Chi tiết các Event Trading qua WebSocket
- **`orderEvent` (Cập nhật lệnh):**
  - Chứa: `orderId`, `clientRequestId`, `symbol`, `side`, `orderType`, `accountNo`, `price`, `quantity`, `cancelQty`, `osQty` (chưa khớp), `filledQty` (đã khớp), `orderStatus`, `rejectReason`, `errorCode`.
- **`orderMatchEvent` (Khớp lệnh):**
  - Chứa: `orderId`, `clientRequestId`, `symbol`, `side`, `accountNo`, `matchPrice`, `matchQty`, `matchTime`.
- **`clientPortfolioEvent` (Thay đổi danh mục/tài sản):**
  - Chứa: `symbol`, `accountNo`, `longQty`, `shortQty`, `net`, `tradePrice`, `marketPrice`, `floatingPL`, `tradingPL`.
- **`fcoEvent` (Sự kiện lệnh điều kiện):**
  - Chứa: `fcoId`, `processStatus`, `matchedQuantity`, `isPlaceOrder`, `instrumentID`, `quantity`, `price`.

### 5.4. Market Streaming Topics
- `trade.<symbol>@[tick/1m/5m]`: Khớp lệnh mã CK (giá `p`, kl `q`, avg `a`, side `si`, tổng kl `v`).
- `trade.index.<index_code>`: Điểm số realtime của chỉ số (VN30, VNINDEX...).
- `indexsummary.<index_code>`: Tổng hợp chỉ số (độ rộng, thanh khoản, adv, dec, ce, fl...).
- `quote.<symbol>`: Sổ lệnh bid/ask 5 mức giá.
- `market.<symbol>`: Thông tin đầu ngày (giá trần `ce`, sàn `fl`, tham chiếu `ref`) & cờ phiên `f` (ATO, LO, ATC).
- `room.<symbol>`: Room ngoại.
- `put.<symbol>`: Khớp thỏa thuận.
- `oddlot.<symbol>`: Giao dịch lô lẻ.

### 5.5. WebSocket Trading API (`/ws/v3/trading`)
- **Endpoint:** `wss://api.ssi.com.vn/ws/v3/trading?token=<jwt>`
- **Xác thực:** Truyền qua Query param `?token=<jwt>` hoặc header `Authorization: Bearer <jwt>`.
- **Giới hạn:** Tối đa 10 kết nối đồng thời / API Key.
- **Lưu ý:** Server trả HTTP 101 Switching Protocols. Nếu lỗi 401/403/429, server gửi message thông báo rồi mới ngắt kết nối:
  `{"code": 401, "msg": "Invalid or expired token"}`

### 5.6. WebSocket Ping/Pong (`/ws/v3/trading`)
- **Client ping:** `{"method": "ping_pong"}`
- **Server pong:** `{"method": "ping_pong", "channel": "HEARTBEAT", "status": "ok", "message": "pong"}`

### 5.7. Đặt lệnh qua WebSocket (`order.place`)
- **Method:** `order.place`
- **Cấu trúc message:**
  ```json
  {
    "id": "req-0001",
    "method": "order.place",
    "params": {
      "symbol": "SSI",
      "side": "B",
      "orderType": "LO",
      "price": "45.50",
      "quantity": 1000,
      "accountNo": "0901351",
      "deviceId": "MAC-AABBCCDDEEFF",
      "clientRequestId": "req-0001"
    },
    "signature": "base64(RSA-SHA256(...))",
    "timestamp": 1755000000000
  }
  ```
- **Response mẫu (200):**
  ```json
  {
    "id": "req-0001",
    "method": "order.place",
    "status": 200,
    "result": {
      "clientRequestId": "req-0001",
      "orderStatus": "PD"
    }
  }
  ```

### 5.8. Sửa lệnh qua WebSocket (`order.amend`)
- **Method:** `order.amend`
- **Cấu trúc message:**
  ```json
  {
    "id": "req-0002",
    "method": "order.amend",
    "params": {
      "orderId": "ORD123456",
      "accountNo": "0901351",
      "price": "46.00",
      "quantity": 500,
      "deviceId": "MAC-AABBCCDDEEFF"
    },
    "signature": "base64(RSA-SHA256(...))",
    "timestamp": 1755000001000
  }
  ```
- **Lưu ý:** Truyền đúng một trong hai trường: `orderId` HOẶC `clientRequestId`.

### 5.9. Hủy lệnh qua WebSocket (`order.cancel`)
- **Method:** `order.cancel`
- **Cấu trúc message:**
  ```json
  {
    "id": "req-0003",
    "method": "order.cancel",
    "params": {
      "orderId": "ORD123456",
      "accountNo": "0901351",
      "deviceId": "MAC-AABBCCDDEEFF"
    },
    "signature": "base64(RSA-SHA256(...))",
    "timestamp": 1755000002000
  }
  ```
- **Lưu ý:** Truyền đúng một trong hai trường: `orderId` HOẶC `clientRequestId`.

### 5.10. Batch đặt lệnh qua WebSocket (`order.batchNew`)
- **Method:** `order.batchNew`
- **Params:** `batchRequestId`, `batchRequestTime`, `orders` (mảng tối đa 50 lệnh).
- **Ký số:** Chữ ký RSA+SHA256 trên toàn bộ nội dung `params`.

### 5.11. Batch hủy lệnh qua WebSocket (`order.batchCancel`)
- **Method:** `order.batchCancel`
- **Params:** `batchRequestId`, `batchRequestTime`, `orders` (mảng tối đa 50 lệnh cần hủy).
- **Ký số:** Chữ ký RSA+SHA256 trên toàn bộ nội dung `params`.

### 5.12. Đặt lệnh điều kiện qua WebSocket (`fco.place`)
- **Method:** `fco.place`
- **Params:** `symbol`, `side`, `type`, `price`, `quantity`, `accountNo`, `deviceId`, v.v.
- **Ký số:** Chữ ký RSA+SHA256 trên toàn bộ nội dung `params`.
- **Response:** Trả về ngay `fcoId`.

### 5.13. Hủy lệnh điều kiện qua WebSocket (`fco.cancel`)
- **Method:** `fco.cancel`
- **Params:** `fcoId`, `code` (optional), `deviceId` (optional), `signature`, `timestamp`.

### 5.14. Sổ lệnh phát sinh từ lệnh điều kiện qua WebSocket (`fco.orderbook`)
- **Method:** `fco.orderbook`
- **Params:** `fcoId`, `pageIndex`, `pageSize`, `timestamp` (Không yêu cầu chữ ký).
- **Kết quả:** Trả về danh sách lệnh con phát sinh với các trường: `orderId`, `accountNo`, `symbol`, `price`, `quantity`, `matchedQuantity`, `osQuantity`, `status`, v.v.

### 5.15. Danh sách lệnh điều kiện qua WebSocket (`fco.list`)
- **Method:** `fco.list`
- **Params:** `accountNo`, `fcoId`, `type`, `processStatus`, `symbol`, `side`, `from`, `to`, `pageIndex`, `pageSize`, `timestamp`.

### 5.16. Lịch sử trạng thái FCO qua WebSocket (`fco.statusHistory`)
- **Method:** `fco.statusHistory`
- **Params:** `fcoId`, `timestamp`.

### 5.17. Vị thế/danh mục qua WebSocket (`query.position`)
- **Method:** `query.position`
- **Params:** `accountNo`, `clientId` (optional), `querySummary` (optional), `timestamp`.
- **Sample Response:**
  ```json
  {
    "id": "req-0009",
    "method": "query.position",
    "status": 200,
    "result": {
      "equity": [
        {
          "accountNo": "0901351",
          "symbol": "SSI",
          "quantity": "1000",
          "sellableQuantity": "1000"
        }
      ],
      "derivative": null
    }
  }
  ```
- **Lưu ý:** Cấu trúc vị thế cổ phiếu cơ sở gồm `quantity` (tổng khối lượng) và `sellableQuantity` (khối lượng khả dụng có thể bán)!

### 5.18. Khối lượng mua bán tối đa qua WebSocket (`query.maxBuySell`)
- **Method:** `query.maxBuySell`
- **Params:** `accountNo`, `symbol`, `price`, `timestamp`.
- **Sample Response:**
  ```json
  {
    "id": "req-0010",
    "method": "query.maxBuySell",
    "status": 200,
    "result": {
      "accountNo": "0901351",
      "maxBuyQty": 5000,
      "marginRatio": "50%",
      "purchasingPower": 227500,
      "maxSellQty": 1000
    }
  }
  ```
- **Lưu ý:** Cho biết chuẩn xác `maxBuyQty`, `maxSellQty`, `purchasingPower` (sức mua)!

### 5.19. Số dư tài khoản qua WebSocket (`query.accountBalance`)
- **Method:** `query.accountBalance`
- **Params:** `accountNo`, `clientId` (optional), `timestamp`.
- **Sample Response:**
  ```json
  {
    "id": "req-0011",
    "method": "query.accountBalance",
    "status": 200,
    "result": {
      "equity": {
        "accountNo": "0901351",
        "accountBalance": "50000000",
        "withdrawable": "45000000"
      },
      "derivative": null
    }
  }
  ```
- **Lưu ý:** Cấu trúc số dư tiền gồm `accountBalance` (tổng số dư) và `withdrawable` (tiền mặt có thể rút)!

### 5.20. PP/MMR qua WebSocket (`query.ppmmrAccount`)
- **Method:** `query.ppmmrAccount`
- **Params:** `accountNo`, `timestamp`.
- **Sample Response:**
  ```json
  {
    "id": "req-0012",
    "method": "query.ppmmrAccount",
    "status": 200,
    "result": {
      "equity": {
        "accountNo": "0901351",
        "purchasingPower": "227500000",
        "marginRatio": "165%"
      },
      "derivative": null
    }
  }
  ```
- **Lưu ý:** Cho biết `purchasingPower` (sức mua thực tế của tài khoản) và `marginRatio` (tỷ lệ ký quỹ)!

### 5.21. Sổ lệnh qua WebSocket (`query.orderBook`)
- **Method:** `query.orderBook`
- **Params:** `accountNo`, `from` (yyyy/MM/dd), `to` (yyyy/MM/dd), `symbol`, `orderStatus`, `pageIndex`, `pageSize`, `timestamp`.
- **Sample Response:**
  ```json
  {
    "id": "req-0013",
    "method": "query.orderBook",
    "status": 200,
    "result": {
      "accountNo": "0901351",
      "totalRecord": 1,
      "orderList": [
        {
          "clientRequestId": "req-0001",
          "orderId": "ORD123456",
          "symbol": "SSI",
          "side": "B",
          "orderType": "LO",
          "quantity": "1000",
          "price": "45.50",
          "filledQty": "1000",
          "cancelQty": "0",
          "avgPrice": "45.48",
          "inputTime": "2026/09/09 09:15:32",
          "modifiedTime": "2026/09/09 09:16:01",
          "orderStatus": "FF",
          "message": ""
        }
      ]
    }
  }
  ```
- **Lưu ý:** Cấu trúc chi tiết lệnh trong sổ lệnh (`orderList`) cho biết: `orderId`, `clientRequestId`, `symbol`, `side`, `price`, `quantity`, `filledQty`, `cancelQty`, `avgPrice`, `orderStatus` (ví dụ `FF` = Khớp hết, `PD` = Đang chờ...).

### 5.22. Subscribe / Unsubscribe trên WebSocket Trading
- **Format:**
  ```json
  {
    "method": "SUBSCRIBE",
    "channel": "TRADING",
    "topics": ["order.0901351", "portfolio.1231996"]
  }
  ```
- **Lưu ý:** Không dùng wildcard `*`, phải chỉ định rõ `accountNo`.
- **Thông báo trả về:**
  ```json
  {
    "channel": "TRADING",
    "topic": "order.0901351",
    "data": {
      "eventType": "orderEvent",
      "clientRequestId": "req-0001",
      "orderId": "ORD123456",
      "orderStatus": "FF"
    }
  }
  ```
