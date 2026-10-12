"""Google Flights search: build the query, fetch it, parse the embedded results data."""
import base64
import json
import re
import urllib.error
import urllib.request

BLOCKED = re.compile(r"unusual traffic|google\.com/sorry|before you continue to google", re.I)
DATA = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);</script>", re.S)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


def _varint(n):
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _bytes(field, payload):
    payload = payload.encode() if isinstance(payload, str) else payload
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _int(field, value):
    return _varint(field << 3) + _varint(value)


def tfs(date, origin, destination, adults=1):
    """Protobuf query: one leg, `adults` adults, economy (1), one-way (2)."""
    leg = _bytes(2, date) + _bytes(13, _bytes(2, origin)) + _bytes(14, _bytes(2, destination))
    msg = _bytes(3, leg) + _bytes(8, bytes([1] * adults)) + _int(9, 1) + _int(19, 2)
    return base64.urlsafe_b64encode(msg).decode().rstrip("=")


def search_url(config, date=None):
    q = tfs(date or config["date"], config["origin"], config["destination"], config["adults"])
    return f"https://www.google.com/travel/flights/search?tfs={q}&hl=en&gl=IL&curr={config['currency']}"


def _offers(data):
    found = []

    def walk(x):
        if not isinstance(x, list):
            return
        if (len(x) > 1 and isinstance(x[0], list) and len(x[0]) > 2 and isinstance(x[0][0], str)
                and isinstance(x[0][2], list) and x[0][2] and isinstance(x[0][2][0], list)
                and len(x[0][2][0]) > 22 and isinstance(x[1], list) and x[1] and isinstance(x[1][0], list)):
            found.append(x)
            return
        for y in x:
            walk(y)

    walk(data)
    offers = []
    for itinerary, fare in ((f[0], f[1]) for f in found):
        segments = []
        for s in itinerary[2]:
            dep = (s[8] or []) + [0, 0]
            segments.append({
                "carrier": s[22][0], "number": str(s[22][1]),
                "origin": s[3], "destination": s[6],
                "date": "%04d-%02d-%02d" % tuple(s[20]),
                "departure": "%02d:%02d" % (dep[0] or 0, dep[1] or 0),
            })
        price = fare[0][1] if len(fare[0]) > 1 else None
        offers.append({"airline": itinerary[0], "segments": segments,
                       "price": price if isinstance(price, (int, float)) else None})
    return offers


def parse(html):
    m = DATA.search(html)
    if not m and BLOCKED.search(html):
        return {"ok": False, "reason": "Google returned a block/consent/CAPTCHA page."}
    if not m:
        return {"ok": False, "reason": "Results data block missing (page layout changed or incomplete)."}
    try:
        offers = _offers(json.loads(m.group(1)))
    except (ValueError, TypeError, IndexError, KeyError):
        return {"ok": False, "reason": "Results data could not be parsed (format changed)."}
    if not offers:
        return {"ok": False, "reason": "No flight results found in page data."}
    return {"ok": True, "reason": "Results parsed.", "offers": offers}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def search(config, date=None):
    url = search_url(config, date)
    try:
        status, html = fetch(url)
    except Exception as e:
        return {"ok": False, "reason": f"Connection failed ({type(e).__name__}).", "url": url}
    result = parse(html) if status == 200 else {"ok": False, "reason": f"Google Flights HTTP {status}."}
    result.update(url=url, http_status=status, bytes=len(html))
    return result
