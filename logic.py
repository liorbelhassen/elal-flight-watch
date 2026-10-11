"""Conservative decisions; no network or browser dependency."""
from datetime import datetime, timezone
import re

AVAILABLE = "AVAILABLE"
ABSENT = "NOT_OFFERED"
UNKNOWN = "UNKNOWN"


def expired(config, now=None):
    return (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(config["stop_at"])


def context_valid(summary, checks):
    # Calibration must cover these independently, from the selected search summary.
    required = {"origin", "destination", "date", "adults", "one_way", "cabin"}
    return (required == {c.get("field") for c in checks}
            and all(c.get("pattern") and re.search(c["pattern"], summary, re.I) for c in checks))


def decide(config, summary, cards, ready=True, blocked=False):
    if blocked or not ready or not config.get("calibrated"):
        return UNKNOWN, "Site blocked, results incomplete, or adapter not calibrated."
    if not context_valid(summary, config.get("query_checks", [])):
        return UNKNOWN, "Search route/date/passenger/cabin/one-way could not be verified."
    flight = re.compile(r"(?<![A-Z0-9])LY\s*0?385(?!\d)", re.I)
    matches = [c for c in cards if flight.search(c["text"])]
    if not matches:
        return ABSENT, "Exact flight not offered in this successfully verified search."
    for card in matches:
        text = card["text"]
        if not all(re.search(r"(?<![A-Z0-9])" + re.escape(config[k]) + r"(?![A-Z0-9])", text, re.I)
                   for k in ("origin", "destination")):
            continue
        if not re.search(r"(?<!\d)" + re.escape(config["departure"]) + r"(?!\d)", text):
            continue
        direct = config.get("direct_flight_pattern")
        if not direct or not re.search(direct, text, re.I):
            continue
        if card.get("sold_out"):
            continue
        price = card.get("price", "")
        if (re.search(config["currency_pattern"], price, re.I)
                and re.search(r"[1-9]\d*", price)
                and card.get("button_visible") and card.get("button_enabled")):
            return AVAILABLE, "Exact direct flight has a positive price and enabled selection control."
    return UNKNOWN, "Flight appears, but sufficient bookable-offer evidence is missing."


def event_for(state, status, now):
    if status == AVAILABLE and state.get("last_known") != AVAILABLE:
        return "available"
    if status == UNKNOWN:
        count = state.get("errors", 0) + 1
        if count >= 3 and now - state.get("last_error_notice", 0) >= 86400:
            return "error"
    if status in (ABSENT, AVAILABLE) and now - state.get("last_heartbeat", 0) >= 86400:
        return "heartbeat"
    return None


def next_state(state, status, now, event=None):
    updated = dict(state)
    updated["last_check"] = now
    updated["errors"] = state.get("errors", 0) + 1 if status == UNKNOWN else 0
    if status != UNKNOWN:
        updated["last_known"] = status
        updated["last_success"] = now
    if event == "error":
        updated["last_error_notice"] = now
    if event in ("heartbeat", "available"):
        updated["last_heartbeat"] = now
    return updated
