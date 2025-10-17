# main.py — Bee’M AI Asistan (Excel fiyatlı final)
# Özellikler:
# - /start, /menu: Kişiye isimle hoş geldin + butonlu menü
# - /fiyat <ürün>: Excel'den "Ad — Fiyat" + lider yönlendirme
# - /fiyat_guncelle: Excel'i URL'den tekrar okur (GİZLİ — sadece admin)
# - /fiyat_durum: Son yükleme ve ürün sayısı
# - /icerik <ürün>: Ürünün detay linkinden içerik/kullanım özeti (Excel'de "url" doluysa onu, yoksa /urun/ sayfasından bulmaya çalışır)
# - “sen kimsin?” → mix tanıtım
# - /kargo, /indirim, /destek: kısa akışlar
#
# Env: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, PRICE_SHEET_URL
# Opsiyonel: ADMIN_USERNAMES (virgüllü liste; varsayılan: ali_cankaya, deryakaratasates)
# Not: Kişisel veri/log tutulmaz.

import os, time, re, difflib, io, requests
# ==== FORCE JSON CATALOG OVERRIDE (drop-in hotfix) ==========================
import os, json, urllib.request
from typing import Any, Dict, List

CATALOG_SOURCE_OVERRIDE = None      # "JSON" ya da None
CATALOG_DATA_OVERRIDE: Dict[str, Any] = {}

def _get_env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default

def _fetch_json(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)

def try_load_json_catalog_override() -> None:
    global CATALOG_SOURCE_OVERRIDE, CATALOG_DATA_OVERRIDE
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
            print(f"[catalog-override] Loaded {len(products)} products from JSON.")
        else:
            print("[catalog-override] JSON reached but no products found.")
    except Exception as e:
        print(f"[catalog-override] JSON load failed: {e}")

# Uygulama import edilir edilmez bir kere dene:
try_load_json_catalog_override()

def get_catalog_size_override() -> int:
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        prods = CATALOG_DATA_OVERRIDE.get("products", [])
        return len(prods) if isinstance(prods, list) else 0
    return 0

def health_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Var olan /health yanıtını JSON override aktifse düzeltir.
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        payload = dict(payload or {})
        payload["source"] = "JSON"
        payload["catalog_size"] = get_catalog_size_override()
    return payload
# ==== /FORCE JSON CATALOG OVERRIDE =========================================

from typing import Dict, Tuple, Optional, List
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
import pandas as pd

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
PRICE_SHEET_URL = os.getenv("PRICE_SHEET_URL", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Admin kullanıcı adları (küçük harf, @ yok)
ADMIN_USERNAMES = set(u.strip().lower() for u in os.getenv(
    "ADMIN_USERNAMES",
    "ali_cankaya, deryakaratasates"
).split(","))

# Ürün linklerini bulmak için fallback sayfası
CATALOG_URL = "https://www.beeminternational.com.tr/urun/"

app = FastAPI()

# -------------- Yardımcılar --------------
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
                        "başvurabilirsiniz' de. Satın alma yönlendirmesinde liderlerden "
                        "İNDİRİM LİNKİ isteyebileceğini hatırlat. Kısa, anlaşılır, Türkçe yanıt ver."
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

def leader_inline_keyboard() -> dict:
    return {
        "inline_keyboard": [[
            {"text": "İletişim - Ali Çankaya", "url": "https://t.me/ali_cankaya"},
            {"text": "İletişim - Derya Karataş Ateş", "url": "https://t.me/deryakaratasates"}
        ]]
    }

def set_bot_commands():
    cmds = [
        {"command": "start", "description": "Başlat ve menüyü göster"},
        {"command": "menu", "description": "Menüyü tekrar göster"},
        {"command": "fiyat", "description": "Fiyat sorgula: /fiyat <ürün>"},
        # /fiyat_guncelle gizli: listeye eklemiyoruz
        {"command": "fiyat_durum", "description": "Fiyat listesi bilgisi"},
        {"command": "icerik", "description": "Ürün içeriği: /icerik <ürün>"},
        {"command": "kargo", "description": "Kargo & teslimat bilgisi"},
        {"command": "indirim", "description": "İndirim linki yönlendirmesi"},
        {"command": "destek", "description": "Canlı destek/iletişim"},
    ]
    try:
        requests.post(f"{TELEGRAM_API}/setMyCommands",
                      json={"commands": cmds}, timeout=20)
    except Exception:
        pass

def tr_norm(s: str) -> str:
    s = s.lower().strip()
    table = str.maketrans("çğıöşüâêîû’'", "cgiosuaeiu  ")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def format_price_try(val) -> str:
    # val sayı ise: 984.5 → "984,50 TL"; metinse parse etmeye çalış
    if isinstance(val, (int, float)):
        num = float(val)
    else:
        raw = str(val).strip()
        raw = raw.replace(".", "").replace(",", ".")
        try:
            num = float(raw)
        except:
            return str(val)
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"

# -------------- Katalog (Excel'den) --------------
class ExcelCatalog:
    def __init__(self):
        # name_map: orijinal_ad -> (fiyat_fmt, url)
        self.name_map: Dict[str, Tuple[str, Optional[str]]] = {}
        # search_index: normalize -> orijinal_ad
        self.search_index: Dict[str, str] = {}
        self.updated_ts: Optional[int] = None
        self.source_info: str = "Henüz yüklenmedi"

    def clear(self):
        self.name_map.clear()
        self.search_index.clear()
        self.updated_ts = None
        self.source_info = "Henüz yüklenmedi"

    def set_from_excel(self, url: str):
        self.clear()
        if not url:
            return
        # Google Drive / GitHub Raw vs hepsi için basit indirme
        headers = {"User-Agent": "Mozilla/5.0 (TelegramBot Excel Loader)"}
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = io.BytesIO(r.content)
        df = pd.read_excel(data, sheet_name="prices")

        # Beklenen kolonlar: product_name, price_tl, aliases, url, notes (diğerleri yoksa sorun değil)
        cols = {c.lower(): c for c in df.columns}
        pname_c = cols.get("product_name") or "product_name"
        price_c = cols.get("price_tl") or "price_tl"
        alias_c = cols.get("aliases") if "aliases" in cols else None
        url_c   = cols.get("url") if "url" in cols else None

        for _, row in df.iterrows():
            name = str(row.get(pname_c) or "").strip()
            if not name:
                continue
            price = row.get(price_c)
            # Fiyatı biçimle
            price_fmt = format_price_try(price) if price is not None and str(price).strip() != "" else ""
            url_val = str(row.get(url_c) or "").strip() if url_c else ""
            # Kaydet
            self.name_map[name] = (price_fmt, url_val if url_val else None)
            self.search_index[tr_norm(name)] = name
            # Aliases
            if alias_c:
                aliases = str(row.get(alias_c) or "").strip()
                if aliases:
                    for a in [x.strip() for x in aliases.split(",") if x.strip()]:
                        self.search_index[tr_norm(a)] = name
        self.updated_ts = int(time.time())
        self.source_info = "Excel"

    def size(self) -> int:
        return len(self.name_map)

    def last_updated_human(self) -> str:
        if not self.updated_ts:
            return "Henüz yüklenmedi"
        t = time.strftime("%d.%m.%Y %H:%M", time.localtime(self.updated_ts))
        return f"{t} itibarıyla"

    def find(self, query: str) -> Tuple[Optional[str], List[str]]:
        if not query:
            return None, []
        qn = tr_norm(query)
        if qn in self.search_index:
            return self.search_index[qn], []
        keys = list(self.search_index.keys())
        close = difflib.get_close_matches(qn, keys, n=3, cutoff=0.6)
        suggestions = [self.search_index[k] for k in close]
        return None, suggestions

CATALOG = ExcelCatalog()

# -------------- İçerik detay (ürün URL'si) --------------
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"}

def find_product_url_by_name(name: str) -> Optional[str]:
    """ Excel'de URL yoksa /urun/ sayfasından benzer isimli linki bulmaya çalış. """
    try:
        r = requests.get(CATALOG_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    target = tr_norm(name)
    best_url, best_score = None, 0.0
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if not txt: 
            continue
        score = difflib.SequenceMatcher(None, tr_norm(txt), target).ratio()
        if score > best_score and "/urun/" in a["href"]:
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.beeminternational.com.tr" + href
            best_url, best_score = href, score
    return best_url

def scrape_product_details(url: str) -> str:
    """ Tekil ürün sayfasından içerik/kullanım özeti. """
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception:
        return "Ürün detay sayfasına şu an ulaşılamıyor. Lütfen daha sonra tekrar deneyiniz."
    soup = BeautifulSoup(r.text, "html.parser")
    full = " ".join(soup.get_text(" ", strip=True).split())

    patterns = [
        r"içindekiler[:\s]+(.{50,500})",
        r"kullanım\s*tal[ıi]mat[ıi](:|\s)+(.{50,500})",
        r"nasıl\s*kullan[ıi]l[ıi]r[:\s]+(.{50,500})",
        r"içerik[:\s]+(.{50,500})",
        r"özellikler[:\s]+(.{50,500})",
    ]
    for pat in patterns:
        m = re.search(pat, full, flags=re.I)
        if m:
            chunk = m.group(1) if m.lastindex == 1 else m.group(2)
            chunk = chunk.strip()
            return (chunk[:400] + "…") if len(chunk) > 400 else chunk

    body = full[:600]
    if body:
        return (body + "…") if len(full) > 600 else body
    return "Bu ürün için detay metni bulunamadı."

# -------------- Metinler --------------
def welcome_text(first_name: Optional[str]) -> str:
    name = first_name or "Değerli Üyemiz"
    return (
        f"Merhaba, aramıza hoş geldin! ({name}) 🌿✨\n"
        "Bee’m International ailesine katıldığın için teşekkür ederiz.\n"
        "Bugün sağlığın ve yaşam kaliten için çok değerli bir adım attın ve biz de bu yolculukta yanındayız.\n\n"
        "Aldığın ürünler; bilimsel içeriği, yüksek saflık oranı ve IFOS – GMP – ISO gibi uluslararası kalite "
        "sertifikalarıyla güvence altındadır. Ürünlerini düzenli kullandığında hem enerjinin yükseldiğini hem "
        "yaşam kalitenin arttığını hissedeceksin.\n\n"
        "📌 *Destek Hattı | Ürün Kullanım Rehberi*\n"
        "Ürünlerinle ilgili kullanım desteği, soru-cevap, tavsiye ya da takip isteyen herkes için buradayız.\n"
        "Herhangi bir sorunda bu mesajı yanıtlaman yeterli 😊\n\n"
        "Unutma: Sağlık yolculuğu birlikte daha güçlü 🍀\n"
        "Tekrar aramıza hoş geldin!\n"
        "**Ali ÇANKAYA - Derya ATEŞ**"
    )

KARGO_INFO = (
    "📦 *Kargo & Teslimat Bilgisi*\n"
    "• Siparişler genellikle **1–3 iş günü** içinde teslim edilir.\n"
    "• Kargonuz gelmediyse Bee’M International iletişim hattını arayabilirsiniz: "
    "**0 530 393 23 36**\n"
    "• Kargo takip numaranız varsa yazın, kontrol edelim.\n\n"
    "_Not: Ürünlerle ilgili genel bilgi verebilirim; tıbbî tavsiye veremem. "
    "Kişisel sağlık durumunuz için doktorunuza başvurabilirsiniz._"
)

BOT_IDENTITY = (
    "Ben **Bee’M AI Asistan** 🤝\n"
    "Bee’M International ürünleri hakkında **içerik**, **fiyat**, **kullanım desteği** ve **bilgi yönlendirmesi** sağlayan "
    "yapay zekâ tabanlı bir yardımcım. **Gerçek bir ekibe bağlı çalışıyorum**: *Ali Çankaya & Derya Karataş Ateş* liderliğinde "
    "destek veriyorum.\n\n"
    "Sorulara **doğru, net ve hızlı** yanıt vermeye çalışırım. Tıbbî tanı/tedavi öneremem; gerekli durumlarda **doktorunuza başvurabilirsiniz**."
)

# -------------- FastAPI --------------
@app.get("/")
def home():
    return {"ok": True, "msg": "Bot ayakta. /health de hazır."}

@app.get("/health")
def health():
    set_bot_commands()
    payload = {
        "status": "healthy",
        "catalog_size": CATALOG.size(),
        "updated": CATALOG.last_updated_human(),
        "source": "Excel" if PRICE_SHEET_URL else "—"
    }
    # JSON override aktifse düzelt
    payload = health_patch(payload)

    # JSON override aktifse 'updated' alanını JSON metadata'dan doldur
    if CATALOG_SOURCE_OVERRIDE == "JSON":
        payload["updated"] = CATALOG_DATA_OVERRIDE.get("metadata", {}).get("updated", "JSON yüklendi")

    return payload


# -------------- Yetki / Yardımcı işlevler --------------
def is_admin(chat: dict) -> bool:
    uname = (chat.get("username") or "").lower()
    return uname in ADMIN_USERNAMES

def ensure_catalog_from_excel():
    if CATALOG.size() == 0 and PRICE_SHEET_URL:
        try:
            CATALOG.set_from_excel(PRICE_SHEET_URL)
        except Exception:
            pass

def price_answer(name: str, price: str) -> str:
    tail = (
        "\n\nBee’M kulübüne katılmak veya *indirimli satın almak* istersen, "
        "liderlerimize yönlendirebilirim."
    )
    return f"*{name}* — *{price}*{tail}"

# -------------- Webhook --------------
@app.post("/webhook")
async def telegram_webhook(req: Request):
    update = await req.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    low = text.lower()
    first_name = chat.get("first_name")

    if not chat_id:
        return {"ok": True}

    # --- Menü / Karşılama ---
    if low.startswith("/start") or low.startswith("/menu") or low == "menu":
        send_message(chat_id, welcome_text(first_name), reply_markup=main_menu_keyboard())
        return {"ok": True}

    # --- Kargo / İndirim / Destek ---
    if low.startswith("/kargo") or low == "📦 kargo":
        send_message(chat_id, KARGO_INFO)
        return {"ok": True}

    if low.startswith("/indirim") or low == "🔗 indirim":
        send_message(chat_id, "İndirimli satın alma için liderlerimizle iletişime geçebilirsiniz:", reply_markup=leader_inline_keyboard())
        return {"ok": True}

    if low.startswith("/destek") or low == "🆘 destek":
        send_message(chat_id, "Canlı destek için aşağıdaki bağlantılardan bize ulaşabilirsiniz.", reply_markup=leader_inline_keyboard())
        return {"ok": True}

    # --- Fiyat akışı ---
    if low == "💰 fiyat":
        send_message(chat_id, "Lütfen ürün adını şu şekilde gönderin:\n`/fiyat <ürün adı>`")
        return {"ok": True}

    if low.startswith("/fiyat"):
        query = text[len("/fiyat"):].strip()
        if not query:
            send_message(chat_id, "Örnek kullanım: `/fiyat OZN-Omega 3`")
            return {"ok": True}
        ensure_catalog_from_excel()
        if CATALOG.size() == 0:
            send_message(chat_id, "Şu an fiyat listesini yükleyemedim. Lütfen `/fiyat_guncelle` sonrası tekrar deneyin.")
            return {"ok": True}
        name, suggestions = CATALOG.find(query)
        if name:
            price, _href = CATALOG.name_map.get(name, ("", None))
            if not price:
                send_message(chat_id, f"*{name}* için fiyat bulunamadı. Excel'de `price_tl` alanını doldurup `/fiyat_guncelle` yapabilirsiniz.")
                return {"ok": True}
            send_message(chat_id, price_answer(name, price), reply_markup=leader_inline_keyboard())
        else:
            if suggestions:
                sug = "\n".join(f"• {s}" for s in suggestions)
                send_message(chat_id, f"Bu isimde ürün bulamadım. Yakın sonuçlar:\n{sug}\n\n"
                                      "İstersen ürün adını düzelterek yeniden deneyebilirsin.")
            else:
                send_message(chat_id, "Bu isimde bir ürün bulamadım. Lütfen ürün adını kontrol edip tekrar dener misiniz?")
        return {"ok": True}

    if low.startswith("/fiyat_durum"):
        msg = f"Kaynak: Excel • Ürün sayısı: {CATALOG.size()} • Yükleme: {CATALOG.last_updated_human()}"
        send_message(chat_id, msg)
        return {"ok": True}

    if low.startswith("/fiyat_guncelle"):
        # GİZLİ — sadece admin kullanıcı adları
        if is_admin(chat) and PRICE_SHEET_URL:
            try:
                CATALOG.set_from_excel(PRICE_SHEET_URL)
                send_message(chat_id, f"Güncellendi ✅ {CATALOG.size()} ürün • {CATALOG.last_updated_human()}")
            except Exception:
                send_message(chat_id, "Güncelleme sırasında bir sorun oluştu. PRICE_SHEET_URL geçerli mi?")
        # admin değilse sessizce geç
        return {"ok": True}

    # --- İçerik akışı ---
    if low.startswith("/icerik"):
        query = text[len("/icerik"):].strip()
        if not query:
            send_message(chat_id, "Örnek kullanım: `/icerik OZN-Omega 3`")
            return {"ok": True}
        ensure_catalog_from_excel()
        name, suggestions = CATALOG.find(query)
        if not name:
            if suggestions:
                sug = "\n".join(f"• {s}" for s in suggestions)
                send_message(chat_id, f"Tam olarak bulamadım. Yakın ürünler:\n{sug}")
            else:
                send_message(chat_id, "Bu isimde bir ürün bulamadım.")
            return {"ok": True}
        price, href = CATALOG.name_map.get(name, ("", None))
        # URL Excel'de yoksa /urun/ sayfasından bulmayı dene
        if not href:
            href = find_product_url_by_name(name)
        if not href:
            send_message(chat_id, f"*{name}* için detay bağlantısı bulunamadı.")
            return {"ok": True}
        detail = scrape_product_details(href)
        send_message(chat_id,
            f"*{name}* — içerik/kullanım özeti:\n{detail}\n\n"
            "_Genel bilgilendirme amaçlıdır; tıbbî tavsiye veremem. "
            "Kişisel durumunuz için doktorunuza başvurabilirsiniz._",
            reply_markup=leader_inline_keyboard()
        )
        return {"ok": True}

    # --- “Sen kimsin?” doğal sorusu ---
    if "sen kimsin" in low or "kimsin" in low or low.startswith("/kim"):
        send_message(chat_id, BOT_IDENTITY)
        return {"ok": True}

    # --- Diğer her şey: Akıllı sohbet ---
    if text:
        reply = ask_groq(text)
        send_message(chat_id, reply)
    return {"ok": True}
