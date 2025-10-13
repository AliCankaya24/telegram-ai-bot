# main.py — Bee’M AI Asistan (final)
# Özellikler:
# - /start, /menu: Kişiye isimle hoş geldin + butonlu menü
# - /fiyat <ürün>: Ad + fiyat + lider yönlendirme
# - /fiyat_guncelle: Ürün/fiyat listesini siteden yeniler (GİZLİ — sadece admin)
# - /fiyat_durum: Son güncelleme ve ürün sayısı
# - /icerik <ürün>: Tekil ürün sayfasından içerik/kullanım özeti
# - “sen kimsin?” gibi doğal sorulara mix tanıtım cevabı
# - /kargo, /indirim, /destek: Kısa yardımcı akışlar
#
# Env: TELEGRAM_BOT_TOKEN, GROQ_API_KEY
# Not: Kişisel veri/log tutulmaz.

import os, time, re, difflib, requests
from typing import Dict, Tuple, Optional, List
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

CATALOG_URL = "https://www.beeminternational.com.tr/urun/"

# Admin kullanıcı adları: /fiyat_guncelle sadece bunlarda çalışır
ADMIN_USERNAMES = set(u.strip().lower() for u in os.getenv(
    "ADMIN_USERNAMES",
    "ali_cankaya, deryakaratasates"
).split(","))

app = FastAPI()

# ----------------- Yardımcılar -----------------
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
        # /fiyat_guncelle gizli: listeye BİLEREK eklenmiyor
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
    table = str.maketrans("çğıöşüâêîû", "cgiosuaeiu")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def format_price_try(raw: str) -> str:
    nums = re.sub(r"[^\d,\.]", "", raw)
    nums = nums.replace(".", "").replace(",", ".")
    try:
        val = float(nums)
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"
    except:
        return raw.strip()

# ----------------- Katalog (bellek içi) -----------------
class ProductCatalog:
    def __init__(self):
        self.name_map: Dict[str, Tuple[str, Optional[str]]] = {}
        self.search_index: Dict[str, str] = {}
        self.updated_ts: Optional[int] = None

    def clear(self):
        self.name_map.clear()
        self.search_index.clear()
        self.updated_ts = None

    def set(self, items: List[Tuple[str, str, Optional[str]]]):
        self.clear()
        for name, price, href in items:
            self.name_map[name] = (price, href)
            self.search_index[tr_norm(name)] = name
        self.updated_ts = int(time.time())

    def size(self) -> int:
        return len(self.name_map)

    def last_updated_human(self) -> str:
        if not self.updated_ts:
            return "Henüz güncellenmedi"
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

CATALOG = ProductCatalog()

# ----------------- Scraper -----------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0; +https://core.telegram.org/bots)"
}

def scrape_catalog() -> List[Tuple[str, str, Optional[str]]]:
    """ Ürün listesini /urun/ sayfasından linkleriyle alır; fiyatı tekil ürün sayfalarından çıkarır.
        Fiyat bulmada: sayfadaki tüm para adaylarını toplayıp (TL/₺ olsa da olmasa da),
        0 ve çok küçük değerleri eleyip en büyük mantıklı değeri fiyat kabul eder. """
    # 1) Liste sayfasını al
    try:
        r = requests.get(CATALOG_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # 2) Ürün linklerini & görünen isimleri topla
    product_links: Dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt = a.get_text(" ", strip=True)
        if not txt or not href:
            continue
        if "/urun/" not in href:
            continue
        if href.startswith("/"):
            href = "https://www.beeminternational.com.tr" + href
        name = txt
        key = tr_norm(name) or tr_norm(href.rsplit("/", 1)[-1])
        if key and key not in product_links:
            product_links[key] = href

    if not product_links:
        return []

    # 3) Her ürün sayfasına girip isim + fiyat çıkar
    results: List[Tuple[str, str, Optional[str]]] = []
    money_pat = re.compile(r"\b(\d{1,3}(?:[\.\s]\d{3})*(?:[\,\.]\d{2})|\d+)\b")  # 984,50 | 1.234,00 | 999

    for key, href in list(product_links.items()):
        try:
            pr = requests.get(href, headers=HEADERS, timeout=30)
            pr.raise_for_status()
        except Exception:
            continue

        psoup = BeautifulSoup(pr.text, "html.parser")
        page_text = " ".join(psoup.get_text(" ", strip=True).split())

        # İsim adayı: h1/h2 başlıklar öncelik
        name_el = psoup.find(["h1", "h2"])
        name = (name_el.get_text(" ", strip=True) if name_el else None) or key

        # 3a) Önce yaygın fiyat alanlarından metinleri topla
        texts: List[str] = []
        for sel in [".price", ".woocommerce-Price-amount", ".product-price", ".summary .price"]:
            for el in psoup.select(sel):
                t = el.get_text(" ", strip=True)
                if t:
                    texts.append(t)

        # 3b) Yedek: tüm sayfa metninden de aday topla
        texts.append(page_text)

        # 3c) Metinlerdeki sayısal para adaylarını topla ve normalize et (TR → float)
        candidates: List[float] = []
        for t in texts:
            for m in money_pat.finditer(t):
                raw = m.group(1)
                # normalize: 1.234,56 → 1234.56  |  999 → 999.00
                x = raw.replace(".", "").replace(" ", "").replace(",", ".")
                try:
                    val = float(x)
                    candidates.append(val)
                except:
                    continue

        # 3d) Adayları filtrele: 0 ve çok küçükleri at (ör. sepet 0,00)
        # (10 TL altını çöplük kabul ediyoruz; istersen bu eşiği 5 yapabiliriz)
        candidates = [v for v in candidates if v >= 10]

        if not candidates:
            # Fiyat çıkarılamadıysa bu ürünü atla
            continue

        # 3e) En büyük mantıklı değeri fiyat kabul et
        best = max(candidates)

        # 3f) TL biçiminde yaz
        price_fmt = f"{best:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"

        results.append((name, price_fmt, href))

        time.sleep(0.2)  # nazik gecikme

    # Yinelenenleri normalize ederek temizle
    seen = set()
    uniq: List[Tuple[str, str, Optional[str]]] = []
    for n, p, h in results:
        k = tr_norm(n)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((n, p, h))

    return uniq

def scrape_product_details(url: str) -> str:
    """ Tekil ürün sayfasından içerik/kullanım özetini çıkarır. """
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

# ----------------- Metinler -----------------
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

# ----------------- FastAPI -----------------
@app.get("/")
def home():
    return {"ok": True, "msg": "Bot ayakta. /health de hazır."}

@app.get("/health")
def health():
    set_bot_commands()
    return {"status": "healthy", "catalog_size": CATALOG.size(), "updated": CATALOG.last_updated_human()}

# ----------------- İş mantığı -----------------
def is_admin(chat: dict) -> bool:
    uname = (chat.get("username") or "").lower()
    return uname in ADMIN_USERNAMES

def ensure_catalog():
    if CATALOG.size() == 0:
        items = scrape_catalog()
        if items:
            CATALOG.set(items)

def price_answer(name: str, price: str) -> str:
    return (
        f"*{name}* — *{price}*\n\n"
        "Bee’M kulübüne katılmak veya *indirimli satın almak* istersen, liderlerimize yönlendirebilirim."
    )

# ----------------- Webhook -----------------
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
        ensure_catalog()
        if CATALOG.size() == 0:
            send_message(chat_id, "Şu an fiyatları çekemiyorum. Lütfen biraz sonra tekrar deneyiniz.")
            return {"ok": True}
        name, suggestions = CATALOG.find(query)
        if name:
            price, _href = CATALOG.name_map.get(name, ("", None))
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
        msg = f"Ürün sayısı: {CATALOG.size()} • Güncelleme: {CATALOG.last_updated_human()}"
        send_message(chat_id, msg)
        return {"ok": True}

    if low.startswith("/fiyat_guncelle"):
        # GİZLİ — sadece admin kullanıcı adları
        if is_admin(chat):
            try:
                items = scrape_catalog()
                if items:
                    CATALOG.set(items)
                    send_message(chat_id, f"Güncellendi ✅ {CATALOG.size()} ürün • {CATALOG.last_updated_human()}")
                else:
                    send_message(chat_id, "Liste boş döndü. Site yapısı değişmiş olabilir.")
            except Exception:
                send_message(chat_id, "Güncelleme sırasında bir sorun oluştu.")
        # admin değilse sessizce yok say
        return {"ok": True}

    # --- İçerik akışı ---
    if low.startswith("/icerik"):
        query = text[len("/icerik"):].strip()
        if not query:
            send_message(chat_id, "Örnek kullanım: `/icerik OZN-Omega 3`")
            return {"ok": True}
        ensure_catalog()
        name, suggestions = CATALOG.find(query)
        if not name:
            if suggestions:
                sug = "\n".join(f"• {s}" for s in suggestions)
                send_message(chat_id, f"Tam olarak bulamadım. Yakın ürünler:\n{sug}")
            else:
                send_message(chat_id, "Bu isimde bir ürün bulamadım.")
            return {"ok": True}
        price, href = CATALOG.name_map.get(name, ("", None))
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
