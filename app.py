# -*- coding: utf-8 -*-
"""سرور Flask پروژه‌ی کارت‌به‌کارت بلو.

- صفحه‌ی templates/index.html را سرو می‌کند.
- لیست اصلی + یوزر/پسورد را در database/blu.db (از طریق code/db.py) نگه می‌دارد.
- code/blu_cart_be_cart.py را در یک thread پس‌زمینه اجرا می‌کند (مرورگر بدون پنجره)،
  وضعیت لحظه‌ای را به صفحه می‌دهد و اگر بلو کد تأیید (OTP) خواست، از کاربر داخل سایت می‌گیرد.

اجرا:  python app.py   ->   http://127.0.0.1:5000

API دیتابیس:
  GET  /api/state                  لیست اصلی + یوزر/پسورد
  POST /api/primary                ذخیره‌ی لیست اصلی (ویرایش/افزودن/حذف)
  POST /api/setting                ذخیره‌ی username / password / nextId

API انتقال:
  POST /api/transfer               {amount, cards:[{id, number, owner}]}  ->  {job_id, status, total}
  GET  /api/transfer/status/<id>   وضعیت کار (برای polling)
  GET  /api/transfer/active        کاری که هنوز در جریان است (برای وصل شدن بعد از رفرش)
  POST /api/transfer/otp           {job_id, otp}  ->  وضعیت کار

وضعیت کل کار (status): pending | running | need_otp | completed | failed
وضعیت هر کارت:        pending | processing | success | failed | skipped
"""
import os
import re
import sys
import threading
import time
import traceback
import uuid

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.join(BASE_DIR, "code")
sys.path.insert(0, CODE_DIR)

import db  # noqa: E402  (code/db.py)

# اگر selenium نصب نباشد یا فایل مشکل داشته باشد، سرور بالا می‌آید و موقع شروع انتقال
# خطای واضح به کاربر نشان داده می‌شود.
try:
    import blu_cart_be_cart as blu  # noqa: E402
    BLU_IMPORT_ERROR = None
except Exception as e:  # pragma: no cover
    blu = None
    BLU_IMPORT_ERROR = e

try:
    from selenium.common.exceptions import TimeoutException, WebDriverException
except Exception:  # pragma: no cover
    TimeoutException = WebDriverException = None

OTP_TIMEOUT = 180  # ثانیه: مهلت وارد کردن OTP توسط کاربر
KEEP_JOBS = 20     # تعداد کارهای تمام‌شده‌ای که در حافظه می‌مانند

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


# ====================== خطاها (همیشه JSON برای /api) ======================
@app.errorhandler(Exception)
def handle_exception(e):
    is_api = request.path.startswith("/api/")
    if isinstance(e, HTTPException):
        if is_api:
            return jsonify(error=e.description), e.code
        return e
    app.logger.error("Unhandled error on %s:\n%s", request.path, traceback.format_exc())
    if is_api:
        return jsonify(error=f"خطای داخلی سرور ({type(e).__name__})"), 500
    return Response("Internal Server Error", status=500)


# ====================== صفحه و دیتابیس ======================
@app.get("/")
def index():
    # فایل HTML دست‌نخورده می‌ماند؛ اسکریپت همگام‌سازی موقع سرو شدن به head اضافه می‌شود
    path = os.path.join(BASE_DIR, "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    tag = '<script src="/static/sync.js"></script>\n</head>'
    html = html.replace("</head>", tag, 1)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/state")
def api_state():
    return jsonify(db.get_state())


@app.post("/api/primary")
def api_primary():
    cards = request.get_json(silent=True)
    if not isinstance(cards, list):
        return jsonify(error="لیست کارت‌ها نامعتبر است"), 400
    try:
        db.save_primary(cards)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, count=len(cards))


@app.post("/api/setting")
def api_setting():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    if key not in ("username", "password", "nextId"):
        return jsonify(error="کلید نامعتبر"), 400
    db.set_setting(key, data.get("value", ""))
    return jsonify(ok=True)


# ====================== انتقال کارت‌به‌کارت ======================
JOBS = {}                 # job_id -> job
JOBS_LOCK = threading.RLock()
ACTIVE = {"job_id": None}  # فقط یک کار هم‌زمان (پروفایل کروم قفل می‌شود)

_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC = "٠١٢٣٤٥٦٧٨٩"
_TO_EN = {ord(c): str(i) for i, c in enumerate(_PERSIAN)}
_TO_EN.update({ord(c): str(i) for i, c in enumerate(_ARABIC)})


def only_digits(value) -> str:
    return re.sub(r"\D", "", str(value or "").translate(_TO_EN))


def format_card(number: str) -> str:
    digits = only_digits(number)[:16]
    if len(digits) != 16:
        return str(number)
    return "-".join(digits[i:i + 4] for i in range(0, 16, 4))


def first_line(e: Exception, limit: int = 200) -> str:
    text = str(e).strip().splitlines()
    return (text[0] if text else "")[:limit]


def friendly_error(e: Exception) -> str:
    """پیام فارسی قابل‌فهم برای نمایش در سایت."""
    if blu is not None:
        if isinstance(e, blu.InsufficientBalance):
            return (f"موجودی حساب کافی نیست (موجودی: {e.balance:,} ریال، "
                    f"حداقل لازم: {e.needed:,} ریال)")
        if isinstance(e, blu.OtpTimeout):
            return "زمان وارد کردن کد تأیید (OTP) تمام شد؛ دوباره تلاش کنید"
    if TimeoutException is not None and isinstance(e, TimeoutException):
        return ("یکی از بخش‌های صفحهٔ بلو در زمان مقرر باز نشد "
                "(اینترنت کند، کد تأیید اشتباه، یا تغییر ظاهر سایت بلو)")
    if WebDriverException is not None and isinstance(e, WebDriverException):
        detail = first_line(e)
        return "اجرای مرورگر یا ارتباط با آن با خطا مواجه شد" + (f": {detail}" if detail else "")
    detail = first_line(e)
    return f"خطای غیرمنتظره ({type(e).__name__})" + (f": {detail}" if detail else "")


def job_payload(job: dict) -> dict:
    """نمای عمومی کار؛ یوزر/پسورد/OTP هرگز در آن نیست."""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "need_otp": job["need_otp"],
        "otp_message": job["otp_message"],
        "current_index": job["current_index"],
        "current_card": job["current_card"],
        "results": [dict(r) for r in job["results"]],
        "success_count": job["success_count"],
        "fail_count": job["fail_count"],
        "total": job["total"],
        "error": job["error"],
    }


def _is_finished(job: dict) -> bool:
    return job["status"] in ("completed", "failed")


def _cleanup_jobs():
    """کارهای تمام‌شده‌ی قدیمی را از حافظه پاک می‌کند (با قفل صدا زده شود)."""
    finished = sorted((j for j in JOBS.values() if _is_finished(j)), key=lambda j: j["created_at"])
    for j in finished[:-KEEP_JOBS]:
        JOBS.pop(j["job_id"], None)


def run_job(job_id: str, username: str, password: str, amount: int):
    job = JOBS[job_id]

    def on_progress(event, index, detail=""):
        with JOBS_LOCK:
            item = job["results"][index]
            if event == "card_start":
                item["status"] = "processing"
                item["message"] = "در حال ارسال..."
                job["current_index"] = index
                job["current_card"] = item["number"]
            elif event == "step":
                item["message"] = detail
            elif event == "card_done":
                item["status"] = "success"
                item["message"] = f"انتقال انجام شد — {detail}" if detail else "انتقال با موفقیت انجام شد"
                job["success_count"] += 1

    def get_otp() -> str:
        # بلو کد خواسته؛ وضعیت را need_otp می‌کنیم تا صفحه مودال OTP را نشان بدهد
        with JOBS_LOCK:
            job["otp_value"] = None
            job["otp_event"].clear()
            job["status"] = "need_otp"
            job["need_otp"] = True
            job["otp_message"] = "کد تأیید (OTP) ارسال‌شده از طرف بلو را وارد کنید"
        got = job["otp_event"].wait(OTP_TIMEOUT)
        with JOBS_LOCK:
            value = job["otp_value"]
            job["otp_value"] = None
            job["need_otp"] = False
            job["otp_message"] = ""
            job["status"] = "running"
            if not got or not value:
                raise blu.OtpTimeout("OTP timeout")
        return value

    try:
        with JOBS_LOCK:
            job["status"] = "running"
            data = db.cards_to_run_data(
                [{"number": r["number"], "owner": r["owner"]} for r in job["results"]]
            )
        blu.run(data, username, password, amount, get_otp=get_otp, on_progress=on_progress)
        with JOBS_LOCK:
            job["status"] = "completed"
            job["current_card"] = None
            job["current_index"] = len(job["results"])
    except Exception as e:
        app.logger.error("Transfer job %s failed:\n%s", job_id, traceback.format_exc())
        message = friendly_error(e)
        with JOBS_LOCK:
            for item in job["results"]:
                if item["status"] == "processing":
                    item["status"] = "failed"
                    item["message"] = message
                    job["fail_count"] += 1
                elif item["status"] == "pending":
                    item["status"] = "skipped"
                    item["message"] = "اجرا متوقف شد"
            job["status"] = "failed"
            job["error"] = message
            job["need_otp"] = False
            job["current_card"] = None
    finally:
        with JOBS_LOCK:
            job["finished_at"] = time.time()
            job["otp_value"] = None
            if ACTIVE["job_id"] == job_id:
                ACTIVE["job_id"] = None
            _cleanup_jobs()


@app.post("/api/transfer")
def api_start_transfer():
    data = request.get_json(force=True, silent=True) or {}

    # --- پیش‌نیازها: همه‌ی خطاها به صورت پیام فارسی به صفحه برمی‌گردد ---
    if blu is None:
        return jsonify(error=f"ماژول انتقال بارگذاری نشد ({type(BLU_IMPORT_ERROR).__name__}: "
                             f"{first_line(BLU_IMPORT_ERROR)}). احتمالاً باید «pip install selenium» بزنید."), 500
    if not os.path.isfile(blu.CHROMEDRIVER_PATH):
        return jsonify(error=f"فایل chromedriver در مسیر {blu.CHROMEDRIVER_PATH} پیدا نشد"), 500

    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        return jsonify(error="لیست کارت‌ها خالی است"), 400

    amount_text = only_digits(data.get("amount"))
    if not amount_text or int(amount_text) <= 0:
        return jsonify(error="مبلغ نامعتبر است"), 400
    amount = int(amount_text)

    results = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict) or not db.is_real_card(c.get("number", "")):
            return jsonify(error=f"شماره کارت ردیف {i + 1} معتبر نیست (باید ۱۶ رقم باشد)"), 400
        results.append({
            "id": c.get("id"),
            "number": format_card(c.get("number", "")),
            "owner": c.get("owner") or db.PLACEHOLDER_OWNER,
            "status": "pending",
            "message": "",
        })

    # یوزر/پسورد از دیتابیس (ذخیره‌شده در پروفایل)؛ اگر خالی بود از درخواست
    db_user, db_pass = db.get_credentials()
    username = db_user or str(data.get("username") or "")
    password = db_pass or str(data.get("password") or "")
    if not username or not password:
        return jsonify(error="یوزرنیم یا پسورد بلو ذخیره نشده است؛ ابتدا در پروفایل ذخیره کنید"), 400

    with JOBS_LOCK:
        active_id = ACTIVE["job_id"]
        if active_id and active_id in JOBS and not _is_finished(JOBS[active_id]):
            return jsonify(error="یک انتقال دیگر هنوز در حال اجراست", job_id=active_id), 409

        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "need_otp": False,
            "otp_message": "",
            "otp_event": threading.Event(),
            "otp_value": None,
            "current_index": 0,
            "current_card": None,
            "results": results,
            "success_count": 0,
            "fail_count": 0,
            "total": len(results),
            "error": None,
            "created_at": time.time(),
            "finished_at": None,
        }
        ACTIVE["job_id"] = job_id

    threading.Thread(
        target=run_job, args=(job_id, username, password, amount), daemon=True
    ).start()

    return jsonify({"job_id": job_id, "status": "pending", "total": len(results)})


@app.get("/api/transfer/status/<job_id>")
def api_transfer_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="این انتقال روی سرور پیدا نشد (ممکن است سرور دوباره اجرا شده باشد)"), 404
        return jsonify(job_payload(job))


@app.get("/api/transfer/active")
def api_transfer_active():
    with JOBS_LOCK:
        job = JOBS.get(ACTIVE["job_id"]) if ACTIVE["job_id"] else None
        if job and not _is_finished(job):
            return jsonify(job=job_payload(job))
    return jsonify(job=None)


@app.post("/api/transfer/otp")
def api_transfer_otp():
    data = request.get_json(force=True, silent=True) or {}
    job_id = str(data.get("job_id") or "")
    otp = only_digits(data.get("otp"))

    if not 4 <= len(otp) <= 8:
        return jsonify(error="کد تأیید باید فقط عدد و بین ۴ تا ۸ رقم باشد"), 400

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="این انتقال روی سرور پیدا نشد"), 404
        if job["status"] != "need_otp":
            return jsonify(error="در حال حاضر کد تأیید لازم نیست (شاید زمان آن تمام شده است)"), 409
        job["otp_value"] = otp
        job["need_otp"] = False
        job["otp_message"] = ""
        job["status"] = "running"
        job["otp_event"].set()
        return jsonify(job_payload(job))


if __name__ == "__main__":
    print("Server on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)