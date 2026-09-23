#!/usr/bin/env python3
"""
Axiom Autonomous Daily Token Sync & Service Manager
Runs 100% server-side on Oracle Cloud Linux.
Headless, zero-browser: authenticates directly with Flattrade REST endpoints.
"""

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import pyotp
import requests

# Ensure line-buffered output so logs appear immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

ROOT = Path("/home/opc/Axiom")
ENV_PATH = ROOT / ".env"
CODE_FILE = ROOT / "data" / "fresh_request_code.txt"
ALERT_FILE = ROOT / "data" / "auth_alert.json"
IST = timezone(timedelta(hours=5, minutes=30))


def log(msg: str):
	now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
	print(f"[{now_ist}] {msg}", flush=True)


def clear_auth_alert():
	if ALERT_FILE.exists():
		try:
			ALERT_FILE.unlink()
		except Exception:
			pass


def trigger_auth_alert(err_msg: str):
	now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
	alert_payload = {
		"timestamp": now_ist,
		"status": "AUTH_FAILED",
		"error": err_msg,
		"action_required": "Flattrade morning authentication failed. Check credentials or broker endpoints.",
	}
	ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
	ALERT_FILE.write_text(json.dumps(alert_payload, indent=2), encoding="utf-8")
	log(f"[CRITICAL ALERT] Auth failure written to {ALERT_FILE}")

	env = load_env()
	webhook = env.get("ALERT_WEBHOOK_URL")
	if webhook:
		try:
			requests.post(webhook, json={"text": f"[AXIOM ALERT] Flattrade Auth Failed at {now_ist}: {err_msg}"}, timeout=5)
			log(f"Alert successfully sent to configured webhook")
		except Exception as e:
			log(f"Failed to post to webhook: {e}")


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def is_already_authenticated_today() -> bool:
    """Check if token was already generated today and Axiom is currently running."""
    if not CODE_FILE.exists():
        return False

    res = subprocess.run(["pgrep", "-x", "axiom"], capture_output=True, text=True)
    if res.returncode != 0:
        return False

    mtime = datetime.fromtimestamp(CODE_FILE.stat().st_mtime, tz=IST)
    today_ist = datetime.now(IST).date()
    if mtime.date() == today_ist:
        pid = res.stdout.strip()
        log(f"[INFO] Axiom is already running (PID: {pid}) with today's token generated at {mtime.strftime('%H:%M:%S IST')}.")
        return True

    return False


def update_env_code(code: str):
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith("FLAT_REQUEST_CODE="):
            lines[i] = f"FLAT_REQUEST_CODE={code}"
            found = True
            break
    if not found:
        lines.append(f"FLAT_REQUEST_CODE={code}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Updated FLAT_REQUEST_CODE in {ENV_PATH}")

    CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODE_FILE.write_text(code.strip() + "\n", encoding="utf-8")
    log(f"Saved token to {CODE_FILE}")


def get_token():
    """
    Attempts to authenticate with Flattrade.
    Returns:
        (code, should_abort)
        code: string request_code if successful, else None
        should_abort: bool True if fatal error (wrong password, blocked, etc.), False to retry
    """
    env = load_env()
    api_key = env.get("FLAT_API_KEY")
    user_id = env.get("FLAT_USER_ID")
    password = env.get("FLAT_PASSWORD")
    totp_key = env.get("FLAT_TOTP_KEY")

    if not all([api_key, user_id, password, totp_key]):
        log("ERROR: Missing FLAT credentials in .env")
        return None, True

    session_url = "https://authapi.flattrade.in/auth/session"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": "https://auth.flattrade.in/",
        "Origin": "https://auth.flattrade.in",
    }

    try:
        r = requests.post(session_url, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"Flattrade session endpoint returned HTTP {r.status_code}")
            return None, False
        sid = r.text.strip()
        if not sid or len(sid) < 10 or "<html" in sid.lower():
            log(f"Broker likely in nightly maintenance (received sid='{sid[:30]}...').")
            return None, False
    except Exception as e:
        log(f"Failed to connect to session endpoint: {e}")
        return None, False

    # Single SHA256 as verified directly from Flattrade auth frontend (app.js)
    p_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        totp_code = pyotp.TOTP(totp_key).now()
    except Exception as e:
        log(f"Failed to compute TOTP: {e}")
        return None, True

    payload = {
        "UserName": user_id,
        "Rd": "",
        "Password": p_hash,
        "PAN_DOB": totp_code,
        "App": "",
        "ClientID": "",
        "Key": "",
        "APIKey": api_key,
        "Sid": sid,
        "Override": "",
        "Source": "AUTHPAGE",
    }

    auth_url = "https://authapi.flattrade.in/ftauth"
    auth_headers = {**headers, "Content-Type": "application/json"}

    try:
        r2 = requests.post(auth_url, json=payload, headers=auth_headers, timeout=15)
        if r2.status_code != 200:
            log(f"Flattrade ftauth endpoint returned HTTP {r2.status_code}")
            return None, False

        try:
            data = r2.json()
        except Exception:
            log(f"ftauth response is not JSON: {r2.text[:100]}")
            return None, False

        redirect = data.get("RedirectURL", "") or str(data)
        m = re.search(r"[?&](?:code|request_code)=([a-zA-Z0-9_-]+)", redirect)
        if m:
            code = m.group(1)
            log(f">>> SUCCESS! Obtained fresh request_code: {code}")
            return code, False
        else:
            emsg = data.get("emsg", "") or str(data)[:120]
            log(f"Flattrade auth response: {emsg}")
            log("FATAL: Flattrade rejected login. Stopping immediately to prevent any account lockout.")
            return None, True
    except Exception as e:
        log(f"Error during ftauth request: {e}")
        return None, False


def run_morning_pipeline():
    log("Running Nifty 500 Dynamic Universe Scanner...")
    scanner_script = ROOT / "scanner.py"
    if scanner_script.exists():
        try:
            res = subprocess.run([sys.executable, str(scanner_script)], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
            last_line = res.stdout.strip().splitlines()[-1] if res.stdout.strip().splitlines() else "OK"
            log(f"Scanner completed: {last_line}")
        except Exception as e:
            log(f"Scanner warning: {e}")

    log("Running Sentinel AI Macro & Volatility Gate...")
    sentinel_script = ROOT / "sentinel.py"
    if sentinel_script.exists():
        try:
            res = subprocess.run([sys.executable, str(sentinel_script)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
            last_line = res.stdout.strip().splitlines()[-1] if res.stdout.strip().splitlines() else "OK"
            log(f"Sentinel completed: {last_line}")
        except Exception as e:
            log(f"Sentinel warning: {e}")


def restart_axiom():
    log("Restarting Axiom bot on server...")
    stop_script = ROOT / "stop.sh"
    start_script = ROOT / "start.sh"
    status_script = ROOT / "status.sh"

    if stop_script.exists():
        subprocess.run([str(stop_script)], cwd=str(ROOT))
        time.sleep(2)

    run_morning_pipeline()

    if start_script.exists():
        subprocess.run([str(start_script)], cwd=str(ROOT))
        time.sleep(2)

    if status_script.exists():
        res = subprocess.run([str(status_script)], cwd=str(ROOT), capture_output=True, text=True)
        log("=== Current Bot Status ===")
        print(res.stdout)


def main():
    parser = argparse.ArgumentParser(description="Axiom Autonomous Token Sync")
    parser.add_argument("--once", action="store_true", help="Try once and exit immediately")
    parser.add_argument("--force", action="store_true", help="Force sync even if already authenticated today")
    parser.add_argument("--retries", type=int, default=120, help="Max retry attempts (default: 120 = 60 mins)")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between attempts (default: 30)")
    args = parser.parse_args()

    log("==================================================")
    log("     AXIOM AUTONOMOUS CLOUD SYNC SERVICE          ")
    log("==================================================")

    if not args.force and is_already_authenticated_today():
        log("Axiom is already running with today's token. No action required.")
        return 0

    if args.once:
        code, abort = get_token()
        if code:
            update_env_code(code)
            restart_axiom()
            clear_auth_alert()
            return 0
        else:
            log("Single attempt failed.")
            trigger_auth_alert("Single auth attempt failed.")
            return 1

    max_retries = args.retries
    for attempt in range(1, max_retries + 1):
        log(f"Attempt {attempt}/{max_retries} to fetch morning session token...")
        code, abort = get_token()
        if code:
            update_env_code(code)
            restart_axiom()
            clear_auth_alert()
            log("=== Axiom Token Sync Completed Successfully! ===")
            return 0

        if abort:
            err_msg = "Aborting retry loop due to fatal authentication error (broker rejection)."
            log(err_msg)
            trigger_auth_alert(err_msg)
            return 1

        log(f"Waiting {args.interval}s before next attempt...")
        time.sleep(args.interval)

    err_msg = f"Reached max retries ({max_retries}) without obtaining valid session token."
    log(f"ERROR: {err_msg}")
    trigger_auth_alert(err_msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
