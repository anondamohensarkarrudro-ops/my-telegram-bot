"""
📺 Auto Serial Bot  v3.0.0
────────────────────────────────────────────────────────────────
• লগ চ্যানেলে এডমিন ভিডিও আপলোড করে → বট নিজে সিরিয়াল/এপিসোড নম্বর/কোয়ালিটি চিনে ফলোয়ারদের পাঠায়
• শুধু ফলোর পরের নতুন এপিসোড যায়, পুরনো যায় না
• দ্রুত ডেলিভারি: কনকারেন্ট সেন্ডিং + টেলিগ্রাম লিমিট মেনে রেট কন্ট্রোল + WAL ডাটাবেস
"""
import asyncio
import difflib
import html
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton as B,
    InlineKeyboardMarkup as M,
    InlineQueryResultArticle,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaVideo,
    InputTextMessageContent,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    InlineQueryHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

load_dotenv()

# ================================================================ কনফিগ
BOT_NAME = "Auto Serial Bot"
VERSION = "3.0.0"
BOT_TOKEN = os.environ["BOT_TOKEN"]
LOG_CHANNEL_ID = int(os.environ["LOG_CHANNEL_ID"])
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
ADMIN_CONTACT = os.environ.get("ADMIN_CONTACT", "").strip()
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "").strip()
FREE_LIMIT_DEFAULT = int(os.environ.get("FREE_LIMIT", "3"))
SEND_RATE = float(os.environ.get("SEND_RATE", "25"))  # সেকেন্ডে মেসেজ (টেলিগ্রাম সীমা ~30)
TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET", "6"))))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "serialbot.db")

START_TIME = time.time()
RESTART = {"flag": False}
PLATFORMS = ["JioHotstar", "Zee5", "SunNXT", "SonyLIV", "Star Jalsha", "Zee Bangla", "Sun Bangla", "Sony Aath"]
QUALS = (480, 720, 1080)
ST_ICON = {"on": "🟢", "end": "✅", "brk": "⏸"}
ST_NAME = {"on": ("চলছে", "Running"), "end": ("শেষ", "Ended"), "brk": ("বিরতি", "On break")}
DOW_NAME = {-1: ("নেই", "None"), 4: ("শুক্রবার", "Friday"), 5: ("শনিবার", "Saturday"), 6: ("রবিবার", "Sunday")}

CHANGELOG = [
    (
        "3.0.0",
        "৩১-১০-২০২৬",
        [
            "⚡ স্পিড: কনকারেন্ট ডেলিভারি, WAL ডাটাবেস, ইনডেক্স, মেমরি ক্যাশ",
            "🏷 বটের নাম: Auto Serial Bot",
            "🔒 /updates শুধু এডমিনের জন্য",
            "🎬 অটো এপিসোড নম্বর ও কোয়ালিটি চেনা (E45 / Episode 45 / 720p)",
            "🎞 480p/720p/1080p কোয়ালিটি, প্ল্যান অনুযায়ী, গ্রুপিং উইন্ডো, এডমিন রিভিউ কার্ড",
            "⭐ /saved, 🕘 /history, 🔔 /notification, 📜 /terms, 🌐 বাংলা/English",
            "🔎 ভুল বানানেও সার্চ, ইনলাইন সার্চ, রিকোয়েস্ট, অ্যানালিটিক্স",
            "🎫 সাপোর্ট টিকেট, রেডিমেড উত্তর, 💳 পেমেন্ট রেকর্ড, 📊 পোল",
            "👑 এডমিন: ব্রডকাস্ট প্রিভিউ, /setwelcome, /setterms, /test, অ্যাকশন লগ + Undo",
        ],
    ),
    ("2.0.0", "৩০-১০-২০২৬", ["💎 প্ল্যান সিস্টেম, ⚙️ সেটিংস, 👑 অ্যাডমিন ড্যাশবোর্ড, ♻️ Bot Restart"]),
    ("1.0.0", "৩০-১০-২০২৬", ["📺 প্রথম রিলিজ: সিরিয়াল ফলো ও স্বয়ংক্রিয় এপিসোড ডেলিভারি"]),
]

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("auto-serial-bot")
_TASKS: set = set()


def spawn(coro):
    """ব্যাকগ্রাউন্ড টাস্ক (রেফারেন্স ধরে রাখে, এরর লগ করে)"""
    t = asyncio.ensure_future(coro)
    _TASKS.add(t)

    def done(x):
        _TASKS.discard(x)
        if not x.cancelled() and x.exception():
            log.error("background task failed", exc_info=x.exception())

    t.add_done_callback(done)
    return t


# ================================================================ ইউটিলিটি
def now() -> int:
    return int(time.time())


def esc(t) -> str:
    return html.escape(str(t if t is not None else ""))


def L(lang, bn, en=None):
    return en if (lang == "en" and en is not None) else bn


def local(ts=None) -> datetime:
    return datetime.fromtimestamp(ts if ts is not None else now(), TZ)


def day_start(ts=None) -> int:
    return int(local(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def fmt_ts(ts) -> str:
    return local(ts).strftime("%d-%m-%Y %I:%M %p") if ts and ts > 0 else "—"


def fmt_day(ts) -> str:
    return local(ts).strftime("%d %b %Y") if ts and ts > 0 else "—"


def fmt_ago(ts, lang="bn") -> str:
    if not ts:
        return "—"
    d = max(0, now() - ts)
    if d < 60:
        return L(lang, "এইমাত্র", "just now")
    if d < 3600:
        return L(lang, f"{d // 60} মিনিট আগে", f"{d // 60}m ago")
    if d < 86400:
        return L(lang, f"{d // 3600} ঘণ্টা আগে", f"{d // 3600}h ago")
    return L(lang, f"{d // 86400} দিন আগে", f"{d // 86400}d ago")


def fmt_td(sec: float) -> str:
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    return f"{d}দিন {h}ঘ {r // 60}মি" if d else f"{h}ঘ {r // 60}মি"


def fmt_size(b) -> str:
    b = int(b or 0)
    if b <= 0:
        return ""
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    return f"{max(1, round(b / (1 << 20)))} MB"


def bar(n: int, total: int, w: int = 6) -> str:
    if total <= 0:
        return "▓" * w
    k = min(w, int(round(w * n / total)))
    return "▓" * k + "░" * (w - k)


_BNDIG = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PUNCT = re.compile(r"[._\-|:;,\[\]\(\)\{\}#@!?\"'/\\+*=~`<>&^%$]+")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").translate(_BNDIG)
    t = t.replace("‌", "").replace("‍", "")
    t = _PUNCT.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().casefold()


_BN = {
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri", "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m", "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh",
    "স": "s", "হ": "h", "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t", "ং": "ng", "ঃ": "h", "ঁ": "n", "্": "", "়": "",
}


def romanize(s: str) -> str:
    """বাংলা → ল্যাটিন (ধ্বনিগত আনুমানিক), ইংরেজি হলে যেমন আছে তেমন"""
    s = unicodedata.normalize("NFC", s or "").translate(_BNDIG)
    for a, b in (("ড়", "ড়"), ("ঢ়", "ঢ়"), ("য়", "য়")):
        s = s.replace(a, b)
    r = "".join(_BN.get(ch, ch) for ch in s).lower()
    r = re.sub(r"[^a-z0-9 ]+", " ", r)
    return re.sub(r"\s+", " ", r).strip()


def skeleton(r: str) -> str:
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouyh ]", "", r))


def split_csv(s: str):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


# ================================================================ ডাটাবেস (WAL, অটোকমিট)
db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
db.row_factory = sqlite3.Row
for _p in ("journal_mode=WAL", "synchronous=NORMAL", "temp_store=MEMORY", "cache_size=-20000", "busy_timeout=5000"):
    db.execute(f"PRAGMA {_p}")


def Q(sql, *a):
    return db.execute(sql, a).fetchall()


def Q1(sql, *a):
    return db.execute(sql, a).fetchone()


def V(sql, *a, d=0):
    r = db.execute(sql, a).fetchone()
    return r[0] if r is not None and r[0] is not None else d


def X(sql, *a):
    return db.execute(sql, a)


def XM(sql, seq):
    seq = list(seq)
    if not seq:
        return
    db.execute("BEGIN")
    try:
        db.executemany(sql, seq)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS serials (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS follows (user_id INTEGER NOT NULL, serial_id INTEGER NOT NULL, PRIMARY KEY (user_id, serial_id));
CREATE TABLE IF NOT EXISTS serial_alias (serial_id INTEGER NOT NULL, alias_key TEXT NOT NULL, alias TEXT, roman TEXT, PRIMARY KEY (serial_id, alias_key));
CREATE TABLE IF NOT EXISTS eps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, serial_id INTEGER NOT NULL, ep_no INTEGER, title TEXT, tkey TEXT,
    created_at INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', publish_at INTEGER DEFAULT 0,
    published_at INTEGER DEFAULT 0, is_test INTEGER DEFAULT 0, review TEXT DEFAULT '', q_alerted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ep_files (
    episode_id INTEGER NOT NULL, quality INTEGER NOT NULL, message_id INTEGER NOT NULL, file_id TEXT, kind TEXT,
    size INTEGER DEFAULT 0, PRIMARY KEY (episode_id, quality));
CREATE TABLE IF NOT EXISTS posts_log (message_id INTEGER PRIMARY KEY, text TEXT, serial_id INTEGER, at INTEGER);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, episode_id INTEGER NOT NULL,
    sent_at INTEGER NOT NULL, quality INTEGER DEFAULT 0, watched INTEGER DEFAULT 0, watched_at INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, episode_id INTEGER NOT NULL,
    due_at INTEGER NOT NULL, kind TEXT DEFAULT 'ep', UNIQUE (user_id, episode_id));
CREATE TABLE IF NOT EXISTS saves (
    user_id INTEGER NOT NULL, episode_id INTEGER NOT NULL, folder TEXT DEFAULT '', note TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0, remind_at INTEGER DEFAULT 0, PRIMARY KEY (user_id, episode_id));
CREATE TABLE IF NOT EXISTS favs (user_id INTEGER NOT NULL, serial_id INTEGER NOT NULL, PRIMARY KEY (user_id, serial_id));
CREATE TABLE IF NOT EXISTS ratings (user_id INTEGER NOT NULL, episode_id INTEGER NOT NULL, val INTEGER, PRIMARY KEY (user_id, episode_id));
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, episode_id INTEGER, reason TEXT, status TEXT DEFAULT 'open', created_at INTEGER);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, status TEXT DEFAULT 'open', subject TEXT, created_at INTEGER, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS ticket_msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER, from_admin INTEGER DEFAULT 0, body TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS canned (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan TEXT, amount INTEGER DEFAULT 0, trx TEXT,
    status TEXT DEFAULT 'pending', created_at INTEGER, decided_at INTEGER DEFAULT 0, decided_by INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan TEXT, until INTEGER, at INTEGER, by INTEGER DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, target TEXT, detail TEXT, at INTEGER, undo TEXT DEFAULT '', undone INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, kind TEXT, preview TEXT, total INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, urgent INTEGER DEFAULT 0, at INTEGER);
CREATE TABLE IF NOT EXISTS delivery_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, episode_id INTEGER, at INTEGER, total INTEGER, ok INTEGER, blocked INTEGER,
    fail INTEGER, queued INTEGER DEFAULT 0, secs REAL DEFAULT 0, dry INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, options TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS poll_votes (poll_id INTEGER, user_id INTEGER, opt INTEGER, PRIMARY KEY (poll_id, user_id));
CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q TEXT, found INTEGER, at INTEGER);
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, qkey TEXT, q TEXT, at INTEGER, UNIQUE (user_id, qkey));
CREATE TABLE IF NOT EXISTS texts (k TEXT, lang TEXT, body TEXT, banner TEXT DEFAULT '', btns TEXT DEFAULT '[]', PRIMARY KEY (k, lang));
CREATE TABLE IF NOT EXISTS text_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, k TEXT, lang TEXT, body TEXT, at INTEGER, by INTEGER);
CREATE TABLE IF NOT EXISTS consents (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ver INTEGER, at INTEGER);
CREATE TABLE IF NOT EXISTS removals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, target TEXT, reason TEXT, status TEXT DEFAULT 'pending',
    created_at INTEGER, reminded INTEGER DEFAULT 0, decided_at INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS holidays (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS admin_marks (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, kind TEXT, ref TEXT, note TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS notif_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, title TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS folders (user_id INTEGER NOT NULL, name TEXT NOT NULL, PRIMARY KEY (user_id, name));
CREATE TABLE IF NOT EXISTS promos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, serial_id INTEGER, message_id INTEGER DEFAULT 0, file_id TEXT, kind TEXT DEFAULT 'video',
    caption TEXT DEFAULT '', created_at INTEGER, sched_at INTEGER DEFAULT 0, status TEXT DEFAULT 'live', spoiler INTEGER DEFAULT 0,
    prem_only INTEGER DEFAULT 0, views INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promo_views (promo_id INTEGER, user_id INTEGER, at INTEGER, PRIMARY KEY (promo_id, user_id));
CREATE TABLE IF NOT EXISTS promo_notify (user_id INTEGER NOT NULL, serial_id INTEGER NOT NULL, PRIMARY KEY (user_id, serial_id));
CREATE TABLE IF NOT EXISTS autodel (chat_id INTEGER, message_id INTEGER, del_at INTEGER);
CREATE TABLE IF NOT EXISTS quality_wait (user_id INTEGER, episode_id INTEGER, wanted INTEGER, PRIMARY KEY (user_id, episode_id));
"""
db.executescript(SCHEMA)

_WANT = {
    "users": {
        "username": "TEXT", "joined_at": "INTEGER DEFAULT 0", "last_seen": "INTEGER DEFAULT 0",
        "plan_until": "INTEGER DEFAULT 0", "banned": "INTEGER DEFAULT 0", "ban_reason": "TEXT DEFAULT ''",
        "blocked": "INTEGER DEFAULT 0", "notify": "INTEGER DEFAULT 1", "silent": "INTEGER DEFAULT 0",
        "lang": "TEXT DEFAULT 'bn'", "ref": "INTEGER DEFAULT 0", "onboarded": "INTEGER DEFAULT 0",
        "consent_ver": "INTEGER DEFAULT 0", "consent_at": "INTEGER DEFAULT 0",
        "q_default": "INTEGER DEFAULT 720", "data_saver": "INTEGER DEFAULT 0", "send_all_q": "INTEGER DEFAULT 0",
        "deliver_mode": "TEXT DEFAULT 'now'", "quiet_on": "INTEGER DEFAULT 0", "quiet_start": "INTEGER DEFAULT 23",
        "quiet_end": "INTEGER DEFAULT 7", "digest": "TEXT DEFAULT 'off'", "holiday_dow": "INTEGER DEFAULT -1",
        "save_hist": "INTEGER DEFAULT 1", "reply_kb": "INTEGER DEFAULT 0", "page_size": "INTEGER DEFAULT 8",
        "nt_ep": "INTEGER DEFAULT 1", "nt_new": "INTEGER DEFAULT 1", "nt_ann": "INTEGER DEFAULT 1",
        "nt_plan": "INTEGER DEFAULT 1", "nt_offer": "INTEGER DEFAULT 1", "nt_rem": "INTEGER DEFAULT 1",
        "exp_warned": "INTEGER DEFAULT 0", "recent_q": "TEXT DEFAULT '[]'", "seen_ver": "TEXT DEFAULT ''",
        "is_tester": "INTEGER DEFAULT 0", "autoplay": "INTEGER DEFAULT 1", "auto_next": "INTEGER DEFAULT 0",
        "auto_del": "INTEGER DEFAULT 0", "nt_promo": "INTEGER DEFAULT 1", "whitelist": "INTEGER DEFAULT 0",
        "tier": "TEXT DEFAULT 'pro'", "ab": "TEXT DEFAULT ''", "promo_spoil": "INTEGER DEFAULT 0",
    },
    "payments": {"method": "TEXT DEFAULT ''", "shot": "TEXT DEFAULT ''", "reason": "TEXT DEFAULT ''"},
    "serials": {
        "roman": "TEXT", "channel": "TEXT DEFAULT ''", "status": "TEXT DEFAULT 'on'", "poster": "TEXT DEFAULT ''",
        "synopsis": "TEXT DEFAULT ''", "air_time": "TEXT DEFAULT ''", "actors": "TEXT DEFAULT ''",
        "genre": "TEXT DEFAULT ''", "pinned": "INTEGER DEFAULT 0", "draft": "INTEGER DEFAULT 0",
        "created_at": "INTEGER DEFAULT 0", "last_ep_at": "INTEGER DEFAULT 0", "last_ep_no": "INTEGER DEFAULT 0",
        "qmask": "TEXT DEFAULT '480,720,1080'", "promo_at": "INTEGER DEFAULT 0", "next_ep_at": "INTEGER DEFAULT 0",
        "autoplay": "INTEGER DEFAULT 1", "promo_on": "INTEGER DEFAULT 1",
    },
    "promos": {"link": "TEXT DEFAULT ''"},
    "follows": {
        "created_at": "INTEGER DEFAULT 0", "mute": "INTEGER DEFAULT 0", "pause_until": "INTEGER DEFAULT 0",
        "q": "INTEGER DEFAULT 0", "promo_off": "INTEGER DEFAULT 0",
    },
}
for _t, _cols in _WANT.items():
    have = {r["name"] for r in db.execute(f"PRAGMA table_info({_t})")}
    for _c, _d in _cols.items():
        if _c not in have:
            db.execute(f"ALTER TABLE {_t} ADD COLUMN {_c} {_d}")

db.executescript(
    """
CREATE INDEX IF NOT EXISTS ix_follows_serial ON follows(serial_id);
CREATE INDEX IF NOT EXISTS ix_eps_serial ON eps(serial_id, status);
CREATE INDEX IF NOT EXISTS ix_eps_pub ON eps(status, publish_at);
CREATE INDEX IF NOT EXISTS ix_del_user ON deliveries(user_id, sent_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_del ON deliveries(user_id, episode_id);
CREATE INDEX IF NOT EXISTS ix_del_ep ON deliveries(episode_id);
CREATE INDEX IF NOT EXISTS ix_queue_due ON queue(due_at);
CREATE INDEX IF NOT EXISTS ix_saves_ep ON saves(episode_id);
CREATE INDEX IF NOT EXISTS ix_search_at ON searches(at);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notif_log(user_id, at);
CREATE INDEX IF NOT EXISTS ix_users_seen ON users(last_seen);
CREATE INDEX IF NOT EXISTS ix_pay_trx ON payments(trx);
CREATE INDEX IF NOT EXISTS ix_autodel ON autodel(del_at);
CREATE INDEX IF NOT EXISTS ix_promo_serial ON promos(serial_id, status);
"""
)
# পুরনো ডাটাবেস আপগ্রেড
_t0 = now()
db.execute("INSERT OR IGNORE INTO users (user_id, name, joined_at, last_seen) SELECT DISTINCT user_id, '', ?, ? FROM follows", (_t0, _t0))
db.execute("UPDATE users SET joined_at=? WHERE joined_at IS NULL OR joined_at=0", (_t0,))
db.execute("UPDATE follows SET created_at=? WHERE created_at IS NULL OR created_at=0", (_t0,))
db.execute("UPDATE serials SET created_at=? WHERE created_at IS NULL OR created_at=0", (_t0,))
for _r in db.execute("SELECT id, name FROM serials").fetchall():
    try:
        db.execute("UPDATE serials SET name_key=?, roman=? WHERE id=?", (norm(_r["name"]), romanize(_r["name"]), _r["id"]))
    except sqlite3.IntegrityError:
        pass

# ================================================================ সেটিংস (ক্যাশসহ)
DEF_SET = {
    "deliver": "1", "protect": "0", "alert_new": "1", "free_limit": str(FREE_LIMIT_DEFAULT), "maintenance": "0",
    "consent_on": "0", "terms_ver": "1", "history_on": "1", "saved_on": "1", "quality_on": "1", "pub_mode": "window",
    "pub_window": "3", "q_free": "480", "q_premium": "1080", "trial_days": "0", "hist_days": "90",
    "alert_q_missing": "1", "faq_url": "", "terms_changes": "", "price_7": "0", "price_30": "0", "price_90": "0",
    "price_life": "0", "restart_chat": "", "autoplay_on": "1", "free_delay": "0", "test_mode": "0",
    "require_plan": "0", "free_for_all": "0", "vcfg": "", "pm_bkash": "", "pm_nagad": "", "pm_rocket": "", "on_bkash": "1",
    "on_nagad": "1", "on_rocket": "1", "lock_clicks": "0", "basic_limit": "10", "q_basic": "720", "price_b7": "0", "price_b30": "0", "price_b90": "0", "autoplay_approve": "0", "wsched": "[]", "ab_on": "0", "sms_verify": "0", "promo_srcs": "[]", "promo_state": "{}", "link_report": "", "promo_last": "0", "promo_prem": "0", "promo_days": "30", "send_rate": "", "testers": "", "last_cleanup": "0", "home_layout": "",
}
_SC: dict = {}


def gs(k: str) -> str:
    if k in _SC:
        return _SC[k]
    r = Q1("SELECT v FROM settings WHERE k=?", k)
    v = r["v"] if r and r["v"] is not None else DEF_SET.get(k, "")
    _SC[k] = v
    return v


def gi(k: str) -> int:
    try:
        return int(gs(k))
    except (TypeError, ValueError):
        return 0


def ss(k: str, v):
    X("INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, str(v))
    _SC[k] = str(v)


def free_limit() -> int:
    return max(1, gi("free_limit"))


# ================================================================ ইউজার / প্ল্যান
_LANG: dict = {}


def user(uid: int):
    return Q1("SELECT * FROM users WHERE user_id=?", uid)


def lg(uid: int) -> str:
    v = _LANG.get(uid)
    if v is None:
        r = Q1("SELECT lang FROM users WHERE user_id=?", uid)
        v = (r["lang"] if r and r["lang"] else "bn")
        _LANG[uid] = v
    return v


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def is_premium(u) -> bool:
    return bool(u) and (u["plan_until"] == -1 or u["plan_until"] > now())


def plan_label(u, lang="bn") -> str:
    if not u:
        return "🆓 Free"
    if u["plan_until"] == -1:
        return ("🥈 Basic" if u["tier"] == "basic" else "💎 Premium") + " (Lifetime)"
    if u["plan_until"] > now():
        d = -(-(u["plan_until"] - now()) // 86400)
        nm = "🥈 Basic" if u["tier"] == "basic" else "💎 Premium"
        return L(lang, f"{nm} ({d} দিন বাকি)", f"{nm} ({d} days left)")
    return "🆓 Free"


def follow_count(uid: int) -> int:
    return V("SELECT COUNT(*) FROM follows WHERE user_id=?", uid)


def max_quality(u) -> int:
    if is_premium(u):
        return gi("q_basic") if u["tier"] == "basic" else gi("q_premium")
    return gi("q_free")


def can_follow(uid: int, extra: int = 1) -> bool:
    u = user(uid)
    if is_premium(u):
        return u["tier"] != "basic" or follow_count(uid) + extra <= max(1, gi("basic_limit"))
    return follow_count(uid) + extra <= free_limit()


def set_plan(uid: int, spec, by: int = 0, note: str = "", tier: str = "pro") -> int:
    """spec: 'free' | 'life' | দিন (int)"""
    u = user(uid)
    if spec == "free":
        until = 0
    elif spec == "life":
        until = -1
    elif u["plan_until"] == -1:
        until = -1
    else:
        base = u["plan_until"] if u["plan_until"] > now() else now()
        until = base + int(spec) * 86400
    X("UPDATE users SET plan_until=?, exp_warned=0, tier=? WHERE user_id=?", until, tier if spec != "free" else "pro", uid)
    X("INSERT INTO plan_history (user_id, plan, until, at, by, note) VALUES (?,?,?,?,?,?)", uid, str(spec), until, now(), by, note)
    return until


def alog(admin_id: int, action: str, target="", detail="", undo=None):
    X(
        "INSERT INTO admin_log (admin_id, action, target, detail, at, undo) VALUES (?,?,?,?,?,?)",
        admin_id, action, str(target), str(detail)[:300], now(), json.dumps(undo) if undo else "",
    )


def nlog(uid: int, kind: str, title: str):
    X("INSERT INTO notif_log (user_id, kind, title, at) VALUES (?,?,?,?)", uid, kind, title[:120], now())


# ================================================================ সার্চ ইঞ্জিন (বানান ভুল সহ্য, বাংলা⇄ইংরেজি)
_SCACHE = None


def sdirty():
    global _SCACHE
    _SCACHE = None


def serial_cache():
    global _SCACHE
    if _SCACHE is None:
        al: dict = {}
        for r in Q("SELECT serial_id, alias_key, roman FROM serial_alias"):
            rom = r["roman"] or romanize(r["alias_key"])
            al.setdefault(r["serial_id"], []).append((r["alias_key"], rom, skeleton(rom)))
        out = []
        for s in Q("SELECT id, name, name_key, roman, draft, pinned, actors FROM serials"):
            rom = s["roman"] or romanize(s["name"])
            names = [(s["name_key"], rom, skeleton(rom))] + al.get(s["id"], [])
            out.append(dict(id=s["id"], key=s["name_key"], names=names, draft=s["draft"], actors=norm(s["actors"] or ""),
                            actors_rom=romanize(s["actors"] or "")))
        _SCACHE = out
    return _SCACHE


def _score(q, qr, qs, names, toks):
    best = 0
    for key, rom, skel in names:
        if not key:
            continue
        s = 0
        if key == q:
            s = 100
        elif key.startswith(q):
            s = 92
        elif q in key:
            s = 82
        elif rom and qr:
            if rom == qr:
                s = 96
            elif rom.startswith(qr):
                s = 88
            elif qr in rom:
                s = 78
            elif skel and qs and skel == qs:
                s = 90
            elif skel and qs and len(qs) >= 3 and qs in skel:
                s = 72
            else:
                if len(toks) > 1 and all((t in key or romanize(t) in rom) for t in toks):
                    s = 80
                elif abs(len(rom) - len(qr)) <= max(4, len(qr)):
                    r = difflib.SequenceMatcher(None, qr, rom).ratio()
                    if qs and skel:
                        r = max(r, difflib.SequenceMatcher(None, qs, skel).ratio() * 0.97)
                    if r >= 0.6:
                        s = int(r * 72)
        if s > best:
            best = s
    return best


def search_serials(text: str, include_draft=False, actors=True, min_score=55):
    """[(score, serial_id)] স্কোর অনুযায়ী"""
    q = norm(text)
    if not q:
        return []
    qr = romanize(q)
    qs = skeleton(qr)
    toks = q.split()
    res = []
    for s in serial_cache():
        if s["draft"] and not include_draft:
            continue
        sc = _score(q, qr, qs, s["names"], toks)
        if actors and sc < min_score and s["actors"] and (q in s["actors"] or (qr and qr in s["actors_rom"])):
            sc = 60
        if sc >= min_score:
            res.append((sc, s["id"]))
    res.sort(key=lambda x: (-x[0], x[1]))
    return res


def suggest_serials(text: str, include_draft=False, limit=5):
    res = search_serials(text, include_draft, actors=False, min_score=38)
    return res[:limit]


# ================================================================ রেট লিমিটার (টেলিগ্রাম ফ্লাড লিমিটের ভেতরে সর্বোচ্চ গতি)
class Limiter:
    def __init__(self, rate: float):
        self.gap = 1.0 / max(1.0, rate)
        self.next = 0.0

    async def acquire(self):
        t = time.monotonic()
        if self.next < t:
            self.next = t
        wait = self.next - t
        self.next += self.gap
        if wait > 0:
            await asyncio.sleep(wait)

    def penalty(self, secs: float):
        self.next = max(self.next, time.monotonic()) + secs


LIM = Limiter(SEND_RATE)
SEM = asyncio.Semaphore(40)


class Ctx:
    """ব্যাকগ্রাউন্ড টাস্কের জন্য ছোট context (bot + application)"""

    def __init__(self, app):
        self.application = app
        self.bot = app.bot
        self.bot_data = app.bot_data


async def guarded(factory):
    """টেলিগ্রাম কল + রেট লিমিট + রিট্রাই। ('ok', result) | ('blocked', None) | ('fail', None)"""
    for _ in range(4):
        await LIM.acquire()
        try:
            return "ok", await factory()
        except RetryAfter as e:
            LIM.penalty(e.retry_after + 1)
        except Forbidden:
            return "blocked", None
        except BadRequest as e:
            m = str(e).lower()
            if "chat not found" in m or "deactivated" in m:
                return "blocked", None
            log.warning("telegram call failed: %s", e)
            return "fail", None
        except NetworkError:
            await asyncio.sleep(1)
        except TelegramError as e:
            log.warning("telegram call failed: %s", e)
            return "fail", None
    return "fail", None


async def tsend(bot, uid, text, kb=None, silent=False, **kw) -> str:
    st, _ = await guarded(lambda: bot.send_message(uid, text[:4096], reply_markup=kb, disable_notification=silent, **kw))
    return st


async def alert_admins(ctx, text, kb=None):
    for a in ADMIN_IDS:
        await tsend(ctx.bot, a, text, kb)


# ================================================================ টেক্সট স্টোর (ডিফল্ট + এডিটযোগ্য)
KNOWN_PH = ("name", "plan", "follows", "limit", "bot", "id", "version", "support", "channel", "date", "prices", "free_limit")
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "tg-spoiler", "blockquote", "tg-emoji", "span"}

DEFAULT_TEXTS = {
    "welcome_new": {
        "bn": "✨ <b>স্বাগতম, {name}!</b> ✨\n\n📺 <b>{bot}</b>-এ আপনাকে স্বাগত। পছন্দের সিরিয়াল ফলো করুন — নতুন এপিসোড আপলোড হলেই সরাসরি আপনার ইনবক্সে চলে আসবে।\n\n🚀 <b>শুরু করুন ৩ ধাপে</b>\n1️⃣ চ্যানেল/প্ল্যাটফর্ম বেছে নিন\n2️⃣ পছন্দের সিরিয়াল ফলো করুন\n3️⃣ নোটিফিকেশন চালু রাখুন\n\n👤 প্ল্যান: {plan}\n🔔 ফলো: <b>{follows}/{limit}</b>",
        "en": "✨ <b>Welcome, {name}!</b> ✨\n\n📺 Welcome to <b>{bot}</b>. Follow your favourite serials — new episodes land straight in your inbox.\n\n🚀 <b>Get started in 3 steps</b>\n1️⃣ Pick a channel/platform\n2️⃣ Follow your serials\n3️⃣ Keep notifications on\n\n👤 Plan: {plan}\n🔔 Following: <b>{follows}/{limit}</b>",
    },
    "welcome_new_b": {
        "bn": "🎬 <b>হ্যালো {name}!</b>\n\n{bot} — আপনার প্রিয় সিরিয়ালের নতুন এপিসোড সরাসরি ইনবক্সে।\n👉 এখনই একটি সিরিয়াল ফলো করুন!\n\n👤 {plan} • 🔔 {follows}/{limit}",
        "en": "🎬 <b>Hi {name}!</b>\n\n{bot} — new episodes of your favourite serials straight to your inbox.\n👉 Follow a serial now!\n\n👤 {plan} • 🔔 {follows}/{limit}",
    },
    "welcome_old": {
        "bn": "👋 <b>আবার স্বাগতম, {name}!</b>\n\n👤 {plan}\n🔔 ফলো: <b>{follows}/{limit}</b>",
        "en": "👋 <b>Welcome back, {name}!</b>\n\n👤 {plan}\n🔔 Following: <b>{follows}/{limit}</b>",
    },
    "welcome_premium": {
        "bn": "💎 <b>আবার স্বাগতম, {name}!</b>\n\nআপনার প্ল্যান: {plan}\n🔔 ফলো: <b>{follows}/{limit}</b>",
        "en": "💎 <b>Welcome back, {name}!</b>\n\nYour plan: {plan}\n🔔 Following: <b>{follows}/{limit}</b>",
    },
    "help": {
        "bn": (
            "❓ <b>{bot} — হেল্প সেন্টার</b>\n\n"
            "📺 <b>বটটি কীভাবে কাজ করে</b>\nএডমিন নতুন এপিসোড আপলোড করলে, যারা ওই সিরিয়াল ফলো করেছে তাদের কাছে বট নিজে থেকেই এপিসোড পাঠিয়ে দেয়। ফলো করার <b>পরের নতুন এপিসোড</b>ই পাবেন, পুরনোগুলো নয়।\n\n"
            "⌨️ <b>কমান্ড</b>\n"
            "/start – হোম মেনু\n/serials – সিরিয়াল তালিকা, ফিল্টার, বাল্ক ফলো\n/myfeeds – আমার ফিড (মিউট, বিরতি, পিন)\n"
            "/search – সিরিয়াল খোঁজা (ভুল বানানেও)\n/saved – সেভ করা এপিসোড, ফোল্ডার, নোট\n/history – দেখার ইতিহাস\n"
            "/notification – নোটিফিকেশন নিয়ন্ত্রণ\n/settings – ভাষা, কোয়ালিটি, পেজ সাইজ\n/plan – প্ল্যান, পেমেন্ট\n"
            "/trending – জনপ্রিয় সিরিয়াল\n/support – এডমিনকে জানান (টিকেট)\n/mytickets – আমার টিকেট\n/terms – নিয়ম ও গোপনীয়তা\n/cancel – চলমান কাজ বাতিল\n\n"
            "🎞 <b>এপিসোডের নিচের বাটন</b>\nকোয়ালিটি বদল • ⭐ সেভ • ✅ দেখেছি • 👍/👎 • ⏮⏭ আগের/পরের • 🚩 রিপোর্ট • 🔕 আনফলো\n\n"
            "💎 <b>প্ল্যান</b>: Free-তে {free_limit}টি সিরিয়াল, Premium-এ আনলিমিটেড + উচ্চ কোয়ালিটি।\n"
            "🔔 <b>নোটিফিকেশন</b>: শব্দ/নিঃশব্দ/বন্ধ, কোয়াইট আওয়ার, ডাইজেস্ট, ছুটির দিন, বিরতি।\n"
            "📞 সাপোর্ট: {support}"
        ),
        "en": (
            "❓ <b>{bot} — Help Center</b>\n\n📺 <b>How it works</b>\nWhen the admin uploads a new episode, followers get it automatically. You only get episodes uploaded <b>after</b> you follow.\n\n"
            "⌨️ <b>Commands</b>\n/start – Home\n/serials – Serial list, filters, bulk follow\n/myfeeds – My feed (mute, pause, pin)\n/search – Find serials (typo tolerant)\n"
            "/saved – Saved episodes, folders, notes\n/history – Watch history\n/notification – Notification control\n/settings – Language, quality, page size\n/plan – Plan & payment\n"
            "/trending – Popular serials\n/support – Contact admin (ticket)\n/mytickets – My tickets\n/terms – Rules & privacy\n/cancel – Cancel current action\n\n"
            "🎞 <b>Episode buttons</b>\nQuality • ⭐ Save • ✅ Watched • 👍/👎 • ⏮⏭ Prev/Next • 🚩 Report • 🔕 Unfollow\n\n"
            "💎 <b>Plans</b>: Free = {free_limit} serials, Premium = unlimited + higher quality.\n📞 Support: {support}"
        ),
    },
    "plan": {
        "bn": "💎 <b>প্ল্যান ও সুবিধা</b>\n\nআপনার প্ল্যান: <b>{plan}</b>\n📺 ফলো: <b>{follows}/{limit}</b>\n\n🆓 <b>Free</b>\n   • সর্বোচ্চ <b>{free_limit}টি</b> সিরিয়াল\n   • 480p কোয়ালিটি\n\n💎 <b>Premium</b>\n   • <b>আনলিমিটেড</b> সিরিয়াল ফলো\n   • 720p ও 1080p কোয়ালিটি\n   • সব ফিচার\n\n{prices}\n📞 প্ল্যান নিতে: {support}\n🆔 আপনার আইডি: <code>{id}</code>",
        "en": "💎 <b>Plans & benefits</b>\n\nYour plan: <b>{plan}</b>\n📺 Following: <b>{follows}/{limit}</b>\n\n🆓 <b>Free</b>\n   • Up to <b>{free_limit}</b> serials\n   • 480p quality\n\n💎 <b>Premium</b>\n   • <b>Unlimited</b> serials\n   • 720p & 1080p quality\n   • All features\n\n{prices}\n📞 To buy: {support}\n🆔 Your ID: <code>{id}</code>",
    },
    "limit": {
        "bn": "⚠️ Free প্ল্যানে সর্বোচ্চ {limit}টি সিরিয়াল ফলো করা যায়।\n💎 আনলিমিটেড ফলো ও 1080p পেতে Premium নিন।",
        "en": "⚠️ Free plan allows up to {limit} serials.\n💎 Get Premium for unlimited follows and 1080p.",
    },
    "expired": {
        "bn": "⌛ আপনার 💎 <b>Premium</b> প্ল্যানের মেয়াদ শেষ হয়েছে।\nরিনিউ করতে /plan দেখুন।",
        "en": "⌛ Your 💎 <b>Premium</b> plan has expired.\nSee /plan to renew.",
    },
    "terms_sum": {
        "bn": "📌 <b>এক নজরে</b>\n• বটটি শুধু ব্যক্তিগত ব্যবহারের জন্য\n• কনটেন্ট রিসেল/শেয়ার-অপব্যবহার নিষিদ্ধ\n• নিয়ম ভাঙলে ব্যান হতে পারে",
        "en": "📌 <b>At a glance</b>\n• Personal use only\n• No resale or abuse of content\n• Rule breakers may be banned",
    },
    "terms_rules": {
        "bn": "📜 <b>ব্যবহারের নিয়ম</b>\n\n১. {bot} শুধু ব্যক্তিগত ব্যবহারের জন্য।\n২. এপিসোড রিসেল বা অন্য কোথাও বাণিজ্যিকভাবে ব্যবহার করা যাবে না।\n৩. বট স্প্যাম/অপব্যবহার করা যাবে না।\n৪. এডমিনের সিদ্ধান্ত চূড়ান্ত।\n৫. নিয়ম বদলালে আপনাকে জানানো হবে।\n\n📞 যোগাযোগ: {support}\n🗓 হালনাগাদ: {date}",
        "en": "📜 <b>Rules of use</b>\n\n1. {bot} is for personal use only.\n2. No resale or commercial use of episodes.\n3. No spam or abuse.\n4. Admin decisions are final.\n5. You will be told when rules change.\n\n📞 Contact: {support}\n🗓 Updated: {date}",
    },
    "terms_privacy": {
        "bn": "🔒 <b>গোপনীয়তা</b>\n\nআমরা শুধু এগুলো জমা রাখি: আইডি, নাম/ইউজারনেম, ফলো তালিকা, প্ল্যান, সেভ ও ইতিহাস। এগুলো শুধু বট চালাতে ব্যবহৃত হয়, তৃতীয় পক্ষকে দেওয়া হয় না।\n\n📥 /terms → <b>আমার ডেটা</b> দিয়ে দেখুন, 🗑 <b>ডেটা মুছুন</b> দিয়ে সব মুছুন।",
        "en": "🔒 <b>Privacy</b>\n\nWe store only: ID, name/username, follows, plan, saves and history — used only to run the bot, never shared.\n\n📥 /terms → <b>My data</b> to view, 🗑 <b>Delete data</b> to erase everything.",
    },
    "terms_plan": {
        "bn": "💎 <b>প্ল্যান ও রিফান্ড</b>\n\n• প্ল্যান সক্রিয় হলে মেয়াদ পর্যন্ত সুবিধা পাবেন।\n• ভুল পেমেন্ট হলে {support}-এ TrxID সহ জানান।\n• ব্যবহার শুরু হওয়া প্ল্যান সাধারণত ফেরতযোগ্য নয়।",
        "en": "💎 <b>Plans & refunds</b>\n\n• Benefits last until the plan expires.\n• For wrong payments contact {support} with TrxID.\n• Used plans are generally non-refundable.",
    },
    "terms_ban": {
        "bn": "🚫 <b>ব্যান নীতি</b>\n\nস্প্যাম, কনটেন্ট চুরি/রিসেল, বটের অপব্যবহার বা এডমিনকে হয়রানি করলে ব্যান হতে পারে। ভুল মনে করলে {support}-এ আপিল করুন।",
        "en": "🚫 <b>Ban policy</b>\n\nSpam, content theft/resale, bot abuse or harassing admins may lead to a ban. Appeal at {support} if you think it was a mistake.",
    },
    "terms_disc": {
        "bn": "⚠️ <b>দাবিত্যাগ</b>\n\nসার্ভিস \"যেমন আছে\" ভিত্তিতে দেওয়া হয়। ডেলিভারি সময়/কোয়ালিটির নিশ্চয়তা নেই। কনটেন্ট নিয়ে আপত্তি থাকলে /terms → 🚩 অপসারণ অনুরোধ দিন।",
        "en": "⚠️ <b>Disclaimer</b>\n\nService is provided \"as is\". No guarantee on delivery time or quality. For content objections use /terms → 🚩 Removal request.",
    },
}
TEXT_KEYS = [
    ("welcome_new", "👋 ওয়েলকাম (নতুন)", "Welcome (new)"), ("welcome_new_b", "🅱️ ওয়েলকাম (নতুন) — ভ্যারিয়েন্ট B", "Welcome (new) — B"), ("welcome_old", "🔁 ওয়েলকাম (পুরনো)", "Welcome (returning)"),
    ("welcome_premium", "💎 ওয়েলকাম (Premium)", "Welcome (Premium)"), ("help", "❓ হেল্প", "Help"),
    ("plan", "💎 প্ল্যান পেজ", "Plan page"), ("limit", "⚠️ লিমিট শেষ", "Limit reached"), ("expired", "⌛ Premium শেষ", "Premium ended"),
]
TERMS_KEYS = [
    ("terms_sum", "📌 এক নজরে", "Summary"), ("terms_rules", "📜 নিয়ম", "Rules"), ("terms_privacy", "🔒 গোপনীয়তা", "Privacy"),
    ("terms_plan", "💎 প্ল্যান ও রিফান্ড", "Plans & refunds"), ("terms_ban", "🚫 ব্যান নীতি", "Ban policy"), ("terms_disc", "⚠️ দাবিত্যাগ", "Disclaimer"),
]
SHORT_TERMS = {
    "bn": {"terms_rules": "📜 <b>নিয়ম</b>\n• ব্যক্তিগত ব্যবহার\n• রিসেল নিষিদ্ধ\n• যোগাযোগ: {support}"},
    "en": {"terms_rules": "📜 <b>Rules</b>\n• Personal use\n• No resale\n• Contact: {support}"},
}


def prices_text(lang="bn") -> str:
    items = [("price_b30", L(lang, "🥈 Basic ৩০ দিন", "🥈 Basic 30 days")), ("price_7", L(lang, "৭ দিন", "7 days")), ("price_30", L(lang, "৩০ দিন", "30 days")),
             ("price_90", L(lang, "৯০ দিন", "90 days")), ("price_life", "♾ Lifetime")]
    lines = [f"   • {n}: <b>{gi(k)} ৳</b>" for k, n in items if gi(k) > 0]
    return (L(lang, "💰 <b>মূল্য</b>\n", "💰 <b>Prices</b>\n") + "\n".join(lines) + "\n") if lines else ""


def ph_map(u, name=None, uid=None, lang="bn") -> dict:
    lim = (str(gi("basic_limit")) if u["tier"] == "basic" else "∞") if is_premium(u) else str(free_limit())
    uid = uid or (u["user_id"] if u else 0)
    return {
        "name": esc(name if name is not None else (u["name"] if u else "")),
        "plan": plan_label(u, lang), "follows": str(follow_count(uid)) if uid else "0", "limit": lim, "bot": BOT_NAME,
        "id": str(uid), "version": VERSION, "support": esc(ADMIN_CONTACT) if ADMIN_CONTACT else "/support",
        "channel": esc(CHANNEL_LINK) if CHANNEL_LINK else "—", "date": local().strftime("%d-%m-%Y"),
        "prices": prices_text(lang), "free_limit": str(free_limit()),
    }


def fill(t: str, u, name=None, uid=None, lang="bn") -> str:
    for k, v in ph_map(u, name, uid, lang).items():
        t = t.replace("{" + k + "}", str(v))
    return t


def validate_text(t: str) -> list:
    errs = []
    for m in re.finditer(r"\{(\w+)\}", t):
        if m.group(1) not in KNOWN_PH:
            errs.append(f"❌ অচেনা প্লেসহোল্ডার: {{{m.group(1)}}}")
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z][\w\-]*)[^>]*>", t):
        close, tag = m.group(1) == "/", m.group(2).lower()
        if tag not in ALLOWED_TAGS:
            errs.append(f"❌ অনুমোদিত নয় এমন ট্যাগ: <{tag}>")
        elif close:
            if stack and stack[-1] == tag:
                stack.pop()
            else:
                errs.append(f"❌ ভুল বন্ধ ট্যাগ: </{tag}>")
        else:
            stack.append(tag)
    if stack:
        errs.append("❌ খোলা ট্যাগ বন্ধ করা হয়নি: " + ", ".join(f"<{x}>" for x in stack))
    if len(t) > 3500:
        errs.append(f"❌ অক্ষর সীমা পার ({len(t)}/3500)")
    return errs


def get_text(k: str, lang: str):
    """(body, banner, btns[[label,url],...])"""
    r = Q1("SELECT body, banner, btns FROM texts WHERE k=? AND lang=?", k, lang)
    if r and r["body"]:
        try:
            btns = json.loads(r["btns"] or "[]")
        except ValueError:
            btns = []
        return r["body"], r["banner"] or "", btns
    d = DEFAULT_TEXTS.get(k, {})
    return d.get(lang) or d.get("bn", ""), "", []


def has_custom(k: str, lang: str) -> bool:
    return bool(Q1("SELECT 1 FROM texts WHERE k=? AND lang=? AND body<>''", k, lang))


def save_text(k: str, lang: str, body=None, banner=None, btns=None, by=0):
    old = Q1("SELECT * FROM texts WHERE k=? AND lang=?", k, lang)
    cur_body = old["body"] if old and old["body"] else DEFAULT_TEXTS.get(k, {}).get(lang) or DEFAULT_TEXTS.get(k, {}).get("bn", "")
    if body is not None and body != cur_body:
        X("INSERT INTO text_versions (k, lang, body, at, by) VALUES (?,?,?,?,?)", k, lang, cur_body, now(), by)
        X("DELETE FROM text_versions WHERE k=? AND lang=? AND id NOT IN (SELECT id FROM text_versions WHERE k=? AND lang=? ORDER BY id DESC LIMIT 5)", k, lang, k, lang)
    nb = body if body is not None else cur_body
    nbn = banner if banner is not None else (old["banner"] if old else "")
    nbt = json.dumps(btns) if btns is not None else (old["btns"] if old else "[]")
    X("INSERT INTO texts (k, lang, body, banner, btns) VALUES (?,?,?,?,?) ON CONFLICT(k, lang) DO UPDATE SET body=excluded.body, banner=excluded.banner, btns=excluded.btns", k, lang, nb, nbn, nbt)
    alog(by, "text_edit", f"{k}/{lang}", f"{len(nb)} chars")


def reset_text(k: str, lang: str, by=0):
    X("DELETE FROM texts WHERE k=? AND lang=?", k, lang)
    alog(by, "text_reset", f"{k}/{lang}")


# ================================================================ রেজিস্ট্রি ও কমন UI হেল্পার
ROUTES: dict = {}
CMDS: dict = {}
AW: dict = {}


def route(*prefixes):
    def deco(fn):
        for p in prefixes:
            ROUTES[p] = fn
        return fn

    return deco


def command(name, admin=False):
    def deco(fn):
        CMDS[name] = (fn, admin)
        return fn

    return deco


def awaiting(kind):
    def deco(fn):
        AW[kind] = fn
        return fn

    return deco


async def ans(q, text=None, alert=False):
    try:
        await q.answer(text, show_alert=alert)
    except TelegramError:
        pass


async def reply(update, text, kb=None, **kw):
    try:
        return await update.effective_message.reply_text(text[:4096], reply_markup=kb, **kw)
    except TelegramError as e:
        log.warning("reply failed: %s", e)


async def show(update, ctx, text, kb=None):
    """কলব্যাকে এডিট, মেসেজে রিপ্লাই; মিডিয়া মেসেজ হলে মুছে নতুন পাঠায়"""
    q = update.callback_query
    if not q:
        return await reply(update, text, kb)
    m = q.message
    try:
        if m is not None and any(getattr(m, a, None) for a in ("photo", "video", "document", "animation", "audio")):
            try:
                await m.delete()
            except TelegramError:
                pass
            return await ctx.bot.send_message(q.from_user.id, text[:4096], reply_markup=kb)
        return await q.edit_message_text(text[:4096], reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("show failed: %s", e)
    except TelegramError as e:
        log.warning("show failed: %s", e)


def set_await(ctx, kind, payload=None):
    ctx.user_data["aw"] = (kind, payload)


def paginate(total: int, page: int, size: int):
    pages = max(1, -(-total // size))
    page = min(max(page, 0), pages - 1)
    return page, pages, page * size


def nav_row(prefix: str, page: int, pages: int):
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(B("⬅️", callback_data=f"{prefix}:{page - 1}"))
    row.append(B(f"📄 {page + 1}/{pages}", callback_data=f"pj:{prefix}:{pages}"))
    if page < pages - 1:
        row.append(B("➡️", callback_data=f"{prefix}:{page + 1}"))
    return row


def home_row(lang="bn"):
    return [B(L(lang, "🏠 মেনু", "🏠 Menu"), callback_data="h"), B("▶️ Continue", callback_data="cw")]


def qset(mask) -> set:
    out = {int(x) for x in split_csv(mask) if x.isdigit() and int(x) in QUALS}
    return out or set(QUALS)


def ep_label(ep, lang="bn") -> str:
    if ep["ep_no"] is not None:
        return f"E{ep['ep_no']}"
    return (ep["title"] or f"#{ep['id']}")[:28]


# ================================================================ এপিসোড চেনা (সিরিয়াল / নম্বর / কোয়ালিটি)
RE_EP = [
    re.compile(r"(?:^|[\s_\-\.\[\(])(?:e|ep|episode|এপিসোড|পর্ব)\s*[\.\-:#]?\s*(\d{1,5})(?!\d)", re.I),
    re.compile(r"(?:^|[\s_\-\.])s\d{1,2}\s*e(\d{1,5})(?!\d)", re.I),
]
RE_Q = re.compile(r"(?<!\d)(\d{3,4})\s*p(?![a-z])", re.I)
RE_QTOK = re.compile(r"\b(480|540|720|1080|2160)\s*p\b|\b(fhd|hd|sd|webrip|web[\s\-]?dl|hdrip|x264|x265|hevc|aac|mkv|mp4)\b", re.I)
RE_MARK = re.compile(r"(?:\b(?:e|ep|episode|এপিসোড|পর্ব)\s*\d)|(?:\d{3,4}\s*p\b)|(?:\bs\d+\s*e\d+)", re.I)


def to_q(n: int) -> int:
    return 480 if n <= 540 else (720 if n <= 900 else 1080)


def file_of(msg):
    for kind in ("video", "document", "animation", "audio"):
        m = getattr(msg, kind, None)
        if m:
            return dict(kind=kind, file_id=m.file_id, size=getattr(m, "file_size", 0) or 0,
                        name=getattr(m, "file_name", None) or "", height=getattr(m, "height", 0) or 0)
    return None


def parse_ep_no(texts):
    for t in texts:
        for rx in RE_EP:
            m = rx.search(t)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
    return None


def parse_quality(texts, height=0):
    for t in texts:
        m = RE_Q.search(t)
        if m:
            return to_q(int(m.group(1))), "ক্যাপশন/নাম"
        tl = t.lower()
        if re.search(r"\bfhd\b", tl):
            return 1080, "ক্যাপশন/নাম"
        if re.search(r"\bhd\b", tl):
            return 720, "ক্যাপশন/নাম"
        if re.search(r"\bsd\b", tl):
            return 480, "ক্যাপশন/নাম"
    if height:
        return (1080 if height >= 1000 else 720 if height >= 600 else 480), f"ভিডিও রেজোলিউশন ({height}p)"
    return 720, "অনুমান (ডিফল্ট)"


def detect(msg):
    """চ্যানেল পোস্ট থেকে সিরিয়াল, এপিসোড নম্বর, কোয়ালিটি চেনা"""
    f = file_of(msg)
    raw = [t for t in (msg.caption, f["name"] if f else None, msg.text) if t]
    texts = [t.translate(_BNDIG) for t in raw]
    blob = " " + " ".join(norm(t) for t in texts) + " "
    pick, how = None, ""
    best = 0
    for s in serial_cache():
        for key, rom, skel in s["names"]:
            if key and f" {key} " in blob and len(key) > best:
                best, pick, how = len(key), s["id"], f"নাম মিলেছে: “{key}”"
    if pick is None:
        rb = " " + romanize(blob) + " "
        for s in serial_cache():
            for key, rom, skel in s["names"]:
                if rom and len(rom) >= 4 and f" {rom} " in rb and len(rom) > best:
                    best, pick, how = len(rom), s["id"], f"ইংরেজি/বাংলা ধ্বনি মিলেছে: “{rom}”"
    if pick is None:
        m = RE_MARK.search(blob)
        head = blob[: m.start()] if m else blob
        if len(head.strip()) >= 3:
            res = search_serials(head, include_draft=True, actors=False, min_score=80)
            if res and (len(res) == 1 or res[0][0] - res[1][0] >= 5):
                pick, how = res[0][1], f"কাছাকাছি নাম (স্কোর {res[0][0]})"
    serial = Q1("SELECT * FROM serials WHERE id=?", pick) if pick else None
    ep_no = parse_ep_no(texts)
    quality, qhow = parse_quality(texts, f["height"] if f else 0)
    first = ""
    for t in (msg.caption, msg.text):
        if t and t.strip():
            first = t.strip().splitlines()[0]
            break
    if not first and f and f["name"]:
        first = os.path.splitext(f["name"])[0]
    title = re.sub(r"_+", " ", first).strip()[:150]
    tkey = norm(RE_QTOK.sub(" ", title))
    return dict(serial=serial, how=how, ep_no=ep_no, quality=quality, qhow=qhow, title=title, tkey=tkey, file=f)


def add_file(d, message_id: int):
    """(episode_id, created, replaced)"""
    sid = d["serial"]["id"]
    if d["ep_no"] is not None:
        ep = Q1("SELECT * FROM eps WHERE serial_id=? AND ep_no=? ORDER BY id DESC LIMIT 1", sid, d["ep_no"])
    else:
        ep = Q1("SELECT * FROM eps WHERE serial_id=? AND ep_no IS NULL AND tkey=? AND created_at>? ORDER BY id DESC LIMIT 1",
                sid, d["tkey"], now() - 3600)
    created = False
    if not ep:
        mode, win = gs("pub_mode"), gi("pub_window")
        pat = 0 if mode == "manual" else now() + (win * 60 if mode == "window" else 3600)
        cur = X("INSERT INTO eps (serial_id, ep_no, title, tkey, created_at, status, publish_at) VALUES (?,?,?,?,?,'pending',?)",
                sid, d["ep_no"], d["title"], d["tkey"], now(), pat)
        ep_id, created = cur.lastrowid, True
    else:
        ep_id = ep["id"]
    replaced = bool(Q1("SELECT 1 FROM ep_files WHERE episode_id=? AND quality=?", ep_id, d["quality"]))
    f = d["file"]
    X("INSERT OR REPLACE INTO ep_files (episode_id, quality, message_id, file_id, kind, size) VALUES (?,?,?,?,?,?)",
      ep_id, d["quality"], message_id, f["file_id"], f["kind"], f["size"])
    return ep_id, created, replaced


# ================================================================ এডমিন রিভিউ কার্ড
def review_view(ep_id: int):
    ep = Q1("SELECT e.*, s.name AS sname, s.qmask FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    if not ep:
        return "❌ এপিসোড পাওয়া যায়নি", None
    files = {r["quality"]: r for r in Q("SELECT * FROM ep_files WHERE episode_id=?", ep_id)}
    allowed = qset(ep["qmask"])
    marks = "  ".join(("✅" if q in files else "❌") + f"{q}p" + (f" ({fmt_size(files[q]['size'])})" if q in files and files[q]["size"] else "") for q in sorted(allowed))
    likes = V("SELECT COUNT(*) FROM ratings WHERE episode_id=? AND val=1", ep_id)
    dis = V("SELECT COUNT(*) FROM ratings WHERE episode_id=? AND val=0", ep_id)
    if ep["status"] == "pending":
        eta = "ম্যানুয়াল (📤 চাপুন)" if not ep["publish_at"] else f"{fmt_ts(ep['publish_at'])} এ স্বয়ংক্রিয়"
        state = f"⏳ <b>অপেক্ষায়</b> — {eta}"
    else:
        state = f"✅ <b>পাঠানো হয়েছে</b> — {fmt_ts(ep['published_at'])}\n👍 {likes}  👎 {dis}"
    text = f"🎬 <b>{esc(ep['sname'])}</b> · {esc(ep_label(ep))}\n{esc(ep['title'] or '')}\n\n{marks}\n\n{state}\n🆔 এপিসোড আইডি: <code>{ep_id}</code>"
    rows = []
    if ep["status"] == "pending":
        rows.append([B("📤 এখনই পাঠাও", callback_data=f"rv:p:{ep_id}"), B("⏳ +১০ মিনিট", callback_data=f"rv:w:{ep_id}")])
    rows.append([B("✏️ কোয়ালিটি ঠিক করুন", callback_data=f"rv:q:{ep_id}"), B("🗑 মুছুন", callback_data=f"rv:x:{ep_id}")])
    return text, M(rows)


async def refresh_review(ctx, ep_id: int, create=False):
    ep = Q1("SELECT review FROM eps WHERE id=?", ep_id)
    if not ep:
        return
    try:
        rv = json.loads(ep["review"] or "{}")
    except ValueError:
        rv = {}
    text, kb = review_view(ep_id)
    for a in ADMIN_IDS:
        mid = rv.get(str(a))
        if mid:
            try:
                await ctx.bot.edit_message_text(text, chat_id=a, message_id=mid, reply_markup=kb)
            except TelegramError:
                pass
        elif create:
            st, m = await guarded(lambda: ctx.bot.send_message(a, text, reply_markup=kb))
            if st == "ok" and m:
                rv[str(a)] = m.message_id
    X("UPDATE eps SET review=? WHERE id=?", json.dumps(rv), ep_id)


# ================================================================ কোয়ালিটি বাছাই ও এপিসোড মেসেজ
def mkP(r) -> dict:
    prem = r["plan_until"] == -1 or r["plan_until"] > now()
    maxq = (gi("q_basic") if r["tier"] == "basic" else gi("q_premium")) if prem else gi("q_free")
    try:
        mute = r["mute"] or 0
    except (IndexError, KeyError):
        mute = 0
    try:
        fq = r["fq"] or 0
    except (IndexError, KeyError):
        fq = 0
    return dict(uid=r["user_id"], lang=r["lang"] or "bn", silent=bool(r["silent"] or mute == 1), want=fq or r["q_default"] or 720,
                saver=bool(r["data_saver"]), allq=bool(r["send_all_q"]), maxq=maxq, hist=bool(r["save_hist"]), prem=prem)


def pick_q(avail, want, maxq, saver, allowed):
    """(কোয়ালিটি, অপেক্ষায়?)"""
    avail = list(avail)
    cand = sorted(q for q in avail if q in allowed and q <= maxq)
    if not cand:
        cand = sorted(avail)[:1]
    if saver:
        return cand[0], False
    lower = [q for q in cand if q <= want]
    q = lower[-1] if lower else cand[0]
    waiting = want <= maxq and want in allowed and want not in avail and q < want
    return q, waiting


VCFG_DEF = {"tpl": "", "header": "", "footer": "", "show_size": 1, "show_q": 1, "prem_line": 0, "lang": "",
            "b_save": 1, "b_watch": 1, "b_rate": 1, "b_nav": 1, "b_report": 1, "b_unf": 1, "b_qual": 1,
            "l_save": "", "l_watch": "", "l_next": "", "l_prev": "", "l_report": "", "l_unf": "", "default_q": 0, "order": "", "thumb": ""}
_VC = {"raw": None, "val": None}


def vcfg():
    raw = gs("vcfg")
    if _VC["raw"] != raw:
        try:
            d = json.loads(raw) if raw else {}
        except ValueError:
            d = {}
        _VC["raw"], _VC["val"] = raw, {**VCFG_DEF, **d}
    return _VC["val"]


def ep_caption(sname, ep, q, size, lang, note="", chan="", premline=False):
    c = vcfg()
    ep_t = (f"{ep['ep_no']}" if ep["ep_no"] is not None else "")
    if c["tpl"]:
        out = (c["tpl"].replace("{title}", esc(sname)).replace("{channel}", esc(chan or CHANNEL_LINK or "")).replace("{ep}", ep_t)
               .replace("{time}", local().strftime("%d-%m-%Y %H:%M")).replace("{quality}", f"{q}p").replace("{size}", fmt_size(size) if size else ""))
        lines = [out]
    else:
        lines = [f"📺 <b>{esc(sname)}</b>"]
        if ep["ep_no"] is not None:
            lines.append(f"🎬 {L(lang, 'এপিসোড', 'Episode')} {ep['ep_no']}")
        elif ep["title"]:
            lines.append(f"🎬 {esc(ep['title'][:100])}")
        meta = ([f"{q}p"] if c["show_q"] else []) + ([fmt_size(size)] if size and c["show_size"] else [])
        if meta:
            lines.append("🎞 " + " • ".join(meta))
    if c["header"]:
        lines.insert(0, c["header"])
    if premline:
        lines.append(L(lang, "💎 Premium ইউজার", "💎 Premium user"))
    if note:
        lines.append(note)
    if c["footer"]:
        lines.append(c["footer"])
    return "\n".join(lines)[:1000]


def ep_kb(ep_id, sid, lang, files, cur, maxq, allowed, saved=False, watched=False, rating=None):
    c = vcfg()
    rows = []
    qs = sorted(q for q in files if q in allowed)
    if gs("quality_on") == "1" and len(qs) > 1 and c["b_qual"]:
        rows.append([B(("✅ " if q == cur else ("🔒 " if q > maxq else "")) + f"{q}p" + (f" · {fmt_size(files[q]['size'])}" if files[q]["size"] else ""),
                       callback_data=f"eq:{ep_id}:{q}") for q in qs])
    r2 = []
    if gs("saved_on") == "1" and c["b_save"]:
        r2.append(B(L(lang, "⭐ সেভড" if saved else (c["l_save"] or "⭐ সেভ"), "⭐ Saved" if saved else (c["l_save"] or "⭐ Save")), callback_data=f"es:{ep_id}:{cur}"))
    if c["b_watch"]:
        r2.append(B(L(lang, "✅ দেখা হয়েছে" if watched else (c["l_watch"] or "☑️ দেখেছি"), "✅ Watched" if watched else (c["l_watch"] or "☑️ Watched")), callback_data=f"ew:{ep_id}:{cur}"))
    r2.append(B("▶️ Continue", callback_data="cw"))
    rows.append(r2)
    r3 = []
    if c["b_rate"]:
        r3 += [B("👍✓" if rating == 1 else "👍", callback_data=f"er:{ep_id}:{cur}:1"), B("👎✓" if rating == 0 else "👎", callback_data=f"er:{ep_id}:{cur}:0")]
    if c["b_nav"]:
        r3 += [B(c["l_prev"] or L(lang, "⏮ আগের", "⏮ Prev"), callback_data=f"en:{ep_id}:-1"), B(c["l_next"] or L(lang, "পরের ⏭", "Next ⏭"), callback_data=f"en:{ep_id}:1")]
    if r3:
        rows.append(r3)
    r4 = []
    if c["b_report"]:
        r4.append(B(c["l_report"] or L(lang, "🚩 রিপোর্ট", "🚩 Report"), callback_data=f"ex:{ep_id}:{cur}"))
    if c["b_unf"]:
        r4.append(B(c["l_unf"] or L(lang, "🔕 আনফলো", "🔕 Unfollow"), callback_data=f"uf:{sid}"))
    if r4:
        rows.append(r4)
    return M(rows)


def render_ep(ep_id, uid, want=None):
    ep = Q1("SELECT e.*, s.name AS sname, s.qmask FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    if not ep:
        return None
    files = {r["quality"]: r for r in Q("SELECT * FROM ep_files WHERE episode_id=?", ep_id)}
    if not files:
        return None
    u = Q1("SELECT u.*, f.mute, f.q AS fq FROM users u LEFT JOIN follows f ON f.user_id=u.user_id AND f.serial_id=? WHERE u.user_id=?", ep["serial_id"], uid)
    if not u:
        return None
    P = mkP(u)
    allowed = qset(ep["qmask"])
    if want and want in files:
        cur, waiting = want, False
    else:
        cur, waiting = pick_q(list(files), P["want"], P["maxq"], P["saver"], allowed)
    f = files[cur]
    saved = bool(Q1("SELECT 1 FROM saves WHERE user_id=? AND episode_id=?", uid, ep_id))
    dv = Q1("SELECT watched FROM deliveries WHERE user_id=? AND episode_id=?", uid, ep_id)
    rt = Q1("SELECT val FROM ratings WHERE user_id=? AND episode_id=?", uid, ep_id)
    note = L(P["lang"], f"⏳ {P['want']}p আসছে, আসলে জানাব", f"⏳ {P['want']}p coming — we'll notify you") if waiting else ""
    cap = ep_caption(ep["sname"], ep, cur, f["size"], P["lang"], note)
    kb = ep_kb(ep_id, ep["serial_id"], P["lang"], files, cur, P["maxq"], allowed, saved, bool(dv and dv["watched"]), rt["val"] if rt else None)
    return dict(ep=ep, files=files, cur=cur, file=f, cap=cap, kb=kb, P=P, waiting=waiting, allowed=allowed)


def make_media(kind, file_id, caption):
    kw = dict(caption=caption, parse_mode=ParseMode.HTML)
    if kind == "video":
        return InputMediaVideo(file_id, supports_streaming=True, **kw)
    if kind == "animation":
        return InputMediaAnimation(file_id, **kw)
    if kind == "audio":
        return InputMediaAudio(file_id, **kw)
    return InputMediaDocument(file_id, **kw)


def record_delivery(uid, ep_id, q):
    if gs("history_on") == "1" and V("SELECT save_hist FROM users WHERE user_id=?", uid, d=1):
        X("INSERT OR IGNORE INTO deliveries (user_id, episode_id, sent_at, quality) VALUES (?,?,?,?)", uid, ep_id, now(), q)


async def send_one_ep(ctx, uid, ep_id, want=None, record=True) -> bool:
    R = render_ep(ep_id, uid, want)
    if not R:
        return False
    protect = gs("protect") == "1"
    st, _ = await guarded(lambda: ctx.bot.copy_message(
        chat_id=uid, from_chat_id=LOG_CHANNEL_ID, message_id=R["file"]["message_id"], caption=R["cap"],
        reply_markup=R["kb"], protect_content=protect or None))
    if st == "ok":
        if record:
            record_delivery(uid, ep_id, R["cur"])
        if R["waiting"]:
            X("INSERT OR REPLACE INTO quality_wait (user_id, episode_id, wanted) VALUES (?,?,?)", uid, ep_id, R["P"]["want"])
    return st == "ok"


def can_get(uid: int, ep_id: int) -> bool:
    ep = Q1("SELECT serial_id FROM eps WHERE id=? AND status='published'", ep_id)
    if not ep:
        return False
    if is_admin(uid) or is_premium(user(uid)):
        return True
    return bool(Q1("SELECT 1 FROM follows WHERE user_id=? AND serial_id=?", uid, ep["serial_id"])
                or Q1("SELECT 1 FROM deliveries WHERE user_id=? AND episode_id=?", uid, ep_id)
                or Q1("SELECT 1 FROM saves WHERE user_id=? AND episode_id=?", uid, ep_id))


# ================================================================ ডেলিভারি সময়সূচি (কোয়াইট আওয়ার / ডাইজেস্ট / ছুটি)
def _next_hour(dt, h):
    c = dt.replace(hour=h, minute=0, second=0, microsecond=0)
    if c <= dt:
        c += timedelta(days=1)
    return c


def _in_quiet(h, s, e):
    if s == e:
        return False
    return (s <= h < e) if s < e else (h >= s or h < e)


def schedule_for(r, t=None):
    """(due_ts, digest?)"""
    t = t or now()
    dt = local(t)
    due, digest = dt, False
    mode, dg = (r["deliver_mode"] or "now"), (r["digest"] or "off")
    if dg == "hourly":
        due, digest = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), True
    elif dg == "daily":
        due, digest = _next_hour(dt, 20 if mode == "night" else 8), True
    elif mode == "morning":
        due = _next_hour(dt, 8)
    elif mode == "night":
        due = _next_hour(dt, 20)
    if r["quiet_on"] and _in_quiet(due.hour, r["quiet_start"], r["quiet_end"]):
        due = _next_hour(due, r["quiet_end"])
    hd = r["holiday_dow"]
    if hd is not None and hd >= 0 and due.weekday() == hd:
        due = (due + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return int(due.timestamp()), digest


# ================================================================ পাবলিশ ও ব্রডকাস্ট (দ্রুত, কনকারেন্ট)
FOLLOWERS_SQL = """
SELECT u.*, f.mute, f.q AS fq FROM follows f JOIN users u ON u.user_id = f.user_id
WHERE f.serial_id = ? AND u.banned = 0 AND u.blocked = 0 AND u.notify = 1 AND u.nt_ep = 1 AND f.mute < 2 AND f.pause_until <= ?
  AND (u.plan_until = -1 OR u.plan_until > ? OR (SELECT COUNT(*) FROM follows f2 WHERE f2.user_id = f.user_id AND f2.rowid <= f.rowid) <= ?)
"""


async def send_job(bot, ep, files, allowed, r, protect):
    async with SEM:
        P = mkP(r)
        cur, waiting = pick_q(list(files), P["want"], P["maxq"], P["saver"], allowed)
        qs = [cur]
        if P["allq"] and not P["saver"]:
            qs = sorted(q for q in files if q in allowed and q <= P["maxq"]) or [cur]
        status = "fail"
        if gs("autoplay_on") != "1" or not r["autoplay"] or not ep["autoplay_s"]:
            lang = P["lang"]
            st, _ = await guarded(lambda: bot.send_message(P["uid"], L(lang, f"🔔 <b>{esc(ep['sname'])}</b> — নতুন এপিসোড {esc(ep_label(ep))} এসেছে!", f"🔔 <b>{esc(ep['sname'])}</b> — new episode {esc(ep_label(ep))} is out!"),
                                  reply_markup=M([[B(L(lang, "▶️ এপিসোড নিন", "▶️ Get episode"), callback_data=f"eg:{ep['id']}")]]), disable_notification=P["silent"]))
            return P["uid"], st, cur, 0, False
        note = L(P["lang"], f"⏳ {P['want']}p আসছে, আসলে জানাব", f"⏳ {P['want']}p coming — we'll notify you") if waiting else ""
        for q in qs:
            f = files[q]
            cap = ep_caption(ep["sname"], ep, q, f["size"], P["lang"], note)
            kb = ep_kb(ep["id"], ep["serial_id"], P["lang"], files, q, P["maxq"], allowed)
            st, _ = await guarded(lambda f=f, cap=cap, kb=kb: bot.copy_message(
                chat_id=P["uid"], from_chat_id=LOG_CHANNEL_ID, message_id=f["message_id"], caption=cap, reply_markup=kb,
                disable_notification=P["silent"], protect_content=protect or None))
            status = st
            if st != "ok":
                break
            if r["auto_del"] and _ and getattr(_, "message_id", None):
                X("INSERT INTO autodel (chat_id, message_id, del_at) VALUES (?,?,?)", P["uid"], _.message_id, now() + r["auto_del"] * 3600)
        return P["uid"], status, cur, (P["want"] if waiting else 0), P["hist"]


async def broadcast_episode(ctx, ep_id: int, dry=False, only=None, quiet=False):
    ep = Q1("SELECT e.*, s.name AS sname, s.qmask, s.draft, s.autoplay AS autoplay_s FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    if not ep:
        return None
    files = {r["quality"]: r for r in Q("SELECT * FROM ep_files WHERE episode_id=?", ep_id)}
    if not files:
        return None
    if not dry and only is None and (gs("deliver") != "1" or ep["draft"]):
        await alert_admins(ctx, f"⏸ ডেলিভারি বন্ধ/ড্রাফট সিরিয়াল থাকায় <b>{esc(ep['sname'])}</b> {esc(ep_label(ep))} কাউকে পাঠানো হয়নি।")
        return None
    protect = gs("protect") == "1"
    allowed = qset(ep["qmask"])
    rows = Q(FOLLOWERS_SQL, ep["serial_id"], now(), now(), free_limit()) if only is None else only
    if only is None and gs("test_mode") == "1":
        rows = [r for r in rows if is_admin(r["user_id"]) or r["is_tester"]]
    fdelay = gi("free_delay") * 60
    consent_on, tv = gs("consent_on") == "1", gi("terms_ver")
    st = dict(total=len(rows), ok=0, blocked=0, fail=0, queued=0, skipped=0, digest=0)
    t0 = time.monotonic()
    jobs, qrows = [], []
    for r in rows:
        if consent_on and r["consent_ver"] < tv:
            st["skipped"] += 1
            continue
        due, digest = schedule_for(r)
        if fdelay and only is None and not (r["plan_until"] == -1 or r["plan_until"] > now()) and not is_admin(r["user_id"]):
            due = max(due, now() + fdelay)
        if only is None and due - now() > 5:
            qrows.append((r["user_id"], ep_id, due, "digest" if digest else "ep"))
            st["queued"] += 1
            st["digest"] += 1 if digest else 0
        else:
            jobs.append(r)
    if dry:
        st["would_send"] = len(jobs)
        return st
    XM("INSERT OR IGNORE INTO queue (user_id, episode_id, due_at, kind) VALUES (?,?,?,?)", qrows)
    results = await asyncio.gather(*(send_job(ctx.bot, ep, files, allowed, r, protect) for r in jobs), return_exceptions=True)
    drows, waits, blocked = [], [], []
    for res in results:
        if isinstance(res, Exception):
            st["fail"] += 1
            log.error("send_job error: %s", res)
            continue
        uid, status, cur, wait, hist = res
        if status == "ok":
            st["ok"] += 1
            if hist and gs("history_on") == "1":
                drows.append((uid, ep_id, now(), cur))
            if wait:
                waits.append((uid, ep_id, wait))
        elif status == "blocked":
            st["blocked"] += 1
            blocked.append((uid,))
        else:
            st["fail"] += 1
    XM("INSERT OR IGNORE INTO deliveries (user_id, episode_id, sent_at, quality) VALUES (?,?,?,?)", drows)
    XM("INSERT OR REPLACE INTO quality_wait (user_id, episode_id, wanted) VALUES (?,?,?)", waits)
    XM("UPDATE users SET blocked=1 WHERE user_id=?", blocked)
    secs = time.monotonic() - t0
    X("INSERT INTO delivery_reports (episode_id, at, total, ok, blocked, fail, queued, secs, dry) VALUES (?,?,?,?,?,?,?,?,0)",
      ep_id, now(), st["total"], st["ok"], st["blocked"], st["fail"], st["queued"], secs)
    if not quiet:
        await alert_admins(ctx, f"📤 <b>{esc(ep['sname'])}</b> · {esc(ep_label(ep))}\n\n✅ পাঠানো: {st['ok']}\n🕒 কিউতে (পরে): {st['queued']}\n🔕 ব্লক: {st['blocked']}\n⚠️ ব্যর্থ: {st['fail']}\n"
                           f"👥 মোট ফলোয়ার: {st['total']}\n⏱ সময়: {secs:.1f} সেকেন্ড")
    return st


_ASKED = set()


async def publish_episode(ctx, ep_id: int, force=False) -> bool:
    if not force and gs("autoplay_approve") == "1":
        if ep_id not in _ASKED and V("SELECT COUNT(*) FROM eps WHERE id=? AND status='pending'", ep_id):
            _ASKED.add(ep_id)
            e = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
            n = V("SELECT COUNT(*) FROM follows WHERE serial_id=?", e["serial_id"])
            await alert_admins(ctx, f"🛡 <b>অটোপ্লে অ্যাপ্রুভাল</b>\n📺 {esc(e['sname'])} {esc(ep_label(e))}\n👥 {n} ফলোয়ার\n\nপাঠানোর অনুমতি দিন:",
                               M([[B("✅ পাঠান", callback_data=f"apv:ok:{ep_id}"), B("⏳ ১ ঘণ্টা পরে", callback_data=f"apv:w:{ep_id}"), B("🚫 পাঠাবেন না", callback_data=f"apv:no:{ep_id}")]]))
        return False
    cur = X("UPDATE eps SET status='published', published_at=? WHERE id=? AND status='pending'", now(), ep_id)
    if cur.rowcount != 1:
        return False
    ep = Q1("SELECT * FROM eps WHERE id=?", ep_id)
    X("UPDATE serials SET last_ep_at=?, last_ep_no=COALESCE(?, last_ep_no) WHERE id=?", now(), ep["ep_no"], ep["serial_id"])
    sdirty()
    await refresh_review(ctx, ep_id)
    spawn(broadcast_episode(ctx, ep_id))
    spawn(notify_promo_waiters(ctx, ep_id))
    return True


async def notify_promo_waiters(ctx, ep_id):
    ep = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    rows = Q("SELECT user_id FROM promo_notify WHERE serial_id=?", ep["serial_id"])
    X("DELETE FROM promo_notify WHERE serial_id=?", ep["serial_id"])

    async def one(r):
        async with SEM:
            lang = lg(r["user_id"])
            flw = Q1("SELECT 1 FROM follows WHERE user_id=? AND serial_id=?", r["user_id"], ep["serial_id"])
            kb = M([[B(L(lang, "▶️ এপিসোড নিন", "▶️ Get episode"), callback_data=f"eg:{ep_id}")] if flw else [B(L(lang, "➕ ফলো করুন", "➕ Follow"), callback_data=f"tg:{ep['serial_id']}:0")]])
            return await tsend(ctx.bot, r["user_id"], L(lang, f"🔔 <b>{esc(ep['sname'])}</b>-এর নতুন এপিসোড {esc(ep_label(ep))} এসেছে!", f"🔔 New episode {esc(ep_label(ep))} of <b>{esc(ep['sname'])}</b> is out!"), kb)
    await asyncio.gather(*(one(r) for r in rows), return_exceptions=True)


async def maybe_publish(ctx, ep_id: int) -> bool:
    ep = Q1("SELECT e.*, s.qmask FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    if not ep or ep["status"] != "pending":
        return False
    mode = gs("pub_mode")
    have = {r["quality"] for r in Q("SELECT quality FROM ep_files WHERE episode_id=?", ep_id)}
    if (mode == "window" and gi("pub_window") == 0) or (mode == "all" and qset(ep["qmask"]) <= have):
        return await publish_episode(ctx, ep_id)
    return False


async def notify_quality_ready(ctx, ep_id: int, q: int):
    rows = Q("SELECT user_id FROM quality_wait WHERE episode_id=? AND wanted=?", ep_id, q)
    if not rows:
        return
    ep = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    for r in rows:
        lang = lg(r["user_id"])
        kb = M([[B(L(lang, f"▶️ {q}p এ পান", f"▶️ Get in {q}p"), callback_data=f"eg:{ep_id}:{q}")]])
        await tsend(ctx.bot, r["user_id"], L(lang, f"🔔 <b>{esc(ep['sname'])}</b> {esc(ep_label(ep))}-এর <b>{q}p</b> এখন পাওয়া যাচ্ছে!",
                                             f"🔔 <b>{esc(ep['sname'])}</b> {esc(ep_label(ep))} is now available in <b>{q}p</b>!"), kb)
    X("DELETE FROM quality_wait WHERE episode_id=? AND wanted=?", ep_id, q)


# ================================================================ চ্যানেল পোস্ট হ্যান্ডলার
async def on_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if msg is None:
        return
    f = file_of(msg)
    if not f:
        return
    d = detect(msg)
    low = (msg.caption or "").lower()
    if re.search(r"#promo|#প্রোমো|প্রোমো|\bpromo\b", low) and d["serial"]:
        sid = d["serial"]["id"]
        X("INSERT INTO promos (serial_id, message_id, file_id, kind, caption, created_at, status) VALUES (?,?,?,?,?,?,'live')",
          sid, msg.message_id, f["file_id"], f["kind"], re.sub(r"#?\bpromo\b|#প্রোমো|প্রোমো", "", msg.caption or "", flags=re.I).strip()[:200], now())
        pid = V("SELECT last_insert_rowid()")
        sdirty()
        await alert_admins(ctx, f"🎬 প্রোমো যুক্ত হয়েছে: <b>{esc(d['serial']['name'])}</b> (#{pid})")
        spawn(promo_announce(ctx, pid))
        return
    X("INSERT OR REPLACE INTO posts_log (message_id, text, serial_id, at) VALUES (?,?,?,?)",
      msg.message_id, (msg.caption or f["name"] or "")[:200], d["serial"]["id"] if d["serial"] else None, now())
    if not d["serial"]:
        sug = suggest_serials(d["title"], include_draft=True, limit=3)
        hint = ""
        if sug:
            hint = "\n\n💡 কাছাকাছি: " + ", ".join(esc(Q1("SELECT name FROM serials WHERE id=?", s)["name"]) for _, s in sug)
        await alert_admins(ctx, "⚠️ <b>লগ চ্যানেলের একটি ভিডিওর সিরিয়াল চেনা যায়নি</b>\n"
                                f"🎬 টাইটেল: {esc(d['title']) or '—'}\n\nভিডিওর ক্যাপশন/ফাইলনেমে সিরিয়ালের নাম থাকতে হবে।\n"
                                f"নতুন সিরিয়াল হলে আগে <code>/addfeed নাম</code> দিন।{hint}")
        return
    ep_id, created, replaced = add_file(d, msg.message_id)
    ep = Q1("SELECT status FROM eps WHERE id=?", ep_id)
    if ep["status"] == "pending":
        await refresh_review(ctx, ep_id, create=created)
        await maybe_publish(ctx, ep_id)
    else:
        await refresh_review(ctx, ep_id)
        if not replaced:
            spawn(notify_quality_ready(ctx, ep_id, d["quality"]))


# ================================================================ কিউ / ডাইজেস্ট / ব্যাকগ্রাউন্ড টিকার
async def process_queue(ctx):
    due = Q("SELECT * FROM queue WHERE due_at<=? ORDER BY due_at LIMIT 400", now())
    if not due:
        return
    X(f"DELETE FROM queue WHERE id IN ({','.join('?' * len(due))})", *[r["id"] for r in due])
    digests, jobs, cache = {}, [], {}
    for r in due:
        if r["kind"] == "digest":
            digests.setdefault(r["user_id"], []).append(r["episode_id"])
        else:
            jobs.append(r)

    async def one(r):
        ep_id = r["episode_id"]
        if ep_id not in cache:
            ep = Q1("SELECT e.*, s.name AS sname, s.qmask, s.autoplay AS autoplay_s FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
            files = {x["quality"]: x for x in Q("SELECT * FROM ep_files WHERE episode_id=?", ep_id)}
            cache[ep_id] = (ep, files)
        ep, files = cache[ep_id]
        if not ep or not files:
            return None
        u = Q1("SELECT u.*, f.mute, f.q AS fq FROM users u JOIN follows f ON f.user_id=u.user_id AND f.serial_id=? WHERE u.user_id=? AND u.banned=0 AND u.blocked=0 AND u.notify=1 AND f.mute<2",
               ep["serial_id"], r["user_id"])
        if not u:
            return None
        return await send_job(ctx.bot, ep, files, qset(ep["qmask"]), u, gs("protect") == "1")

    res = await asyncio.gather(*(one(r) for r in jobs), return_exceptions=True)
    drows, blocked = [], []
    for x, r in zip(res, jobs):
        if isinstance(x, Exception) or x is None:
            continue
        uid, status, cur, wait, hist = x
        if status == "ok" and hist and gs("history_on") == "1":
            drows.append((uid, r["episode_id"], now(), cur))
        elif status == "blocked":
            blocked.append((uid,))
    XM("INSERT OR IGNORE INTO deliveries (user_id, episode_id, sent_at, quality) VALUES (?,?,?,?)", drows)
    XM("UPDATE users SET blocked=1 WHERE user_id=?", blocked)
    for uid, eps_ in digests.items():
        lang = lg(uid)
        lines, rows, seen = [], [], set()
        for eid in eps_:
            if eid in seen:
                continue
            seen.add(eid)
            e = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", eid)
            if e:
                lines.append(f"• {esc(e['sname'])} {esc(ep_label(e))}")
                if len(rows) < 10:
                    rows.append([B(f"▶️ {e['sname'][:26]} {ep_label(e)}", callback_data=f"eg:{eid}")])
        if lines:
            await tsend(ctx.bot, uid, L(lang, "📬 <b>নতুন এপিসোড (ডাইজেস্ট)</b>\n\n", "📬 <b>New episodes (digest)</b>\n\n") + "\n".join(lines[:40]) +
                        L(lang, "\n\n👇 ট্যাপ করে এপিসোড নিন", "\n\n👇 Tap to get an episode"), M(rows))


async def tick_publish(ctx):
    for r in Q("SELECT id FROM eps WHERE status='pending' AND publish_at>0 AND publish_at<=?", now()):
        await publish_episode(ctx, r["id"])


async def tick_minute(ctx):
    t = now()
    # সেভ রিমাইন্ডার
    for r in Q("SELECT s.user_id, s.episode_id, e.serial_id, se.name AS sname, e.ep_no, e.title FROM saves s JOIN eps e ON e.id=s.episode_id JOIN serials se ON se.id=e.serial_id WHERE s.remind_at>0 AND s.remind_at<=?", t):
        X("UPDATE saves SET remind_at=0 WHERE user_id=? AND episode_id=?", r["user_id"], r["episode_id"])
        if V("SELECT watched FROM deliveries WHERE user_id=? AND episode_id=?", r["user_id"], r["episode_id"]) or not V("SELECT nt_rem FROM users WHERE user_id=?", r["user_id"], d=1):
            continue
        lang = lg(r["user_id"])
        await tsend(ctx.bot, r["user_id"], L(lang, f"⏰ <b>পরে দেখব</b> রিমাইন্ডার: {esc(r['sname'])} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else ''}",
                                             f"⏰ <b>Watch later</b> reminder: {esc(r['sname'])} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else ''}"),
                    M([[B(L(lang, "▶️ এখন দেখুন", "▶️ Watch now"), callback_data=f"eg:{r['episode_id']}")]]))
        nlog(r["user_id"], "rem", "⏰ পরে দেখব")
    # Premium সতর্কতা ও শেষ
    for r in Q("SELECT user_id, plan_until FROM users WHERE plan_until>0 AND plan_until<=?", t):
        X("UPDATE users SET plan_until=0 WHERE user_id=?", r["user_id"])
        X("INSERT INTO plan_history (user_id, plan, until, at, by, note) VALUES (?,?,?,?,0,'expired')", r["user_id"], "expired", 0, t)
        lang = lg(r["user_id"])
        body, _, _ = get_text("expired", lang)
        await tsend(ctx.bot, r["user_id"], fill(body, user(r["user_id"]), lang=lang))
        nlog(r["user_id"], "plan", "⌛ Premium শেষ")
    for r in Q("SELECT user_id, plan_until FROM users WHERE plan_until>? AND plan_until<=? AND exp_warned<>plan_until", t, t + 3 * 86400):
        X("UPDATE users SET exp_warned=plan_until WHERE user_id=?", r["user_id"])
        lang = lg(r["user_id"])
        await tsend(ctx.bot, r["user_id"], L(lang, f"⚠️ আপনার 💎 Premium-এর মেয়াদ শেষ হচ্ছে: <b>{fmt_ts(r['plan_until'])}</b>\nরিনিউ করতে /plan দেখুন।",
                                             f"⚠️ Your 💎 Premium expires on <b>{fmt_ts(r['plan_until'])}</b>\nRenew via /plan."))
        nlog(r["user_id"], "plan", "⚠️ মেয়াদ শেষ হচ্ছে")
    # 1080p নেই অ্যালার্ট
    if gs("alert_q_missing") == "1":
        for r in Q("SELECT e.id, e.ep_no, s.name, s.qmask FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.status='published' AND e.q_alerted=0 AND e.published_at<=? AND e.published_at>?", t - 3600, t - 86400):
            X("UPDATE eps SET q_alerted=1 WHERE id=?", r["id"])
            have = {x["quality"] for x in Q("SELECT quality FROM ep_files WHERE episode_id=?", r["id"])}
            miss = sorted(qset(r["qmask"]) - have)
            if 1080 in miss:
                await alert_admins(ctx, f"⏰ <b>{esc(r['name'])}</b> E{r['ep_no'] if r['ep_no'] is not None else '?'} — এক ঘণ্টা পরেও {', '.join(str(m) + 'p' for m in miss)} আপলোড হয়নি।")
    # অপসারণ অনুরোধ রিমাইন্ডার
    for r in Q("SELECT id, target FROM removals WHERE status='pending' AND reminded=0 AND created_at<=?", t - 86400):
        X("UPDATE removals SET reminded=1 WHERE id=?", r["id"])
        await alert_admins(ctx, f"⏰ অপসারণ অনুরোধ <b>#{r['id']}</b> ({esc(r['target'])[:60]}) ২৪ ঘণ্টা ধরে অপেক্ষায়। /removals")


async def tick_extra(ctx):
    t = now()
    await tick_wsched(ctx)
    await tick_promosrc(ctx)
    await tick_sms(ctx)
    for r in Q("SELECT rowid AS rid, chat_id, message_id FROM autodel WHERE del_at<=? LIMIT 200", t):
        X("DELETE FROM autodel WHERE rowid=?", r["rid"])
        await guarded(lambda r=r: ctx.bot.delete_message(r["chat_id"], r["message_id"]))
    for r in Q("SELECT id, message_id FROM promos WHERE status='live' AND created_at<=?", t - max(1, gi("promo_days")) * 86400):
        X("UPDATE promos SET status='archived' WHERE id=?", r["id"])
    for r in Q("SELECT id, serial_id, file_id, kind, caption FROM promos WHERE status='sched' AND sched_at>0 AND sched_at<=?", t):
        X("UPDATE promos SET status='live', created_at=? WHERE id=?", t, r["id"])
        spawn(promo_announce(ctx, r["id"]))
    # প্ল্যান রিমাইন্ডার (৩ দিন ও ১ দিন)
    for days in (3, 1):
        for r in Q("SELECT user_id, plan_until FROM users WHERE plan_until>? AND plan_until<=? AND nt_plan=1 AND exp_warned<>plan_until*10+?", t, t + days * 86400, days):
            X("UPDATE users SET exp_warned=plan_until*10+? WHERE user_id=?", days, r["user_id"])
            lang = lg(r["user_id"])
            await tsend(ctx.bot, r["user_id"], L(lang, f"⏰ আপনার 💎 Premium আর <b>{days} দিন</b> পর শেষ হবে ({fmt_ts(r['plan_until'])})।", f"⏰ Your 💎 Premium ends in <b>{days} day(s)</b> ({fmt_ts(r['plan_until'])})."),
                        M([[B(L(lang, "🔄 এক ট্যাপে রিনিউ", "🔄 Renew in one tap"), callback_data="tpr")]]))


async def send_promo(bot, uid, p, lang, kb, spoil):
    cap = L(lang, f"🎬 <b>প্রোমো — {esc(p['sname'])}</b>\n{esc(p['caption'])}", f"🎬 <b>Promo — {esc(p['sname'])}</b>\n{esc(p['caption'])}")
    link = f"\n🔗 {esc(p['link'])}" if "link" in p.keys() and p["link"] else ""
    if spoil or p["spoiler"]:
        sc = f"<tg-spoiler>{cap}{link}</tg-spoiler>"[:1000]
        if p["file_id"] and p["kind"] == "video":
            return (await guarded(lambda: bot.send_video(uid, p["file_id"], caption=sc, reply_markup=kb, has_spoiler=True)))[0]
        if p["file_id"] and p["kind"] == "photo":
            return (await guarded(lambda: bot.send_photo(uid, p["file_id"], caption=sc, reply_markup=kb, has_spoiler=True)))[0]
        return (await guarded(lambda: bot.send_message(uid, sc, reply_markup=kb)))[0]
    if p["message_id"]:
        return (await guarded(lambda: bot.copy_message(uid, LOG_CHANNEL_ID, p["message_id"], caption=(cap + link)[:1000], reply_markup=kb)))[0]
    if p["file_id"] and p["kind"] == "photo":
        return (await guarded(lambda: bot.send_photo(uid, p["file_id"], caption=(cap + link)[:1000], reply_markup=kb)))[0]
    return (await guarded(lambda: bot.send_message(uid, cap + link, reply_markup=kb)))[0]


def promo_kb(p, lang):
    return M([[B(L(lang, "📺 সিরিয়াল পেজ", "📺 Serial page"), callback_data=f"sp:{p['serial_id']}"), B(L(lang, "🔔 এপিসোড এলে জানাও", "🔔 Notify me"), callback_data=f"pvn:{p['serial_id']}")]])


async def promo_announce(ctx, promo_id):
    """প্রোমো ফলোয়ারদের কাছে পাঠানো"""
    p = Q1("SELECT p.*, s.name AS sname, s.draft FROM promos p JOIN serials s ON s.id=p.serial_id WHERE p.id=?", promo_id)
    if not p or p["draft"]:
        return
    rows = Q("SELECT u.user_id, u.lang, u.plan_until, u.promo_spoil FROM follows f JOIN users u ON u.user_id=f.user_id WHERE f.serial_id=? AND f.promo_off=0 AND u.nt_promo=1 AND u.banned=0 AND u.blocked=0", p["serial_id"])
    if p["prem_only"]:
        rows = [r for r in rows if r["plan_until"] == -1 or r["plan_until"] > now()]

    async def one(r):
        async with SEM:
            lang = r["lang"] or "bn"
            return await send_promo(ctx.bot, r["user_id"], p, lang, promo_kb(p, lang), r["promo_spoil"])
    res = await asyncio.gather(*(one(r) for r in rows), return_exceptions=True)
    X("UPDATE promos SET views=views+? WHERE id=?", sum(1 for x in res if x == "ok"), promo_id)
    X("UPDATE serials SET promo_at=? WHERE id=?", now(), p["serial_id"])


async def tick_cleanup(ctx):
    if now() - gi("last_cleanup") < 86400:
        return
    ss("last_cleanup", now())
    days = max(7, gi("hist_days"))
    X("DELETE FROM deliveries WHERE sent_at<?", now() - days * 86400)
    X("DELETE FROM notif_log WHERE at<?", now() - 30 * 86400)
    X("DELETE FROM posts_log WHERE at<?", now() - 30 * 86400)
    X("DELETE FROM searches WHERE at<?", now() - 90 * 86400)
    X("DELETE FROM queue WHERE due_at<?", now() - 2 * 86400)
    X("DELETE FROM quality_wait WHERE episode_id NOT IN (SELECT id FROM eps WHERE published_at>?)", now() - 14 * 86400)
    db.execute("PRAGMA optimize")


async def ticker(app):
    ctx = Ctx(app)
    n = 0
    while True:
        try:
            await asyncio.sleep(15)
            n += 1
            await tick_publish(ctx)
            await process_queue(ctx)
            if n % 4 == 0:
                await tick_minute(ctx)
                await tick_extra(ctx)
            if n % 240 == 0:
                await tick_cleanup(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ticker error")


# ================================================================ এপিসোডের বাটন কলব্যাক
def _lock_alert(lang):
    return L(lang, "🔒 এই কোয়ালিটি শুধু 💎 Premium-এর জন্য। /plan দেখুন।", "🔒 This quality is for 💎 Premium only. See /plan.")


async def swap_media(q, R):
    f = R["file"]
    if not f["file_id"]:
        return False
    try:
        await q.edit_message_media(media=make_media(f["kind"], f["file_id"], R["cap"]), reply_markup=R["kb"])
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
        log.warning("edit media failed: %s", e)
    except TelegramError as e:
        log.warning("edit media failed: %s", e)
    return False


@route("eq")
async def cb_eq(update, ctx, q, p):
    uid, ep_id, want = q.from_user.id, int(p[1]), int(p[2])
    R = render_ep(ep_id, uid, want)
    if not R:
        return await ans(q, "❌ পাওয়া যায়নি", True)
    if want > R["P"]["maxq"]:
        return await ans(q, _lock_alert(R["P"]["lang"]), True)
    if await swap_media(q, R):
        X("UPDATE deliveries SET quality=? WHERE user_id=? AND episode_id=?", want, uid, ep_id)
        return await ans(q, f"🎞 {want}p")
    await send_one_ep(ctx, uid, ep_id, want, record=False)
    await ans(q, f"🎞 {want}p")


async def _refresh_kb(q, ep_id, uid, cur):
    R = render_ep(ep_id, uid, cur)
    if R:
        try:
            await q.edit_message_reply_markup(reply_markup=R["kb"])
        except TelegramError:
            pass


@route("es")
async def cb_es(update, ctx, q, p):
    uid, ep_id, cur = q.from_user.id, int(p[1]), int(p[2])
    if gs("saved_on") != "1":
        return await ans(q, "⭐ সেভ ফিচার বন্ধ আছে", True)
    if Q1("SELECT 1 FROM saves WHERE user_id=? AND episode_id=?", uid, ep_id):
        X("DELETE FROM saves WHERE user_id=? AND episode_id=?", uid, ep_id)
        toast = L(lg(uid), "⭐ সেভ থেকে বাদ", "⭐ Removed from saved")
    else:
        X("INSERT OR IGNORE INTO saves (user_id, episode_id, created_at) VALUES (?,?,?)", uid, ep_id, now())
        toast = L(lg(uid), "⭐ সেভ হয়েছে — /saved এ দেখুন", "⭐ Saved — see /saved")
    await _refresh_kb(q, ep_id, uid, cur)
    await ans(q, toast)


@route("ew")
async def cb_ew(update, ctx, q, p):
    uid, ep_id, cur = q.from_user.id, int(p[1]), int(p[2])
    record_delivery(uid, ep_id, cur)
    row = Q1("SELECT watched FROM deliveries WHERE user_id=? AND episode_id=?", uid, ep_id)
    if not row:
        return await ans(q, L(lg(uid), "ইতিহাস বন্ধ থাকায় ট্র্যাক হচ্ছে না", "History is off, not tracked"), True)
    nv = 0 if row["watched"] else 1
    X("UPDATE deliveries SET watched=?, watched_at=? WHERE user_id=? AND episode_id=?", nv, now() if nv else 0, uid, ep_id)
    await _refresh_kb(q, ep_id, uid, cur)
    if nv and V("SELECT auto_next FROM users WHERE user_id=?", uid):
        nx = Q1("SELECT n.id FROM eps e JOIN eps n ON n.serial_id=e.serial_id AND n.status='published' AND n.id>e.id WHERE e.id=? ORDER BY n.id LIMIT 1", ep_id)
        if nx and can_get(uid, nx["id"]):
            spawn(send_one_ep(ctx, uid, nx["id"]))
    await ans(q, "✅" if nv else "☑️")


@route("er")
async def cb_er(update, ctx, q, p):
    uid, ep_id, cur, val = q.from_user.id, int(p[1]), int(p[2]), int(p[3])
    old = Q1("SELECT val FROM ratings WHERE user_id=? AND episode_id=?", uid, ep_id)
    if old and old["val"] == val:
        X("DELETE FROM ratings WHERE user_id=? AND episode_id=?", uid, ep_id)
    else:
        X("INSERT OR REPLACE INTO ratings (user_id, episode_id, val) VALUES (?,?,?)", uid, ep_id, val)
    await _refresh_kb(q, ep_id, uid, cur)
    await ans(q, L(lg(uid), "ধন্যবাদ! 🙏", "Thanks! 🙏"))


@route("en")
async def cb_en(update, ctx, q, p):
    uid, ep_id, d = q.from_user.id, int(p[1]), int(p[2])
    ep = Q1("SELECT serial_id FROM eps WHERE id=?", ep_id)
    if not ep:
        return await ans(q, "❌", True)
    if d > 0:
        n = Q1("SELECT id FROM eps WHERE serial_id=? AND status='published' AND id>? ORDER BY id LIMIT 1", ep["serial_id"], ep_id)
    else:
        n = Q1("SELECT id FROM eps WHERE serial_id=? AND status='published' AND id<? ORDER BY id DESC LIMIT 1", ep["serial_id"], ep_id)
    lang = lg(uid)
    if not n:
        return await ans(q, L(lang, "এটাই শেষ এপিসোড" if d > 0 else "এটাই প্রথম এপিসোড", "This is the last episode" if d > 0 else "This is the first episode"), True)
    if not can_get(uid, n["id"]):
        return await ans(q, L(lang, "আগে সিরিয়ালটি ফলো করুন (বা 💎 Premium নিন)", "Follow the serial first (or get 💎 Premium)"), True)
    R = render_ep(n["id"], uid)
    if not R:
        return await ans(q, "❌", True)
    if await swap_media(q, R):
        record_delivery(uid, n["id"], R["cur"])
        return await ans(q)
    await send_one_ep(ctx, uid, n["id"])
    await ans(q)


@route("eg")
async def cb_eg(update, ctx, q, p):
    uid, ep_id = q.from_user.id, int(p[1])
    want = int(p[2]) if len(p) > 2 else None
    lang = lg(uid)
    if not can_get(uid, ep_id):
        e = Q1("SELECT id FROM eps WHERE id=?", ep_id)
        return await ans(q, L(lang, "⚠️ এপিসোডটি আর পাওয়া যাচ্ছে না বা অ্যাক্সেস নেই।", "⚠️ Episode no longer available or no access."), True)
    ok = await send_one_ep(ctx, uid, ep_id, want)
    await ans(q, "✅" if ok else L(lang, "⚠️ পাঠানো যায়নি", "⚠️ Could not send"), not ok)


@route("ex")
async def cb_ex(update, ctx, q, p):
    ep_id, cur = int(p[1]), int(p[2])
    lang = lg(q.from_user.id)
    kb = M([[B(L(lang, "🎞 ভিডিও চলে না", "🎞 Video won't play"), callback_data=f"exr:{ep_id}:v:{cur}"),
             B(L(lang, "🔊 অডিও/সাবটাইটেল", "🔊 Audio/subtitle"), callback_data=f"exr:{ep_id}:a:{cur}")],
            [B(L(lang, "❌ ভুল এপিসোড", "❌ Wrong episode"), callback_data=f"exr:{ep_id}:w:{cur}"),
             B(L(lang, "📝 অন্য", "📝 Other"), callback_data=f"exr:{ep_id}:o:{cur}")],
            [B(L(lang, "↩️ ফিরুন", "↩️ Back"), callback_data=f"exb:{ep_id}:{cur}")]])
    try:
        await q.edit_message_reply_markup(reply_markup=kb)
    except TelegramError:
        pass
    await ans(q)


@route("exb")
async def cb_exb(update, ctx, q, p):
    await _refresh_kb(q, int(p[1]), q.from_user.id, int(p[2]))
    await ans(q)


REASONS = {"v": "ভিডিও চলে না", "a": "অডিও/সাবটাইটেল সমস্যা", "w": "ভুল এপিসোড", "o": "অন্য সমস্যা"}


@route("exr")
async def cb_exr(update, ctx, q, p):
    uid, ep_id, code, cur = q.from_user.id, int(p[1]), p[2], int(p[3])
    cur_ = X("INSERT INTO reports (user_id, episode_id, reason, created_at) VALUES (?,?,?,?)", uid, ep_id, REASONS.get(code, "অন্য"), now())
    rid = cur_.lastrowid
    ep = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    u = q.from_user
    await alert_admins(ctx, f"🚩 <b>ভিডিও রিপোর্ট #{rid}</b>\n📺 {esc(ep['sname']) if ep else '?'} {esc(ep_label(ep)) if ep else ''} ({cur}p)\n"
                            f"👤 {esc(u.full_name)} • <code>{u.id}</code>\n⚠️ {REASONS.get(code, '')}",
                       M([[B("✅ সমাধান", callback_data=f"rp:ok:{rid}"), B("💬 ইউজারকে জানান", callback_data=f"rp:msg:{rid}")]]))
    await _refresh_kb(q, ep_id, uid, cur)
    await ans(q, L(lg(uid), "🚩 রিপোর্ট এডমিনের কাছে পাঠানো হয়েছে। ধন্যবাদ!", "🚩 Report sent to admin. Thanks!"), True)


@route("uf")
async def cb_uf(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    X("DELETE FROM follows WHERE user_id=? AND serial_id=?", uid, sid)
    row = Q1("SELECT name FROM serials WHERE id=?", sid)
    lang = lg(uid)
    try:
        await q.edit_message_reply_markup(reply_markup=M([[B(L(lang, "↩️ আবার ফলো করুন", "↩️ Follow again"), callback_data=f"rf:{sid}")]]))
    except TelegramError:
        pass
    await ans(q, L(lang, f"🔕 আনফলো: {row['name'] if row else ''}", f"🔕 Unfollowed: {row['name'] if row else ''}"), True)


@route("rf")
async def cb_rf(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    lang = lg(uid)
    if not can_follow(uid):
        return await ans(q, L(lang, "⚠️ ফলো লিমিট পূর্ণ। /plan", "⚠️ Follow limit reached. /plan"), True)
    X("INSERT OR IGNORE INTO follows (user_id, serial_id, created_at) VALUES (?,?,?)", uid, sid, now())
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await ans(q, L(lang, "✅ আবার ফলো করা হয়েছে", "✅ Followed again"), True)


# ---- এডমিন: রিভিউ কার্ড বাটন ও রিপোর্ট
@route("rv")
async def cb_rv(update, ctx, q, p):
    if not is_admin(q.from_user.id):
        return await ans(q, "⛔", True)
    act, ep_id = p[1], int(p[2])
    ep = Q1("SELECT * FROM eps WHERE id=?", ep_id)
    if not ep:
        return await ans(q, "❌ নেই", True)
    if act == "p":
        ok = await publish_episode(ctx, ep_id)
        alog(q.from_user.id, "publish", ep_id)
        return await ans(q, "📤 পাঠানো শুরু" if ok else "আগেই পাঠানো হয়েছে")
    if act == "w":
        X("UPDATE eps SET publish_at=? WHERE id=? AND status='pending'", max(now(), ep["publish_at"] or 0) + 600, ep_id)
        await refresh_review(ctx, ep_id)
        return await ans(q, "⏳ ১০ মিনিট বাড়ানো হয়েছে")
    if act == "x":
        if len(p) < 4:
            return await show(update, ctx, "⚠️ এই এপিসোড মুছে ফেলবেন? সেভ/ইতিহাস থেকেও সরে যাবে, ইউজারকে জানানো হবে।",
                              M([[B("✅ হ্যাঁ, মুছুন", callback_data=f"rv:x:{ep_id}:1"), B("❌ না", callback_data=f"rv:b:{ep_id}")]]))
        await delete_episode(ctx, ep_id, q.from_user.id)
        return await show(update, ctx, "🗑 এপিসোড মুছে ফেলা হয়েছে।")
    if act == "b":
        text, kb = review_view(ep_id)
        return await show(update, ctx, text, kb)
    if act == "q":
        files = Q("SELECT quality FROM ep_files WHERE episode_id=? ORDER BY quality", ep_id)
        if len(p) == 3:
            rows = [[B(f"{r['quality']}p ফাইলটি ভুল?", callback_data=f"rv:q:{ep_id}:{r['quality']}")] for r in files]
            rows.append([B("↩️", callback_data=f"rv:b:{ep_id}")])
            return await show(update, ctx, "✏️ কোন ফাইলের কোয়ালিটি বদলাবেন?", M(rows))
        old = int(p[3])
        if len(p) == 4:
            rows = [[B(f"→ {n}p", callback_data=f"rv:q:{ep_id}:{old}:{n}") for n in QUALS if n != old], [B("↩️", callback_data=f"rv:b:{ep_id}")]]
            return await show(update, ctx, f"{old}p ফাইলটির সঠিক কোয়ালিটি কত?", M(rows))
        new = int(p[4])
        a = Q1("SELECT * FROM ep_files WHERE episode_id=? AND quality=?", ep_id, old)
        b = Q1("SELECT * FROM ep_files WHERE episode_id=? AND quality=?", ep_id, new)
        if a:
            X("DELETE FROM ep_files WHERE episode_id=? AND quality IN (?,?)", ep_id, old, new)
            X("INSERT INTO ep_files (episode_id, quality, message_id, file_id, kind, size) VALUES (?,?,?,?,?,?)", ep_id, new, a["message_id"], a["file_id"], a["kind"], a["size"])
            if b:
                X("INSERT INTO ep_files (episode_id, quality, message_id, file_id, kind, size) VALUES (?,?,?,?,?,?)", ep_id, old, b["message_id"], b["file_id"], b["kind"], b["size"])
        alog(q.from_user.id, "fix_quality", ep_id, f"{old}->{new}")
        await refresh_review(ctx, ep_id)
        text, kb = review_view(ep_id)
        return await show(update, ctx, text, kb)
    await ans(q)


async def delete_episode(ctx, ep_id: int, admin_id=0):
    ep = Q1("SELECT e.*, s.name AS sname FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.id=?", ep_id)
    if not ep:
        return
    users = [r["user_id"] for r in Q("SELECT user_id FROM saves WHERE episode_id=?", ep_id)]
    for t in ("ep_files", "deliveries", "saves", "ratings", "queue", "quality_wait"):
        X(f"DELETE FROM {t} WHERE episode_id=?", ep_id)
    X("DELETE FROM eps WHERE id=?", ep_id)
    alog(admin_id, "ep_delete", ep_id, f"{ep['sname']} {ep_label(ep)}")
    for uid in users:
        lang = lg(uid)
        await tsend(ctx.bot, uid, L(lang, f"ℹ️ সেভ করা এপিসোড <b>{esc(ep['sname'])} {esc(ep_label(ep))}</b> সরিয়ে ফেলা হয়েছে, তাই সেভ তালিকা থেকেও বাদ দেওয়া হলো।",
                                    f"ℹ️ Saved episode <b>{esc(ep['sname'])} {esc(ep_label(ep))}</b> was removed, so it was removed from your saved list."))


@route("rp")
async def cb_rp(update, ctx, q, p):
    if not is_admin(q.from_user.id):
        return await ans(q, "⛔", True)
    rid = int(p[2])
    r = Q1("SELECT * FROM reports WHERE id=?", rid)
    if not r:
        return await ans(q, "নেই", True)
    if p[1] == "ok":
        X("UPDATE reports SET status='done' WHERE id=?", rid)
        await tsend(ctx.bot, r["user_id"], L(lg(r["user_id"]), "✅ আপনার রিপোর্ট করা সমস্যাটি সমাধান করা হয়েছে। ধন্যবাদ!", "✅ The issue you reported has been fixed. Thanks!"))
        try:
            await q.edit_message_text(q.message.text_html + "\n\n✅ <b>সমাধান হয়েছে</b>", parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
        return await ans(q, "✅")
    set_await(ctx, "report_msg", rid)
    await ans(q, "✍️ ইউজারকে যা জানাতে চান লিখে পাঠান", True)


# ================================================================ গেট: রেজিস্ট্রেশন, ব্যান, মেইনটেন্যান্স, সম্মতি, প্ল্যান-লক
LOCK_OK = {"h", "tp", "tpm", "tpx", "tps", "tpc", "tpr", "su", "tm", "tmp", "tc", "lang", "noop", "hp", "pl", "tkv", "tku", "tmx", "tmd"}
LOCK_CMDS = {"/start", "/tkply", "/pay", "/support", "/terms", "/help", "/cancel", "/plan"}
CONSENT_CB = {"tc", "tmp", "lang", "noop", "tm"}


async def gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or u.is_bot:
        return
    chat = update.effective_chat
    if chat is not None and chat.type != "private":
        return
    t = now()
    row = user(u.id)
    if row is None:
        trial = gi("trial_days")
        X("INSERT INTO users (user_id, name, username, joined_at, last_seen, lang, plan_until) VALUES (?,?,?,?,?,'bn',?)",
          u.id, u.full_name, u.username, t, t, t + trial * 86400 if trial > 0 else 0)
        if trial > 0:
            X("INSERT INTO plan_history (user_id, plan, until, at, by, note) VALUES (?,?,?,?,0,'trial')", u.id, f"trial{trial}", t + trial * 86400, t)
        ref = 0
        m = update.message
        if m and m.text and m.text.startswith("/start") and "ref_" in m.text:
            try:
                ref = int(m.text.split("ref_")[1].split()[0])
            except (ValueError, IndexError):
                ref = 0
        if ref and ref != u.id and user(ref):
            X("UPDATE users SET ref=? WHERE user_id=?", ref, u.id)
        row = user(u.id)
        if gs("alert_new") == "1" and not is_admin(u.id):
            total = V("SELECT COUNT(*) FROM users")
            un = f"@{esc(u.username)}" if u.username else "—"
            spawn(alert_admins(Ctx(ctx.application), f"🆕 <b>নতুন ইউজার</b>\n👤 {esc(u.full_name)} ({un})\n🆔 <code>{u.id}</code>\n"
                                                      f"{'🔗 রেফারার: <code>' + str(ref) + '</code>' + chr(10) if ref else ''}👥 মোট: {total}"))
    elif t - (row["last_seen"] or 0) > 60 or row["name"] != u.full_name or row["username"] != u.username or row["blocked"]:
        X("UPDATE users SET name=?, username=?, last_seen=?, blocked=0 WHERE user_id=?", u.full_name, u.username, t, u.id)
    _LANG[u.id] = row["lang"] or "bn"
    if is_admin(u.id):
        return
    lang = _LANG[u.id]
    cb = update.callback_query
    cmd0 = ""
    if update.message and update.message.text and update.message.text.startswith("/"):
        cmd0 = update.message.text.split()[0].split("@")[0].lower()

    async def block(text, kb=None):
        if cb:
            await ans(cb, re.sub(r"<[^>]+>", "", text)[:190], True)
        elif update.effective_message:
            await reply(update, text, kb)
        raise ApplicationHandlerStop

    if row["banned"]:
        r = f"\n📝 কারণ: {esc(row['ban_reason'])}" if row["ban_reason"] else ""
        await block(L(lang, f"🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।{r}\n📞 আপিল: ", f"🚫 You are banned from this bot.{r}\n📞 Appeal: ") + (ADMIN_CONTACT or "—"))
    if gs("maintenance") == "1":
        await block(L(lang, "🛠 <b>কাজ চলছে</b>\nকিছুক্ষণ পর আবার চেষ্টা করুন।", "🛠 <b>Under maintenance</b>\nPlease try again shortly."))
    if gs("consent_on") == "1" and row["consent_ver"] < gi("terms_ver"):
        ok = (cb.data.split(":")[0] in CONSENT_CB) if cb else (cmd0 in ("/start", "/terms"))
        if not ok and (cb or update.message):
            if cb:
                await ans(cb, L(lang, "আগে নিয়মে সম্মত হোন — /start", "Please accept the terms first — /start"), True)
            else:
                t_, kb_ = consent_view(u.id)
                await reply(update, t_, kb_)
            raise ApplicationHandlerStop
    if gs("require_plan") == "1" and gs("free_for_all") != "1" and not is_premium(row) and not row["whitelist"]:
        ok = (cb.data.split(":")[0] in LOCK_OK) if cb else (cmd0 in LOCK_CMDS or bool(ctx.user_data.get("aw")))
        if not ok and (cb or update.message):
            ss("lock_clicks", gi("lock_clicks") + 1)
            await block(L(lang, "🔒 <b>আপনার কোনো সাবস্ক্রিপশন নেই।</b>\nবট ব্যবহার করতে প্ল্যান কিনুন।", "🔒 <b>You have no active subscription.</b>\nBuy a plan to use the bot."),
                        M([[B(L(lang, "💳 প্ল্যান কিনুন", "💳 Buy plan"), callback_data="tp"), B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")]]))


# ================================================================ হোম
HOME_BTNS = {
    "serials": ("📺 সিরিয়াল তালিকা", "📺 Serials", "ls:0"), "feed": ("📌 আমার ফিড", "📌 My feed", "fd:0"),
    "search": ("🔍 সার্চ", "🔍 Search", "sq"), "saved": ("⭐ সেভ করা", "⭐ Saved", "sv:0"),
    "history": ("🕘 ইতিহাস", "🕘 History", "hs"), "trending": ("🔥 ট্রেন্ডিং", "🔥 Trending", "tr"),
    "newser": ("🆕 নতুন যোগ", "🆕 New", "nw"), "promos": ("🎬 প্রোমো", "🎬 Promos", "pr:0"),
    "notification": ("🔔 নোটিফিকেশন", "🔔 Notifications", "nf"), "autoplay": ("▶️ অটোপ্লে", "▶️ Autoplay", "ap"),
    "settings": ("⚙️ সেটিংস", "⚙️ Settings", "st"), "plan": ("💎 প্ল্যান", "💎 Plan", "pl"),
    "help": ("❓ হেল্প", "❓ Help", "hp"), "support": ("📩 সাপোর্ট", "📩 Support", "su"), "terms": ("📜 নিয়ম", "📜 Terms", "tm"),
}
DEFAULT_LAYOUT = list(HOME_BTNS)


def layout():
    try:
        d = json.loads(gs("home_layout") or "{}")
    except ValueError:
        d = {}
    order = [x for x in d.get("order", []) if x in HOME_BTNS]
    order += [x for x in DEFAULT_LAYOUT if x not in order]
    return order, set(d.get("off", []))


def home_view(uid, name, lang, override=None):
    base = user(uid)
    u = dict(base) if base else {"user_id": uid, "name": name, "plan_until": 0, "onboarded": 2}
    if override:
        u.update({k: v for k, v in override.items() if not k.startswith("_")})
    new = (u["onboarded"] < 2) if not override else bool(override.get("_new"))
    key = "welcome_new" if new else ("welcome_premium" if is_premium(u) else "welcome_old")
    if new and gs("ab_on") == "1" and not override:
        ab = (base["ab"] if base else "") or ("b" if uid % 2 else "a")
        if base and not base["ab"]:
            X("UPDATE users SET ab=? WHERE user_id=?", ab, uid)
        if ab == "b":
            key = "welcome_new_b"
    body, banner, cbtns = get_text(key, lang)
    text = fill(body, u, name, uid, lang)
    ex = []
    hol = Q1("SELECT reason FROM holidays WHERE day=?", local().strftime("%Y-%m-%d"))
    if hol:
        ex.append(f"🎉 <b>{L(lang, 'আজ ছুটি', 'Holiday today')}:</b> {esc(hol['reason'])}")
    today = Q("SELECT s.name, s.last_ep_no FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? AND s.last_ep_at>=? AND s.draft=0 ORDER BY s.last_ep_at DESC LIMIT 5", uid, day_start())
    if today:
        ex.append(f"🆕 <b>{L(lang, 'আজকের নতুন এপিসোড', 'New today')}</b>\n" + "\n".join(f"• {esc(r['name'])}" + (f" E{r['last_ep_no']}" if r["last_ep_no"] else "") for r in today))
    if not is_premium(u):
        fc = follow_count(uid)
        ex.append(f"📊 {bar(fc, free_limit())} {fc}/{free_limit()}")
    elif u["plan_until"] > 0 and u["plan_until"] - now() <= 3 * 86400:
        ex.append(L(lang, f"⚠️ মেয়াদ শেষ হচ্ছে: {fmt_ts(u['plan_until'])} — রিনিউ করুন", f"⚠️ Expiring: {fmt_ts(u['plan_until'])} — renew now"))
    if not u.get("notify", 1):
        ex.append(L(lang, "🔕 আপনার নোটিফিকেশন বন্ধ — /notification", "🔕 Notifications are off — /notification"))
    if gs("free_for_all") == "1":
        ex.append(L(lang, "🎁 এখন সবার জন্য ফ্রি মোড চালু!", "🎁 Free-for-all mode is ON!"))
    if ex:
        text += "\n\n" + "\n\n".join(ex)
    rows = []
    cw = Q1("SELECT 1 FROM deliveries WHERE user_id=? LIMIT 1", uid)
    if gs("require_plan") == "1" and not is_premium(u):
        rows.append([B(L(lang, "💳 প্ল্যান কিনুন", "💳 Buy plan"), callback_data="tp")])
    if cw:
        rows.append([B(L(lang, "▶️ Continue Watching", "▶️ Continue Watching"), callback_data="cw")])
    order, off = layout()
    flat = [B(L(lang, HOME_BTNS[k][0], HOME_BTNS[k][1]), callback_data=HOME_BTNS[k][2]) for k in order if k not in off]
    rows += [flat[i:i + 2] for i in range(0, len(flat), 2)]
    chs = {r["channel"]: r["n"] for r in Q("SELECT channel, COUNT(*) AS n FROM serials WHERE draft=0 AND channel<>'' GROUP BY channel")}
    chips = [B(f"{p} ({chs[p]})", callback_data=f"lf:chh:{i}") for i, p in enumerate(PLATFORMS) if p in chs][:6]
    rows += [chips[i:i + 3] for i in range(0, len(chips), 3)]
    for b in cbtns:
        try:
            rows.append([B(b[0], url=b[1])])
        except Exception:
            pass
    if is_admin(uid) and not override:
        unread = "🔴 " if (base and base["seen_ver"] != VERSION) else ""
        rows.append([B("🛠 এডমিন প্যানেল", callback_data="ad:home"), B(f"{unread}🆕 আপডেট বোর্ড", callback_data="ad:upd")])
        rows.append([B("👁 ইউজার হিসেবে দেখুন", callback_data="ad:vu")])
    return text, M(rows), banner, new


async def send_home(update, ctx):
    u = update.effective_user
    text, kb, banner, new = home_view(u.id, u.first_name or u.full_name, lg(u.id))
    q = update.callback_query
    if new:
        X("UPDATE users SET onboarded=2 WHERE user_id=?", u.id)
    if banner:
        try:
            if q and q.message:
                try:
                    await q.message.delete()
                except TelegramError:
                    pass
            await ctx.bot.send_photo(u.id, banner, caption=text[:1024], reply_markup=kb)
            return
        except TelegramError as e:
            log.warning("banner failed: %s", e)
    await show(update, ctx, text, kb)


@route("h")
async def cb_home(update, ctx, q, p):
    ctx.user_data.pop("aw", None)
    await send_home(update, ctx)
    await ans(q)


@command("start")
async def cmd_start(update, ctx):
    u = update.effective_user
    row = user(u.id)
    payload = ctx.args[0] if ctx.args else ""
    if row["onboarded"] == 0 and not is_admin(u.id):
        return await reply(update, "🌐 <b>ভাষা বাছাই করুন / Choose language</b>",
                           M([[B("🇧🇩 বাংলা", callback_data="lang:bn"), B("🇬🇧 English", callback_data="lang:en")]]))
    if gs("consent_on") == "1" and row["consent_ver"] < gi("terms_ver") and not is_admin(u.id):
        t_, kb_ = consent_view(u.id)
        return await reply(update, t_, kb_)
    if payload.startswith("s_") and payload[2:].isdigit():
        return await send_serial(update, ctx, int(payload[2:]))
    if payload.startswith("ep_") and payload[3:].isdigit():
        eid = int(payload[3:])
        if can_get(u.id, eid):
            if await send_one_ep(ctx, u.id, eid):
                return
        e = Q1("SELECT serial_id FROM eps WHERE id=?", eid)
        if e:
            return await send_serial(update, ctx, e["serial_id"])
    await send_home(update, ctx)


@route("lang")
async def cb_lang(update, ctx, q, p):
    uid = q.from_user.id
    X("UPDATE users SET lang=? WHERE user_id=?", p[1], uid)
    _LANG[uid] = p[1]
    row = user(uid)
    if row["onboarded"] == 0:
        X("UPDATE users SET onboarded=1 WHERE user_id=?", uid)
        if gs("consent_on") == "1" and row["consent_ver"] < gi("terms_ver"):
            t_, kb_ = consent_view(uid)
            await show(update, ctx, t_, kb_)
            return await ans(q)
    await ans(q, "✅")
    await send_home(update, ctx)


# ================================================================ সিরিয়াল তালিকা (ফিল্টার, সর্ট, বাল্ক ফলো)
SORTS = [("az", "A–Z", "A–Z"), ("pop", "🔥 জনপ্রিয়", "🔥 Popular"), ("new", "🆕 নতুন যোগ", "🆕 Newly added"), ("recent", "📡 সদ্য এপিসোড", "📡 Latest episode")]


def SL(ctx):
    d = ctx.user_data.get("sl")
    if d is None:
        d = ctx.user_data["sl"] = dict(ch=None, st=None, sort="az", unf=0, letter=None, ids=None, q=None, multi=0, sel=set(), adm=None, promo=0)
    return d


def serial_rows(uid, f, admin, page, size):
    where, args = [], []
    if not admin:
        where.append("s.draft=0")
    if f["ch"] is not None:
        where.append("s.channel=?")
        args.append(PLATFORMS[f["ch"]])
    if f["st"]:
        where.append("s.status=?")
        args.append(f["st"])
    if f["unf"]:
        where.append("fl.user_id IS NULL")
    if f["letter"]:
        where.append("substr(s.name_key,1,1)=?")
        args.append(f["letter"])
    if f["promo"]:
        where.append("s.promo_at>?")
        args.append(now() - 7 * 86400)
    if f["ids"] is not None:
        if not f["ids"]:
            where.append("0")
        else:
            where.append(f"s.id IN ({','.join('?' * len(f['ids']))})")
            args += list(f["ids"])
    adm = f["adm"] if admin else None
    if adm == "inc":
        where.append("(s.poster='' OR s.synopsis='' OR s.channel='')")
    elif adm == "nof":
        where.append("NOT EXISTS (SELECT 1 FROM follows x WHERE x.serial_id=s.id)")
    elif adm == "draft":
        where.append("s.draft=1")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM serials s LEFT JOIN follows fl ON fl.serial_id=s.id AND fl.user_id=? LEFT JOIN favs fv ON fv.serial_id=s.id AND fv.user_id=?{w}"
    params = [uid, uid] + args
    total = V(f"SELECT COUNT(*) {base}", *params)
    page, pages, off = paginate(total, page, size)
    cols = ("s.id, s.name, s.channel, s.status, s.last_ep_no, s.last_ep_at, s.pinned, s.draft, s.promo_at, (fl.user_id IS NOT NULL) AS followed, "
            "(fv.user_id IS NOT NULL) AS fav, (SELECT COUNT(*) FROM follows x WHERE x.serial_id=s.id) AS fc")
    if f["ids"] and f["sort"] == "rel":
        rows = Q(f"SELECT {cols} {base}", *params)
        order = {sid: i for i, sid in enumerate(f["ids"])}
        rows = sorted(rows, key=lambda r: order.get(r["id"], 9999))[off:off + size]
    else:
        ob = {"az": "s.name_key", "pop": "fc DESC, s.name_key", "new": "s.created_at DESC", "recent": "s.last_ep_at DESC"}.get(f["sort"], "s.name_key")
        rows = Q(f"SELECT {cols} {base} ORDER BY fv.user_id IS NULL, s.pinned DESC, {ob} LIMIT ? OFFSET ?", *params, size, off)
    return rows, total, page, pages


def serial_label(r, lang, sel=None):
    tags = ""
    if r["last_ep_at"] and r["last_ep_at"] >= day_start():
        tags += " 🆕"
    if r["promo_at"] and r["promo_at"] > now() - 86400:
        tags += " 🔴NEW"
    pre = ("☑️ " if r["id"] in sel else "⬜ ") if sel is not None else ""
    return (f"{pre}{ST_ICON.get(r['status'], '🟢')}{'📌' if r['pinned'] else ''}{'⭐' if r['fav'] else ''}{'📝' if r['draft'] else ''} "
            f"{r['name'][:24]}{(' · E' + str(r['last_ep_no'])) if r['last_ep_no'] else ''}{tags}")


def list_view(uid, page, ctx):
    f, lang, admin, u = SL(ctx), lg(uid), is_admin(uid), user(uid)
    size = u["page_size"] or 8
    rows, total, page, pages = serial_rows(uid, f, admin, page, size)
    prem = is_premium(u)
    fc = follow_count(uid)
    head = L(lang, "🔎 <b>সার্চ ফলাফল</b>", "🔎 <b>Search results</b>") if f["q"] else L(lang, "📺 <b>সিরিয়াল তালিকা</b>", "📺 <b>Serial list</b>")
    lines = [head, f"🔔 {bar(fc, free_limit()) if not prem else '∞'} {fc}/{'∞' if prem else free_limit()} • {plan_label(u, lang)}"]
    flt = []
    if f["q"]:
        flt.append(f"🔎 “{esc(f['q'])}”")
    if f["ch"] is not None:
        flt.append(f"📡 {PLATFORMS[f['ch']]}")
    if f["st"]:
        flt.append(f"{ST_ICON[f['st']]} {ST_NAME[f['st']][0 if lang == 'bn' else 1]}")
    if f["unf"]:
        flt.append(L(lang, "➕ ফলো করিনি", "➕ Not followed"))
    if f["letter"]:
        flt.append(f"🔤 {esc(f['letter'])}")
    if f["promo"]:
        flt.append("🔴 NEW promo")
    if f["adm"]:
        flt.append(f"🛠 {f['adm']}")
    if flt:
        lines.append(" • ".join(flt))
    sname = next((x[1 if lang == 'bn' else 2] for x in SORTS if x[0] == f["sort"]), L(lang, "🎯 মিল অনুযায়ী", "🎯 Relevance"))
    lines.append(L(lang, f"মোট <b>{total}</b>টি পাওয়া গেছে • ↕️ {sname}", f"<b>{total}</b> found • ↕️ {sname}"))
    lines.append(L(lang, "\n👇 নামে ট্যাপ করলে সিরিয়াল পেজ, পাশের বাটনে ফলো/আনফলো" if not f["multi"] else "\n☑️ একাধিক বেছে নিয়ে নিচে ফলো করুন",
                   "\n👇 Tap a name for details, side button to follow/unfollow" if not f["multi"] else "\n☑️ Select several, then follow below"))
    kb = []
    for r in rows:
        if f["multi"]:
            kb.append([B(serial_label(r, lang, f["sel"]), callback_data=f"lm:{r['id']}:{page}")])
        else:
            kb.append([B(serial_label(r, lang), callback_data=f"sp:{r['id']}"), B("✅" if r["followed"] else "➕", callback_data=f"tg:{r['id']}:{page}")])
    nv = nav_row("ls", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, "🔽 ফিল্টার", "🔽 Filter"), callback_data="lp"), B(L(lang, "↕️ সর্ট", "↕️ Sort"), callback_data="lo"),
               B(L(lang, "☑️ বাছুন" if not f["multi"] else "✖️ বাছাই বন্ধ", "☑️ Select" if not f["multi"] else "✖️ Stop"), callback_data="lm0")])
    kb.append([B("🔍", callback_data="sq"), B("🔤 A–Z", callback_data="la"), B("🎲", callback_data="lr"), B("🔥", callback_data="tr"), B("💡", callback_data="sg")])
    if f["multi"] and f["sel"]:
        kb.append([B(L(lang, f"✅ ফলো করুন ({len(f['sel'])})", f"✅ Follow ({len(f['sel'])})"), callback_data="lb:f"), B("🧹", callback_data="lb:c")])
        if admin:
            kb.append([B("📡 চ্যানেল", callback_data="lb:ac"), B("📊 স্ট্যাটাস", callback_data="lb:as"), B("📝 ড্রাফট", callback_data="lb:ad"), B("📤 প্রকাশ", callback_data="lb:ap")])
    if f["ch"] is not None:
        kb.append([B(L(lang, f"📡 {PLATFORMS[f['ch']]}-র সব ফলো করুন", f"📡 Follow all of {PLATFORMS[f['ch']]}"), callback_data="lb:ch")])
    if not prem and fc >= free_limit():
        kb.append([B(L(lang, "💎 আপগ্রেড করুন — লিমিট পূর্ণ", "💎 Upgrade — limit reached"), callback_data="pl")])
    if admin:
        kb.append([B("➕ সিরিয়াল যোগ", callback_data="ad:sa"), B("🧰 এডমিন ফিল্টার", callback_data="lp")])
    if f["q"]:
        kb.append([B(L(lang, "📤 শেয়ার", "📤 Share"), switch_inline_query=f["q"][:40])])
    kb.append(home_row(lang))
    return "\n".join(lines), M(kb)


def filter_view(uid, ctx):
    f, lang, admin = SL(ctx), lg(uid), is_admin(uid)
    cnt = {r["channel"]: r["n"] for r in Q("SELECT channel, COUNT(*) AS n FROM serials WHERE draft=0 GROUP BY channel")}
    rows = []
    chips = [B(("✅ " if f["ch"] == i else "") + f"{p} ({cnt.get(p, 0)})", callback_data=f"lf:ch:{i}") for i, p in enumerate(PLATFORMS)]
    rows += [chips[i:i + 2] for i in range(0, len(chips), 2)]
    rows.append([B(("✅ " if f["st"] == s else "") + f"{ST_ICON[s]} {ST_NAME[s][0 if lang == 'bn' else 1]}", callback_data=f"lf:st:{s}") for s in ("on", "end", "brk")])
    rows.append([B(("✅ " if f["unf"] else "") + L(lang, "➕ শুধু ফলো করিনি যেগুলো", "➕ Only not followed"), callback_data="lf:unf:1"),
                 B(("✅ " if f["promo"] else "") + "🔴 NEW promo", callback_data="lf:promo:1")])
    rows.append([B(f"📏 {n}", callback_data=f"lf:ps:{n}") for n in (8, 15, 25)])
    if admin:
        rows.append([B(("✅ " if f["adm"] == k else "") + n, callback_data=f"lf:adm:{k}") for k, n in (("inc", "অসম্পূর্ণ"), ("nof", "ফলোয়ারহীন"), ("draft", "ড্রাফট"))])
    rows.append([B(L(lang, "📺 ফলাফল দেখুন", "📺 Show results"), callback_data="ls:0"), B(L(lang, "♻️ রিসেট", "♻️ Reset"), callback_data="lf:rst:1")])
    return L(lang, "🔽 <b>ফিল্টার ও প্ল্যাটফর্ম</b>\nপ্ল্যাটফর্ম/চ্যানেলের পাশে সিরিয়াল সংখ্যা দেখানো হলো।", "🔽 <b>Filters & platforms</b>\nSerial counts shown next to each platform."), M(rows)


PAGERS = {}
PAGERS["ls"] = lambda uid, page, ctx: list_view(uid, page, ctx)


@route("ls", "fd", "sv", "hl", "nfs", "tkl", "pr", "ps_", "hu")
async def cb_pager(update, ctx, q, p):
    fn = PAGERS.get(p[0])
    if not fn:
        return await ans(q)
    text, kb = fn(q.from_user.id, int(p[1]) if len(p) > 1 else 0, ctx)
    await show(update, ctx, text, kb)
    await ans(q)


@route("pj")
async def cb_pj(update, ctx, q, p):
    set_await(ctx, "jump", (p[1], int(p[2])))
    await ans(q, L(lg(q.from_user.id), f"📄 পেজ নম্বর লিখে পাঠান (১–{p[2]})", f"📄 Send a page number (1–{p[2]})"), True)


@awaiting("jump")
async def aw_jump(update, ctx, payload):
    prefix, pages = payload
    t = (update.effective_message.text or "").translate(_BNDIG).strip()
    if not t.isdigit():
        return await reply(update, "❌ সংখ্যা লিখুন")
    fn = PAGERS.get(prefix)
    if fn:
        text, kb = fn(update.effective_user.id, min(max(int(t), 1), pages) - 1, ctx)
        await reply(update, text, kb)


@route("lp")
async def cb_lp(update, ctx, q, p):
    text, kb = filter_view(q.from_user.id, ctx)
    await show(update, ctx, text, kb)
    await ans(q)


@route("lf")
async def cb_lf(update, ctx, q, p):
    uid, f = q.from_user.id, SL(ctx)
    k, v = p[1], ":".join(p[2:])
    if k == "ch":
        f["ch"] = None if f["ch"] == int(v) else int(v)
    elif k == "chh":
        f.update(ch=int(v), st=None, unf=0, letter=None, ids=None, q=None, sort="az", multi=0, sel=set(), adm=None, promo=0)
        text, kb = list_view(uid, 0, ctx)
        await show(update, ctx, text, kb)
        return await ans(q)
    elif k == "st":
        f["st"] = None if f["st"] == v else v
    elif k == "unf":
        f["unf"] = 0 if f["unf"] else 1
    elif k == "promo":
        f["promo"] = 0 if f["promo"] else 1
    elif k == "adm":
        f["adm"] = None if f["adm"] == v else v
    elif k == "lt":
        f["letter"] = v or None
        text, kb = list_view(uid, 0, ctx)
        await show(update, ctx, text, kb)
        return await ans(q)
    elif k == "ps":
        X("UPDATE users SET page_size=? WHERE user_id=?", int(v), uid)
    elif k == "rst":
        f.update(ch=None, st=None, sort="az", unf=0, letter=None, ids=None, q=None, multi=0, sel=set(), adm=None, promo=0)
    text, kb = filter_view(uid, ctx)
    await show(update, ctx, text, kb)
    await ans(q)


@route("lo")
async def cb_lo(update, ctx, q, p):
    f = SL(ctx)
    keys = [x[0] for x in SORTS] + (["rel"] if f["ids"] else [])
    f["sort"] = keys[(keys.index(f["sort"]) + 1) % len(keys)] if f["sort"] in keys else "az"
    text, kb = list_view(q.from_user.id, 0, ctx)
    await show(update, ctx, text, kb)
    await ans(q)


@route("la")
async def cb_la(update, ctx, q, p):
    lang = lg(q.from_user.id)
    letters = [r[0] for r in Q("SELECT DISTINCT substr(name_key,1,1) FROM serials WHERE draft=0 ORDER BY 1") if r[0] and not r[0].isdigit()]
    chips = [B(c.upper(), callback_data=f"lf:lt:{c}") for c in letters[:48]]
    rows = [chips[i:i + 6] for i in range(0, len(chips), 6)]
    rows.append([B(L(lang, "সব", "All"), callback_data="lf:lt:"), B("↩️", callback_data="ls:0")])
    await show(update, ctx, L(lang, "🔤 <b>A–Z সূচি</b>\nঅক্ষর চাপুন:", "🔤 <b>A–Z index</b>\nTap a letter:"), M(rows))
    await ans(q)


@route("lm0")
async def cb_lm0(update, ctx, q, p):
    f = SL(ctx)
    f["multi"] = 0 if f["multi"] else 1
    f["sel"] = set()
    text, kb = list_view(q.from_user.id, 0, ctx)
    await show(update, ctx, text, kb)
    await ans(q)


@route("lm")
async def cb_lm(update, ctx, q, p):
    f, sid = SL(ctx), int(p[1])
    f["sel"] ^= {sid}
    text, kb = list_view(q.from_user.id, int(p[2]), ctx)
    await show(update, ctx, text, kb)
    await ans(q)


async def send_limit(ctx, uid):
    lang = lg(uid)
    body, _, _ = get_text("limit", lang)
    await tsend(ctx.bot, uid, fill(body, user(uid), lang=lang), M([[B(L(lang, "💎 আপগ্রেড", "💎 Upgrade"), callback_data="pl")]]))


def toggle_follow(uid, sid):
    if Q1("SELECT 1 FROM follows WHERE user_id=? AND serial_id=?", uid, sid):
        X("DELETE FROM follows WHERE user_id=? AND serial_id=?", uid, sid)
        return "off"
    if not can_follow(uid):
        return "limit"
    X("INSERT OR IGNORE INTO follows (user_id, serial_id, created_at) VALUES (?,?,?)", uid, sid, now())
    return "on"


@route("tg")
async def cb_tg(update, ctx, q, p):
    uid, sid, page = q.from_user.id, int(p[1]), int(p[2])
    lang = lg(uid)
    s = Q1("SELECT name FROM serials WHERE id=?", sid)
    if not s:
        return await ans(q, "❌", True)
    r = toggle_follow(uid, sid)
    if r == "limit":
        await ans(q)
        return await send_limit(ctx, uid)
    text, kb = list_view(uid, page, ctx)
    await show(update, ctx, text, kb)
    await ans(q, (L(lang, f"✅ ফলো: {s['name']}\nনতুন এপিসোড পাবেন।", f"✅ Following: {s['name']}") if r == "on" else L(lang, f"🔕 আনফলো: {s['name']}", f"🔕 Unfollowed: {s['name']}")))


@route("ft")
async def cb_ft(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    r = toggle_follow(uid, sid)
    if r == "limit":
        await ans(q)
        return await send_limit(ctx, uid)
    await send_serial(update, ctx, sid)
    await ans(q, "✅" if r == "on" else "🔕")


def bulk_follow(uid, sids):
    added = skipped = 0
    for sid in sids:
        if Q1("SELECT 1 FROM follows WHERE user_id=? AND serial_id=?", uid, sid):
            continue
        if can_follow(uid):
            X("INSERT OR IGNORE INTO follows (user_id, serial_id, created_at) VALUES (?,?,?)", uid, sid, now())
            added += 1
        else:
            skipped += 1
    return added, skipped


@route("lb")
async def cb_lb(update, ctx, q, p):
    uid, f, lang, act = q.from_user.id, SL(ctx), lg(q.from_user.id), p[1]
    if act in ("f", "ch"):
        if act == "f":
            sids = list(f["sel"])
        else:
            sids = [r["id"] for r in Q("SELECT id FROM serials WHERE draft=0 AND channel=?", PLATFORMS[f["ch"]])]
        added, skipped = bulk_follow(uid, sids)
        f["sel"], f["multi"] = set(), 0
        if skipped:
            await send_limit(ctx, uid)
        text, kb = list_view(uid, 0, ctx)
        await show(update, ctx, text, kb)
        return await ans(q, L(lang, f"✅ {added}টি ফলো হয়েছে" + (f", {skipped}টি লিমিটে বাদ" if skipped else ""), f"✅ {added} followed" + (f", {skipped} skipped (limit)" if skipped else "")), bool(skipped))
    if act == "c":
        f["sel"] = set()
        text, kb = list_view(uid, 0, ctx)
        await show(update, ctx, text, kb)
        return await ans(q)
    if not is_admin(uid):
        return await ans(q, "⛔", True)
    sel = list(f["sel"])
    if act == "ac":
        rows = [[B(p_, callback_data=f"lb:ac2:{i}")] for i, p_ in enumerate(PLATFORMS)] + [[B("— খালি —", callback_data="lb:ac2:-1")]]
        return await show(update, ctx, "📡 নির্বাচিত সিরিয়ালগুলোর চ্যানেল কী হবে?", M(rows))
    if act == "ac2":
        ch = PLATFORMS[int(p[2])] if int(p[2]) >= 0 else ""
        X(f"UPDATE serials SET channel=? WHERE id IN ({','.join('?' * len(sel))})", ch, *sel)
    elif act == "as":
        rows = [[B(f"{ST_ICON[s]} {ST_NAME[s][0]}", callback_data=f"lb:as2:{s}")] for s in ("on", "end", "brk")]
        return await show(update, ctx, "📊 নির্বাচিত সিরিয়ালগুলোর স্ট্যাটাস কী হবে?", M(rows))
    elif act == "as2":
        X(f"UPDATE serials SET status=? WHERE id IN ({','.join('?' * len(sel))})", p[2], *sel)
    elif act in ("ad", "ap"):
        X(f"UPDATE serials SET draft=? WHERE id IN ({','.join('?' * len(sel))})", 1 if act == "ad" else 0, *sel)
    if sel:
        alog(uid, "bulk_" + act, len(sel))
        sdirty()
    f["sel"], f["multi"] = set(), 0
    text, kb = list_view(uid, 0, ctx)
    await show(update, ctx, text, kb)
    await ans(q, "✅")


# ================================================================ সিরিয়াল পেজ
def serial_view(uid, sid):
    s = Q1("SELECT * FROM serials WHERE id=?", sid)
    admin, lang = is_admin(uid), lg(uid)
    if not s or (s["draft"] and not admin):
        return None
    ix = 0 if lang == "bn" else 1
    n_eps = V("SELECT COUNT(*) FROM eps WHERE serial_id=? AND status='published'", sid)
    last = Q1("SELECT * FROM eps WHERE serial_id=? AND status='published' ORDER BY id DESC LIMIT 1", sid)
    fol = Q1("SELECT * FROM follows WHERE user_id=? AND serial_id=?", uid, sid)
    fav = bool(Q1("SELECT 1 FROM favs WHERE user_id=? AND serial_id=?", uid, sid))
    tags = (" 🆕" if s["last_ep_at"] >= day_start() and s["last_ep_at"] else "") + (" 🔴NEW" if s["promo_at"] > now() - 86400 else "") + (" 📌" if s["pinned"] else "") + (" 📝 ড্রাফট" if s["draft"] else "")
    lines = [f"📺 <b>{esc(s['name'])}</b>{tags}"]
    meta = ([f"📡 {esc(s['channel'])}"] if s["channel"] else []) + [f"{ST_ICON.get(s['status'], '')} {ST_NAME.get(s['status'], ('', ''))[ix]}"]
    lines.append(" • ".join(meta))
    if s["genre"]:
        lines.append(f"🏷 {esc(s['genre'])}")
    if s["actors"]:
        lines.append(f"🎭 {esc(s['actors'])}")
    if s["air_time"]:
        lines.append(f"🕒 {esc(s['air_time'])}")
    lines.append(L(lang, f"🎞 মোট এপিসোড: <b>{n_eps}</b>", f"🎞 Total episodes: <b>{n_eps}</b>") +
                 (L(lang, f" • শেষ: {esc(ep_label(last))} ({fmt_day(last['published_at'])})", f" • Last: {esc(ep_label(last))} ({fmt_day(last['published_at'])})") if last else ""))
    if s["next_ep_at"] and s["next_ep_at"] > now():
        d = s["next_ep_at"] - now()
        cd = f"{d // 3600} ঘণ্টা {(d % 3600) // 60} মিনিট" if d < 86400 else f"{d // 86400} দিন {(d % 86400) // 3600} ঘণ্টা"
        lines.append(L(lang, f"📅 পরের এপিসোড: <b>{fmt_ts(s['next_ep_at'])}</b>\n⏳ আর {cd} বাকি", f"📅 Next episode: <b>{fmt_ts(s['next_ep_at'])}</b>\n⏳ {cd} left"))
    if s["synopsis"]:
        lines.append("\n📝 " + esc(s["synopsis"][:500]))
    if admin:
        fc = V("SELECT COUNT(*) FROM follows WHERE serial_id=?", sid)
        sc = V("SELECT COUNT(*) FROM saves sv JOIN eps e ON e.id=sv.episode_id WHERE e.serial_id=?", sid)
        lines.append(f"\n👑 <b>এডমিন:</b> 👥 {fc} ফলোয়ার • ⭐ {sc} জন সেভ করেছে • 🖼 পোস্টার {'✅' if s['poster'] else '❌'} • 🎞 {s['qmask']}")
    kb = [[B(L(lang, "✅ ফলো করছেন (আনফলো)" if fol else "➕ ফলো করুন", "✅ Following (unfollow)" if fol else "➕ Follow"), callback_data=f"ft:{sid}"),
           B("⭐" if fav else "☆", callback_data=f"fv:{sid}")]]
    r2 = []
    if last:
        r2.append(B(L(lang, "▶️ শেষ এপিসোড", "▶️ Latest ep"), callback_data=f"le:{sid}"))
    if V("SELECT COUNT(*) FROM promos WHERE serial_id=? AND status='live'", sid):
        r2.append(B(L(lang, "🎬 প্রোমো", "🎬 Promo"), callback_data=f"pv:{sid}"))
    if r2:
        kb.append(r2)
    if fol:
        kb.append([B(L(lang, "🔔 এই সিরিয়ালের সেটিংস", "🔔 Serial settings"), callback_data=f"ns:{sid}")])
    else:
        kb.append([B(L(lang, "📲 এপিসোড আসলে জানাও", "📲 Notify me on new episode"), callback_data=f"ft:{sid}")])
    if admin:
        kb.append([B("✏️ এডিট", callback_data=f"ae:{sid}"), B("📌 পিন", callback_data=f"ap_:{sid}"), B("📝↔📤 ড্রাফট", callback_data=f"adr:{sid}")])
        kb.append([B("📢 ফলোয়ারদের মেসেজ", callback_data=f"abc:{sid}"), B("🔗 মার্জ", callback_data=f"amg:{sid}"), B("🗑 মুছুন", callback_data=f"adel:{sid}")])
        kb.append([B("🎬 প্রোমো যোগ", callback_data=f"apr:{sid}"), B("📅 পরের এপিসোড সময়", callback_data=f"anx:{sid}")])
    kb.append([B(L(lang, "↩️ তালিকা", "↩️ List"), callback_data="ls:0")] + [home_row(lang)[0]])
    return "\n".join(lines), M(kb), s["poster"]


async def send_serial(update, ctx, sid):
    uid = update.effective_user.id
    v = serial_view(uid, sid)
    q = update.callback_query
    if not v:
        if q:
            return await ans(q, "❌ সিরিয়াল পাওয়া যায়নি", True)
        return await reply(update, "❌ সিরিয়াল পাওয়া যায়নি")
    text, kb, poster = v
    if poster:
        try:
            if q and q.message:
                try:
                    await q.message.delete()
                except TelegramError:
                    pass
            await ctx.bot.send_photo(uid, poster, caption=text[:1024], reply_markup=kb)
            return
        except TelegramError as e:
            log.warning("poster failed: %s", e)
    await show(update, ctx, text, kb)


@route("sp")
async def cb_sp(update, ctx, q, p):
    await send_serial(update, ctx, int(p[1]))
    await ans(q)


@route("fv")
async def cb_fv(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    if Q1("SELECT 1 FROM favs WHERE user_id=? AND serial_id=?", uid, sid):
        X("DELETE FROM favs WHERE user_id=? AND serial_id=?", uid, sid)
        t = "☆"
    else:
        X("INSERT OR IGNORE INTO favs (user_id, serial_id) VALUES (?,?)", uid, sid)
        t = L(lg(uid), "⭐ পছন্দের তালিকায় যোগ হয়েছে", "⭐ Added to favourites")
    if len(p) > 2:
        text, kb = saved_view(uid, 0, ctx)
        await show(update, ctx, text, kb)
    else:
        await send_serial(update, ctx, sid)
    await ans(q, t)


@route("le")
async def cb_le(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    e = Q1("SELECT id FROM eps WHERE serial_id=? AND status='published' ORDER BY id DESC LIMIT 1", sid)
    lang = lg(uid)
    if not e:
        return await ans(q, L(lang, "এখনো কোনো এপিসোড নেই", "No episodes yet"), True)
    if not can_get(uid, e["id"]):
        return await ans(q, L(lang, "আগে সিরিয়ালটি ফলো করুন (বা 💎 Premium নিন)", "Follow first (or get 💎 Premium)"), True)
    ok = await send_one_ep(ctx, uid, e["id"])
    await ans(q, "✅" if ok else "⚠️", not ok)


# ---- ট্রেন্ডিং, নতুন, র‍্যান্ডম, সাজেশন
def simple_list(uid, title, rows, back="tr"):
    lang = lg(uid)
    kb = [[B(f"{'✅' if r['followed'] else '➕'} {r['name'][:30]}" + (f" · 👥{r['fc']}" if 'fc' in r.keys() else ""), callback_data=f"sp:{r['id']}")] for r in rows]
    kb.append(home_row(lang))
    return title if rows else title + L(lang, "\n\nকিছু পাওয়া যায়নি।", "\n\nNothing found."), M(kb)


@route("tr")
async def cb_tr(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT s.id, s.name, (SELECT COUNT(*) FROM follows x WHERE x.serial_id=s.id) AS fc, (fl.user_id IS NOT NULL) AS followed FROM serials s LEFT JOIN follows fl ON fl.serial_id=s.id AND fl.user_id=? WHERE s.draft=0 ORDER BY fc DESC LIMIT 10", uid)
    today = Q("SELECT s.id, s.name, COUNT(*) AS fc, 0 AS followed FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.created_at>=? AND s.draft=0 GROUP BY s.id ORDER BY fc DESC LIMIT 5", day_start())
    text, kb = simple_list(uid, L(lang, "🔥 <b>ট্রেন্ডিং — সবচেয়ে বেশি ফলোয়ার</b>", "🔥 <b>Trending — most followers</b>"), rows)
    extra = [[B(f"📆 {r['name'][:28]} (+{r['fc']})", callback_data=f"sp:{r['id']}")] for r in today]
    if extra:
        text += L(lang, "\n\n📆 <b>আজকের জনপ্রিয়</b> নিচে", "\n\n📆 <b>Popular today</b> below")
        kb.inline_keyboard = tuple(kb.inline_keyboard[:-1]) + tuple(tuple(r) for r in extra) + (kb.inline_keyboard[-1],)
    await show(update, ctx, text, kb)
    await ans(q)


@command("trending")
async def cmd_trending(update, ctx):
    class _Q:  # কমান্ড থেকে একই ভিউ
        pass
    uid, lang = update.effective_user.id, lg(update.effective_user.id)
    rows = Q("SELECT s.id, s.name, (SELECT COUNT(*) FROM follows x WHERE x.serial_id=s.id) AS fc, (fl.user_id IS NOT NULL) AS followed FROM serials s LEFT JOIN follows fl ON fl.serial_id=s.id AND fl.user_id=? WHERE s.draft=0 ORDER BY fc DESC LIMIT 10", uid)
    text, kb = simple_list(uid, L(lang, "🔥 <b>ট্রেন্ডিং — সবচেয়ে বেশি ফলোয়ার</b>", "🔥 <b>Trending</b>"), rows)
    await reply(update, text, kb)


@route("nw")
async def cb_nw(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT s.id, s.name, (fl.user_id IS NOT NULL) AS followed FROM serials s LEFT JOIN follows fl ON fl.serial_id=s.id AND fl.user_id=? WHERE s.draft=0 ORDER BY s.created_at DESC LIMIT 10", uid)
    text, kb = simple_list(uid, L(lang, "🆕 <b>নতুন যোগ হওয়া সিরিয়াল</b>", "🆕 <b>Newly added serials</b>"), rows)
    await show(update, ctx, text, kb)
    await ans(q)


@route("lr")
async def cb_lr(update, ctx, q, p):
    uid = q.from_user.id
    rows = Q("SELECT s.id FROM serials s WHERE s.draft=0 AND s.id NOT IN (SELECT serial_id FROM follows WHERE user_id=?)", uid) or Q("SELECT id FROM serials WHERE draft=0")
    if not rows:
        return await ans(q, "কোনো সিরিয়াল নেই", True)
    await send_serial(update, ctx, random.choice(rows)["id"])
    await ans(q, "🎲")


@route("sg")
async def cb_sg(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("""SELECT s.id, s.name, (SELECT COUNT(*) FROM follows x WHERE x.serial_id=s.id) AS fc, 0 AS followed FROM serials s
                WHERE s.draft=0 AND s.id NOT IN (SELECT serial_id FROM follows WHERE user_id=?)
                ORDER BY (s.channel IN (SELECT s2.channel FROM follows f JOIN serials s2 ON s2.id=f.serial_id WHERE f.user_id=? AND s2.channel<>'')) DESC, fc DESC LIMIT 8""", uid, uid)
    text, kb = simple_list(uid, L(lang, "💡 <b>আপনার জন্য সাজেশন</b>\nআপনার ফলো করা সিরিয়ালের মতো আরও:", "💡 <b>Suggestions for you</b>"), rows)
    await show(update, ctx, text, kb)
    await ans(q)


# ================================================================ সার্চ (কমান্ড ছাড়াও, বানান ভুল সহ্য, রিকোয়েস্ট, ইনলাইন)
DAY_WORDS = {"আজ": 0, "আজকের": 0, "today": 0, "গতকাল": 1, "গতকালের": 1, "yesterday": 1}


def parse_query(text):
    t = (text or "").translate(_BNDIG).strip()
    day, keep = None, []
    for w in t.split():
        if w.lower() in DAY_WORDS:
            day = DAY_WORDS[w.lower()]
        else:
            keep.append(w)
    t2 = " ".join(keep)
    ep_no = None
    m = re.search(r"(?:^|\s)(?:episode|এপিসোড|পর্ব|ep|e)?\s*(\d{1,5})$", t2, re.I)
    if m and len(t2[: m.start()].strip()) >= 2:
        ep_no, t2 = int(m.group(1)), t2[: m.start()].strip()
    return t2, ep_no, day


def remember_query(uid, qt):
    try:
        r = json.loads(V("SELECT recent_q FROM users WHERE user_id=?", uid, d="[]") or "[]")
    except ValueError:
        r = []
    r = [x for x in r if x != qt]
    r.insert(0, qt)
    X("UPDATE users SET recent_q=? WHERE user_id=?", json.dumps(r[:5], ensure_ascii=False), uid)


@route("sq")
async def cb_sq(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    set_await(ctx, "search")
    try:
        rq = json.loads(V("SELECT recent_q FROM users WHERE user_id=?", uid, d="[]") or "[]")
    except ValueError:
        rq = []
    rows = [[B(f"🕘 {x[:30]}", callback_data=f"rs:{i}")] for i, x in enumerate(rq)]
    rows.append(home_row(lang))
    await show(update, ctx, L(lang, "🔍 <b>সার্চ</b>\nসিরিয়ালের নাম লিখুন (বাংলা বা ইংরেজি, বানান ভুল হলেও চলবে)।\nউদাহরণ: <code>কোন গোপনে 250</code>, <code>গতকাল</code>", "🔍 <b>Search</b>\nType a serial name (Bengali or English, typos are fine).\nExample: <code>kon gopone 250</code>, <code>yesterday</code>"), M(rows))
    await ans(q)


@route("rs")
async def cb_rs(update, ctx, q, p):
    try:
        rq = json.loads(V("SELECT recent_q FROM users WHERE user_id=?", q.from_user.id, d="[]") or "[]")
        text = rq[int(p[1])]
    except (ValueError, IndexError):
        return await ans(q)
    ctx.user_data.pop("aw", None)
    await ans(q)
    await do_search(update, ctx, text)


@awaiting("search")
async def aw_search(update, ctx, payload):
    await do_search(update, ctx, update.effective_message.text or "")


@command("search")
async def cmd_search(update, ctx):
    t = " ".join(ctx.args).strip()
    if not t:
        set_await(ctx, "search")
        return await reply(update, L(lg(update.effective_user.id), "🔍 সিরিয়ালের নাম লিখুন:", "🔍 Type a serial name:"))
    await do_search(update, ctx, t)


async def do_search(update, ctx, text):
    uid = update.effective_user.id
    lang, admin = lg(uid), is_admin(uid)
    text = (text or "").strip()[:80]
    if not text:
        return
    name, ep_no, day = parse_query(text)
    full = search_serials(text, include_draft=admin)
    if full and full[0][0] >= 92:
        name, ep_no, day, res = text, None, None, full
    else:
        res = search_serials(name, include_draft=admin) if name else []
    X("INSERT INTO searches (user_id, q, found, at) VALUES (?,?,?,?)", uid, text, len(res), now())
    remember_query(uid, text[:60])
    ctx.user_data["lastq"] = text
    msg = update.effective_message
    if ep_no is not None and res:
        sid = res[0][1]
        ep = Q1("SELECT id FROM eps WHERE serial_id=? AND ep_no=? AND status='published' ORDER BY id DESC LIMIT 1", sid, ep_no)
        s = Q1("SELECT name FROM serials WHERE id=?", sid)
        if not ep:
            return await msg.reply_text(L(lang, f"❌ {esc(s['name'])} — E{ep_no} পাওয়া যায়নি।", f"❌ {esc(s['name'])} — E{ep_no} not found."))
        if can_get(uid, ep["id"]):
            if await send_one_ep(ctx, uid, ep["id"]):
                return
        return await msg.reply_text(L(lang, f"🔒 <b>{esc(s['name'])}</b> E{ep_no} পেতে আগে সিরিয়ালটি ফলো করুন।", f"🔒 Follow <b>{esc(s['name'])}</b> to get E{ep_no}."),
                                    reply_markup=M([[B(L(lang, "➕ ফলো করুন", "➕ Follow"), callback_data=f"ft:{sid}")]]))
    if day is not None:
        lo = day_start() - day * 86400
        ids = [s for _, s in res] or [r["serial_id"] for r in Q("SELECT serial_id FROM follows WHERE user_id=?", uid)]
        eps = Q(f"SELECT e.id, e.ep_no, e.title, s.name FROM eps e JOIN serials s ON s.id=e.serial_id WHERE e.status='published' AND e.published_at>=? AND e.published_at<? AND e.serial_id IN ({','.join('?' * len(ids)) or 'NULL'}) ORDER BY e.published_at DESC LIMIT 12", lo, lo + 86400, *ids) if ids else []
        eps = [e for e in eps if can_get(uid, e["id"])]
        kb = [[B(f"▶️ {e['name'][:24]} {ep_label(e)}", callback_data=f"eg:{e['id']}")] for e in eps] + [home_row(lang)]
        return await msg.reply_text(L(lang, f"📅 <b>{'গতকালের' if day else 'আজকের'} এপিসোড</b> — {len(eps)}টি", f"📅 Episodes of {'yesterday' if day else 'today'} — {len(eps)}"), reply_markup=M(kb))
    if not res:
        sug = suggest_serials(name or text, include_draft=admin)
        kb = [[B(f"💡 {Q1('SELECT name FROM serials WHERE id=?', sid)['name'][:30]}", callback_data=f"sp:{sid}")] for _, sid in sug]
        kb.append([B(L(lang, "📩 সিরিয়াল রিকোয়েস্ট করুন", "📩 Request this serial"), callback_data="rq"), B(L(lang, "🔍 আবার", "🔍 Again"), callback_data="sq")])
        kb.append(home_row(lang))
        return await msg.reply_text(L(lang, f"❌ “{esc(text)}” পাওয়া যায়নি।" + ("\n💡 <b>আপনি কি এটা বোঝাতে চেয়েছেন?</b>" if sug else ""), f"❌ “{esc(text)}” not found." + ("\n💡 <b>Did you mean?</b>" if sug else "")), reply_markup=M(kb))
    f = SL(ctx)
    f.update(ids=[sid for _, sid in res], sort="rel", q=text, ch=None, st=None, unf=0, letter=None, multi=0, sel=set(), adm=None, promo=0)
    t, kb = list_view(uid, 0, ctx)
    await msg.reply_text(t, reply_markup=kb)


@route("rq")
async def cb_rq(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    text = ctx.user_data.get("lastq")
    if not text:
        return await ans(q, "আগে সার্চ করুন", True)
    key = norm(text)
    if Q1("SELECT 1 FROM requests WHERE user_id=? AND qkey=?", uid, key):
        return await ans(q, L(lang, "✅ আপনি আগেই রিকোয়েস্ট করেছেন", "✅ Already requested"), True)
    X("INSERT INTO requests (user_id, qkey, q, at) VALUES (?,?,?,?)", uid, key, text[:80], now())
    n = V("SELECT COUNT(*) FROM requests WHERE qkey=?", key)
    if n in (1, 5, 10, 25, 50, 100):
        await alert_admins(Ctx(ctx.application), f"📩 <b>সিরিয়াল রিকোয়েস্ট</b>: “{esc(text)}”\n🗳 এখন পর্যন্ত {n} জন চেয়েছে। /requests", M([[B("➕ সিরিয়াল যোগ করুন", callback_data=f"ad:rqa:{key[:40]}")]]))
    await ans(q, L(lang, "📩 রিকোয়েস্ট এডমিনের কাছে পাঠানো হয়েছে!", "📩 Request sent to admin!"), True)


async def on_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    t = (iq.query or "").strip()
    if t:
        ids = [sid for _, sid in search_serials(t)[:20]]
    else:
        ids = [r["id"] for r in Q("SELECT s.id FROM serials s WHERE s.draft=0 ORDER BY (SELECT COUNT(*) FROM follows x WHERE x.serial_id=s.id) DESC LIMIT 10")]
    bot_un = ctx.bot.username
    out = []
    for sid in ids:
        s = Q1("SELECT name, channel, status FROM serials WHERE id=?", sid)
        link = f"https://t.me/{bot_un}?start=s_{sid}"
        out.append(InlineQueryResultArticle(
            id=str(sid), title=s["name"], description=f"{s['channel'] or ''} {ST_ICON.get(s['status'], '')}".strip(),
            input_message_content=InputTextMessageContent(f"📺 <b>{esc(s['name'])}</b>\n🔗 {link}", parse_mode=ParseMode.HTML),
            reply_markup=M([[B("▶️ বটে খুলুন / Open in bot", url=link)]])))
    try:
        await iq.answer(out, cache_time=30, is_personal=False)
    except TelegramError as e:
        log.warning("inline failed: %s", e)


# ================================================================ /myfeeds
def FD(ctx):
    d = ctx.user_data.get("fd")
    if d is None:
        d = ctx.user_data["fd"] = dict(ch=None, sort="recent", q=None)
    return d


def mute_icon(m, pause):
    if pause and pause > now():
        return "⏸"
    return {0: "", 1: "🔕", 2: "⛔"}.get(m, "")


def feed_view(uid, page, ctx):
    f, lang, u = FD(ctx), lg(uid), user(uid)
    size = u["page_size"] or 8
    where, args = ["fl.user_id=?", "s.draft=0"], [uid]
    if f["ch"] is not None:
        where.append("s.channel=?")
        args.append(PLATFORMS[f["ch"]])
    if f["q"]:
        where.append("s.name_key LIKE ?")
        args.append(f"%{norm(f['q'])}%")
    base = "FROM follows fl JOIN serials s ON s.id=fl.serial_id LEFT JOIN favs fv ON fv.serial_id=s.id AND fv.user_id=fl.user_id WHERE " + " AND ".join(where)
    total = V(f"SELECT COUNT(*) {base}", *args)
    page, pages, off = paginate(total, page, size)
    ob = {"recent": "s.last_ep_at DESC", "az": "s.name_key", "follow": "fl.created_at DESC"}.get(f["sort"], "s.last_ep_at DESC")
    rows = Q(f"SELECT s.id, s.name, s.status, s.last_ep_no, s.last_ep_at, fl.mute, fl.pause_until, (fv.user_id IS NOT NULL) AS fav, "
             f"(SELECT COUNT(*) FROM deliveries d JOIN eps e ON e.id=d.episode_id WHERE d.user_id=fl.user_id AND e.serial_id=s.id AND d.watched=0) AS unseen "
             f"{base} ORDER BY fv.user_id IS NULL, {ob} LIMIT ? OFFSET ?", *args, size, off)
    sn = {"recent": L(lang, "সদ্য এপিসোড", "Latest"), "az": "A–Z", "follow": L(lang, "সম্প্রতি ফলো", "Recently followed")}[f["sort"]]
    lines = [L(lang, "📌 <b>আমার ফিড</b>", "📌 <b>My feed</b>"), L(lang, f"মোট <b>{total}</b>টি • ↕️ {sn}", f"<b>{total}</b> serials • ↕️ {sn}")]
    if f["q"]:
        lines.append(f"🔍 “{esc(f['q'])}”")
    if f["ch"] is not None:
        lines.append(f"📡 {PLATFORMS[f['ch']]}")
    if total == 0:
        lines.append(L(lang, "\nআপনি এখনো কিছু ফলো করেননি। /serials দেখুন।", "\nYou follow nothing yet. See /serials."))
    else:
        lines.append(L(lang, "\n👇 নামে ট্যাপ = সিরিয়াল পেজ • ❌ = আনফলো", "\n👇 Tap name = details • ❌ = unfollow"))
    undo = ctx.user_data.get("undo_unf")
    kb = []
    if undo:
        nm = V("SELECT name FROM serials WHERE id=?", undo, d="")
        kb.append([B(L(lang, f"↩️ ফিরিয়ে আনুন: {nm[:20]}", f"↩️ Undo: {nm[:20]}"), callback_data=f"fr:{undo}")])
    for r in rows:
        tag = f" · E{r['last_ep_no']}" if r["last_ep_no"] else ""
        ago = f" • {fmt_ago(r['last_ep_at'], lang)}" if r["last_ep_at"] else ""
        new = f" • {r['unseen']} {L(lang, 'নতুন', 'new')}" if r["unseen"] else ""
        kb.append([B(f"{ST_ICON.get(r['status'], '')}{mute_icon(r['mute'], r['pause_until'])}{'⭐' if r['fav'] else ''} {r['name'][:18]}{tag}{ago}{new}", callback_data=f"sp:{r['id']}"),
                   B("❌", callback_data=f"fu:{r['id']}:{page}")])
    nv = nav_row("fd", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, "↕️ সর্ট", "↕️ Sort"), callback_data="fo"), B("🔍", callback_data="fq"), B(L(lang, "📅 আজকের সময়সূচি", "📅 Today's schedule"), callback_data="fsch")])
    mine = {r["channel"] for r in Q("SELECT s.channel FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? AND s.channel<>''", uid)}
    chips = [B(("✅ " if f["ch"] == i else "") + p_, callback_data=f"fc:{i}") for i, p_ in enumerate(PLATFORMS) if p_ in mine]
    kb += [chips[i:i + 3] for i in range(0, len(chips), 3)]
    kb.append([B(L(lang, "🔕 সব মিউট/বিরতি", "🔕 Mute/pause all"), callback_data="nfp"), B(L(lang, "🔔 নোটিফিকেশন", "🔔 Notifications"), callback_data="nf")])
    if f["q"] or f["ch"] is not None:
        kb.append([B(L(lang, "♻️ ফিল্টার মুছুন", "♻️ Clear filters"), callback_data="fc:x")])
    kb.append(home_row(lang))
    return "\n".join(lines), M(kb)


PAGERS["fd"] = feed_view


@command("myfeeds")
async def cmd_myfeeds(update, ctx):
    ctx.user_data.pop("undo_unf", None)
    t, kb = feed_view(update.effective_user.id, 0, ctx)
    await reply(update, t, kb)


@route("fo")
async def cb_fo(update, ctx, q, p):
    f = FD(ctx)
    ks = ["recent", "az", "follow"]
    f["sort"] = ks[(ks.index(f["sort"]) + 1) % 3]
    t, kb = feed_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("fc")
async def cb_fc(update, ctx, q, p):
    f = FD(ctx)
    if p[1] == "x":
        f["ch"], f["q"] = None, None
    else:
        f["ch"] = None if f["ch"] == int(p[1]) else int(p[1])
    t, kb = feed_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("fq")
async def cb_fq(update, ctx, q, p):
    set_await(ctx, "feed_q")
    await ans(q, L(lg(q.from_user.id), "🔍 ফিডে খোঁজার নাম লিখে পাঠান", "🔍 Send a name to search in your feed"), True)


@awaiting("feed_q")
async def aw_feed_q(update, ctx, payload):
    FD(ctx)["q"] = update.effective_message.text.strip()
    t, kb = feed_view(update.effective_user.id, 0, ctx)
    await reply(update, t, kb)


@route("fu")
async def cb_fu(update, ctx, q, p):
    lang, sid, page = lg(q.from_user.id), int(p[1]), int(p[2])
    nm = V("SELECT name FROM serials WHERE id=?", sid, d="")
    await show(update, ctx, L(lang, f"❌ <b>{esc(nm)}</b> আনফলো করবেন?", f"❌ Unfollow <b>{esc(nm)}</b>?"),
               M([[B(L(lang, "✅ হ্যাঁ", "✅ Yes"), callback_data=f"fu2:{sid}:{page}"), B(L(lang, "❌ না", "❌ No"), callback_data=f"fd:{page}")]]))
    await ans(q)


@route("fu2")
async def cb_fu2(update, ctx, q, p):
    uid, sid, page = q.from_user.id, int(p[1]), int(p[2])
    X("DELETE FROM follows WHERE user_id=? AND serial_id=?", uid, sid)
    ctx.user_data["undo_unf"] = sid
    t, kb = feed_view(uid, page, ctx)
    await show(update, ctx, t, kb)
    await ans(q, L(lg(uid), "🔕 আনফলো হয়েছে", "🔕 Unfollowed"))


@route("fr")
async def cb_fr(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    ctx.user_data.pop("undo_unf", None)
    if not can_follow(uid):
        await ans(q)
        return await send_limit(ctx, uid)
    X("INSERT OR IGNORE INTO follows (user_id, serial_id, created_at) VALUES (?,?,?)", uid, sid, now())
    t, kb = feed_view(uid, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q, "✅")


@route("fsch")
async def cb_fsch(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT s.name, s.air_time, s.next_ep_at, s.last_ep_at FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? AND s.draft=0 AND s.status='on' ORDER BY s.air_time", uid)
    lines = [L(lang, "📅 <b>আজকের সময়সূচি</b>", "📅 <b>Today's schedule</b>")]
    for r in rows:
        nx = f" • ⏳ {fmt_ts(r['next_ep_at'])}" if r["next_ep_at"] and r["next_ep_at"] > now() else ""
        got = " ✅" if r["last_ep_at"] >= day_start() else ""
        if r["air_time"] or nx or got:
            lines.append(f"• {esc(r['name'])} — {esc(r['air_time'] or '—')}{nx}{got}")
    if len(lines) == 1:
        lines.append(L(lang, "সময়সূচির তথ্য নেই।", "No schedule info."))
    await show(update, ctx, "\n".join(lines), M([[B("↩️", callback_data="fd:0")] + home_row(lang)]))
    await ans(q)


# ---- সিরিয়াল ধরে নোটিফিকেশন/মিউট/বিরতি/কোয়ালিটি
@route("ns")
async def cb_ns(update, ctx, q, p):
    uid, sid, lang = q.from_user.id, int(p[1]), lg(q.from_user.id)
    f = Q1("SELECT * FROM follows WHERE user_id=? AND serial_id=?", uid, sid)
    s = Q1("SELECT name FROM serials WHERE id=?", sid)
    if not f or not s:
        return await ans(q, "❌", True)
    m = f["mute"]
    pz = f"⏸ {fmt_ts(f['pause_until'])} পর্যন্ত" if f["pause_until"] > now() else "—"
    text = L(lang, f"🔔 <b>{esc(s['name'])}</b> — সেটিংস\n\nমোড: {['🔔 শব্দ সহ', '🔕 নিঃশব্দ', '⛔ বন্ধ (ফলো থাকবে)'][m]}\nবিরতি: {pz}\nকোয়ালিটি: {f['q'] or 'ডিফল্ট'}{'p' if f['q'] else ''}\nপ্রোমো: {'❌ বন্ধ' if f['promo_off'] else '✅ চালু'}",
             f"🔔 <b>{esc(s['name'])}</b> — settings\n\nMode: {['🔔 Sound', '🔕 Silent', '⛔ Off (still followed)'][m]}\nPause: {pz}\nQuality: {f['q'] or 'default'}\nPromo: {'❌ off' if f['promo_off'] else '✅ on'}")
    kb = [[B(("✅" if m == i else "") + n, callback_data=f"nss:{sid}:{i}") for i, n in enumerate(("🔔", "🔕", "⛔"))],
          [B(f"⏸ {d}{L(lang, 'দিন', 'd')}", callback_data=f"nsp:{sid}:{d}") for d in (3, 7, 14)] + [B(L(lang, "✋ তুলুন", "✋ Resume"), callback_data=f"nsp:{sid}:0")],
          [B(("✅" if f["q"] == n else "") + (f"{n}p" if n else L(lang, "ডিফল্ট", "Default")), callback_data=f"nsq:{sid}:{n}") for n in (0,) + QUALS],
          [B(L(lang, "🎬 প্রোমো: " + ("চালু করুন" if f["promo_off"] else "বন্ধ করুন"), "🎬 Promo: " + ("turn on" if f["promo_off"] else "turn off")), callback_data=f"nspo:{sid}")],
          [B("↩️", callback_data=f"sp:{sid}")] + home_row(lang)]
    await show(update, ctx, text, M(kb))
    await ans(q)


@route("nss", "nsp", "nsq", "nspo")
async def cb_ns2(update, ctx, q, p):
    uid, sid = q.from_user.id, int(p[1])
    if p[0] == "nss":
        X("UPDATE follows SET mute=? WHERE user_id=? AND serial_id=?", int(p[2]), uid, sid)
    elif p[0] == "nsp":
        d = int(p[2])
        X("UPDATE follows SET pause_until=? WHERE user_id=? AND serial_id=?", now() + d * 86400 if d else 0, uid, sid)
    elif p[0] == "nsq":
        n = int(p[2])
        if n > max_quality(user(uid)):
            return await ans(q, _lock_alert(lg(uid)), True)
        X("UPDATE follows SET q=? WHERE user_id=? AND serial_id=?", n, uid, sid)
    else:
        X("UPDATE follows SET promo_off=1-promo_off WHERE user_id=? AND serial_id=?", uid, sid)
    q.data = f"ns:{sid}"
    await cb_ns(update, ctx, q, ["ns", str(sid)])


# ================================================================ /saved
def SV(ctx):
    d = ctx.user_data.get("svs")
    if d is None:
        d = ctx.user_data["svs"] = dict(tab="ep", sort="new", sid=None, q=None, folder=None, multi=0, sel=set())
    return d


DEF_FOLDERS = ["পরে দেখব", "সেরা এপিসোড"]


def user_folders(uid):
    return DEF_FOLDERS + [r["name"] for r in Q("SELECT name FROM folders WHERE user_id=? ORDER BY name", uid) if r["name"] not in DEF_FOLDERS]


def saved_view(uid, page, ctx):
    S, lang, u = SV(ctx), lg(uid), user(uid)
    size = max(5, min(10, u["page_size"] or 8))
    tabs = [B(("▸ " if S["tab"] == k else "") + n, callback_data=f"svt:{k}") for k, n in (("ep", L(lang, "🎬 এপিসোড", "🎬 Episodes")), ("se", L(lang, "📺 সিরিয়াল", "📺 Serials")), ("fo", L(lang, "📁 ফোল্ডার", "📁 Folders")))]
    kb = [tabs]
    if gs("saved_on") != "1":
        return L(lang, "⭐ সেভ ফিচার এখন বন্ধ আছে।", "⭐ Saving is currently off."), M([home_row(lang)])
    if S["tab"] == "se":
        rows = Q("SELECT s.id, s.name FROM favs f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? AND s.draft=0 ORDER BY s.name", uid)
        for r in rows:
            kb.append([B(f"⭐ {r['name'][:30]}", callback_data=f"sp:{r['id']}"), B("✖️", callback_data=f"fv:{r['id']}:r")])
        text = L(lang, f"⭐ <b>পছন্দের সিরিয়াল</b> ({len(rows)})\nসিরিয়াল পেজ থেকে ☆ চাপলে এখানে জমবে।", f"⭐ <b>Favourite serials</b> ({len(rows)})")
        kb.append(home_row(lang))
        return text, M(kb)
    if S["tab"] == "fo":
        fol = user_folders(uid)
        cnt = {r["folder"]: r["n"] for r in Q("SELECT folder, COUNT(*) AS n FROM saves WHERE user_id=? GROUP BY folder", uid)}
        for i, nm in enumerate(fol):
            kb.append([B(f"📁 {nm} ({cnt.get(nm, 0)})", callback_data=f"svf:{i}")])
        kb.append([B(L(lang, "➕ নতুন ফোল্ডার", "➕ New folder"), callback_data="svn"), B(L(lang, "সব", "All"), callback_data="svf:x")])
        kb.append(home_row(lang))
        return L(lang, "📁 <b>আমার ফোল্ডার</b>", "📁 <b>My folders</b>"), M(kb)
    where, args = ["sv.user_id=?"], [uid]
    if S["sid"]:
        where.append("e.serial_id=?")
        args.append(S["sid"])
    if S["folder"] is not None:
        where.append("sv.folder=?")
        args.append(S["folder"])
    if S["q"]:
        where.append("(se.name_key LIKE ? OR lower(sv.note) LIKE ?)")
        args += [f"%{norm(S['q'])}%", f"%{S['q'].lower()}%"]
    base = "FROM saves sv JOIN eps e ON e.id=sv.episode_id JOIN serials se ON se.id=e.serial_id WHERE " + " AND ".join(where)
    total = V(f"SELECT COUNT(*) {base}", *args)
    page, pages, off = paginate(total, page, size)
    ob = {"new": "sv.created_at DESC", "old": "sv.created_at", "serial": "se.name_key, e.ep_no"}[S["sort"]]
    rows = Q(f"SELECT sv.episode_id, sv.note, sv.folder, sv.created_at, e.ep_no, e.title, se.name {base} ORDER BY {ob} LIMIT ? OFFSET ?", *args, size, off)
    flt = ([f"📁 {esc(S['folder'])}"] if S["folder"] is not None else []) + ([f"🔍 {esc(S['q'])}"] if S["q"] else []) + ([f"📺 {esc(V('SELECT name FROM serials WHERE id=?', S['sid'], d=''))}"] if S["sid"] else [])
    sn = {"new": L(lang, "নতুন আগে", "Newest"), "old": L(lang, "পুরনো আগে", "Oldest"), "serial": L(lang, "সিরিয়াল অনুযায়ী", "By serial")}[S["sort"]]
    text = L(lang, f"⭐ <b>সেভ করা এপিসোড</b> ({total})\n↕️ {sn}", f"⭐ <b>Saved episodes</b> ({total})\n↕️ {sn}") + ("\n" + " • ".join(flt) if flt else "")
    text += L(lang, "\n\n👇 ট্যাপ করলে ভিডিও আবার আসবে, ⚙️ এ নোট/ফোল্ডার/শেয়ার", "\n\n👇 Tap to get the video again, ⚙️ for note/folder/share") if total else L(lang, "\n\nএখনো কিছু সেভ করেননি। এপিসোডের নিচের ⭐ বাটন চাপুন।", "\n\nNothing saved yet. Tap ⭐ under an episode.")
    for r in rows:
        lab = f"🎬 {r['name'][:16]} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else (r['title'] or '')[:10]} · {fmt_day(r['created_at'])}{' 📝' if r['note'] else ''}{' 📁' if r['folder'] else ''}"
        if S["multi"]:
            kb.append([B(("☑️ " if r["episode_id"] in S["sel"] else "⬜ ") + lab[2:], callback_data=f"svm:{r['episode_id']}:{page}")])
        else:
            kb.append([B(lab, callback_data=f"eg:{r['episode_id']}"), B("⚙️", callback_data=f"svi:{r['episode_id']}")])
    nv = nav_row("sv", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, "↕️ সর্ট", "↕️ Sort"), callback_data="svs"), B("🔍", callback_data="svq"), B(L(lang, "📺 সিরিয়াল", "📺 Serial"), callback_data="svsr"),
               B(L(lang, "☑️ বাছুন" if not S["multi"] else "✖️ বন্ধ", "☑️ Select" if not S["multi"] else "✖️ Stop"), callback_data="svm0")])
    if S["multi"] and S["sel"]:
        kb.append([B(L(lang, f"🗑 নির্বাচিত মুছুন ({len(S['sel'])})", f"🗑 Delete selected ({len(S['sel'])})"), callback_data="svx")])
    if total:
        kb.append([B(L(lang, "🗑 সব মুছুন", "🗑 Delete all"), callback_data="svall")])
    if S["q"] or S["sid"] or S["folder"] is not None:
        kb.append([B(L(lang, "♻️ ফিল্টার মুছুন", "♻️ Clear filters"), callback_data="svf:x")])
    kb.append(home_row(lang))
    return text, M(kb)


PAGERS["sv"] = saved_view


@command("saved")
async def cmd_saved(update, ctx):
    uid = update.effective_user.id
    if is_admin(uid) and ctx.args and ctx.args[0].isdigit():
        return await admin_saved_of(update, ctx, int(ctx.args[0]))
    t, kb = saved_view(uid, 0, ctx)
    await reply(update, t, kb)


async def admin_saved_of(update, ctx, uid):
    rows = Q("SELECT se.name, e.ep_no, sv.folder, sv.note, sv.created_at FROM saves sv JOIN eps e ON e.id=sv.episode_id JOIN serials se ON se.id=e.serial_id WHERE sv.user_id=? ORDER BY sv.created_at DESC LIMIT 30", uid)
    lines = [f"⭐ <b>{uid}-এর সেভ তালিকা</b> ({len(rows)}+)"] + [f"• {esc(r['name'])} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else ''} — {fmt_day(r['created_at'])}{(' 📁' + esc(r['folder'])) if r['folder'] else ''}{(' 📝' + esc(r['note'][:30])) if r['note'] else ''}" for r in rows]
    await reply(update, "\n".join(lines))


@route("svt")
async def cb_svt(update, ctx, q, p):
    SV(ctx)["tab"] = p[1]
    t, kb = saved_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("svs")
async def cb_svs(update, ctx, q, p):
    S = SV(ctx)
    ks = ["new", "old", "serial"]
    S["sort"] = ks[(ks.index(S["sort"]) + 1) % 3]
    t, kb = saved_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("svq")
async def cb_svq(update, ctx, q, p):
    set_await(ctx, "saved_q")
    await ans(q, L(lg(q.from_user.id), "🔍 সিরিয়ালের নাম বা নোটের শব্দ লিখুন", "🔍 Type a serial name or note word"), True)


@awaiting("saved_q")
async def aw_saved_q(update, ctx, payload):
    SV(ctx).update(q=update.effective_message.text.strip(), tab="ep")
    t, kb = saved_view(update.effective_user.id, 0, ctx)
    await reply(update, t, kb)


@route("svsr")
async def cb_svsr(update, ctx, q, p):
    uid = q.from_user.id
    rows = Q("SELECT DISTINCT se.id, se.name FROM saves sv JOIN eps e ON e.id=sv.episode_id JOIN serials se ON se.id=e.serial_id WHERE sv.user_id=? ORDER BY se.name LIMIT 20", uid)
    kb = [[B(r["name"][:30], callback_data=f"svf:s{r['id']}")] for r in rows] + [[B("↩️", callback_data="sv:0")]]
    await show(update, ctx, L(lg(uid), "📺 কোন সিরিয়াল?", "📺 Which serial?"), M(kb))
    await ans(q)


@route("svf")
async def cb_svf(update, ctx, q, p):
    uid, S = q.from_user.id, SV(ctx)
    v = p[1]
    if v == "x":
        S.update(sid=None, q=None, folder=None, tab="ep")
    elif v.startswith("s"):
        S.update(sid=int(v[1:]), tab="ep")
    else:
        fol = user_folders(uid)
        S.update(folder=fol[int(v)] if int(v) < len(fol) else None, tab="ep")
    t, kb = saved_view(uid, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("svn")
async def cb_svn(update, ctx, q, p):
    set_await(ctx, "folder_new")
    await ans(q, L(lg(q.from_user.id), "📁 নতুন ফোল্ডারের নাম লিখুন", "📁 Type the new folder name"), True)


@awaiting("folder_new")
async def aw_folder_new(update, ctx, payload):
    uid, name = update.effective_user.id, (update.effective_message.text or "").strip()[:24]
    if name:
        X("INSERT OR IGNORE INTO folders (user_id, name) VALUES (?,?)", uid, name)
    SV(ctx)["tab"] = "fo"
    t, kb = saved_view(uid, 0, ctx)
    await reply(update, t, kb)


@route("svm0")
async def cb_svm0(update, ctx, q, p):
    S = SV(ctx)
    S["multi"] = 0 if S["multi"] else 1
    S["sel"] = set()
    t, kb = saved_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("svm")
async def cb_svm(update, ctx, q, p):
    SV(ctx)["sel"] ^= {int(p[1])}
    t, kb = saved_view(q.from_user.id, int(p[2]), ctx)
    await show(update, ctx, t, kb)
    await ans(q)


@route("svx")
async def cb_svx(update, ctx, q, p):
    uid, S = q.from_user.id, SV(ctx)
    for e in S["sel"]:
        X("DELETE FROM saves WHERE user_id=? AND episode_id=?", uid, e)
    n = len(S["sel"])
    S["sel"], S["multi"] = set(), 0
    t, kb = saved_view(uid, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q, f"🗑 {n}")


@route("svall")
async def cb_svall(update, ctx, q, p):
    lang = lg(q.from_user.id)
    await show(update, ctx, L(lang, "⚠️ <b>সব সেভ করা এপিসোড মুছে ফেলবেন?</b>", "⚠️ <b>Delete ALL saved episodes?</b>"),
               M([[B(L(lang, "✅ হ্যাঁ, সব মুছুন", "✅ Yes, delete all"), callback_data="svall2"), B(L(lang, "❌ না", "❌ No"), callback_data="sv:0")]]))
    await ans(q)


@route("svall2")
async def cb_svall2(update, ctx, q, p):
    X("DELETE FROM saves WHERE user_id=?", q.from_user.id)
    t, kb = saved_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q, "🗑")


def saved_item(uid, ep_id):
    r = Q1("SELECT sv.*, e.ep_no, e.title, se.name FROM saves sv JOIN eps e ON e.id=sv.episode_id JOIN serials se ON se.id=e.serial_id WHERE sv.user_id=? AND sv.episode_id=?", uid, ep_id)
    lang = lg(uid)
    if not r:
        return None
    text = (f"⭐ <b>{esc(r['name'])}</b> {('E' + str(r['ep_no'])) if r['ep_no'] is not None else esc(r['title'] or '')}\n📅 {fmt_ts(r['created_at'])}\n"
            f"📁 {esc(r['folder']) or '—'}\n📝 {esc(r['note']) or '—'}" + (f"\n⏰ {fmt_ts(r['remind_at'])}" if r["remind_at"] > now() else ""))
    kb = [[B(L(lang, "▶️ পাঠাও", "▶️ Send"), callback_data=f"eg:{ep_id}"), B(L(lang, "✏️ নোট", "✏️ Note"), callback_data=f"svo:{ep_id}")],
          [B(L(lang, "📁 ফোল্ডার", "📁 Folder"), callback_data=f"svk:{ep_id}"), B(L(lang, "⏰ ৭ দিন পরে মনে করাও", "⏰ Remind in 7d"), callback_data=f"svr:{ep_id}")],
          [B(L(lang, "🔗 শেয়ার", "🔗 Share"), callback_data=f"svh:{ep_id}"), B(L(lang, "🗑 বাদ দিন", "🗑 Remove"), callback_data=f"svd:{ep_id}")],
          [B("↩️", callback_data="sv:0")] + home_row(lang)]
    return text, M(kb)


@route("svi")
async def cb_svi(update, ctx, q, p):
    v = saved_item(q.from_user.id, int(p[1]))
    if not v:
        return await ans(q, L(lg(q.from_user.id), "⚠️ এপিসোডটি আর নেই", "⚠️ Episode no longer exists"), True)
    await show(update, ctx, *v)
    await ans(q)


@route("svo")
async def cb_svo(update, ctx, q, p):
    set_await(ctx, "note", int(p[1]))
    await ans(q, L(lg(q.from_user.id), "✏️ নোট লিখে পাঠান (মুছতে - লিখুন)", "✏️ Send your note (send - to clear)"), True)


@awaiting("note")
async def aw_note(update, ctx, ep_id):
    t = (update.effective_message.text or "").strip()
    X("UPDATE saves SET note=? WHERE user_id=? AND episode_id=?", "" if t == "-" else t[:200], update.effective_user.id, ep_id)
    v = saved_item(update.effective_user.id, ep_id)
    if v:
        await reply(update, *v)


@route("svk")
async def cb_svk(update, ctx, q, p):
    uid, ep_id = q.from_user.id, int(p[1])
    fol = user_folders(uid)
    kb = [[B(f"📁 {n}", callback_data=f"svk2:{ep_id}:{i}")] for i, n in enumerate(fol)] + [[B(L(lg(uid), "কোনোটাই না", "None"), callback_data=f"svk2:{ep_id}:x")], [B("↩️", callback_data=f"svi:{ep_id}")]]
    await show(update, ctx, L(lg(uid), "📁 কোন ফোল্ডারে রাখবেন?", "📁 Which folder?"), M(kb))
    await ans(q)


@route("svk2")
async def cb_svk2(update, ctx, q, p):
    uid, ep_id = q.from_user.id, int(p[1])
    fol = user_folders(uid)
    name = "" if p[2] == "x" else fol[int(p[2])]
    X("UPDATE saves SET folder=? WHERE user_id=? AND episode_id=?", name, uid, ep_id)
    if name == "পরে দেখব":
        X("UPDATE saves SET remind_at=? WHERE user_id=? AND episode_id=?", now() + 7 * 86400, uid, ep_id)
    v = saved_item(uid, ep_id)
    if v:
        await show(update, ctx, *v)
    await ans(q, "✅")


@route("svr")
async def cb_svr(update, ctx, q, p):
    uid, ep_id = q.from_user.id, int(p[1])
    X("UPDATE saves SET remind_at=? WHERE user_id=? AND episode_id=?", now() + 7 * 86400, uid, ep_id)
    v = saved_item(uid, ep_id)
    if v:
        await show(update, ctx, *v)
    await ans(q, L(lg(uid), "⏰ ৭ দিন পরেও না দেখলে মনে করানো হবে", "⏰ We'll remind you in 7 days if unwatched"), True)


@route("svh")
async def cb_svh(update, ctx, q, p):
    lang = lg(q.from_user.id)
    link = f"https://t.me/{ctx.bot.username}?start=ep_{int(p[1])}"
    await show(update, ctx, L(lang, f"🔗 <b>শেয়ার লিংক</b>\n<code>{link}</code>\n\nবন্ধু লিংকে ট্যাপ করলে এপিসোড পাবে (সিরিয়ালটি ফলো থাকলে বা Premium হলে), নইলে সিরিয়ালের পেজ দেখবে।",
                              f"🔗 <b>Share link</b>\n<code>{link}</code>\n\nYour friend gets the episode if they follow the serial or have Premium; otherwise they see the serial page."),
               M([[B(L(lang, "📤 পাঠান", "📤 Send"), switch_inline_query=f"{link}")], [B("↩️", callback_data=f"svi:{int(p[1])}")]]))
    await ans(q)


@route("svd")
async def cb_svd(update, ctx, q, p):
    X("DELETE FROM saves WHERE user_id=? AND episode_id=?", q.from_user.id, int(p[1]))
    t, kb = saved_view(q.from_user.id, 0, ctx)
    await show(update, ctx, t, kb)
    await ans(q, "🗑")


# ================================================================ /history
def HF(ctx):
    d = ctx.user_data.get("hf")
    if d is None:
        d = ctx.user_data["hf"] = dict(sid=None, d=None, uw=0, q=None)
    return d


def history_main(uid):
    lang = lg(uid)
    total = V("SELECT COUNT(*) FROM deliveries WHERE user_id=?", uid)
    watched = V("SELECT COUNT(*) FROM deliveries WHERE user_id=? AND watched=1", uid)
    on = V("SELECT save_hist FROM users WHERE user_id=?", uid, d=1)
    text = L(lang, f"🕘 <b>আমার ইতিহাস</b>\n\n🎬 পাওয়া এপিসোড: <b>{total}</b>\n✅ দেখা হয়েছে: <b>{watched}</b>\n❌ না-দেখা: <b>{total - watched}</b>\n💾 ইতিহাস সংরক্ষণ: {'✅ চালু' if on else '❌ বন্ধ'}\n\n🔒 শুধু আপনিই নিজের ইতিহাস দেখতে পারবেন।",
             f"🕘 <b>My history</b>\n\n🎬 Received: <b>{total}</b>\n✅ Watched: <b>{watched}</b>\n❌ Unwatched: <b>{total - watched}</b>\n💾 History: {'✅ on' if on else '❌ off'}\n\n🔒 Only you can see your history.")
    kb = [[B(L(lang, "🎬 দেখা তালিকা", "🎬 List"), callback_data="hl:0"), B(L(lang, "❌ না-দেখা", "❌ Unwatched"), callback_data="hu:0")],
          [B(L(lang, "▶️ যেখানে ছেড়েছিলাম", "▶️ Where I left off"), callback_data="cw"), B(L(lang, "📊 পরিসংখ্যান", "📊 Stats"), callback_data="hst")],
          [B(L(lang, "🔍 সার্চ", "🔍 Search"), callback_data="hq"), B(L(lang, "🗑 মুছুন", "🗑 Delete"), callback_data="hd")],
          [B(L(lang, "💎 প্ল্যান ইতিহাস", "💎 Plan history"), callback_data="hph"), B(L(lang, "💳 পেমেন্ট", "💳 Payments"), callback_data="hpm")],
          [B(L(lang, "🎫 টিকেট ইতিহাস", "🎫 Tickets"), callback_data="tkl:0"), B(L(lang, "💾 সংরক্ষণ বন্ধ/চালু", "💾 Toggle saving"), callback_data="hsv")], home_row(lang)]
    return text, M(kb)


def history_list(uid, page, ctx, unwatched=False):
    h, lang, u = HF(ctx), lg(uid), user(uid)
    if unwatched:
        h = dict(h, uw=1)
    where, args = ["d.user_id=?"], [uid]
    if h["sid"]:
        where.append("e.serial_id=?")
        args.append(h["sid"])
    if h["d"]:
        lo = {"t": day_start(), "w": now() - 7 * 86400, "m": int(local().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())}[h["d"]]
        where.append("d.sent_at>=?")
        args.append(lo)
    if h["uw"]:
        where.append("d.watched=0")
    if h["q"]:
        where.append("s.name_key LIKE ?")
        args.append(f"%{norm(h['q'])}%")
    base = "FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id WHERE " + " AND ".join(where)
    total = V(f"SELECT COUNT(*) {base}", *args)
    page, pages, off = paginate(total, page, 10)
    rows = Q(f"SELECT d.episode_id, d.sent_at, d.watched, d.quality, e.ep_no, e.title, s.name {base} ORDER BY d.sent_at DESC LIMIT 10 OFFSET ?", *args, off)
    pre = "hu" if unwatched else "hl"
    text = L(lang, f"🕘 <b>{'না-দেখা এপিসোড' if unwatched else 'দেখার ইতিহাস'}</b> ({total})\nনতুন থেকে পুরনো • ট্যাপ করলে আবার আসবে", f"🕘 <b>{'Unwatched' if unwatched else 'Watch history'}</b> ({total})\nNewest first • tap to get again")
    flt = ([{"t": L(lang, "আজ", "Today"), "w": L(lang, "৭ দিন", "7 days"), "m": L(lang, "এই মাস", "This month")}[h["d"]]] if h["d"] else []) + ([f"🔍 {esc(h['q'])}"] if h["q"] else []) + ([f"📺 {esc(V('SELECT name FROM serials WHERE id=?', h['sid'], d=''))}"] if h["sid"] else [])
    if flt:
        text += "\n" + " • ".join(flt)
    kb = []
    for r in rows:
        lab = f"{'✅' if r['watched'] else '▫️'} {r['name'][:16]} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else (r['title'] or '')[:10]} · {r['quality']}p · {fmt_day(r['sent_at'])}"
        kb.append([B(lab, callback_data=f"eg:{r['episode_id']}"), B("☑️" if r["watched"] else "✅", callback_data=f"hw:{r['episode_id']}:{page}:{1 if unwatched else 0}")])
    nv = nav_row(pre, page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, "আজ", "Today"), callback_data="hf:d:t"), B(L(lang, "৭ দিন", "7d"), callback_data="hf:d:w"), B(L(lang, "এই মাস", "Month"), callback_data="hf:d:m"), B(L(lang, "সব", "All"), callback_data="hf:d:a")])
    kb.append([B(L(lang, "📺 সিরিয়াল ফিল্টার", "📺 By serial"), callback_data="hf:s"), B("♻️", callback_data="hf:x")])
    kb.append([B("↩️", callback_data="hs")] + home_row(lang))
    return text, M(kb)


PAGERS["hl"] = lambda uid, page, ctx: history_list(uid, page, ctx)
PAGERS["hu"] = lambda uid, page, ctx: history_list(uid, page, ctx, True)


@command("history")
async def cmd_history(update, ctx):
    uid = update.effective_user.id
    if is_admin(uid) and ctx.args and ctx.args[0].lstrip("-").isdigit():
        return await admin_history_of(update, ctx, int(ctx.args[0]))
    t, kb = history_main(uid)
    await reply(update, t, kb)


@route("hs")
async def cb_hs(update, ctx, q, p):
    await show(update, ctx, *history_main(q.from_user.id))
    await ans(q)


@route("hf")
async def cb_hf(update, ctx, q, p):
    uid, h = q.from_user.id, HF(ctx)
    if p[1] == "d":
        h["d"] = None if p[2] == "a" else p[2]
    elif p[1] == "x":
        h.update(sid=None, d=None, uw=0, q=None)
    elif p[1] == "sid":
        h["sid"] = int(p[2])
    elif p[1] == "s":
        rows = Q("SELECT DISTINCT s.id, s.name FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id WHERE d.user_id=? ORDER BY s.name LIMIT 20", uid)
        kb = [[B(r["name"][:30], callback_data=f"hf:sid:{r['id']}")] for r in rows] + [[B("↩️", callback_data="hl:0")]]
        await show(update, ctx, L(lg(uid), "📺 কোন সিরিয়ালের ইতিহাস?", "📺 Which serial?"), M(kb))
        return await ans(q)
    await show(update, ctx, *history_list(uid, 0, ctx))
    await ans(q)


@route("hw")
async def cb_hw(update, ctx, q, p):
    uid, ep_id, page, un = q.from_user.id, int(p[1]), int(p[2]), p[3] == "1"
    X("UPDATE deliveries SET watched=1-watched, watched_at=? WHERE user_id=? AND episode_id=?", now(), uid, ep_id)
    await show(update, ctx, *history_list(uid, page, ctx, un))
    await ans(q)


@route("hq")
async def cb_hq(update, ctx, q, p):
    set_await(ctx, "hist_q")
    await ans(q, L(lg(q.from_user.id), "🔍 ইতিহাসে খোঁজার সিরিয়ালের নাম লিখুন", "🔍 Type a serial name to search history"), True)


@awaiting("hist_q")
async def aw_hist_q(update, ctx, payload):
    HF(ctx)["q"] = update.effective_message.text.strip()
    await reply(update, *history_list(update.effective_user.id, 0, ctx))


@route("hst")
async def cb_hst(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    top = Q("SELECT s.name, COUNT(*) AS n FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id WHERE d.user_id=? GROUP BY s.id ORDER BY n DESC LIMIT 3", uid)
    month = V("SELECT COUNT(*) FROM deliveries WHERE user_id=? AND sent_at>=?", uid, int(local().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()))
    lines = [L(lang, "📊 <b>আমার পরিসংখ্যান</b>", "📊 <b>My stats</b>"), L(lang, f"🎬 মোট এপিসোড: {V('SELECT COUNT(*) FROM deliveries WHERE user_id=?', uid)}", f"🎬 Total: {V('SELECT COUNT(*) FROM deliveries WHERE user_id=?', uid)}"),
             L(lang, f"📅 এই মাসে: {month}", f"📅 This month: {month}"), L(lang, f"⭐ সেভ: {V('SELECT COUNT(*) FROM saves WHERE user_id=?', uid)}", f"⭐ Saved: {V('SELECT COUNT(*) FROM saves WHERE user_id=?', uid)}"),
             L(lang, f"📌 ফলো: {follow_count(uid)}", f"📌 Following: {follow_count(uid)}")]
    if top:
        lines.append(L(lang, "\n🏆 সবচেয়ে বেশি:", "\n🏆 Most:") + "\n" + "\n".join(f"{i + 1}. {esc(r['name'])} — {r['n']}" for i, r in enumerate(top)))
    await show(update, ctx, "\n".join(lines), M([[B("↩️", callback_data="hs")] + home_row(lang)]))
    await ans(q)


@route("hd")
async def cb_hd(update, ctx, q, p):
    lang = lg(q.from_user.id)
    await show(update, ctx, L(lang, "🗑 <b>ইতিহাস মুছুন</b>", "🗑 <b>Delete history</b>"), M([[B(L(lang, "📺 একটি সিরিয়ালের", "📺 One serial"), callback_data="hds")],
                                                                                              [B(L(lang, "🗑 সব ইতিহাস", "🗑 All history"), callback_data="hda")], [B("↩️", callback_data="hs")]]))
    await ans(q)


@route("hds")
async def cb_hds(update, ctx, q, p):
    uid = q.from_user.id
    rows = Q("SELECT DISTINCT s.id, s.name FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id WHERE d.user_id=? ORDER BY s.name LIMIT 20", uid)
    kb = [[B(r["name"][:30], callback_data=f"hds2:{r['id']}")] for r in rows] + [[B("↩️", callback_data="hd")]]
    await show(update, ctx, L(lg(uid), "কোন সিরিয়ালের ইতিহাস মুছবেন?", "Which serial's history?"), M(kb))
    await ans(q)


@route("hds2", "hda", "hda2")
async def cb_hdx(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if p[0] == "hds2":
        if len(p) < 3:
            return await show(update, ctx, L(lang, "⚠️ নিশ্চিত?", "⚠️ Sure?"), M([[B("✅", callback_data=f"hds2:{p[1]}:1"), B("❌", callback_data="hd")]]))
        X("DELETE FROM deliveries WHERE user_id=? AND episode_id IN (SELECT id FROM eps WHERE serial_id=?)", uid, int(p[1]))
    elif p[0] == "hda":
        return await show(update, ctx, L(lang, "⚠️ <b>সব ইতিহাস মুছে ফেলবেন?</b>", "⚠️ <b>Delete ALL history?</b>"), M([[B("✅", callback_data="hda2"), B("❌", callback_data="hd")]]))
    else:
        X("DELETE FROM deliveries WHERE user_id=?", uid)
    await show(update, ctx, *history_main(uid))
    await ans(q, "🗑")


@route("hsv")
async def cb_hsv(update, ctx, q, p):
    uid = q.from_user.id
    X("UPDATE users SET save_hist=1-save_hist WHERE user_id=?", uid)
    await show(update, ctx, *history_main(uid))
    await ans(q)


@route("hph")
async def cb_hph(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT * FROM plan_history WHERE user_id=? ORDER BY at DESC LIMIT 15", uid)
    lines = [L(lang, "💎 <b>আমার প্ল্যান ইতিহাস</b>", "💎 <b>My plan history</b>")]
    for r in rows:
        who = L(lang, "সিস্টেম", "system") if not r["by"] else L(lang, "এডমিন", "admin")
        until = L(lang, "লাইফটাইম", "lifetime") if r["until"] == -1 else (fmt_ts(r["until"]) if r["until"] > 0 else "—")
        lines.append(f"• {fmt_ts(r['at'])} — <b>{esc(r['plan'])}</b> {esc(r['note'])}\n   ⏳ {L(lang, 'মেয়াদ', 'until')}: {until} • 👤 {who}")
    if not rows:
        lines.append(L(lang, "কোনো প্ল্যান নেওয়া হয়নি।", "No plans yet."))
    await show(update, ctx, "\n".join(lines), M([[B("↩️", callback_data="hs")] + home_row(lang)]))
    await ans(q)


@route("hpm")
async def cb_hpm(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT 15", uid)
    ic = {"pending": "⏳", "approved": "✅", "rejected": "❌", "cancelled": "🚫"}
    lines = [L(lang, "💳 <b>পেমেন্ট রেকর্ড</b>", "💳 <b>Payment records</b>")]
    for r in rows:
        lines.append(f"{ic.get(r['status'], '•')} #{r['id']} • {fmt_ts(r['created_at'])}\n   {esc(r['plan'])} • {r['amount']}৳ • {esc(r['method'])} • <code>{esc(r['trx'])}</code>" + (f"\n   ⚠️ {esc(r['reason'])}" if r["reason"] else ""))
    if not rows:
        lines.append(L(lang, "কোনো পেমেন্ট নেই।", "No payments."))
    await show(update, ctx, "\n".join(lines), M([[B("↩️", callback_data="hs")] + home_row(lang)]))
    await ans(q)


# ================================================================ ▶️ Continue Watching (সব জায়গায় বাটন)
def continue_view(uid):
    lang = lg(uid)
    rows = Q("""SELECT s.id AS sid, s.name, d.episode_id, d.watched, e.ep_no, e.title, d.sent_at,
                (SELECT id FROM eps n WHERE n.serial_id=s.id AND n.status='published' AND n.id>e.id ORDER BY n.id LIMIT 1) AS next_id
                FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id
                WHERE d.user_id=? AND d.id=(SELECT d2.id FROM deliveries d2 JOIN eps e2 ON e2.id=d2.episode_id WHERE d2.user_id=d.user_id AND e2.serial_id=s.id ORDER BY d2.sent_at DESC LIMIT 1)
                ORDER BY d.sent_at DESC LIMIT 10""", uid)
    lines = [L(lang, "▶️ <b>Continue Watching</b>\nযেখানে ছেড়েছিলেন সেখান থেকে শুরু করুন:", "▶️ <b>Continue Watching</b>\nPick up where you left off:")]
    kb = []
    for r in rows:
        lab = f"{r['name'][:18]} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else (r['title'] or '')[:10]}"
        if r["watched"] and r["next_id"]:
            kb.append([B(f"▶️ {L(lang, 'পরেরটা', 'Next')}: {lab}", callback_data=f"eg:{r['next_id']}"), B("✅", callback_data=f"cww:{r['next_id']}")])
        elif not r["watched"]:
            kb.append([B(f"▶️ {lab}", callback_data=f"eg:{r['episode_id']}"), B("✅", callback_data=f"cww:{r['episode_id']}")])
        else:
            kb.append([B(f"✅ {lab}", callback_data=f"sp:{r['sid']}")])
    if not rows:
        lines.append(L(lang, "\nএখনো কিছু দেখা হয়নি।", "\nNothing watched yet."))
    kb.append(home_row(lang))
    return "\n".join(lines), M(kb)


@route("cw")
async def cb_cw(update, ctx, q, p):
    await show(update, ctx, *continue_view(q.from_user.id))
    await ans(q)


@command("continue")
async def cmd_continue(update, ctx):
    await reply(update, *continue_view(update.effective_user.id))


@route("cww")
async def cb_cww(update, ctx, q, p):
    uid, ep_id = q.from_user.id, int(p[1])
    record_delivery(uid, ep_id, 0)
    X("UPDATE deliveries SET watched=1, watched_at=? WHERE user_id=? AND episode_id=?", now(), uid, ep_id)
    await show(update, ctx, *continue_view(uid))
    await ans(q, "✅")


# ================================================================ কমন: হেল্প, সার্চ, ক্যান্সেল
import io

try:
    from telegram import CopyTextButton
except ImportError:  # পুরনো PTB
    CopyTextButton = None


@command("serials")
async def cmd_serials(update, ctx):
    t, kb = list_view(update.effective_user.id, 0, ctx)
    await reply(update, t, kb)


@command("cancel")
async def cmd_cancel(update, ctx):
    ctx.user_data.pop("aw", None)
    await reply(update, L(lg(update.effective_user.id), "✅ বাতিল করা হয়েছে।", "✅ Cancelled."), M([home_row(lg(update.effective_user.id))]))


@route("noop")
async def cb_noop(update, ctx, q, p):
    await ans(q)


def help_view(uid):
    lang, u = lg(uid), user(uid)
    body, _, _ = get_text("help", lang)
    kb = []
    if gs("faq_url"):
        kb.append([B("📚 FAQ", url=gs("faq_url"))])
    kb.append([B(L(lang, "📩 সাপোর্ট", "📩 Support"), callback_data="su"), B(L(lang, "📜 নিয়ম", "📜 Terms"), callback_data="tm")])
    kb.append(home_row(lang))
    return fill(body, u, uid=uid, lang=lang), M(kb)


@command("help")
async def cmd_help(update, ctx):
    await reply(update, *help_view(update.effective_user.id))


@route("hp")
async def cb_hp(update, ctx, q, p):
    await show(update, ctx, *help_view(q.from_user.id))
    await ans(q)


# ================================================================ সেটিংস
def settings_view(uid):
    u, lang = user(uid), lg(uid)
    mq = max_quality(u)
    onoff = lambda v: "✅" if v else "❌"
    text = L(lang, f"⚙️ <b>সেটিংস</b>\n\n🌐 ভাষা: {'বাংলা' if lang == 'bn' else 'English'}\n🎞 ডিফল্ট কোয়ালিটি: <b>{u['q_default']}p</b> (আপনার সর্বোচ্চ {mq}p)\n"
                   f"📉 ডেটা সেভার: {onoff(u['data_saver'])}\n📦 সব কোয়ালিটি একসাথে: {onoff(u['send_all_q'])}\n📄 পেজ সাইজ: {u['page_size']}\n🕘 ইতিহাস সংরক্ষণ: {onoff(u['save_hist'])}\n⌨️ নিচের মেনু কিবোর্ড: {onoff(u['reply_kb'])}",
             f"⚙️ <b>Settings</b>\n\n🌐 Language: {'বাংলা' if lang == 'bn' else 'English'}\n🎞 Default quality: <b>{u['q_default']}p</b> (max {mq}p)\n"
             f"📉 Data saver: {onoff(u['data_saver'])}\n📦 Send all qualities: {onoff(u['send_all_q'])}\n📄 Page size: {u['page_size']}\n🕘 Save history: {onoff(u['save_hist'])}\n⌨️ Bottom keyboard: {onoff(u['reply_kb'])}")
    kb = [[B("🇧🇩 বাংলা", callback_data="stl:bn"), B("🇬🇧 English", callback_data="stl:en")],
          [B(("✅ " if u["q_default"] == n else ("🔒 " if n > mq else "")) + f"{n}p", callback_data=f"stq:{n}") for n in QUALS],
          [B(L(lang, f"📉 ডেটা সেভার {onoff(u['data_saver'])}", f"📉 Data saver {onoff(u['data_saver'])}"), callback_data="stt:data_saver"),
           B(L(lang, f"📦 সব কোয়ালিটি {onoff(u['send_all_q'])}", f"📦 All qualities {onoff(u['send_all_q'])}"), callback_data="stt:send_all_q")],
          [B(("✅" if u["page_size"] == n else "") + f"📄 {n}", callback_data=f"stp:{n}") for n in (5, 8, 10)],
          [B(L(lang, f"🕘 ইতিহাস {onoff(u['save_hist'])}", f"🕘 History {onoff(u['save_hist'])}"), callback_data="stt:save_hist"),
           B(L(lang, f"⌨️ কিবোর্ড {onoff(u['reply_kb'])}", f"⌨️ Keyboard {onoff(u['reply_kb'])}"), callback_data="stt:reply_kb")],
          [B(L(lang, "🔔 নোটিফিকেশন", "🔔 Notifications"), callback_data="nf"), B(L(lang, "▶️ অটোপ্লে", "▶️ Autoplay"), callback_data="ap")],
          [B(L(lang, "💎 প্ল্যান", "💎 Plan"), callback_data="pl"), B(L(lang, "📜 নিয়ম/ডেটা", "📜 Terms/Data"), callback_data="tm")], home_row(lang)]
    return text, M(kb)


@command("settings")
async def cmd_settings(update, ctx):
    await reply(update, *settings_view(update.effective_user.id))


@route("st")
async def cb_st(update, ctx, q, p):
    await show(update, ctx, *settings_view(q.from_user.id))
    await ans(q)


@route("stl", "stq", "stt", "stp")
async def cb_st2(update, ctx, q, p):
    uid = q.from_user.id
    if p[0] == "stl":
        X("UPDATE users SET lang=? WHERE user_id=?", p[1], uid)
        _LANG[uid] = p[1]
    elif p[0] == "stq":
        n = int(p[1])
        if n > max_quality(user(uid)):
            return await ans(q, _lock_alert(lg(uid)), True)
        X("UPDATE users SET q_default=? WHERE user_id=?", n, uid)
    elif p[0] == "stp":
        X("UPDATE users SET page_size=? WHERE user_id=?", int(p[1]), uid)
    else:
        col = p[1]
        if col not in ("data_saver", "send_all_q", "save_hist", "reply_kb"):
            return await ans(q)
        X(f"UPDATE users SET {col}=1-{col} WHERE user_id=?", uid)
        if col == "reply_kb":
            await sync_reply_kb(ctx, uid)
    await show(update, ctx, *settings_view(uid))
    await ans(q, "✅")


async def sync_reply_kb(ctx, uid):
    u, lang = user(uid), lg(uid)
    if u["reply_kb"]:
        kb = ReplyKeyboardMarkup([[KeyboardButton(L(lang, "📺 সিরিয়াল", "📺 Serials")), KeyboardButton(L(lang, "📌 আমার ফিড", "📌 My feed"))],
                                  [KeyboardButton(L(lang, "🔍 সার্চ", "🔍 Search")), KeyboardButton(L(lang, "▶️ Continue", "▶️ Continue"))]], resize_keyboard=True)
        await tsend(ctx.bot, uid, L(lang, "⌨️ কিবোর্ড চালু", "⌨️ Keyboard on"), kb)
    else:
        await tsend(ctx.bot, uid, L(lang, "⌨️ কিবোর্ড বন্ধ", "⌨️ Keyboard off"), ReplyKeyboardRemove())


# ================================================================ /notification
def notif_view(uid):
    u, lang = user(uid), lg(uid)
    onoff = lambda v: "✅" if v else "❌"
    dm = {"now": L(lang, "⚡ সাথে সাথে", "⚡ Instant"), "morning": L(lang, "🌅 সকাল ৮টা", "🌅 8 AM"), "night": L(lang, "🌙 রাত ৮টা", "🌙 8 PM")}[u["deliver_mode"] or "now"]
    dg = {"off": L(lang, "বন্ধ", "off"), "hourly": L(lang, "ঘণ্টায়", "hourly"), "daily": L(lang, "দৈনিক", "daily")}[u["digest"] or "off"]
    off = L(lang, "নেই", "none") if u["holiday_dow"] < 0 else DOWS[u["holiday_dow"]]
    text = L(lang, f"🔔 <b>নোটিফিকেশন কন্ট্রোল</b>\n\n🔔 মূল সুইচ: {onoff(u['notify'])}\n🔕 নিঃশব্দ: {onoff(u['silent'])}\n⏰ ডেলিভারি সময়: {dm}\n🌙 কোয়াইট আওয়ার: {onoff(u['quiet_on'])} ({u['quiet_start']}:00–{u['quiet_end']}:00)\n📬 ডাইজেস্ট: {dg}\n📅 সাপ্তাহিক ছুটি: {off}\n\n"
                   f"📂 <b>ধরন</b>: এপিসোড {onoff(u['nt_ep'])} • নতুন সিরিয়াল {onoff(u['nt_new'])} • ঘোষণা {onoff(u['nt_ann'])} • প্ল্যান {onoff(u['nt_plan'])} • অফার {onoff(u['nt_offer'])} • রিমাইন্ডার {onoff(u['nt_rem'])} • প্রোমো {onoff(u['nt_promo'])}",
             f"🔔 <b>Notification control</b>\n\n🔔 Master: {onoff(u['notify'])}\n🔕 Silent: {onoff(u['silent'])}\n⏰ Delivery: {dm}\n🌙 Quiet hours: {onoff(u['quiet_on'])} ({u['quiet_start']}:00–{u['quiet_end']}:00)\n📬 Digest: {dg}\n📅 Weekly off day: {off}\n\n"
             f"📂 <b>Types</b>: episodes {onoff(u['nt_ep'])} • new serials {onoff(u['nt_new'])} • announcements {onoff(u['nt_ann'])} • plan {onoff(u['nt_plan'])} • offers {onoff(u['nt_offer'])} • reminders {onoff(u['nt_rem'])} • promo {onoff(u['nt_promo'])}")
    kb = [[B(L(lang, f"🔔 মূল {onoff(u['notify'])}", f"🔔 Master {onoff(u['notify'])}"), callback_data="nft:notify"), B(L(lang, f"🔕 নিঃশব্দ {onoff(u['silent'])}", f"🔕 Silent {onoff(u['silent'])}"), callback_data="nft:silent")],
          [B(("✅" if (u["deliver_mode"] or "now") == k else "") + n, callback_data=f"nfd:{k}") for k, n in (("now", "⚡"), ("morning", "🌅"), ("night", "🌙"))],
          [B(L(lang, f"🌙 কোয়াইট {onoff(u['quiet_on'])}", f"🌙 Quiet {onoff(u['quiet_on'])}"), callback_data="nft:quiet_on"), B(L(lang, "⏱ সময় বদল", "⏱ Set hours"), callback_data="nfq")],
          [B(("✅" if (u["digest"] or "off") == k else "") + n, callback_data=f"nfg:{k}") for k, n in (("off", L(lang, "📬 বন্ধ", "📬 Off")), ("hourly", L(lang, "ঘণ্টায়", "Hourly")), ("daily", L(lang, "দৈনিক", "Daily")))],
          [B(L(lang, "📅 ছুটির দিন", "📅 Off day"), callback_data="nfh")],
          [B(L(lang, f"এপিসোড {onoff(u['nt_ep'])}", f"Episodes {onoff(u['nt_ep'])}"), callback_data="nft:nt_ep"), B(L(lang, f"নতুন {onoff(u['nt_new'])}", f"New {onoff(u['nt_new'])}"), callback_data="nft:nt_new"),
           B(L(lang, f"ঘোষণা {onoff(u['nt_ann'])}", f"News {onoff(u['nt_ann'])}"), callback_data="nft:nt_ann")],
          [B(L(lang, f"প্ল্যান {onoff(u['nt_plan'])}", f"Plan {onoff(u['nt_plan'])}"), callback_data="nft:nt_plan"), B(L(lang, f"অফার {onoff(u['nt_offer'])}", f"Offers {onoff(u['nt_offer'])}"), callback_data="nft:nt_offer"),
           B(L(lang, f"রিমাইন্ডার {onoff(u['nt_rem'])}", f"Reminders {onoff(u['nt_rem'])}"), callback_data="nft:nt_rem"), B(L(lang, f"প্রোমো {onoff(u['nt_promo'])}", f"Promo {onoff(u['nt_promo'])}"), callback_data="nft:nt_promo")],
          [B(L(lang, f"🙈 প্রোমো স্পয়লার-ঢাকা {onoff(u['promo_spoil'])}", f"🙈 Hide promos {onoff(u['promo_spoil'])}"), callback_data="nft:promo_spoil")],
          [B(L(lang, "🎛 প্রিসেট", "🎛 Presets"), callback_data="nfpr"), B(L(lang, "📺 সিরিয়াল ধরে", "📺 Per serial"), callback_data="nfs:0")],
          [B(L(lang, "🧪 টেস্ট", "🧪 Test"), callback_data="nftest"), B(L(lang, "🩺 ডায়াগনস্টিক", "🩺 Diagnostics"), callback_data="nfdg"), B(L(lang, "🕘 ইতিহাস", "🕘 History"), callback_data="nfl")], home_row(lang)]
    return text, M(kb)


@command("notification")
async def cmd_notification(update, ctx):
    await reply(update, *notif_view(update.effective_user.id))


@route("nf")
async def cb_nf(update, ctx, q, p):
    await show(update, ctx, *notif_view(q.from_user.id))
    await ans(q)


@route("nft", "nfd", "nfg")
async def cb_nf2(update, ctx, q, p):
    uid = q.from_user.id
    if p[0] == "nft" and p[1] in ("notify", "silent", "quiet_on", "nt_ep", "nt_new", "nt_ann", "nt_plan", "nt_offer", "nt_rem", "nt_promo", "promo_spoil"):
        X(f"UPDATE users SET {p[1]}=1-{p[1]} WHERE user_id=?", uid)
    elif p[0] == "nfd" and p[1] in ("now", "morning", "night"):
        X("UPDATE users SET deliver_mode=? WHERE user_id=?", p[1], uid)
    elif p[0] == "nfg" and p[1] in ("off", "hourly", "daily"):
        X("UPDATE users SET digest=? WHERE user_id=?", p[1], uid)
    await show(update, ctx, *notif_view(uid))
    await ans(q, "✅")


@route("nfq")
async def cb_nfq(update, ctx, q, p):
    set_await(ctx, "quiet")
    await ans(q, L(lg(q.from_user.id), "🌙 শুরু-শেষ ঘণ্টা লিখুন, যেমন: 23-7", "🌙 Send start-end hours, e.g. 23-7"), True)


@awaiting("quiet")
async def aw_quiet(update, ctx, payload):
    m = re.match(r"^\s*(\d{1,2})\s*[-–to ]+\s*(\d{1,2})\s*$", (update.effective_message.text or "").translate(_BNDIG))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 23:
        return await reply(update, "❌ ফরম্যাট: <code>23-7</code>")
    X("UPDATE users SET quiet_start=?, quiet_end=?, quiet_on=1 WHERE user_id=?", int(m.group(1)), int(m.group(2)), update.effective_user.id)
    await reply(update, *notif_view(update.effective_user.id))


@route("nfh")
async def cb_nfh(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if len(p) > 1:
        X("UPDATE users SET holiday_dow=? WHERE user_id=?", int(p[1]), uid)
        return await show(update, ctx, *notif_view(uid))
    kb = [[B(n, callback_data=f"nfh:{i}") for i, n in enumerate(DOWS[:4])], [B(n, callback_data=f"nfh:{i}") for i, n in enumerate(DOWS[4:], 4)],
          [B(L(lang, "❌ কোনো ছুটি নয়", "❌ None"), callback_data="nfh:-1")]]
    await show(update, ctx, L(lang, "📅 সপ্তাহের কোন দিন নোটিফিকেশন বন্ধ থাকবে?", "📅 Which weekday should be quiet?"), M(kb))
    await ans(q)


@route("nfpr")
async def cb_nfpr(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if len(p) > 1:
        pr = p[1]
        if pr == "all":
            X("UPDATE users SET notify=1, silent=0, quiet_on=0, digest='off', deliver_mode='now', nt_ep=1, nt_new=1, nt_ann=1, nt_plan=1, nt_offer=1, nt_rem=1, nt_promo=1 WHERE user_id=?", uid)
        elif pr == "imp":
            X("UPDATE users SET notify=1, nt_ep=1, nt_plan=1, nt_new=0, nt_offer=0, nt_promo=0, nt_ann=1 WHERE user_id=?", uid)
        elif pr == "night":
            X("UPDATE users SET quiet_on=1, quiet_start=23, quiet_end=7, silent=1 WHERE user_id=?", uid)
        elif pr == "dig":
            X("UPDATE users SET digest='daily', deliver_mode='morning' WHERE user_id=?", uid)
        elif pr == "off":
            X("UPDATE users SET notify=0 WHERE user_id=?", uid)
        return await show(update, ctx, *notif_view(uid))
    kb = [[B(L(lang, "🔔 সব চালু", "🔔 All on"), callback_data="nfpr:all")], [B(L(lang, "⭐ শুধু জরুরি", "⭐ Important only"), callback_data="nfpr:imp")],
          [B(L(lang, "🌙 রাতে নীরব", "🌙 Quiet nights"), callback_data="nfpr:night")], [B(L(lang, "📬 সকালে দৈনিক ডাইজেস্ট", "📬 Morning digest"), callback_data="nfpr:dig")],
          [B(L(lang, "⛔ সব বন্ধ", "⛔ All off"), callback_data="nfpr:off")], [B("↩️", callback_data="nf")]]
    await show(update, ctx, L(lang, "🎛 <b>প্রিসেট বেছে নিন</b>", "🎛 <b>Pick a preset</b>"), M(kb))
    await ans(q)


@route("nfp")
async def cb_nfp(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if len(p) > 1:
        if p[1] == "m":
            X("UPDATE follows SET mute=1 WHERE user_id=?", uid)
        elif p[1] == "u":
            X("UPDATE follows SET mute=0, pause_until=0 WHERE user_id=?", uid)
        else:
            X("UPDATE follows SET pause_until=? WHERE user_id=?", now() + int(p[1]) * 86400, uid)
        ctx.user_data.pop("fd", None)
        await show(update, ctx, *feed_view(uid, 0, ctx))
        return await ans(q, "✅")
    kb = [[B(L(lang, "🔕 সব নিঃশব্দ", "🔕 Mute all"), callback_data="nfp:m"), B(L(lang, "🔔 সব স্বাভাবিক", "🔔 Unmute all"), callback_data="nfp:u")],
          [B(L(lang, f"⏸ {d} দিন বিরতি", f"⏸ Pause {d}d"), callback_data=f"nfp:{d}") for d in (1, 3, 7)], [B("↩️", callback_data="fd:0")]]
    await show(update, ctx, L(lang, "🔕 <b>সব সিরিয়াল একসাথে</b>", "🔕 <b>All serials at once</b>"), M(kb))
    await ans(q)


def notif_serials(uid, page, ctx):
    lang, size = lg(uid), 8
    total = V("SELECT COUNT(*) FROM follows WHERE user_id=?", uid)
    page, pages, off = paginate(total, page, size)
    rows = Q("SELECT s.id, s.name, f.mute, f.pause_until FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? ORDER BY s.name LIMIT ? OFFSET ?", uid, size, off)
    kb = [[B(f"{mute_icon(r['mute'], r['pause_until']) or '🔔'} {r['name'][:30]}", callback_data=f"ns:{r['id']}")] for r in rows]
    nv = nav_row("nfs", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B("↩️", callback_data="nf")] + home_row(lang))
    return L(lang, "📺 <b>সিরিয়াল ধরে নোটিফিকেশন</b>\nট্যাপ করে আলাদা সেটিংস দিন।", "📺 <b>Per-serial notifications</b>\nTap to customise."), M(kb)


PAGERS["nfs"] = notif_serials


@route("nftest")
async def cb_nftest(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    st = await tsend(ctx.bot, uid, L(lang, "🧪 টেস্ট নোটিফিকেশন — এটি দেখতে পেলে সব ঠিক আছে ✅", "🧪 Test notification — if you see this, all good ✅"))
    await ans(q, "✅" if st == "ok" else "⚠️")


@route("nfdg")
async def cb_nfdg(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    u = user(uid)
    issues = []
    if not u["notify"]:
        issues.append(L(lang, "🔕 মূল নোটিফিকেশন বন্ধ", "🔕 Master switch is off"))
    if not u["nt_ep"]:
        issues.append(L(lang, "📂 এপিসোড নোটিফিকেশন বন্ধ", "📂 Episode notifications are off"))
    if u["quiet_on"]:
        issues.append(L(lang, f"🌙 কোয়াইট আওয়ার চালু ({u['quiet_start']}–{u['quiet_end']}) — তখন দেরিতে আসবে", "🌙 Quiet hours on — deliveries are delayed"))
    if u["digest"] != "off":
        issues.append(L(lang, "📬 ডাইজেস্ট মোড চালু — একসাথে আসবে", "📬 Digest mode — delivered in batches"))
    if u["blocked"]:
        issues.append(L(lang, "🚫 বট ব্লক করা ছিল", "🚫 Bot was blocked"))
    if V("SELECT COUNT(*) FROM follows WHERE user_id=?", uid) == 0:
        issues.append(L(lang, "📌 কোনো সিরিয়াল ফলো করেননি", "📌 You follow no serials"))
    pend = V("SELECT COUNT(*) FROM queue WHERE user_id=?", uid)
    text = L(lang, "🩺 <b>ডায়াগনস্টিক</b>\n\n", "🩺 <b>Diagnostics</b>\n\n") + ("\n".join(issues) if issues else L(lang, "✅ কোনো সমস্যা পাওয়া যায়নি।", "✅ No problems found.")) + L(lang, f"\n\n⏳ অপেক্ষমাণ ডেলিভারি: {pend}", f"\n\n⏳ Pending deliveries: {pend}")
    await show(update, ctx, text, M([[B("↩️", callback_data="nf")] + home_row(lang)]))
    await ans(q)


@route("nfl")
async def cb_nfl(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    rows = Q("SELECT * FROM notif_log WHERE user_id=? ORDER BY at DESC LIMIT 15", uid)
    text = L(lang, "🕘 <b>নোটিফিকেশন ইতিহাস</b>\n\n", "🕘 <b>Notification history</b>\n\n") + ("\n".join(f"• {fmt_ts(r['at'])} — {esc(r['title'])}" for r in rows) or L(lang, "খালি", "empty"))
    await show(update, ctx, text, M([[B("↩️", callback_data="nf")] + home_row(lang)]))
    await ans(q)


# ================================================================ /autoplay (ইউজার)
def autoplay_view(uid):
    u, lang = user(uid), lg(uid)
    onoff = lambda v: "✅ চালু" if v else "❌ বন্ধ"
    onoff_e = lambda v: "✅ On" if v else "❌ Off"
    f = onoff if lang == "bn" else onoff_e
    glob = gs("autoplay_on") == "1"
    ad = {0: L(lang, "বন্ধ", "off"), 1: L(lang, "১ ঘণ্টা", "1h"), 6: L(lang, "৬ ঘণ্টা", "6h"), 24: L(lang, "২৪ ঘণ্টা", "24h")}.get(u["auto_del"], f"{u['auto_del']}h")
    text = L(lang, f"▶️ <b>অটোপ্লে সেটিংস</b>\n\n🌐 গ্লোবাল অটোপ্লে: {'✅ চালু' if glob else '⛔ এডমিন বন্ধ রেখেছে'}\n▶️ ভিডিও নিজে নিজে আসবে: {f(u['autoplay'])}\n⏭ ✅ দেখার পর পরের এপিসোড: {f(u['auto_next'])}\n"
                   f"🎞 ডিফল্ট কোয়ালিটি: {u['q_default']}p\n🔕 নিঃশব্দ: {f(u['silent'])}\n🌙 কোয়াইট আওয়ার: {f(u['quiet_on'])}\n📬 ডাইজেস্ট: {u['digest']}\n🗑 অটো-ডিলিট: {ad}\n⏰ রিমাইন্ডার: {f(u['nt_rem'])}\n\n"
                   "💡 অটোপ্লে বন্ধ থাকলে শুধু নোটিফিকেশন + [▶️ এপিসোড নিন] বাটন আসবে।",
             f"▶️ <b>Autoplay settings</b>\n\n🌐 Global autoplay: {'✅ on' if glob else '⛔ admin turned off'}\n▶️ Auto-send videos: {f(u['autoplay'])}\n⏭ Auto-next after ✅: {f(u['auto_next'])}\n"
             f"🎞 Default quality: {u['q_default']}p\n🔕 Silent: {f(u['silent'])}\n🌙 Quiet hours: {f(u['quiet_on'])}\n📬 Digest: {u['digest']}\n🗑 Auto-delete: {ad}\n⏰ Reminders: {f(u['nt_rem'])}\n\n"
             "💡 With autoplay off you only get a notification + [▶️ Get episode] button.")
    kb = [[B(L(lang, "▶️ অটোপ্লে " + ("বন্ধ" if u["autoplay"] else "চালু"), "▶️ Autoplay " + ("off" if u["autoplay"] else "on")), callback_data="apt:autoplay"),
           B(L(lang, "⏭ অটো-নেক্সট " + ("বন্ধ" if u["auto_next"] else "চালু"), "⏭ Auto-next " + ("off" if u["auto_next"] else "on")), callback_data="apt:auto_next")],
          [B(L(lang, "🔕 নিঃশব্দ", "🔕 Silent"), callback_data="apt:silent"), B(L(lang, "🌙 কোয়াইট", "🌙 Quiet"), callback_data="apt:quiet_on"), B(L(lang, "⏰ রিমাইন্ডার", "⏰ Reminders"), callback_data="apt:nt_rem")],
          [B(("✅" if u["auto_del"] == h else "") + (f"🗑 {h}h" if h else L(lang, "🗑 বন্ধ", "🗑 off")), callback_data=f"apd:{h}") for h in (0, 1, 6, 24)],
          [B(("✅" if (u["digest"] or "off") == k else "") + n, callback_data=f"nfg:{k}") for k, n in (("off", "📬 Off"), ("hourly", "Hourly"), ("daily", "Daily"))],
          [B(L(lang, "🎞 কোয়ালিটি", "🎞 Quality"), callback_data="st"), B(L(lang, "📺 সিরিয়াল ধরে", "📺 Per serial"), callback_data="nfs:0")],
          [B(L(lang, "♻️ রিসেট", "♻️ Reset"), callback_data="apr0"), B(L(lang, "📊 স্ট্যাটাস", "📊 Status"), callback_data="aps")], home_row(lang)]
    return text, M(kb)


@command("autoplay")
async def cmd_autoplay(update, ctx):
    uid = update.effective_user.id
    if is_admin(uid):
        return await reply(update, *admin_autoplay_view())
    await reply(update, *autoplay_view(uid))


@route("ap")
async def cb_ap(update, ctx, q, p):
    uid = q.from_user.id
    await show(update, ctx, *(admin_autoplay_view() if is_admin(uid) and len(p) > 1 and p[1] == "a" else autoplay_view(uid)))
    await ans(q)


@route("apt", "apd", "apr0", "aps")
async def cb_ap2(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if p[0] == "apt" and p[1] in ("autoplay", "auto_next", "silent", "quiet_on", "nt_rem"):
        X(f"UPDATE users SET {p[1]}=1-{p[1]} WHERE user_id=?", uid)
    elif p[0] == "apd":
        X("UPDATE users SET auto_del=? WHERE user_id=?", int(p[1]), uid)
    elif p[0] == "apr0":
        X("UPDATE users SET autoplay=1, auto_next=0, auto_del=0, silent=0, quiet_on=0, digest='off', deliver_mode='now', q_default=? WHERE user_id=?", min(720, max_quality(user(uid))), uid)
    elif p[0] == "aps":
        n = V("SELECT COUNT(*) FROM deliveries WHERE user_id=? AND sent_at>=?", uid, now() - 7 * 86400)
        qn = V("SELECT COUNT(*) FROM queue WHERE user_id=?", uid)
        nf_ = V("SELECT COUNT(*) FROM follows WHERE user_id=? AND mute<2 AND pause_until<=?", uid, now())
        return await show(update, ctx, L(lang, f"📊 <b>অটোপ্লে স্ট্যাটাস</b>\n\n🎬 গত ৭ দিনে এসেছে: {n}\n⏳ কিউতে: {qn}\n📺 সক্রিয় সিরিয়াল: {nf_}", f"📊 <b>Autoplay status</b>\n\n🎬 Received in 7 days: {n}\n⏳ Queued: {qn}\n📺 Active serials: {nf_}"),
                          M([[B("↩️", callback_data="ap")] + home_row(lang)]))
    await show(update, ctx, *autoplay_view(uid))
    await ans(q, "✅")


# ================================================================ প্ল্যান ও পেমেন্ট (/plan, /tkply)
DOWS = ["সোম", "মঙ্গল", "বুধ", "বৃহস্পতি", "শুক্র", "শনি", "রবি"]
PLAN_SPECS = {"b7": ("price_b7", "🥈 Basic ৭ দিন", "🥈 Basic 7 days", 7), "b30": ("price_b30", "🥈 Basic ৩০ দিন", "🥈 Basic 30 days", 30), "b90": ("price_b90", "🥈 Basic ৯০ দিন", "🥈 Basic 90 days", 90), "7": ("price_7", "৭ দিন", "7 days", 7), "30": ("price_30", "৩০ দিন", "30 days", 30), "90": ("price_90", "৯০ দিন", "90 days", 90), "life": ("price_life", "♾ Lifetime", "♾ Lifetime", 0)}
METHODS = {"bkash": ("bKash", "📱"), "nagad": ("Nagad", "📲"), "rocket": ("Rocket", "🚀")}


def plan_view(uid):
    u, lang = user(uid), lg(uid)
    body, banner, _ = get_text("plan", lang)
    kb = [[B(L(lang, "💳 প্ল্যান কিনুন / রিনিউ", "💳 Buy / renew"), callback_data="tp")],
          [B(L(lang, "🧾 আমার পেমেন্ট", "🧾 My payments"), callback_data="hpm"), B(L(lang, "💎 আমার প্ল্যান", "💎 My plan"), callback_data="tpm0")],
          [B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")], home_row(lang)]
    return fill(body, u, uid=uid, lang=lang), M(kb)


@command("plan")
async def cmd_plan(update, ctx):
    await reply(update, *plan_view(update.effective_user.id))


@route("pl")
async def cb_pl(update, ctx, q, p):
    await show(update, ctx, *plan_view(q.from_user.id))
    await ans(q)


@route("tpm0")
async def cb_tpm0(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    u = user(uid)
    pend = V("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='pending'", uid)
    text = L(lang, f"💎 <b>আমার প্ল্যান</b>\n\n{plan_label(u, lang)}\n" + (f"⏳ মেয়াদ: {fmt_ts(u['plan_until'])}\n" if u["plan_until"] > 0 else "") + (f"⏳ অপেক্ষমাণ পেমেন্ট: {pend}" if pend else ""),
             f"💎 <b>My plan</b>\n\n{plan_label(u, lang)}\n" + (f"⏳ Valid until: {fmt_ts(u['plan_until'])}\n" if u["plan_until"] > 0 else "") + (f"⏳ Pending payments: {pend}" if pend else ""))
    await show(update, ctx, text, M([[B(L(lang, "🔄 রিনিউ", "🔄 Renew"), callback_data="tpr")], [B("↩️", callback_data="pl")] + home_row(lang)]))
    await ans(q)


def pay_methods():
    return [(k, METHODS[k][0], METHODS[k][1], gs("pm_" + k)) for k in METHODS if gs("on_" + k) == "1" and gs("pm_" + k)]


@command("tkply")
async def cmd_tkply(update, ctx):
    await reply(update, *tp_view(update.effective_user.id))


def tp_view(uid):
    lang = lg(uid)
    kb, lines = [], [L(lang, "💳 <b>প্ল্যান কিনুন</b>\n\nপ্ল্যান বেছে নিন:", "💳 <b>Buy a plan</b>\n\nChoose a plan:")]
    for k, (pk, bn, en, d) in PLAN_SPECS.items():
        price = gi(pk)
        if price > 0:
            kb.append([B(f"{'' if k.startswith('b') else '💎 '}{L(lang, bn, en)} — {price} ৳", callback_data=f"tpm:{k}")])
    if not kb:
        lines.append(L(lang, "\n⚠️ এখনো কোনো প্ল্যানের দাম সেট করা হয়নি। সাপোর্টে যোগাযোগ করুন।", "\n⚠️ No prices configured yet. Contact support."))
    pend = Q("SELECT id, plan, amount FROM payments WHERE user_id=? AND status='pending'", uid)
    for r in pend:
        kb.append([B(L(lang, f"⏳ #{r['id']} বাতিল ({r['amount']}৳)", f"⏳ Cancel #{r['id']} ({r['amount']}৳)"), callback_data=f"tpc:{r['id']}")])
    kb.append([B(L(lang, "🧾 পেমেন্ট ইতিহাস", "🧾 History"), callback_data="hpm"), B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")])
    kb.append(home_row(lang))
    return "\n".join(lines), M(kb)


@route("tp")
async def cb_tp(update, ctx, q, p):
    await show(update, ctx, *tp_view(q.from_user.id))
    await ans(q)


@route("tpr")
async def cb_tpr(update, ctx, q, p):
    uid = q.from_user.id
    last = Q1("SELECT plan FROM plan_history WHERE user_id=? AND plan IN ('7','30','90','life') ORDER BY at DESC LIMIT 1", uid)
    if last and gi(PLAN_SPECS[last["plan"]][0]) > 0:
        q.data = f"tpm:{last['plan']}"
        return await cb_tpm(update, ctx, q, ["tpm", last["plan"]])
    await show(update, ctx, *tp_view(uid))
    await ans(q)


@route("tpm")
async def cb_tpm(update, ctx, q, p):
    uid, lang, plan = q.from_user.id, lg(q.from_user.id), p[1]
    if plan not in PLAN_SPECS or gi(PLAN_SPECS[plan][0]) <= 0:
        return await ans(q, "❌", True)
    ms = pay_methods()
    if not ms:
        return await show(update, ctx, L(lang, "⚠️ পেমেন্ট নম্বর সেট করা নেই। সাপোর্টে যোগাযোগ করুন।", "⚠️ Payment numbers not set. Contact support."), M([[B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")]]))
    kb = [[B(f"{ic} {nm}", callback_data=f"tpx:{plan}:{k}")] for k, nm, ic, num in ms] + [[B("↩️", callback_data="tp")]]
    await show(update, ctx, L(lang, f"💳 <b>{PLAN_SPECS[plan][1]}</b> — {gi(PLAN_SPECS[plan][0])} ৳\n\nপেমেন্ট মাধ্যম বেছে নিন:", f"💳 <b>{PLAN_SPECS[plan][2]}</b> — {gi(PLAN_SPECS[plan][0])} ৳\n\nChoose a payment method:"), M(kb))
    await ans(q)


@route("tpx")
async def cb_tpx(update, ctx, q, p):
    uid, lang, plan, mk = q.from_user.id, lg(q.from_user.id), p[1], p[2]
    if plan not in PLAN_SPECS or mk not in METHODS:
        return await ans(q, "❌", True)
    num, price = gs("pm_" + mk), gi(PLAN_SPECS[plan][0])
    nm = METHODS[mk][0]
    text = L(lang, f"{METHODS[mk][1]} <b>{nm} পেমেন্ট</b>\n\n💎 প্ল্যান: <b>{PLAN_SPECS[plan][1]}</b>\n💰 পরিমাণ: <b>{price} ৳</b>\n📞 নম্বর (Send Money): <code>{esc(num)}</code>\n\n"
                   f"<b>ধাপ:</b>\n১️⃣ উপরের নম্বরে <b>{price} ৳</b> পাঠান\n২️⃣ ফিরতি মেসেজের <b>TrxID</b> কপি করুন\n৩️⃣ এখানে TrxID লিখে পাঠান (চাইলে স্ক্রিনশটও দিতে পারেন)\n\n⏳ এডমিন যাচাই করলে প্ল্যান অটো চালু হবে।",
             f"{METHODS[mk][1]} <b>{nm} payment</b>\n\n💎 Plan: <b>{PLAN_SPECS[plan][2]}</b>\n💰 Amount: <b>{price} ৳</b>\n📞 Number (Send Money): <code>{esc(num)}</code>\n\n"
             f"<b>Steps:</b>\n1️⃣ Send <b>{price} ৳</b> to the number\n2️⃣ Copy the <b>TrxID</b>\n3️⃣ Send the TrxID here (a screenshot is optional)\n\n⏳ Your plan activates once the admin verifies.")
    row1 = []
    if CopyTextButton:
        try:
            row1.append(B(L(lang, "📋 নম্বর কপি", "📋 Copy number"), copy_text=CopyTextButton(num)))
        except Exception:
            pass
    kb = ([row1] if row1 else []) + [[B(L(lang, "✍️ TrxID দিন", "✍️ Enter TrxID"), callback_data=f"tps:{plan}:{mk}")], [B("↩️", callback_data=f"tpm:{plan}")]]
    await show(update, ctx, text, M(kb))
    await ans(q)


@route("tps")
async def cb_tps(update, ctx, q, p):
    set_await(ctx, "trx", (p[1], p[2]))
    await ans(q, L(lg(q.from_user.id), "✍️ এখন TrxID লিখে পাঠান (বা স্ক্রিনশট)।", "✍️ Send your TrxID (or a screenshot) now."), True)
    await reply(update, L(lg(q.from_user.id), "✍️ <b>TrxID লিখে পাঠান</b> (বা পেমেন্টের স্ক্রিনশট)।\n/cancel দিয়ে বাতিল।", "✍️ <b>Send the TrxID</b> (or a payment screenshot).\n/cancel to abort."))


@awaiting("trx")
async def aw_trx(update, ctx, payload):
    plan, mk = payload
    m = update.effective_message
    uid, lang = update.effective_user.id, lg(update.effective_user.id)
    shot = m.photo[-1].file_id if m.photo else ""
    trx = ((m.text or m.caption or "").strip().upper().replace(" ", ""))[:40]
    if not trx and not shot:
        set_await(ctx, "trx", payload)
        return await reply(update, L(lang, "❌ TrxID লিখুন বা স্ক্রিনশট দিন।", "❌ Send a TrxID or screenshot."))
    if trx and not re.match(r"^[A-Z0-9]{6,20}$", trx):
        if not shot:
            set_await(ctx, "trx", payload)
            return await reply(update, L(lang, "❌ TrxID সঠিক নয় (৬–২০ অক্ষর/সংখ্যা)। আবার লিখুন।", "❌ Invalid TrxID (6–20 letters/digits). Try again."))
    dup = Q1("SELECT id, user_id, status FROM payments WHERE trx=? AND trx<>'' AND status IN ('pending','approved')", trx) if trx else None
    if dup:
        await alert_admins(Ctx(ctx.application), f"⚠️ <b>ডুপ্লিকেট TrxID চেষ্টা</b>\n👤 <code>{uid}</code> → <code>{esc(trx)}</code>\nআগের পেমেন্ট #{dup['id']} ({dup['status']}) ইউজার <code>{dup['user_id']}</code>")
        return await reply(update, L(lang, "⚠️ এই TrxID আগেই ব্যবহার হয়েছে। সঠিক TrxID দিন বা সাপোর্টে যোগাযোগ করুন।", "⚠️ This TrxID was already used. Send the right one or contact support."), M([[B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")]]))
    price = gi(PLAN_SPECS[plan][0])
    X("INSERT INTO payments (user_id, plan, amount, trx, status, created_at, method, shot) VALUES (?,?,?,?,'pending',?,?,?)", uid, plan, price, trx, now(), METHODS[mk][0], shot)
    pid = V("SELECT last_insert_rowid()")
    await reply(update, L(lang, f"✅ <b>পেমেন্ট অনুরোধ #{pid} জমা হয়েছে</b>\n\n⏳ স্ট্যাটাস: যাচাইয়ের অপেক্ষায়\nএডমিন যাচাই করলেই প্ল্যান চালু হবে এবং আপনাকে জানানো হবে।",
                          f"✅ <b>Payment request #{pid} submitted</b>\n\n⏳ Status: awaiting verification\nYou'll be notified once the admin approves."),
                M([[B(L(lang, "🚫 ভুল? বাতিল করুন", "🚫 Wrong? Cancel"), callback_data=f"tpc:{pid}"), B(L(lang, "⚠️ সমস্যা জানান", "⚠️ Report problem"), callback_data=f"tpq:{pid}")], home_row(lang)]))
    await send_pay_card(Ctx(ctx.application), pid)
    if gs("sms_verify") == "1":
        spawn(tick_sms(Ctx(ctx.application)))


async def send_pay_card(ctx, pid, shot_only=False):
    r = Q1("SELECT p.*, u.name, u.username FROM payments p JOIN users u ON u.user_id=p.user_id WHERE p.id=?", pid)
    if not r:
        return
    un = f"@{esc(r['username'])}" if r["username"] else "—"
    text = (f"💳 <b>নতুন পেমেন্ট অনুরোধ #{pid}</b>\n👤 {esc(r['name'])} ({un})\n🆔 <code>{r['user_id']}</code>\n💎 {esc(r['plan'])} • 💰 <b>{r['amount']} ৳</b>\n📱 {esc(r['method'])}\n🧾 TrxID: <code>{esc(r['trx']) or '—'}</code>\n🕒 {fmt_ts(r['created_at'])}")
    kb = M([[B("✅ Approve", callback_data=f"pay:ok:{pid}"), B("❌ Reject", callback_data=f"pay:no:{pid}")], [B("👤 ইউজার", callback_data=f"au:{r['user_id']}")]])
    for a in ADMIN_IDS:
        if r["shot"]:
            await guarded(lambda a=a: ctx.bot.send_photo(a, r["shot"], caption=text[:1000], reply_markup=kb))
        else:
            await tsend(ctx.bot, a, text, kb)


@route("tpc")
async def cb_tpc(update, ctx, q, p):
    uid, pid = q.from_user.id, int(p[1])
    r = X("UPDATE payments SET status='cancelled', decided_at=? WHERE id=? AND user_id=? AND status='pending'", now(), pid, uid)
    await ans(q, L(lg(uid), "🚫 বাতিল হয়েছে" if r.rowcount else "⚠️ বাতিল করা যাবে না", "🚫 Cancelled" if r.rowcount else "⚠️ Cannot cancel"), True)
    if r.rowcount:
        await alert_admins(Ctx(ctx.application), f"🚫 ইউজার <code>{uid}</code> পেমেন্ট #{pid} বাতিল করেছে।")
    await show(update, ctx, *tp_view(uid))


@route("tpq")
async def cb_tpq(update, ctx, q, p):
    set_await(ctx, "ticket_new", f"পেমেন্ট #{p[1]} সমস্যা")
    await ans(q, L(lg(q.from_user.id), "⚠️ সমস্যাটি লিখে পাঠান", "⚠️ Describe the problem"), True)


# ================================================================ সাপোর্ট টিকেট
TK_ST = {"open": "🟢", "answered": "💬", "closed": "⚫"}


def support_view(uid):
    lang = lg(uid)
    n = V("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status<>'closed'", uid)
    kb = [[B(L(lang, "✍️ নতুন টিকেট", "✍️ New ticket"), callback_data="tkn"), B(L(lang, f"🎫 আমার টিকেট ({n})", f"🎫 My tickets ({n})"), callback_data="tkl:0")]]
    if gs("faq_url"):
        kb.append([B("📚 FAQ", url=gs("faq_url"))])
    if ADMIN_CONTACT.startswith("@"):
        kb.append([B(L(lang, "📞 সরাসরি এডমিন", "📞 Direct admin"), url=f"https://t.me/{ADMIN_CONTACT[1:]}")])
    kb.append(home_row(lang))
    return L(lang, "📩 <b>সাপোর্ট</b>\n\nসমস্যা/প্রশ্ন জানাতে টিকেট খুলুন। প্রতিটি টিকেটের নম্বর থাকে, উত্তর এলে এখানেই জানানো হবে।", "📩 <b>Support</b>\n\nOpen a ticket for any issue/question. Each has a number; replies arrive here."), M(kb)


@command("support")
async def cmd_support(update, ctx):
    await reply(update, *support_view(update.effective_user.id))


@route("su")
async def cb_su(update, ctx, q, p):
    await show(update, ctx, *support_view(q.from_user.id))
    await ans(q)


@route("tkn")
async def cb_tkn(update, ctx, q, p):
    set_await(ctx, "ticket_new")
    await reply(update, L(lg(q.from_user.id), "✍️ <b>আপনার সমস্যা/প্রশ্ন লিখে পাঠান।</b>\n/cancel দিয়ে বাতিল।", "✍️ <b>Describe your issue/question.</b>\n/cancel to abort."))
    await ans(q)


@awaiting("ticket_new")
async def aw_ticket_new(update, ctx, payload):
    uid, lang = update.effective_user.id, lg(update.effective_user.id)
    body = (update.effective_message.text or update.effective_message.caption or "").strip()
    if not body:
        set_await(ctx, "ticket_new", payload)
        return await reply(update, "❌ টেক্সট লিখুন।")
    if V("SELECT COUNT(*) FROM tickets WHERE user_id=? AND created_at>?", uid, now() - 3600) >= 3 and not is_admin(uid):
        return await reply(update, L(lang, "⏳ ঘণ্টায় সর্বোচ্চ ৩টি টিকেট। একটু পরে চেষ্টা করুন।", "⏳ Max 3 tickets per hour. Try later."))
    subj = (f"[{payload}] " if payload else "") + body[:60]
    X("INSERT INTO tickets (user_id, status, subject, created_at, updated_at) VALUES (?,?,?,?,?)", uid, "open", subj, now(), now())
    tid = V("SELECT last_insert_rowid()")
    X("INSERT INTO ticket_msgs (ticket_id, from_admin, body, at) VALUES (?,0,?,?)", tid, body[:2000], now())
    await reply(update, L(lang, f"✅ <b>টিকেট #{tid}</b> খোলা হয়েছে। উত্তর এলে জানাব।", f"✅ <b>Ticket #{tid}</b> opened. We'll notify you on reply."), M([[B(L(lang, "🎫 দেখুন", "🎫 View"), callback_data=f"tkv:{tid}")], home_row(lang)]))
    await notify_ticket(Ctx(ctx.application), tid, body)


async def notify_ticket(ctx, tid, body):
    t = Q1("SELECT t.*, u.name, u.username FROM tickets t JOIN users u ON u.user_id=t.user_id WHERE t.id=?", tid)
    un = f"@{esc(t['username'])}" if t["username"] else "—"
    await alert_admins(ctx, f"🎫 <b>টিকেট #{tid}</b>\n👤 {esc(t['name'])} ({un}) <code>{t['user_id']}</code>\n\n{esc(body[:800])}",
                       M([[B("💬 উত্তর", callback_data=f"ar:{tid}"), B("📋 ক্যানড", callback_data=f"arc:{tid}"), B("⚫ বন্ধ", callback_data=f"atc:{tid}")]]))


def tickets_view(uid, page, ctx):
    lang = lg(uid)
    total = V("SELECT COUNT(*) FROM tickets WHERE user_id=?", uid)
    page, pages, off = paginate(total, page, 8)
    rows = Q("SELECT * FROM tickets WHERE user_id=? ORDER BY updated_at DESC LIMIT 8 OFFSET ?", uid, off)
    kb = [[B(f"{TK_ST.get(r['status'], '•')} #{r['id']} {r['subject'][:30]}", callback_data=f"tkv:{r['id']}")] for r in rows]
    nv = nav_row("tkl", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, "✍️ নতুন", "✍️ New"), callback_data="tkn")] + home_row(lang))
    return L(lang, f"🎫 <b>আমার টিকেট</b> ({total})", f"🎫 <b>My tickets</b> ({total})") + ("" if total else L(lang, "\n\nকোনো টিকেট নেই।", "\n\nNo tickets.")), M(kb)


PAGERS["tkl"] = tickets_view


@command("mytickets")
async def cmd_mytickets(update, ctx):
    await reply(update, *tickets_view(update.effective_user.id, 0, ctx))


@route("tkv")
async def cb_tkv(update, ctx, q, p):
    uid, lang, tid = q.from_user.id, lg(q.from_user.id), int(p[1])
    t = Q1("SELECT * FROM tickets WHERE id=? AND user_id=?", tid, uid)
    if not t:
        return await ans(q, "❌", True)
    msgs = Q("SELECT * FROM ticket_msgs WHERE ticket_id=? ORDER BY id DESC LIMIT 6", tid)[::-1]
    text = f"🎫 <b>#{tid}</b> {TK_ST.get(t['status'], '')} {esc(t['subject'])}\n\n" + "\n\n".join(f"{'🛠 ' + L(lang, 'এডমিন', 'Admin') if m['from_admin'] else '👤 ' + L(lang, 'আপনি', 'You')} • {fmt_ts(m['at'])}\n{esc(m['body'])}" for m in msgs)
    kb = []
    if t["status"] != "closed":
        kb.append([B(L(lang, "💬 উত্তর দিন", "💬 Reply"), callback_data=f"tku:{tid}"), B(L(lang, "⚫ বন্ধ করুন", "⚫ Close"), callback_data=f"tkx:{tid}")])
    kb.append([B("↩️", callback_data="tkl:0")] + home_row(lang))
    await show(update, ctx, text, M(kb))
    await ans(q)


@route("tku")
async def cb_tku(update, ctx, q, p):
    set_await(ctx, "ticket_reply", int(p[1]))
    await reply(update, L(lg(q.from_user.id), "💬 উত্তর লিখে পাঠান।", "💬 Send your reply."))
    await ans(q)


@awaiting("ticket_reply")
async def aw_ticket_reply(update, ctx, tid):
    uid = update.effective_user.id
    t = Q1("SELECT * FROM tickets WHERE id=? AND user_id=? AND status<>'closed'", tid, uid)
    body = (update.effective_message.text or "").strip()
    if not t or not body:
        return await reply(update, "❌")
    X("INSERT INTO ticket_msgs (ticket_id, from_admin, body, at) VALUES (?,0,?,?)", tid, body[:2000], now())
    X("UPDATE tickets SET status='open', updated_at=? WHERE id=?", now(), tid)
    await reply(update, L(lg(uid), "✅ উত্তর পাঠানো হয়েছে।", "✅ Reply sent."), M([[B("🎫", callback_data=f"tkv:{tid}")]]))
    await notify_ticket(Ctx(ctx.application), tid, body)


@route("tkx")
async def cb_tkx(update, ctx, q, p):
    X("UPDATE tickets SET status='closed', updated_at=? WHERE id=? AND user_id=?", now(), int(p[1]), q.from_user.id)
    await ans(q, "⚫")
    await show(update, ctx, *tickets_view(q.from_user.id, 0, ctx))


# ================================================================ /terms, সম্মতি, ডেটা
def consent_view(uid):
    lang, u = lg(uid), user(uid)
    body, _, _ = get_text("terms_sum", lang)
    rules, _, _ = get_text("terms_rules", lang)
    ch = gs("terms_changes")
    text = L(lang, "📜 <b>ব্যবহারের নিয়ম ও গোপনীয়তা</b>\n\nবট ব্যবহারের আগে নিচের নিয়মে সম্মত হতে হবে।\n\n", "📜 <b>Terms & privacy</b>\n\nPlease accept the terms to use the bot.\n\n") + fill(body, u, uid=uid, lang=lang) + "\n\n" + fill(rules, u, uid=uid, lang=lang)
    if ch:
        text += L(lang, f"\n\n🆕 <b>পরিবর্তন:</b> {esc(ch)}", f"\n\n🆕 <b>Changes:</b> {esc(ch)}")
    kb = [[B(L(lang, "✅ সম্মত আছি", "✅ I agree"), callback_data="tc:y")], [B(L(lang, "📖 পুরো নিয়ম", "📖 Full terms"), callback_data="tm")]]
    return text, M(kb)


@route("tc")
async def cb_tc(update, ctx, q, p):
    uid = q.from_user.id
    X("UPDATE users SET consent_ver=?, consent_at=? WHERE user_id=?", gi("terms_ver"), now(), uid)
    X("INSERT INTO consents (user_id, ver, at) VALUES (?,?,?)", uid, gi("terms_ver"), now())
    await ans(q, "✅")
    await send_home(update, ctx)


def terms_menu(uid):
    lang = lg(uid)
    kb = [[B(L(lang, bn, en), callback_data=f"tmp:{k}")] for k, bn, en in TERMS_KEYS]
    kb += [[B(L(lang, "📥 আমার ডেটা", "📥 My data"), callback_data="tmx"), B(L(lang, "🗑 ডেটা মুছুন", "🗑 Delete data"), callback_data="tmd")],
           [B(L(lang, "🚩 অপসারণ অনুরোধ", "🚩 Removal request"), callback_data="tmr")], home_row(lang)]
    return L(lang, f"📜 <b>নিয়ম ও গোপনীয়তা</b> (সংস্করণ {gi('terms_ver')})", f"📜 <b>Terms & privacy</b> (v{gi('terms_ver')})"), M(kb)


@command("terms")
async def cmd_terms(update, ctx):
    await reply(update, *terms_menu(update.effective_user.id))


@route("tm")
async def cb_tm(update, ctx, q, p):
    await show(update, ctx, *terms_menu(q.from_user.id))
    await ans(q)


@route("tmp")
async def cb_tmp(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    body, _, _ = get_text(p[1], lang)
    await show(update, ctx, fill(body, user(uid), uid=uid, lang=lang), M([[B("↩️", callback_data="tm")] + home_row(lang)]))
    await ans(q)


@route("tmx")
async def cb_tmx(update, ctx, q, p):
    uid = q.from_user.id
    d = {"user": dict(user(uid)), "follows": [r["name"] for r in Q("SELECT s.name FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=?", uid)],
         "saves": [dict(r) for r in Q("SELECT * FROM saves WHERE user_id=?", uid)], "history": [dict(r) for r in Q("SELECT * FROM deliveries WHERE user_id=?", uid)],
         "payments": [dict(r) for r in Q("SELECT * FROM payments WHERE user_id=?", uid)], "tickets": [dict(r) for r in Q("SELECT * FROM tickets WHERE user_id=?", uid)]}
    bio = io.BytesIO(json.dumps(d, ensure_ascii=False, indent=1, default=str).encode())
    bio.name = f"my_data_{uid}.json"
    await guarded(lambda: ctx.bot.send_document(uid, bio, caption="📥 My data"))
    await ans(q, "✅")


@route("tmd")
async def cb_tmd(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    if len(p) > 1 and p[1] == "y":
        for t in ("follows", "saves", "favs", "deliveries", "ratings", "searches", "queue", "folders", "quality_wait", "requests", "notif_log"):
            X(f"DELETE FROM {t} WHERE user_id=?", uid)
        X("UPDATE users SET recent_q='[]' WHERE user_id=?", uid)
        alog(0, "user_data_deleted", uid)
        return await show(update, ctx, L(lang, "🗑 আপনার ডেটা মুছে ফেলা হয়েছে।", "🗑 Your data has been deleted."), M([home_row(lang)]))
    await show(update, ctx, L(lang, "⚠️ <b>ফলো, সেভ, ইতিহাস, সার্চ — সব মুছে যাবে।</b> (প্ল্যান ও পেমেন্ট রেকর্ড থাকবে)", "⚠️ <b>Follows, saves, history, searches will be erased.</b> (Plan & payment records stay)"),
               M([[B(L(lang, "✅ হ্যাঁ, মুছুন", "✅ Yes, delete"), callback_data="tmd:y"), B(L(lang, "❌ না", "❌ No"), callback_data="tm")]]))
    await ans(q)


@route("tmr")
async def cb_tmr(update, ctx, q, p):
    set_await(ctx, "removal")
    await reply(update, L(lg(q.from_user.id), "🚩 যে কনটেন্ট সরাতে চান তার নাম/লিংক ও কারণ লিখুন।", "🚩 Describe the content to remove and why."))
    await ans(q)


@awaiting("removal")
async def aw_removal(update, ctx, payload):
    uid, txt = update.effective_user.id, (update.effective_message.text or "").strip()
    if not txt:
        return await reply(update, "❌")
    X("INSERT INTO removals (user_id, target, reason, status, created_at) VALUES (?,?,?,'pending',?)", uid, txt[:200], txt[:500], now())
    rid = V("SELECT last_insert_rowid()")
    await reply(update, L(lg(uid), f"✅ অনুরোধ #{rid} জমা হয়েছে।", f"✅ Request #{rid} submitted."))
    await alert_admins(Ctx(ctx.application), f"🚩 <b>অপসারণ অনুরোধ #{rid}</b>\n👤 <code>{uid}</code>\n{esc(txt[:500])}", M([[B("✅ সম্পন্ন", callback_data=f"arm:{rid}:done"), B("❌ বাতিল", callback_data=f"arm:{rid}:no")]]))


# ================================================================ প্রোমো (ইউজার)
def promos_view(uid, page, ctx):
    lang = lg(uid)
    rows = Q("""SELECT s.id, s.name, s.next_ep_at, MAX(p.created_at) AS pa, COUNT(*) AS n FROM promos p JOIN serials s ON s.id=p.serial_id
                WHERE p.status='live' AND s.draft=0 GROUP BY s.id ORDER BY pa DESC""")
    page, pages, off = paginate(len(rows), page, 8)
    kb = []
    for r in rows[off:off + 8]:
        new = "🔴 NEW " if now() - r["pa"] < 86400 else ""
        nx = f" ⏳{fmt_td(r['next_ep_at'] - now())}" if r["next_ep_at"] > now() else ""
        kb.append([B(f"{new}🎬 {r['name'][:22]}{nx}", callback_data=f"pv:{r['id']}")])
    nv = nav_row("pr", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B(L(lang, f"🔔 প্রোমো {'বন্ধ' if user(uid)['nt_promo'] else 'চালু'}", f"🔔 Promo {'off' if user(uid)['nt_promo'] else 'on'}"), callback_data="nft:nt_promo")] + home_row(lang))
    return L(lang, "🎬 <b>প্রোমো</b>\n🔴 = আজকের নতুন • ⏳ = পরের এপিসোডের কাউন্টডাউন", "🎬 <b>Promos</b>\n🔴 = new today • ⏳ = next-episode countdown") + ("" if rows else L(lang, "\n\nএখন কোনো প্রোমো নেই।", "\n\nNo promos right now.")), M(kb)


PAGERS["pr"] = promos_view
PAGERS["ps_"] = promos_view


@route("pv")
async def cb_pv(update, ctx, q, p):
    uid, lang, sid = q.from_user.id, lg(q.from_user.id), int(p[1])
    ps = Q("SELECT p.*, s.name AS sname FROM promos p JOIN serials s ON s.id=p.serial_id WHERE p.serial_id=? AND p.status='live' ORDER BY p.created_at DESC LIMIT 3", sid)
    if not ps:
        return await ans(q, L(lang, "প্রোমো নেই", "No promo"), True)
    u = user(uid)
    prem = is_premium(u) or is_admin(uid)
    sent = 0
    for pr in ps:
        if pr["prem_only"] and not prem:
            continue
        st = await send_promo(ctx.bot, uid, pr, lang, promo_kb(pr, lang), u["promo_spoil"])
        sent += st == "ok"
        X("INSERT OR IGNORE INTO promo_views (promo_id, user_id, at) VALUES (?,?,?)", pr["id"], uid, now())
        X("UPDATE promos SET clicks=clicks+1 WHERE id=?", pr["id"])
    await ans(q, "✅" if sent else L(lang, "🔒 শুধু Premium-এর জন্য", "🔒 Premium only"), not sent)


@route("pvn")
async def cb_pvn(update, ctx, q, p):
    uid, sid, lang = q.from_user.id, int(p[1]), lg(q.from_user.id)
    X("INSERT OR IGNORE INTO promo_notify (user_id, serial_id) VALUES (?,?)", uid, sid)
    await ans(q, L(lang, "🔔 এপিসোড এলে জানানো হবে", "🔔 We'll notify you when it arrives"), True)


# ================================================================ এডমিন: কমন
ADMIN_PFX = {"rv", "rp", "ad", "au", "aul", "aup", "aub", "pay", "ar", "arc", "arx", "atc", "arm", "ae", "aef", "ap_", "adr", "abc", "amg", "amg2", "adel", "adel2",
             "apr", "anx", "abx", "ast", "astt", "asr", "ainc", "aexp", "aset", "atg", "alu", "alog", "atk", "apol", "aap", "aapt", "aaps", "aapx", "pol0",
             "apv", "abt", "wsc", "lkc", "psr", "smc", "ewl", "evc", "evt", "evb", "evl", "evr", "evu", "tsx", "stx", "ahl", "apm", "ap2", "aco", "acd", "ahv"}


def aroute(*p):
    ADMIN_PFX.update(p)
    return route(*p)


def acommand(name):
    return command(name, admin=True)


def apply_undo(u):
    """অ্যাডমিন লগের undo JSON প্রয়োগ"""
    t = u.get("t")
    if t == "set":
        ss(u["k"], u["v"])
    elif t == "ban":
        X("UPDATE users SET banned=?, ban_reason=? WHERE user_id=?", u["b"], u.get("r", ""), u["uid"])
    elif t == "plan":
        X("UPDATE users SET plan_until=? WHERE user_id=?", u["until"], u["uid"])
    elif t == "serial":
        X("UPDATE serials SET " + u["col"] + "=? WHERE id=?", u["v"], u["sid"])
        sdirty()
    elif t == "text":
        save_text(u["k"], u["lang"], u["body"])
    else:
        return False
    return True


# ================================================================ /stats, ড্যাশবোর্ড
def stats_view():
    t = now()
    tot = V("SELECT COUNT(*) FROM users")
    prem = V("SELECT COUNT(*) FROM users WHERE plan_until=-1 OR plan_until>?", t)
    active = V("SELECT COUNT(*) FROM users WHERE last_seen>?", t - 86400)
    new24 = V("SELECT COUNT(*) FROM users WHERE joined_at>?", t - 86400)
    bl, bn = V("SELECT COUNT(*) FROM users WHERE blocked=1"), V("SELECT COUNT(*) FROM users WHERE banned=1")
    ser = V("SELECT COUNT(*) FROM serials WHERE draft=0")
    eps = V("SELECT COUNT(*) FROM eps WHERE status='published'")
    d24 = V("SELECT COUNT(*) FROM deliveries WHERE sent_at>?", t - 86400)
    pend = V("SELECT COUNT(*) FROM payments WHERE status='pending'")
    tk = V("SELECT COUNT(*) FROM tickets WHERE status='open'")
    rm = V("SELECT COUNT(*) FROM removals WHERE status='pending'")
    qn = V("SELECT COUNT(*) FROM queue")
    inc = V("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved' AND decided_at>=?", day_start())
    rep = Q1("SELECT * FROM delivery_reports WHERE dry=0 ORDER BY id DESC LIMIT 1")
    last = f"\n📤 শেষ ডেলিভারি: ✅{rep['ok']} 🕒{rep['queued']} 🔕{rep['blocked']} ⚠️{rep['fail']} • {rep['secs']:.1f}s" if rep else ""
    text = (f"📊 <b>{BOT_NAME} — ড্যাশবোর্ড</b> v{VERSION}\n\n👥 মোট ইউজার: <b>{tot}</b> (🆕 আজ {new24})\n🟢 সক্রিয় (২৪ঘ): {active}\n💎 Premium: <b>{prem}</b> • 🆓 Free: {tot - prem}\n🚫 ব্যান: {bn} • 🔕 ব্লকড: {bl}\n\n"
            f"📺 সিরিয়াল: {ser} • 🎬 এপিসোড: {eps}\n📦 ২৪ঘণ্টায় ডেলিভারি: {d24} • ⏳ কিউ: {qn}\n\n💳 অপেক্ষমাণ পেমেন্ট: <b>{pend}</b> • 🎫 ওপেন টিকেট: <b>{tk}</b> • 🚩 অপসারণ: {rm}\n💰 আজকের আয়: {inc} ৳\n🔒 লক-ক্লিক: {gi('lock_clicks')}{last}")
    kb = [[B("👥 ইউজার তালিকা", callback_data="aul:0"), B("💎 প্ল্যান সক্রিয়", callback_data="ad:pa")],
          [B(f"💳 পেমেন্ট ({pend})", callback_data="pay:l"), B(f"🎫 টিকেট ({tk})", callback_data="atk:0")],
          [B("📢 ব্রডকাস্ট", callback_data="abx:new"), B("📊 পোল", callback_data="apol:new")],
          [B("📈 আয় রিপোর্ট", callback_data="ainc:d"), B("📥 CSV", callback_data="aexp")],
          [B("⚙️ সেটিংস", callback_data="aset:0"), B("▶️ অটোপ্লে", callback_data="ap:a")],
          [B("🔄 রিফ্রেশ", callback_data="ast"), B("♻️ Bot Restart", callback_data="asr")], home_row("bn")]
    return text, M(kb)


@acommand("stats")
async def cmd_stats(update, ctx):
    await reply(update, *stats_view())


@aroute("ast")
async def cb_ast(update, ctx, q, p):
    await show(update, ctx, *stats_view())
    await ans(q, "🔄")


@aroute("asr")
async def cb_asr(update, ctx, q, p):
    if len(p) > 1 and p[1] == "y":
        ss("restart_chat", q.from_user.id)
        alog(q.from_user.id, "restart")
        await show(update, ctx, "♻️ রিস্টার্ট হচ্ছে… কয়েক সেকেন্ড অপেক্ষা করুন।")
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    await show(update, ctx, "♻️ <b>বট রিস্টার্ট করবেন?</b>", M([[B("✅ হ্যাঁ", callback_data="asr:y"), B("❌ না", callback_data="ast")]]))
    await ans(q)


@aroute("ad")
async def cb_ad(update, ctx, q, p):
    uid, act = q.from_user.id, p[1]
    if act == "home":
        await show(update, ctx, *admin_panel())
    elif act == "upd":
        X("UPDATE users SET seen_ver=? WHERE user_id=?", VERSION, uid)
        await show(update, ctx, *updates_view())
    elif act == "vu":
        kb = [[B("🆕 নতুন ইউজার", callback_data="ahv:new"), B("🆓 Free", callback_data="ahv:free")], [B("💎 Premium", callback_data="ahv:prem"), B("🔒 প্ল্যানহীন", callback_data="ahv:lock")], [B("↩️", callback_data="ad:home")]]
        await show(update, ctx, "👁 <b>ইউজার হিসেবে হোম দেখুন</b> (শুধু প্রিভিউ)", M(kb))
    elif act == "sa":
        set_await(ctx, "addfeed")
        await reply(update, ADDFEED_HELP)
    elif act == "pa":
        set_await(ctx, "activate")
        await reply(update, "💎 <b>প্ল্যান সক্রিয়</b>\nফরম্যাট: <code>ইউজারআইডি দিন</code> (দিন = 7/30/90 অথবা life)\nউদাহরণ: <code>123456789 30</code>")
    await ans(q)


@aroute("ahv")
async def cb_ahv(update, ctx, q, p):
    uid, lang = q.from_user.id, lg(q.from_user.id)
    ov = {"new": dict(_new=True, plan_until=0), "free": dict(plan_until=0, onboarded=2), "prem": dict(plan_until=-1, onboarded=2), "lock": dict(plan_until=0, onboarded=2)}[p[1]]
    text, kb, banner, new = home_view(uid, q.from_user.first_name, lang, override=ov)
    rows = [list(r) for r in kb.inline_keyboard] + [[B("↩️ প্যানেল", callback_data="ad:home")]]
    if p[1] == "lock":
        text = "🔒 <b>আপনার কোনো সাবস্ক্রিপশন নেই।</b>\nবট ব্যবহার করতে প্ল্যান কিনুন।"
        rows = [[B("💳 প্ল্যান কিনুন", callback_data="tp"), B("📞 সাপোর্ট", callback_data="su")], [B("↩️ প্যানেল", callback_data="ad:home")]]
    await show(update, ctx, "👁 <i>প্রিভিউ</i>\n\n" + text, M(rows))
    await ans(q)


def admin_panel():
    pend = V("SELECT COUNT(*) FROM payments WHERE status='pending'")
    tk = V("SELECT COUNT(*) FROM tickets WHERE status='open'")
    kb = [[B("📊 স্ট্যাটস", callback_data="ast"), B("👥 ইউজার", callback_data="aul:0")],
          [B("➕ সিরিয়াল যোগ", callback_data="ad:sa"), B("📺 সিরিয়াল", callback_data="ls:0")],
          [B(f"💳 পেমেন্ট ({pend})", callback_data="pay:l"), B(f"🎫 টিকেট ({tk})", callback_data="atk:0")],
          [B("📢 ব্রডকাস্ট", callback_data="abx:new"), B("📊 পোল", callback_data="apol:new")],
          [B("✏️ ওয়েলকাম", callback_data="evt:w"), B("📜 টার্মস", callback_data="evt:t")],
          [B("🎞 /editvideo", callback_data="evb:0"), B("▶️ অটোপ্লে", callback_data="ap:a")],
          [B("⚙️ সেটিংস", callback_data="aset:0"), B("🧪 টেস্ট", callback_data="tsx")],
          [B("🧾 অ্যাকশন লগ", callback_data="alog:0"), B("🔍 সার্চ অ্যানালিটিক্স", callback_data="aap")],
          [B("🔗 লিংক চেক", callback_data="lkc"), B("🅰️🅱️ A/B", callback_data="abt:s")],
          [B("🏠 হোম লেআউট", callback_data="ahl"), B(f"{'🔴 ' if user_seen() else ''}🆕 আপডেট বোর্ড", callback_data="ad:upd")], home_row("bn")]
    return "🛠 <b>এডমিন প্যানেল</b>", M(kb)


def user_seen():
    return False


@acommand("admin")
async def cmd_admin(update, ctx):
    await reply(update, *admin_panel())


# ---- /updates: আপডেট বোর্ড (শুধু এডমিন)
def updates_view():
    lines = [f"🆕 <b>{BOT_NAME} — আপডেট বোর্ড</b>", f"বর্তমান ভার্সন: <b>v{VERSION}</b>"]
    for ver, date, items in CHANGELOG:
        lines.append(f"\n🔹 <b>v{ver}</b> • {date}")
        lines += [f"  {x}" for x in items]
    return "\n".join(lines)[:4000], M([[B("↩️ প্যানেল", callback_data="ad:home")]])


@acommand("updates")
async def cmd_updates(update, ctx):
    X("UPDATE users SET seen_ver=? WHERE user_id=?", VERSION, update.effective_user.id)
    await reply(update, *updates_view())


# ================================================================ ইউজার ম্যানেজমেন্ট
def users_list(uid, page, ctx):
    f = ctx.user_data.get("aq")
    where, args = "", []
    if f:
        where = "WHERE name LIKE ? OR username LIKE ? OR CAST(user_id AS TEXT) LIKE ?"
        args = [f"%{f}%"] * 3
    total = V(f"SELECT COUNT(*) FROM users {where}", *args)
    page, pages, off = paginate(total, page, 10)
    rows = Q(f"SELECT * FROM users {where} ORDER BY last_seen DESC LIMIT 10 OFFSET ?", *args, off)
    kb = [[B(f"{'💎' if is_premium(r) else '🆓'}{'🚫' if r['banned'] else ''} {r['name'][:18] or r['user_id']} • {r['user_id']}", callback_data=f"au:{r['user_id']}")] for r in rows]
    nv = nav_row("aul", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B("🔍 খুঁজুন", callback_data="aul:s"), B("♻️", callback_data="aul:x"), B("↩️", callback_data="ast")])
    return f"👥 <b>ইউজার</b> ({total})" + (f" • 🔍 {esc(f)}" if f else ""), M(kb)


PAGERS["aul"] = users_list


@aroute("aul")
async def cb_aul(update, ctx, q, p):
    if p[1] == "s":
        set_await(ctx, "user_q")
        return await ans(q, "নাম/ইউজারনেম/আইডি লিখুন", True)
    if p[1] == "x":
        ctx.user_data.pop("aq", None)
        p = ["aul", "0"]
    await show(update, ctx, *users_list(q.from_user.id, int(p[1]), ctx))
    await ans(q)


@awaiting("user_q")
async def aw_user_q(update, ctx, payload):
    ctx.user_data["aq"] = update.effective_message.text.strip()
    await reply(update, *users_list(update.effective_user.id, 0, ctx))


def user_card(uid):
    u = user(uid)
    if not u:
        return "❌ ইউজার নেই", M([[B("↩️", callback_data="aul:0")]])
    un = f"@{esc(u['username'])}" if u["username"] else "—"
    pays = V("SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=? AND status='approved'", uid)
    text = (f"👤 <b>{esc(u['name'])}</b> {un}\n🆔 <code>{uid}</code>\n💎 {plan_label(u)}" + (f" • {fmt_ts(u['plan_until'])}" if u["plan_until"] > 0 else "") +
            f"\n📅 জয়েন: {fmt_ts(u['joined_at'])}\n👀 শেষ: {fmt_ago(u['last_seen'])}\n📌 ফলো: {follow_count(uid)} • 🎬 পেয়েছে: {V('SELECT COUNT(*) FROM deliveries WHERE user_id=?', uid)} • ⭐ {V('SELECT COUNT(*) FROM saves WHERE user_id=?', uid)}\n💰 মোট পেমেন্ট: {pays} ৳\n🌐 {u['lang']} • 🎞 {u['q_default']}p"
            + (f"\n🚫 <b>ব্যান</b>: {esc(u['ban_reason'])}" if u["banned"] else "") + ("\n🔕 ব্লক করেছে" if u["blocked"] else "") + ("\n🧪 টেস্টার" if u["is_tester"] else "") + ("\n✅ হোয়াইটলিস্ট" if u["whitelist"] else ""))
    kb = [[B(f"+{d}d", callback_data=f"aup:{uid}:{d}") for d in (7, 30, 90)] + [B("♾", callback_data=f"aup:{uid}:life"), B("🆓 Free", callback_data=f"aup:{uid}:free")],
          [B(f"🥈 Basic +{d}d", callback_data=f"aup:{uid}:b{d}") for d in (7, 30, 90)],
          [B("🔓 আনব্যান" if u["banned"] else "🚫 ব্যান", callback_data=f"aub:{uid}"), B("✉️ মেসেজ", callback_data=f"aub:{uid}:m")],
          [B("🧪 টেস্টার " + ("✖️" if u["is_tester"] else "➕"), callback_data=f"aub:{uid}:t"), B("✅ হোয়াইটলিস্ট " + ("✖️" if u["whitelist"] else "➕"), callback_data=f"aub:{uid}:w")],
          [B("⭐ সেভ", callback_data=f"aub:{uid}:s"), B("🕘 ইতিহাস", callback_data=f"aub:{uid}:h"), B("📌 ফলো", callback_data=f"aub:{uid}:f")],
          [B("↩️", callback_data="aul:0")]]
    return text, M(kb)


@aroute("au")
async def cb_au(update, ctx, q, p):
    await show(update, ctx, *user_card(int(p[1])))
    await ans(q)


async def notify_plan(ctx, uid, until):
    lang = lg(uid)
    body = L(lang, f"🎉 <b>আপনার 💎 Premium সক্রিয় হয়েছে!</b>\n⏳ মেয়াদ: <b>{'Lifetime' if until == -1 else fmt_ts(until)}</b>\n\nউপভোগ করুন!", f"🎉 <b>Your 💎 Premium is active!</b>\n⏳ Valid until: <b>{'Lifetime' if until == -1 else fmt_ts(until)}</b>")
    await tsend(ctx.bot, uid, body, M([home_row(lang)]))
    nlog(uid, "plan", "💎 Premium সক্রিয়")


@aroute("aup")
async def cb_aup(update, ctx, q, p):
    aid, uid, spec = q.from_user.id, int(p[1]), p[2]
    old = user(uid)["plan_until"]
    basic = spec.startswith("b")
    spec = spec[1:] if basic else spec
    until = set_plan(uid, spec if spec in ("free", "life") else int(spec), aid, "admin", "basic" if basic else "pro")
    alog(aid, "plan", uid, spec, undo={"t": "plan", "uid": uid, "until": old})
    if spec == "free":
        await tsend(ctx.bot, uid, L(lg(uid), "ℹ️ আপনার প্ল্যান Free করা হয়েছে।", "ℹ️ Your plan was set to Free."))
    else:
        await notify_plan(Ctx(ctx.application), uid, until)
    await show(update, ctx, *user_card(uid))
    await ans(q, "✅")


@aroute("aub")
async def cb_aub(update, ctx, q, p):
    aid, uid = q.from_user.id, int(p[1])
    act = p[2] if len(p) > 2 else "b"
    u = user(uid)
    if act == "b":
        if u["banned"]:
            X("UPDATE users SET banned=0, ban_reason='' WHERE user_id=?", uid)
            alog(aid, "unban", uid, undo={"t": "ban", "uid": uid, "b": 1, "r": u["ban_reason"]})
            await tsend(ctx.bot, uid, "✅ আপনার ব্যান তুলে নেওয়া হয়েছে।")
        else:
            set_await(ctx, "ban_reason", uid)
            return await reply(update, f"🚫 <code>{uid}</code>-কে ব্যান করার কারণ লিখুন (বা <code>-</code>)।")
    elif act == "m":
        set_await(ctx, "dm", uid)
        return await reply(update, f"✉️ <code>{uid}</code>-কে পাঠানোর মেসেজ লিখুন।")
    elif act == "t":
        X("UPDATE users SET is_tester=1-is_tester WHERE user_id=?", uid)
    elif act == "w":
        X("UPDATE users SET whitelist=1-whitelist WHERE user_id=?", uid)
    elif act == "s":
        return await admin_saved_of(update, ctx, uid)
    elif act == "h":
        return await admin_history_of(update, ctx, uid)
    elif act == "f":
        rows = Q("SELECT s.name FROM follows f JOIN serials s ON s.id=f.serial_id WHERE f.user_id=? ORDER BY s.name", uid)
        return await reply(update, f"📌 <b>{uid}-এর ফলো</b> ({len(rows)})\n" + "\n".join(f"• {esc(r['name'])}" for r in rows[:60]))
    await show(update, ctx, *user_card(uid))
    await ans(q, "✅")


@awaiting("ban_reason")
async def aw_ban_reason(update, ctx, uid):
    r = (update.effective_message.text or "").strip()
    r = "" if r == "-" else r[:200]
    u = user(uid)
    X("UPDATE users SET banned=1, ban_reason=? WHERE user_id=?", r, uid)
    alog(update.effective_user.id, "ban", uid, r, undo={"t": "ban", "uid": uid, "b": 0, "r": ""})
    await tsend(ctx.bot, uid, f"🚫 আপনাকে ব্যান করা হয়েছে।" + (f"\n📝 কারণ: {esc(r)}" if r else "") + (f"\n📞 আপিল: {ADMIN_CONTACT}" if ADMIN_CONTACT else ""))
    await reply(update, *user_card(uid))


@awaiting("dm")
async def aw_dm(update, ctx, uid):
    st = await tsend(ctx.bot, uid, "✉️ <b>এডমিনের বার্তা</b>\n\n" + esc(update.effective_message.text or ""))
    await reply(update, "✅ পাঠানো হয়েছে" if st == "ok" else "⚠️ পাঠানো যায়নি")


@awaiting("activate")
async def aw_activate(update, ctx, payload):
    m = re.match(r"^\s*(\d+)\s+(\d+|life)\s*$", update.effective_message.text or "")
    if not m or not user(int(m.group(1))):
        return await reply(update, "❌ ফরম্যাট ঠিক নয় বা ইউজার নেই। উদাহরণ: <code>123456789 30</code>")
    uid, spec = int(m.group(1)), m.group(2)
    old = user(uid)["plan_until"]
    until = set_plan(uid, "life" if spec == "life" else int(spec), update.effective_user.id, "admin")
    alog(update.effective_user.id, "plan", uid, spec, undo={"t": "plan", "uid": uid, "until": old})
    await notify_plan(Ctx(ctx.application), uid, until)
    await reply(update, f"✅ {uid} → {plan_label(user(uid))}")


async def _plan_cmd(update, ctx, remove):
    a = ctx.args
    if remove and len(a) >= 1 and a[0].isdigit() or (not remove and len(a) >= 2 and a[0].isdigit()):
        uid = int(a[0])
        if not user(uid):
            return await reply(update, "❌ ইউজার নেই")
        old = user(uid)["plan_until"]
        if remove:
            set_plan(uid, "free", update.effective_user.id, "removeprem")
            await tsend(ctx.bot, uid, "ℹ️ আপনার Premium বাতিল করা হয়েছে।")
            alog(update.effective_user.id, "removeprem", uid, undo={"t": "plan", "uid": uid, "until": old})
            return await reply(update, f"✅ {uid} → Free")
        spec = "life" if a[1].lower() == "life" else (int(a[1]) if a[1].isdigit() else None)
        if spec is None:
            return await reply(update, "❌ দিন সংখ্যা বা life")
        until = set_plan(uid, spec, update.effective_user.id, "giveprem")
        alog(update.effective_user.id, "giveprem", uid, a[1], undo={"t": "plan", "uid": uid, "until": old})
        await notify_plan(Ctx(ctx.application), uid, until)
        return await reply(update, f"✅ {uid} → {plan_label(user(uid))}")
    await reply(update, "ব্যবহার: <code>/removeprem আইডি</code>" if remove else "ব্যবহার: <code>/giveprem আইডি দিন</code> (দিন = সংখ্যা বা life)")


@acommand("giveprem")
async def cmd_giveprem(update, ctx):
    await _plan_cmd(update, ctx, False)


@acommand("removeprem")
async def cmd_removeprem(update, ctx):
    await _plan_cmd(update, ctx, True)


@acommand("ban")
async def cmd_ban(update, ctx):
    if not ctx.args or not ctx.args[0].isdigit() or not user(int(ctx.args[0])):
        return await reply(update, "ব্যবহার: <code>/ban আইডি [কারণ]</code>")
    uid, r = int(ctx.args[0]), " ".join(ctx.args[1:])[:200]
    X("UPDATE users SET banned=1, ban_reason=? WHERE user_id=?", r, uid)
    alog(update.effective_user.id, "ban", uid, r, undo={"t": "ban", "uid": uid, "b": 0, "r": ""})
    await reply(update, f"🚫 {uid} ব্যান হয়েছে")


@acommand("unban")
async def cmd_unban(update, ctx):
    if not ctx.args or not ctx.args[0].isdigit():
        return await reply(update, "ব্যবহার: <code>/unban আইডি</code>")
    X("UPDATE users SET banned=0, ban_reason='' WHERE user_id=?", int(ctx.args[0]))
    alog(update.effective_user.id, "unban", ctx.args[0])
    await reply(update, "✅ আনব্যান")


@acommand("users")
async def cmd_users(update, ctx):
    ctx.user_data.pop("aq", None)
    await reply(update, *users_list(update.effective_user.id, 0, ctx))


# ================================================================ পেমেন্ট (এডমিন)
@aroute("pay")
async def cb_pay(update, ctx, q, p):
    aid, act = q.from_user.id, p[1]
    if act == "l":
        rows = Q("SELECT * FROM payments WHERE status='pending' ORDER BY created_at LIMIT 15")
        kb = [[B(f"#{r['id']} • {r['user_id']} • {r['amount']}৳ • {r['method']}", callback_data=f"pay:v:{r['id']}")] for r in rows] + [[B("↩️", callback_data="ast")]]
        return await show(update, ctx, f"💳 <b>অপেক্ষমাণ পেমেন্ট</b> ({len(rows)})", M(kb)) or await ans(q)
    pid = int(p[2])
    r = Q1("SELECT * FROM payments WHERE id=?", pid)
    if not r:
        return await ans(q, "❌", True)
    if act == "v":
        await ans(q)
        return await send_pay_card(Ctx(ctx.application), pid)
    if r["status"] != "pending":
        return await ans(q, f"আগেই {r['status']}", True)
    if act == "ok":
        basic = r["plan"].startswith("b")
        pl = r["plan"][1:] if basic else r["plan"]
        spec = "life" if pl == "life" else int(pl)
        old = user(r["user_id"])["plan_until"]
        until = set_plan(r["user_id"], spec, aid, f"payment#{pid}", "basic" if basic else "pro")
        X("UPDATE payments SET status='approved', decided_at=?, decided_by=? WHERE id=?", now(), aid, pid)
        alog(aid, "pay_ok", pid, f"{r['user_id']} {r['amount']}", undo={"t": "plan", "uid": r["user_id"], "until": old})
        await notify_plan(Ctx(ctx.application), r["user_id"], until)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        await ans(q, "✅ অনুমোদিত")
        await reply(update, f"✅ পেমেন্ট #{pid} অনুমোদিত → {r['user_id']}")
    elif act == "no":
        kb = [[B(x, callback_data=f"pay:r:{pid}:{i}")] for i, x in enumerate(("TrxID ভুল", "টাকা পাওয়া যায়নি", "পরিমাণ কম", "ডুপ্লিকেট"))] + [[B("📝 অন্য কারণ", callback_data=f"pay:r:{pid}:x")]]
        await reply(update, f"❌ #{pid} বাতিলের কারণ:", M(kb))
        await ans(q)
    elif act == "r":
        why = ("TrxID ভুল", "টাকা পাওয়া যায়নি", "পরিমাণ কম", "ডুপ্লিকেট")[int(p[3])] if p[3] != "x" else None
        if why is None:
            set_await(ctx, "pay_reason", pid)
            return await ans(q, "কারণ লিখে পাঠান", True)
        await reject_payment(ctx, pid, aid, why)
        await ans(q, "❌")


async def reject_payment(ctx, pid, aid, why):
    r = Q1("SELECT * FROM payments WHERE id=?", pid)
    X("UPDATE payments SET status='rejected', decided_at=?, decided_by=?, reason=? WHERE id=?", now(), aid, why, pid)
    alog(aid, "pay_no", pid, why)
    lang = lg(r["user_id"])
    await tsend(ctx.bot, r["user_id"], L(lang, f"❌ <b>পেমেন্ট #{pid} গ্রহণ করা হয়নি</b>\n📝 কারণ: {esc(why)}\n\nসঠিক তথ্য দিয়ে আবার চেষ্টা করুন বা সাপোর্টে জানান।", f"❌ <b>Payment #{pid} rejected</b>\n📝 Reason: {esc(why)}\n\nTry again or contact support."),
                M([[B(L(lang, "💳 আবার চেষ্টা", "💳 Try again"), callback_data="tp"), B(L(lang, "📞 সাপোর্ট", "📞 Support"), callback_data="su")]]))


@awaiting("pay_reason")
async def aw_pay_reason(update, ctx, pid):
    await reject_payment(Ctx(ctx.application), pid, update.effective_user.id, (update.effective_message.text or "")[:200])
    await reply(update, f"❌ #{pid} বাতিল")


@acommand("payments")
async def cmd_payments(update, ctx):
    rows = Q("SELECT * FROM payments WHERE status='pending' ORDER BY created_at LIMIT 15")
    kb = [[B(f"#{r['id']} • {r['user_id']} • {r['amount']}৳ • {r['method']}", callback_data=f"pay:v:{r['id']}")] for r in rows]
    await reply(update, f"💳 <b>অপেক্ষমাণ পেমেন্ট</b> ({len(rows)})", M(kb) if kb else None)


@aroute("ainc")
async def cb_ainc(update, ctx, q, p):
    mode = p[1]
    if mode == "d":
        rows = Q("SELECT strftime('%Y-%m-%d', decided_at+?, 'unixepoch') AS k, COUNT(*) AS n, SUM(amount) AS s FROM payments WHERE status='approved' GROUP BY k ORDER BY k DESC LIMIT 14", int(TZ.utcoffset(None).total_seconds()))
        t = "📈 <b>দৈনিক আয়</b>"
    elif mode == "m":
        rows = Q("SELECT strftime('%Y-%m', decided_at+?, 'unixepoch') AS k, COUNT(*) AS n, SUM(amount) AS s FROM payments WHERE status='approved' GROUP BY k ORDER BY k DESC LIMIT 12", int(TZ.utcoffset(None).total_seconds()))
        t = "📈 <b>মাসিক আয়</b>"
    else:
        rows = Q("SELECT method AS k, COUNT(*) AS n, SUM(amount) AS s FROM payments WHERE status='approved' GROUP BY method ORDER BY s DESC")
        t = "📈 <b>মেথড অনুযায়ী আয়</b>"
    tot = V("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'")
    body = "\n".join(f"• {esc(r['k'])}: <b>{r['s']} ৳</b> ({r['n']}টি)" for r in rows) or "ডেটা নেই"
    kb = [[B("দৈনিক", callback_data="ainc:d"), B("মাসিক", callback_data="ainc:m"), B("মেথড", callback_data="ainc:p")], [B("↩️", callback_data="ast")]]
    await show(update, ctx, f"{t}\n\n{body}\n\n💰 মোট: <b>{tot} ৳</b>", M(kb))
    await ans(q)


@acommand("income")
async def cmd_income(update, ctx):
    q = update.callback_query
    t = V("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'")
    await reply(update, f"💰 মোট আয়: <b>{t} ৳</b>", M([[B("📈 রিপোর্ট", callback_data="ainc:d")]]))


@aroute("aexp")
async def cb_aexp(update, ctx, q, p):
    if len(p) < 2:
        kb = [[B("💳 পেমেন্ট", callback_data="aexp:pay"), B("👥 ইউজার", callback_data="aexp:usr")], [B("📌 ফলো", callback_data="aexp:fol"), B("🧾 লগ", callback_data="aexp:log")], [B("↩️", callback_data="ast")]]
        await show(update, ctx, "📥 <b>CSV এক্সপোর্ট</b>", M(kb))
        return await ans(q)
    import csv
    sql = {"pay": "SELECT * FROM payments ORDER BY id", "usr": "SELECT user_id,name,username,plan_until,joined_at,last_seen,banned,lang FROM users", "fol": "SELECT f.user_id, s.name FROM follows f JOIN serials s ON s.id=f.serial_id", "log": "SELECT * FROM admin_log ORDER BY id"}[p[1]]
    rows = Q(sql)
    sio = io.StringIO()
    w = csv.writer(sio)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow(list(r))
    bio = io.BytesIO(("﻿" + sio.getvalue()).encode())
    bio.name = f"{p[1]}_{local().strftime('%Y%m%d')}.csv"
    await guarded(lambda: ctx.bot.send_document(q.from_user.id, bio, caption=f"📥 {len(rows)} rows"))
    alog(q.from_user.id, "export", p[1])
    await ans(q, "✅")


# ================================================================ টিকেট (এডমিন)
def tickets_admin(uid, page, ctx):
    total = V("SELECT COUNT(*) FROM tickets WHERE status<>'closed'")
    page, pages, off = paginate(total, page, 8)
    rows = Q("SELECT * FROM tickets WHERE status<>'closed' ORDER BY updated_at DESC LIMIT 8 OFFSET ?", off)
    kb = [[B(f"{TK_ST.get(r['status'], '')} #{r['id']} {r['subject'][:28]}", callback_data=f"ar:{r['id']}:v")] for r in rows]
    nv = nav_row("atk", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B("📋 ক্যানড উত্তর", callback_data="aco"), B("↩️", callback_data="ast")])
    return f"🎫 <b>ওপেন টিকেট</b> ({total})", M(kb)


PAGERS["atk"] = tickets_admin


@aroute("atk")
async def cb_atk(update, ctx, q, p):
    await show(update, ctx, *tickets_admin(q.from_user.id, int(p[1]), ctx))
    await ans(q)


@acommand("tickets")
async def cmd_tickets(update, ctx):
    await reply(update, *tickets_admin(update.effective_user.id, 0, ctx))


@aroute("ar")
async def cb_ar(update, ctx, q, p):
    tid = int(p[1])
    t = Q1("SELECT * FROM tickets WHERE id=?", tid)
    if not t:
        return await ans(q, "❌", True)
    if len(p) > 2 and p[2] == "v":
        msgs = Q("SELECT * FROM ticket_msgs WHERE ticket_id=? ORDER BY id DESC LIMIT 6", tid)[::-1]
        txt = f"🎫 <b>#{tid}</b> • <code>{t['user_id']}</code>\n\n" + "\n\n".join(f"{'🛠' if m['from_admin'] else '👤'} {fmt_ts(m['at'])}\n{esc(m['body'])}" for m in msgs)
        await reply(update, txt, M([[B("💬 উত্তর", callback_data=f"ar:{tid}"), B("📋 ক্যানড", callback_data=f"arc:{tid}"), B("⚫ বন্ধ", callback_data=f"atc:{tid}")]]))
        return await ans(q)
    set_await(ctx, "t_reply", tid)
    await reply(update, f"💬 টিকেট #{tid}-এর উত্তর লিখুন।")
    await ans(q)


async def send_ticket_reply(ctx, tid, body):
    t = Q1("SELECT * FROM tickets WHERE id=?", tid)
    X("INSERT INTO ticket_msgs (ticket_id, from_admin, body, at) VALUES (?,1,?,?)", tid, body[:2000], now())
    X("UPDATE tickets SET status='answered', updated_at=? WHERE id=?", now(), tid)
    lang = lg(t["user_id"])
    await tsend(ctx.bot, t["user_id"], L(lang, f"💬 <b>টিকেট #{tid}-এ উত্তর</b>\n\n{esc(body)}", f"💬 <b>Reply to ticket #{tid}</b>\n\n{esc(body)}"),
                M([[B(L(lang, "💬 উত্তর", "💬 Reply"), callback_data=f"tku:{tid}"), B("🎫", callback_data=f"tkv:{tid}")]]))
    nlog(t["user_id"], "ticket", f"🎫 #{tid} উত্তর")


@awaiting("t_reply")
async def aw_t_reply(update, ctx, tid):
    await send_ticket_reply(Ctx(ctx.application), tid, update.effective_message.text or "")
    await reply(update, "✅ পাঠানো হয়েছে")


@aroute("arc")
async def cb_arc(update, ctx, q, p):
    rows = Q("SELECT * FROM canned ORDER BY id")
    kb = [[B(r["title"][:30], callback_data=f"arx:{p[1]}:{r['id']}")] for r in rows] + [[B("➕ নতুন", callback_data="acd:new")]]
    await reply(update, "📋 <b>রেডিমেড উত্তর</b>" + ("" if rows else "\nখালি — ➕ দিয়ে যোগ করুন।"), M(kb))
    await ans(q)


@aroute("arx")
async def cb_arx(update, ctx, q, p):
    c = Q1("SELECT * FROM canned WHERE id=?", int(p[2]))
    if c:
        await send_ticket_reply(Ctx(ctx.application), int(p[1]), c["body"])
    await ans(q, "✅ পাঠানো হয়েছে")


@aroute("aco", "acd")
async def cb_aco(update, ctx, q, p):
    if p[0] == "acd":
        if p[1] == "new":
            set_await(ctx, "canned_new")
            await reply(update, "📋 ফরম্যাট:\n<code>শিরোনাম | উত্তরের টেক্সট</code>")
        else:
            X("DELETE FROM canned WHERE id=?", int(p[1]))
            await reply(update, "🗑")
        return await ans(q)
    rows = Q("SELECT * FROM canned ORDER BY id")
    kb = [[B(r["title"][:24], callback_data="noop"), B("🗑", callback_data=f"acd:{r['id']}")] for r in rows] + [[B("➕ নতুন", callback_data="acd:new"), B("↩️", callback_data="atk:0")]]
    await show(update, ctx, "📋 <b>রেডিমেড উত্তর</b>", M(kb))
    await ans(q)


@awaiting("canned_new")
async def aw_canned_new(update, ctx, payload):
    parts = (update.effective_message.text or "").split("|", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return await reply(update, "❌ ফরম্যাট: <code>শিরোনাম | টেক্সট</code>")
    X("INSERT INTO canned (title, body) VALUES (?,?)", parts[0].strip()[:40], parts[1].strip()[:1500])
    await reply(update, "✅ যোগ হয়েছে")


@acommand("canned")
async def cmd_canned(update, ctx):
    rows = Q("SELECT * FROM canned ORDER BY id")
    kb = [[B(r["title"][:24], callback_data="noop"), B("🗑", callback_data=f"acd:{r['id']}")] for r in rows] + [[B("➕ নতুন", callback_data="acd:new")]]
    await reply(update, "📋 <b>রেডিমেড উত্তর</b>", M(kb))


@aroute("atc")
async def cb_atc(update, ctx, q, p):
    tid = int(p[1])
    t = Q1("SELECT * FROM tickets WHERE id=?", tid)
    X("UPDATE tickets SET status='closed', updated_at=? WHERE id=?", now(), tid)
    if t:
        await tsend(ctx.bot, t["user_id"], f"⚫ আপনার টিকেট #{tid} বন্ধ করা হয়েছে।")
    await ans(q, "⚫")


@aroute("arm")
async def cb_arm(update, ctx, q, p):
    rid, act = int(p[1]), p[2]
    r = Q1("SELECT * FROM removals WHERE id=?", rid)
    if r:
        X("UPDATE removals SET status=?, decided_at=? WHERE id=?", "done" if act == "done" else "rejected", now(), rid)
        await tsend(ctx.bot, r["user_id"], f"{'✅ আপনার অপসারণ অনুরোধ নিষ্পন্ন হয়েছে।' if act == 'done' else 'ℹ️ আপনার অপসারণ অনুরোধ গ্রহণ করা হয়নি।'} (#{rid})")
    await ans(q, "✅")


@acommand("removals")
async def cmd_removals(update, ctx):
    rows = Q("SELECT * FROM removals WHERE status='pending' ORDER BY id LIMIT 10")
    if not rows:
        return await reply(update, "✅ কোনো অপেক্ষমাণ অনুরোধ নেই।")
    for r in rows:
        await reply(update, f"🚩 <b>#{r['id']}</b> • <code>{r['user_id']}</code>\n{esc(r['reason'])}", M([[B("✅ সম্পন্ন", callback_data=f"arm:{r['id']}:done"), B("❌ বাতিল", callback_data=f"arm:{r['id']}:no")]]))


# ================================================================ /addfeed ও সিরিয়াল ম্যানেজমেন্ট
ADDFEED_HELP = ("➕ <b>সিরিয়াল যোগ (AddFeed)</b>\n\nফরম্যাট (প্রতি লাইনে একটি):\n<code>সিরিয়ালের নাম | প্ল্যাটফর্ম | অন্য নাম১, অন্য নাম২</code>\n\n"
                "উদাহরণ:\n<code>কৃষ্ণকলি | Zee Bangla | Krishnakoli\nআনন্দী | Star Jalsha</code>\n\n"
                "প্ল্যাটফর্ম ও অন্য নাম ঐচ্ছিক। লগ চ্যানেলে ভিডিওর ক্যাপশন/ফাইলনেমে এই নাম থাকলেই বট চিনে নেবে।\n/cancel দিয়ে বাতিল।")


def platform_of(s):
    s = norm(s)
    for p in PLATFORMS:
        if norm(p) == s or s and (s in norm(p)):
            return p
    return ""


def create_serial(name, channel="", aliases=None, draft=0):
    key = norm(name)
    if not key:
        return None, "empty"
    if Q1("SELECT id FROM serials WHERE name_key=?", key):
        return Q1("SELECT id FROM serials WHERE name_key=?", key)["id"], "exists"
    X("INSERT INTO serials (name, name_key, roman, channel, created_at, draft) VALUES (?,?,?,?,?,?)", name.strip(), key, romanize(name), channel, now(), draft)
    sid = V("SELECT last_insert_rowid()")
    for a in aliases or []:
        add_alias(sid, a)
    sdirty()
    return sid, "new"


def add_alias(sid, a):
    k = norm(a)
    if k:
        X("INSERT OR IGNORE INTO serial_alias (serial_id, alias_key, alias, roman) VALUES (?,?,?,?)", sid, k, a.strip(), romanize(a))
        sdirty()


async def do_addfeed(update, ctx, text):
    out, n_new = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("|")]
        name = parts[0]
        ch = platform_of(parts[1]) if len(parts) > 1 and parts[1] else ""
        als = split_csv(parts[2]) if len(parts) > 2 else []
        sid, st = create_serial(name, ch, als)
        if st == "new":
            n_new += 1
            out.append(f"✅ {esc(name)}" + (f" • {ch}" if ch else ""))
            alog(update.effective_user.id, "addfeed", name)
        elif st == "exists":
            out.append(f"♻️ {esc(name)} (আগে থেকেই আছে)")
    if n_new:
        subs = V("SELECT COUNT(*) FROM users WHERE notify=1 AND nt_new=1 AND banned=0 AND blocked=0")
        out.append(f"\n🔔 নতুন সিরিয়ালের খবর {subs} জনকে জানাতে চান?")
        await reply(update, "\n".join(out), M([[B("📣 জানান", callback_data="abx:newser"), B("না", callback_data="noop")]]))
    else:
        await reply(update, "\n".join(out) or "❌ কিছু পাওয়া যায়নি")


@acommand("addfeed")
async def cmd_addfeed(update, ctx):
    txt = update.effective_message.text.partition(" ")[2].strip()
    if not txt:
        set_await(ctx, "addfeed")
        return await reply(update, ADDFEED_HELP)
    await do_addfeed(update, ctx, txt)


@awaiting("addfeed")
async def aw_addfeed(update, ctx, payload):
    await do_addfeed(update, ctx, update.effective_message.text or "")


@acommand("alias")
async def cmd_alias(update, ctx):
    if len(ctx.args) < 2 or not ctx.args[0].isdigit():
        return await reply(update, "ব্যবহার: <code>/alias সিরিয়াল_আইডি অন্য নাম</code>")
    add_alias(int(ctx.args[0]), " ".join(ctx.args[1:]))
    await reply(update, "✅ অ্যালিয়াস যোগ হয়েছে")


EDIT_FIELDS = {"name": "নাম", "channel": "প্ল্যাটফর্ম", "status": "স্ট্যাটাস (on/end/brk)", "poster": "পোস্টার (ছবি পাঠান)", "synopsis": "সারসংক্ষেপ",
               "air_time": "সম্প্রচার সময় (যেমন: রাত ৮টা)", "actors": "অভিনেতা (কমা দিয়ে)", "genre": "ধরন/জনরা", "qmask": "কোয়ালিটি (যেমন 480,720)", "alias": "অন্য নাম যোগ"}


@aroute("ae")
async def cb_ae(update, ctx, q, p):
    sid = int(p[1])
    s = Q1("SELECT * FROM serials WHERE id=?", sid)
    if not s:
        return await ans(q, "❌", True)
    kb = [[B(v.split(" (")[0], callback_data=f"aef:{sid}:{k}")] for k, v in EDIT_FIELDS.items()]
    kb = [kb[i] + (kb[i + 1] if i + 1 < len(kb) else []) for i in range(0, len(kb), 2)]
    kb.append([B("↩️", callback_data=f"sp:{sid}")])
    als = [r["alias"] for r in Q("SELECT alias FROM serial_alias WHERE serial_id=?", sid)]
    await show(update, ctx, f"✏️ <b>{esc(s['name'])}</b> (#{sid})\n📡 {esc(s['channel']) or '—'} • {s['status']} • {s['qmask']}\n🏷 {esc(', '.join(als)) or '—'}", M(kb))
    await ans(q)


@aroute("aef")
async def cb_aef(update, ctx, q, p):
    sid, f = int(p[1]), p[2]
    if f == "channel":
        kb = [[B(x, callback_data=f"aef2:{sid}:channel:{i}")] for i, x in enumerate(PLATFORMS)] + [[B("— খালি —", callback_data=f"aef2:{sid}:channel:-1")]]
        await show(update, ctx, "📡 প্ল্যাটফর্ম বেছে নিন", M(kb))
        return await ans(q)
    set_await(ctx, "edit_field", (sid, f))
    await reply(update, f"✏️ <b>{EDIT_FIELDS[f]}</b> লিখে পাঠান (মুছতে <code>-</code>)।")
    await ans(q)


ADMIN_PFX.add("aef2")


@route("aef2")
async def cb_aef2(update, ctx, q, p):
    sid, i = int(p[1]), int(p[3])
    old = V("SELECT channel FROM serials WHERE id=?", sid, d="")
    X("UPDATE serials SET channel=? WHERE id=?", PLATFORMS[i] if i >= 0 else "", sid)
    alog(q.from_user.id, "serial_edit", sid, "channel", undo={"t": "serial", "sid": sid, "col": "channel", "v": old})
    sdirty()
    await send_serial(update, ctx, sid)
    await ans(q, "✅")


@awaiting("edit_field")
async def aw_edit_field(update, ctx, payload):
    sid, f = payload
    m = update.effective_message
    val = (m.text or "").strip()
    if f == "poster":
        val = m.photo[-1].file_id if m.photo else ("" if val == "-" else "")
    elif val == "-":
        val = ""
    if f == "alias":
        for a in split_csv(val):
            add_alias(sid, a)
    elif f == "name":
        if not val or Q1("SELECT id FROM serials WHERE name_key=? AND id<>?", norm(val), sid):
            return await reply(update, "❌ নাম খালি বা আগে থেকেই আছে")
        X("UPDATE serials SET name=?, name_key=?, roman=? WHERE id=?", val, norm(val), romanize(val), sid)
    elif f == "status":
        if val not in ST_ICON:
            return await reply(update, "❌ on / end / brk")
        X("UPDATE serials SET status=? WHERE id=?", val, sid)
    elif f == "qmask":
        qs = sorted(qset(val))
        X("UPDATE serials SET qmask=? WHERE id=?", ",".join(map(str, qs)), sid)
    elif f in ("channel", "synopsis", "air_time", "actors", "genre", "poster"):
        old = V(f"SELECT {f} FROM serials WHERE id=?", sid, d="")
        X(f"UPDATE serials SET {f}=? WHERE id=?", platform_of(val) if f == "channel" else val[:600], sid)
        alog(update.effective_user.id, "serial_edit", sid, f, undo={"t": "serial", "sid": sid, "col": f, "v": old})
    sdirty()
    await reply(update, "✅ সংরক্ষিত", M([[B("📺 সিরিয়াল পেজ", callback_data=f"sp:{sid}"), B("✏️ আরও", callback_data=f"ae:{sid}")]]))


@aroute("ap_")
async def cb_ap_(update, ctx, q, p):
    sid = int(p[1])
    X("UPDATE serials SET pinned=1-pinned WHERE id=?", sid)
    sdirty()
    await send_serial(update, ctx, sid)
    await ans(q, "📌")


@aroute("adr")
async def cb_adr(update, ctx, q, p):
    sid = int(p[1])
    X("UPDATE serials SET draft=1-draft WHERE id=?", sid)
    sdirty()
    await send_serial(update, ctx, sid)
    await ans(q, "📝↔📤")


@aroute("adel")
async def cb_adel(update, ctx, q, p):
    sid = int(p[1])
    s = Q1("SELECT name FROM serials WHERE id=?", sid)
    if not s:
        return await ans(q, "❌", True)
    n = V("SELECT COUNT(*) FROM follows WHERE serial_id=?", sid)
    await show(update, ctx, f"🗑 <b>{esc(s['name'])}</b> মুছবেন?\n👥 {n} জন ফলোয়ার, এপিসোডসহ সব ডেটা যাবে।", M([[B("✅ হ্যাঁ, মুছুন", callback_data=f"adel2:{sid}"), B("❌ না", callback_data=f"sp:{sid}")]]))
    await ans(q)


@aroute("adel2")
async def cb_adel2(update, ctx, q, p):
    sid = int(p[1])
    nm = V("SELECT name FROM serials WHERE id=?", sid, d="")
    for t in ("follows", "serial_alias", "favs", "promos"):
        X(f"DELETE FROM {t} WHERE serial_id=?", sid)
    X("DELETE FROM ep_files WHERE episode_id IN (SELECT id FROM eps WHERE serial_id=?)", sid)
    X("DELETE FROM eps WHERE serial_id=?", sid)
    X("DELETE FROM serials WHERE id=?", sid)
    sdirty()
    alog(q.from_user.id, "serial_delete", nm)
    await show(update, ctx, f"🗑 {esc(nm)} মুছে ফেলা হয়েছে।", M([[B("↩️", callback_data="ls:0")]]))
    await ans(q)


@aroute("amg")
async def cb_amg(update, ctx, q, p):
    set_await(ctx, "merge", int(p[1]))
    await reply(update, "🔗 <b>মার্জ</b>: যে সিরিয়ালে মিশাতে চান তার নাম লিখুন (এই সিরিয়ালের সব ফলোয়ার/এপিসোড সেখানে যাবে)।")
    await ans(q)


@awaiting("merge")
async def aw_merge(update, ctx, src):
    res = [s for _, s in search_serials(update.effective_message.text or "", include_draft=True)[:6] if s != src]
    if not res:
        return await reply(update, "❌ পাওয়া যায়নি")
    kb = [[B(Q1("SELECT name FROM serials WHERE id=?", s)["name"][:30], callback_data=f"amg2:{src}:{s}")] for s in res]
    await reply(update, "কোনটিতে মার্জ করবেন?", M(kb))


ADMIN_PFX.add("amg2")


@route("amg2")
async def cb_amg2(update, ctx, q, p):
    src, dst = int(p[1]), int(p[2])
    n1 = V("SELECT name FROM serials WHERE id=?", src, d="")
    X("INSERT OR IGNORE INTO follows (user_id, serial_id, created_at, mute, pause_until, q) SELECT user_id, ?, created_at, mute, pause_until, q FROM follows WHERE serial_id=?", dst, src)
    X("DELETE FROM follows WHERE serial_id=?", src)
    X("UPDATE eps SET serial_id=? WHERE serial_id=?", dst, src)
    X("INSERT OR IGNORE INTO serial_alias (serial_id, alias_key, alias, roman) VALUES (?,?,?,?)", dst, norm(n1), n1, romanize(n1))
    X("UPDATE serial_alias SET serial_id=? WHERE serial_id=?", dst, src)
    X("DELETE FROM serials WHERE id=?", src)
    sdirty()
    alog(q.from_user.id, "merge", f"{n1}->{dst}")
    await show(update, ctx, "✅ মার্জ সম্পন্ন")
    await send_serial(update, ctx, dst)


@aroute("abc")
async def cb_abc(update, ctx, q, p):
    set_await(ctx, "bc_content", ("serial", int(p[1])))
    await reply(update, "📢 এই সিরিয়ালের ফলোয়ারদের জন্য মেসেজ (টেক্সট/ছবি/ভিডিও) পাঠান।")
    await ans(q)


@aroute("anx")
async def cb_anx(update, ctx, q, p):
    set_await(ctx, "next_ep", int(p[1]))
    await reply(update, "📅 পরের এপিসোডের সময়: <code>2026-11-05 20:00</code> অথবা কত ঘণ্টা পরে (যেমন <code>+5h</code>) অথবা <code>-</code> মুছতে।")
    await ans(q)


@awaiting("next_ep")
async def aw_next_ep(update, ctx, sid):
    t = (update.effective_message.text or "").strip().translate(_BNDIG)
    if t == "-":
        X("UPDATE serials SET next_ep_at=0 WHERE id=?", sid)
        return await reply(update, "✅ মুছেছে")
    m = re.match(r"^\+(\d+)h$", t)
    try:
        ts = now() + int(m.group(1)) * 3600 if m else int(datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())
    except ValueError:
        return await reply(update, "❌ ফরম্যাট ঠিক নয়")
    X("UPDATE serials SET next_ep_at=? WHERE id=?", ts, sid)
    n = V("SELECT COUNT(*) FROM follows WHERE serial_id=?", sid)
    await reply(update, f"✅ সেট হয়েছে: {fmt_ts(ts)}\n👥 {n} ফলোয়ারকে জানাবেন?", M([[B("📣 জানান", callback_data=f"abx:nx:{sid}"), B("না", callback_data="noop")]]))


@aroute("apr")
async def cb_apr(update, ctx, q, p):
    set_await(ctx, "promo_new", int(p[1]))
    await reply(update, "🎬 <b>প্রোমো যোগ</b>\nএকটি ভিডিও/ছবি পাঠান (ক্যাপশনে বিবরণ)।\nশিডিউল করতে ক্যাপশনের শেষে <code>@2026-11-05 18:00</code> লিখুন।\nঅথবা শুধু লগ চ্যানেলে ভিডিওর ক্যাপশনে <code>#promo</code> + সিরিয়ালের নাম দিন।")
    await ans(q)


@awaiting("promo_new")
async def aw_promo_new(update, ctx, sid):
    m = update.effective_message
    f = file_of(m) or (dict(kind="photo", file_id=m.photo[-1].file_id) if m.photo else None)
    cap = (m.caption or m.text or "").strip()
    sched = 0
    mm = re.search(r"@(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", cap)
    if mm:
        try:
            sched = int(datetime.strptime(mm.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())
            cap = cap.replace(mm.group(0), "").strip()
        except ValueError:
            pass
    pm = 0
    if f:
        st, c = await guarded(lambda: ctx.bot.copy_message(LOG_CHANNEL_ID, m.chat_id, m.message_id))
        pm = c.message_id if st == "ok" and c else 0
    X("INSERT INTO promos (serial_id, message_id, file_id, kind, caption, created_at, sched_at, status, prem_only) VALUES (?,?,?,?,?,?,?,?,?)",
      sid, pm, f["file_id"] if f else "", f["kind"] if f else "text", cap[:300], now(), sched, "sched" if sched > now() else "live", gi("promo_prem"))
    pid = V("SELECT last_insert_rowid()")
    if sched <= now():
        spawn(promo_announce(Ctx(ctx.application), pid))
    await reply(update, f"✅ প্রোমো #{pid} " + (f"শিডিউল: {fmt_ts(sched)}" if sched > now() else "পাঠানো শুরু হয়েছে"),
                M([[B("🗑 মুছুন", callback_data=f"abx:pd:{pid}")]]))
    sdirty()


# ================================================================ ব্রডকাস্ট (প্রিভিউ + কনফার্ম)
def BC(ctx):
    return ctx.user_data.setdefault("bc", dict(target="all", urgent=0, btn="", src=None, kind="all", ref=0))


@aroute("abx")
async def cb_abx(update, ctx, q, p):
    aid, act = q.from_user.id, p[1]
    b = BC(ctx)
    if act == "new":
        ctx.user_data["bc"] = dict(target="all", urgent=0, btn="", src=None, kind="all", ref=0)
        set_await(ctx, "bc_content", ("all", 0))
        await reply(update, "📢 <b>ব্রডকাস্ট</b>\nমেসেজ পাঠান (টেক্সট/ছবি/ভিডিও/ফাইল)। বাটন যোগ করতে পরের ধাপে সুযোগ পাবেন।")
    elif act == "newser":
        ctx.user_data["bc"] = dict(target="all", urgent=0, btn="", src=None, kind="all", ref=0)
        last = Q("SELECT name FROM serials ORDER BY id DESC LIMIT 3")
        txt = "🆕 <b>নতুন সিরিয়াল যোগ হয়েছে!</b>\n" + "\n".join(f"• {esc(r['name'])}" for r in last) + "\n\n/serials থেকে ফলো করুন।"
        b.update(text=txt, kind="newser", src=None)
        await send_bc_preview(ctx, aid, text=txt)
    elif act == "nx":
        sid = int(p[2])
        s = Q1("SELECT name, next_ep_at FROM serials WHERE id=?", sid)
        txt = f"⏳ <b>{esc(s['name'])}</b> — পরের এপিসোড: <b>{fmt_ts(s['next_ep_at'])}</b>"
        b.update(target=f"serial:{sid}", text=txt, src=None)
        await send_bc_preview(ctx, aid, text=txt)
    elif act == "pd":
        X("UPDATE promos SET status='deleted' WHERE id=?", int(p[2]))
        await ans(q, "🗑")
        return
    elif act in ("all", "prem", "free", "act7", "lock"):
        b["target"] = act
        await refresh_bc(update, ctx, q)
    elif act == "urg":
        b["urgent"] = 1 - b["urgent"]
        await refresh_bc(update, ctx, q)
    elif act == "btn":
        set_await(ctx, "bc_btn")
        await reply(update, "🔘 বাটন লিখুন (প্রতি লাইনে একটি): <code>লেবেল | https://লিংক</code>\nমুছতে <code>-</code>")
    elif act == "go":
        await bc_run(ctx, aid, q)
    await ans(q)


async def refresh_bc(update, ctx, q):
    kb = bc_kb(BC(ctx))
    try:
        await q.edit_message_reply_markup(reply_markup=kb)
    except TelegramError:
        pass


def bc_targets_sql(b):
    t = b["target"]
    base = "SELECT user_id FROM users WHERE banned=0 AND blocked=0 AND notify=1 AND nt_ann=1"
    if b["urgent"]:
        base = "SELECT user_id FROM users WHERE banned=0 AND blocked=0"
    if t == "all":
        return base, []
    if t == "prem":
        return base + " AND (plan_until=-1 OR plan_until>?)", [now()]
    if t == "free":
        return base + " AND NOT (plan_until=-1 OR plan_until>?)", [now()]
    if t == "act7":
        return base + " AND last_seen>?", [now() - 7 * 86400]
    if t == "lock":
        return base + " AND NOT (plan_until=-1 OR plan_until>?)", [now()]
    if t.startswith("serial:"):
        return "SELECT f.user_id FROM follows f JOIN users u ON u.user_id=f.user_id WHERE f.serial_id=? AND u.banned=0 AND u.blocked=0", [int(t.split(":")[1])]
    return base, []


def bc_kb(b):
    sql, args = bc_targets_sql(b)
    n = V(f"SELECT COUNT(*) FROM ({sql})", *args)
    tg = lambda k, n_: B(("✅ " if b["target"] == k else "") + n_, callback_data=f"abx:{k}")
    rows = [[tg("all", "সবাই"), tg("prem", "💎 Premium"), tg("free", "🆓 Free")], [tg("act7", "🟢 ৭ দিনে সক্রিয়")],
            [B(("✅ " if b["urgent"] else "") + "🚨 জরুরি (নোটিফিকেশন বন্ধ থাকলেও)", callback_data="abx:urg")],
            [B("🔘 বাটন যোগ" + (" ✅" if b["btn"] else ""), callback_data="abx:btn")],
            [B(f"🚀 পাঠান ({n} জন)", callback_data="abx:go"), B("❌ বাতিল", callback_data="noop")]]
    return M(rows)


def bc_btn_markup(b):
    rows = []
    for line in (b["btn"] or "").splitlines():
        if "|" in line:
            a, u = line.split("|", 1)
            if u.strip().startswith(("http://", "https://", "tg://")):
                rows.append([B(a.strip()[:30], url=u.strip())])
    return M(rows) if rows else None


async def send_bc_preview(ctx, aid, text=None, src=None):
    b = BC(ctx)
    if src:
        b["src"] = src
    await guarded(lambda: ctx.bot.send_message(aid, "👁 <b>প্রিভিউ</b> — নিচের মেসেজটি এভাবে যাবে:"))
    mk = bc_btn_markup(b)
    if b.get("src"):
        await guarded(lambda: ctx.bot.copy_message(aid, b["src"][0], b["src"][1], reply_markup=mk))
    else:
        await guarded(lambda: ctx.bot.send_message(aid, b.get("text", ""), reply_markup=mk))
    await guarded(lambda: ctx.bot.send_message(aid, "🎯 টার্গেট বেছে নিন ও কনফার্ম করুন:", reply_markup=bc_kb(b)))


@awaiting("bc_content")
async def aw_bc_content(update, ctx, payload):
    b = BC(ctx)
    kind, ref = payload
    m = update.effective_message
    b["target"] = f"serial:{ref}" if kind == "serial" else "all"
    b["src"] = (m.chat_id, m.message_id)
    b["text"] = m.text or ""
    await send_bc_preview(ctx, update.effective_user.id)


@awaiting("bc_btn")
async def aw_bc_btn(update, ctx, payload):
    t = (update.effective_message.text or "").strip()
    BC(ctx)["btn"] = "" if t == "-" else t
    await send_bc_preview(ctx, update.effective_user.id)


async def bc_run(ctx, aid, q):
    b = BC(ctx)
    sql, args = bc_targets_sql(b)
    uids = [r["user_id"] for r in Q(sql, *args)]
    X("INSERT INTO broadcasts (admin_id, kind, preview, total, urgent, at) VALUES (?,?,?,?,?,?)", aid, b["target"], (b.get("text") or "[media]")[:100], len(uids), b["urgent"], now())
    bid = V("SELECT last_insert_rowid()")
    mk = bc_btn_markup(b)
    src, text = b.get("src"), b.get("text", "")
    try:
        await q.edit_message_text(f"🚀 পাঠানো শুরু… {len(uids)} জন")
    except TelegramError:
        pass

    async def one(uid):
        async with SEM:
            if src:
                st, _ = await guarded(lambda: ctx.bot.copy_message(uid, src[0], src[1], reply_markup=mk))
            else:
                st, _ = await guarded(lambda: ctx.bot.send_message(uid, text, reply_markup=mk))
            return uid, st

    async def job():
        t0 = time.monotonic()
        res = await asyncio.gather(*(one(u) for u in uids), return_exceptions=True)
        ok = sum(1 for r in res if not isinstance(r, Exception) and r[1] == "ok")
        bl = [(r[0],) for r in res if not isinstance(r, Exception) and r[1] == "blocked"]
        XM("UPDATE users SET blocked=1 WHERE user_id=?", bl)
        X("UPDATE broadcasts SET ok=?, fail=? WHERE id=?", ok, len(uids) - ok, bid)
        await tsend(ctx.bot, aid, f"📢 <b>ব্রডকাস্ট #{bid} সম্পন্ন</b>\n✅ {ok} • 🔕 ব্লক {len(bl)} • ⚠️ ব্যর্থ {len(uids) - ok - len(bl)}\n⏱ {time.monotonic() - t0:.1f}s")

    spawn(job())
    alog(aid, "broadcast", b["target"], f"{len(uids)} users")


@acommand("broadcast")
async def cmd_broadcast(update, ctx):
    ctx.user_data["bc"] = dict(target="all", urgent=0, btn="", src=None, kind="all", ref=0)
    set_await(ctx, "bc_content", ("all", 0))
    await reply(update, "📢 <b>ব্রডকাস্ট</b>\nমেসেজ পাঠান (টেক্সট/ছবি/ভিডিও/ফাইল)।")


# ================================================================ পোল
POLL_ADMIN = ""


@aroute("apol")
async def cb_apol(update, ctx, q, p):
    set_await(ctx, "poll_new")
    await reply(update, "📊 <b>পোল</b>\nফরম্যাট:\n<code>প্রশ্ন\nঅপশন ১\nঅপশন ২\nঅপশন ৩</code>")
    await ans(q)


@acommand("poll")
async def cmd_poll(update, ctx):
    set_await(ctx, "poll_new")
    await reply(update, "📊 <b>পোল</b>\nফরম্যাট:\n<code>প্রশ্ন\nঅপশন ১\nঅপশন ২</code>")


@awaiting("poll_new")
async def aw_poll_new(update, ctx, payload):
    lines = [x.strip() for x in (update.effective_message.text or "").splitlines() if x.strip()]
    if len(lines) < 3 or len(lines) > 7:
        return await reply(update, "❌ প্রশ্ন + ২–৬টি অপশন দিন")
    X("INSERT INTO polls (question, options, at) VALUES (?,?,?)", lines[0], json.dumps(lines[1:], ensure_ascii=False), now())
    pid = V("SELECT last_insert_rowid()")
    await reply(update, f"✅ পোল #{pid} তৈরি।", M([[B("📤 সবাইকে পাঠান", callback_data=f"pol0:send:{pid}"), B("📊 ফলাফল", callback_data=f"pol0:res:{pid}")]]))


def poll_kb(pid, opts, lang="bn"):
    return M([[B(o[:40], callback_data=f"pol:{pid}:{i}")] for i, o in enumerate(opts)])


@aroute("pol0")
async def cb_pol0(update, ctx, q, p):
    act, pid = p[1], int(p[2])
    po = Q1("SELECT * FROM polls WHERE id=?", pid)
    opts = json.loads(po["options"])
    if act == "res":
        cnt = {r["opt"]: r["n"] for r in Q("SELECT opt, COUNT(*) AS n FROM poll_votes WHERE poll_id=? GROUP BY opt", pid)}
        tot = sum(cnt.values())
        await reply(update, f"📊 <b>{esc(po['question'])}</b>\n\n" + "\n".join(f"{esc(o)} — {cnt.get(i, 0)} ({(cnt.get(i, 0) * 100 // tot) if tot else 0}%)" for i, o in enumerate(opts)) + f"\n\nমোট: {tot}")
        return await ans(q)
    uids = [r["user_id"] for r in Q("SELECT user_id FROM users WHERE banned=0 AND blocked=0 AND nt_ann=1")]

    async def one(uid):
        async with SEM:
            return await tsend(ctx.bot, uid, f"📊 <b>{esc(po['question'])}</b>", poll_kb(pid, opts))

    spawn(asyncio.gather(*(one(u) for u in uids), return_exceptions=True))
    alog(q.from_user.id, "poll_send", pid, f"{len(uids)}")
    await ans(q, f"📤 {len(uids)} জনকে পাঠানো হচ্ছে", True)


@route("pol")
async def cb_pol(update, ctx, q, p):
    uid, pid, opt = q.from_user.id, int(p[1]), int(p[2])
    X("INSERT OR REPLACE INTO poll_votes (poll_id, user_id, opt) VALUES (?,?,?)", pid, uid, opt)
    await ans(q, L(lg(uid), "✅ ভোট নেওয়া হয়েছে, ধন্যবাদ!", "✅ Vote recorded, thanks!"), True)


# ================================================================ /setwelcome ও /setterms (টেক্সট এডিটর)
TEMPLATES = {
    "simple": ("সাদামাটা", "👋 <b>স্বাগতম, {name}!</b>\n\n📺 {bot}-এ পছন্দের সিরিয়াল ফলো করুন।\n👤 {plan} • 🔔 {follows}/{limit}"),
    "festive": ("উৎসব", "🎉 <b>শুভেচ্ছা, {name}!</b> 🎉\n\n{bot}-এর সাথে থাকুন — নতুন এপিসোড সবার আগে পাবেন!\n💎 {plan}\n📞 {support}"),
    "pro": ("প্রফেশনাল", "✨ <b>{bot}</b>\n━━━━━━━━━━\n👤 {name} • 🆔 <code>{id}</code>\n💎 {plan}\n📺 ফলো: <b>{follows}/{limit}</b>\n━━━━━━━━━━\nনিচের মেনু থেকে শুরু করুন 👇"),
}


def ed_keys(kind):
    return TEXT_KEYS[:4] if kind == "w" else (TERMS_KEYS if kind == "t" else TEXT_KEYS[4:])


@acommand("setwelcome")
async def cmd_setwelcome(update, ctx):
    await reply(update, *ed_menu("w"))


@acommand("setterms")
async def cmd_setterms(update, ctx):
    await reply(update, *ed_menu("t"))


def ed_menu(kind):
    kb = [[B(bn + ("  ✏️" if has_custom(k, "bn") or has_custom(k, "en") else ""), callback_data=f"evl:{k}:bn")] for k, bn, en in ed_keys(kind)]
    if kind == "t":
        kb.append([B(f"🔢 ভার্সন বাড়ান (এখন {gi('terms_ver')})", callback_data="evv"), B("📝 পরিবর্তনের নোট", callback_data="evch")])
        kb.append([B(f"{'✅' if gs('consent_on') == '1' else '❌'} সম্মতি বাধ্যতামূলক", callback_data="atg:consent_on:evt:t")])
    else:
        kb.append([B("📚 টেমপ্লেট", callback_data="evtp"), B("🔤 প্লেসহোল্ডার", callback_data="evph")])
        kb.append([B(f"🅰️🅱️ A/B টেস্ট {'✅' if gs('ab_on') == '1' else '❌'}", callback_data="abt:t"), B("📊 A/B ফলাফল", callback_data="abt:s"), B("⏰ শিডিউল", callback_data="wsc:l")])
        kb.append([B("📋 আরও টেক্সট (হেল্প/প্ল্যান/লিমিট)", callback_data="evt:o")])
    kb.append([B("↩️", callback_data="ad:home")])
    return ("✏️ <b>ওয়েলকাম/টেক্সট এডিটর</b>" if kind in "wo" else "📜 <b>টার্মস এডিটর</b>"), M(kb)


@aroute("evt")
async def cb_evt(update, ctx, q, p):
    await show(update, ctx, *ed_menu(p[1]))
    await ans(q)


@aroute("evph")
async def cb_evph(update, ctx, q, p):
    await reply(update, "🔤 <b>প্লেসহোল্ডার</b>\n" + "\n".join(f"<code>{{{k}}}</code>" for k in KNOWN_PH) + "\n\nসাপোর্টেড ট্যাগ: b, i, u, s, code, a, tg-spoiler, blockquote")
    await ans(q)


@aroute("evtp")
async def cb_evtp(update, ctx, q, p):
    kb = [[B(v[0], callback_data=f"evtq:{k}")] for k, v in TEMPLATES.items()] + [[B("↩️", callback_data="evt:w")]]
    await show(update, ctx, "📚 <b>টেমপ্লেট বেছে নিন</b> (bn welcome_old-এ বসবে)", M(kb))
    await ans(q)


ADMIN_PFX.add("evtq")


@route("evtq")
async def cb_evtq(update, ctx, q, p):
    body = TEMPLATES[p[1]][1]
    save_text("welcome_old", "bn", body, by=q.from_user.id)
    await ans(q, "✅ বসানো হয়েছে")
    await show(update, ctx, *ed_view("welcome_old", "bn"))


def ed_view(k, lang):
    body, banner, btns = get_text(k, lang)
    vers = V("SELECT COUNT(*) FROM text_versions WHERE k=? AND lang=?", k, lang)
    lbl = next((bn for kk, bn, en in TEXT_KEYS + TERMS_KEYS if kk == k), k)
    text = f"✏️ <b>{lbl}</b> • {'বাংলা' if lang == 'bn' else 'English'} {'✅ কাস্টম' if has_custom(k, lang) else '(ডিফল্ট)'}\n🖼 ব্যানার: {'আছে' if banner else 'নেই'} • 🔘 বাটন: {len(btns)} • 🕘 ভার্সন: {vers}\n\n<pre>{esc(body[:1800])}</pre>"
    kb = [[B("✏️ এডিট", callback_data=f"eve:{k}:{lang}"), B("👁 প্রিভিউ", callback_data=f"evp:{k}:{lang}")],
          [B("↩️ আগের ভার্সন", callback_data=f"evu:{k}:{lang}"), B("♻️ ডিফল্ট", callback_data=f"evr:{k}:{lang}")],
          [B("🖼 ব্যানার", callback_data=f"evn:{k}:{lang}"), B("🔘 বাটন", callback_data=f"evk:{k}:{lang}")],
          [B("🌐 " + ("English" if lang == "bn" else "বাংলা"), callback_data=f"evl:{k}:{'en' if lang == 'bn' else 'bn'}"), B("🧾 এডিট লগ", callback_data=f"evg:{k}")],
          [B("⏰ এই টেক্সট শিডিউল করুন", callback_data=f"wsc:n:{k}:{lang}")],
          [B("↩️", callback_data="evt:" + ("t" if k.startswith("terms") else ("w" if k.startswith("welcome") else "o")))]]
    return text, M(kb)


@aroute("evl")
async def cb_evl(update, ctx, q, p):
    await show(update, ctx, *ed_view(p[1], p[2]))
    await ans(q)


@aroute("eve")
async def cb_eve(update, ctx, q, p):
    set_await(ctx, "ed_text", (p[1], p[2]))
    await reply(update, f"✏️ নতুন টেক্সট পাঠান। প্লেসহোল্ডার: " + " ".join(f"<code>{{{k}}}</code>" for k in KNOWN_PH) + "\n/cancel দিয়ে বাতিল।")
    await ans(q)


@awaiting("ed_text")
async def aw_ed_text(update, ctx, payload):
    k, lang = payload
    t = update.effective_message.text_html or update.effective_message.text or ""
    errs = validate_text(t)
    if errs:
        set_await(ctx, "ed_text", payload)
        return await reply(update, "⚠️ <b>সমস্যা পাওয়া গেছে:</b>\n" + "\n".join(errs) + "\n\nঠিক করে আবার পাঠান।")
    save_text(k, lang, t, by=update.effective_user.id)
    await reply(update, "✅ সংরক্ষিত — প্রিভিউ:")
    await reply(update, fill(t, user(update.effective_user.id), update.effective_user.first_name, update.effective_user.id, lang))
    await reply(update, *ed_view(k, lang))


@aroute("evp")
async def cb_evp(update, ctx, q, p):
    uid = q.from_user.id
    body, banner, btns = get_text(p[1], p[2])
    kb = M([[B(b[0], url=b[1])] for b in btns if len(b) > 1]) if btns else None
    if banner:
        await guarded(lambda: ctx.bot.send_photo(uid, banner, caption=fill(body, user(uid), q.from_user.first_name, uid, p[2])[:1024], reply_markup=kb))
    else:
        await reply(update, fill(body, user(uid), q.from_user.first_name, uid, p[2]), kb)
    await ans(q)


@aroute("evu")
async def cb_evu(update, ctx, q, p):
    v = Q1("SELECT * FROM text_versions WHERE k=? AND lang=? ORDER BY id DESC LIMIT 1", p[1], p[2])
    if not v:
        return await ans(q, "আগের ভার্সন নেই", True)
    X("DELETE FROM text_versions WHERE id=?", v["id"])
    X("INSERT INTO texts (k, lang, body) VALUES (?,?,?) ON CONFLICT(k, lang) DO UPDATE SET body=excluded.body", p[1], p[2], v["body"])
    alog(q.from_user.id, "text_undo", f"{p[1]}/{p[2]}")
    await show(update, ctx, *ed_view(p[1], p[2]))
    await ans(q, "↩️")


@aroute("evr")
async def cb_evr(update, ctx, q, p):
    reset_text(p[1], p[2], q.from_user.id)
    await show(update, ctx, *ed_view(p[1], p[2]))
    await ans(q, "♻️")


@aroute("evn")
async def cb_evn(update, ctx, q, p):
    set_await(ctx, "ed_banner", (p[1], p[2]))
    await reply(update, "🖼 ব্যানার ছবি পাঠান (মুছতে <code>-</code> লিখুন)।")
    await ans(q)


@awaiting("ed_banner")
async def aw_ed_banner(update, ctx, payload):
    k, lang = payload
    m = update.effective_message
    save_text(k, lang, banner=(m.photo[-1].file_id if m.photo else ""), by=update.effective_user.id)
    await reply(update, *ed_view(k, lang))


@aroute("evk")
async def cb_evk(update, ctx, q, p):
    set_await(ctx, "ed_btns", (p[1], p[2]))
    await reply(update, "🔘 বাটন লিখুন (প্রতি লাইনে একটি):\n<code>লেবেল | https://লিংক</code>\nমুছতে <code>-</code>")
    await ans(q)


@awaiting("ed_btns")
async def aw_ed_btns(update, ctx, payload):
    k, lang = payload
    t = (update.effective_message.text or "").strip()
    btns = [] if t == "-" else [[a.strip(), u.strip()] for a, u in (ln.split("|", 1) for ln in t.splitlines() if "|" in ln) if u.strip().startswith(("http://", "https://", "tg://"))]
    save_text(k, lang, btns=btns, by=update.effective_user.id)
    await reply(update, *ed_view(k, lang))


@aroute("evg")
async def cb_evg(update, ctx, q, p):
    rows = Q("SELECT * FROM admin_log WHERE action LIKE 'text_%' AND target LIKE ? ORDER BY id DESC LIMIT 10", p[1] + "/%")
    await reply(update, "🧾 <b>এডিট লগ</b>\n" + ("\n".join(f"• {fmt_ts(r['at'])} — {r['action']} ({r['admin_id']})" for r in rows) or "খালি"))
    await ans(q)


@aroute("evv")
async def cb_evv(update, ctx, q, p):
    ss("terms_ver", gi("terms_ver") + 1)
    alog(q.from_user.id, "terms_ver", gi("terms_ver"))
    await show(update, ctx, *ed_menu("t"))
    await ans(q, f"ভার্সন {gi('terms_ver')} — ইউজাররা আবার সম্মতি দেবে (সম্মতি চালু থাকলে)", True)


@aroute("evch")
async def cb_evch(update, ctx, q, p):
    set_await(ctx, "terms_note")
    await reply(update, "📝 পরিবর্তনের সংক্ষিপ্ত নোট লিখুন (ইউজারকে দেখানো হবে)।")
    await ans(q)


@awaiting("terms_note")
async def aw_terms_note(update, ctx, payload):
    ss("terms_changes", (update.effective_message.text or "")[:300])
    await reply(update, *ed_menu("t"))


# ================================================================ সেটিংস টগল (এডমিন) ও ভ্যালু এডিট
TOGGLES = [("deliver", "🚚 অটো ডেলিভারি"), ("protect", "🔐 ফরোয়ার্ড প্রোটেক্ট"), ("alert_new", "🆕 নতুন ইউজার অ্যালার্ট"), ("maintenance", "🛠 মেইনটেন্যান্স"),
           ("consent_on", "📜 সম্মতি বাধ্যতামূলক"), ("history_on", "🕘 হিস্ট্রি"), ("saved_on", "⭐ সেভ"), ("quality_on", "🎞 কোয়ালিটি বাটন"), ("alert_q_missing", "⏰ কোয়ালিটি-মিসিং অ্যালার্ট"),
           ("require_plan", "🔒 প্ল্যান-লক (প্ল্যান ছাড়া বন্ধ)"), ("free_for_all", "🎁 সবার জন্য ফ্রি মোড"), ("autoplay_on", "▶️ গ্লোবাল অটোপ্লে"), ("test_mode", "🧪 টেস্ট মোড"),
           ("on_bkash", "📱 bKash"), ("on_nagad", "📲 Nagad"), ("on_rocket", "🚀 Rocket"), ("promo_prem", "🎬 প্রোমো শুধু Premium"), ("autoplay_approve", "🛡 অটোপ্লে অ্যাপ্রুভাল (পাঠানোর আগে জিজ্ঞাসা)"), ("sms_verify", "📩 SMS অটো-ভেরিফাই (Termux:API)")]
VALUES = [("free_limit", "🆓 Free লিমিট"), ("trial_days", "🎁 ফ্রি ট্রায়াল (দিন)"), ("q_free", "🎞 Free সর্বোচ্চ কোয়ালিটি"), ("q_premium", "💎 Premium সর্বোচ্চ কোয়ালিটি"),
          ("pub_window", "⏱ গ্রুপিং উইন্ডো (মিনিট)"), ("pub_mode", "📤 পাবলিশ মোড (window/all/manual)"), ("hist_days", "🕘 হিস্ট্রি রাখার দিন"),
          ("basic_limit", "🥈 Basic ফলো লিমিট"), ("q_basic", "🥈 Basic সর্বোচ্চ কোয়ালিটি"), ("price_b7", "🥈 Basic ৭ দিন দাম"), ("price_b30", "🥈 Basic ৩০ দিন দাম"), ("price_b90", "🥈 Basic ৯০ দিন দাম"), ("price_7", "৭ দিনের দাম"), ("price_30", "৩০ দিনের দাম"), ("price_90", "৯০ দিনের দাম"), ("price_life", "Lifetime দাম"),
          ("pm_bkash", "bKash নম্বর"), ("pm_nagad", "Nagad নম্বর"), ("pm_rocket", "Rocket নম্বর"), ("free_delay", "🆓 Free ইউজারের দেরি (মিনিট)"), ("send_rate", "⚡ সেন্ড রেট/সেকেন্ড"), ("faq_url", "📚 FAQ লিংক")]


def settings_admin(page):
    if page == 0:
        kb = [[B(("✅ " if gs(k) == "1" else "❌ ") + n, callback_data=f"atg:{k}")] for k, n in TOGGLES]
        kb.append([B("➡️ মানসমূহ", callback_data="aset:1"), B("↩️", callback_data="ast")])
        return "⚙️ <b>বট সেটিংস — সুইচ</b>", M(kb)
    kb = [[B(f"{n}: {esc(gs(k)) or '—'}", callback_data=f"aset:v:{k}")] for k, n in VALUES]
    kb.append([B("⬅️ সুইচ", callback_data="aset:0"), B("↩️", callback_data="ast")])
    return "⚙️ <b>বট সেটিংস — মান</b>\nট্যাপ করে বদলান।", M(kb)


@acommand("settings_admin")
async def cmd_sa(update, ctx):
    await reply(update, *settings_admin(0))


@aroute("aset")
async def cb_aset(update, ctx, q, p):
    if p[1] == "v":
        set_await(ctx, "set_val", p[2])
        await reply(update, f"✏️ <b>{dict(VALUES)[p[2]]}</b> — নতুন মান লিখুন (খালি করতে <code>-</code>)। এখন: <code>{esc(gs(p[2]))}</code>")
        return await ans(q)
    await show(update, ctx, *settings_admin(int(p[1])))
    await ans(q)


@awaiting("set_val")
async def aw_set_val(update, ctx, k):
    v = (update.effective_message.text or "").strip().translate(_BNDIG)
    v = "" if v == "-" else v
    if k in ("free_limit", "trial_days", "q_free", "q_premium", "pub_window", "hist_days", "price_7", "price_30", "price_90", "price_life", "free_delay", "basic_limit", "q_basic", "price_b7", "price_b30", "price_b90") and not v.isdigit():
        return await reply(update, "❌ সংখ্যা দিন")
    if k in ("q_free", "q_premium", "q_basic") and int(v) not in QUALS:
        return await reply(update, "❌ 480 / 720 / 1080")
    if k == "pub_mode" and v not in ("window", "all", "manual"):
        return await reply(update, "❌ window / all / manual")
    old = gs(k)
    ss(k, v)
    if k == "send_rate" and v:
        try:
            LIM.gap = 1.0 / max(1.0, float(v))
        except ValueError:
            pass
    alog(update.effective_user.id, "set", k, v, undo={"t": "set", "k": k, "v": old})
    await reply(update, "✅ সংরক্ষিত", M([[B("⚙️ সেটিংস", callback_data="aset:1")]]))


@aroute("atg")
async def cb_atg(update, ctx, q, p):
    k = p[1]
    old = gs(k)
    ss(k, "0" if old == "1" else "1")
    alog(q.from_user.id, "toggle", k, gs(k), undo={"t": "set", "k": k, "v": old})
    if len(p) > 3:
        await show(update, ctx, *ed_menu(p[3]))
    else:
        await show(update, ctx, *settings_admin(0))
    await ans(q, "✅")


# ================================================================ অ্যাডমিন অ্যাকশন লগ + আন্ডু, হিস্ট্রি
@aroute("alog")
async def cb_alog(update, ctx, q, p):
    await show(update, ctx, *log_view(int(p[1])))
    await ans(q)


def log_view(page):
    total = V("SELECT COUNT(*) FROM admin_log")
    page, pages, off = paginate(total, page, 10)
    rows = Q("SELECT * FROM admin_log ORDER BY id DESC LIMIT 10 OFFSET ?", off)
    lines = [f"🧾 <b>অ্যাকশন লগ</b> ({total})"] + [f"{'↩️' if r['undone'] else '•'} {fmt_ts(r['at'])} — <b>{esc(r['action'])}</b> {esc(r['target'])} {esc(r['detail'][:40])}" for r in rows]
    kb = [[B(f"↩️ আন্ডু #{r['id']} {r['action']}", callback_data=f"alu:{r['id']}")] for r in rows if r["undo"] and not r["undone"]][:5]
    nv = nav_row("alog", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B("↩️", callback_data="ad:home")])
    return "\n".join(lines), M(kb)


PAGERS["alog"] = lambda uid, page, ctx: log_view(page)


@aroute("alu")
async def cb_alu(update, ctx, q, p):
    r = Q1("SELECT * FROM admin_log WHERE id=?", int(p[1]))
    if not r or not r["undo"] or r["undone"]:
        return await ans(q, "আন্ডু সম্ভব নয়", True)
    ok = apply_undo(json.loads(r["undo"]))
    if ok:
        X("UPDATE admin_log SET undone=1 WHERE id=?", r["id"])
        alog(q.from_user.id, "undo", r["id"])
    await show(update, ctx, *log_view(0))
    await ans(q, "↩️ হয়েছে" if ok else "❌", not ok)


async def admin_history_of(update, ctx, uid):
    rows = Q("SELECT s.name, e.ep_no, d.sent_at, d.watched, d.quality FROM deliveries d JOIN eps e ON e.id=d.episode_id JOIN serials s ON s.id=e.serial_id WHERE d.user_id=? ORDER BY d.sent_at DESC LIMIT 25", uid)
    pays = Q("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 5", uid)
    plans = Q("SELECT * FROM plan_history WHERE user_id=? ORDER BY at DESC LIMIT 5", uid)
    txt = f"🕘 <b>{uid}-এর টাইমলাইন</b>\n\n🎬 <b>ডেলিভারি</b>\n" + ("\n".join(f"{'✅' if r['watched'] else '▫️'} {fmt_ts(r['sent_at'])} {esc(r['name'])} {('E' + str(r['ep_no'])) if r['ep_no'] is not None else ''} {r['quality']}p" for r in rows) or "—")
    txt += "\n\n💎 <b>প্ল্যান</b>\n" + ("\n".join(f"• {fmt_ts(r['at'])} {esc(r['plan'])} {esc(r['note'])}" for r in plans) or "—")
    txt += "\n\n💳 <b>পেমেন্ট</b>\n" + ("\n".join(f"• #{r['id']} {r['amount']}৳ {r['status']} {esc(r['trx'])}" for r in pays) or "—")
    await reply(update, txt[:4000])


# ================================================================ সার্চ অ্যানালিটিক্স / রিকোয়েস্ট
@aroute("aap")
async def cb_aap(update, ctx, q, p):
    top = Q("SELECT q, COUNT(*) AS n FROM searches GROUP BY q ORDER BY n DESC LIMIT 8")
    nf = Q("SELECT q, COUNT(*) AS n FROM searches WHERE found=0 GROUP BY q ORDER BY n DESC LIMIT 8")
    rq = Q("SELECT q, COUNT(*) AS n FROM requests GROUP BY qkey ORDER BY n DESC LIMIT 8")
    sv = Q("SELECT se.name, COUNT(*) AS n FROM saves sv JOIN eps e ON e.id=sv.episode_id JOIN serials se ON se.id=e.serial_id GROUP BY se.id ORDER BY n DESC LIMIT 5")
    txt = ("🔍 <b>সার্চ অ্যানালিটিক্স</b>\n\n🔝 <b>টপ সার্চ</b>\n" + ("\n".join(f"• {esc(r['q'])} ({r['n']})" for r in top) or "—") +
           "\n\n❌ <b>পাওয়া যায়নি</b>\n" + ("\n".join(f"• {esc(r['q'])} ({r['n']})" for r in nf) or "—") +
           "\n\n🗳 <b>রিকোয়েস্ট ভোট</b>\n" + ("\n".join(f"• {esc(r['q'])} — {r['n']} জন" for r in rq) or "—") +
           "\n\n⭐ <b>সবচেয়ে বেশি সেভ</b>\n" + ("\n".join(f"• {esc(r['name'])} ({r['n']})" for r in sv) or "—"))
    await show(update, ctx, txt, M([[B("↩️", callback_data="ad:home")]]))
    await ans(q)


@acommand("requests")
async def cmd_requests(update, ctx):
    rq = Q("SELECT q, COUNT(*) AS n FROM requests GROUP BY qkey ORDER BY n DESC LIMIT 15")
    await reply(update, "🗳 <b>রিকোয়েস্ট</b>\n" + ("\n".join(f"• {esc(r['q'])} — {r['n']} জন" for r in rq) or "কিছু নেই") + "\n\nযোগ করতে: /addfeed")


@acommand("holiday")
async def cmd_holiday(update, ctx):
    if len(ctx.args) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", ctx.args[0]):
        X("INSERT INTO holidays (day, reason) VALUES (?,?)", ctx.args[0], " ".join(ctx.args[1:])[:100])
        return await reply(update, "✅ ছুটি যোগ হয়েছে")
    rows = Q("SELECT * FROM holidays ORDER BY day DESC LIMIT 10")
    await reply(update, "🎉 <b>ছুটি</b>\n" + "\n".join(f"• {r['day']} — {esc(r['reason'])}" for r in rows) + "\n\nযোগ: <code>/holiday 2026-11-05 দীপাবলি</code>")


@aroute("ahl")
async def cb_ahl(update, ctx, q, p):
    order, off = layout()
    if len(p) > 1 and p[1] == "reset":
        ss("home_layout", "")
        return await show(update, ctx, "♻️ রিসেট হয়েছে", M([[B("↩️", callback_data="ahl")]]))
    if len(p) > 2:
        k = p[1]
        d = {"order": order, "off": list(off)}
        if p[2] == "t":
            d["off"] = [x for x in d["off"] if x != k] if k in off else d["off"] + [k]
        elif p[2] == "u" and order.index(k) > 0:
            i = order.index(k)
            order[i - 1], order[i] = order[i], order[i - 1]
            d["order"] = order
        ss("home_layout", json.dumps(d))
        alog(q.from_user.id, "home_layout", k)
        order, off = layout()
    kb = [[B(("✅ " if k not in off else "❌ ") + HOME_BTNS[k][0], callback_data=f"ahl:{k}:t"), B("⬆️", callback_data=f"ahl:{k}:u")] for k in order]
    kb.append([B("♻️ ডিফল্ট", callback_data="ahl:reset"), B("↩️", callback_data="ad:home")])
    if len(p) > 1 and p[1] == "reset":
        ss("home_layout", "")
        return await show(update, ctx, "♻️ রিসেট হয়েছে", M([[B("↩️", callback_data="ahl")]]))
    await show(update, ctx, "🏠 <b>হোম মেনু লেআউট</b>\n✅/❌ দিয়ে লুকান, ⬆️ দিয়ে ওপরে নিন।", M(kb))
    await ans(q)


# ================================================================ অটোপ্লে (এডমিন)
def admin_autoplay_view():
    onoff = lambda k: "✅" if gs(k) == "1" else "❌"
    n_off = V("SELECT COUNT(*) FROM serials WHERE autoplay=0")
    pend = V("SELECT COUNT(*) FROM queue")
    rep = Q1("SELECT * FROM delivery_reports WHERE dry=0 ORDER BY id DESC LIMIT 1")
    txt = (f"▶️ <b>অটোপ্লে — এডমিন কন্ট্রোল</b>\n\n🌐 গ্লোবাল: {onoff('autoplay_on')}\n🧪 টেস্ট মোড: {onoff('test_mode')} (শুধু এডমিন/টেস্টার পায়)\n🆓 Free ইউজারের দেরি: {gi('free_delay')} মিনিট (Premium আগে পায়)\n"
           f"⚡ সেন্ড রেট: {gs('send_rate') or SEND_RATE}/সেকেন্ড\n⏸ অটোপ্লে বন্ধ সিরিয়াল: {n_off}\n⏳ কিউ: {pend}\n"
           + (f"📤 শেষ ডেলিভারি: ✅{rep['ok']} ⚠️{rep['fail']} 🔕{rep['blocked']} ({rep['secs']:.1f}s)\n" if rep else "")
           + f"🔔 এরর অ্যালার্ট: যেকোনো ব্যর্থতা এডমিনকে জানানো হয়।")
    kb = [[B(f"{onoff('autoplay_on')} গ্লোবাল", callback_data="atg:autoplay_on:ap:a"), B(f"{onoff('test_mode')} টেস্ট মোড", callback_data="atg:test_mode:ap:a")],
          [B("🆓 দেরি সেট", callback_data="aset:v:free_delay"), B("⚡ রেট সেট", callback_data="aset:v:send_rate")],
          [B("📺 সিরিয়াল ধরে অটোপ্লে", callback_data="apm:0"), B("⏸ সব পজ/রিজিউম", callback_data="apm:p")],
          [B("📊 ডেলিভারি রিপোর্ট", callback_data="apm:r"), B("↩️", callback_data="ast")]]
    return txt, M(kb)


@aroute("apm")
async def cb_apm(update, ctx, q, p):
    if p[1] == "p":
        ss("autoplay_on", "0" if gs("autoplay_on") == "1" else "1")
        await show(update, ctx, *admin_autoplay_view())
        return await ans(q, "✅")
    if p[1] == "r":
        rows = Q("SELECT r.*, s.name FROM delivery_reports r LEFT JOIN eps e ON e.id=r.episode_id LEFT JOIN serials s ON s.id=e.serial_id ORDER BY r.id DESC LIMIT 8")
        await show(update, ctx, "📊 <b>ডেলিভারি রিপোর্ট</b>\n\n" + "\n".join(f"• {fmt_ts(r['at'])} {esc(r['name'] or '')}: ✅{r['ok']} 🕒{r['queued']} 🔕{r['blocked']} ⚠️{r['fail']} {r['secs']:.1f}s{' (ড্রাই)' if r['dry'] else ''}" for r in rows),
                   M([[B("↩️", callback_data="ap:a")]]))
        return await ans(q)
    if p[1] == "t":
        X("UPDATE serials SET autoplay=1-autoplay WHERE id=?", int(p[2]))
        page = int(p[3])
    else:
        page = int(p[1])
    total = V("SELECT COUNT(*) FROM serials WHERE draft=0")
    page, pages, off = paginate(total, page, 10)
    rows = Q("SELECT id, name, autoplay FROM serials WHERE draft=0 ORDER BY name LIMIT 10 OFFSET ?", off)
    kb = [[B(("▶️ " if r["autoplay"] else "⏸ ") + r["name"][:30], callback_data=f"apm:t:{r['id']}:{page}")] for r in rows]
    nv = nav_row("apm", page, pages)
    if nv:
        kb.append(nv)
    kb.append([B("↩️", callback_data="ap:a")])
    await show(update, ctx, "📺 <b>সিরিয়াল ধরে অটোপ্লে</b> (⏸ = শুধু নোটিফিকেশন যাবে)", M(kb))
    await ans(q)


# ================================================================ /editvideo ও /testvideo
EV_TEXT = {"tpl": "ক্যাপশন টেমপ্লেট ({title} {channel} {ep} {time} {quality} {size})", "header": "হেডার লাইন", "footer": "ফুটার লাইন", "l_save": "সেভ বাটনের লেবেল",
           "l_watch": "দেখেছি বাটনের লেবেল", "l_next": "পরের বাটনের লেবেল", "l_prev": "আগের বাটনের লেবেল", "l_report": "রিপোর্ট বাটনের লেবেল", "l_unf": "আনফলো বাটনের লেবেল"}
EV_BOOL = {"show_size": "ফাইল সাইজ", "show_q": "কোয়ালিটি লাইন", "b_qual": "কোয়ালিটি বাটন", "b_save": "সেভ বাটন", "b_watch": "দেখেছি বাটন", "b_rate": "👍/👎", "b_nav": "আগের/পরের", "b_report": "রিপোর্ট", "b_unf": "আনফলো"}


def vcfg_save(d, aid, what):
    old = gs("vcfg")
    X("INSERT INTO settings (k, v) VALUES ('vcfg_prev', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", old)
    ss("vcfg", json.dumps(d, ensure_ascii=False))
    alog(aid, "editvideo", what, "", undo={"t": "set", "k": "vcfg", "v": old})


def ev_view():
    c = vcfg()
    txt = ("🎞 <b>/editvideo — ভিডিও/ক্যাপশন এডিটর</b>\n\n" + f"📝 টেমপ্লেট: <code>{esc(c['tpl']) or 'ডিফল্ট'}</code>\n🔝 হেডার: {esc(c['header']) or '—'}\n🔚 ফুটার: {esc(c['footer']) or '—'}\n" +
           "\n".join(f"{'✅' if c[k] else '❌'} {n}" for k, n in EV_BOOL.items()))
    kb = [[B(n.split(" (")[0], callback_data=f"evb:t:{k}")] for k, n in EV_TEXT.items()]
    kb = [kb[i] + (kb[i + 1] if i + 1 < len(kb) else []) for i in range(0, len(kb), 2)]
    bl = [B(("✅ " if c[k] else "❌ ") + n, callback_data=f"evb:b:{k}") for k, n in EV_BOOL.items()]
    kb += [bl[i:i + 2] for i in range(0, len(bl), 2)]
    kb.append([B("↩️ আন্ডু", callback_data="evb:u"), B("♻️ রিসেট", callback_data="evb:r"), B("👁 প্রিভিউ", callback_data="evb:p")])
    kb.append([B("↩️", callback_data="ad:home")])
    return txt, M(kb)


@acommand("editvideo")
async def cmd_editvideo(update, ctx):
    await reply(update, *ev_view())


@aroute("evb")
async def cb_evb(update, ctx, q, p):
    aid, act = q.from_user.id, p[1]
    c = dict(vcfg())
    if act == "0":
        pass
    elif act == "t":
        set_await(ctx, "ev_text", p[2])
        await reply(update, f"✏️ <b>{EV_TEXT[p[2]]}</b> লিখে পাঠান (মুছতে <code>-</code>)।")
        return await ans(q)
    elif act == "b":
        c[p[2]] = 0 if c[p[2]] else 1
        vcfg_save(c, aid, p[2])
    elif act == "u":
        prev = gs("vcfg_prev")
        ss("vcfg", prev)
        alog(aid, "editvideo_undo")
    elif act == "r":
        vcfg_save({}, aid, "reset")
    elif act == "p":
        return await send_test_video(ctx, aid, q)
    await show(update, ctx, *ev_view())
    await ans(q, "✅")


@awaiting("ev_text")
async def aw_ev_text(update, ctx, k):
    v = (update.effective_message.text_html or "").strip()
    if v == "-":
        v = ""
    errs = [e for e in validate_text(v.replace("{title}", "").replace("{channel}", "").replace("{ep}", "").replace("{time}", "").replace("{quality}", "").replace("{size}", "")) if "প্লেসহোল্ডার" not in e]
    if errs or len(v) > 600:
        set_await(ctx, "ev_text", k)
        return await reply(update, "⚠️ " + ("\n".join(errs) or "সর্বোচ্চ ৬০০ অক্ষর") + "\nআবার পাঠান।")
    c = dict(vcfg())
    c[k] = v
    vcfg_save(c, update.effective_user.id, k)
    await reply(update, *ev_view())


async def send_test_video(ctx, aid, q=None):
    ep = Q1("SELECT e.id FROM eps e WHERE e.status='published' AND EXISTS (SELECT 1 FROM ep_files f WHERE f.episode_id=e.id) ORDER BY e.id DESC LIMIT 1")
    if not ep:
        ep = Q1("SELECT e.id FROM eps e WHERE EXISTS (SELECT 1 FROM ep_files f WHERE f.episode_id=e.id) ORDER BY e.id DESC LIMIT 1")
    if not ep:
        if q:
            await ans(q, "❌ কোনো এপিসোড নেই", True)
        return await tsend(ctx.bot, aid, "❌ প্রিভিউ করার মতো কোনো এপিসোড নেই।")
    ok = await send_one_ep(ctx, aid, ep["id"], record=False)
    if q:
        await ans(q, "👁 পাঠানো হয়েছে (শুধু আপনি দেখছেন)" if ok else "⚠️ ব্যর্থ", not ok)


@acommand("testvideo")
async def cmd_testvideo(update, ctx):
    await send_test_video(ctx, update.effective_user.id)


# ================================================================ /test — এডমিন টেস্ট স্যুট
def test_menu():
    kb = [[B("🏓 পিং", callback_data="tsx:ping"), B("🤖 টোকেন", callback_data="tsx:tok")], [B("📡 চ্যানেল অ্যাক্সেস", callback_data="tsx:chan"), B("🗄 DB চেক", callback_data="tsx:db")],
          [B("🧪 ড্রাই-রান (শেষ এপিসোড)", callback_data="tsx:dry"), B("⚡ স্পিড আনুমানিক", callback_data="tsx:spd")],
          [B("🔎 ডিটেক্ট টেস্ট", callback_data="tsx:det"), B("🏷 অ্যালিয়াস টেস্ট", callback_data="tsx:ali")],
          [B("🎬 এপিসোড টেস্ট (আমাকে)", callback_data="tsx:ep"), B("👁 ইউজার ভিউ", callback_data="ad:vu")], [B("↩️", callback_data="ad:home")]]
    return "🧪 <b>টেস্ট স্যুট</b>", M(kb)


@acommand("test")
async def cmd_test(update, ctx):
    await reply(update, *test_menu())


@aroute("tsx")
async def cb_tsx(update, ctx, q, p):
    aid = q.from_user.id
    if len(p) < 2:
        await show(update, ctx, *test_menu())
        return await ans(q)
    act = p[1]
    if act == "ping":
        t0 = time.monotonic()
        await ctx.bot.get_me()
        return await ans(q, f"🏓 {int((time.monotonic() - t0) * 1000)} ms", True)
    if act == "tok":
        me = await ctx.bot.get_me()
        return await ans(q, f"✅ @{me.username} (id {me.id})", True)
    if act == "chan":
        try:
            ch = await ctx.bot.get_chat(LOG_CHANNEL_ID)
            m = await ctx.bot.get_chat_member(LOG_CHANNEL_ID, ctx.bot.id)
            return await ans(q, f"✅ {ch.title} • বটের ভূমিকা: {m.status}", True)
        except TelegramError as e:
            return await ans(q, f"❌ {str(e)[:150]}", True)
    if act == "db":
        r = db.execute("PRAGMA integrity_check").fetchone()[0]
        return await ans(q, f"🗄 {r} • ইউজার {V('SELECT COUNT(*) FROM users')} • এপিসোড {V('SELECT COUNT(*) FROM eps')}", True)
    if act == "spd":
        n = V("SELECT COUNT(DISTINCT user_id) FROM follows")
        rate = float(gs("send_rate") or SEND_RATE)
        return await ans(q, f"⚡ {n} জনকে পাঠাতে ≈ {fmt_td(n / rate)} (রেট {rate:g}/সে)", True)
    if act == "dry":
        ep = Q1("SELECT id FROM eps WHERE status='published' ORDER BY id DESC LIMIT 1")
        if not ep:
            return await ans(q, "এপিসোড নেই", True)
        st = await broadcast_episode(Ctx(ctx.application), ep["id"], dry=True)
        return await ans(q, f"🧪 ড্রাই-রান: মোট {st['total']} • এখনই {st.get('would_send', 0)} • কিউতে {st['queued']} • স্কিপ {st['skipped']}", True)
    if act == "ep":
        await send_test_video(ctx, aid, q)
        return
    if act == "det":
        set_await(ctx, "t_detect")
        await reply(update, "🔎 একটি ক্যাপশন/ফাইলনেম লিখুন (যেমন: <code>কৃষ্ণকলি Episode 45 720p</code>)।")
        return await ans(q)
    if act == "ali":
        set_await(ctx, "t_alias")
        await reply(update, "🏷 একটি নাম/বানান লিখুন — কোন সিরিয়াল মিলছে দেখাব।")
        return await ans(q)


@awaiting("t_detect")
async def aw_t_detect(update, ctx, payload):
    t = (update.effective_message.text or "").translate(_BNDIG)
    blob = " " + norm(t) + " "
    ep = parse_ep_no([t])
    qu = parse_quality([t])[0]
    res = search_serials(t, include_draft=True, min_score=60)[:3]
    names = ", ".join(f"{esc(Q1('SELECT name FROM serials WHERE id=?', s)['name'])} ({sc:.0f})" for sc, s in res) or "—"
    await reply(update, f"🔎 <b>ফলাফল</b>\n📺 সিরিয়াল: {names}\n🎬 এপিসোড: {ep if ep is not None else '—'}\n🎞 কোয়ালিটি: {qu or '—'}")


@awaiting("t_alias")
async def aw_t_alias(update, ctx, payload):
    res = search_serials(update.effective_message.text or "", include_draft=True)[:5]
    await reply(update, "🏷 <b>মিল</b>\n" + ("\n".join(f"• {esc(Q1('SELECT name FROM serials WHERE id=?', s)['name'])} — {sc:.0f}" for sc, s in res) or "কিছু মেলেনি"))


# ================================================================ A/B ওয়েলকাম টেস্ট
@aroute("abt")
async def cb_abt(update, ctx, q, p):
    if p[1] == "t":
        ss("ab_on", "0" if gs("ab_on") == "1" else "1")
        alog(q.from_user.id, "ab_toggle", gs("ab_on"))
        await show(update, ctx, *ed_menu("w"))
        return await ans(q, "✅")
    rows = {}
    for v in ("a", "b"):
        n = V("SELECT COUNT(*) FROM users WHERE ab=?", v)
        f = V("SELECT COUNT(*) FROM users u WHERE u.ab=? AND EXISTS (SELECT 1 FROM follows f WHERE f.user_id=u.user_id)", v)
        pr = V("SELECT COUNT(*) FROM users u WHERE u.ab=? AND (u.plan_until=-1 OR u.plan_until>?)", v, now())
        rows[v] = (n, f, pr)
    def pct(a, b): return f"{a * 100 // b}%" if b else "—"
    txt = ("🅰️🅱️ <b>ওয়েলকাম A/B ফলাফল</b>\n(নতুন ইউজার: জোড় আইডি = A, বিজোড় = B)\n\n"
           f"🅰️ A: {rows['a'][0]} জন • ফলো করেছে {rows['a'][1]} ({pct(rows['a'][1], rows['a'][0])}) • Premium {rows['a'][2]}\n"
           f"🅱️ B: {rows['b'][0]} জন • ফলো করেছে {rows['b'][1]} ({pct(rows['b'][1], rows['b'][0])}) • Premium {rows['b'][2]}\n\n"
           "ভ্যারিয়েন্ট B-র টেক্সট এডিট করতে: ✏️ ওয়েলকাম → 🅱️।")
    await show(update, ctx, txt, M([[B("↩️", callback_data="evt:w")]]))
    await ans(q)


# ================================================================ ওয়েলকাম/টেক্সট শিডিউল
@aroute("wsc")
async def cb_wsc(update, ctx, q, p):
    try:
        lst = json.loads(gs("wsched") or "[]")
    except ValueError:
        lst = []
    if p[1] == "n":
        set_await(ctx, "ws_time", (p[2], p[3]))
        await reply(update, "⏰ কখন চালু হবে? ফরম্যাট: <code>2026-11-05 08:00</code>\n(তখন নতুন টেক্সট নিজে থেকে বসবে)")
        return await ans(q)
    if p[1] == "d":
        i = int(p[2])
        if 0 <= i < len(lst):
            lst.pop(i)
            ss("wsched", json.dumps(lst, ensure_ascii=False))
    kb = [[B(f"🗑 {fmt_ts(x['at'])} {x['key']}/{x['lang']}", callback_data=f"wsc:d:{i}")] for i, x in enumerate(lst)] + [[B("↩️", callback_data="evt:w")]]
    await show(update, ctx, f"⏰ <b>শিডিউল করা টেক্সট</b> ({len(lst)})\nনতুন যোগ করতে এডিটরে গিয়ে ⏰ চাপুন।", M(kb))
    await ans(q)


@awaiting("ws_time")
async def aw_ws_time(update, ctx, payload):
    try:
        ts = int(datetime.strptime((update.effective_message.text or "").strip().translate(_BNDIG), "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())
    except ValueError:
        set_await(ctx, "ws_time", payload)
        return await reply(update, "❌ ফরম্যাট: <code>2026-11-05 08:00</code>")
    set_await(ctx, "ws_text", (payload[0], payload[1], ts))
    await reply(update, "✏️ এখন যে টেক্সট বসবে সেটা পাঠান।")


@awaiting("ws_text")
async def aw_ws_text(update, ctx, payload):
    k, lang, ts = payload
    t = update.effective_message.text_html or ""
    errs = validate_text(t)
    if errs:
        set_await(ctx, "ws_text", payload)
        return await reply(update, "⚠️ " + "\n".join(errs))
    try:
        lst = json.loads(gs("wsched") or "[]")
    except ValueError:
        lst = []
    lst.append(dict(at=ts, key=k, lang=lang, body=t))
    ss("wsched", json.dumps(lst, ensure_ascii=False))
    alog(update.effective_user.id, "wsched", f"{k}/{lang}", fmt_ts(ts))
    await reply(update, f"✅ {fmt_ts(ts)}-এ {k}/{lang} বদলাবে।")


async def tick_wsched(ctx):
    raw = gs("wsched")
    if not raw or raw == "[]":
        return
    try:
        lst = json.loads(raw)
    except ValueError:
        return
    due = [x for x in lst if x["at"] <= now()]
    if not due:
        return
    for x in due:
        save_text(x["key"], x["lang"], x["body"], by=0)
        await alert_admins(ctx, f"⏰ শিডিউল অনুযায়ী <b>{x['key']}/{x['lang']}</b> টেক্সট বদলানো হয়েছে।")
    ss("wsched", json.dumps([x for x in lst if x["at"] > now()], ensure_ascii=False))


# ================================================================ অটোপ্লে অ্যাপ্রুভাল
@aroute("apv")
async def cb_apv(update, ctx, q, p):
    act, ep_id = p[1], int(p[2])
    if act == "ok":
        ok = await publish_episode(ctx, ep_id, force=True)
        alog(q.from_user.id, "approve_send", ep_id)
        txt = "✅ অনুমোদিত — পাঠানো শুরু" if ok else "আগেই পাঠানো/বাতিল হয়েছে"
    elif act == "w":
        X("UPDATE eps SET publish_at=? WHERE id=? AND status='pending'", now() + 3600, ep_id)
        _ASKED.discard(ep_id)
        txt = "⏳ এক ঘণ্টা পরে আবার জিজ্ঞাসা করা হবে"
    else:
        X("UPDATE eps SET status='held' WHERE id=? AND status='pending'", ep_id)
        alog(q.from_user.id, "approve_no", ep_id)
        txt = "🚫 এই এপিসোড কাউকে পাঠানো হবে না"
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await reply(update, txt)
    await ans(q)


# ================================================================ লিংক/বাটন হেলথ চেকার
def _http_check(url):
    import urllib.request
    try:
        rq = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 AutoSerialBot"})
        with urllib.request.urlopen(rq, timeout=8) as r:
            return r.status
    except Exception as e:
        return getattr(e, "code", None) or type(e).__name__


@acommand("linkcheck")
async def cmd_linkcheck(update, ctx):
    await reply(update, "🔎 চেক করা হচ্ছে… (১০–৩০ সেকেন্ড)")
    urls = {}
    if CHANNEL_LINK:
        urls["CHANNEL_LINK"] = CHANNEL_LINK
    if gs("faq_url"):
        urls["FAQ"] = gs("faq_url")
    if ADMIN_CONTACT.startswith("@"):
        urls["ADMIN_CONTACT"] = f"https://t.me/{ADMIN_CONTACT[1:]}"
    for r in Q("SELECT k, lang, btns FROM texts"):
        try:
            for b in json.loads(r["btns"] or "[]"):
                if len(b) > 1:
                    urls[f"বাটন {r['k']}/{r['lang']}: {b[0]}"] = b[1]
        except ValueError:
            pass
    for r in Q("SELECT k, lang, body FROM texts"):
        for u in re.findall(r'href="(https?://[^"]+)"', r["body"] or ""):
            urls[f"লিংক {r['k']}/{r['lang']}"] = u
    loop = asyncio.get_running_loop()
    res = await asyncio.gather(*(loop.run_in_executor(None, _http_check, u) for u in urls.values()))
    lines = ["🔗 <b>লিংক রিপোর্ট</b>"]
    for (k, u), st in zip(urls.items(), res):
        ok = isinstance(st, int) and st < 400
        lines.append(f"{'✅' if ok else '❌'} {esc(k)} → {st}\n   {esc(u[:70])}")
    if not urls:
        lines.append("চেক করার মতো লিংক সেট করা নেই।")
    lines.append("\n🔘 <b>হোম বাটন</b>")
    order, off = layout()
    for k in order:
        pre = HOME_BTNS[k][2].split(":")[0]
        lines.append(f"{'✅' if pre in ROUTES else '❌'} {HOME_BTNS[k][0]} → {pre}{' (লুকানো)' if k in off else ''}")
    lines.append(f"\n🤖 ডিপ-লিংক: https://t.me/{ctx.bot.username}")
    ss("link_report", f"{now()}")
    await reply(update, "\n".join(lines)[:4000])


@aroute("lkc")
async def cb_lkc(update, ctx, q, p):
    await ans(q)
    await cmd_linkcheck(update, ctx)


# ================================================================ প্রোমো সোর্স (ZEE5/SonyLIV ইত্যাদি পেজ থেকে অটো)
def _fetch_meta(url):
    import urllib.request, html as H
    try:
        rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AutoSerialBot)", "Accept-Language": "en,bn"})
        with urllib.request.urlopen(rq, timeout=12) as r:
            raw = r.read(400000).decode("utf-8", "ignore")
    except Exception as e:
        return {"err": str(getattr(e, "code", e))}
    out = {}
    for k in ("og:title", "og:description", "og:image", "og:video", "og:video:url"):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']+)' % re.escape(k), raw, re.I) or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(k), raw, re.I)
        if m:
            out[k] = H.unescape(m.group(1))
    if "og:title" not in out:
        m = re.search(r"<title>([^<]+)</title>", raw, re.I)
        if m:
            out["og:title"] = H.unescape(m.group(1).strip())
    return out


async def fetch_promo_src(ctx, sid, url, notify=True):
    meta = await asyncio.get_running_loop().run_in_executor(None, _fetch_meta, url)
    if "err" in meta or not meta.get("og:title"):
        return None, meta.get("err", "no-title")
    try:
        st = json.loads(gs("promo_state") or "{}")
    except ValueError:
        st = {}
    sig = meta.get("og:title", "") + "|" + meta.get("og:image", "") + "|" + meta.get("og:description", "")[:80]
    if st.get(url) == sig:
        return 0, "unchanged"
    st[url] = sig
    ss("promo_state", json.dumps(st, ensure_ascii=False))
    img = meta.get("og:image", "")
    X("INSERT INTO promos (serial_id, message_id, file_id, kind, caption, created_at, status, prem_only, link) VALUES (?,?,?,?,?,?,?,?,?)",
      sid, 0, img, "photo" if img else "text", (meta.get("og:title", "") + "\n" + meta.get("og:description", ""))[:300], now(), "live", gi("promo_prem"), meta.get("og:video") or meta.get("og:video:url") or url)
    pid = V("SELECT last_insert_rowid()")
    if notify:
        spawn(promo_announce(ctx, pid))
    return pid, "new"


async def tick_promosrc(ctx):
    if now() - gi("promo_last") < 6 * 3600:
        return
    ss("promo_last", now())
    try:
        srcs = json.loads(gs("promo_srcs") or "[]")
    except ValueError:
        return
    for s in srcs:
        try:
            pid, st = await fetch_promo_src(ctx, s["sid"], s["url"])
            if pid:
                await alert_admins(ctx, f"🎬 অটো-প্রোমো #{pid} ({esc(V('SELECT name FROM serials WHERE id=?', s['sid'], d=''))}) যোগ হয়েছে।")
        except Exception:
            log.exception("promo src failed")


@acommand("promosrc")
async def cmd_promosrc(update, ctx):
    try:
        srcs = json.loads(gs("promo_srcs") or "[]")
    except ValueError:
        srcs = []
    a = ctx.args
    if len(a) >= 3 and a[0] == "add" and a[1].isdigit() and a[2].startswith("http"):
        srcs.append(dict(sid=int(a[1]), url=a[2]))
        ss("promo_srcs", json.dumps(srcs))
        return await reply(update, "✅ সোর্স যোগ হয়েছে। প্রতি ৬ ঘণ্টায় চেক হবে। এখনই চেক: <code>/promosrc run</code>")
    if len(a) >= 2 and a[0] == "del" and a[1].isdigit() and int(a[1]) < len(srcs):
        srcs.pop(int(a[1]))
        ss("promo_srcs", json.dumps(srcs))
        return await reply(update, "🗑 মুছেছে")
    if a and a[0] == "run":
        out = []
        for s in srcs:
            pid, st = await fetch_promo_src(ctx, s["sid"], s["url"])
            out.append(f"• {esc(s['url'][:50])} → {st}{' #' + str(pid) if pid else ''}")
        return await reply(update, "🎬 <b>ফলাফল</b>\n" + ("\n".join(out) or "কোনো সোর্স নেই"))
    await reply(update, "🎬 <b>অটো প্রোমো সোর্স</b>\n" + ("\n".join(f"{i}. সিরিয়াল #{s['sid']} — {esc(s['url'][:60])}" for i, s in enumerate(srcs)) or "কোনো সোর্স নেই") +
                "\n\nযোগ: <code>/promosrc add সিরিয়াল_আইডি https://পেজ-লিংক</code>\nমুছুন: <code>/promosrc del নম্বর</code>\nএখনই চেক: <code>/promosrc run</code>\n\n"
                "ℹ️ পেজের og:title/og:image/og:video মেটা থেকে প্রোমো বানায়। যে সাইট বট ব্লক করে বা JS দিয়ে লোড হয় সেখানে কাজ নাও করতে পারে।")


# ================================================================ SMS অটো-ভেরিফাই (Termux:API)
SMS_SENDERS = ("bkash", "nagad", "rocket", "16216", "16167", "dbbl")


async def read_sms():
    try:
        p = await asyncio.create_subprocess_exec("termux-sms-list", "-l", "40", "-t", "inbox", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 20)
        if p.returncode != 0:
            return None, (err or b"").decode()[:150]
        return json.loads(out.decode() or "[]"), ""
    except FileNotFoundError:
        return None, "termux-sms-list পাওয়া যায়নি (pkg install termux-api + Termux:API অ্যাপ)"
    except Exception as e:
        return None, str(e)[:150]


def parse_sms(body):
    trx = re.search(r"(?:TrxID|TxnID|Trx ID|TrxId)[:\s]*([A-Z0-9]{6,20})", body, re.I)
    amt = re.search(r"(?:Tk|BDT|৳)\s*([\d,]+(?:\.\d+)?)", body, re.I)
    return (trx.group(1).upper() if trx else ""), (float(amt.group(1).replace(",", "")) if amt else 0.0)


_SMS_WARNED = {"v": 0}


async def tick_sms(ctx):
    if gs("sms_verify") != "1":
        return
    pend = Q("SELECT * FROM payments WHERE status='pending' AND trx<>''")
    if not pend:
        return
    msgs, err = await read_sms()
    if msgs is None:
        if now() - _SMS_WARNED["v"] > 3600:
            _SMS_WARNED["v"] = now()
            await alert_admins(ctx, f"⚠️ SMS অটো-ভেরিফাই কাজ করছে না: {esc(err)}")
        return
    got = {}
    for m in msgs:
        sender = str(m.get("number", "")).lower()
        body = m.get("body", "")
        if not any(s in sender for s in SMS_SENDERS) and not re.search(r"bkash|nagad|rocket", body, re.I):
            continue
        t, a = parse_sms(body)
        if t:
            got[t] = a
    for r in pend:
        a = got.get(r["trx"])
        if a is None:
            continue
        if a + 0.5 < r["amount"]:
            await alert_admins(ctx, f"⚠️ SMS-এ TrxID <code>{esc(r['trx'])}</code> মিলেছে কিন্তু পরিমাণ কম ({a} < {r['amount']}) — পেমেন্ট #{r['id']} ম্যানুয়ালি দেখুন।")
            continue
        await auto_approve(ctx, r["id"], f"sms {a}")


async def auto_approve(ctx, pid, note):
    r = Q1("SELECT * FROM payments WHERE id=? AND status='pending'", pid)
    if not r:
        return
    basic = r["plan"].startswith("b")
    pl = r["plan"][1:] if basic else r["plan"]
    old = user(r["user_id"])["plan_until"]
    until = set_plan(r["user_id"], "life" if pl == "life" else int(pl), 0, f"auto#{pid}", "basic" if basic else "pro")
    X("UPDATE payments SET status='approved', decided_at=?, decided_by=0, reason=? WHERE id=?", now(), note, pid)
    alog(0, "pay_auto", pid, f"{r['user_id']} {r['amount']} {note}", undo={"t": "plan", "uid": r["user_id"], "until": old})
    await notify_plan(ctx, r["user_id"], until)
    await alert_admins(ctx, f"🤖 পেমেন্ট #{pid} SMS মিলিয়ে অটো-অনুমোদিত ✅ ({r['amount']}৳, ইউজার <code>{r['user_id']}</code>)")


@acommand("smscheck")
async def cmd_smscheck(update, ctx):
    msgs, err = await read_sms()
    if msgs is None:
        return await reply(update, f"❌ {esc(err)}\n\nসেটআপ: <code>pkg install termux-api</code> + Play/F-Droid থেকে <b>Termux:API</b> অ্যাপ ইনস্টল ও SMS পারমিশন দিন।")
    rows = [(m.get("number"), parse_sms(m.get("body", ""))) for m in msgs]
    ok = [x for x in rows if x[1][0]]
    await reply(update, f"📩 inbox থেকে {len(msgs)}টি SMS পড়া গেছে, TrxID সহ {len(ok)}টি।\n" + "\n".join(f"• {esc(str(n))}: {t} / {a}৳" for n, (t, a) in ok[:8]) +
                f"\n\nঅটো-ভেরিফাই: {'✅ চালু' if gs('sms_verify') == '1' else '❌ বন্ধ — /admin → সেটিংস থেকে চালু করুন'}")


# ================================================================ ডিসপ্যাচার ও main()
UNKNOWN = "❓ অজানা কমান্ড। /help দেখুন।"
ROUTES_NOADMIN_OK = {"lb"}  # lb-এর ভেতরে নিজস্ব অ্যাডমিন চেক আছে


async def on_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m or not m.text:
        return
    parts = m.text.split()
    name = parts[0][1:].split("@")[0].lower()
    ctx.args = parts[1:]
    ent = CMDS.get(name)
    uid = update.effective_user.id
    if not ent or (ent[1] and not is_admin(uid)):
        return await reply(update, L(lg(uid), UNKNOWN, "❓ Unknown command. See /help."))
    ctx.user_data.pop("aw", None)
    try:
        await ent[0](update, ctx)
    except Exception:
        log.exception("command /%s failed", name)
        await reply(update, "⚠️ একটি ত্রুটি হয়েছে। আবার চেষ্টা করুন।")


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    p = (q.data or "").split(":")
    fn = ROUTES.get(p[0])
    if not fn:
        return await ans(q)
    if p[0] in ADMIN_PFX and not is_admin(q.from_user.id):
        return await ans(q, "⛔", True)
    try:
        await fn(update, ctx, q, p)
    except ApplicationHandlerStop:
        raise
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("callback %s: %s", q.data, e)
        await ans(q)
    except Exception:
        log.exception("callback %s failed", q.data)
        await ans(q, "⚠️ ত্রুটি, আবার চেষ্টা করুন", True)


RK_MAP = {"📺 সিরিয়াল": "serials", "📺 Serials": "serials", "📌 আমার ফিড": "myfeeds", "📌 My feed": "myfeeds", "🔍 সার্চ": "search", "🔍 Search": "search", "▶️ Continue": "continue"}


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m:
        return
    aw = ctx.user_data.pop("aw", None)
    if aw:
        fn = AW.get(aw[0])
        if fn:
            try:
                return await fn(update, ctx, aw[1])
            except Exception:
                log.exception("await %s failed", aw[0])
                return await reply(update, "⚠️ একটি ত্রুটি হয়েছে।")
    t = (m.text or "").strip()
    if not t:
        return
    cmd = RK_MAP.get(t)
    if cmd:
        ctx.args = []
        return await CMDS[cmd][0](update, ctx)
    await do_search(update, ctx, t)


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = update.my_chat_member
    if c and c.chat.type == "private":
        X("UPDATE users SET blocked=? WHERE user_id=?", 1 if c.new_chat_member.status in ("kicked", "left") else 0, c.chat.id)


_ERR = {"n": 0, "t": 0}


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    err = ctx.error
    if isinstance(err, (NetworkError,)):
        log.warning("network error: %s", err)
        return
    log.error("unhandled error", exc_info=err)
    t = now()
    _ERR["n"] = _ERR["n"] + 1 if t - _ERR["t"] < 600 else 1
    _ERR["t"] = t
    if _ERR["n"] in (5, 50):
        spawn(alert_admins(Ctx(ctx.application), f"🚨 গত ১০ মিনিটে {_ERR['n']}টি এরর: <code>{esc(str(err)[:200])}</code>"))


USER_CMDS = [("start", "🏠 হোম মেনু"), ("serials", "📺 সিরিয়াল তালিকা"), ("myfeeds", "📌 আমার ফিড"), ("search", "🔍 সার্চ"), ("saved", "⭐ সেভ করা"), ("history", "🕘 ইতিহাস"),
             ("continue", "▶️ Continue Watching"), ("trending", "🔥 ট্রেন্ডিং"), ("notification", "🔔 নোটিফিকেশন"), ("autoplay", "▶️ অটোপ্লে"), ("settings", "⚙️ সেটিংস"),
             ("plan", "💎 প্ল্যান"), ("tkply", "💳 প্ল্যান কিনুন"), ("support", "📩 সাপোর্ট"), ("mytickets", "🎫 আমার টিকেট"), ("terms", "📜 নিয়ম"), ("help", "❓ হেল্প"), ("cancel", "✖️ বাতিল")]
ADMIN_CMDS = USER_CMDS + [("admin", "🛠 এডমিন প্যানেল"), ("stats", "📊 স্ট্যাটস ও রিস্টার্ট"), ("updates", "🆕 আপডেট বোর্ড"), ("addfeed", "➕ সিরিয়াল যোগ"), ("broadcast", "📢 ব্রডকাস্ট"),
                          ("users", "👥 ইউজার"), ("giveprem", "💎 প্রিমিয়াম দিন"), ("removeprem", "❌ প্রিমিয়াম সরান"), ("payments", "💳 পেমেন্ট"), ("tickets", "🎫 টিকেট"),
                          ("setwelcome", "✏️ ওয়েলকাম এডিট"), ("setterms", "📜 টার্মস এডিট"), ("editvideo", "🎞 ক্যাপশন এডিট"), ("testvideo", "👁 ভিডিও প্রিভিউ"), ("test", "🧪 টেস্ট"),
                          ("poll", "📊 পোল"), ("linkcheck", "🔗 লিংক চেক"), ("promosrc", "🎬 প্রোমো সোর্স"), ("smscheck", "📩 SMS টেস্ট"), ("ban", "🚫 ব্যান"), ("unban", "✅ আনব্যান")]


async def post_init(app: Application):
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in USER_CMDS], scope=BotCommandScopeDefault())
        for a in ADMIN_IDS:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in ADMIN_CMDS], scope=BotCommandScopeChat(a))
    except TelegramError as e:
        log.warning("set_my_commands failed: %s", e)
    me = await app.bot.get_me()
    log.info("%s v%s চালু: @%s • ইউজার %s • সিরিয়াল %s", BOT_NAME, VERSION, me.username, V("SELECT COUNT(*) FROM users"), V("SELECT COUNT(*) FROM serials"))
    if gs("send_rate"):
        try:
            LIM.gap = 1.0 / max(1.0, float(gs("send_rate")))
        except ValueError:
            pass
    try:
        await app.bot.get_chat(LOG_CHANNEL_ID)
    except TelegramError as e:
        log.error("লগ চ্যানেল পাওয়া যাচ্ছে না (বট কি অ্যাডমিন?): %s", e)
        for a in ADMIN_IDS:
            await tsend(app.bot, a, f"⚠️ লগ চ্যানেল অ্যাক্সেস করা যাচ্ছে না। বটকে চ্যানেলের অ্যাডমিন করুন।\n<code>{esc(str(e))}</code>")
    chat = gs("restart_chat")
    if chat.isdigit():
        ss("restart_chat", "")
        await tsend(app.bot, int(chat), f"✅ <b>{BOT_NAME}</b> রিস্টার্ট সম্পন্ন (v{VERSION})")
    spawn(ticker(app))


def main():
    req = HTTPXRequest(connection_pool_size=80, read_timeout=30, write_timeout=30, connect_timeout=15, pool_timeout=20)
    app = (Application.builder().token(BOT_TOKEN).defaults(Defaults(parse_mode=ParseMode.HTML)).request(req)
           .concurrent_updates(256).post_init(post_init).build())
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST & filters.Chat(LOG_CHANNEL_ID), on_channel_post))
    app.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.PRIVATE, on_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline))
    from telegram.ext import ChatMemberHandler
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION), on_message))
    app.add_error_handler(on_error)
    log.info("পোলিং শুরু…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
