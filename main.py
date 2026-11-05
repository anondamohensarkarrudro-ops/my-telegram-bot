import os, sys, re, time, math, glob, json, html, shutil, subprocess, threading, queue
import asyncio, contextlib, logging
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


# ───────────────────────── yt-dlp অটো-আপডেট ─────────────────────────
# YouTube প্রায়ই বদলায়; পুরনো yt-dlp হলে "Requested format is not available" আসে।
# Docker cache-এর কারণে pip install পুরনো ভার্সনেই আটকে থাকে, তাই প্রতিবার চালুর সময় আপডেট হবে।
def pip_update():
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--no-cache-dir", "--quiet", "yt-dlp[default]"],
                           capture_output=True, text=True, timeout=240)
        print("yt-dlp আপডেট:", "সফল" if r.returncode == 0 else (r.stderr or "")[-300:])
    except Exception as e:
        print("yt-dlp আপডেট ব্যর্থ:", e)


if os.environ.get("AUTO_UPDATE", "1") == "1":
    pip_update()

from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeAudio
import yt_dlp

try:
    print("yt-dlp ভার্সন:", yt_dlp.version.__version__)
except Exception:
    pass


# ───────────────────────── কনফিগ ─────────────────────────
def need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Environment variable '{name}' সেট করা নেই। Render > Environment-এ যোগ করুন।")
    return v


TOKEN = need("TOKEN")
if TOKEN.lower().startswith("bot"):
    TOKEN = TOKEN[3:]
CHAT = need("CHAT")
API_ID = int(need("API_ID"))
API_HASH = need("API_HASH")
SESSION = need("SESSION")
TARGET = int(need("TARGET"))
FIRST = os.environ.get("CHANNEL", "").strip()
DIR = "./downloads"
DATA = "data.json"
LIMIT = 1900 * 1024 * 1024
API = f"https://api.telegram.org/bot{TOKEN}/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Cookie": "CONSENT=YES+1"}
START = time.time()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

shutil.rmtree(DIR, ignore_errors=True)  # আগের রান-এর ফেলে যাওয়া ফাইল মুছে ফেলা
os.makedirs(DIR, exist_ok=True)

# ───────────────────────── YouTube কুকি ─────────────────────────
# Render Secret File: cookies.txt (/etc/secrets/cookies.txt)
COOKIES_SRC = None
for _p in ("/etc/secrets/cookies.txt", "./cookies.txt"):
    if os.path.exists(_p) and os.path.getsize(_p) > 10:
        try:
            raw = open(_p, "r", encoding="utf-8", errors="ignore").read().replace("\r\n", "\n").replace("\r", "\n")
            if not raw.lstrip().startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
                raw = "# Netscape HTTP Cookie File\n" + raw
            COOKIES_SRC = "/tmp/cookies_base.txt"
            with open(COOKIES_SRC, "w", encoding="utf-8") as _f:
                _f.write(raw)
            break
        except Exception as _e:
            print("cookies পড়া যায়নি:", _e)
print("COOKIES:", "পাওয়া গেছে" if COOKIES_SRC else "পাওয়া যায়নি (Secret File cookies.txt নেই)")


@contextlib.contextmanager
def ytdl(quiet=True, **extra):
    """প্রতিবার আলাদা কুকি-কপি দিয়ে yt-dlp চালায় (থ্রেডের মধ্যে ফাইল নষ্ট হওয়া ঠেকাতে)।"""
    opts = {"quiet": quiet, "no_warnings": quiet, "noplaylist": True, "retries": 5, "fragment_retries": 10,
            "socket_timeout": 30, "remote_components": ["ejs:github"]}
    ck = None
    if COOKIES_SRC:
        ck = f"/tmp/yt_ck_{threading.get_ident()}_{time.time_ns()}.txt"
        try:
            shutil.copy(COOKIES_SRC, ck)
            opts["cookiefile"] = ck
        except Exception as e:
            print("cookie copy:", e)
            ck = None
    opts.update(extra)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            yield ydl
    finally:
        if ck:
            with contextlib.suppress(Exception):
                os.remove(ck)


# ───────────────────────── ওয়েব সার্ভার (Render জাগিয়ে রাখতে) ─────────────────────────
logging.getLogger("werkzeug").setLevel(logging.WARNING)
app = Flask(__name__)


@app.route("/")
def home():
    return "alive"


@app.route("/health")
def health():
    return "ok"


def serve():
    port = int(os.environ.get("PORT", 10000))
    try:
        from waitress import serve as wserve
        wserve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)


def keepalive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except Exception as e:
            print("keepalive:", e)


threading.Thread(target=serve, daemon=True).start()
threading.Thread(target=keepalive, daemon=True).start()

# ───────────────────────── Telethon ─────────────────────────
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
client = TelegramClient(StringSession(SESSION), API_ID, API_HASH, loop=LOOP,
                        connection_retries=None, retry_delay=5, auto_reconnect=True, flood_sleep_threshold=60)
client.parse_mode = "html"


def run(coro):
    return LOOP.run_until_complete(coro)


run(client.connect())
if not run(client.is_user_authorized()):
    raise SystemExit("SESSION ফাঁকা বা অবৈধ। নতুন StringSession বানিয়ে Render Environment-এ SESSION দিন।")
run(client.get_dialogs())

lock = threading.Lock()
Q = queue.Queue()
CUR = [""]
OFFSET = [0]
TITLES = {}
last_backup = [None]
D = {"channels": [], "seen": {}, "interval": 15, "quality": 720, "tag": "", "sent": 0, "last": "", "wait": {}}

HELP = ("🤖 <b>🌟 RudroBots YouTube Downloader Bot 🌟</b>\n\n"
        "স্বাগতম! যেকোনো ইউটিউব লিংক সরাসরি বটে পাঠালে বা /dl [link] লিখলে ভিডিও ডাউনলোড হবে।\n\n"
        "📂 <b>চ্যানেল কমান্ডসমূহ:</b>\n"
        "• /add [লিংক/@handle] — নতুন চ্যানেল যোগ\n"
        "• /list — চ্যানেল তালিকা\n"
        "• /ch [নম্বর] — চ্যানেলের সেটিংস\n"
        "• /remove [নম্বর] — চ্যানেল বাদ\n"
        "• /pause /resume [নম্বর] — পজ/চালু\n"
        "• /old [নম্বর] [সংখ্যা] — পুরনো ভিডিও\n\n"
        "⚙️ <b>চ্যানেলভিত্তিক সেটিং (/set নম্বর অপশন মান):</b>\n"
        "• include [keyword] — ফিল্টার শব্দ\n"
        "• exclude [keyword] — বাদ দেওয়ার শব্দ\n"
        "• noshorts [on/off] — শর্টস ফিল্টার\n"
        "• quality [360/480/720/1080/auto] — কোয়ালিটি\n"
        "• audio [on/off] — শুধু MP3\n"
        "• prefix [Text] — ক্যাপশন প্রিফিক্স\n"
        "• ctag [Name] — ফাইলের শেষে ও ট্যাগের নাম (যেমন: Alamin)\n\n"
        "🛠 <b>সাধারণ কমান্ড:</b>\n"
        "• /tag [#tag] — গ্লোবাল ট্যাগ\n"
        "• /dl [link] — সরাসরি লিংক থেকে ডাউনলোড\n"
        "• /queue — লাইনের তালিকা\n"
        "• /status — স্ট্যাটাস\n"
        "• /update — yt-dlp আপডেট ও বট রিস্টার্ট")


# ───────────────────────── Telegram Bot API হেল্পার ─────────────────────────
def clean_err(e):
    return ANSI.sub("", str(e)).strip()


def tg(method, _timeout=60, **kw):
    data = urllib.parse.urlencode(kw).encode()
    try:
        with urllib.request.urlopen(API + method, data, timeout=_timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        with contextlib.suppress(Exception):
            body = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"Telegram {method} HTTP {e.code}: {body}") from None


def call(method, **kw):
    return tg(method, **kw)


def say(text, chat=None, markup=None):
    try:
        kw = {"chat_id": chat or CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup:
            kw["reply_markup"] = markup
        return tg("sendMessage", **kw)["result"]["message_id"]
    except Exception as e:
        print("say error:", e)
        return None


def edit(text, msg_id, chat=None, markup=None):
    try:
        kw = {"chat_id": chat or CHAT, "message_id": msg_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup:
            kw["reply_markup"] = markup
        tg("editMessageText", **kw)
    except Exception as e:
        if "not modified" not in str(e):
            print("edit error:", e)


def set_bot_menu():
    commands_list = [
        {"command": "start", "description": "🚀 বট শুরু করুন"},
        {"command": "add", "description": "➕ চ্যানেল যোগ করুন"},
        {"command": "list", "description": "📺 চ্যানেল তালিকা"},
        {"command": "ch", "description": "⚙️ চ্যানেল সেটিংস"},
        {"command": "dl", "description": "⬇️ লিংক থেকে ডাউনলোড"},
        {"command": "queue", "description": "🔄 ডাউনলোড কিউ"},
        {"command": "status", "description": "📊 বট স্ট্যাটাস"},
    ]
    try:
        tg("setMyCommands", _timeout=30, commands=json.dumps(commands_list))
    except Exception as e:
        print("Menu error:", e)


def send_error_log(user_name, user_id, channel_name, link, error_msg):
    err = clean_err(error_msg)[:2500]
    log_text = (
        f"🚨 <b>MJ Bot Error Logs</b>\n"
        f"<b>Download Error</b>\n\n"
        f"👤 <b>User:</b> {html.escape(str(user_name))}\n"
        f"🆔 <b>UserID:</b> <code>{user_id}</code>\n"
        f"📺 <b>Channel/Source:</b> {html.escape(str(channel_name))}\n\n"
        f"🔗 <b>Link:</b> {link}\n\n"
        f"⚠️ <b>Context:</b> Video Download & Processing\n\n"
        f"<b>Error:</b>\n"
        f"<blockquote expandable>{html.escape(err)}</blockquote>"
    )
    say(log_text, chat=TARGET)
    say(log_text, chat=CHAT)


# ───────────────────────── ডেটা সেভ/রিস্টোর ─────────────────────────
def save():
    with lock:
        for _ in range(5):
            try:
                tmp = DATA + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(D, f, ensure_ascii=False)
                os.replace(tmp, DATA)
                return
            except RuntimeError:  # অন্য থ্রেড একই সময়ে D বদলালে
                time.sleep(0.2)
            except Exception as e:
                print("save error:", e)
                return


async def restore():
    async for m in client.iter_messages(TARGET, search="#BACKUP", limit=5):
        if m.document:
            await m.download_media(DATA)
            return m.id


try:
    last_backup[0] = run(restore())
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            D.update(json.load(f))
except Exception as e:
    print("restore:", e)


def backup():
    save()
    msg = run(client.send_file(TARGET, DATA, caption="#BACKUP", force_document=True))
    old_id = last_backup[0]
    last_backup[0] = msg.id
    if old_id:
        try:
            run(client.delete_messages(TARGET, [old_id]))
        except Exception as e:
            print("backup delete:", e)


# ───────────────────────── YouTube ফিড/লিংক ─────────────────────────
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "ignore")


def feed_html(cid):
    page = get(f"https://www.youtube.com/channel/{cid}/videos")
    name_match = re.search(r"<title>(.*?)</title>", page)
    name = html.unescape(name_match.group(1).replace(" - YouTube", "")) if name_match else "Channel"
    items = []
    seen_local = set()
    for v in re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', page):
        if v in seen_local:
            continue
        seen_local.add(v)
        tm = re.search(rf'"videoId":"{v}".*?"title":{{"runs":\[{{"text":"(.*?)"}}', page)
        t = html.unescape(tm.group(1)) if tm else f"New Video {v}"
        items.append((v, t, f"/shorts/{v}" in page))
        if len(items) >= 10:
            break
    return name, items


def feed(cid):
    """প্রথমে YouTube-এর অফিশিয়াল RSS (স্থিতিশীল), না হলে পেজ স্ক্র্যাপ।"""
    try:
        root = ET.fromstring(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        name = root.findtext("a:title", default="Channel", namespaces=ns)
        items = []
        for e in root.findall("a:entry", ns):
            v = e.findtext("yt:videoId", namespaces=ns)
            t = e.findtext("a:title", default="", namespaces=ns)
            link = e.find("a:link", ns)
            href = link.get("href", "") if link is not None else ""
            if v:
                items.append((v, t or f"New Video {v}", "/shorts/" in href))
        if items:
            return name, items[:10]
    except Exception as e:
        print("rss ব্যর্থ, পেজ থেকে নিচ্ছি:", e)
    return feed_html(cid)


def resolve(text):
    text = text.strip()
    m = re.search(r"UC[\w-]{22}", text)
    if m:
        return m.group(0)
    if text.startswith("http"):
        url = text
    else:
        url = "https://www.youtube.com/" + (text if text.startswith("@") else "@" + text)
    try:
        page = get(url)
        for pat in (r'rel="canonical" href="https://www.youtube.com/channel/(UC[\w-]{22})',
                    r'"externalId":"(UC[\w-]{22})"',
                    r'"channelId":"(UC[\w-]{22})"'):
            m = re.search(pat, page)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def extract_vid(text, bare=False):
    text = text.strip()
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/|live/)([a-zA-Z0-9_-]{11})', text)
    if m:
        return m.group(1)
    if bare and re.fullmatch(r'[a-zA-Z0-9_-]{11}', text):
        return text
    return None


def chan(cid):
    return next((c for c in D["channels"] if c["id"] == cid), None)


def pick(s):
    s = s.strip()
    return D["channels"][int(s) - 1] if s.isdigit() and 1 <= int(s) <= len(D["channels"]) else None


def remember(cid, vid):
    s = D["seen"].setdefault(cid, [])
    if vid not in s:
        s.append(vid)
        del s[:-500]


def allowed(ch, title, short):
    if ch.get("noshorts") and short:
        return False
    low = title.lower()
    inc = ch.get("include") or []
    if inc and not any(w.lower() in low for w in inc):
        return False
    if any(w.lower() in low for w in (ch.get("exclude") or [])):
        return False
    return True


def info(vid):
    try:
        with ytdl(True, skip_download=True, ignore_no_formats_error=True) as ydl:
            j = ydl.extract_info(f"https://youtu.be/{vid}", download=False) or {}
        hs = sorted({f["height"] for f in (j.get("formats") or []) if f.get("height") and f.get("vcodec") != "none"})
        return hs, j.get("duration"), j.get("title") or "", j.get("live_status") or ""
    except Exception as e:
        print("info error:", clean_err(e)[:200])
        return [], None, "", ""


def notify(vid, title, name):
    hs, dur, _, st = info(vid)
    if len(TITLES) > 300:
        TITLES.pop(next(iter(TITLES)))
    TITLES[vid] = (title, name)
    res = " • ".join(f"{h}p" for h in hs) or "অজানা"
    d = time.strftime("%H:%M:%S", time.gmtime(dur)) if dur else "—"
    note = {"is_live": "🔴 লাইভ চলছে — শেষ হলে অটো ডাউনলোড হবে",
            "is_upcoming": "🕒 লাইভ/প্রিমিয়ার আসছে — শেষ হলে অটো ডাউনলোড হবে",
            "post_live": "⏳ লাইভ শেষ, প্রসেস হচ্ছে — একটু পরে ডাউনলোড হবে"}.get(st, "⏳ ডাউনলোড শুরু হচ্ছে...")

    cap = (f"✨ <b>#NewEpisode #YouTube #RudroBots</b>\n\n"
           f"🎬 <b>Serial Name:</b> {html.escape(title[:300])}\n"
           f"📺 <b>Channel Name:</b> {html.escape(name)}\n"
           f"🎞 <b>Available Quality:</b> {res}\n"
           f"⏱ <b>Duration:</b> {d}\n\n"
           f"🔗 <b>Watch Link:</b>\n<code>https://youtu.be/{vid}</code>\n\n"
           f"📢 {note}")

    kb = json.dumps({"inline_keyboard": [
        [{"text": "💖 360p", "callback_data": f"dl|{vid}|360"},
         {"text": "⚡ 720p", "callback_data": f"dl|{vid}|720"},
         {"text": "🚀 1080p", "callback_data": f"dl|{vid}|1080"}],
        [{"text": "🎵 MP3 Audio", "callback_data": f"dl|{vid}|mp3"}]]})

    for chat in (CHAT, TARGET):
        sent = False
        for q in ("maxresdefault", "hqdefault"):
            try:
                call("sendPhoto", chat_id=chat, photo=f"https://i.ytimg.com/vi/{vid}/{q}.jpg", caption=cap, parse_mode="HTML", reply_markup=kb)
                sent = True
                break
            except Exception:
                continue
        if not sent:
            say(cap, chat, kb)
    return st


# ───────────────────────── ফাইল/ভিডিও প্রসেসিং ─────────────────────────
def safe_name(s, max_bytes=100):
    """ফাইলের নামে নিষিদ্ধ অক্ষর বাদ + দৈর্ঘ্য সীমিত (Linux-এ ২৫৫ বাইটের বেশি হলে এরর হয়)।"""
    s = re.sub(r'[\\/*?:"<>|#\x00-\x1f]', "", s or "").strip()
    s = re.sub(r"\s+", ".", s).strip(".")
    while len(s.encode("utf-8")) > max_bytes:
        s = s[:-1]
    return s.strip(".") or "video"


def probe(path):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,height:format=duration", "-of", "json", path], timeout=60)
        j = json.loads(out)
        st = (j.get("streams") or [{}])[0]
        return float((j.get("format") or {}).get("duration") or 0), int(st.get("width") or 0), int(st.get("height") or 0)
    except Exception:
        return 0.0, 0, 0


def make_thumb(path, out):
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", out],
                       timeout=60, check=True, capture_output=True)
        return out if os.path.exists(out) else None
    except Exception:
        return None


def find_output(workdir, vid, audio):
    want = os.path.join(workdir, vid + (".mp3" if audio else ".mp4"))
    if os.path.exists(want):
        return want
    cands = [os.path.join(workdir, f) for f in os.listdir(workdir)
             if f.startswith(vid + ".") and not f.endswith((".part", ".ytdl", ".temp", ".jpg", ".webp", ".png"))]
    return max(cands, key=os.path.getsize) if cands else None


def to_mp4(path):
    """mp4 না হলে mp4-তে রূপান্তর (প্রথমে কপি, না হলে রি-এনকোড)।"""
    if path.lower().endswith(".mp4"):
        return path
    out = os.path.splitext(path)[0] + "_conv.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c", "copy", "-movflags", "+faststart", out], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Exception("mp4-তে রূপান্তর ব্যর্থ: " + r.stderr[-300:])
    os.remove(path)
    return out


def finalize_video(path, safe, suffix):
    """সাইজ ঠিক থাকলে একটাই ফাইল, বেশি হলে ভাগ করে। নাম: Title.720p.YouTube.Tag.mp4"""
    d = os.path.dirname(path)
    size = os.path.getsize(path)
    if size <= LIMIT:
        new = os.path.join(d, f"{safe}.{suffix}.mp4")
        os.rename(path, new)
        return [new]
    dur, _, _ = probe(path)
    if dur <= 0:
        raise Exception("ভিডিওর দৈর্ঘ্য পড়া যায়নি, ভাগ করা সম্ভব হয়নি।")
    n = math.ceil(size / LIMIT) + 1
    seg = max(1, int(dur / n))
    stem = os.path.splitext(os.path.basename(path))[0]
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-map", "0:v", "-map", "0:a?", "-c", "copy", "-f", "segment",
                        "-segment_time", str(seg), "-reset_timestamps", "1", os.path.join(d, f"{stem}_part%02d.mp4")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Exception("ffmpeg ভাগ করা ব্যর্থ: " + r.stderr[-300:])
    os.remove(path)
    temp = sorted(f for f in os.listdir(d) if f.startswith(stem + "_part") and f.endswith(".mp4"))
    parts = []
    for i, f in enumerate(temp, 1):
        new = os.path.join(d, f"{safe}.Part{i}.{suffix}.mp4")
        os.rename(os.path.join(d, f), new)
        parts.append(new)
    return parts


def fmt_size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024


def fmt_eta(s):
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def bar_of(p):
    filled = max(0, min(20, int(p // 5)))
    return "█" * filled + "-" * (20 - filled)


def process(vid, title, name, announce=False, quality=None, audio=False, prefix="", ctag="", user_name="Admin", user_id=CHAT):
    if announce:
        try:
            notify(vid, title, name)
        except Exception as e:
            print("notify:", e)
    qh = quality or D["quality"] or 720
    url = f"https://youtu.be/{vid}"
    suffix_name = safe_name(ctag, 30) if ctag and ctag.strip() else "YouTube"
    workdir = os.path.join(DIR, vid)
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    msg_id = say(f"📥 <b>Downloading Video...</b>\n\n<b>Filename:</b> {html.escape(title[:100])}\n[{bar_of(0)}] 0%")
    last_up = [0]

    def prog(d):
        if d.get("status") != "downloading":
            return
        now = time.time()
        if now - last_up[0] < 3:
            return
        last_up[0] = now
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        p = min(100.0, done * 100.0 / total) if total else 0.0
        spd = d.get("speed")
        txt = (f"📥 <b>Downloading Video...</b>\n\n"
               f"<b>Filename:</b> {html.escape(title[:100])}\n"
               f"[{bar_of(p)}] {p:.1f}%\n\n"
               f"• <b>Size:</b> {fmt_size(done)} / {fmt_size(total) if total else 'Unknown'}\n"
               f"• <b>Speed:</b> {fmt_size(spd) + '/s' if spd else '—'}\n"
               f"• <b>ETA:</b> {fmt_eta(d.get('eta'))}")
        if msg_id:
            edit(txt, msg_id)

    try:
        if audio:
            fmt = "bestaudio/best"
        else:
            fmt = (f"bv*[height<={qh}][ext=mp4]+ba[ext=m4a]/bv*[height<={qh}]+ba/b[height<={qh}]/bv*+ba/b")
        extra = {"format": fmt, "outtmpl": os.path.join(workdir, vid + ".%(ext)s"), "progress_hooks": [prog],
                 "noprogress": True, "concurrent_fragment_downloads": 4, "quiet": False, "no_warnings": False}
        if audio:
            extra["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            extra["merge_output_format"] = "mp4"
        with ytdl(False, **extra) as ydl:
            ydl.download([url])

        path = find_output(workdir, vid, audio)
        if not path:
            raise Exception("ডাউনলোড করা ফাইল পাওয়া যায়নি।")

        safe = safe_name(title, 100)
        res_tag = f"{qh}p.YouTube" if not audio else "Audio.YouTube"
        final_suffix = f"{res_tag}.{suffix_name}"

        if audio:
            final_path = os.path.join(workdir, f"{safe}.{final_suffix}.mp3")
            os.rename(path, final_path)
            parts = [final_path]
        else:
            parts = finalize_video(to_mp4(path), safe, final_suffix)

        total_parts = len(parts)
        for i, p in enumerate(parts, 1):
            fname = os.path.basename(p)
            cap = (html.escape(prefix[:100]) + " " if prefix else "") + ("🎵 " if audio else "🎬 ") + \
                  f"{html.escape(title[:200])}\n📺 {html.escape(name[:100])}\n📁 <code>{html.escape(fname)}</code>"
            if total_parts > 1:
                cap += f"\n📦 Part {i}/{total_parts}"
            if ctag:
                cap += f"\n{html.escape(ctag[:50])}"
            elif D.get("tag"):
                cap += f"\n{html.escape(D['tag'][:100])}"

            dur, w, h = probe(p)
            thumb = None
            if audio:
                attrs = [DocumentAttributeAudio(duration=int(dur), title=title[:64])]
            else:
                attrs = [DocumentAttributeVideo(duration=int(dur), w=w or 1280, h=h or 720, supports_streaming=True)]
                thumb = make_thumb(p, os.path.join(workdir, f"thumb{i}.jpg"))

            up_start = time.time()
            up_last = [0]

            def tg_prog(current, total, fname=fname, up_start=up_start, up_last=up_last):
                now = time.time()
                if now - up_last[0] < 3:
                    return
                up_last[0] = now
                p_val = (current / total) * 100 if total > 0 else 0
                diff = now - up_start
                spd = current / diff if diff > 0 else 1
                eta_sec = int((total - current) / spd) if spd > 0 else 0
                if msg_id:
                    edit(f"📤 <b>Uploading: {p_val:.1f}%</b>\n\n"
                         f"[{bar_of(p_val)}]\n"
                         f"<b>{current / 1048576:.1f} MB of {total / 1048576:.1f} MB</b>\n"
                         f"⚡ <b>Speed:</b> {spd / 1048576:.2f} MB/sec\n"
                         f"⏳ <b>ETA:</b> {eta_sec}sec\n\n"
                         f"📁 <b>Filename:</b> {html.escape(fname)}", msg_id)

            msg = run(client.send_file(TARGET, p, caption=cap, attributes=attrs, thumb=thumb,
                                       force_document=False, progress_callback=tg_prog))
            try:
                call("copyMessage", chat_id=CHAT, from_chat_id=TARGET, message_id=msg.id)
            except Exception as e:
                print("copy:", e)

        if msg_id:
            with contextlib.suppress(Exception):
                tg("deleteMessage", _timeout=30, chat_id=CHAT, message_id=msg_id)

        D["sent"] = D.get("sent", 0) + 1
        D["last"] = title[:80]
        say(f"✅ সফলভাবে পাঠানো হয়েছে: {html.escape(title[:200])}")
    except Exception as e:
        err_text = clean_err(e)
        print("process error:", err_text)
        if msg_id:
            edit("❌ ডাউনলোডে ব্যর্থ হয়েছে!", msg_id)
        send_error_log(user_name, user_id, name, f"https://youtu.be/{vid}", err_text)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # সব টেম্প/রিনেম করা ফাইল মুছে ডিস্ক ফাঁকা রাখে


def run_ch(ch, vid, title, announce=False):
    process(vid, title, ch["name"], announce, quality=ch.get("quality"), audio=ch.get("audio", False),
            prefix=ch.get("prefix", ""), ctag=ch.get("ctag", ""))


def old(cid, n):
    ch = chan(cid)
    if not ch:
        return
    items = []
    try:
        with ytdl(True, extract_flat="in_playlist", playlistend=n, noplaylist=False) as ydl:
            j = ydl.extract_info(f"https://www.youtube.com/channel/{cid}/videos", download=False) or {}
        items = [(e["id"], e.get("title") or e["id"]) for e in (j.get("entries") or []) if e and e.get("id")]
    except Exception as e:
        print("old error:", clean_err(e)[:200])
    if not items:
        say("❌ ভিডিওর তালিকা পাওয়া যায়নি।")
        return
    say(f"📥 {len(items)}টি পুরনো ভিডিও ডাউনলোড শুরু হচ্ছে...")
    for vid, title in reversed(items):
        run_ch(ch, vid, title, True)
        remember(cid, vid)
    backup()


def describe(job):
    if job[0] == "video":
        return "🎬 " + job[3][:50]
    if job[0] == "old":
        return f"📥 পুরনো {job[2]}টি ভিডিও"
    if job[0] == "manual":
        t_info = TITLES.get(job[1])
        t_name = t_info[0] if t_info else job[1]
        return f"⬇️ {t_name[:40]} ({job[2]})"
    if job[0] == "link":
        return f"🔗 লিংক ডাউনলোড: {job[1]}"
    return "💾 ব্যাকআপ"


# ───────────────────────── কমান্ড হ্যান্ডলার ─────────────────────────
def handle(text, user_name="Admin", user_id=CHAT):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    chs = D["channels"]
    if cmd in ("/start", "/help"):
        say(HELP)
    elif cmd == "/add":
        if not arg:
            say("⚠️ ব্যবহার: /add চ্যানেল-লিংক বা @handle")
            return
        cid = resolve(arg)
        if not cid:
            say("❌ চ্যানেলটি খুঁজে পাওয়া যায়নি।")
            return
        if chan(cid):
            say("⚠️ এই চ্যানেলটি ইতিপূর্বেই যোগ করা আছে।")
            return
        name, items = feed(cid)
        chs.append({"id": cid, "name": name})
        D["seen"][cid] = [v for v, _, _ in items]
        save()
        Q.put(("backup",))
        say(f"✅ সফলভাবে যোগ হয়েছে: <b>{html.escape(name)}</b>")
    elif cmd == "/list":
        if not chs:
            say("📂 কোনো চ্যানেল যুক্ত করা নেই।")
            return
        rows = "\n".join(f"<b>{i}.</b> {'⏸ ' if c.get('paused') else '▶️ '}{html.escape(c['name'])}" for i, c in enumerate(chs, 1))
        say(f"📺 <b>যুক্ত চ্যানেলসমূহ:</b>\n{rows}")
    elif cmd == "/ch":
        c = pick(arg)
        if not c:
            say("⚠️ ব্যবহার: /ch [চ্যানেল নম্বর]")
            return
        say(f"⚙️ <b>চ্যানেল সেটিংস: {html.escape(c['name'])}</b>\n\n"
            f"🔹 <b>অবস্থা:</b> {'⏸ পজ' if c.get('paused') else '▶️ চালু'}\n"
            f"🔹 <b>কোয়ালিটি:</b> {c.get('quality') or 'auto'}\n"
            f"🔹 <b>শুধু MP3:</b> {'হ্যাঁ' if c.get('audio') else 'না'}\n"
            f"🔹 <b>Shorts বাদ:</b> {'হ্যাঁ' if c.get('noshorts') else 'না'}\n"
            f"🔹 <b>Custom Tag (Ctag):</b> {html.escape(c.get('ctag') or '—')}\n"
            f"🔹 <b>Prefix:</b> {html.escape(c.get('prefix') or '—')}")
    elif cmd == "/remove":
        c = pick(arg)
        if not c:
            say("⚠️ ব্যবহার: /remove [নম্বর]")
            return
        chs.remove(c)
        D["seen"].pop(c["id"], None)
        save()
        Q.put(("backup",))
        say(f"🗑 বাদ দেওয়া হয়েছে: {html.escape(c['name'])}")
    elif cmd in ("/pause", "/resume"):
        c = pick(arg)
        if not c:
            say(f"⚠️ ব্যবহার: {cmd} [নম্বর]")
            return
        c["paused"] = cmd == "/pause"
        save()
        Q.put(("backup",))
        say(f"{'⏸ পজ' if c['paused'] else '▶️ চালু'}: {html.escape(c['name'])}")
    elif cmd == "/old":
        a = arg.split()
        if len(a) < 2 or not a[1].isdigit() or not pick(a[0]):
            say("⚠️ ব্যবহার: /old [নম্বর] [সংখ্যা]")
            return
        Q.put(("old", pick(a[0])["id"], min(int(a[1]), 50)))
        say("⏳ পুরনো ভিডিও ডাউনলোডের লাইনে যোগ হয়েছে।")
    elif cmd == "/set":
        a = arg.split(None, 2)
        c = pick(a[0]) if a else None
        if not c or len(a) < 3:
            say("⚠️ ব্যবহার: /set [নম্বর] [অপশন] [মান]\nঅপশন: include, exclude, noshorts, quality, audio, prefix, ctag")
            return
        opt, val = a[1].lower(), a[2].strip()
        clear = val == "-"
        if opt in ("include", "exclude"):
            c[opt] = [] if clear else [w.strip() for w in val.split(",") if w.strip()]
        elif opt in ("noshorts", "audio"):
            c[opt] = val.lower() in ("on", "yes", "1")
        elif opt == "quality":
            if val.lower() == "auto":
                c[opt] = None
            elif val.isdigit() and 144 <= int(val) <= 2160:
                c[opt] = int(val)
            else:
                say("⚠️ কোয়ালিটি হবে: 360 / 480 / 720 / 1080 / auto")
                return
        elif opt in ("prefix", "ctag"):
            c[opt] = "" if clear else val
        else:
            say("⚠️ ভুল অপশন!")
            return
        save()
        Q.put(("backup",))
        say(f"✅ আপডেট হয়েছে ({opt})। দেখতে লিখুন: /ch {chs.index(c) + 1}")
    elif cmd == "/dl":
        vid = extract_vid(arg, bare=True)
        if not vid:
            say("⚠️ সঠিক ইউটিউব লিংক দিন। ব্যবহার: /dl [link]")
            return
        Q.put(("link", vid, user_name, user_id))
        say("⏳ লিংকটি ডাউনলোডের লাইনে যোগ করা হয়েছে।")
    elif cmd == "/tag":
        if not arg:
            say(f"🏷 বর্তমান ট্যাগ: {html.escape(D.get('tag') or 'নেই')}")
            return
        D["tag"] = "" if arg == "-" else arg
        save()
        Q.put(("backup",))
        say("✅ গ্লোবাল ট্যাগ সেট করা হয়েছে।")
    elif cmd == "/queue":
        jobs = [describe(j) for j in list(Q.queue) if j[0] != "backup"]
        txt = f"🔄 <b>চলমান:</b> {html.escape(CUR[0]) or 'নেই'}\n📥 <b>লাইনে:</b> {len(jobs)}টি"
        if jobs:
            txt += "\n\n" + "\n".join(f"{i}. {html.escape(j)}" for i, j in enumerate(jobs[:10], 1))
        say(txt)
    elif cmd == "/status":
        s = int(time.time() - START)
        free = shutil.disk_usage(DIR).free / 1e9
        try:
            ver = yt_dlp.version.__version__
        except Exception:
            ver = "?"
        say(f"📊 <b>বট স্ট্যাটাস</b>\n⏱ রানিং: {s // 3600} ঘণ্টা {(s % 3600) // 60} মিনিট\n📺 চ্যানেল: {len(chs)}টি\n"
            f"✅ পাঠানো: {D.get('sent', 0)}টি\n💾 ডিস্ক ফাঁকা: {free:.1f} GB\n🍪 কুকি: {'আছে' if COOKIES_SRC else 'নেই'}\n🔧 yt-dlp: {ver}")
    elif cmd == "/update":
        if CUR[0] or not Q.empty():
            say("⚠️ ডাউনলোড চলছে বা লাইনে আছে। শেষ হলে আবার /update দিন।")
            return
        say("🔄 yt-dlp আপডেট হচ্ছে, বট রিস্টার্ট হবে (১-২ মিনিট)...")
        pip_update()
        save()
        with contextlib.suppress(Exception):
            tg("getUpdates", _timeout=15, offset=OFFSET[0], timeout=0)  # এই কমান্ড "পড়া হয়েছে" জানানো, নইলে রিস্টার্ট লুপ হবে
        os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        vid = extract_vid(text)
        if vid:
            Q.put(("link", vid, user_name, user_id))
            say("⏳ ইউটিউব লিংক শনাক্ত হয়েছে, ডাউনলোডের লাইনে যোগ করা হলো।")


def handle_cb(cb):
    try:
        user = cb.get("from", {})
        user_name = user.get("first_name", "Admin")
        user_id = user.get("id", CHAT)
        ok = str(user_id) == str(CHAT) or str(cb.get("message", {}).get("chat", {}).get("id", "")) == str(CHAT)
        if not ok:
            call("answerCallbackQuery", callback_query_id=cb["id"], text="⛔ শুধু অ্যাডমিন!")
            return
        _, vid, mode = cb["data"].split("|")
        Q.put(("manual", vid, mode, user_name, user_id))
        call("answerCallbackQuery", callback_query_id=cb["id"], text="⏳ ভিডিওটি ডাউনলোডের লাইনে যোগ হয়েছে।")
    except Exception as e:
        print("cb:", e)


def commands():
    try:
        tg("deleteWebhook", _timeout=30, drop_pending_updates="false")
    except Exception as e:
        print("deleteWebhook:", e)
    conflicts = 0
    while True:
        try:
            r = tg("getUpdates", _timeout=45, timeout=25, offset=OFFSET[0],
                   allowed_updates=json.dumps(["message", "callback_query"]))
            conflicts = 0
            for u in r.get("result", []):
                OFFSET[0] = u["update_id"] + 1
                if u.get("callback_query"):
                    handle_cb(u["callback_query"])
                    continue
                m = u.get("message") or {}
                text = (m.get("text") or "").strip()
                chat = str(m.get("chat", {}).get("id", ""))
                user = m.get("from", {})
                user_name = user.get("first_name", "Admin")
                user_id = user.get("id", chat)
                if chat != str(CHAT) or not text:
                    continue
                try:
                    handle(text, user_name, user_id)
                except Exception as e:
                    say(f"⚠️ এরর: {html.escape(clean_err(e))[:300]}")
        except Exception as e:
            if "HTTP 409" in str(e):
                # একই টোকেনে অন্য কোথাও (Replit/পুরনো Render ইনস্ট্যান্স) বট চলছে
                conflicts += 1
                if conflicts == 1 or conflicts % 30 == 0:
                    print("⚠️ 409 Conflict: এই বট টোকেন অন্য কোথাও চলছে। অন্য জায়গার বট বন্ধ করুন।")
                time.sleep(10)
            else:
                print("cmd:", e)
                time.sleep(5)


# ───────────────────────── অটো চেকার ─────────────────────────
def announce_new(ch, vid, title):
    st = ""
    try:
        st = notify(vid, title, ch["name"])
    except Exception as e:
        print("notify:", e)
    if st in ("is_live", "is_upcoming", "post_live"):
        D["wait"][vid] = {"cid": ch["id"], "title": title, "t": time.time()}
    else:
        Q.put(("video", ch["id"], vid, title, "Auto Checker", CHAT))


def checker():
    while True:
        for ch in list(D["channels"]):
            if ch.get("paused"):
                continue
            try:
                _, items = feed(ch["id"])
                seen = D["seen"].setdefault(ch["id"], [])
                new = [(v, t, s) for v, t, s in items if v not in seen]
                for v, t, s in reversed(new):
                    remember(ch["id"], v)
                    if allowed(ch, t, s):
                        announce_new(ch, v, t)
                if new:
                    save()
            except Exception as e:
                print("check:", clean_err(e)[:200])
                time.sleep(20)
        time.sleep(max(30, int(D.get("interval") or 30)))


def waiter():
    while True:
        time.sleep(60)
        for vid, w in list(D["wait"].items()):
            try:
                if time.time() - w["t"] > 3 * 86400:
                    D["wait"].pop(vid, None)
                    continue
                st = info(vid)[3]
                if st in ("was_live", "not_live"):
                    D["wait"].pop(vid, None)
                    if chan(w["cid"]):
                        Q.put(("video", w["cid"], vid, w["title"], "Waiter System", CHAT))
                    save()
            except Exception as e:
                print("wait:", e)


# ───────────────────────── স্টার্ট ─────────────────────────
if not D["channels"] and FIRST:
    try:
        first_id = resolve(FIRST) or FIRST
        name, items = feed(first_id)
        D["channels"].append({"id": first_id, "name": name})
        D["seen"][first_id] = [v for v, _, _ in items]
        save()
    except Exception as e:
        print("FIRST channel:", e)

set_bot_menu()

threading.Thread(target=checker, daemon=True).start()
threading.Thread(target=commands, daemon=True).start()
threading.Thread(target=waiter, daemon=True).start()
print("✅ বট চালু হয়েছে", flush=True)

while True:
    job = Q.get()
    CUR[0] = describe(job)
    try:
        if job[0] == "video":
            ch = chan(job[1])
            if ch:
                run_ch(ch, job[2], job[3])
                backup()
        elif job[0] == "old":
            old(job[1], job[2])
        elif job[0] == "manual":
            vid, mode, u_name, u_id = job[1], job[2], job[3], job[4]
            t_info = TITLES.get(vid)
            t = t_info[0] if t_info else (info(vid)[2] or vid)
            n = t_info[1] if t_info else "YouTube"
            process(vid, t, n, False, quality=int(mode) if mode.isdigit() else None, audio=(mode == "mp3"), user_name=u_name, user_id=u_id)
            save()
        elif job[0] == "link":
            vid, u_name, u_id = job[1], job[2], job[3]
            t = info(vid)[2] or f"Video {vid}"
            process(vid, t, "Direct Link", False, user_name=u_name, user_id=u_id)
            save()
        elif job[0] == "backup":
            backup()
    except Exception as e:
        print("job error:", clean_err(e))
        say(f"⚠️ এরর: {html.escape(clean_err(e))[:300]}")
    finally:
        CUR[0] = ""
