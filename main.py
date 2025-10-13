# main.py
# Basit Telegram ↔ Groq köprüsü (FastAPI + Webhook)
# Ortam değişkenleri (Railway'de eklenecek):
# TELEGRAM_BOT_TOKEN, GROQ_API_KEY

import os, requests
from fastapi import FastAPI, Request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "msg": "Bot çalışıyor. /health de hazır."}

@app.get("/health")
def health():
    return {"status": "healthy"}

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
                        "Net, sıcak ve Türkçe yanıt ver. "
                        "Tıbbi tavsiye verme; genel bilgi ver ve "
                        "kişiye doktoruna danışmasını öner. "
                        "İndirim sorulursa liderlerden İNDİRİM LİNKİ "
                        "isteyebileceğini hatırlat."
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

def send_message(chat_id: int, text: str):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception:
        pass

@app.post("/webhook")
async def telegram_webhook(req: Request):
    update = await req.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text") or ""

    if not chat_id:
        return {"ok": True}

    # /start karşılama
    if isinstance(text, str) and text.strip().lower().startswith("/start"):
        send_message(chat_id, (
            "Merhaba! Ben yapay zekâ destekli asistanım. "
            "Sorularını yaz, hızlıca yanıtlayayım. "
            "Ürün konularında tıbbi tavsiye veremem; özel durumlar için "
            "doktoruna danışmalısın. Fiyat/indirim için bilgi isteyebilirsin; "
            "indirimli almak istersen liderlerimizden İNDİRİM LİNKİ al."
        ))
        return {"ok": True}

    if text:
        reply = ask_groq(text)
        send_message(chat_id, reply)

    return {"ok": True}
