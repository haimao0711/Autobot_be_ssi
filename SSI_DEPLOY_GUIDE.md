# Hướng dẫn thay container backend (VPS → SSI) trên server

Server: `/var/www/AutoBotCk_Backend` · Container liên quan: `stock-predict-api`, `celery-worker` (x2), `celery-beat`, `celery-flower`. Postgres, pgbouncer, redis, nginx **không đụng tới**.

> [!IMPORTANT]
> Làm **ngoài giờ giao dịch** (sau 15:00), khi không còn lệnh chờ khớp và không còn cổ phiếu đang được bot quản lý dở.

## Bảng tiến trình

Chú thích: ✅ đã xong · ⬜ chưa làm · 👤 bạn làm · 🤖 nhờ Antigravity. Bạn tick `[x]` khi hoàn thành từng việc.

### Phần code (Antigravity đã làm)

- [x] ✅ Lớp SSI: client, handler, điều phối broker (`common/api/ssi/*`, `common/api/broker.py`)
- [x] ✅ Đăng nhập SSI bằng PIN/OTP, lưu token vào Redis dùng chung giữa các container
- [x] ✅ Xóa token SSI khi đăng xuất hằng ngày (buộc nhập OTP mới mỗi ngày)
- [x] ✅ Chống đặt trùng lệnh khi lỗi mạng
- [x] ✅ Migration `0014` (thêm 6 cột vào bảng account), SDK `ssi-fctrading` trong `requirements.txt`
- [x] ✅ `docker-compose.yml`: mount `./secrets`, thêm `REDIS_URL` cho `stock-predict-api`
- [x] ✅ `.gitignore` chặn `secrets/`, `*.pem`
- [x] ✅ Test offline 8/8 đạt

### Việc của bạn (theo thứ tự)

| # | Việc | Mục | Người làm | Xong |
|---|---|---|---|---|
| 1 | Tạo kho git private, push code | 2 | 👤 | [ ] |
| 2 | Whitelist IP VPS trên iBoard/FastConnect | 0.1 | 👤 | [ ] |
| 3 | Đặt private key vào `secrets/` trên server, `chmod 600` | 0.2 | 👤 | [ ] |
| 4 | Backup DB bằng `pg_dump` | 0.3 | 👤 | [ ] |
| 5 | Gắn thẻ image `vps-backup` để quay lại | 0.4 | 👤 | [ ] |
| 6 | ✅ Đã `docker compose down` bản cũ (giữ volume). Còn lại: kiểm tra `docker volume ls` thấy 2 volume dữ liệu | 1 | 👤 | [ ] |
| 7 | `git clone` vào `AutoBotCk_Backend_Ssi`, chép `.env*`, `redis.conf` | 2 | 👤 | [ ] |
| 8 | Build image mới | 3 | 👤 | [ ] |
| 9 | `sqlmigrate` rồi `migrate account` | 3 | 👤 | [ ] |
| 10 | `UPDATE account` bật SSI cho 1 tài khoản thử | 4 | 👤 | [ ] |
| 11 | `docker compose up -d` các container backend | 5 | 👤 | [ ] |
| 12 | Đăng nhập OTP, thử 1 lệnh 100 cp: mua, sửa, hủy | 6 | 👤 | [ ] |
| 13 | Gửi log/response thật cho Antigravity để chốt các `TODO` tên trường | — | 👤 → 🤖 | [ ] |
| 14 | Chốt `TODO` tên trường và mã `orderStatus` trong `handler.py` | — | 🤖 | [ ] |
| 15 | Chạy bot khối lượng nhỏ 1–2 phiên, rồi mở rộng | 6 | 👤 | [ ] |

> [!NOTE]
> Bước 13–14 dừng ở dữ liệu thật: cho tới khi có response từ SSI, một số tên trường số dư/danh mục/trạng thái lệnh trong code vẫn là phỏng đoán.

## 0. Chuẩn bị (làm trước, không ảnh hưởng bot đang chạy)

1. **Whitelist IP:** vào iBoard/FastConnect đăng ký IP public của VPS (`curl ifconfig.me` trên server). Không có bước này SSI sẽ từ chối.
2. **Private key:** tạo thư mục và đặt file khóa (trong thư mục dự án mới):
   ```bash
   mkdir -p /var/www/AutoBotCk_Backend_Ssi/secrets
   # chép file vào, ví dụ ssi_private.pem
   chmod 600 /var/www/AutoBotCk_Backend_Ssi/secrets/ssi_private.pem
   ```
   Trong container đường dẫn là `/app/secrets/ssi_private.pem` → đây là giá trị cho cột `ssi_private_key_path`.
3. **Backup DB:**
   ```bash
   docker exec autobotck_backend-stock-predict-postgres-1 pg_dump -U myuser stockdb > /root/stockdb_$(date +%F).sql
   ```
4. **Gắn thẻ image cũ để quay lại** (thư mục cũ giữ nguyên, không cần copy):
   ```bash
   for s in stock-predict-api celery-worker celery-beat celery-flower; do
     docker tag autobotck_backend-$s:latest autobotck_backend-$s:vps-backup
   done
   ```

> [!IMPORTANT]
> **Mô hình triển khai:** thư mục cũ `/var/www/AutoBotCk_Backend` (VPS) đã được `docker compose down` (xóa container, **giữ volume dữ liệu**). Toàn bộ stack (Postgres, pgbouncer, Redis, 4 service backend) giờ chạy từ `/var/www/AutoBotCk_Backend_Ssi` bằng `docker compose up -d`.
> - `docker-compose.yml` mới khai báo volume `postgres_data`/`redis_data` là `external`, trỏ vào `autobotck_backend_postgres_data` và `autobotck_backend_redis_data` của project cũ. Nhờ đó DB và Redis giữ nguyên dữ liệu; nếu volume không tồn tại Compose sẽ báo lỗi chứ không tạo DB rỗng.
> - **Tuyệt đối không** chạy `docker compose down -v` hay `docker volume rm` — sẽ mất dữ liệu.

## 1. Đã dừng bản cũ

Bạn đã chạy `docker compose down` ở thư mục cũ: 8 container (api, worker x2, beat, flower, redis, pgbouncer, postgres) đã bị xóa; nginx, certbot, frontend, sql_server vẫn chạy. Kiểm tra volume còn:

```bash
docker volume ls | grep autobotck_backend
# cần thấy: autobotck_backend_postgres_data và autobotck_backend_redis_data
docker network ls | grep autobotck_backend   # network ngoài, vẫn còn
```

## 2. Lấy code từ git về thư mục mới

Trên máy bạn, trước khi push: kiểm tra `git status` để chắc không có file khóa (`.gitignore` đã chặn `secrets/` và `*.pem`). Kho git nên là **private** vì `docker-compose.yml` có mật khẩu Postgres và `.env.production` có `SECRET_KEY`.

Trên server:
```bash
cd /var/www
git clone <URL kho git riêng> AutoBotCk_Backend_Ssi
cd AutoBotCk_Backend_Ssi
cp ../AutoBotCk_Backend/.env* . 2>/dev/null
cp ../AutoBotCk_Backend/redis.conf . 2>/dev/null
mkdir -p secrets   # rồi đặt ssi_private.pem vào (bước 0)
diff ../AutoBotCk_Backend/docker-compose.yml docker-compose.yml
```

## 3. Build và migrate

Postgres và pgbouncer cần chạy trước khi migrate:
```bash
cd /var/www/AutoBotCk_Backend_Ssi
docker compose up -d stock-predict-postgres pgbouncer redis
docker compose ps          # postgres, redis phải healthy
docker compose build stock-predict-api celery-worker celery-beat celery-flower
# Chỉ thêm 6 cột vào bảng account; tài khoản cũ mặc định broker='smartone'
docker compose run --rm stock-predict-api python manage.py sqlmigrate account 0014
docker compose run --rm stock-predict-api python manage.py migrate account
```
Kiểm tra dữ liệu cũ còn: `docker compose exec stock-predict-postgres psql -U myuser stockdb -c "select count(*) from account;"` (số dòng phải khác 0).

> [!NOTE]
> Service `stock-predict-api` chạy `gunicorn` trực tiếp (không qua `entrypoint.sh`) nên **migrate không tự chạy**, phải chạy tay như trên.

## 4. Bật SSI cho tài khoản

```sql
UPDATE account SET broker='ssi', ssi_consumer_id='...', ssi_consumer_secret='...',
  ssi_private_key_path='/app/secrets/ssi_private.pem', ssi_two_fa_type=1, ssi_pin=NULL
WHERE account_num='<số TK SSI>';
```
(Tên bảng thật xem `common/table_names.py`, mục `ACCOUNT`.)

## 5. Khởi động toàn bộ stack mới

```bash
cd /var/www/AutoBotCk_Backend_Ssi
docker compose up -d
docker compose ps
docker exec nginx-proxy nginx -s reload     # để nginx nhận lại địa chỉ container api mới
docker compose logs -f --tail=100 celery-worker
```

## 6. Kiểm tra

1. Gọi API đăng nhập (như đăng nhập VPS cũ): với PIN sẽ xác thực ngay; với OTP gọi lần 1 để nhận OTP, lần 2 kèm `otp`.
2. Xem log không có `SSI ... lỗi`.
3. Thử 1 lệnh 100 cổ phiếu: mua → sửa giá → hủy, đối chiếu trên iBoard.
4. Bật bot khối lượng nhỏ 1–2 phiên rồi mới tăng.

## Quy trình mỗi ngày với OTP (`ssi_two_fa_type=1`)

1. Trước giờ giao dịch: gọi API đăng nhập **không kèm `otp`** → hệ thống yêu cầu SSI gửi OTP (SMS/Email/Smart OTP) và trả thông báo "Đã gửi OTP SSI".
2. Gọi lại API đăng nhập **kèm `otp`** → lấy token đặt lệnh, bật scheduler.
3. Sau giờ giao dịch: job đăng xuất hằng ngày (`update_all_accounts_login_status`) xóa token SSI trong Redis → mọi worker dừng đặt lệnh ngay, hôm sau phải nhập OTP mới.
4. Nếu token hết hạn giữa phiên, `validate_session` trả `False` và bot dừng đặt lệnh (không tự đặt lệnh khi chưa xác thực). Lệnh đã đặt trước đó vẫn nằm trên sàn.

## Quay lại bản cũ (rollback)

```bash
# 1) dừng stack SSI (không dùng -v)
cd /var/www/AutoBotCk_Backend_Ssi
docker compose down
# 2) chạy lại stack VPS cũ từ thư mục cũ (image cũ vẫn còn, volume dữ liệu dùng chung)
cd /var/www/AutoBotCk_Backend
docker compose up -d --no-build
docker exec nginx-proxy nginx -s reload
```
Lưu ý: compose cũ khai báo volume theo tên project `autobotck_backend`, trùng với volume external của bản mới nên dữ liệu dùng chung.
Các cột mới trong DB vô hại với bản cũ, không cần xóa. Muốn tài khoản chạy lại SmartOne: `UPDATE account SET broker='smartone' WHERE ...;`

## Lưu ý

- Mật khẩu Postgres đang nằm thẳng trong `docker-compose.yml`; nên chuyển sang `.env` khi có thời gian.
- Token đặt lệnh của SSI được lưu trong Redis (`ssi-writer-token:*`) để api và worker dùng chung; hết hạn thì bot ngừng đặt lệnh cho đến khi đăng nhập lại.

