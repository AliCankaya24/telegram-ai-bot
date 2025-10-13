# main.py
# Telegram ↔ Groq destekli bot (komutlar + menü + loglama + KVKK onayı)
# Env: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, LOG_WEBHOOK_URL (Apps Script Web App URL'in)

import os, time, requests
from fastapi import FastAPI, Request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL", "")  # Google Apps Script Web App URL
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()

# -------- yardımcılar --------
def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
    except Exception:
        pass

def ask_groq(prompt: str) -> str:
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": (
                        "Profesyonel satış danışmanı gibi konuş. Net, sıcak ve ikna edici ol. "
                        "Tıbbi tavsiye verme; genel bilgi ver ve kullanıcıya 'doktorunuza "
                        "başvurabilirsiniz' de. İndirim/satın alma yönlendirmesinde "
                        "liderlerden İNDİRİM LİNKİ isteyebileceğini hatırlat. "
                        "Kısa, anlaşılır, Türkçe yanıt ver."
                    )},
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=30
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Şu an yanıt veremiyorum, lütfen tekrar dener misiniz?"

def main_menu_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "💰 Fiyat"}, {"text": "📦 Kargo"}],
            [{"text": "🔗 İndirim"}, {"text": "🆘 Destek"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def set_bot_commands():
    cmds = [
        {"command": "start", "description": "Başlat ve menüyü göster"},
        {"command": "menu", "description": "Menüyü tekrar göster"},
        {"command": "fiyat", "description": "Güncel fiyat bilgisi"},
        {"command": "kargo", "description": "Kargo & teslimat bilgisi"},
        {"command": "indirim", "description": "İndirim linki yönlendirmesi"},
        {"command": "destek", "description": "Canlı destek/iletişim"},
        {"command": "onay", "description": "Veri işleme onayı ver"},
        {"command": "sil", "description": "Kayıtları silme talebi (opt-out)"},
    ]
    try:
        requests.post(f"{TELEGRAM_API}/setMyCommands",
                      json={"commands": cmds}, timeout=20)
    except Exception:
        pass

def log_event(event_type: str, chat: dict, text: str = "", extra: dict | None = None):
    """Google Apps Script Web App'e log gönder."""
    if not LOG_WEBHOOK_URL:
        return
    payload = {
        "event_type": event_type,         # "message" | "command" | "consent" | "optout"
        "ts": int(time.time()),
        "chat_id": chat.get("id"),
        "username": chat.get("username"),
        "first_name": chat.get("first_name"),
        "last_name": chat.get("last_name"),
        "text": text,
    }
    if extra:
        payload.update(extra)
    try:
        requests.post(LOG_WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass

# -------- metinler --------
WELCOME = (
    "Merhaba! 👋 Aramıza hoş geldin.\n\n"
    "Ben yapay zekâ destekli asistanım. Aşağıdaki menüden hızlıca seçim yapabilir "
    "ya da bana doğrudan soru yazabilirsin.\n\n"
    "Önemli not: Ürünlerle ilgili genel bilgi verebilirim ancak *tıbbi tavsiye veremem*. "
    "Kişisel sağlık durumun için **doktorunuza başvurabilirsiniz**. "
    "İndirimli alış için liderlerimizden **İNDİRİM LİNKİ** isteyebilirsin."
)

KARGO_INFO = (
    "📦 *Kargo & Teslimat Bilgisi*\n"
    "• Siparişler genellikle **1–3 iş günü** içinde teslim edilir.\n"
    "• Kargonuz gelmediyse Bee’M International iletişim hattını arayabilirsiniz: "
    "**0 530 393 23 36**\n"
    "• Kargo takip numaranız varsa yazın, kontrol edelim."
)

# -------- health --------
@app.get("/")
def home():
    return {"ok": True, "msg": "Bot ayakta. /health de hazır."}

@app.get("/health")
def health():
    set_bot_commands()
    return {"status": "healthy"}

# -------- webhook --------
@app.post("/webhook")
async def telegram_webhook(req: Request):
    update = await req.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    low = text.lower()

    # /onay ve /sil (KVKK)
    if low.startswith("/onay"):
        send_message(chat_id, "Teşekkürler! ✔ Veri işleme onayınız alındı. "
                              "İstediğiniz zaman `/sil` komutu ile silme talebi oluşturabilirsiniz.")
        log_event("consent", chat, text="/onay", extra={"consent": True})
        return {"ok": True}

    if low.startswith("/sil"):
        send_message(chat_id, "Talebiniz alındı. Kayıtlarınızın silinmesi için işleme alıyoruz. "
                              "Size yalnızca zorunlu bilgilendirmeler yapılacaktır.")
        log_event("optout", chat, text="/sil", extra={"consent": False})
        return {"ok": True}

    # menü/karşılama
    if low.startswith("/start") or low.startswith("/menu") or low == "menu":
        send_message(chat_id, WELCOME, reply_markup=main_menu_keyboard())
        log_event("command", chat, text="/start")
        return {"ok": True}

    # hızlı komutlar
    if low.startswith("/fiyat") or low == "💰 fiyat":
        msg = ("💰 *Fiyat Bilgisi*\n"
               "Hangi ürün için fiyat öğrenmek istersiniz? Ürün adını yazın, en güncel bilgiyi ileteyim.\n\n"
               "Not: İndirimli almak isterseniz liderlerimizden **İNDİRİM LİNKİ** talep edebilirsiniz.")
        send_message(chat_id, msg); log_event("command", chat, text="/fiyat")
        return {"ok": True}

    if low.startswith("/kargo") or low == "📦 kargo":
        send_message(chat_id, KARGO_INFO); log_event("command", chat, text="/kargo")
        return {"ok": True}

    if low.startswith("/indirim") or low == "🔗 indirim":
        markup = {
            "inline_keyboard": [[
                {"text": "İndirim Linki Talep Et (Ali)", "url": "https://t.me/ali_cankaya"},
                {"text": "İndirim Linki Talep Et (Derya)", "url": "https://t.me/deryakaratasates"}
            ]]
        }
        send_message(chat_id, "İndirimli alış için liderlerimizle iletişime geçebilirsiniz:", reply_markup=markup)
        log_event("command", chat, text="/indirim")
        return {"ok": True}

    if low.startswith("/destek") or low == "🆘 destek":
        markup = {
            "inline_keyboard": [[
                {"text": "Ali Çankaya ile İletişim", "url": "https://t.me/ali_cankaya"},
                {"text": "Derya Karataş Ateş ile İletişim", "url": "https://t.me/deryakaratasates"}
            ]]
        }
        send_message(chat_id, "Canlı destek için aşağıdaki bağlantılardan bize ulaşabilirsiniz.", reply_markup=markup)
        log_event("command", chat, text="/destek")
        return {"ok": True}

    # akıllı sohbet
    if text:
        reply = ask_groq(text)
        send_message(chat_id, reply)
        log_event("message", chat, text=text)
    return {"ok": True}
