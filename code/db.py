"""لایه دیتابیس (SQLite) برای لیست اصلی و اطلاعات بلو بانک.

مسیر دیتابیس:  <ریشه پروژه>/database/blu.db
فقط «لیست اصلی» ذخیره می‌شود؛ لیست فرعی عمداً ذخیره نمی‌شود و با هر بار اجرای
app.py از روی لیست اصلی ساخته می‌شود.
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

# ایران ساعت تابستانی ندارد؛ منطقهٔ زمانی ثابت ۳:۳۰+
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CODE_DIR)
DB_PATH = os.path.join(ROOT_DIR, "database", "blu.db")

# فقط لیست اصلی در دیتابیس ذخیره می‌شود؛ لیست فرعی عمداً ذخیره نمی‌شود
TABLES = {"primary": "primary_cards"}

PLACEHOLDER_OWNER = "مالک حساب"


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        for table in TABLES.values():
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                    pos       INTEGER NOT NULL,
                    client_id INTEGER NOT NULL,
                    number    TEXT    NOT NULL,
                    owner     TEXT    NOT NULL
                )"""
            )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        # سوابق تراکنش‌ها (موفق/ناموفق) با تاریخ شمسی
        conn.execute(
            """CREATE TABLE IF NOT EXISTS history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL    NOT NULL,
                jdate      TEXT    NOT NULL,
                status     TEXT    NOT NULL,
                number     TEXT    NOT NULL,
                owner      TEXT    NOT NULL,
                amount     INTEGER NOT NULL,
                message    TEXT    NOT NULL DEFAULT ''
            )"""
        )
        # ستون روش انتقال (blu | card | paya) برای دیتابیس‌های قدیمی اضافه می‌شود
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(history)").fetchall()]
        if "method" not in cols:
            conn.execute("ALTER TABLE history ADD COLUMN method TEXT NOT NULL DEFAULT ''")


# ---------- کارت‌ها ----------
def _clean_cards(cards):
    """ورودی لیست دیکشنری‌هاست؛ خروجی ردیف‌های آماده‌ی ذخیره: (pos, client_id, number, owner).
    اگر ورودی ساختار درستی نداشته باشد ValueError می‌دهد."""
    if not isinstance(cards, list):
        raise ValueError("لیست کارت‌ها باید آرایه باشد")
    rows = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            raise ValueError(f"کارت شمارهٔ {i + 1} نامعتبر است")
        try:
            client_id = int(c.get("id", i + 1))
        except (TypeError, ValueError):
            raise ValueError(f"شناسهٔ کارت شمارهٔ {i + 1} نامعتبر است")
        number = str(c.get("number", "")).strip()
        owner = str(c.get("owner", "")).strip() or PLACEHOLDER_OWNER
        rows.append((i, client_id, number, owner))
    return rows


def save_primary(cards):
    """لیست اصلی را کامل جایگزین می‌کند (ویرایش، افزودن و حذف همه در یک تراکنش).
    فقط وقتی کاربر در پروفایل «ذخیره» بزند صدا زده می‌شود. هر کارت با شماره و نام مالک ذخیره می‌شود."""
    rows = _clean_cards(cards)
    table = TABLES["primary"]
    with connect() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (pos, client_id, number, owner) VALUES (?, ?, ?, ?)", rows
        )


def get_cards(list_name="primary"):
    table = TABLES[list_name]
    with connect() as conn:
        rows = conn.execute(
            f"SELECT client_id, number, owner FROM {table} ORDER BY pos"
        ).fetchall()
    return [{"id": r["client_id"], "number": r["number"], "owner": r["owner"]} for r in rows]


# ---------- تنظیمات ----------
def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_setting(key, default=""):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_state():
    return {
        "primary": get_cards("primary"),
        "username": get_setting("username"),
        "password": get_setting("password"),
        "nextId": int(get_setting("nextId", "1") or 1),
        "showBrowser": get_setting("show_browser", "0") == "1",
        "transferMethod": get_transfer_method(),
    }


# ---------- روش انتقال ----------
TRANSFER_METHODS = ("card", "paya")  # card = کارت به کارت عادی، paya = بین بانکی (پایا)
DEFAULT_TRANSFER_METHOD = "card"


def normalize_method(value, default=""):
    value = str(value or "").strip().lower()
    return value if value in TRANSFER_METHODS else default


def get_transfer_method():
    """روش پیش‌فرض انتقال که در تنظیمات انتخاب شده (card | paya)."""
    return normalize_method(get_setting("transfer_method", ""), DEFAULT_TRANSFER_METHOD)


# ---------- تاریخ شمسی ----------
def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100)
            + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_text(ts=None):
    """زمان (epoch) را به رشتهٔ «۱۴۰۵/۰۷/۱۲ 16:12» به وقت تهران تبدیل می‌کند (ارقام لاتین)."""
    dt = datetime.fromtimestamp(ts, TEHRAN_TZ) if ts is not None else datetime.now(TEHRAN_TZ)
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d} {dt.hour:02d}:{dt.minute:02d}"


# ---------- سوابق ----------
def add_history(status, number, owner, amount, message="", method=""):
    """یک ردیف سابقه ذخیره می‌کند. status: success | failed ؛ method: blu | card | paya"""
    import time as _time
    now = _time.time()
    with connect() as conn:
        conn.execute(
            "INSERT INTO history (created_at, jdate, status, number, owner, amount, message, method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, jalali_text(now), status, str(number), str(owner or PLACEHOLDER_OWNER),
             int(amount or 0), str(message or ""), str(method or "")),
        )


def get_history(limit=1000):
    """جدیدترین‌ها اول."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, jdate, status, number, owner, amount, message, method "
            "FROM history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_history(item_id):
    """یک سابقه را با شناسه حذف می‌کند؛ تعداد ردیف حذف‌شده را برمی‌گرداند."""
    with connect() as conn:
        return conn.execute("DELETE FROM history WHERE id = ?", (int(item_id),)).rowcount


def clear_history():
    """همهٔ سوابق را پاک می‌کند؛ تعداد ردیف حذف‌شده را برمی‌گرداند."""
    with connect() as conn:
        return conn.execute("DELETE FROM history").rowcount


# ---------- خروجی برای اسکریپت سلنیوم ----------
def _digits(number):
    return re.sub(r"\D", "", str(number))


def is_real_card(number):
    """شماره‌ی واقعی یعنی دقیقاً ۱۶ رقم (XXXX-... رد می‌شود)."""
    return bool(re.fullmatch(r"\d{16}", _digits(number))) and "X" not in str(number).upper()


def cards_to_run_data(cards):
    """لیست دیکشنری کارت‌ها را به [(شماره کارت ۱۶ رقمی، نام مالک، مبلغ یا 0، روش یا ""), ...] برای run() تبدیل می‌کند.
    مبلغ 0 یعنی مبلغ پیش‌فرض؛ روش خالی یعنی روش پیش‌فرض تنظیمات."""
    data = []
    for c in cards:
        if not is_real_card(c.get("number", "")):
            continue
        amount = _digits(c.get("amount", ""))
        data.append((
            _digits(c.get("number", "")),
            c.get("owner") or PLACEHOLDER_OWNER,
            int(amount) if amount and int(amount) > 0 else 0,
            normalize_method(c.get("method", "")),
        ))
    return data


def get_run_data():
    """لیست اصلی به شکل [(شماره کارت ۱۶ رقمی، نام مالک), ...] مناسب پارامتر data در run()."""
    return cards_to_run_data(get_cards("primary"))


def get_credentials():
    return get_setting("username"), get_setting("password")


init_db()
