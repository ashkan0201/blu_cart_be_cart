from time import sleep
from random import randint

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException

CONTINUE_XPATH = '(//button[.//span[normalize-space()="ادامه"]])[last()]'


def dump_debug(driver, tag):
    """موقع خطا عکس و HTML صفحه رو ذخیره می‌کنه تا بشه فهمید چی شده."""
    try:
        driver.save_screenshot(f"debug_{tag}.png")
        with open(f"debug_{tag}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"[debug] saved debug_{tag}.png / debug_{tag}.html")
    except Exception as e:
        print("[debug] could not save debug files:", e)


# مودال استوری: فقط اونی که نوار پیشرفت (progressbar) داره
STORY_XPATH = (
    '//div[contains(@class,"_motionContainer") and contains(@class,"_open")]'
    '[.//div[contains(@class,"_progressbarContainer")]]'
)
STORY_CLOSE_XPATH = (
    f'({STORY_XPATH}//svg[.//path[starts-with(@d,"M4.47 4.47")]]/parent::div)[1]'
)


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
            print("[story] closed")
            return True
        try:
            x = driver.find_element(By.XPATH, STORY_CLOSE_XPATH)
            driver.execute_script("arguments[0].click();", x)
            print("[story] clicked X")
        except Exception as e:
            print("[story] X click failed:", type(e).__name__)
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
    """صبر می‌کنه تا دکمه قابل کلیک بشه و کلیک می‌کنه؛ اگه timeout شد debug ذخیره می‌کنه."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
    except Exception:
        dump_debug(driver, tag)
        raise
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


def run(data, user_name, pass_word, cash_pay):
    result = []
    for shomareh_cart, name in data:
        while_stop = True
        while while_stop:
            random_num_for_sleep = randint(5, 15) / 10

            options = Options()
            options.add_argument("--user-data-dir=./chrome-profile")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--app=https://app.blubank.com/")
            options.add_argument("--window-size=390,844")
            options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

            service = Service("/home/ashkan/Desktop/blu/chromedriver")

            driver = webdriver.Chrome(service=service, options=options)
            sleep(random_num_for_sleep)

            print("TITLE:", driver.title)
            print("URL:", driver.current_url)

            try:
                # ---------- ورود ----------
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

                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, '//*[contains(text(),"تایید دستگاه جدید")]'))
                    )
                    input("Enter the SMS verification code manually, then press Enter...")
                except Exception:
                    pass

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
                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/nav/div[2]', "menu")
                sleep(random_num_for_sleep)

                # استوری‌ها رو فوراً ببند (بعد از منو)
                close_stories(driver, wait_appear=4)

                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div/div/div[3]/button', "cart_be_cart")
                sleep(random_num_for_sleep)
                close_stories(driver, wait_appear=3)  # ممکنه اینجا هم استوری بیاد

                # ---------- شماره کارت ----------
                shomareh_cart_replay = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div/input'))
                )
                shomareh_cart_replay.click()
                shomareh_cart_replay.send_keys(shomareh_cart)
                sleep(random_num_for_sleep)

                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div[2]/button', "ersal1")
                sleep(random_num_for_sleep)

                # ---------- مبلغ ----------
                try:
                    cash_pay_replay = WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div/div[2]/div/input'))
                    )
                except Exception:
                    dump_debug(driver, "amount_input")
                    raise
                cash_pay_replay.click()
                set_react_input(driver, cash_pay_replay, cash_pay)
                sleep(random_num_for_sleep)

                # دکمه «ادامه» با متن پیدا می‌شه، نه با ترتیب div ها
                wait_click(driver, CONTINUE_XPATH, "ersal2")
                sleep(random_num_for_sleep)

                try:
                    transfer_button = driver.find_element(
                        By.XPATH,
                        '//*[@id="root"]/div[1]/div[3]/div/div/div[1]/button[5]'
                    )

                    classes = transfer_button.get_attribute("class")

                    if "disabled" in classes.lower():
                        raise Exception("Button is disabled")

                    transfer_button.click()

                except Exception:
                    wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div/div/div[1]/button[1]', "blu_to_other")
                    sleep(random_num_for_sleep)

                # ---------- چک موجودی ----------
                try:
                    balance_element = WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div/div/section/div/div'))
                    )

                    balance_text = balance_element.text
                    balance_text = balance_text.split(":", 1)[1].strip()

                    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
                    english_digits = "0123456789"
                    translation_table = str.maketrans(persian_digits, english_digits)

                    balance_text = balance_text.translate(translation_table)
                    balance_text = balance_text.replace("٬", "").replace(",", "").replace(" ", "")
                    balance_text = balance_text.replace("ریال", "")

                    balance_rial = int(balance_text)
                    if balance_rial < cash_pay + 50000:
                        driver.quit()
                        return result

                except Exception:
                    # خطا در خوندن موجودی (فرق داره با موجودی ناکافی)
                    dump_debug(driver, "balance")
                    raise

                # ---------- تایید نهایی ----------
                wait_click(driver, '//*[@id="root"]/div[1]/div[3]/div[2]/button', "move_pay")
                sleep(random_num_for_sleep)

                finaly_cart_be_cart = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="root"]/div[1]/div[3]/div[1]/div[1]/div[3]/span'))
                )
                finaly_cart_be_cart_text = finaly_cart_be_cart.text
                result.append((finaly_cart_be_cart_text, name, cash_pay))
                driver.quit()
                while_stop = False

            except Exception:
                # فقط وقتی کل مرحله خطا بده؛ مرورگر بسته می‌شه و خطا نمایش داده می‌شه
                dump_debug(driver, "fatal")
                driver.quit()
                raise
    return result


data = [
    ("6219861077175262", "اشکان نوروزی"),
    ("5859831869301746", "اشکان نوروزی"),
    ("6037991235519224", "کیمیا اقامحمدی"),
    ("6362141123263585", "کیمیا اقامحمدی"),
    ("6280231355120782", "کیمیا اقامحمدی"),
    ("6219861077175262", "اشکان نوروزی"),
    ("5859831869301746", "اشکان نوروزی"),
    ("6037991235519224", "کیمیا اقامحمدی"),
    ("6362141123263585", "کیمیا اقامحمدی"),
    ("6219861077175262", "اشکان نوروزی"),
    ("5859831869301746", "اشکان نوروزی"),
    ("6037991235519224", "کیمیا اقامحمدی"),
    ("6362141123263585", "کیمیا اقامحمدی"),
    ("6280231355120782", "کیمیا اقامحمدی"),
    ("6219861077175262", "اشکان نوروزی"),
    ("5859831869301746", "اشکان نوروزی"),
    ("6037991235519224", "کیمیا اقامحمدی"),
    ("6362141123263585", "کیمیا اقامحمدی"),
    ("6280231355120782", "کیمیا اقامحمدی")
]
user_name = "ashkan0201"
pass_word = "#YE206turbo#"
cash_pay = "10000"
result = run(
    data,
    user_name,
    pass_word,
    int(cash_pay)
)

print(result)