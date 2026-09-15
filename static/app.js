let selectedType = "Phòng quạt";
let selectedPrice = 10000;
const $ = id => document.getElementById(id);
const today = new Date().toISOString().slice(0, 10);
$("date").value = today;

document.querySelectorAll(".tab").forEach(btn => btn.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
  btn.classList.add("active");
  $(btn.dataset.tab).classList.add("active");
  if (btn.dataset.tab === "history") loadHistory();
});

document.querySelectorAll(".room-option").forEach(btn => btn.onclick = () => {
  document.querySelectorAll(".room-option").forEach(x => x.classList.remove("selected"));
  btn.classList.add("selected");
  selectedType = btn.dataset.type;
  selectedPrice = Number(btn.dataset.price);
  updatePrice();
  updateAvailability();
});

function hours() {
  const [sh, sm] = $("start").value.split(":").map(Number);
  const [eh, em] = $("end").value.split(":").map(Number);
  return ((eh * 60 + em) - (sh * 60 + sm)) / 60;
}

function updatePrice() {
  const h = hours();
  const total = h > 0 ? selectedPrice * Number($("quantity").value) * h : 0;
  $("optionPrice").textContent = total.toLocaleString("vi-VN") + "đ";
}

async function updateAvailability() {
  try {
    const q = Number($("quantity").value);
    const res = await fetch(`/api/availability?room_type=${encodeURIComponent(selectedType)}&quantity=${q}&booking_date=${encodeURIComponent($("date").value)}&start_time=${encodeURIComponent($("start").value)}&end_time=${encodeURIComponent($("end").value)}`);
    const data = await res.json();
    $("availability").textContent = `Còn ${data.available} phòng · ${data.can_book ? "Có thể đặt" : "Không đủ phòng"}`;
    $("availability").className = "availability-box " + (data.can_book ? "ok" : "bad");
  } catch {
    $("availability").textContent = "Không kết nối được server";
    $("availability").className = "availability-box bad";
  }
}

["quantity", "start", "end", "date"].forEach(id => $(id).addEventListener("input", () => {
  updatePrice();
  if (id === "quantity") updateAvailability();
}));

async function createBooking(data) {
  $("optionError").textContent = "";
  try {
    const res = await fetch("/api/bookings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(data)
    });
    const result = await res.json();
    if (!res.ok) {
      $("optionError").textContent = result.detail || "Không thể tạo booking.";
      return null;
    }
    showResult(result);
    return result;
  } catch {
    $("optionError").textContent = "Không kết nối được Backend.";
    return null;
  }
}

function showResult(b) {
  document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
  document.querySelector(".tabs").style.display = "none";
  $("result").classList.remove("hidden");

  $("bookingCode").textContent = b.booking_code;
  $("rType").textContent = b.room_type;
  $("rQty").textContent = b.quantity + " phòng";
  $("rTime").textContent = `${b.booking_date} | ${b.start_time} - ${b.end_time}`;
  $("rPrice").textContent = Number(b.total_price).toLocaleString("vi-VN") + "đ";
  $("rRooms").textContent = (b.assigned_rooms || []).join(", ");

  $("qrImage").src = `/api/qr/${encodeURIComponent(b.booking_code)}?t=${Date.now()}`;
}

$("bookOptions").onclick = () => createBooking({
  room_type: selectedType,
  quantity: Number($("quantity").value),
  booking_date: $("date").value,
  start_time: $("start").value,
  end_time: $("end").value,
  student_id: $("studentId").value.trim() || null
});

function addMessage(text, type) {
  $("messages").insertAdjacentHTML("beforeend", `<div class="msg ${type}">${text}</div>`);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function getChatSessionId() {
  let sid = localStorage.getItem("smart_room_chat_session");
  if (!sid) {
    sid = (crypto.randomUUID ? crypto.randomUUID() : "session-" + Date.now());
    localStorage.setItem("smart_room_chat_session", sid);
  }
  return sid;
}

async function sendChat() {
  const input = $("chatInput");
  const text = input.value.trim();
  if (!text) return;

  addMessage(text, "user");
  input.value = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        message: text,
        student_id: $("studentId").value.trim() || null,
        session_id: getChatSessionId()
      })
    });
    const data = await res.json();

    if (!data.ok) {
      addMessage(data.reply, "bot");
      return;
    }

    const p = data.parsed;
    const safe = JSON.stringify({
      room_type: p.room_type,
      quantity: p.quantity,
      booking_date: p.booking_date,
      start_time: p.start_time,
      end_time: p.end_time,
      student_id: $("studentId").value.trim() || null
    }).replace(/'/g, "&#39;");

    addMessage(
      `${data.reply}<br><button class="confirm-chat" data-booking='${safe}'>Xác nhận đặt phòng</button>`,
      "bot"
    );

    document.querySelectorAll(".confirm-chat").forEach(btn => {
      btn.onclick = async () => {
        const booking = JSON.parse(btn.dataset.booking);
        await createBooking(booking);
      };
    });
  } catch {
    addMessage("Không kết nối được Backend.", "bot");
  }
}

$("send").onclick = sendChat;
$("chatInput").onkeydown = e => { if (e.key === "Enter") sendChat(); };

document.querySelectorAll(".examples button").forEach(b => {
  b.onclick = () => {
    $("chatInput").value = b.textContent;
    sendChat();
  };
});

async function loadHistory() {
  const box = $("historyList");
  box.textContent = "Đang tải...";
  try {
    const res = await fetch("/api/bookings");
    const rows = await res.json();
    if (!rows.length) {
      box.innerHTML = "<div class='empty'>Chưa có booking.</div>";
      return;
    }
    box.innerHTML = rows.map(b => `
      <div class="history-item">
        <div><strong>${b.booking_code}</strong><small>${b.booking_date} · ${b.start_time}-${b.end_time}</small></div>
        <div><strong>${b.quantity} · ${b.room_type}</strong><small>${Number(b.total_price).toLocaleString("vi-VN")}đ · ${b.status}</small></div>
      </div>
    `).join("");
  } catch {
    box.textContent = "Không tải được dữ liệu.";
  }
}

$("refreshHistory").onclick = loadHistory;
$("newBooking").onclick = () => location.reload();

updatePrice();
updateAvailability();
