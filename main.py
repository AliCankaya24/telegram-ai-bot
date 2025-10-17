# main.py — Bee’M AI Asistan (JSON içerik + Excel fiyat) — FINAL
# - /health: JSON katalog durumu
# - /telegram: Telegram webhook (python-telegram-bot v20, async)
# - /icerik <ürün|alias|ihtiyaç>: JSON katalogtan ürün kartı (alias öncelikli)
# - /fiyat <ürün>: Excel PRICE_SHEET_URL'den fiyat (esnek başlık/fiyat okuma)
# - Tüm ürün/fiyat cevaplarında Ali & Derya butonları
# - Kişisel veri/log tutulmaz

import os, re, io, json, asyncio, urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# Telegram (python-telegram-bot v20)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)

# Excel fiyat için
import pandas as pd

# ================== YARDIMCI ARAÇLAR ==================

def _get_env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default

def _norm(s: str) -> str:
    if not s: return ""
    s = s.lower().strip()
    repl = {
        "ı":"i","İ":"i","ş":"s","Ş":"s","ğ":"g","Ğ":"g",
        "ç":"c","Ç":"c","ö":"o","Ö":"o","ü":"u","Ü":"u",
        "’":"'", "”":'"', "“":'"'
    }
    for a,b in repl.items():
        s = s.replace(a,b)
    return " ".join(s.split())

def human_now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")

def _get_effective_text(update: Update) -> str:
    m = update.effective_message
    if not m:
        return ""
    return (m.text or m.caption or "").strip()

# ================== JSON KATALOG OVERRIDE ==================

CATALOG_SOURCE_OVERRIDE: Optional[str] = None
CATALOG_DATA_OVERRIDE: Dict[str, Any] = {}
ALIAS_INDEX: List[tuple] = []  # (alias_norm, product_dict)

def _fetch_json(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)

def try_load_json_catalog_override() -> None:
    """
    ENV JSON istiyorsa kataloğu RAW URL'den yükler ve alias indeksini (ürün adı + aliaslar) kurar.
    """
    global CATALOG_SOURCE_OVERRIDE, CATALOG_DATA_OVERRIDE, ALIAS_INDEX
    source = _get_env("PRODUCTS_SOURCE", "CATALOG_SOURCE").upper()
    if source != "JSON":
        return
    url = _get_env("PRODUCTS_JSON_URL", "CATALOG_JSON_URL", "CATALOG_URL")
    if not url:
        return
    try:
        data = _fetch_json(url)
        products = data.get("products", [])
        if isinstance(products, list) and products:
            CATALOG_SOURCE_OVERRIDE = "JSON"
            CATALOG_DATA_OVERRIDE = data
            # Alias indeksi
            ALIAS_INDEX = []
            for p in products:
                name = (p.get("product_name") or "").strip()
                aliases = p.get("aliases", []) or []
                for a in set(aliases + ([name] if name else [])):
                    ALIAS_INDEX.append((_norm(a), p))
            ALIAS_INDEX.sort(key=lambda x: len(x[0]), reverse=True)
            print(f"[catalog] JSON loaded: {len(products)} products.")
        else:
            print("[catalog] JSON reached but empty products.")
    except Exception as e:
        print(f"[catalog] JSON load failed: {e}")

def get_catalog_size_override() -> int:
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        prods = CATALOG_DATA_OVERRIDE.get("products", [])
        return len(prods) if isinstance(prods, list) else 0
    return 0

def health_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """JSON override aktifse /health yanıtını düzeltir."""
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        payload = dict(payload or {})
        payload["source"] = "JSON"
        payload["catalog_size"] = get_catalog_size_override()
        payload["updated"] = CATALOG_DATA_OVERRIDE.get("metadata", {}).get(
            "updated", payload.get("updated") or "JSON yüklendi"
        )
    return payload

def find_product_by_query(text: str) -> Optional[Dict[str, Any]]:
    """Kullanıcı metninden ürün eşleştir (alias + ürün adı)."""
    q = _norm(text)
    if not q:
        return None
    for alias_norm, prod in ALIAS_INDEX:
        if alias_norm and alias_norm in q:
            return prod
    return None

# Uygulama yüklenirken JSON’u dener
try_load_json_catalog_override()

# ================== EXCEL FİYAT KAYNAĞI (ESNEK OKUMA) ==================

PRICE_SHEET_URL = _get_env("PRICE_SHEET_URL")  # GitHub RAW .xlsx olabilir
_price_cache: Dict[str, float] = {}
_price_cache_updated: str = "Henüz yüklenmedi"

def load_prices_from_excel(force: bool = False) -> None:
    """Excel URL'den fiyatları yükle. Başlık yoksa da (Ad/Fiyat) akıllı algıla ve metinden sayıya çevir."""
    global _price_cache, _price_cache_updated
    if not PRICE_SHEET_URL:
        _price_cache = {}
        _price_cache_updated = "Kaynak yok"
        return
    if _price_cache and not force:
        return

    try:
        data = urllib.request.urlopen(PRICE_SHEET_URL, timeout=30).read()

        # 1) Normal okuma (başlık var varsay)
        df = pd.read_excel(io.BytesIO(data))
        name_col = None
        price_col = None

        # Kolon adlarını normalize ederek bul
        for c in df.columns:
            cn = _norm(str(c))
            if name_col is None and ("ad" in cn or "urun" in cn):
                name_col = c
            if price_col is None and ("fiyat" in cn or "price" in cn):
                price_col = c

        # 2) Başlık bulunamadıysa: header=None ile tekrar oku ve ilk satırı başlık kabul et
        if name_col is None or price_col is None:
            df2 = pd.read_excel(io.BytesIO(data), header=None)
            header_row = df2.iloc[0].astype(str).fillna("").tolist()
            cand_name_idx, cand_price_idx = None, None
            for idx, val in enumerate(header_row):
                v = _norm(val)
                if cand_name_idx is None and ("ad" in v or "urun" in v):
                    cand_name_idx = idx
                if cand_price_idx is None and ("fiyat" in v or "price" in v):
                    cand_price_idx = idx
            if cand_name_idx is None: cand_name_idx = 0
            if cand_price_idx is None: cand_price_idx = 1
            df = df2.iloc[1:, [cand_name_idx, cand_price_idx]].copy()
            df.columns = ["Ad", "Fiyat"]
            name_col, price_col = "Ad", "Fiyat"

        # 3) Temizle ve cache’e yaz
        cache = {}
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            if not name or name.lower() == "nan":
                continue
            # fiyatı sayıya çevir (metinden arındır)
            raw = str(row[price_col]).strip()
            raw = raw.replace("TL", "").replace("₺", "").replace(" ", "")
            # 1.000,50 / 1,000.50 gibi durumlara karşı:
            raw = raw.replace(".", "").replace(",", ".")
            try:
                price_val = float(raw)
            except Exception:
                try:
                    price_val = float(row[price_col])
                except Exception:
                    continue
            cache[_norm(name)] = price_val

        _price_cache = cache
        _price_cache_updated = human_now()
        print(f"[prices] Loaded {len(_price_cache)} items (flex)")
    except Exception as e:
        print(f"[prices] load failed: {e}")
        _price_cache = {}
        _price_cache_updated = "Yüklenemedi"

def find_price(name_query: str) -> Optional[float]:
    """Ad benzerliğine göre fiyat bulur."""
    if not _price_cache:
        load_prices_from_excel()
    if not _price_cache:
        return None

    q = _norm(name_query)

    # 1) Doğrudan
    if q in _price_cache:
        return _price_cache[q]

    # 2) İçerme
    for k, v in _price_cache.items():
        if k in q or q in k:
            return v

    # 3) Basit yakın eşleşme (kelime kesişim oranı)
    best_val = None
    best_score = 0.0
    qset = set(q.split())
    for k, v in _price_cache.items():
        kset = set(k.split())
        inter = len(qset & kset)
        score = inter / max(1, len(kset))
        if score > best_score:
            best_score = score
            best_val = v
    return best_val

# ================== TELEGRAM BİLEŞENLERİ ==================

BOT_TOKEN = _get_env("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env eksik!")

ALI_TELE = _get_env("ALI_TELEGRAM", default="@ali_cankaya").lstrip("@")
DERYA_TELE = _get_env("DERYA_TELEGRAM", default="@deryakaratasates").lstrip("@")

def build_leader_buttons() -> InlineKeyboardMarkup:
    buttons = [[
        InlineKeyboardButton("Ali Çankaya", url=f"https://t.me/{ALI_TELE}"),
        InlineKeyboardButton("Derya Karataş Ateş", url=f"https://t.me/{DERYA_TELE}")
    ]]
    return InlineKeyboardMarkup(buttons)

def product_card_text(p: Dict[str, Any]) -> str:
    """Ürün kartı metni (Stil C kapanış dahil)."""
    name = p.get("product_name","BEE’M Ürünü")
    desc = p.get("description","")
    ingredients = p.get("ingredients",[])
    usage = p.get("usage","")
    med = p.get("medical_note","")

    parts = [f"✨ <b>{name}</b>"]
    if desc: parts.append(desc)
    if ingredients:
        parts.append("\n<b>Öne Çıkan İçerikler:</b>")
        for it in ingredients[:10]:
            parts.append(f"• {it}")
    if usage: parts.append(f"\n<b>Kullanım:</b> {usage}")
    if med: parts.append(f"\n<i>{med}</i>")
    # Satış kapanışı (C)
    parts.append("\n<i>Daha net sonuç için düzenli kullanım önerilir. Sipariş veya danışmanlık için iletişime geçebilirsin.</i>")
    return "\n".join(parts)

# ================== TELEGRAM HANDLER’LAR ==================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        print(f"[error] {context.error}")
    except Exception:
        pass

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    u = update.effective_user
    full_name = (u.full_name or u.first_name or "Misafir").strip()
    msg = (
        f"Merhaba, aramıza hoş geldin <b>{full_name}</b>! 🌿✨\n"
        "Bee’M International ailesine katıldığın için teşekkür ederiz.\n\n"
        "Komutlar: /yardim — /icerik — /fiyat — /fiyat_durum"
    )
    await m.reply_text(msg, parse_mode="HTML", reply_markup=build_leader_buttons())

async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    msg = (
        "<b>Komutlar</b>\n"
        "• /icerik <ürün|ihtiyaç> — Ürün bilgi kartı\n"
        "• /fiyat <ürün> — Excel’den fiyat\n"
        "• /fiyat_durum — Fiyat kaynağı ve yükleme zamanı\n"
        "• /yardim — Bu menü\n\n"
        "Not: Tıbbi tavsiye veremem; lütfen doktorunuza danışın."
    )
    await m.reply_text(msg, parse_mode="HTML", reply_markup=build_leader_buttons())

async def cmd_fiyat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    q = " ".join(context.args) if context.args else ""
    if not q:
        await m.reply_text("Kullanım: /fiyat <ürün adı>", reply_markup=build_leader_buttons())
        return
    price = find_price(q)
    if price is None:
        await m.reply_text(
            "Üzgünüm, bu isimde bir ürün bulamadım veya fiyatı tanımlı değil. "
            "Lütfen ürün adını kontrol edip tekrar deneyiniz.",
            reply_markup=build_leader_buttons()
        )
        return
    price_txt = f"{int(price):,}".replace(",", ".")
    await m.reply_text(
        f"{q.strip()} — <b>{price_txt} TL</b>",
        parse_mode="HTML",
        reply_markup=build_leader_buttons()
    )

async def cmd_fiyat_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    load_prices_from_excel()
    src = "Excel" if PRICE_SHEET_URL else "—"
    msg = (
        f"<b>Fiyat Kaynağı:</b> {src}\n"
        f"<b>Son Yükleme:</b> {_price_cache_updated}\n"
        f"<b>Kayıtlı Ürün Sayısı:</b> {len(_price_cache)}"
    )
    await m.reply_text(msg, parse_mode="HTML")

async def cmd_icerik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    q = " ".join(context.args) if context.args else ""
    if not q:
        await m.reply_text("Kullanım: /icerik <ürün|alias|ihtiyaç>", reply_markup=build_leader_buttons())
        return
    if CATALOG_SOURCE_OVERRIDE != "JSON":
        await m.reply_text("Ürün kataloğu şu anda aktif değil. Daha sonra tekrar dene.", reply_markup=build_leader_buttons())
        return
    prod = find_product_by_query(q)
    if not prod:
        await m.reply_text("Bu isimde ürün bulamadım. Adı kontrol edip yeniden deneyebilirsin.", reply_markup=build_leader_buttons())
        return
    await m.reply_text(product_card_text(prod), parse_mode="HTML", reply_markup=build_leader_buttons())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    text = _get_effective_text(update)

    # 1) ÜRÜN ÖNCELİĞİ (alias + ürün adı)
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        prod = find_product_by_query(text)
        if prod:
            await m.reply_text(product_card_text(prod), parse_mode="HTML", reply_markup=build_leader_buttons())
            return

    # 2) Basit satış danışmanı cevabı (tıbbi iddia yok)
    reply = (
        "İhtiyacını anladım. Sana uygun ürünü birlikte netleştirebiliriz. "
        "Kısa bir mesajla neye odaklandığını yaz: ‘cilt temizliği’, ‘enerji’, ‘eklem desteği’…\n\n"
        "Detay için butonlardan bize yazabilirsin."
    )
    await m.reply_text(reply, reply_markup=build_leader_buttons())

async def on_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m: return
    await m.reply_text(
        "Ben Bee’M AI Asistanıyım. Ürün bilgisi ve satış yönlendirmesi için buradayım. "
        "Sağlıkla ilgili kişisel konularda doktorunuza danışın.",
        reply_markup=build_leader_buttons()
    )

# ================== TELEGRAM APP (v20) ==================

BOT_TOKEN = _get_env("TELEGRAM_BOT_TOKEN")
application: Application = ApplicationBuilder().token(BOT_TOKEN).build()

# Komutlar
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("yardim", cmd_yardim))
application.add_handler(CommandHandler("fiyat", cmd_fiyat))
application.add_handler(CommandHandler("fiyat_durum", cmd_fiyat_durum))
application.add_handler(CommandHandler("icerik", cmd_icerik))

# “sen kimsin” yakalayıcı
application.add_handler(MessageHandler(filters.Regex(re.compile(r"\b(kimsin|sen kimsin|kim\s?sin)\b", re.I)), on_whoami))

# Serbest metin
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

# Hata yakalayıcı
application.add_error_handler(on_error)

# ================== FASTAPI APP ==================

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    # Safe startup: Telegram init hata verirse servis yine de ayakta kalsın
    try:
        await application.initialize()
        asyncio.create_task(application.bot.set_my_commands([
            ("start","Hoş geldin"),
            ("yardim","Komutlar"),
            ("icerik","Ürün kartı"),
            ("fiyat","Fiyat"),
            ("fiyat_durum","Fiyat durumu")
        ]))
        print("[startup] Telegram initialized.")
    except Exception as e:
        print(f"[startup] Telegram init failed (continuing): {e}")

@app.get("/")
def root():
    return PlainTextResponse("OK")

def _health_base() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "catalog_size": get_catalog_size_override(),
        "updated": (CATALOG_DATA_OVERRIDE.get("metadata", {}) or {}).get("updated", "Henüz yüklenmedi"),
        "source": "JSON" if CATALOG_SOURCE_OVERRIDE == "JSON" else ("Excel" if PRICE_SHEET_URL else "—"),
    }

@app.get("/health")
def health():
    return JSONResponse(health_patch(_health_base()))

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    try:
        await application.process_update(update)
    except Exception as e:
        print(f"[webhook] process_update error: {e}")
    return JSONResponse({"ok": True})
