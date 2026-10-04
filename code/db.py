"""لایه دیتابیس (SQLite) برای لیست اصلی و اطلاعات بلو بانک.

مسیر دیتابیس:  <ریشه پروژه>/database/blu.db
فقط «لیست اصلی» ذخیره می‌شود؛ لیست فرعی عمداً ذخیره نمی‌شود و با هر بار اجرای
app.py از روی لیست اصلی ساخته می‌شود.
"""
import os
import re
import sqlite3
from contextlib import contextmanager

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
    }


# ---------- خروجی برای اسکریپت سلنیوم ----------
def _digits(number):
    return re.sub(r"\D", "", str(number))


def is_real_card(number):
    """شماره‌ی واقعی یعنی دقیقاً ۱۶ رقم (XXXX-... رد می‌شود)."""
    return bool(re.fullmatch(r"\d{16}", _digits(number))) and "X" not in str(number).upper()


def cards_to_run_data(cards):
    """لیست دیکشنری کارت‌ها را به [(شماره کارت ۱۶ رقمی، نام مالک), ...] برای run() تبدیل می‌کند."""
    return [
        (_digits(c.get("number", "")), c.get("owner") or PLACEHOLDER_OWNER)
        for c in cards
        if is_real_card(c.get("number", ""))
    ]


def get_run_data():
    """لیست اصلی به شکل [(شماره کارت ۱۶ رقمی، نام مالک), ...] مناسب پارامتر data در run()."""
    return cards_to_run_data(get_cards("primary"))


def get_credentials():
    return get_setting("username"), get_setting("password")


init_db()
