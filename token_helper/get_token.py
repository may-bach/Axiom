# token_helper/get_token.py
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure dotenv is loaded from the root .env
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv()

try:
    import pyotp
except ImportError:
    pyotp = None

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def get_request_code():
    api_key = os.getenv("FLAT_API_KEY")
    user_id = os.getenv("FLAT_USER_ID")
    password = os.getenv("FLAT_PASSWORD")
    pan_last5 = os.getenv("FLAT_PAN_LAST5")
    totp_key = os.getenv("FLAT_TOTP_KEY")

    if not all([api_key, user_id, password]):
        print("[ERROR] Missing required environment variables (FLAT_API_KEY, FLAT_USER_ID, FLAT_PASSWORD). Check .env")
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("[1/4] Navigating to Flattrade authorization page...")
            auth_url = f"https://auth.flattrade.in/?app_key={api_key}"
            page.goto(auth_url, wait_until="networkidle", timeout=30000)

            print("[2/4] Entering credentials...")
            u_inp = page.get_by_placeholder("User ID")
            u_inp.click()
            u_inp.press_sequentially(user_id, delay=30)

            p_inp = page.get_by_placeholder("Password")
            p_inp.click()
            p_inp.press_sequentially(password, delay=30)

            # Generate 6-digit TOTP code if TOTP key is provided
            if totp_key:
                if len(totp_key.strip()) > 8:
                    if pyotp is None:
                        print("[ERROR] 'pyotp' module is required to generate TOTP from secret. Please run: pip install pyotp")
                        browser.close()
                        return None
                    totp_code = pyotp.TOTP(totp_key.strip()).now()
                    print(f"Generated 6-digit TOTP: {totp_code}")
                else:
                    totp_code = totp_key.strip()

                t_inp = page.get_by_placeholder("OTP / TOTP")
                t_inp.click()
                t_inp.press_sequentially(totp_code, delay=30)

            captured_code = None

            def handle_request(req):
                nonlocal captured_code
                m = re.search(r"[?&](?:code|request_code)=([a-zA-Z0-9_-]+)", req.url)
                if m:
                    captured_code = m.group(1)
                    print(f">>> Intercepted authorization code: {captured_code}")

            page.on("request", handle_request)

            print("[3/4] Submitting login form...")
            page.get_by_role("button", name="Log In").click()
            page.wait_for_timeout(2000)

            # Check for Password Expiry / Reset notice
            try:
                page_text = page.locator("body").inner_text()
                if "RESETPASSWORD" in page_text or "click confirm to change your password" in page_text:
                    print("\n" + "=" * 65)
                    print("[ACTION REQUIRED] Flattrade password has EXPIRED!")
                    print("Flattrade requires periodic password rotation by exchange rules.")
                    print("1. Please log in manually at: https://web.flattrade.in")
                    print("2. Set your new password.")
                    print("3. Update FLAT_PASSWORD in your .env file with the new password.")
                    print("=" * 65 + "\n")
                    page.screenshot(path=str(ROOT_DIR / "token_helper" / "password_expired.png"))
                    browser.close()
                    return None

                # Check for Wrong PAN/DOB or 2FA challenge
                if "Wrong PAN/DOB" in page_text and pan_last5:
                    print("Detected secondary PAN/DOB prompt — filling PAN...")
                    page.get_by_label("PAN").fill(pan_last5)
                    page.get_by_role("button", name="Log In").click()
            except Exception:
                pass

            # Wait for code interception
            print("[4/4] Submitting and waiting for redirect code...")
            for _ in range(40):
                if captured_code:
                    break
                page.wait_for_timeout(500)

            if captured_code:
                print(f"\n>>> SUCCESS! Fresh code obtained: {captured_code}")
                browser.close()
                return captured_code
            else:
                url = page.url
                print(f"[ERROR] Failed to obtain code. Current URL: {url}")
                page.screenshot(path=str(ROOT_DIR / "token_helper" / "auth_failed.png"))
                print("Screenshot saved to token_helper/auth_failed.png")
                browser.close()
                return None

        except Exception as e:
            print(f"[ERROR] Exception during token retrieval: {e}")
            try:
                page.screenshot(path=str(ROOT_DIR / "token_helper" / "error.png"))
                print("Saved error screenshot to token_helper/error.png")
            except Exception:
                pass
            browser.close()
            return None

def update_env(code):
    for p in [ROOT_DIR / ".env", Path(".env")]:
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                found = False
                for i, line in enumerate(lines):
                    if line.startswith("FLAT_REQUEST_CODE="):
                        lines[i] = f"FLAT_REQUEST_CODE={code}"
                        found = True
                        break
                if not found:
                    lines.append(f"FLAT_REQUEST_CODE={code}")
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
                print(f"Updated FLAT_REQUEST_CODE in {p}")
                return
            except Exception as ex:
                print(f"Could not update {p}: {ex}")

if __name__ == "__main__":
    code = get_request_code()
    if code:
        update_env(code)
        try:
            fresh_txt = ROOT_DIR / "data" / "fresh_request_code.txt"
            fresh_txt.write_text(code, encoding="utf-8")
            print(f"Saved fresh code to {fresh_txt}")
        except Exception:
            pass
    else:
        sys.exit(1)