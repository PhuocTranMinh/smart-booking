# Chatbot memory module for Smart Room
# Lưu thông tin qua nhiều lượt chat và chỉ hỏi phần còn thiếu.

import re
from datetime import datetime, timedelta

SESSIONS = {}

class ChatSession:
    def __init__(self):
        self.data = {
            "intent": "BOOK",
            "room_type": None,
            "specific_room": None,
            "quantity": None,
            "date": None,
            "start_time": None,
            "end_time": None,
            "duration_hours": None,
            "confirmed": False,
        }

    def update(self, values):
        # Không xóa dữ liệu cũ; chỉ cập nhật giá trị mới.
        for key, value in values.items():
            if value not in (None, ""):
                self.data[key] = value

    def reset(self):
        self.__init__()

def get_session(session_id):
    if session_id not in SESSIONS:
        SESSIONS[session_id] = ChatSession()
    return SESSIONS[session_id]

def normalize(text):
    text = text.lower().strip()
    for a, b in {
        "điều hoà": "điều hòa",
        "dieu hoa": "điều hòa",
        "may lanh": "máy lạnh",
        "phong": "phòng",
        "gio": "giờ",
    }.items():
        text = text.replace(a, b)
    return text

ROOM_SYNONYMS = {
    "Phòng máy lạnh": ["máy lạnh", "điều hòa", "air conditioner",
                       "aircon", "air con", "phòng lạnh", "ac"],
    "Phòng quạt": ["phòng quạt", "phòng có quạt", "fan"],
    "Phòng học": ["phòng học", "classroom"],
}

def parse_room_type(text):
    t = normalize(text)
    for room_type, words in ROOM_SYNONYMS.items():
        if any(w in t for w in words):
            return room_type
    return None

def parse_specific_room(text):
    m = re.search(r"\b([A-Za-z]\d{2})\b", text)
    return m.group(1).upper() if m else None

NUMBERS = {
    "một":1, "mot":1, "hai":2, "ba":3, "bốn":4, "bon":4,
    "năm":5, "nam":5, "sáu":6, "sau":6, "bảy":7, "bay":7,
    "tám":8, "chín":9, "chin":9, "mười":10
}

def parse_quantity(text):
    t = normalize(text)
    m = re.search(r"\b(\d+)\s*(?:phòng|phong|cái)\b", t)
    if m:
        return int(m.group(1))
    for word, n in NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b\s*(?:phòng|phong|cái)", t):
            return n
    if re.fullmatch(r"\d+", t):
        return int(t)
    if t in NUMBERS:
        return NUMBERS[t]
    return None

def parse_date(text):
    t = normalize(text)
    today = datetime.now().date()
    if "hôm nay" in t:
        return today.isoformat()
    if "ngày mai" in t or re.search(r"\bmai\b", t):
        return (today + timedelta(days=1)).isoformat()
    if "ngày kia" in t:
        return (today + timedelta(days=2)).isoformat()

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b", t)
    if m:
        d, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if not m.group(3):
            try:
                if datetime(year, month, d).date() < today:
                    year += 1
            except ValueError:
                return None
        try:
            return datetime(year, month, d).date().isoformat()
        except ValueError:
            return None

    m = re.search(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    return None

def parse_one_time(value):
    value = value.strip().lower()
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", value)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{minute:02d}" if h <= 23 and minute <= 59 else None

    m = re.fullmatch(
        r"(\d{1,2})\s*(?:h|giờ)(?:\s*(\d{1,2}))?"
        r"(?:\s*(sáng|trưa|chiều|tối))?", value)
    if m:
        h, minute, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if period in ("chiều", "tối") and h < 12:
            h += 12
        if period == "trưa" and h < 11:
            h += 12
        if period == "sáng" and h == 12:
            h = 0
        return f"{h:02d}:{minute:02d}" if h <= 23 and minute <= 59 else None
    return None

def parse_times(text):
    t = normalize(text)
    token = r"(\d{1,2}(?:[:.]\d{2}|h\d{0,2}|h|\s*giờ(?:\s*\d{1,2})?)(?:\s*(?:sáng|trưa|chiều|tối))?)"
    m = re.search(token + r"\s*(?:đến|tới|-)\s*" + token, t)
    if m:
        return parse_one_time(m.group(1)), parse_one_time(m.group(2))

    m = re.search(r"(?:bắt đầu\s*lúc|lúc|từ)\s*" + token, t)
    if m:
        return parse_one_time(m.group(1)), None
    return None, None

def parse_duration(text):
    m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:tiếng|giờ)\b", normalize(text))
    return float(m.group(1).replace(",", ".")) if m else None

def extract_entities(text):
    result = {}
    room = parse_room_type(text)
    if room: result["room_type"] = room
    specific = parse_specific_room(text)
    if specific: result["specific_room"] = specific
    quantity = parse_quantity(text)
    if quantity is not None: result["quantity"] = quantity
    date = parse_date(text)
    if date: result["date"] = date
    start, end = parse_times(text)
    if start: result["start_time"] = start
    if end: result["end_time"] = end
    duration = parse_duration(text)
    if duration is not None: result["duration_hours"] = duration
    return result

def complete_time(data):
    if data.get("start_time") and data.get("duration_hours") and not data.get("end_time"):
        start = datetime.strptime(data["start_time"], "%H:%M")
        data["end_time"] = (start + timedelta(hours=data["duration_hours"])).strftime("%H:%M")

def chat(session_id, message):
    session = get_session(session_id)
    text = normalize(message)

    if any(x in text for x in ["đặt lại", "làm lại", "bắt đầu lại", "reset"]):
        session.reset()
        return {"complete": False, "data": session.data,
                "reply": "Được, mình đã tạo yêu cầu mới. Bạn muốn đặt loại phòng nào?"}

    # Ghi nhớ thông tin mới vào phiên hiện tại.
    session.update(extract_entities(text))
    data = session.data
    complete_time(data)

    # Chỉ hỏi trường đang thiếu.
    if not data["room_type"] and not data["specific_room"]:
        return {"complete": False, "data": data,
                "reply": "Bạn muốn đặt loại phòng nào? Ví dụ: phòng quạt, phòng máy lạnh hoặc phòng học."}
    if not data["quantity"]:
        return {"complete": False, "data": data,
                "reply": "Bạn muốn đặt bao nhiêu phòng?"}
    if not data["date"]:
        return {"complete": False, "data": data,
                "reply": "Bạn muốn đặt phòng vào ngày nào?"}
    if not data["start_time"]:
        return {"complete": False, "data": data,
                "reply": "Bạn muốn bắt đầu sử dụng lúc mấy giờ?"}
    if not data["end_time"]:
        return {"complete": False, "data": data,
                "reply": "Bạn muốn sử dụng đến mấy giờ?"}

    room = data["specific_room"] or data["room_type"]
    summary = f"{data['quantity']} {room}, ngày {data['date']}, từ {data['start_time']} đến {data['end_time']}"
    return {"complete": True, "data": data,
            "reply": f"Mình đã ghi nhớ đầy đủ: {summary}. Bạn có muốn xác nhận đặt phòng không?"}

if __name__ == "__main__":
    sid = "demo"
    while True:
        msg = input("Bạn: ")
        if msg.lower() == "exit":
            break
        print("Bot:", chat(sid, msg)["reply"])
        print("Memory:", result["data"])
