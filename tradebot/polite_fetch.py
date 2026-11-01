"""
Polite HTTP GET with per-edge adaptive throttling, exponential backoff on 429/503,
Retry-After support, and failure classification.

What it does
  - Paces requests per *registrable domain* (marvel.fandom.com and dc.fandom.com share one
    pacer, because they share one CDN edge and one rate limit).
  - Every 429/503 (or a MediaWiki `ratelimited`/`maxlag` error body) doubles that host's pause
    multiplier up to BACKOFF_MAX; every clean response decays it by BACKOFF_DECAY. The edge is
    paced at the SLOWEST multiplier any of its subdomains currently has.
  - After THROTTLE_STRIKES consecutive throttles an optional `on_quarantine(host, strikes, mult)`
    callback fires, so a caller can defer that host instead of hammering it.
  - `get()` returns the body, `get_json()` returns parsed JSON; both can fill an `outcome` dict
    with {"ok": bool, "why": "ok|http-404|throttled|http-<code>|nonjson|network|unknown"} so a
    caller can tell "the page does not exist" from "we were throttled" -- the distinction that
    otherwise turns rate limiting into fake empty results.

Source: panscriptum  src/feats.py (_throttle, note_throttled, note_ok, registrable_domain, api)
        and src/wiki_source.py (_get). Merged and decoupled from project globals.
        Copied from code-library python/http/polite_fetch.py; Trade Bot changes: get_json passes
        headers (Alpaca/Polygon keys go in headers, never URLs), HOST_PAUSE set for the data APIs.
Dependencies: none (stdlib urllib).
Adapt: set USER_AGENT (or env POLITE_FETCH_UA) to something with a contact; tune BASE_PAUSE and
       HOST_PAUSE (substring -> seconds) per site; wire `on_quarantine` to your own skip list.
"""
import collections
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Wikimedia's UA policy asks for a contact; a real UA is also what stops Fandom answering 403.
# SEC asks for a contact in the UA; set POLITE_FETCH_UA to one.
USER_AGENT = os.environ.get("POLITE_FETCH_UA", "TradeBot/0.1 279877749+GeddyLifeson@users.noreply.github.com")

BASE_PAUSE = 0.34                                     # seconds between requests to one edge
# polygon free tier is 5 calls/min; kraken public ~1/s; sec 10/s; alpaca 200/min
HOST_PAUSE = {"polygon.io": 12.5, "kraken.com": 1.1, "sec.gov": 0.15, "alpaca.markets": 0.35,
              "coinbase.com": 0.15}
TIMEOUT = 45

BACKOFF_MAX = 32.0       # 0.34s base becomes ~11s at the ceiling -- slow, never stopped
BACKOFF_GROWTH = 2.0
BACKOFF_DECAY = 0.8      # earn speed back slowly, lose it fast
THROTTLE_STRIKES = 3

# Browser UA for hosts that refuse every non-browser client (read them at human pace).
UA_OVERRIDES = {}

_TWO_LABEL_SUFFIXES = frozenset((
    "co.uk", "org.uk", "ac.uk", "me.uk", "com.au", "net.au", "org.au", "co.nz", "co.za",
    "co.jp", "or.jp", "ne.jp", "co.kr", "com.br", "com.cn", "com.mx", "com.tr", "com.ar",
))

_HOST_LOCKS = collections.defaultdict(threading.Lock)
_HOST_LAST = {}
_BACKOFF = {}
_STRIKE = {}

on_quarantine = None     # optional callback(host, strikes, multiplier)


def registrable_domain(host):
    """The domain a remote EDGE belongs to -- the unit a rate limit is actually enforced on.

    Errs toward SHARING: an unknown multi-label suffix collapses unrelated hosts onto one pacer
    (slower than needed) rather than splitting one edge into many (which gets you IP-banned).
    """
    h = (host or "").strip().lower()
    if not h:
        return h
    h = h.split("/")[0].split(":")[0].rstrip(".")
    labels = [p for p in h.split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _pause_for(host):
    for frag, val in HOST_PAUSE.items():
        if frag in host:
            return val
    return BASE_PAUSE


def throttle(host):
    """Block until it is polite to send one request to `host`."""
    dom = registrable_domain(host)
    with _HOST_LOCKS[dom]:
        base = _pause_for(dom)
        mult = _BACKOFF.get(host, 1.0)
        for h, m in tuple(_BACKOFF.items()):          # snapshot: other edges insert concurrently
            if m > mult and registrable_domain(h) == dom:
                mult = m
        wait = (base * mult) - (time.time() - _HOST_LAST.get(dom, 0.0))
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST[dom] = time.time()


def note_throttled(host):
    """Call on a 429/503. Widens this host's pause immediately. -> new multiplier."""
    with _HOST_LOCKS[registrable_domain(host)]:
        _BACKOFF[host] = min(BACKOFF_MAX, _BACKOFF.get(host, 1.0) * BACKOFF_GROWTH)
        _STRIKE[host] = _STRIKE.get(host, 0) + 1
        strikes, mult = _STRIKE[host], _BACKOFF[host]
    if strikes >= THROTTLE_STRIKES and on_quarantine is not None:
        try:
            on_quarantine(host, strikes, mult)
        except Exception:
            pass
    return mult


def note_ok(host):
    """Call on a clean response. Decays the backoff and clears the strike run."""
    with _HOST_LOCKS[registrable_domain(host)]:
        cur = _BACKOFF.get(host, 1.0)
        if cur > 1.0:
            _BACKOFF[host] = max(1.0, cur * BACKOFF_DECAY)
        _STRIKE[host] = 0


def backoff_state():
    """-> {host: multiplier} for hosts currently slowed."""
    return {h: round(m, 2) for h, m in tuple(_BACKOFF.items()) if m > 1.0}


def _retry_after(headers):
    # Retry-After may be an HTTP-date rather than seconds; a date falls back to the ladder.
    try:
        return int((headers or {}).get("Retry-After") or 0)
    except (TypeError, ValueError):
        return 0


def get(url, retries=2, timeout=TIMEOUT, outcome=None, headers=None):
    """GET `url` politely. -> body text, or None (with `outcome["why"]` saying why)."""
    def stamp(ok, why):
        if outcome is not None:
            outcome["ok"], outcome["why"] = ok, why

    if outcome is not None:
        outcome.clear()
        stamp(False, "unknown")
    host = urllib.parse.urlparse(url).netloc
    hdrs = {"User-Agent": UA_OVERRIDES.get(host, USER_AGENT)}
    hdrs.update(headers or {})
    for attempt in range(retries + 1):
        try:
            throttle(host)
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            note_ok(host)
            stamp(True, "ok")
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                stamp(False, "http-404")          # the one clean negative
                return None
            if e.code in (429, 503):
                wait = _retry_after(e.headers) or (5 * (attempt + 1) ** 2)
                note_throttled(host)
                if attempt == retries:
                    stamp(False, "throttled")
                    return None
                time.sleep(min(wait, 120))
                continue
            if attempt == retries:
                stamp(False, "http-%d" % e.code)
                return None
            time.sleep(2 + attempt * 4)
        except Exception:
            if attempt == retries:
                stamp(False, "network")
                return None
            time.sleep(2 + attempt * 4)
    return None


def get_json(url, retries=2, timeout=TIMEOUT, outcome=None, headers=None):
    """GET + parse JSON. Treats a MediaWiki `{"error": {"code": "ratelimited"|"maxlag"}}` 200 as a
    throttle, and a non-JSON 200 (WAF/login interstitial) as "nonjson", not as a network fault."""
    oc = {} if outcome is None else outcome
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(retries + 1):
        body = get(url, retries=0, timeout=timeout, outcome=oc, headers=headers)
        if body is None:
            if oc.get("why") in ("throttled", "network") and attempt < retries:
                time.sleep(min(5 * (attempt + 1) ** 2, 120))
                continue
            return None
        try:
            parsed = json.loads(body)
        except ValueError:
            oc["ok"], oc["why"] = False, "nonjson"
            if attempt < retries:
                time.sleep(2 + attempt * 4)
                continue
            return None
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or "unknown")
            if code in ("ratelimited", "maxlag"):
                note_throttled(host)
                if attempt < retries:
                    time.sleep(min(5 * (attempt + 1) ** 2, 120))
                    continue
                oc["ok"], oc["why"] = False, "throttled"
                return None
            oc["ok"], oc["why"] = False, "api-error-" + code
            return None
        return parsed
    return None
