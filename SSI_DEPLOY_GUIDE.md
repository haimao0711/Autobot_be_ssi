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
| 5 | Backup code cũ và gắn thẻ image `vps-backup` | 0.4 | 👤 | [ ] |
| 6 | Dừng giao dịch bot cũ, xác nhận không còn lệnh chờ | 1 | 👤 | [ ] |
| 7 | Clone code mới, chép lại `.env*`, `redis.conf` | 2 | 👤 | [ ] |
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
2. **Private key:** tạo thư mục và đặt file khóa:
   ```bash
   mkdir -p /var/www/AutoBotCk_Backend/secrets
   # chép file vào, ví dụ ssi_private.pem
   chmod 600 /var/www/AutoBotCk_Backend/secrets/ssi_private.pem
   echo "secrets/" >> .gitignore
   ```
   Trong container đường dẫn là `/app/secrets/ssi_private.pem` → đây là giá trị cho cột `ssi_private_key_path`.
3. **Backup DB:**
   ```bash
   docker exec autobotck_backend-stock-predict-postgres-1 pg_dump -U myuser stockdb > /root/stockdb_$(date +%F).sql
   ```
4. **Backup code + gắn thẻ image cũ để quay lại:**
   ```bash
   cp -r /var/www/AutoBotCk_Backend /var/www/AutoBotCk_Backend_vps_backup
   for s in stock-predict-api celery-worker celery-beat celery-flower; do
     docker tag autobotck_backend-$s:latest autobotck_backend-$s:vps-backup
   done
   ```

## 1. Dừng giao dịch trên bot cũ

- Trên giao diện bot: tắt giao dịch (hệ thống tự hủy lệnh chờ và dừng scheduler).
- Kiểm tra trên SmartOne không còn lệnh chờ khớp.

## 2. Cập nhật code (qua kho git riêng)

Trên máy bạn, trước khi push:
- `.gitignore` đã chặn `secrets/` và `*.pem`. Kiểm tra `git status` để chắc không có file khóa.
- `docker-compose.yml` đang chứa mật khẩu Postgres và `.env.production` có `SECRET_KEY`: nếu kho git là private thì chấp nhận được, nên đổi sang biến môi trường khi có thời gian.

Trên server:
```bash
cd /var/www
mv AutoBotCk_Backend AutoBotCk_Backend_old   # giữ làm bản quay lại (đã backup ở bước 0)
git clone <URL kho git riêng> AutoBotCk_Backend
cd AutoBotCk_Backend
# chép lại các file riêng của server từ bản cũ
cp ../AutoBotCk_Backend_old/.env* . 2>/dev/null
cp ../AutoBotCk_Backend_old/redis.conf . 2>/dev/null
mkdir -p secrets   # rồi đặt ssi_private.pem vào (bước 0)
```
Dùng `diff` giữa `docker-compose.yml` cũ và mới trước khi `up`. Compose dùng network ngoài `autobotck_backend` và volume `postgres_data`/`redis_data` theo tên project, nên **phải giữ tên thư mục là `AutoBotCk_Backend`** để compose nhận đúng volume dữ liệu cũ.

## 3. Build và migrate

```bash
cd /var/www/AutoBotCk_Backend
docker compose build stock-predict-api celery-worker celery-beat celery-flower
# Chỉ thêm 6 cột vào bảng account; tài khoản cũ mặc định broker='smartone'
docker compose run --rm stock-predict-api python manage.py sqlmigrate account 0014
docker compose run --rm stock-predict-api python manage.py migrate account
```

> [!NOTE]
> Service `stock-predict-api` chạy `gunicorn` trực tiếp (không qua `entrypoint.sh`) nên **migrate không tự chạy**, phải chạy tay như trên.

## 4. Bật SSI cho tài khoản

```sql
UPDATE account SET broker='ssi', ssi_consumer_id='...', ssi_consumer_secret='...',
  ssi_private_key_path='/app/secrets/ssi_private.pem', ssi_two_fa_type=1, ssi_pin=NULL
WHERE account_num='<số TK SSI>';
```
(Tên bảng thật xem `common/table_names.py`, mục `ACCOUNT`.)

## 5. Khởi động lại

```bash
docker compose up -d stock-predict-api celery-worker celery-beat celery-flower
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
cd /var/www/AutoBotCk_Backend
docker compose down stock-predict-api celery-worker celery-beat celery-flower
for s in stock-predict-api celery-worker celery-beat celery-flower; do
  docker tag autobotck_backend-$s:vps-backup autobotck_backend-$s:latest
done
# khôi phục code từ AutoBotCk_Backend_vps_backup rồi:
docker compose up -d --no-build stock-predict-api celery-worker celery-beat celery-flower
```
Các cột mới trong DB vô hại với bản cũ, không cần xóa. Muốn tài khoản chạy lại SmartOne: `UPDATE account SET broker='smartone' WHERE ...;`

## Lưu ý

- Mật khẩu Postgres đang nằm thẳng trong `docker-compose.yml`; nên chuyển sang `.env` khi có thời gian.
- Token đặt lệnh của SSI được lưu trong Redis (`ssi-writer-token:*`) để api và worker dùng chung; hết hạn thì bot ngừng đặt lệnh cho đến khi đăng nhập lại.

