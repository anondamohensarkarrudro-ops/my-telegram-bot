import os, re, time, math, glob, json, html, shutil, subprocess, threading, queue
import urllib.request, urllib.parse
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession

TOKEN = os.environ["TOKEN"]
CHAT = os.environ["CHAT"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION"]
TARGET = int(os.environ["TARGET"])
FIRST = os.environ.get("CHANNEL", "")
DIR = "./downloads"
LIMIT = 1900 * 1024 * 1024
API = f"https://api.telegram.org/bot{TOKEN}/"
UA = {"User-Agent": "Mozilla/5.0", "Cookie": "CONSENT=YES+1"}
START = time.time()
os.makedirs(DIR, exist_ok=True)

app = Flask(__name__)
@app.route("/")
def home():
    return "alive"
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000))), daemon=True).start()

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
client.start()
client.loop.run_until_complete(client.get_dialogs())

lock = threading.Lock()
Q = queue.Queue()
CUR = [""]
TITLES = {}
last_backup = [None]
D = {"channels": [], "seen": {}, "interval": 5, "quality": 720, "tag": "", "sent": 0, "last": "", "wait": {}}

HELP = ("🤖 <b>কমান্ড তালিকা</b>\n\n"
        "<b>চ্যানেল</b>\n"
        "/add লিংক — চ্যানেল যোগ (লিংক, @handle বা UC আইডি)\n"
        "/list — চ্যানেল তালিকা\n"
        "/ch নম্বর — চ্যানেলের সেটিং দেখা\n"
        "/remove নম্বর — চ্যানেল বাদ\n"
        "/pause নম্বর — চ্যানেল পজ\n"
        "/resume নম্বর — আবার চালু\n"
        "/old নম্বর সংখ্যা — পুরনো ভিডিও ডাউনলোড (যেমন /old 1 5)\n\n"
        "<b>চ্যানেলভিত্তিক সেটিং</b> (/set নম্বর অপশন মান)\n"
        "/set 1 include Full Episode, Promo — শুধু এই শব্দ থাকলে নেবে\n"
        "/set 1 exclude Trailer — এই শব্দ থাকলে বাদ\n"
        "/set 1 noshorts on — Shorts বাদ (on/off)\n"
        "/set 1 quality 480 — এই চ্যানেলের কোয়ালিটি (auto দিলে সাধারণ কোয়ালিটি)\n"
        "/set 1 audio on — শুধু MP3 (on/off)\n"
        "/set 1 prefix [Star Jalsha] — ক্যাপশনের শুরুতে লেখা\n"
        "(কোনো সেটিং মুছতে মান হিসেবে - দিন)\n\n"
        "<b>সাধারণ</b>\n"
        "/tag #serial — সব ক্যাপশনের শেষে ট্যাগ (মুছতে /tag -)\n"
        "/interval সেকেন্ড — চেকের সময় (কমপক্ষে ৫)\n"
        "/quality 360, 480, 720 বা 1080 — সাধারণ কোয়ালিটি\n"
        "/queue — লাইনে কী কী আছে\n"
        "/status — বটের অবস্থা")

def save():
    with lock:
        with open("data.json", "w") as f:
            json.dump(D, f)

async def restore():
    async for m in client.iter_messages(TARGET, search="#BACKUP", limit=5):
        if m.document:
            await m.download_media("data.json")
            return m.id

try:
    last_backup[0] = client.loop.run_until_complete(restore())
    if os.path.exists("data.json"):
        D.update(json.load(open("data.json")))
except Exception as e:
    print("restore", e)

def call(method, **kw):
    return urllib.request.urlopen(API + method, urllib.parse.urlencode(kw).encode(), timeout=60)

def say(text, chat=None, markup=None):
    try:
        kw = {"chat_id": chat or CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup:
            kw["reply_markup"] = markup
        call("sendMessage", **kw)
    except Exception as e:
        print(e)

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "ignore")

def feed(cid):
    xml = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    name = html.unescape(re.search(r"<title>(.*?)</title>", xml).group(1))
    items = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        v = re.search(r"<yt:videoId>(.*?)</yt:videoId>", e).group(1)
        t = html.unescape(re.search(r"<title>(.*?)</title>", e).group(1))
        lk = re.search(r'<link rel="alternate" href="([^"]+)"', e)
        items.append((v, t, bool(lk and "/shorts/" in lk.group(1))))
    return name, items

def resolve(text):
    text = text.strip()
    m = re.search(r"UC[\w-]{22}", text)
    if m:
        return m.group(0)
    if text.startswith("http"):
        url = text
    else:
        url = "https://www.youtube.com/" + (text if text.startswith("@") else "@" + text)
    page = get(url)
    for pat in (r'rel="canonical" href="https://www.youtube.com/channel/(UC[\w-]{22})',
                r'"externalId":"(UC[\w-]{22})"',
                r'"channelId":"(UC[\w-]{22})"'):
        m = re.search(pat, page)
        if m:
            return m.group(1)
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
        r = subprocess.run(["yt-dlp", "-J", "--no-playlist", f"https://youtu.be/{vid}"], capture_output=True, text=True, timeout=90)
        j = json.loads(r.stdout)
        hs = sorted({f["height"] for f in j.get("formats", []) if f.get("height") and f.get("vcodec") != "none"})
        return hs, j.get("duration"), j.get("title") or "", j.get("live_status") or ""
        return hs, j.get("duration"), j.get("title") or "", j.get("live_status") or ""
    except Exception:
        return [], None, "", ""


def notify(vid, title, name):
    hs, dur, _, st = info(vid)
    if len(TITLES) > 300:
        TITLES.pop(next(iter(TITLES)))
    res = " • ".join(f"{h}p" for h in hs) or "জানা যায়নি"
    d = time.strftime("%H:%M:%S", time.gmtime(dur)) if dur else "—"
    note = {"is_live": "🔴 লাইভ চলছে — শেষ হলে অটো ডাউনলোড হবে",
            "is_upcoming": "🕒 লাইভ/প্রিমিয়ার আসছে — শেষ হলে অটো ডাউনলোড হবে",
            "post_live": "⏳ লাইভ শেষ, প্রসেস হচ্ছে — একটু পরে ডাউনলোড হবে"}.get(st, "⏳ ডাউনলোড শুরু হচ্ছে...")
    cap = (f"🎬 <b>{html.escape(title[:300])}</b>\n📺 {html.escape(name)}\n"
           f"🎞 Available: {res}\n⏱ {d}\n🔗 <code>https://youtu.be/{vid}</code>\n{note}")
    kb = json.dumps({"inline_keyboard": [
        [{"text": "⬇️ 360p", "callback_data": f"dl|{vid}|360"},
         {"text": "⬇️ 720p", "callback_data": f"dl|{vid}|720"},
         {"text": "⬇️ 1080p", "callback_data": f"dl|{vid}|1080"}],
        [{"text": "🎵 MP3", "callback_data": f"dl|{vid}|mp3"}]]})
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


def split(path):
    size = os.path.getsize(path)
    if size <= LIMIT:
        return [path]
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))
    n = math.ceil(size / LIMIT) + 1
    base = path.rsplit(".", 1)[0]
    subprocess.run(["ffmpeg", "-i", path, "-c", "copy", "-map", "0", "-f", "segment", "-segment_time", str(int(dur / n)), "-reset_timestamps", "1", f"{base}_part%02d.mp4"])
    os.remove(path)
    return sorted(glob.glob(base + "_part*.mp4"))

def process(vid, title, name, announce=False, quality=None, audio=False, prefix=""):
    if announce:
        notify(vid, title, name)
    qh = quality or D["quality"]
    url = f"https://youtu.be/{vid}"
    if audio:
        path = f"{DIR}/{vid}.mp3"
        cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", f"{DIR}/{vid}.%(ext)s", url]
    else:
        path = f"{DIR}/{vid}.mp4"
        cmd = ["yt-dlp", "-f", f"bv*[height<={qh}]+ba/b[height<={qh}]", "--merge-output-format", "mp4",
               "--remux-video", "mp4", "-o", f"{DIR}/{vid}.%(ext)s", url]
    try:
        r = None
        for attempt in range(3):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(path):
                break
            time.sleep(10)
        else:
            err = (r.stderr.strip().splitlines() or ["অজানা এরর"])[-1]
            say(f"❌ ৩ বার চেষ্টা করেও ডাউনলোড হয়নি: {html.escape(title[:200])}\n{html.escape(err[:300])}")
            return
        parts = [path] if audio else split(path)
        for i, p in enumerate(parts, 1):
            cap = (prefix + " " if prefix else "") + ("🎵 " if audio else "🎬 ") + f"{title}\n📺 {name}"
            if len(parts) > 1:
                cap += f"\n📦 Part {i}/{len(parts)}"
            if D.get("tag"):
                cap += "\n" + D["tag"]
            msg = client.loop.run_until_complete(client.send_file(TARGET, p, caption=cap[:1000], supports_streaming=not audio))
            try:
                call("copyMessage", chat_id=CHAT, from_chat_id=TARGET, message_id=msg.id)
            except Exception as e:
                print("copy", e)
        D["sent"] = D.get("sent", 0) + 1
        D["last"] = title[:80]
        say(f"✅ পাঠানো হয়েছে: {html.escape(title[:200])}")
    finally:
        for f in glob.glob(f"{DIR}/{vid}*"):
            try:
                os.remove(f)
            except Exception:
                pass

def run_ch(ch, vid, title, announce=False):
    process(vid, title, ch["name"], announce, quality=ch.get("quality"), audio=ch.get("audio", False), prefix=ch.get("prefix", ""))

def old(cid, n):
    ch = chan(cid)
    if not ch:
        return
    r = subprocess.run(["yt-dlp", "--flat-playlist", "--playlist-end", str(n), "--print", "%(id)s|%(title)s",
                        f"https://www.youtube.com/channel/{cid}/videos"], capture_output=True, text=True, timeout=180)
    items = [l.split("|", 1) for l in r.stdout.splitlines() if "|" in l]
    if not items:
        say("❌ ভিডিওর তালিকা পাওয়া যায়নি।")
        return
    say(f"📥 {len(items)}টা পুরনো ভিডিও ডাউনলোড শুরু হচ্ছে...")
    for vid, title in reversed(items):
        run_ch(ch, vid, title, True)
        remember(cid, vid)
    backup()

def backup():
    save()
    msg = client.loop.run_until_complete(client.send_file(TARGET, "data.json", caption="#BACKUP"))
    old_id = last_backup[0]
    last_backup[0] = msg.id
    if old_id:
        try:
            client.loop.run_until_complete(client.delete_messages(TARGET, [old_id]))
        except Exception as e:
            print(e)

def describe(job):
    if job[0] == "video":
        return "🎬 " + job[3][:50]
    if job[0] == "old":
        return f"📥 পুরনো {job[2]}টা ভিডিও"
    if job[0] == "manual":
        return f"⬇️ {(TITLES.get(job[1]) or (job[1],))[0][:40]} ({job[2]})"
    return "💾 ব্যাকআপ"

def handle(text):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    chs = D["channels"]
    if cmd in ("/start", "/help"):
        say(HELP)
    elif cmd == "/add":
        if not arg:
            say("ব্যবহার: /add চ্যানেল-লিংক বা @handle")
            return
        cid = resolve(arg)
        if not cid:
            say("❌ চ্যানেল পাওয়া যায়নি।")
            return
        if chan(cid):
            say("এটা আগেই যোগ করা আছে।")
            return
        name, items = feed(cid)
        chs.append({"id": cid, "name": name})
        D["seen"][cid] = [v for v, _, _ in items]
        save()
        Q.put(("backup",))
        say(f"✅ যোগ হয়েছে: <b>{html.escape(name)}</b>\nনতুন ভিডিও এলে অটো আসবে।\nপুরনো ভিডিও চাইলে: /old {len(chs)} 5")
    elif cmd == "/list":
        if not chs:
            say("কোনো চ্যানেল নেই। /add দিয়ে যোগ করুন।")
            return
        rows = "\n".join(f"{i}. {'⏸ ' if c.get('paused') else ''}{html.escape(c['name'])}" for i, c in enumerate(chs, 1))
        say(f"📺 চ্যানেল তালিকা:\n{rows}\n\n⏱ চেক: প্রতি {D['interval']} সেকেন্ড | 🎞 {D['quality']}p")
    elif cmd == "/ch":
        c = pick(arg)
        if not c:
            say("ব্যবহার: /ch নম্বর")
            return
        say(f"⚙️ <b>{html.escape(c['name'])}</b>\n"
            f"অবস্থা: {'⏸ পজ' if c.get('paused') else '▶️ চালু'}\n"
            f"কোয়ালিটি: {c.get('quality') or 'auto'}\n"
            f"শুধু MP3: {'হ্যাঁ' if c.get('audio') else 'না'}\n"
            f"Shorts বাদ: {'হ্যাঁ' if c.get('noshorts') else 'না'}\n"
            f"include: {html.escape(', '.join(c.get('include') or []) or '—')}\n"
            f"exclude: {html.escape(', '.join(c.get('exclude') or []) or '—')}\n"
            f"prefix: {html.escape(c.get('prefix') or '—')}")
    elif cmd == "/remove":
        c = pick(arg)
        if not c:
            say("ব্যবহার: /remove নম্বর (নম্বর দেখতে /list)")
            return
        chs.remove(c)
        D["seen"].pop(c["id"], None)
        save()
        Q.put(("backup",))
        say(f"🗑 বাদ দেওয়া হয়েছে: {html.escape(c['name'])}")
    elif cmd in ("/pause", "/resume"):
        c = pick(arg)
        if not c:
            say(f"ব্যবহার: {cmd} নম্বর")
            return
        c["paused"] = cmd == "/pause"
        save()
        Q.put(("backup",))
        say(f"{'⏸ পজ' if c['paused'] else '▶️ চালু'}: {html.escape(c['name'])}")
    elif cmd == "/old":
        a = arg.split()
        if len(a) < 2 or not a[1].isdigit() or not pick(a[0]):
            say("ব্যবহার: /old নম্বর সংখ্যা (যেমন /old 1 5)")
            return
        Q.put(("old", pick(a[0])["id"], min(int(a[1]), 50)))
        say("⏳ লাইনে যোগ হয়েছে, শীঘ্রই শুরু হবে।")
    elif cmd == "/set":
        a = arg.split(None, 2)
        c = pick(a[0]) if a else None
        if not c or len(a) < 3:
            say("ব্যবহার: /set নম্বর অপশন মান\nঅপশন: include, exclude, noshorts, quality, audio, prefix")
            return
        opt, val = a[1].lower(), a[2].strip()
        clear = val == "-"
        if opt in ("include", "exclude"):
            c[opt] = [] if clear else [w.strip() for w in val.split(",") if w.strip()]
        elif opt in ("noshorts", "audio"):
            c[opt] = val.lower() in ("on", "yes", "1")
        elif opt == "quality":
            if val not in ("360", "480", "720", "1080", "auto"):
                say("কোয়ালিটি: 360, 480, 720, 1080 বা auto")
                return
            c["quality"] = None if val == "auto" else int(val)
        elif opt == "prefix":
            c["prefix"] = "" if clear else val
        else:
            say("অপশন: include, exclude, noshorts, quality, audio, prefix")
            return
        save()
        Q.put(("backup",))
        say(f"✅ {html.escape(c['name'])}: {opt} সেট হয়েছে। দেখতে /ch {chs.index(c) + 1}")
    elif cmd == "/tag":
        if not arg:
            say(f"বর্তমান ট্যাগ: {html.escape(D.get('tag') or 'নেই')}")
            return
        D["tag"] = "" if arg == "-" else arg
        save()
        Q.put(("backup",))
        say("✅ ট্যাগ সেট হয়েছে।")
    elif cmd == "/interval":
        if not arg.isdigit():
            say("ব্যবহার: /interval সেকেন্ড")
            return
        D["interval"] = max(5, int(arg))
        save()
        Q.put(("backup",))
        say(f"⏱ এখন প্রতি {D['interval']} সেকেন্ডে চেক হবে।")
    elif cmd == "/quality":
        if arg not in ("360", "480", "720", "1080"):
            say("ব্যবহার: /quality 360, 480, 720 বা 1080")
            return
        D["quality"] = int(arg)
        save()Q.put(("backup",))
        say(f"🎞 কোয়ালিটি এখন {arg}p।")
    elif cmd == "/queue":
        jobs = [describe(j) for j in list(Q.queue) if j[0] != "backup"]
        txt = f"🔄 এখন: {html.escape(CUR[0]) or 'কিছু না'}\n📥 লাইনে {len(jobs)}টা"
        if jobs:
            txt += "\n" + "\n".join(f"{i}. {html.escape(j)}" for i, j in enumerate(jobs[:10], 1))
        say(txt)
    elif cmd == "/status":
        s = int(time.time() - START)
        free = shutil.disk_usage(DIR).free / 1e9
        paused = sum(1 for c in chs if c.get("paused"))
        say(f"📊 <b>স্ট্যাটাস</b>\n⏱ চালু: {s // 3600} ঘণ্টা {s % 3600 // 60} মিনিট\n"
            f"📺 চ্যানেল: {len(chs)} (পজ: {paused})\n"
            f"📥 লাইনে: {sum(1 for j in list(Q.queue) if j[0] != 'backup')}টা\n"
            f"🔄 এখন: {html.escape(CUR[0]) or 'কিছু না'}\n"
            f"🕒 লাইভ/প্রিমিয়ারের অপেক্ষায়: {len(D['wait'])}টা\n"
            f"✅ মোট পাঠানো: {D.get('sent', 0)}টা\n"
            f"🎬 শেষ: {html.escape(D.get('last') or '—')}\n"
            f"💾 ডিস্ক ফাঁকা: {free:.1f} GB")

def handle_cb(cb):
    try:
        ok = str(cb["from"]["id"]) == str(CHAT) or str(cb.get("message", {}).get("chat", {}).get("id", "")) == str(CHAT)
        if not ok:
            call("answerCallbackQuery", callback_query_id=cb["id"], text="⛔ শুধু অ্যাডমিন")
            return
        _, vid, mode = cb["data"].split("|")
        Q.put(("manual", vid, mode))
        call("answerCallbackQuery", callback_query_id=cb["id"], text="⏳ লাইনে যোগ হয়েছে")
    except Exception as e:
        print("cb", e)

def commands():
    offset = 0
    while True:
        try:
            r = urllib.request.urlopen(API + f"getUpdates?timeout=25&offset={offset}", timeout=40).read().decode()
            for u in json.loads(r)["result"]:
                offset = u["update_id"] + 1
                if u.get("callback_query"):
                    handle_cb(u["callback_query"])
                    continue
                m = u.get("message") or {}
                text = (m.get("text") or "").strip()
                chat = str(m.get("chat", {}).get("id", ""))
                if not text.startswith("/"):
                    continue
                if chat != str(CHAT):
                    say("⛔ এই বট শুধু অ্যাডমিনের জন্য।", chat)
                    continue
                try:
                    handle(text)
                except Exception as e:
                    say(f"⚠️ এরর: {html.escape(str(e))[:300]}")
        except Exception as e:
            print("cmd", e)
            time.sleep(5)

def announce_new(ch, vid, title):
    st = ""
    try:
        st = notify(vid, title, ch["name"])
    except Exception as e:
        print("notify", e)
    if st in ("is_live", "is_upcoming", "post_live"):
        D["wait"][vid] = {"cid": ch["id"], "title": title, "t": time.time()}
    else:
        Q.put(("video", ch["id"], vid, title))

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
                print("check", e)
                time.sleep(20)
        time.sleep(D["interval"])

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
                        Q.put(("video", w["cid"], vid, w["title"]))
                    save()
            except Exception as e:
                print("wait", e)

if not D["channels"] and FIRST:
    try:
        name, items = feed(FIRST)
        D["channels"].append({"id": FIRST, "name": name})
        D["seen"][FIRST] = [v for v, _, _ in items]
        save()
    except Exception as e:
        print(e)

threading.Thread(target=checker, daemon=True).start()
threading.Thread(target=commands, daemon=True).start()
threading.Thread(target=waiter, daemon=True).start()

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
            vid, mode = job[1], job[2]
            t, n = TITLES.get(vid) or (None, "")
            if not t:
                t = info(vid)[2] or vid
            process(vid, t, n, False, quality=int(mode) if mode.isdigit() else None, audio=(mode == "mp3"))
            save()
        elif job[0] == "backup":
            backup()
    except Exception as e:
        print(e)
        say(f"⚠️ এরর: {html.escape(str(e))[:300]}")
    finally:
        CUR[0] = ""
