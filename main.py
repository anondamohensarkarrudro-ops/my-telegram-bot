import os, re, time, math, glob, json, html, shutil, subprocess, threading, queue
import urllib.request, urllib.parse
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession
import yt_dlp

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
        "• exclude [keyword] — বাদ দেওয়ার শব্দ\n"
        "• noshorts [on/off] — শর্টস ফিল্টার\n"
        "• quality [360/480/720/1080/auto] — কোয়ালিটি\n"
        "• audio [on/off] — শুধু MP3\n"
        "• prefix [Text] — ক্যাপশন প্রিফিক্স\n"
        "• ctag [Name] — ফাইলের শেষে ও ট্যাগের নাম (যেমন: Alamin)\n\n"
        "🛠 <b>সাধারণ কমান্ড:</b>\n"
        "• /tag [#tag] — গ্লোবাল ট্যাগ\n"
        "• /dl [link] — সরাসরি লিংক থেকে ডাউনলোড\n"
        "• /queue — লাইনের তালিকা\n"
        "• /status — স্ট্যাটাস")

def set_bot_menu():
    commands = [
        {"command": "start", "description": "🚀 বট শুরু করুন"},
        {"command": "add", "description": "➕ চ্যানেল যোগ করুন"},
        {"command": "list", "description": "📺 চ্যানেল তালিকা"},
        {"command": "ch", "description": "⚙️ চ্যানেল সেটিংস"},
        {"command": "dl", "description": "⬇️ লিংক থেকে ডাউনলোড"},
        {"command": "queue", "description": "🔄 ডাউনলোড কিউ"},
        {"command": "status", "description": "📊 বট স্ট্যাটাস"}
    ]
    try:
        urllib.request.urlopen(API + "setMyCommands", urllib.parse.urlencode({"commands": json.dumps(commands)}).encode(), timeout=30)
    except Exception as e:
        print("Menu error:", e)

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
        res = urllib.request.urlopen(API + "sendMessage", urllib.parse.urlencode(kw).encode(), timeout=60).read().decode()
        return json.loads(res)["result"]["message_id"]
    except Exception as e:
        print(e)
        return None

def edit(text, msg_id, chat=None, markup=None):
    try:
        kw = {"chat_id": chat or CHAT, "message_id": msg_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup:
            kw["reply_markup"] = markup
        urllib.request.urlopen(API + "editMessageText", urllib.parse.urlencode(kw).encode(), timeout=60)
    except Exception as e:
        print("edit error:", e)

def send_error_log(user_name, user_id, channel_name, link, error_msg):
    log_text = (
        f"🚨 <b>MJ Bot Error Logs</b>\n"
        f"<b>Download Error</b>\n\n"
        f"👤 <b>User:</b> {html.escape(str(user_name))}\n"
        f"🆔 <b>UserID:</b> <code>{user_id}</code>\n"
        f"📺 <b>Channel/Source:</b> {html.escape(str(channel_name))}\n\n"
        f"🔗 <b>Link:</b> {link}\n\n"
        f"⚠️ <b>Context:</b> Video Download & Processing\n\n"
        f"<b>Error:</b>\n"
        f"<blockquote expandable>{html.escape(str(error_msg))}</blockquote>"
    )
    say(log_text, chat=TARGET)
    say(log_text, chat=CHAT)

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "ignore")

def feed(cid):
    url = f"https://www.youtube.com/channel/{cid}/videos"
    html_content = get(url)
    name_match = re.search(r"<title>(.*?)</title>", html_content)
    name = html.unescape(name_match.group(1).replace(" - YouTube", "")) if name_match else "Channel"
    items = []
    video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html_content)
    seen_local = set()
    for v in video_ids:
        if v not in seen_local:
            seen_local.add(v)
            title_match = re.search(rf'"videoId":"{v}".*?"title":{{"runs":\[{{"text":"(.*?)"}}', html_content)
            t = html.unescape(title_match.group(1)) if title_match else f"New Video {v}"
            is_short = f"/shorts/{v}" in html_content
            items.append((v, t, is_short))
            if len(items) >= 10:
                break
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

def extract_vid(text):
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', text)
    return m.group(1) if m else (text.strip() if len(text.strip()) == 11 else None)

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
    except Exception:
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

def split(path, custom_suffix=""):
    size = os.path.getsize(path)
    if size <= LIMIT:
        if custom_suffix:
            base, ext = path.rsplit(".", 1)
            clean_name = re.sub(r'[\\/*?:"<>|#]', "", base.replace(DIR + "/", ""))
            new_path = f"{DIR}/{clean_name}.{custom_suffix}.{ext}"
            os.rename(path, new_path)
            return [new_path]
        return [path]
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))
    n = math.ceil(size / LIMIT) + 1
    base = path.rsplit(".", 1)[0]
    subprocess.run(["ffmpeg", "-i", path, "-c", "copy", "-map", "0", "-f", "segment", "-segment_time", str(int(dur / n)), "-reset_timestamps", "1", f"{base}_part%02d.mp4"])
    os.remove(path)
    
    parts = sorted(glob.glob(base + "_part*.mp4"))
    if custom_suffix:
        new_parts = []
        for i, p in enumerate(parts, 1):
            p_base, p_ext = p.rsplit(".", 1)
            clean_name = re.sub(r'[\\/*?:"<>|#]', "", p_base.replace(DIR + "/", ""))
            new_p = f"{DIR}/{clean_name}.Part{i}.{custom_suffix}.{p_ext}"
            os.rename(p, new_p)
            new_parts.append(new_p)
        return new_parts
    return parts

def process(vid, title, name, announce=False, quality=None, audio=False, prefix="", ctag="", user_name="Admin", user_id=CHAT):
    if announce:
        notify(vid, title, name)
    qh = quality or D["quality"]
    url = f"https://youtu.be/{vid}"
    
    suffix_name = ctag.strip() if ctag else "YouTube"
    suffix_name = re.sub(r'[\\/*?:"<>|#]', "", suffix_name)
    
    if audio:
        path = f"{DIR}/{vid}.mp3"
    else:
        path = f"{DIR}/{vid}.mp4"
    
    msg_id = say(f"📥 <b>Downloading Video...</b>\n\n<b>Filename:</b> {html.escape(title[:100])}\n[--------------------] 0%")
    last_up = [0]
    
    def prog(d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - last_up[0] > 3:
                last_up[0] = now
                p_str = d.get('_percent_str', '0%').strip('%').strip()
                try:
                    p = float(p_str)
                except:
                    p = 0.0
                filled = int(p // 5)
                bar = "█" * filled + "-" * (20 - filled)
                s_size = d.get('_downloaded_bytes_str', '0B')
                t_size = d.get('_total_bytes_str', d.get('_total_bytes_estimate_str', 'Unknown'))
                speed = d.get('_speed_str', '0B/s')
                eta = d.get('_eta_str', '0s')
                txt = (f"📥 <b>Downloading Video...</b>\n\n"
                       f"<b>Filename:</b> {html.escape(title[:100])}\n"
                       f"[{bar}] {p:.1f}%\n\n"
                       f"• <b>Size:</b> {s_size} / {t_size}\n"
                       f"• <b>Speed:</b> {speed}\n"
                       f"• <b>ETA:</b> {eta}")
                if msg_id:
                    edit(txt, msg_id)

    try:
        ydl_opts = {
            'format': f'bv*[height<={qh}]+ba/b[height<={qh}]' if not audio else 'bestaudio',
            'outtmpl': f'{DIR}/{vid}.%(ext)s',
            'progress_hooks': [prog]
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        if not os.path.exists(path):
            files = glob.glob(f"{DIR}/{vid}.*")
            if files:
                path = files[0]

        if not os.path.exists(path):
            raise Exception("Downloaded file not found or download failed.")

        safe_title = re.sub(r'[\\/*?:"<>|#]', "", title.strip()).replace(" ", ".")
        resolution_tag = f"{qh}p.YouTube" if not audio else "Audio.YouTube"
        final_suffix = f"{resolution_tag}.{suffix_name}"
        
        base_renamed_path = f"{DIR}/{safe_title}"
        if audio:
            final_path = f"{base_renamed_path}.{final_suffix}.mp3"
            if os.path.exists(path):
                os.rename(path, final_path)
            parts = [final_path]
        else:
            temp_renamed = f"{base_renamed_path}.mp4"
            if os.path.exists(path):
                os.rename(path, temp_renamed)
            parts = split(temp_renamed, custom_suffix=final_suffix)

        total_parts = len(parts)
        
        for i, p in enumerate(parts, 1):
            file_base_name = os.path.basename(p)
            cap = (prefix + " " if prefix else "") + ("🎵 " if audio else "🎬 ") + f"{title}\n📺 {name}\n📁 <code>{html.escape(file_base_name)}</code>"
            if total_parts > 1:
                cap += f"\n📦 Part {i}/{total_parts}"
            if ctag:
                cap += f"\n{ctag}"
            elif D.get("tag"):
                cap += f"\n{D['tag']}"
                
            up_start = time.time()
            up_last = [0]
            
            def tg_prog(current, total):
                now = time.time()
                if now - up_last[0] > 3:
                    up_last[0] = now
                    p_val = (current / total) * 100 if total > 0 else 0
                    filled = int(p_val // 5)
                    bar = "█" * filled + "-" * (20 - filled)
                    cur_mb = current / (1024 * 1024)
                    tot_mb = total / (1024 * 1024)
                    diff = now - up_start
                    spd = current / diff if diff > 0 else 1
                    spd_str = f"{spd / (1024*1024):.2f} MB/sec"
                    eta_sec = int((total - current) / spd) if spd > 0 else 0
                    if msg_id:
                        up_txt = (f"📤 <b>Uploading: {p_val:.1f}%</b>\n\n"
                                  f"[{bar}]\n"
                                  f"<b>{cur_mb:.1f} MB of {tot_mb:.1f} MB</b>\n"
                                  f"⚡ <b>Speed:</b> {spd_str}\n"
                                  f"⏳ <b>ETA:</b> {eta_sec}sec\n\n"
                                  f"📁 <b>Filename:</b> {html.escape(file_base_name)}")
                        edit(up_txt, msg_id)

            msg = client.loop.run_until_complete(client.send_file(TARGET, p, caption=cap[:1000], supports_streaming=not audio, progress_callback=tg_prog))
            try:
                call("copyMessage", chat_id=CHAT, from_chat_id=TARGET, message_id=msg.id)
            except Exception as e:
                print("copy", e)
                
        if msg_id:
            try:
                urllib.request.urlopen(API + f"deleteMessage?chat_id={CHAT}&message_id={msg_id}", timeout=30)
            except:
                pass
                
        D["sent"] = D.get("sent", 0) + 1
        D["last"] = title[:80]
        say(f"✅ সফলভাবে পাঠানো হয়েছে: {html.escape(title[:200])}")
    except Exception as e:
        err_text = str(e)
        print("process error:", err_text)
        if msg_id:
            edit(f"❌ ডাউনলোডে ব্যর্থ হয়েছে!", msg_id)
        send_error_log(user_name, user_id, name, f"https://youtu.be/{vid}", err_text)
    finally:
        for f in glob.glob(f"{DIR}/{vid}*"):
            try:
                os.remove(f)
            except:
                pass

def run_ch(ch, vid, title, announce=False):
    process(vid, title, ch["name"], announce, quality=ch.get("quality"), audio=ch.get("audio", False), prefix=ch.get("prefix", ""), ctag=ch.get("ctag", ""))

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
    say(f"📥 {len(items)}টি পুরনো ভিডিও ডাউনলোড শুরু হচ্ছে...")
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
        return f"📥 পুরনো {job[2]}টি ভিডিও"
    if job[0] == "manual":
        t_info = TITLES.get(job[1])
        t_name = t_info[0] if t_info else job[1]
        return f"⬇️ {t_name[:40]} ({job[2]})"
    if job[0] == "link":
        return f"🔗 লিংক ডাউনলোড: {job[1]}"
    return "💾 ব্যাকআপ"

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
            say("❌ চ্যানেলটি খুঁজে পাওয়া যায়নি।")
            return
        if chan(cid):
            say("⚠️ এই চ্যানেলটি ইতিপূর্বেই যোগ করা আছে।")
            return
        name, items = feed(cid)
        chs.append({"id": cid, "name": name})
        D["seen"][cid] = [v for v, _, _ in items]
        save()
        Q.put(("backup",))
        say(f"✅ সফলভাবে যোগ হয়েছে: <b>{html.escape(name)}</b>")
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
        say(f"🗑 বাদ দেওয়া হয়েছে: {html.escape(c['name'])}")
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
        say("⏳ পুরনো ভিডিও ডাউনলোডের লাইনে যোগ হয়েছে।")
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
            c[opt] = None if val == "auto" else int(val)
        elif opt in ("prefix", "ctag"):
            c[opt] = "" if clear else val
        else:
            say("⚠️ ভুল অপশন!")
            return
        save()
        Q.put(("backup",))
        say(f"✅ আপডেট হয়েছে ({opt})। দেখতে লিখুন: /ch {chs.index(c) + 1}")
    elif cmd == "/dl":
        vid = extract_vid(arg)
        if not vid:
            say("⚠️ সঠিক ইউটিউব লিংক দিন। ব্যবহার: /dl [link]")
            return
        Q.put(("link", vid, user_name, user_id))
        say("⏳ লিংকটি ডাউনলোডের লাইনে যোগ করা হয়েছে।")
    elif cmd == "/tag":
        if not arg:
            say(f"🏷 বর্তমান ট্যাগ: {html.escape(D.get('tag') or 'নেই')}")
            return
        D["tag"] = "" if arg == "-" else arg
        save()
        Q.put(("backup",))
        say("✅ গ্লোবাল ট্যাগ সেট করা হয়েছে।")
    elif cmd == "/queue":
        jobs = [describe(j) for j in list(Q.queue) if j[0] != "backup"]
        txt = f"🔄 <b>চলমান:</b> {html.escape(CUR[0]) or 'নেই'}\n📥 <b>লাইনে:</b> {len(jobs)}টি"
        if jobs:
            txt += "\n\n" + "\n".join(f"{i}. {html.escape(j)}" for i, j in enumerate(jobs[:10], 1))
        say(txt)
    elif cmd == "/status":
        s = int(time.time() - START)
        free = shutil.disk_usage(DIR).free / 1e9
        say(f"📊 <b>বট স্ট্যাটাস</b>\n⏱ রানিং: {s // 3600} ঘণ্টা\n📺 চ্যানেল: {len(chs)}টি\n✅ পাঠানো: {D.get('sent', 0)}টি\n💾 ডিস্ক ফাঁকা: {free:.1f} GB")
    else:
        vid = extract_vid(text)
        if vid:
            Q.put(("link", vid, user_name, user_id))
            say("⏳ ইউটিউব লিংক শনাক্ত হয়েছে, ডাউনলোডের লাইনে যোগ করা হলো।")

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
        call("answerCallbackQuery", callback_query_id=cb["id"], text="⏳ ভিডিওটি ডাউনলোডের লাইনে যোগ হয়েছে।")
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
                user = m.get("from", {})
                user_name = user.get("first_name", "Admin")
                user_id = user.get("id", chat)
                if chat != str(CHAT):
                    continue
                try:
                    handle(text, user_name, user_id)
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
                        Q.put(("video", w["cid"], vid, w["title"], "Waiter System", CHAT))
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

set_bot_menu()

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
            vid, mode, u_name, u_id = job[1], job[2], job[3], job[4]
            t_info = TITLES.get(vid)
            t = t_info[0] if t_info else (info(vid)[2] or vid)
            n = t_info[1] if t_info else "YouTube"
            process(vid, t, n, False, quality=int(mode) if mode.isdigit() else None, audio=(mode == "mp3"), user_name=u_name, user_id=u_id)
            save()
        elif job[0] == "link":
            vid, u_name, u_id = job[1], job[2], job[3]
            _, _, t, _ = info(vid)
            t = t or f"Video {vid}"
            process(vid, t, "Direct Link", False, user_name=u_name, user_id=u_id)
            save()
        elif job[0] == "backup":
            backup()
    except Exception as e:
        print(e)
        say(f"⚠️ এরর: {html.escape(str(e))[:300]}")
    finally:
        CUR[0] = ""
