# main.py — Bee’M AI Asistan (JSON içerik + Excel fiyat)
# Özellikler:
# - /start: İsimle hoş geldin + butonlu menü
# - /yardim: Komutlar
# - /fiyat <ürün>: Excel'den "Ad — Fiyat" + lider butonları
# - /fiyat_durum: Fiyat kaynağı ve yükleme durumu
# - /icerik <ürün|alias|ihtiyaç cümlesi>: JSON katalogtan ürün kartı
# - Serbest metin: Önce ürün eşleşmesi; yoksa satış danışmanı cevabı
# - “sen kimsin” vb: Bot tanıtım
#
# Env: TELEGRAM_BOT_TOKEN (zorunlu), PRICE_SHEET_URL (Excel)
#      PRODUCTS_SOURCE=JSON, PRODUCTS_JSON_URL=<RAW>
#      ALI_TELEGRAM, DERYA_TELEGRAM
#
# Not: Kişisel veri/log tutulmaz.

import os, re, io, json, time, urllib.request, asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

# ---- 3. taraflar ----
import pandas as pd  # Excel fiyat için (requirements: pandas, openpyxl)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ================== YARDIMCILAR ==================

def _get_env(*names: str, default: str = "") -> str:
    """ENV değerlerini sırayla dener; ilk dolu olanı döndürür."""
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default

def _norm(s: str) -> str:
    """Türkçe normalize + boşluk sadeleştirme."""
    if not s: return ""
    s = s.lower().strip()
    repl = {
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
        "’": "'", "”": '"', "“": '"'
    }
    for a,b in repl.items():
        s = s.replace(a,b)
    return " ".join(s.split())

def human_now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")

# ================== JSON KATALOG OVERRIDE ==================

CATALOG_SOURCE_OVERRIDE: Optional[str] = None   # "JSON" / None
CATALOG_DATA_OVERRIDE: Dict[str, Any] = {}
ALIAS_INDEX: List[tuple] = []  # (alias_norm, product_dict)

def _fetch_json(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)

def try_load_json_catalog_override() -> None:
    """ENV JSON istiyorsa kataloğu yükle ve alias indeksini kur."""
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
            # alias indeksini kur
            ALIAS_INDEX = []
            for p in products:
                name = p.get("product_name","").strip()
                aliases = p.get("aliases", []) or []
                candidates = set(aliases + ([name] if name else []))
                for a in candidates:
                    ALIAS_INDEX.append((_norm(a), p))
            # uzun alias'lar önce
            ALIAS_INDEX.sort(key=lambda x: len(x[0]), reverse=True)
            print(f"[catalog-override] Loaded {len(products)} products from JSON.")
        else:
            print("[catalog-override] JSON reached but no products found.")
    except Exception as e:
        print(f"[catalog-override] JSON load failed: {e}")

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
        payload["updated"] = CATALOG_DATA_OVERRIDE.get("metadata", {}).get("updated", payload.get("updated") or "JSON yüklendi")
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

# ================== EXCEL FİYAT KAYNAĞI ==================

PRICE_SHEET_URL = _get_env("PRICE_SHEET_URL")  # GitHub raw .xlsx olabilir
_price_cache: Dict[str, float] = {}
_price_cache_updated: str = "Henüz yüklenmedi"

def load_prices_from_excel(force: bool = False) -> None:
    """Excel URL’den fiyatları yükle (Ad, Fiyat) sütunlarını okur."""
    global _price_cache, _price_cache_updated
    if not PRICE_SHEET_URL:
        _price_cache = {}
        _price_cache_updated = "Kaynak yok"
        return
    if _price_cache and not force:
        return
    try:
        # Excel indir
        data = urllib.request.urlopen(PRICE_SHEET_URL, timeout=30).read()
        df = pd.read_excel(io.BytesIO(data))
        # Beklenen sütun adları: Ad, Fiyat (senin şablon öyleydi)
        name_col = None
        price_col = None
        for c in df.columns:
            cn = _norm(str(c))
            if name_col is None and ("ad" in cn or "urun" in cn):
                name_col = c
            if price_col is None and ("fiyat" in cn or "price" in cn):
                price_col = c
        if name_col is None or price_col is None:
            raise ValueError("Excel sütunları bulunamadı (Ad/Fiyat).")
        cache = {}
        for _, row in df.iterrows():
            name = str(row[name_col]).strip()
            if not name or name.lower() == "nan":
                continue
            try:
                price_val = float(row[price_col])
            except Exception:
                continue
            cache[_norm(name)] = price_val
        _price_cache = cache
        _price_cache_updated = human_now()
        print(f"[prices] Loaded {len(_price_cache)} items from Excel at { _price_cache_updated }")
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
    # 3) Basit yakın eşleşme
    best = None
    best_ratio = 0.0
    for k,v in _price_cache.items():
        # çok basit jaccard benzeri
        inter = len(set(k.split()) & set(q.split()))
        ratio = inter / max(1, len(set(k.split())))
        if ratio > best_ratio:
            best_ratio = ratio
            best = v
    return best

# ================== TELEGRAM BİLEŞENLERİ ==================

BOT_TOKEN = _get_env("TELEGRAM_BOT_TOKEN")
ALI_TELE = _get_env("ALI_TELEGRAM", default="@ali_cankaya").lstrip("@")
DERYA_TELE = _get_env("DERYA_TELEGRAM", default="@deryakaratasates").lstrip("@")

def build_leader_buttons() -> InlineKeyboardMarkup:
    buttons = [[
        InlineKeyboardButton("Ali Çankaya", url=f"https://t.me/{ALI_TELE}"),
        InlineKeyboardButton("Derya Karataş Ateş", url=f"https://t.me/{DERYA_TELE}"),
    ]]
    return InlineKeyboardMarkup(buttons)

def product_card_text(p: Dict[str, Any]) -> str:
    name = p.get("product_name","BEE’M Ürünü")
    desc = p.get("description","")
    ingredients = p.get("ingredients",[])
    usage = p.get("usage","")
    lines = []
    lines.append(f"✨ <b>{name}</b>")
    if desc:
        lines.append(desc)
    # İçerik listesi uzun ise kısaltmadan madde madde göster
    if ingredients:
        lines.append("\n<b>Öne Çıkan İçerikler:</b>")
        for it in ingredients[:10]:
            lines.append(f"• {it}")
    if usage:
        lines.append(f"\n<b>Kullanım:</b> {usage}")
    # Tıbbi uyarı
    med = p.get("medical_note","")
    if med:
        lines.append(f"\n<i>{med}</i>")
    # Kapanış stili: C (güçlü)
    lines.append("\n<i>Daha net sonuç için düzenli kullanım önerilir. Sipariş veya danışmanlık için iletişime geçebilirsin.</i>")
    return "\n".join(lines)

# ================== TELEGRAM HANDLER’LAR ==================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    full_name = (user.full_name or user.first_name or "Misafir").strip()
    msg = (
        f"Merhaba, aramıza hoş geldin <b>{full_name}</b>! 🌿✨\n"
        "Bee’M International ailesine katıldığın için teşekkür ederiz.\n\n"
        "Ürünlerin bilimsel içeriği ve uluslararası kalite standartlarıyla güvence altındadır. "
        "Soru ve destek için yazabilirsin.\n\n"
        "Komutlar: /yardim — /icerik — /fiyat — /fiyat_durum"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=build_leader_buttons())

async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>Komutlar</b>\n"
        "• /icerik <ürün|ihtiyaç> — Ürün bilgi kartı\n"
        "• /fiyat <ürün> — Excel’den fiyat\n"
        "• /fiyat_durum — Fiyat kaynağı ve yükleme zamanı\n"
        "• /yardim — Bu menü\n\n"
        "Not: Ürün dışı sağlık sorularında tıbbi tavsiye veremem; doktorunuza danışın."
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=build_leader_buttons())

async def cmd_fiyat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args) if context.args else ""
    if not q:
        await update.message.reply_text("Kullanım: /fiyat <ürün adı>", reply_markup=build_leader_buttons())
        return
    price = find_price(q)
    if price is None:
        await update.message.reply_text(
            "Üzgünüm, bu isimde bir ürün bulamadım veya fiyatı tanımlı değil. "
            "Lütfen ürün adını kontrol edip tekrar deneyiniz.",
            reply_markup=build_leader_buttons()
        )
        return
    await update.message.reply_text(
        f"{q.strip()} — <b>{int(price):,} TL</b>".replace(",", "."),
        parse_mode="HTML",
        reply_markup=build_leader_buttons()
    )

async def cmd_fiyat_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_prices_from_excel()  # gerekirse yükler
    src = "Excel" if PRICE_SHEET_URL else "—"
    msg = (
        f"<b>Fiyat Kaynağı:</b> {src}\n"
        f"<b>Son Yükleme:</b> {_price_cache_updated}\n"
        f"<b>Kayıtlı Ürün Sayısı:</b> {len(_price_cache)}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def cmd_icerik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args) if context.args else ""
    if not q:
        await update.message.reply_text("Kullanım: /icerik <ürün|alias|ihtiyaç>", reply_markup=build_leader_buttons())
        return
    if CATALOG_SOURCE_OVERRIDE != "JSON":
        await update.message.reply_text(
            "Ürün kataloğu şu anda aktif değil. Lütfen daha sonra tekrar deneyiniz.",
            reply_markup=build_leader_buttons()
        )
        return
    prod = find_product_by_query(q)
    if not prod:
        await update.message.reply_text(
            "Bu isimde ürün bulamadım. Ürün adını kontrol edip yeniden deneyebilirsin.",
            reply_markup=build_leader_buttons()
        )
        return
    await update.message.reply_text(product_card_text(prod), parse_mode="HTML", reply_markup=build_leader_buttons())

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Serbest metin: önce ürün eşleşmesi; yoksa satış asistanı cevabı."""
    text = update.message.text or ""
    # 1) ÜRÜN ÖNCELİĞİ
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        prod = find_product_by_query(text)
        if prod:
            await update.message.reply_text(product_card_text(prod), parse_mode="HTML", reply_markup=build_leader_buttons())
            return
    # 2) Basit satış danışmanı cevabı (tıbbi iddia yok)
    reply = (
        "İhtiyacını anladım. Sana uygun ürünü birlikte netleştirebiliriz. "
        "Kısa bir mesajla neye odaklandığını yaz: örn. ‘cilt temizliği’, ‘enerji’, ‘eklem desteği’.\n\n"
        "Detaylı sorular ve sipariş için butonlardan bize ulaşabilirsin."
    )
    await update.message.reply_text(reply, reply_markup=build_leader_buttons())

async def on_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Ben Bee’M AI Asistanıyım. Ürün bilgisi, kullanım önerisi ve satış yönlendirmesi için yanındayım. "
        "Sağlık konularında tıbbi tavsiye veremem; doktorunuza danışınız."
    )
    await update.message.reply_text(msg, reply_markup=build_leader_buttons())

# ================== TELEGRAM APP & WEBHOOK ==================

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN env eksik!")

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

async def _set_bot_commands():
    try:
        await application.bot.set_my_commands([
            ("start", "Hoş geldin mesajı"),
            ("yardim", "Komutlar menüsü"),
            ("icerik", "Ürün bilgi kartı"),
            ("fiyat", "Excel’den fiyat"),
            ("fiyat_durum", "Fiyat kaynağı ve durum"),
        ])
    except Exception as e:
        print(f"[botcmd] set_my_commands failed: {e}")

# ================== FASTAPI APP ==================

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    # Excel fiyatları lazy yüklenir; burada komutları set edelim
    asyncio.create_task(_set_bot_commands())

@app.get("/")
def root_ok():
    return PlainTextResponse("OK")

@app.get("/health")
def health():
    load_prices_from_excel()  # lazy yük
    payload = {
        "status": "healthy",
        "catalog_size": get_catalog_size_override(),
        "updated": CATALOG_DATA_OVERRIDE.get("metadata", {}).get("updated", "Henüz yüklenmedi"),
        "source": "JSON" if CATALOG_SOURCE_OVERRIDE == "JSON" else ("Excel" if PRICE_SHEET_URL else "—"),
    }
    payload = health_patch(payload)
    return JSONResponse(payload)

@app.post("/telegram")
async def telegram_webhook(req: Request):
    """Telegram webhook endpoint."""
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.initialize()
    await application.process_update(update)
    return JSONResponse({"ok": True})
