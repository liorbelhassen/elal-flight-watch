"""Browser experiment. Monitoring remains off until the live adapter is calibrated."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from logic import AVAILABLE, ABSENT, UNKNOWN, decide, expired, event_for, next_state

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "diagnostics"
STATE = ROOT / ".state" / "status.json"
CONFIG = ROOT / "config.json"
BLOCKED = re.compile(r"captcha|access denied|verify you are human|unusual traffic|request blocked|incident id", re.I)
SOLD_OUT = re.compile(r"sold\s*out|not available|unavailable|אזלו|אין מקומות|טיסה מלאה", re.I)


def telegram(method, payload):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN secret.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram HTTP {e.code}; check secrets and bot permissions.") from None
    except Exception:
        raise RuntimeError("Telegram request failed; no credentials logged.") from None
    if not data.get("ok"):
        raise RuntimeError("Telegram rejected request; check configuration.")
    return data["result"]


def send(text):
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not re.fullmatch(r"[1-9]\d*", chat_id):
        raise RuntimeError("Set TELEGRAM_CHAT_ID to your private-chat numeric ID.")
    telegram("sendMessage", {"chat_id": chat_id, "text": text,
                             "link_preview_options": {"is_disabled": True}})


def bootstrap(username):
    username = username.lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
        raise RuntimeError("Provide your exact Telegram username, from Telegram Settings.")
    updates = telegram("getUpdates", {"limit": 100, "timeout": 0})
    candidates = set()
    for update in updates:
        m = update.get("message", {})
        sender, chat = m.get("from", {}), m.get("chat", {})
        if (chat.get("type") == "private" and not sender.get("is_bot")
                and sender.get("id") == chat.get("id")
                and sender.get("username", "").lower() == username
                and m.get("text", "").strip() == "/start LY385"
                and time.time() - m.get("date", 0) < 86400):
            candidates.add(chat["id"])
    if len(candidates) != 1:
        raise RuntimeError("Send /start LY385 to your own bot, then retry with your exact Telegram username.")
    chat_id = candidates.pop()
    telegram("sendMessage", {"chat_id": chat_id, "text":
             "✅ חיבור הטלגרם הצליח. זו הודעת בדיקה בלבד; מעקב הטיסה עדיין לא הופעל."})
    print(f"TELEGRAM_CHAT_ID={chat_id}")


def search_url(mode):
    url = os.environ.get("SEARCH_URL", "").strip()
    if not url and mode == "diagnose":
        return "https://booking.elal.com/booking/flights?market=IL&lang=en"
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "booking.elal.com"
            or parts.username or parts.password):
        raise RuntimeError("SEARCH_URL must be a public HTTPS search URL on booking.elal.com.")
    return url


def write_report(data):
    REPORT.mkdir(exist_ok=True)
    (REPORT / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def inspect_once(config, url, diagnostic=False):
    from playwright.sync_api import sync_playwright
    report = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "target": {k: config[k] for k in ("flight", "origin", "destination", "date", "departure")}}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(locale="en-US", timezone_id="Asia/Jerusalem")
            page = context.new_page()
            page.set_default_timeout(12000)
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            report["http_status"] = response.status if response else None
            if response and response.status >= 400:
                report.update(status=UNKNOWN, reason="Search page returned an HTTP error.")
                write_report(report)
                return UNKNOWN, report
            # Allow the public app to render; do not retry or bypass challenges.
            page.wait_for_timeout(5000)
            text = page.locator("body").inner_text(timeout=10000)
            if BLOCKED.search(text):
                report.update(status=UNKNOWN, reason="Site requested human verification or blocked access.")
                write_report(report)
                return UNKNOWN, report
            for action in config.get("public_search_actions", []):
                locator = page.locator(action["selector"])
                if action["type"] == "click":
                    locator.click()
                elif action["type"] == "fill":
                    locator.fill(action["value"])
                else:
                    raise RuntimeError("Unsupported public-search action.")
            if diagnostic:
                # No cookies, raw HTML, request bodies, screenshots or storage state are saved.
                report["page_text"] = page.locator("body").inner_text()[:16000]
                report["controls"] = page.locator("input,button,[role=button],[role=combobox]").evaluate_all("""els => els.slice(0,120).map(e => ({
                  tag:e.tagName, type:e.getAttribute('type'), id:e.id,
                  classes:e.className, name:e.getAttribute('name'),
                  label:e.getAttribute('aria-label'), placeholder:e.getAttribute('placeholder'),
                  text:(e.innerText||'').slice(0,160)}))""")
                report.update(status="DIAGNOSTIC_ONLY", reason="Live selectors require calibration before monitoring.")
                write_report(report)
                return UNKNOWN, report
            if not config.get("calibrated"):
                report.update(status=UNKNOWN, reason="Adapter not calibrated; monitoring disabled.")
                write_report(report)
                return UNKNOWN, report
            selectors = config["selectors"]
            if not all(selectors.get(k) for k in ("results_ready", "query_summary", "flight_card", "price", "select_button")):
                raise RuntimeError("Missing calibrated selectors.")
            page.locator(selectors["results_ready"]).first.wait_for(state="visible", timeout=45000)
            summary = page.locator(selectors["query_summary"]).inner_text()
            cards = []
            locators = page.locator(selectors["flight_card"])
            for i in range(min(locators.count(), 100)):
                card = locators.nth(i)
                if not card.is_visible():
                    continue
                card_text = card.inner_text()
                if not re.search(r"(?<![A-Z0-9])LY\s*0?385(?!\d)", card_text, re.I):
                    cards.append({"text": card_text})
                    continue
                price = card.locator(selectors["price"])
                button = card.locator(selectors["select_button"])
                cards.append({"text": card_text,
                              "price": price.first.inner_text() if price.count() else "",
                              "button_visible": bool(button.count() and button.first.is_visible()),
                              "button_enabled": bool(button.count() and button.first.is_enabled()),
                              "sold_out": bool(SOLD_OUT.search(card_text))})
            blocked = bool(BLOCKED.search(page.locator("body").inner_text()))
            status, reason = decide(config, summary, cards, blocked=blocked)
            report.update(status=status, reason=reason, query_summary=summary,
                          matched_cards=[c for c in cards if re.search(r"LY\s*0?385(?!\d)", c["text"], re.I)])
            write_report(report)
            return status, report
        finally:
            browser.close()


def run_check(config, url):
    if not config.get("calibrated"):
        raise RuntimeError("Adapter is not calibrated. Run diagnose and finish live validation first.")
    try:
        status, report = inspect_once(config, url)
    except Exception:
        status, report = UNKNOWN, {"status": UNKNOWN, "reason": "Browser/search failed; not evidence that the flight is full."}
        write_report(report)
    if status == AVAILABLE:
        # Confirm with another fresh browser, with no prior session/cookies.
        try:
            confirmation, second = inspect_once(config, url)
        except Exception:
            confirmation = UNKNOWN
        if confirmation != AVAILABLE:
            status = UNKNOWN
            report.update(status=UNKNOWN, reason="Positive result did not repeat in a fresh search.")
        else:
            report["confirmed_twice"] = True
        write_report(report)
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    now = time.time()
    event = event_for(state, status, now)
    if event == "available":
        send("✈️ נמצאה הצעה לבחירה לטיסת אל על LY385\n"
             "תל אביב → רומא, 2.11.2026, 06:10\n"
             "נוסע אחד, הלוך בלבד, תיירים. התוצאה חזרה בשתי בדיקות טריות.\n"
             "המחיר והכרטיס עדיין כפופים לאישור באתר; לא בוצעה הזמנה.\n" + url)
    elif event == "error":
        send("⚠️ שלוש בדיקות רצופות לא הצליחו לאמת זמינות ל־LY385.\n"
             "אין להסיק שהטיסה מלאה. בדוק את ההרצה האחרונה ב־GitHub Actions.")
    elif event == "heartbeat":
        send("✅ בדיקת המעקב היומית הצליחה.\n" +
             ("טיסת LY385 לא הוצעה לרכישה בחיפוש האחרון; זו אינה הוכחה שהטיסה מלאה."
              if status == ABSENT else "טיסת LY385 עדיין מוצגת עם הצעה לבחירה.") +
             "\nתל אביב → רומא, 2.11.2026, 06:10.")
    # Persist only AFTER notification succeeds, allowing retry if Telegram fails.
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(next_state(state, status, now, event)))
    print(f"Result: {status}. See diagnostics/report.json for the reason.")
    return 2 if status == UNKNOWN else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["diagnose", "bootstrap", "test-telegram", "check"])
    parser.add_argument("--username", default="")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.mode == "bootstrap":
        bootstrap(args.username)
        return 0
    if args.mode == "test-telegram":
        send("✅ הודעת בדיקה: הטלגרם מחובר. אין בהודעה זו מידע על זמינות טיסה.")
        return 0
    if expired(config):
        print("Flight departure has passed; no further airline requests.")
        return 0
    url = search_url(args.mode)
    if args.mode == "diagnose":
        inspect_once(config, url, diagnostic=True)
        print("Diagnostic saved. This run does not start monitoring or send flight alerts.")
        return 0
    return run_check(config, url)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never stringify browser/HTTP exceptions: their text can contain secret URLs.
        detail = str(exc) if isinstance(exc, RuntimeError) else "Operation failed; no credentials logged."
        write_report({"status": UNKNOWN, "reason": detail, "error_type": type(exc).__name__})
        print(f"UNKNOWN: {detail}")
        raise SystemExit(2)
