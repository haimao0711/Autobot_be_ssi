# Hướng dẫn Chuyển đổi và Vận hành SSI FastConnect API v3 trên Server

> **Phiên bản:** FastConnect API v3 (`https://api.ssi.com.vn`)  
> **Thư mục chạy bản SSI:** `/var/www/AutoBotCk_Backend_Ssi`  
> **Thư mục bản cũ (VPS SmartOne):** `/var/www/AutoBotCk_Backend`  
> **Nguyên tắc an toàn:** Dùng thư mục code mới, build image riêng; dùng chung Postgres và Redis qua volume `autobotck_backend_postgres_data` và `autobotck_backend_redis_data`.

---

## 📋 Checklist Vận hành

| # | Việc | Trạng thái |
|---|---|---|
| 1 | Viết module chữ ký số RSA-SHA256 (`common/api/ssi/signature.py`) | ✅ Hoàn thành |
| 2 | Viết lại `common/api/ssi/client.py` theo FastConnect v3 REST API | ✅ Hoàn thành |
| 3 | Viết lại `common/api/ssi/handler.py` (Đặt/Sửa/Hủy/Sổ lệnh/Số dư/Vị thế) | ✅ Hoàn thành |
| 4 | Cập nhật và chạy thành công Unit Test (`common/api/ssi/tests.py`) | ✅ Hoàn thành (7/7 tests passed) |
| 5 | Đặt private key RSA tại `/var/www/AutoBotCk_Backend_Ssi/secrets/ssi_private.pem` | ✅ Hoàn thành |
| 6 | Push code lên nhánh `dev` và pull trên VPS | ⏳ Thực hiện tiếp |
| 7 | Restart containers trên VPS (`stock-predict-api`, `celery-worker`) | ⏳ Thực hiện tiếp |
| 8 | Cập nhật tài khoản thử nghiệm trên DB sang `broker = 'ssi'` | ⏳ Thực hiện tiếp |
| 9 | Đăng nhập OTP và kiểm thử lệnh thực tế | ⏳ Thực hiện tiếp |

---

## 🛠️ Các lệnh thực hiện trên Server VPS

### 1. Cập nhật code mới nhất từ git
```bash
cd /var/www/AutoBotCk_Backend_Ssi
git pull origin dev
docker compose restart stock-predict-api celery-worker-1 celery-worker-2 celery-beat
```

### 2. Cấu hình tài khoản thử nghiệm trong Database PostgreSQL
Chạy lệnh vào `psql`:
```bash
docker exec -it stock-predict-postgres-1 psql -U postgres -d postgres
```
Thực hiện truy vấn SQL:
```sql
-- Kiểm tra danh sách tài khoản hiện có
SELECT id, name, account_num, broker FROM account;

-- Cập nhật tài khoản thử nghiệm sang broker SSI
UPDATE account
SET broker = 'ssi',
    ssi_consumer_id = '0TzoSXEDsqDFcGjaH80fVK0N41VavOpq',
    ssi_consumer_secret = '<API_SECRET_CUA_BAN>',
    ssi_private_key_path = '/app/secrets/ssi_private.pem',
    ssi_two_fa_type = 1
WHERE account_num = 'T017081';  -- Thay bằng số tài khoản SSI của bạn

-- Kiểm tra lại
SELECT id, name, account_num, broker, ssi_consumer_id, ssi_two_fa_type FROM account WHERE broker = 'ssi';
\q
```

### 3. Quy trình Đăng nhập OTP hằng ngày & Vận hành
1. **Bấm "Lấy OTP" hoặc gọi API đăng nhập lần đầu:**
   - Bot gọi `POST /api/v3/auth/requestOtp`. SSI gửi SMS/Email OTP hoặc thông báo trên app iBoard.
2. **Nhập mã OTP:**
   - Người dùng nhập mã OTP trên giao diện (hoặc gửi API `POST /api/authencation/stock-exchange/` với `{ "otp": "xxxxxx" }`).
   - Bot gọi `POST /api/v3/auth/token` với `apiKey`, `apiSecret` và `otp`.
   - Token JWT được cấp và tự động lưu vào Redis (`ssi-access-token:*`).
3. **Bot tự động vận hành trong ngày:**
   - Celery worker định kỳ gọi `ensure_token`, tự động sử dụng token trong Redis để đặt/sửa/hủy lệnh và query số dư/danh mục mà không cần nhập lại OTP trong phiên.
   - Nếu token hết hạn, bot tự dùng `refreshToken` để gia hạn.

---

## 🔍 Kiểm tra Log vận hành
```bash
# Xem log API
docker logs -f autobotck_backend_ssi-stock-predict-api-1 --tail 100

# Xem log Celery Worker
docker logs -f autobotck_backend_ssi-celery-worker-1-1 --tail 100
```
