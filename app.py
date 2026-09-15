from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import json
import os
import re
import uuid
from zoneinfo import ZoneInfo

import qrcode
from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from chatbot_memory import chat as memory_chat
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, or_, and_
from sqlalchemy.orm import declarative_base, sessionmaker, Session

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smart_room.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

ROOM_SEED = [
    ("A01", "Phòng quạt", 10000),
    ("A02", "Phòng quạt", 10000),
    ("B01", "Phòng máy lạnh", 20000),
    ("B02", "Phòng máy lạnh", 20000),
    ("C01", "Phòng học", 15000),
]

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "demo-esp32-key")


class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    room_type = Column(String, nullable=False)
    price_per_hour = Column(Float, nullable=False)
    status = Column(String, default="AVAILABLE", nullable=False)


class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True)
    booking_code = Column(String, unique=True, nullable=False, index=True)
    student_id = Column(String, nullable=True)
    room_type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    allocated_rooms = Column(String, nullable=False, default="[]")
    booking_date = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    total_price = Column(Float, nullable=False)
    status = Column(String, default="CONFIRMED", nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


Base.metadata.create_all(bind=engine)


def seed_rooms():
    db = SessionLocal()
    try:
        if db.query(Room).count() == 0:
            db.add_all([Room(name=n, room_type=t, price_per_hour=p) for n, t, p in ROOM_SEED])
            db.commit()
    finally:
        db.close()


seed_rooms()

app = FastAPI(title="Smart Room Booking API", version="3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class BookingCreate(BaseModel):
    room_type: str
    quantity: int = Field(ge=1, le=10)
    booking_date: str
    start_time: str
    end_time: str
    student_id: str | None = None


class BookingOut(BaseModel):
    booking_code: str
    student_id: str | None
    room_type: str
    quantity: int
    assigned_rooms: list[str]
    booking_date: str
    start_time: str
    end_time: str
    total_price: float
    status: str


class ChatRequest(BaseModel):
    message: str
    student_id: str | None = None
    session_id: str | None = None


class DeviceVerifyRequest(BaseModel):
    booking_code: str
    room_name: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hours_between(start: str, end: str) -> float:
    try:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
    except Exception:
        raise HTTPException(400, "Thời gian không hợp lệ.")
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes <= 0:
        raise HTTPException(400, "Giờ kết thúc phải lớn hơn giờ bắt đầu.")
    return minutes / 60


def find_price(db: Session, room_type: str) -> float:
    room = db.query(Room).filter(Room.room_type == room_type).first()
    if not room:
        raise HTTPException(400, "Loại phòng không tồn tại.")
    return room.price_per_hour


def parse_time_minutes(value: str) -> int:
    h, m = map(int, value.split(":"))
    return h * 60 + m


def overlapping_bookings(db: Session, room_type: str, booking_date: str, start_time: str, end_time: str):
    # Two intervals overlap when existing.start < new.end AND existing.end > new.start.
    rows = db.query(Booking).filter(
        Booking.room_type == room_type,
        Booking.booking_date == booking_date,
        Booking.status == "CONFIRMED",
    ).all()
    start = parse_time_minutes(start_time)
    end = parse_time_minutes(end_time)
    return [
        b for b in rows
        if parse_time_minutes(b.start_time) < end and parse_time_minutes(b.end_time) > start
    ]


def free_rooms(db: Session, room_type: str, booking_date: str, start_time: str, end_time: str):
    rooms = db.query(Room).filter(Room.room_type == room_type, Room.status == "AVAILABLE").order_by(Room.name).all()
    used = set()
    for b in overlapping_bookings(db, room_type, booking_date, start_time, end_time):
        used.update(json.loads(b.allocated_rooms or "[]"))
    return [r for r in rooms if r.name not in used]


def parse_chat_message(text: str):
    low = text.lower().strip()
    if "máy lạnh" in low or "may lanh" in low or "phòng lạnh" in low or "phong lanh" in low:
        room_type = "Phòng máy lạnh"
    elif "phòng học" in low or "phong hoc" in low:
        room_type = "Phòng học"
    elif "quạt" in low or "quat" in low:
        room_type = "Phòng quạt"
    else:
        room_type = None

    qty = None
    m = re.search(r"(\d+)\s*(?:phòng|phong)", low)
    if m:
        qty = int(m.group(1))
    elif re.search(r"\b(hai|2)\b", low):
        qty = 2
    elif re.search(r"\b(một|mot|1)\b", low):
        qty = 1

    matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(?:h|giờ|gio)?(?!\d)", low))
    times = []
    for x in matches:
        h = int(x.group(1))
        minute = int(x.group(2) or 0)
        if 0 <= h <= 23 and 0 <= minute <= 59:
            times.append(f"{h:02d}:{minute:02d}")
    start_time = times[0] if times else None
    end_time = times[1] if len(times) >= 2 else None

    if start_time and not end_time:
        duration = re.search(r"(\d+(?:\.\d+)?)\s*(?:tiếng|tieng|giờ|gio)", low)
        if duration:
            end_minutes = parse_time_minutes(start_time) + int(float(duration.group(1)) * 60)
            if end_minutes < 24 * 60:
                end_time = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"

    date_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", low)
    booking_date = None
    if date_match:
        y, mo, d = map(int, date_match.groups())
        booking_date = f"{y:04d}-{mo:02d}-{d:02d}"

    return {
        "room_type": room_type,
        "quantity": qty,
        "booking_date": booking_date,
        "start_time": start_time,
        "end_time": end_time,
    }


def make_qr_payload(booking: Booking):
    return {"booking_id": booking.booking_code, "version": 3}


def booking_dict(b: Booking):
    return {
        "booking_code": b.booking_code,
        "student_id": b.student_id,
        "room_type": b.room_type,
        "quantity": b.quantity,
        "assigned_rooms": json.loads(b.allocated_rooms or "[]"),
        "booking_date": b.booking_date,
        "start_time": b.start_time,
        "end_time": b.end_time,
        "total_price": b.total_price,
        "status": b.status,
    }


def require_device_key(x_device_key: str | None):
    if not x_device_key or x_device_key != DEVICE_API_KEY:
        raise HTTPException(401, "Thiết bị chưa được xác thực.")


def booking_time_status(b: Booking):
    now = datetime.now(VN_TZ)
    if b.status != "CONFIRMED":
        return "CANCELLED"
    if b.booking_date < now.strftime("%Y-%m-%d"):
        return "EXPIRED"
    if b.booking_date > now.strftime("%Y-%m-%d"):
        return "UPCOMING"
    current = now.hour * 60 + now.minute
    start = parse_time_minutes(b.start_time)
    end = parse_time_minutes(b.end_time)
    if current < start:
        return "UPCOMING"
    if current >= end:
        return "EXPIRED"
    return "ACTIVE"


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "service": "smart-room-api", "version": app.version}


@app.get("/api/rooms")
def get_rooms(db: Session = Depends(get_db)):
    return [
        {"id": r.id, "name": r.name, "room_type": r.room_type, "price_per_hour": r.price_per_hour, "status": r.status}
        for r in db.query(Room).all()
    ]


@app.get("/api/availability")
def availability(
    room_type: str,
    quantity: int = Query(1, ge=1),
    booking_date: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    db: Session = Depends(get_db),
):
    if booking_date and start_time and end_time:
        available = len(free_rooms(db, room_type, booking_date, start_time, end_time))
    else:
        available = db.query(Room).filter(Room.room_type == room_type, Room.status == "AVAILABLE").count()
    return {"room_type": room_type, "requested": quantity, "available": available, "can_book": available >= quantity}


@app.post("/api/bookings", response_model=BookingOut)
def create_booking(data: BookingCreate, db: Session = Depends(get_db)):
    try:
        datetime.strptime(data.booking_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Ngày phải có dạng YYYY-MM-DD.")

    duration = hours_between(data.start_time, data.end_time)
    price = find_price(db, data.room_type)
    available_rooms = free_rooms(db, data.room_type, data.booking_date, data.start_time, data.end_time)
    if len(available_rooms) < data.quantity:
        raise HTTPException(409, f"Không đủ phòng trong khung giờ này. Còn {len(available_rooms)} phòng trống.")

    selected_rooms = [r.name for r in available_rooms[:data.quantity]]
    code = "BK" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4].upper()
    booking = Booking(
        booking_code=code,
        student_id=data.student_id,
        room_type=data.room_type,
        quantity=data.quantity,
        allocated_rooms=json.dumps(selected_rooms),
        booking_date=data.booking_date,
        start_time=data.start_time,
        end_time=data.end_time,
        total_price=price * data.quantity * duration,
        status="CONFIRMED",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking_dict(booking)


@app.get("/api/bookings/{booking_code}", response_model=BookingOut)
def get_booking(booking_code: str, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.booking_code == booking_code).first()
    if not booking:
        raise HTTPException(404, "Không tìm thấy booking.")
    return booking_dict(booking)


@app.get("/api/bookings")
def list_bookings(db: Session = Depends(get_db)):
    bookings = db.query(Booking).order_by(Booking.id.desc()).all()
    return [booking_dict(b) for b in bookings]


@app.get("/api/qr/{booking_code}")
def booking_qr(booking_code: str, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.booking_code == booking_code).first()
    if not booking:
        raise HTTPException(404, "Booking không tồn tại.")
    payload = json.dumps(make_qr_payload(booking), separators=(",", ":"))
    img = qrcode.make(payload)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/device/booking/{booking_code}")
def device_get_booking(
    booking_code: str,
    x_device_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_device_key(x_device_key)
    booking = db.query(Booking).filter(Booking.booking_code == booking_code).first()
    if not booking:
        raise HTTPException(404, "Booking không tồn tại.")
    result = booking_dict(booking)
    result["time_status"] = booking_time_status(booking)
    result["qr_payload"] = make_qr_payload(booking)
    return result


@app.post("/api/device/verify")
def device_verify(
    data: DeviceVerifyRequest,
    x_device_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_device_key(x_device_key)
    booking = db.query(Booking).filter(Booking.booking_code == data.booking_code).first()
    if not booking:
        return {"allowed": False, "reason": "BOOKING_NOT_FOUND", "message": "Không tìm thấy booking."}

    time_status = booking_time_status(booking)
    assigned = json.loads(booking.allocated_rooms or "[]")
    room_ok = data.room_name in assigned

    if booking.status != "CONFIRMED":
        return {"allowed": False, "reason": "BOOKING_CANCELLED", "message": "Booking đã bị hủy.", "booking": booking_dict(booking)}
    if not room_ok:
        return {"allowed": False, "reason": "WRONG_ROOM", "message": "QR không thuộc phòng hiện tại.", "booking": booking_dict(booking), "time_status": time_status}
    if time_status != "ACTIVE":
        return {"allowed": False, "reason": time_status, "message": "Booking chưa đến giờ hoặc đã hết giờ.", "booking": booking_dict(booking), "time_status": time_status}

    return {"allowed": True, "reason": "OK", "message": "Cho phép sử dụng phòng.", "booking": booking_dict(booking), "time_status": time_status}


@app.post("/api/chat")
def chat_endpoint(data: ChatRequest, db: Session = Depends(get_db)):
    """Chatbot nhiều lượt: giữ lại thông tin đã nói trong cùng session_id."""
    session_id = data.session_id or data.student_id or "guest"
    result = memory_chat(session_id, data.message)
    state = result["data"]

    # Nếu người dùng chỉ nói mã phòng cụ thể (A01/B01/C01...), suy ra loại phòng.
    room_type = state.get("room_type")
    specific = state.get("specific_room")
    if specific:
        prefix = specific[0].upper()
        mapped = {"A": "Phòng quạt", "B": "Phòng máy lạnh", "C": "Phòng học"}
        room_type = mapped.get(prefix, room_type)

    parsed = {
        "room_type": room_type,
        "specific_room": specific,
        "quantity": state.get("quantity"),
        "booking_date": state.get("date"),
        "start_time": state.get("start_time"),
        "end_time": state.get("end_time"),
    }

    # Chưa đủ dữ liệu -> chỉ hỏi phần còn thiếu, không mất dữ liệu cũ.
    if not result["complete"]:
        return {"ok": False, "complete": False, "session_id": session_id,
                "parsed": parsed, "memory": state, "reply": result["reply"]}

    # Kiểm tra loại phòng hợp lệ trước khi báo giá.
    if not room_type:
        return {"ok": False, "complete": False, "session_id": session_id,
                "parsed": parsed, "memory": state,
                "reply": "Mình chưa xác định được loại phòng. Bạn nói rõ phòng quạt, phòng máy lạnh hoặc phòng học nhé."}

    try:
        duration = hours_between(parsed["start_time"], parsed["end_time"])
        price = find_price(db, room_type)
    except HTTPException as exc:
        return {"ok": False, "complete": False, "session_id": session_id,
                "parsed": parsed, "memory": state, "reply": str(exc.detail)}

    quantity = int(parsed["quantity"])
    available_rooms = free_rooms(
        db, room_type, parsed["booking_date"],
        parsed["start_time"], parsed["end_time"]
    )

    if specific and specific not in [r.name for r in db.query(Room).filter(Room.room_type == room_type).all()]:
        return {"ok": False, "complete": False, "session_id": session_id,
                "parsed": parsed, "memory": state,
                "reply": f"Không tìm thấy phòng {specific}."}

    if specific and specific in [r.name for r in available_rooms]:
        selected_rooms = [specific]
        if quantity > 1:
            selected_rooms += [r.name for r in available_rooms if r.name != specific][:quantity - 1]
    else:
        selected_rooms = [r.name for r in available_rooms[:quantity]]

    if len(selected_rooms) < quantity:
        return {"ok": False, "complete": False, "session_id": session_id,
                "parsed": parsed, "memory": state,
                "reply": f"Khung giờ này chỉ còn {len(available_rooms)} phòng {room_type}, không đủ {quantity} phòng."}

    total = price * quantity * duration
    return {
        "ok": True,
        "complete": True,
        "session_id": session_id,
        "parsed": parsed,
        "memory": state,
        "price_per_hour": price,
        "duration": duration,
        "total_price": total,
        "available": len(available_rooms),
        "suggested_rooms": selected_rooms,
        "reply": (
            f"Mình đã nhớ toàn bộ yêu cầu: {quantity} {room_type}, "
            f"ngày {parsed['booking_date']}, {parsed['start_time']} - {parsed['end_time']}. "
            f"Tổng tiền dự kiến {total:,.0f}đ. Bạn có muốn xác nhận đặt phòng không?"
        ),
    }

@app.post("/api/bookings/{booking_code}/cancel")
def cancel_booking(booking_code: str, db: Session = Depends(get_db)):
    booking = db.query(Booking).filter(Booking.booking_code == booking_code).first()
    if not booking:
        raise HTTPException(404, "Booking không tồn tại.")
    booking.status = "CANCELLED"
    db.commit()
    return {"ok": True, "booking_code": booking_code, "status": booking.status}
