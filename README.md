# Smart Room Booking V3 — Render + PostgreSQL + QR + ESP32

Phiên bản này nâng cấp V2 để có thể deploy trực tiếp trên Render:

- FastAPI backend + web frontend.
- PostgreSQL trên Render; local vẫn có thể chạy bằng SQLite nếu không đặt `DATABASE_URL`.
- QR tạo bằng server-side Python, không phụ thuộc CDN.
- Booking tự cấp phòng cụ thể (`A01`, `A02`, ...), có kiểm tra trùng khung giờ.
- API riêng cho ESP32: đọc booking và xác thực QR theo phòng hiện tại.
- `DEVICE_API_KEY` dùng để bảo vệ các endpoint dành cho thiết bị.
- Có `render.yaml` để tạo Web Service + Render Postgres.

## 1. Chạy local

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Mở: `http://127.0.0.1:8000`

Health check: `http://127.0.0.1:8000/health`

## 2. Deploy Render bằng Blueprint

1. Đưa toàn bộ thư mục này lên GitHub.
2. Trên Render chọn **New → Blueprint** và chọn repository.
3. Render đọc `render.yaml` và tạo:
   - Web Service `smart-room-api`
   - PostgreSQL `smart-room-db`
4. Sau khi deploy, mở URL `https://...onrender.com`.
5. Vào Render Dashboard → Web Service → Environment để xem `DEVICE_API_KEY` được tạo tự động. Không đưa key này vào QR hoặc frontend.

`DATABASE_URL` được nối tự động tới PostgreSQL bằng `fromDatabase`.

## 3. API chính

### Public

- `GET /api/rooms`
- `GET /api/availability?room_type=Phòng%20quạt&quantity=1&booking_date=2026-09-20&start_time=09:00&end_time=11:00`
- `POST /api/bookings`
- `GET /api/bookings/{booking_code}`
- `GET /api/bookings`
- `GET /api/qr/{booking_code}`
- `POST /api/chat`
- `POST /api/bookings/{booking_code}/cancel`

### ESP32

ESP32 gửi header:

```text
X-Device-Key: <DEVICE_API_KEY>
```

Lấy đầy đủ booking:

```http
GET /api/device/booking/BK20260914083012ABCD
X-Device-Key: <DEVICE_API_KEY>
```

Kiểm tra QR có được phép dùng ở phòng hiện tại:

```http
POST /api/device/verify
Content-Type: application/json
X-Device-Key: <DEVICE_API_KEY>

{
  "booking_code": "BK20260914083012ABCD",
  "room_name": "A01"
}
```

Kết quả ví dụ:

```json
{
  "allowed": true,
  "reason": "OK",
  "message": "Cho phép sử dụng phòng.",
  "time_status": "ACTIVE"
}
```

Nếu QR thuộc phòng khác:

```json
{
  "allowed": false,
  "reason": "WRONG_ROOM"
}
```

Nếu chưa tới giờ:

```json
{
  "allowed": false,
  "reason": "UPCOMING"
}
```

Nếu đã hết giờ:

```json
{
  "allowed": false,
  "reason": "EXPIRED"
}
```

## 4. Luồng hoạt động với ESP32

```text
Web / Chatbot
     ↓
POST /api/bookings
     ↓
PostgreSQL
     ↓
Booking code + assigned room
     ↓
QR { booking_id, version }
     ↓
ESP32 QR scanner
     ↓
POST /api/device/verify
     ↓
room_name hiện tại + thời gian
     ↓
allowed=true  → mở/cho phép sử dụng
allowed=false → buzzer / báo sai phòng / chưa tới giờ
```

## Lưu ý Free Render

Render hiện cho phép Free Web Service và Free Postgres để test/hobby. Free Postgres có giới hạn 1 GB và hết hạn sau 30 ngày; vì vậy bản này phù hợp để demo đồ án, không nên coi là database production lâu dài.

## V4 - Chatbot memory
- `chatbot_memory.py` lưu trạng thái theo `session_id`.
- Frontend lưu `session_id` bằng `localStorage`.
- `/api/chat` cập nhật thông tin mới nhưng không xóa thông tin cũ.
- Có thể nhập từng phần, ví dụ:
  1. `Cho mình 2 phòng máy lạnh`
  2. `ngày mai`
  3. `từ 9h đến 11h`
- Khi đủ thông tin, backend kiểm tra phòng trống, tính tiền và cho phép xác nhận.
- Nếu nhập `đặt lại` / `làm lại` / `reset`, chatbot bắt đầu yêu cầu mới.

Lưu ý: phiên chat hiện được lưu trong RAM của process Python. Khi Render restart/redeploy, memory chat sẽ mất. Booking vẫn nằm trong PostgreSQL.
