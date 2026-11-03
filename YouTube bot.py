import os, re, time, math, glob, subprocess, threading
import urllib.request, urllib.parse
from flask import Flask
from telethon import TelegramClient
from telethon.sessions import StringSession

TOKEN = os.environ["TOKEN"]
CHAT = os.environ["CHAT"]
CHANNEL = os.environ["CHANNEL"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION"]
TARGET = int(os.environ["TARGET"])
DIR = "./downloads"
LIMIT = 1900 * 1024 * 1024
FEED = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}"
API = f"https://api.telegram.org/bot{TOKEN}/"
os.makedirs(DIR, exist_ok=True)

app = Flask(__name__)
@app.route("/")
def home():
    return "alive"
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000))), daemon=True).start()

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
client.start()
client.loop.run_until_complete(client.get_dialogs())

def call(method, **kw):
    return urllib.request.urlopen(API + method, urllib.parse.urlencode(kw).encode())

def notify(vid, title):
    cap = f"🎬 {title}\n🔗 https://youtu.be/{vid}\n⏳ ডাউনলোড শুরু..."
    for q in ("maxresdefault", "hqdefault"):
        try:
            call("sendPhoto", chat_id=CHAT, caption=cap, photo=f"https://i.ytimg.com/vi/{vid}/{q}.jpg")
            return
        except Exception:
            continue
    call("sendMessage", chat_id=CHAT, text=cap)

def videos():
    xml = urllib.request.urlopen(FEED).read().decode()
    ids = re.findall(r"<yt:videoId>(.*?)</yt:videoId>", xml)
    titles = re.findall(r"<media:title>(.*?)</media:title>", xml)
    return list(zip(ids, titles))

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

seen = set(v for v, _ in videos())

while True:
    try:
        for vid, title in videos():
            if vid in seen:
                continue
            seen.add(vid)
            notify(vid, title)
            subprocess.run(["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]", "--merge-output-format", "mp4", "-o", f"{DIR}/{vid}.%(ext)s", f"https://youtu.be/{vid}"])
            for p in split(f"{DIR}/{vid}.mp4"):
                client.loop.run_until_complete(client.send_file(TARGET, p, caption=title, supports_streaming=True))
                os.remove(p)
            call("sendMessage", chat_id=CHAT, text=f"✅ পাঠানো হয়েছে: {title}")
    except Exception as e:
        print(e)
    time.sleep(300)
