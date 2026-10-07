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
MIN_AMOUNT = 1_000             # ریال (۱۰۰ تومان): حداقل مبلغ هر انتقال
MAX_AMOUNT = 30_000_000_000    # ریال (۳ میلیارد تومان): حداکثر مبلغ هر انتقال

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
    state = db.get_state()
    # وضعیت آخرین اجرا (فقط در حافظهٔ سرور؛ با هر بار اجرای app.py خالی شروع می‌شود)
    with JOBS_LOCK:
        state["lastRun"] = {k: dict(v) for k, v in LAST_RUN.items()}
    return jsonify(state)


@app.get("/api/history")
def api_history():
    limit = request.args.get("limit", 1000, type=int) or 1000
    return jsonify(items=db.get_history(max(1, min(limit, 5000))))


@app.post("/api/history/delete")
def api_history_delete():
    data = request.get_json(silent=True) or {}
    try:
        item_id = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify(error="شناسهٔ سابقه نامعتبر است"), 400
    return jsonify(ok=True, deleted=db.delete_history(item_id))


@app.post("/api/history/clear")
def api_history_clear():
    deleted = db.clear_history()
    # با پاک شدن سوابق، وضعیت ردیف‌های لیست فرعی هم به حالت پیش‌فرض برمی‌گردد
    with JOBS_LOCK:
        LAST_RUN.clear()
    return jsonify(ok=True, deleted=deleted)


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
    if key not in ("username", "password", "nextId", "show_browser", "transfer_method"):
        return jsonify(error="کلید نامعتبر"), 400
    if key == "transfer_method" and db.normalize_method(data.get("value", "")) == "":
        return jsonify(error="روش انتقال نامعتبر است"), 400
    db.set_setting(key, data.get("value", ""))
    return jsonify(ok=True)


# ====================== انتقال کارت‌به‌کارت ======================
JOBS = {}                 # job_id -> job
JOBS_LOCK = threading.RLock()
ACTIVE = {"job_id": None}  # فقط یک کار هم‌زمان (پروفایل کروم قفل می‌شود)

# آخرین وضعیت هر کارت (کلید: شماره ۱۶ رقمی) برای نمایش در ردیف لیست فرعی.
# عمداً فقط در حافظه است: با شروع برنامه خالی است و تکلیف هیچ کارتی مشخص نیست.
LAST_RUN = {}

_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC = "٠١٢٣٤٥٦٧٨٩"
_TO_EN = {ord(c): str(i) for i, c in enumerate(_PERSIAN)}
_TO_EN.update({ord(c): str(i) for i, c in enumerate(_ARABIC)})


METHOD_LABEL = {
    "blu": "بلو به بلو",
    "card": "کارت به کارت عادی",
    "paya": "بین بانکی (پایا)",
}


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
        if isinstance(e, blu.AmountLimitExceeded):
            return "مبلغ از سقف مجاز انتقال بیشتر است؛ عملیات متوقف شد"
        if isinstance(e, blu.InvalidInfo):
            return "بلو اعلام کرد اطلاعات وارد شده نادرست است (یوزرنیم یا پسورد را در پروفایل بررسی کنید)؛ عملیات متوقف شد"
        if isinstance(e, blu.UnknownBluError):
            return "بلو در ورود «خطای ناشناخته» داد و بعد از ۳ بار ری‌استارت مرورگر هم برطرف نشد؛ عملیات متوقف شد"
        if isinstance(e, blu.BalanceUnreadable):
            return ("موجودی حساب از صفحهٔ بلو خوانده نشد؛ برای احتیاط هیچ واریزی برای این کارت انجام نشد "
                    "(ممکن است ظاهر صفحهٔ بلو تغییر کرده باشد)؛ عملیات متوقف شد")
        if isinstance(e, blu.OtpTimeout):
            return "زمان وارد کردن کد تأیید (OTP) تمام شد؛ دوباره تلاش کنید"
        if isinstance(e, blu.StoppedByUser):
            return "عملیات و سشن‌ها توسط شما متوقف شد"
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
        "otp_wrong": job.get("otp_retry", False),
        "notice": job.get("notice"),
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


def record_result(item: dict, status: str, message: str = "", interrupted: bool = False):
    """وضعیت آخرین اجرای کارت را نگه می‌دارد و نتیجهٔ موفق/ناموفق را در سوابق (دیتابیس) می‌نویسد.
    (با JOBS_LOCK صدا زده شود.)"""
    LAST_RUN[only_digits(item["number"])] = {
        "status": status,
        "message": message,
        "amount": item.get("amount") or 0,
        "interrupted": interrupted,
        "remaining": bool(item.get("remaining")),
        "method": item.get("method_used") or "",
    }
    if status in ("success", "failed"):
        try:
            db.add_history(status, item["number"], item["owner"], item.get("amount") or 0, message,
                           item.get("method_used") or "")
        except Exception:
            app.logger.error("could not write history:\n%s", traceback.format_exc())


def run_job(job_id: str, username: str, password: str, amount: int, headless=None,
            default_method: str = "card"):
    job = JOBS[job_id]

    def set_notice(kind, text):
        # با قفل صدا زده می‌شود؛ seq باعث می‌شود صفحه هر پیام را فقط یک بار نشان دهد
        job["notice_seq"] += 1
        job["notice"] = {"seq": job["notice_seq"], "kind": kind, "text": text}

    def on_progress(event, index, detail=""):
        # درخواست توقف: فقط در نقطه‌های امن (قبل از رویدادهای پایانی کارت) اجرا را قطع می‌کنیم
        if job.get("stop") and event in ("card_start", "step", "method"):
            raise blu.StoppedByUser("stopped by user")
        with JOBS_LOCK:
            item = job["results"][index]
            if event == "card_start":
                item["status"] = "processing"
                item["message"] = "در حال ارسال..."
                job["current_index"] = index
                job["current_card"] = item["number"]
            elif event == "step":
                item["message"] = detail
            elif event == "otp_wrong":
                job["otp_retry"] = True
                set_notice("otp_wrong", "کد تأیید اشتباه بود! سشن از اول ری‌استارت می‌شود؛ منتظر کد جدید بلو باش و آن را دوباره وارد کن")
                item["message"] = "کد تأیید اشتباه بود — ری‌استارت سشن"
            elif event == "method":
                # روشی که واقعاً برای این کارت به کار رفت (بلو به بلو همیشه اولویت دارد)
                item["method_used"] = detail
                label = METHOD_LABEL.get(detail, detail)
                item["message"] = f"روش انتقال: {label}"
                if detail == "blu" and item.get("method") != "blu":
                    set_notice("method", f"کارت {item['number']}: حساب بلو بود؛ با «بلو به بلو» انجام می‌شود")
                elif item.get("method") == "paya" and detail == "card":
                    set_notice("method", f"کارت {item['number']}: پایا در دسترس نبود؛ با «کارت به کارت عادی» انجام می‌شود")
                else:
                    set_notice("method", f"کارت {item['number']}: روش «{label}»")
            elif event == "otp_ok":
                job["otp_retry"] = False
                set_notice("otp_ok", "کد تأیید درست بود ✓")
            elif event == "card_failed":
                # فقط این کارت ناموفق است؛ کار ادامه پیدا می‌کند
                item["status"] = "failed"
                item["message"] = detail or "انتقال ناموفق بود"
                job["fail_count"] += 1
                record_result(item, "failed", item["message"])
            elif event == "card_remaining":
                # خود بلو انتقال را ناموفق اعلام کرد؛ کارت «باقی‌مانده» می‌ماند تا دوباره اجرا شود
                item["status"] = "failed"
                item["remaining"] = True
                item["message"] = detail or "بلو انتقال را ناموفق اعلام کرد"
                job["fail_count"] += 1
                set_notice("otp_wrong", f"کارت {item['number']}: بلو انتقال را ناموفق اعلام کرد؛ این کارت باقی می‌ماند")
                record_result(item, "failed", item["message"])
            elif event == "card_done":
                item["status"] = "success"
                item["message"] = f"انتقال انجام شد — {detail}" if detail else "انتقال با موفقیت انجام شد"
                job["success_count"] += 1
                record_result(item, "success", detail or "انتقال با موفقیت انجام شد")
                label = METHOD_LABEL.get(item.get("method_used"), "")
                if label:
                    set_notice("otp_ok", f"کارت {item['number']} با روش «{label}» انجام شد ✓")

    def get_otp() -> str:
        # بلو کد خواسته؛ وضعیت را need_otp می‌کنیم تا صفحه مودال OTP را نشان بدهد
        with JOBS_LOCK:
            job["otp_value"] = None
            job["otp_event"].clear()
            job["status"] = "need_otp"
            job["need_otp"] = True
            job["otp_message"] = (
                "کد قبلی اشتباه بود و سشن از اول ری‌استارت شد؛ کد جدید ارسال‌شده از طرف بلو را وارد کنید"
                if job.get("otp_retry")
                else "کد تأیید (OTP) ارسال‌شده از طرف بلو را وارد کنید"
            )
        got = job["otp_event"].wait(OTP_TIMEOUT)
        if job.get("stop"):
            with JOBS_LOCK:
                job["need_otp"] = False
                job["otp_message"] = ""
            raise blu.StoppedByUser("stopped by user")
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
                [{"number": r["number"], "owner": r["owner"], "amount": r["amount"],
                  "method": r.get("method") or default_method}
                 for r in job["results"]]
            )
        blu.run(data, username, password, amount, get_otp=get_otp,
                on_progress=on_progress, headless=headless, default_method=default_method)
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
                    # کارتی که وسط خطا در حال انتقال بود؛ برای ادامه‌ی بعدی علامت می‌خورد
                    item["status"] = "failed"
                    item["message"] = message
                    item["interrupted"] = True
                    job["fail_count"] += 1
                    record_result(item, "failed", message, interrupted=True)
                elif item["status"] == "pending":
                    item["status"] = "skipped"
                    item["message"] = "اجرا متوقف شد"
                    record_result(item, "skipped", item["message"])
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

    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        return jsonify(error="لیست کارت‌ها خالی است"), 400

    # مبلغ پیش‌فرض (اختیاری اگر همهٔ کارت‌ها مبلغ اختصاصی داشته باشند)
    amount_text = only_digits(data.get("amount"))
    amount = int(amount_text) if amount_text else 0

    # روش پیش‌فرض: از درخواست، وگرنه از تنظیمات (card | paya)
    default_method = db.normalize_method(data.get("method")) or db.get_transfer_method()

    results = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict) or not db.is_real_card(c.get("number", "")):
            return jsonify(error=f"شماره کارت ردیف {i + 1} معتبر نیست (باید ۱۶ رقم باشد)"), 400
        card_amount_text = only_digits(c.get("amount"))
        card_amount = int(card_amount_text) if card_amount_text else amount
        if card_amount <= 0:
            return jsonify(error=f"مبلغ کارت ردیف {i + 1} نامعتبر است"), 400
        if card_amount < MIN_AMOUNT:
            return jsonify(error=f"مبلغ کارت ردیف {i + 1}: حداقل مبلغ انتقال ۱۰۰ تومان (۱,۰۰۰ ریال) است"), 400
        if card_amount > MAX_AMOUNT:
            return jsonify(error=f"مبلغ کارت ردیف {i + 1}: حداکثر مبلغ انتقال ۳ میلیارد تومان است"), 400
        results.append({
            "id": c.get("id"),
            "number": format_card(c.get("number", "")),
            "owner": c.get("owner") or db.PLACEHOLDER_OWNER,
            "amount": card_amount,
            "method": db.normalize_method(c.get("method")) or default_method,
            "method_used": "",
            "remaining": False,
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
            "otp_retry": False,   # کد قبلی اشتباه بود و سشن ری‌استارت شده
            "stop": False,        # کاربر دکمهٔ توقف را زده
            "notice": None,       # پیام لحظه‌ای برای نمایش در سایت: {seq, kind, text}
            "notice_seq": 0,
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
        # اجرای تازه، وضعیت قبلیِ همین کارت‌ها را کنار می‌گذارد
        for r in results:
            LAST_RUN.pop(only_digits(r["number"]), None)

    # تنظیم پروفایل «نمایش مرورگر»؛ اگر هیچ‌وقت تنظیم نشده، همان پیش‌فرض فایل بلو (BLU_HEADLESS)
    show_setting = db.get_setting("show_browser", "")
    headless = None if show_setting == "" else (show_setting != "1")

    threading.Thread(
        target=run_job, args=(job_id, username, password, amount, headless, default_method), daemon=True
    ).start()

    return jsonify({"job_id": job_id, "status": "pending", "total": len(results)})


@app.post("/api/transfer/stop/<job_id>")
def api_transfer_stop(job_id: str):
    """متوقف کردن کار در حال اجرا: مرورگر/سشن بسته می‌شود و کارت‌های اجرانشده «مانده» علامت می‌خورند."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="کار پیدا نشد"), 404
        if _is_finished(job):
            return jsonify(ok=True, already_finished=True)
        job["stop"] = True
        job["otp_event"].set()  # اگر منتظر OTP است بیدار شود
    return jsonify(ok=True)


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