"""LY385 TLV->FCO 2026-11-02 availability watch (Google Flights source, Telegram alerts)."""
import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import gflights
from logic import AVAILABLE, ABSENT, UNKNOWN, decide, expired, event_for, next_state

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "diagnostics"
STATE = ROOT / ".state" / "status.json"
CONFIG = ROOT / "config.json"
ELAL_URL = "https://booking.elal.com/booking/flights?market=IL&lang=en"
ISRAEL = ZoneInfo("Asia/Jerusalem")


def israel_time(ts=None):
    return datetime.fromtimestamp(ts or time.time(), ISRAEL).strftime("%d.%m.%Y %H:%M")


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
    # The repository is public, so the chat ID goes only to the user's Telegram, never to the log.
    telegram("sendMessage", {"chat_id": chat_id, "text":
             "✅ חיבור הטלגרם הצליח (הודעת בדיקה, המעקב עדיין לא פעיל).\n"
             f"מספר הצ'אט שלך: {chat_id}\n"
             "שמור אותו ב־GitHub כ־Secret בשם TELEGRAM_CHAT_ID."})
    print("Private chat found. Its ID was sent to your Telegram chat (not printed here).")


def write_report(data):
    REPORT.mkdir(exist_ok=True)
    (REPORT / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n")


def brief(offer):
    s = offer["segments"]
    return {"flights": "+".join(f"{x['carrier']}{x['number']}" for x in s),
            "departure": s[0]["departure"], "stops": len(s) - 1, "price": offer["price"]}


def inspect(config, date=None):
    cfg = dict(config, date=date or config["date"])
    result = gflights.search(cfg)
    status, reason, offer, code = decide(cfg, result)
    report = {"checked_at_israel": israel_time(), "source": "Google Flights", "date": cfg["date"],
              "query": f"{cfg['origin']}->{cfg['destination']} one-way, {cfg['adults']} adult, economy, {cfg['currency']}",
              "url": result.get("url"), "http_status": result.get("http_status"),
              "status": status, "reason": reason, "code": code,
              "match": brief(offer) if offer else None,
              "nonstop_results": [brief(o) for o in result.get("offers", []) if len(o["segments"]) == 1]}
    return status, report, offer, code


def alert_text(config, offer, url, ts):
    return ("✈️ LY385 מוצעת לבחירה ב־Google Flights\n"
            "אל על, תל אביב (TLV) → רומא פיומיצ'ינו (FCO)\n"
            f"יום שני 2.11.2026, יציאה {offer['segments'][0]['departure']}, טיסה ישירה\n"
            "מבוגר אחד, הלוך בלבד, תיירים\n"
            f"מחיר מוצג: ₪{offer['price']} (נמצא בשתי בדיקות טריות)\n"
            f"נבדק: {israel_time(ts)} (שעון ישראל)\n"
            "המקור הוא Google Flights ולא אתר אל על; המחיר והמקום כפופים לאישור בזמן ההזמנה. לא בוצעה הזמנה.\n"
            f"החיפוש: {url}")


def run_check(config, sleep=time.sleep):
    status, report, offer, code = inspect(config)
    if status == AVAILABLE:
        sleep(config.get("confirm_delay_seconds", 60))
        second, report2, offer2, _ = inspect(config)
        if second != AVAILABLE or brief(offer2)["departure"] != brief(offer)["departure"]:
            status, code = UNKNOWN, "unconfirmed"
            report.update(status=UNKNOWN, code=code,
                          reason=f"Positive result did not repeat in a fresh search ({report2['reason']}).")
        else:
            report["confirmed_twice"] = True
            offer = offer2
    write_report(report)
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    now = time.time()
    event = event_for(state, status, now, code)
    if event == "available":
        send(alert_text(config, offer, report["url"], now))
    elif event == "mismatch":
        send("⚠️ שינוי אפשרי בלוח הזמנים של LY385 ב־2.11.2026\n"
             f"{report['reason']}\n"
             "המעקב ממשיך לחפש רק את 06:10 ולא שולח התראת זמינות לשעה אחרת. "
             "אם תרצה לעקוב אחרי השעה החדשה, כתוב זאת במפורש.\n" + report["url"])
    elif event == "error":
        send("⚠️ שלוש בדיקות רצופות של LY385 לא הצליחו להשלים חיפוש מאומת.\n"
             f"סיבה אחרונה: {report['reason']}\n"
             "אין להסיק מכך שהטיסה מלאה. ההתראה הבאה מסוג זה תישלח לכל היותר בעוד 24 שעות.")
    elif event == "heartbeat":
        send("✅ בדיקה יומית: המעקב אחרי LY385 (2.11.2026, 06:10, TLV→FCO) פועל.\n" +
             ("בחיפוש האחרון LY385 לא הוצעה לרכישה ב־Google Flights."
              if status == ABSENT else "LY385 עדיין מוצעת לבחירה במחיר ₪%s." % offer["price"]) +
             f"\nנבדק: {israel_time(now)}")
    # Persist only after the notification succeeded, so a failed send is retried next hour.
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(next_state(state, status, now, event), indent=1))
    print(f"Result: {status} - {report['reason']} (event: {event})")
    return 2 if status == UNKNOWN else 0


def probe(config):
    """Live adapter validation without Telegram or state: target date + same-flight positive control."""
    target = inspect(config)[1]
    control = inspect(config, config["positive_control_date"])[1]
    write_report({"target": target, "positive_control": control})
    for name, r in (("target", target), ("control", control)):
        print(f"{name} {r['date']}: {r['status']} - {r['reason']}")
    return 0 if control["status"] == AVAILABLE and target["status"] != UNKNOWN else 2


def diagnose(config):
    req = urllib.request.Request(ELAL_URL, headers={"User-Agent": gflights.UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            code, body = r.status, r.read(600).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(600).decode("utf-8", "replace")
    except Exception as e:
        code, body = None, type(e).__name__
    body = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|[0-9a-f:]{8,}", "[ip omitted]", body)
    print(f"EL AL booking site from this runner: HTTP {code}; {' '.join(body.split())[:200]}")
    return probe(config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["probe", "diagnose", "bootstrap", "test-telegram", "check"])
    parser.add_argument("--username", default="")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.mode == "bootstrap":
        bootstrap(args.username)
        return 0
    if args.mode == "test-telegram":
        send("✅ הודעת בדיקה: הטלגרם מחובר למעקב LY385. אין בהודעה זו מידע על זמינות.")
        return 0
    if expired(config):
        print("Flight departure has passed; no further searches.")
        return 0
    if args.mode == "check":
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            if not os.environ.get(key, "").strip():
                raise RuntimeError(f"Missing {key} secret; check mode needs Telegram.")
        return run_check(config)
    return diagnose(config) if args.mode == "diagnose" else probe(config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else "Operation failed; no credentials logged."
        write_report({"status": UNKNOWN, "reason": detail, "error_type": type(exc).__name__})
        print(f"UNKNOWN: {detail}")
        raise SystemExit(2)
