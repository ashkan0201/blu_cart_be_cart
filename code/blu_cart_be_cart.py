import os
import re
import shutil
import sys
from time import sleep, time
from random import randint

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException

# مسیرها نسبت به همین فایل هستند تا از هر جایی (مثلاً از app.py) درست کار کنند
CODE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", os.path.join(CODE_DIR, "chrome-profile"))  # سشن ورود بلو اینجا می‌ماند

ROOT_DIR = os.path.dirname(CODE_DIR)


def find_driver():
    """درایور مناسب سیستم‌عامل فعلی را از داخل فولدر پروژه پیدا می‌کند (یا None).

    ساختار پیشنهادی (هر دو را می‌توانی هم‌زمان بگذاری):
        blu/drivers/windows/chromedriver.exe
        blu/drivers/linux/chromedriver
    کنار app.py (chromedriver یا chromedriver.exe) هم پیدا می‌شود.
    اگر پیدا نشد None برمی‌گردد و Selenium Manager خودکار دانلود می‌کند.
    """
    if sys.platform.startswith("win"):
        folder, name = "windows", "chromedriver.exe"
    elif sys.platform == "darwin":
        folder, name = "mac", "chromedriver"
    else:
        folder, name = "linux", "chromedriver"

    candidates = [
        os.path.join(ROOT_DIR, "drivers", folder, name),
        os.path.join(ROOT_DIR, "drivers", name),
        os.path.join(ROOT_DIR, name),
        os.path.join(CODE_DIR, name),
    ]
    for path in candidates:
        if os.path.isfile(path):
            if not sys.platform.startswith("win") and not os.access(path, os.X_OK):
                try:  # روی لینوکس/مک اجازهٔ اجرا بده
                    os.chmod(path, os.stat(path).st_mode | 0o111)
                except OSError:
                    pass
            return path
    return None

# مرورگر پیش‌فرض بدون پنجره (headless) اجرا می‌شود؛ برای دیدن مرورگر: BLU_HEADLESS=0
HEADLESS = os.environ.get("BLU_HEADLESS", "1") != "0"

CONTINUE_XPATH = '(//button[.//span[normalize-space()="ادامه"]])[last()]'

# مودال استوری: فقط اونی که نوار پیشرفت (progressbar) داره
STORY_XPATH = (
    '//div[contains(@class,"_motionContainer") and contains(@class,"_open")]'
    '[.//div[contains(@class,"_progressbarContainer")]]'
)
STORY_CLOSE_XPATH = (
    f'({STORY_XPATH}//svg[.//path[starts-with(@d,"M4.47 4.47")]]/parent::div)[1]'
)


class OtpError(Exception):
    """خطاهای مربوط به کد تأیید (OTP)."""


class WrongOtp(OtpError):
    """کد تأیید وارد شده اشتباه بود («کد تایید نادرست است»)؛ سشن از اول ری‌استارت می‌شود."""


class OtpTimeout(OtpError):
    """کاربر در زمان مقرر OTP را وارد نکرد."""


class InsufficientBalance(Exception):
    """موجودی برای انتقال کافی نیست."""

    def __init__(self, balance, needed):
        super().__init__(f"balance={balance} needed={needed}")
        self.balance = balance
        self.needed = needed


class InvalidCard(Exception):
    """بلو گفته شماره کارت/شبا نادرست است؛ فقط همین کارت رد می‌شود و بقیه ادامه پیدا می‌کنند."""


class AmountLimitExceeded(Exception):
    """مبلغ از سقف مجاز انتقال بیشتر است؛ کل عملیات متوقف می‌شود."""


class InvalidInfo(Exception):
    """بلو گفته اطلاعات وارد شده نادرست است؛ کل عملیات متوقف می‌شود."""


class UnknownBluError(Exception):
    """بلو موقع ورود «خطای ناشناخته» داد. چند بار با ری‌استارت مرورگر تلاش می‌شود؛ بعدش کل عملیات متوقف می‌شود."""


class BalanceUnreadable(Exception):
    """موجودی از صفحهٔ بلو خوانده نشد؛ برای احتیاط هیچ واریزی انجام نمی‌شود و کل عملیات متوقف می‌شود."""


class StoppedByUser(Exception):
    """کاربر از صفحهٔ انتقال دکمهٔ توقف را زد؛ مرورگر بسته می‌شود و کل عملیات متوقف می‌شود (بدون ۳ به ۳)."""


class BankTransferFailed(Exception):
    """بلو در صفحهٔ نهایی «انتقال ناموفق» را نشان داد (مثلاً تداخل بانکی)؛ کارت «باقی‌مانده» می‌ماند
    و دوباره تلاش نمی‌شود. بقیهٔ کارت‌ها ادامه پیدا می‌کنند."""


# خطاهایی که قبلاً جداگانه هندل شده‌اند و مشمول «۳ به ۳» نیستند
NO_RETRY_ERRORS = (InsufficientBalance, AmountLimitExceeded, InvalidInfo, BalanceUnreadable, OtpTimeout)

# روش‌های انتقال
METHOD_BLU = "blu"    # بلو به بلو (همیشه اولویت اول، اگر دکمه‌اش باز باشد)
METHOD_CARD = "card"  # کارت به کارت عادی
METHOD_PAYA = "paya"  # بین بانکی (پایا)

BLU_TO_BLU_XPATH = '//*[@id="root"]/div[1]/div[3]/div/div/div[1]/button[5]'
CARD_TO_CARD_XPATH = '//*[@id="root"]/div[1]/div[3]/div/div/div[1]/button[1]'
# فقط «پایا»؛ دکمهٔ «بین بانکی (پل)» هم عنوان مشابه دارد و نباید انتخاب شود (یای عربی/فارسی یکی گرفته می‌شود)
PAYA_BUTTON_XPATH = (
    '//button[.//h4[contains(translate(normalize-space(), "ي", "ی"), "پایا")]]'
    '[not(.//h4[contains(normalize-space(), "پل")])]'
)
REASON_INPUT_XPATH = ('//div[@data-input-row="true"][.//h5[normalize-space()="بابت"]]'
                      '//div[@data-input-container="true"]')
# فقط داخل پنجرهٔ «علت انتقال» (bottom sheet) دنبال «مدیریت نقدینگی» می‌گردیم؛ متن می‌تواند «نقدینگی» یا «مدیریت نقدینگی» باشد
REASON_LIQUIDITY_XPATH = ('//div[@data-rsbs-overlay="true"]'
                          '//span[contains(normalize-space(), "نقدینگی")]')
# تلاش دوم اگر «نقدینگی» در لیست نبود
REASON_DAILY_XPATH = ('//div[@data-rsbs-overlay="true"]'
                      '//span[contains(normalize-space(), "امور روزمره")]')
PAYA_CONFIRM_XPATH = '//button[.//span[normalize-space()="تایید و انتقال"]]'
STATUS_SUCCESS_XPATH = '//span[normalize-space()="انتقال موفق"]'
STATUS_FAILED_XPATH = '//span[contains(normalize-space(), "انتقال ناموفق")]'
LEGACY_STATUS_XPATH = '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div[3]/span'

MIN_BALANCE_BUFFER = 50000  # ریال: بعد از انتقال باید حداقل این مقدار (کارمزد و احتیاط) در حساب بماند

# خطای ناشناخته‌ی بلو موقع ورود: ابتدا چند بار با همان پروفایل، بعد پاک کردن پروفایل و چند بار دیگر
TRIES_BEFORE_RESET = 3  # تلاش با پروفایل (سشن) فعلی
TRIES_AFTER_RESET = 3   # تلاش بعد از پاک کردن پروفایل (ورود از صفر، با OTP)

# متن خطاهای بلو (فقط بخش ثابت متن؛ عدد/فاصله‌های بعدی مهم نیست)
ERR_CARD_TEXT = "شماره کارت یا شبا نادرست است"
ERR_LIMIT_TEXT = "حداکثر مبلغ انتقال"
ERR_INFO_TEXT = "اطلاعات وارد شده نادرست است"
ERR_UNKNOWN_TEXT = "خطای ناشناخته"
ERR_OTP_TEXTS = ("کد تایید نادرست", "کد تأیید نادرست")


def _error_xpath(text):
    return (f'//*[self::div or self::span or self::p]'
            f'[contains(normalize-space(text()), "{text}")]')


def _visible_error(driver, text):
    try:
        for el in driver.find_elements(By.XPATH, _error_xpath(text)):
            if el.is_displayed():
                return True
    except Exception as e:
        print("[error-check] failed:", type(e).__name__, str(e)[:120])
    return False


def check_page_errors(driver, card=False, amount=False, info=False, unknown=False):
    """اگه یکی از پیام‌های خطای بلو (از بین موارد خواسته‌شده) روی صفحه باشه exception می‌ده.
    card:   «شماره کارت یا شبا نادرست است»  -> InvalidCard (فقط همین کارت رد می‌شه)
    amount: «حداکثر مبلغ انتقال ... است»     -> AmountLimitExceeded (توقف کل عملیات)
    info:   «اطلاعات وارد شده نادرست است»    -> InvalidInfo (توقف کل عملیات)
    unknown:«خطای ناشناخته ای رخ داده است»   -> UnknownBluError (ری‌استارت مرورگر، حداکثر ۳ بار)"""
    if card and _visible_error(driver, ERR_CARD_TEXT):
        raise InvalidCard("invalid card number")
    if amount and _visible_error(driver, ERR_LIMIT_TEXT):
        raise AmountLimitExceeded("max transfer amount exceeded")
    if info and _visible_error(driver, ERR_INFO_TEXT):
        raise InvalidInfo("bank says entered info is invalid")
    if unknown and _visible_error(driver, ERR_UNKNOWN_TEXT):
        raise UnknownBluError("bank says unknown error")


OTP_INPUT_XPATH = '//input[@autocomplete="one-time-code" and not(@disabled)]'
HOME_NAV_XPATH = '//*[@id="root"]/div[1]/div[3]/nav/div[2]'
NOT_NOW_XPATH = '//button[.//span[normalize-space()="الان نه!"]]'


def reset_profile(tries=5):
    """پوشه‌ی chrome-profile (سشن ذخیره‌شده‌ی بلو) رو پاک می‌کنه؛ بعد از بسته شدن مرورگر صدا زده بشه.
    ممکنه کروم هنوز فایل‌ها رو قفل کرده باشه، پس چند بار با فاصله تلاش می‌کنه."""
    for _ in range(tries):
        shutil.rmtree(CHROME_PROFILE_DIR, ignore_errors=True)
        if not os.path.exists(CHROME_PROFILE_DIR):
            print("[profile] chrome-profile deleted")
            return True
        sleep(1)
    print("[profile] could not fully delete chrome-profile")
    return False


def save_debug(driver, tag):
    """اسکرین‌شات و HTML صفحه رو برای عیب‌یابی توی code/debug ذخیره می‌کنه."""
    try:
        d = os.path.join(CODE_DIR, "debug")
        os.makedirs(d, exist_ok=True)
        stamp = int(time())
        driver.save_screenshot(os.path.join(d, f"{tag}-{stamp}.png"))
        with open(os.path.join(d, f"{tag}-{stamp}.html"), "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("[debug] saved", tag, stamp)
    except Exception as e:
        print("[debug] save failed:", e)


def wait_login_outcome(driver, timeout=45, next_stable=3):
    """بعد از زدن «ورود به بلو» منتظر xpath فیلد OTP می‌مونه.
       "otp"  -> فیلد OTP دیده شد (فقط همین‌جا از کاربر کد خواسته می‌شه)
       "next" -> OTP نیومد و صفحه‌ی اصلی/«الان نه!» حداقل next_stable ثانیه پشت‌سرهم پایدار بود
                 (یعنی بلو این بار OTP نخواست)
       None   -> تا timeout هیچ‌کدوم نشد
    در این مدت خطاهای ورود («اطلاعات وارد شده نادرست است» / «خطای ناشناخته») هم چک می‌شن."""
    end = time() + timeout
    next_since = None
    while time() < end:
        check_page_errors(driver, info=True, unknown=True)
        try:
            # اولویت با OTP است
            for el in driver.find_elements(By.XPATH, OTP_INPUT_XPATH):
                if el.is_displayed():
                    return "otp"
            if driver.find_elements(By.XPATH, HOME_NAV_XPATH) or driver.find_elements(By.XPATH, NOT_NOW_XPATH):
                next_since = next_since or time()
                if time() - next_since >= next_stable:
                    return "next"
            else:
                next_since = None
        except Exception:
            pass
        sleep(0.3)
    return None


def wait_otp_result(driver, timeout=20, stable=2):
    """بعد از وارد کردن OTP نتیجه رو مشخص می‌کنه:
       WrongOtp  -> «کد تایید نادرست است» دیده شد
       True      -> کد پذیرفته شد (صفحه‌ی اصلی/«الان نه!» اومد یا فیلد OTP حداقل `stable` ثانیه ناپدید شد)
       None      -> تا timeout نامشخص بود
    خطاهای «اطلاعات نادرست» و «خطای ناشناخته» هم همین‌جا چک می‌شن."""
    end = time() + timeout
    gone_since = None
    while time() < end:
        for t in ERR_OTP_TEXTS:
            if _visible_error(driver, t):
                raise WrongOtp("wrong otp")
        check_page_errors(driver, info=True, unknown=True)
        try:
            if driver.find_elements(By.XPATH, HOME_NAV_XPATH) or driver.find_elements(By.XPATH, NOT_NOW_XPATH):
                return True
            shown = any(e.is_displayed() for e in driver.find_elements(By.XPATH, '//input[@autocomplete="one-time-code"]'))
            if shown:
                gone_since = None
            else:
                gone_since = gone_since or time()
                if time() - gone_since >= stable:
                    return True
        except Exception:
            pass
        sleep(0.3)
    return None


def wait_click_or_error(driver, xpath, tag, timeout=30, **checks):
    """مثل wait_click ولی اگه تو این فاصله پیام خطای خواسته‌شده ظاهر بشه فوراً exception می‌ده
    (چون با خطا معمولاً دکمه غیرفعال می‌شه و بدون این، تا ۳۰ ثانیه منتظر می‌موند)."""
    def cond(d):
        check_page_errors(d, **checks)
        for el in d.find_elements(By.XPATH, xpath):
            if el.is_displayed() and el.is_enabled():
                return True
        return False
    sleep(0.5)  # فرصت برای ظاهر شدن پیام اعتبارسنجی بعد از وارد کردن مقدار
    check_page_errors(driver, **checks)
    WebDriverWait(driver, timeout, poll_frequency=0.3).until(cond)
    return wait_click(driver, xpath, tag, timeout)


def _any_visible(driver, xpath):
    try:
        for el in driver.find_elements(By.XPATH, xpath):
            if el.is_displayed():
                return el
    except Exception as e:
        print("[visible-check] failed:", type(e).__name__, str(e)[:120])
    return None


def choose_transfer_method(driver, wanted, pause=1.0):
    """روش انتقال را در صفحه انتخاب می‌کند و روش واقعی (blu | card | paya) را برمی‌گرداند.
    اولویت: ۱) بلو به بلو اگر دکمه‌اش باز بود (هر روشی که کاربر زده باشد)،
            ۲) روش انتخاب‌شدهٔ کاربر (پایا؛ اگر پایا باز نبود کارت به کارت عادی)،
            ۳) کارت به کارت عادی."""
    # صبر کن گزینه‌ها بالا بیایند تا بلو به بلو به‌خاطر دیر رسیدن صفحه نادیده گرفته نشود
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, CARD_TO_CARD_XPATH))
        )
    except Exception:
        pass

    try:
        blu_button = driver.find_element(By.XPATH, BLU_TO_BLU_XPATH)
        if "disabled" in (blu_button.get_attribute("class") or "").lower():
            raise Exception("Button is disabled")
        blu_button.click()
        return METHOD_BLU
    except Exception:
        pass

    if wanted == METHOD_PAYA:
        try:
            paya_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, PAYA_BUTTON_XPATH))
            )
            if "disabled" in (paya_button.get_attribute("class") or "").lower():
                raise Exception("paya disabled")
            paya_button.click()
            return METHOD_PAYA
        except Exception:
            print("[method] paya not available, falling back to normal card-to-card")

    wait_click(driver, CARD_TO_CARD_XPATH, "blu_to_other")
    sleep(pause)
    return METHOD_CARD


def _pick_reason_option(driver, xpath, label, pause):
    """یک گزینه را در پنجرهٔ «علت انتقال» پیدا و انتخاب می‌کند؛ اگر نبود False برمی‌گرداند."""
    try:
        option = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
    except Exception:
        print(f"[paya] reason option not present: {label}")
        return False
    # لیست داخل پنجرهٔ اسکرول‌دار است؛ گزینه را به دید بیاور
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
    sleep(0.3)
    try:
        option.click()
    except Exception:
        driver.execute_script("arguments[0].click();", option)
    sleep(pause)
    print(f"[paya] reason selected: {label}")
    return True


def select_paya_reason(driver, pause=1.0):
    """در فلوی پایا: «بابت» را باز می‌کند و «مدیریت نقدینگی» را انتخاب می‌کند؛
    اگر نقدینگی در لیست نبود، تلاش دوم: «هزینه عمومی و امور روزمره».
    خود بخش بابت هم ممکن است در صفحه باشد یا نباشد؛ اگر نبود بدون خطا رد می‌شود
    (اگر واقعاً الزامی باشد، دکمهٔ تایید فعال نمی‌شود و همان ۳ به ۳ اجرا می‌شود)."""
    try:
        reason = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, REASON_INPUT_XPATH))
        )
    except Exception:
        print("[paya] reason field not present, skipping")
        return False
    try:
        reason.click()
    except Exception:
        driver.execute_script("arguments[0].click();", reason)
    sleep(pause)
    if _pick_reason_option(driver, REASON_LIQUIDITY_XPATH, "نقدینگی", pause):
        return True
    return _pick_reason_option(driver, REASON_DAILY_XPATH, "هزینه عمومی و امور روزمره", pause)


def wait_final_status(driver, used_method, timeout=30):
    """منتظر وضعیت نهایی می‌ماند: ("success", متن) یا ("failed", متن).
    اگر تا timeout هیچ وضعیتی پیدا نشد TimeoutException می‌دهد (انتقال انجام‌نشده حساب می‌شود)."""
    holder = {}

    def cond(d):
        el = _any_visible(d, STATUS_FAILED_XPATH)
        if el is not None:
            holder["r"] = ("failed", el.text.strip() or "انتقال ناموفق")
            return True
        el = _any_visible(d, STATUS_SUCCESS_XPATH)
        if el is not None:
            holder["r"] = ("success", el.text.strip() or "انتقال موفق")
            return True
        if used_method != METHOD_PAYA:  # فلوهای قبلی: المنت وضعیت قدیمی
            try:
                for e in d.find_elements(By.XPATH, LEGACY_STATUS_XPATH):
                    holder["r"] = ("success", e.text.strip() or "انتقال موفق")
                    return True
            except Exception:
                pass
        return False

    WebDriverWait(driver, timeout, poll_frequency=0.3).until(cond)
    return holder["r"]


def _safe_quit(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _notify(on_progress, event, index, detail=""):
    """گزارش پیشرفت به بیرون (app.py). اگه callback نباشه کاری نمی‌کنه."""
    if on_progress:
        on_progress(event, index, detail)


def close_stories(driver, wait_appear=2, tries=3):
    """استوری‌ها رو فوراً می‌بنده. اول ضربدر، و اگه نشد خود مودال رو از DOM حذف می‌کنه."""
    try:
        WebDriverWait(driver, wait_appear).until(
            EC.presence_of_element_located((By.XPATH, STORY_XPATH))
        )
    except Exception:
        return False  # استوری‌ای نیست

    print("[story] detected")
    for _ in range(tries):
        if not driver.find_elements(By.XPATH, STORY_XPATH):
            return True
        try:
            x = driver.find_element(By.XPATH, STORY_CLOSE_XPATH)
            driver.execute_script("arguments[0].click();", x)
        except Exception:
            pass
        sleep(0.4)

    if driver.find_elements(By.XPATH, STORY_XPATH):
        print("[story] still open, removing overlay from DOM")
        driver.execute_script("""
            document.querySelectorAll('[class*="_motionContainer"][class*="_open"]')
              .forEach(e => { if (e.querySelector('[class*="_progressbarContainer"]')) e.remove(); });
        """)
        sleep(0.3)
    return True


def wait_click(driver, xpath, tag, timeout=30):
    """صبر می‌کنه تا دکمه قابل کلیک بشه و کلیک می‌کنه."""
    el = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    try:
        el.click()
    except ElementClickInterceptedException:
        # احتمالاً استوری روی دکمه افتاده؛ ببند و دوباره امتحان کن
        close_stories(driver, wait_appear=1)
        try:
            el.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", el)
    return el


def set_react_input(driver, element, value):
    """مقدار رو طوری ست می‌کنه که React هم تغییر رو ببینه."""
    driver.execute_script("""
        const input = arguments[0];
        const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(input, arguments[1]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    """, element, str(value))


_BALANCE_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def parse_balance(text):
    """متن موجودی صفحه (مثلاً «موجودی: ۱٬۲۳۴٬۵۶۷ ریال») را به عدد (ریال) تبدیل می‌کند.
    اگر قالب متن آن چیزی نبود که انتظار می‌رود، None برمی‌گرداند (هرگز حدس نمی‌زند)."""
    if not text or ":" not in text:
        return None
    part = text.split(":", 1)[1].translate(_BALANCE_DIGITS)
    m = re.search(r"\d[\d,٬]*", part)
    if not m:
        return None
    digits = m.group(0).replace(",", "").replace("٬", "")
    return int(digits) if digits else None


def read_balance(driver, timeout=30):
    """منتظر می‌ماند تا موجودی واقعاً روی صفحه نمایش داده شود و عددش را برمی‌گرداند.
    اگر تا timeout خوانده نشد BalanceUnreadable می‌دهد (و واریزی انجام نمی‌شود)."""
    xpath = '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div/div/section/div/div'
    holder = {}

    def cond(d):
        for el in d.find_elements(By.XPATH, xpath):
            value = parse_balance(el.text)
            if value is not None:
                holder["value"] = value
                return True
        return False

    try:
        WebDriverWait(driver, timeout, poll_frequency=0.3).until(cond)
    except Exception:
        raise BalanceUnreadable("balance not readable")
    return holder["value"]


def run(data, user_name, pass_word, cash_pay, get_otp=None, on_progress=None, headless=None,
        default_method=METHOD_CARD):
    """
    data:          [(شماره کارت، نام مالک[، مبلغ اختصاصی[، روش]])]؛ روش: "card" یا "paya" (خالی = default_method).
                   اگه مبلغ اختصاصی نباشه (یا 0)، cash_pay (مبلغ پیش‌فرض) واریز می‌شه.
                   بلو به بلو همیشه اولویت دارد؛ روش انتخابی فقط وقتی به کار می‌رود که بلو به بلو ممکن نباشد.
    get_otp():     وقتی بلو کد تأیید خواست صدا زده می‌شه و باید رشته‌ی OTP رو برگردونه
                   (اگه None باشه از ترمینال input می‌گیره).
    on_progress(event, index, detail): event یکی از card_start | step | method | card_done | card_failed |
                   card_remaining (بلو انتقال را ناموفق اعلام کرد) | otp_ok | otp_wrong
    headless:      None یعنی از HEADLESS بالای فایل استفاده کن.
    """
    if headless is None:
        headless = HEADLESS
    result = []
    default_amount = int(cash_pay) if cash_pay else 0
    for index, item in enumerate(data):
        shomareh_cart, name = item[0], item[1]
        card_amount = int(item[2]) if len(item) > 2 and item[2] else default_amount
        card_method = (item[3] if len(item) > 3 and item[3] in (METHOD_CARD, METHOD_PAYA)
                       else (default_method if default_method in (METHOD_CARD, METHOD_PAYA) else METHOD_CARD))
        if card_amount <= 0:
            raise ValueError(f"مبلغ کارت شمارهٔ {index + 1} مشخص نیست")
        _notify(on_progress, "card_start", index)
        while_stop = True
        unknown_fails = 0        # تعداد «خطای ناشناخته» در مرحلهٔ فعلی (قبل/بعد از پاک شدن پروفایل)
        profile_reset = False    # آیا پروفایل برای این کارت پاک شده؟
        while while_stop:
            random_num_for_sleep = randint(5, 15) / 10

            options = Options()
            options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            if headless:
                options.add_argument("--headless=new")
            options.add_argument("--app=https://app.blubank.com/")
            options.add_argument("--window-size=390,844")
            options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

            # درایور محلی اگر بود، وگرنه Selenium Manager خودش پیدا/دانلود می‌کند
            driver_path = find_driver()
            print("DRIVER:", driver_path or "auto (Selenium Manager)")
            service = Service(driver_path) if driver_path else Service()
            driver = None
            try:
                driver = webdriver.Chrome(service=service, options=options)
                sleep(random_num_for_sleep)

                print("TITLE:", driver.title)
                print("URL:", driver.current_url)

                # ---------- ورود ----------
                _notify(on_progress, "step", index, "ورود به بلو")
                try:
                    button_to_Login = driver.find_element(By.XPATH, '//*[@id="root"]/div[1]/div[2]/div/div[2]/button')
                    button_to_Login.click()
                    sleep(random_num_for_sleep)
                except Exception:
                    pass

                try:
                    username_input = WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="login-form"]/div/div[1]/div/input'))
                    )
                    driver.execute_script("""
                    const input = arguments[0];
                    const value = arguments[1];
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype,
                        'value'
                    ).set;
                    setter.call(input, '');
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    setter.call(input, value);
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    """, username_input, user_name)
                except Exception:
                    pass

                password_input = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="login-form"]/div/div[2]/div/input'))
                )
                password_input.click()
                password_input.send_keys(pass_word)

                wait_click(driver, '//button[.//span[contains(text(),"ورود به بلو")]]', "login")
                sleep(random_num_for_sleep)
                # بعد از ورود: خطاهای ورود چک می‌شن و منتظر فیلد OTP (یا رد شدن بدون OTP) می‌مونیم
                _notify(on_progress, "step", index, "در انتظار پاسخ بلو بعد از ورود")
                outcome = wait_login_outcome(driver)
                print("[login] outcome:", outcome)

                if outcome == "otp":
                    otp_input = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, OTP_INPUT_XPATH))
                    )
                    otp = (get_otp() if get_otp else input("OTP: ")).strip()
                    otp_input.send_keys(otp)
                    sleep(random_num_for_sleep)
                    # نتیجهٔ کد: اشتباه -> WrongOtp (ری‌استارت)، درست -> به کاربر اطلاع می‌دیم
                    _notify(on_progress, "step", index, "بررسی کد تأیید")
                    if wait_otp_result(driver):
                        _notify(on_progress, "otp_ok", index, "")
                elif outcome is None:
                    save_debug(driver, "login-no-otp")  # عیب‌یابی: ببین صفحه چی نشون می‌داد

                try:
                    not_now_button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, '//button[.//span[normalize-space()="الان نه!"]]')
                        )
                    )
                    not_now_button.click()
                    sleep(random_num_for_sleep)
                except Exception:
                    pass

                # ---------- منوی کارت به کارت ----------
                _notify(on_progress, "step", index, "باز کردن بخش کارت به کارت")
                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/nav/div[2]', "menu")
                sleep(random_num_for_sleep)

                # استوری‌ها رو فوراً ببند (بعد از منو)
                close_stories(driver, wait_appear=4)

                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div/div/div[3]/button', "cart_be_cart")
                sleep(random_num_for_sleep)
                close_stories(driver, wait_appear=3)  # ممکنه اینجا هم استوری بیاد

                # ---------- شماره کارت ----------
                _notify(on_progress, "step", index, "ثبت شماره کارت مقصد")
                shomareh_cart_replay = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div/input'))
                )
                shomareh_cart_replay.click()
                shomareh_cart_replay.send_keys(shomareh_cart)
                sleep(random_num_for_sleep)

                # شماره کارت نادرست: هنگام زدن دکمه ادامه خطا میاد -> فقط همین کارت رد می‌شه
                wait_click_or_error(driver, '//*[@id="root"]/div[1]/div[3]/div[2]/button', "ersal1", card=True)
                sleep(random_num_for_sleep)
                check_page_errors(driver, card=True)

                # ---------- مبلغ ----------
                _notify(on_progress, "step", index, "ثبت مبلغ")
                cash_pay_replay = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div/div[2]/div/input'))
                )
                cash_pay_replay.click()
                set_react_input(driver, cash_pay_replay, card_amount)
                sleep(random_num_for_sleep)

                # دکمه «ادامه» با متن پیدا می‌شه، نه با ترتیب div ها
                # سقف مبلغ: هنگام زدن دکمه ادامه/انتقال خطا میاد -> توقف کل عملیات
                wait_click_or_error(driver, CONTINUE_XPATH, "ersal2", amount=True)
                sleep(random_num_for_sleep)
                check_page_errors(driver, amount=True)

                # ---------- انتخاب روش انتقال ----------
                # اولویت: بلو به بلو (اگر باز بود) ← روش انتخابی کاربر (پایا / کارت به کارت)
                used_method = choose_transfer_method(driver, card_method, random_num_for_sleep)
                print("[method] wanted:", card_method, "used:", used_method)
                _notify(on_progress, "method", index, used_method)
                sleep(random_num_for_sleep)

                # ---------- چک موجودی ----------
                _notify(on_progress, "step", index, "بررسی موجودی")
                # موجودی باید خوانده و کافی بودنش تأیید شود؛ در غیر این صورت تا کلیک «تأیید نهایی» نمی‌رسیم
                balance_ok = False
                balance_rial = read_balance(driver)
                needed = card_amount + MIN_BALANCE_BUFFER
                if balance_rial < needed:
                    raise InsufficientBalance(balance_rial, needed)
                balance_ok = True
                _notify(on_progress, "step", index, f"موجودی کافی است ({balance_rial:,} ریال)")

                if used_method == METHOD_PAYA:
                    _notify(on_progress, "step", index, "انتخاب بابت: نقدینگی")
                    select_paya_reason(driver, random_num_for_sleep)

                # ---------- تایید نهایی ----------
                _notify(on_progress, "step", index, "تأیید نهایی انتقال")
                if not balance_ok:  # محافظ نهایی: بدون تأیید موجودی هرگز پرداخت نمی‌شود
                    raise BalanceUnreadable("balance was not verified before payment")
                if used_method == METHOD_PAYA:
                    wait_click(driver, PAYA_CONFIRM_XPATH, "paya_pay")
                else:
                    wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div[2]/button', "move_pay")
                sleep(random_num_for_sleep)

                # وضعیت نهایی: موفق ← کارت تمام است | ناموفق (خود بلو) ← کارت باقی می‌ماند
                # | وضعیتی پیدا نشد ← انتقال انجام‌نشده حساب می‌شود و «۳ به ۳» اجرا می‌شود
                final_state, final_text = wait_final_status(driver, used_method)
                if final_state == "failed":
                    raise BankTransferFailed(final_text)
                result.append((final_text, name, card_amount))
                driver.quit()
                _notify(on_progress, "card_done", index, final_text)
                while_stop = False

            except StoppedByUser:
                # دکمهٔ توقف: مرورگر بسته می‌شود و کل عملیات بدون ۳ به ۳ متوقف می‌شود
                _safe_quit(driver)
                raise

            except WrongOtp:
                # کد اشتباه بود: مرورگر بسته می‌شه، به کاربر خبر می‌دیم و از اول وارد می‌شیم
                _safe_quit(driver)
                _notify(on_progress, "otp_wrong", index, "")
                sleep(1)
                # ادامهٔ while: مرورگر دوباره باز می‌شود و OTP تازه از کاربر خواسته می‌شود

            except InvalidCard:
                # شماره کارت نادرست: همین کارت ناموفق ثبت می‌شه و میره سراغ کارت بعدی
                _safe_quit(driver)
                _notify(on_progress, "card_failed", index, "شماره کارت یا شبا نادرست است")
                result.append((None, name, card_amount))
                while_stop = False

            except BankTransferFailed as e:
                # خود بلو «انتقال ناموفق» را برگرداند: دوباره تلاش نمی‌شود؛ کارت «باقی‌مانده» علامت می‌خورد
                _safe_quit(driver)
                _notify(on_progress, "card_remaining", index,
                        "بلو انتقال را ناموفق اعلام کرد (ممکن است تداخل بانکی باشد)؛ کارت باقی ماند")
                result.append((None, name, card_amount))
                while_stop = False

            except NO_RETRY_ERRORS:
                # موجودی/سقف مبلغ/اطلاعات نادرست/خواندن موجودی/تایم‌اوت OTP: همان رفتار قبلی، کل عملیات متوقف
                _safe_quit(driver)
                raise

            except Exception as e:
                # «خطای ناشناخته‌ی بلو» و هر خطای هندل‌نشدهٔ دیگر: روش ۳ به ۳
                #   ۳ بار ری‌استارت سشن ← پاک شدن پروفایل ← ۳ بار دیگر ← توقف کامل
                _safe_quit(driver)
                unknown_fails += 1
                if isinstance(e, UnknownBluError):
                    what = "خطای ناشناخته از بلو"
                else:
                    what = f"خطای پیش‌بینی‌نشده ({type(e).__name__})"
                    print("[retry] unhandled error:", type(e).__name__, str(e)[:200])
                if not profile_reset:
                    if unknown_fails >= TRIES_BEFORE_RESET:
                        _notify(on_progress, "step", index,
                                f"{what} ({TRIES_BEFORE_RESET} بار) — پاک کردن پروفایل کروم و ورود دوباره (OTP لازم است)")
                        sleep(1)
                        reset_profile()
                        profile_reset = True
                        unknown_fails = 0
                    else:
                        _notify(on_progress, "step", index,
                                f"{what} — ری‌استارت سشن ({unknown_fails} از {TRIES_BEFORE_RESET})")
                        sleep(1)
                else:
                    if unknown_fails >= TRIES_AFTER_RESET:
                        raise  # بعد از پاک شدن پروفایل هم ۳ بار شکست خورد: کل عملیات متوقف
                    _notify(on_progress, "step", index,
                            f"{what} — ری‌استارت سشن بعد از پاک شدن پروفایل ({unknown_fails} از {TRIES_AFTER_RESET})")
                    sleep(1)
                # مرورگر دوباره از اول باز می‌شود (ادامهٔ while)
    return result


if __name__ == "__main__":
    # اجرای مستقیم فایل (بدون سایت): لیست اصلی و یوزر/پسورد از دیتابیس خونده می‌شه.
    # موقع import از app.py این بخش اجرا نمی‌شه.
    import db

    data = db.get_run_data()
    user_name, pass_word = db.get_credentials()
    cash_pay = "10000"
    result = run(
        data,
        user_name,
        pass_word,
        int(cash_pay)
    )

    print(result)
