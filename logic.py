"""Conservative decisions; no network dependency."""
from datetime import datetime, timezone

AVAILABLE = "AVAILABLE"
ABSENT = "NOT_OFFERED"
UNKNOWN = "UNKNOWN"
DAY = 86400


def expired(config, now=None):
    return (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(config["stop_at"])


def is_target(segment, config):
    return (segment["carrier"].upper() == config["carrier"]
            and segment["number"].isdigit() and int(segment["number"]) == int(config["number"]))


def decide(config, result):
    """Return (status, reason, offer, code)."""
    if not result.get("ok"):
        return UNKNOWN, result.get("reason", "Search failed."), None, "search"
    offers = result["offers"]
    for o in offers:
        s = o["segments"]
        if (s[0]["origin"] != config["origin"] or s[-1]["destination"] != config["destination"]
                or s[0]["date"] != config["date"]):
            return UNKNOWN, "Results do not match TLV->FCO on the requested date.", None, "context"
    matches = [o for o in offers if any(is_target(s, config) for s in o["segments"])]
    if not matches:
        return ABSENT, f"{config['flight']} not offered among {len(offers)} verified results.", None, None
    for o in matches:
        s = o["segments"]
        if len(s) != 1 or o["airline"] != config["carrier"]:
            continue
        if s[0]["departure"] != config["departure"]:
            return (UNKNOWN, f"{config['flight']} listed at {s[0]['departure']} instead of {config['departure']}.",
                    o, "mismatch")
        if not o["price"] or o["price"] <= 0:
            return UNKNOWN, f"{config['flight']} listed without a valid price.", o, "price"
        return AVAILABLE, f"{config['flight']} nonstop {s[0]['departure']} offered at {o['price']} {config['currency']}.", o, None
    return UNKNOWN, f"{config['flight']} appears only as part of a non-direct itinerary.", None, "context"


def event_for(state, status, now, code=None):
    if status == AVAILABLE and state.get("last_known") != AVAILABLE:
        return "available"
    if status == UNKNOWN:
        if code == "mismatch" and now - state.get("last_mismatch_notice", 0) >= DAY:
            return "mismatch"
        if state.get("errors", 0) + 1 >= 3 and now - state.get("last_error_notice", 0) >= DAY:
            return "error"
    if status in (ABSENT, AVAILABLE) and now - state.get("last_heartbeat", 0) >= DAY:
        return "heartbeat"
    return None


def next_state(state, status, now, event=None):
    updated = dict(state)
    updated["last_check"] = now
    updated["last_status"] = status
    updated["errors"] = state.get("errors", 0) + 1 if status == UNKNOWN else 0
    if status != UNKNOWN:
        updated["last_known"] = status
        updated["last_success"] = now
    if event == "error":
        updated["last_error_notice"] = now
    if event == "mismatch":
        updated["last_mismatch_notice"] = now
    if event in ("heartbeat", "available"):
        updated["last_heartbeat"] = now
    return updated
