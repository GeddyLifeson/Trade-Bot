"""Free data sources: listings for the catalogue, daily bars, macro series. No Yahoo, no orders.

Alpaca is reached only on the paper API host (listings) and the market-data host (bars).
Keys come from environment variables and travel in headers, never in URLs or logs.
"""
import csv
import datetime as dt
import io
import os
import random
import urllib.parse
import zipfile

from . import polite_fetch as pf
from . import store

BAR = ["open", "high", "low", "close", "volume"]
ALPACA_PAPER = "https://paper-api.alpaca.markets"
ALPACA_DATA = "https://data.alpaca.markets"
EQUITY_SOURCES = ["stooq", "polygon", "alpaca"]
CRYPTO_SOURCES = ["coinbase", "kraken"]
FRED_SERIES = ["DGS3MO", "DGS2", "DGS10", "T10Y2Y", "T10Y3M", "DTB3", "DFF", "BAMLH0A0HYM2",
               "VIXCLS", "NFCI", "ICSA", "UNRATE", "CPIAUCSL", "INDPRO", "UMCSENT"]
_KRAKEN_BASE = {"XBT": "BTC", "XDG": "DOGE"}


class MissingKey(RuntimeError):
    pass


class SourceError(RuntimeError):
    pass


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def final(rows):
    """Drop bars dated today (UTC): they are still forming, and storing one stores a price nobody closed at."""
    t = today().isoformat()
    return [r for r in rows if r["date"] < t]


def _json(url, what, headers=None):
    oc = {}
    data = pf.get_json(url, outcome=oc, headers=headers)
    if data is None:
        raise SourceError(f"{what}: {oc.get('why', 'unknown')}")
    return data


def _text(url, what, headers=None):
    oc = {}
    body = pf.get(url, outcome=oc, headers=headers)
    if body is None:
        raise SourceError(f"{what}: {oc.get('why', 'unknown')}")
    return body


def _env(*names):
    vals = [os.environ.get(n) for n in names]
    if not all(vals):
        raise MissingKey(" and ".join(names))
    return vals


def alpaca_headers():
    k, s = _env("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


# ---- listings ---------------------------------------------------------------------------------

def list_alpaca():
    """Every Alpaca US equity asset, active and inactive (inactive = delisted, kept)."""
    data = _json(ALPACA_PAPER + "/v2/assets?status=all", "alpaca assets", alpaca_headers())
    return [{"symbol": a["symbol"], "name": a.get("name") or "", "asset_class": "us_equity",
             "exchange": a.get("exchange") or "", "status": a.get("status") or "",
             "tradable": a.get("tradable"), "fractionable": a.get("fractionable")}
            for a in data if a.get("class", "us_equity") == "us_equity"]


def list_sec():
    """SEC's ticker file. Fallback listing when Alpaca keys are missing; current tickers only."""
    data = _json("https://www.sec.gov/files/company_tickers_exchange.json", "sec tickers")
    out = {}
    for row in data["data"]:
        r = dict(zip(data["fields"], row))
        sym = str(r["ticker"]).upper().replace("-", ".")   # Alpaca style: BRK.B
        out.setdefault(sym, {"symbol": sym, "name": r.get("name") or "", "asset_class": "us_equity",
                             "exchange": r.get("exchange") or "", "status": "active"})
    return list(out.values())


def list_coinbase():
    data = _json("https://api.exchange.coinbase.com/products", "coinbase products")
    return [{"symbol": p["id"], "name": p.get("display_name") or p["id"], "asset_class": "crypto",
             "exchange": "coinbase",
             "status": "active" if p.get("status") == "online" and not p.get("trading_disabled") else "inactive"}
            for p in data if p.get("quote_currency") == "USD"]


def kraken_pairs():
    """-> {symbol like BTC-USD: kraken altname} for USD-quoted pairs."""
    data = _json("https://api.kraken.com/0/public/AssetPairs", "kraken pairs")
    out = {}
    for name, p in data.get("result", {}).items():
        ws = p.get("wsname") or ""
        if name.endswith(".d") or not ws.endswith("/USD"):
            continue
        base = ws.split("/")[0]
        out[_KRAKEN_BASE.get(base, base) + "-USD"] = p["altname"]
    return out


def list_kraken():
    return [{"symbol": s, "name": s, "asset_class": "crypto", "exchange": "kraken", "status": "active"}
            for s in kraken_pairs()]


def merge_catalogue(cat, listings, day=None):
    """Upsert listings {source: rows} into cat. Nothing is ever removed: a symbol every one of whose
    sources was listed this run without it becomes status "not_seen" (delisted, kept)."""
    day = day or today().isoformat()
    for source, rows in listings.items():
        for r in rows:
            old = cat.get(r["symbol"])
            srcs = set(old["sources"].split(";")) if old and old["sources"] else set()
            row = dict(old or {"first_seen": day})
            # Alpaca's listing is the authority once it has seen a symbol; other listings only fill gaps
            authority = source == "alpaca" or "alpaca" not in srcs
            for k, v in r.items():
                if v not in (None, "") and (authority or not row.get(k)):
                    row[k] = str(v)
            row["sources"] = ";".join(sorted(srcs | {source}))
            row["last_seen"] = day
            cat[r["symbol"]] = row
    listed = set(listings)
    for row in cat.values():
        if row["last_seen"] != day and set(row["sources"].split(";")) <= listed:
            row["status"] = "not_seen"
    return cat


# ---- bars ------------------------------------------------------------------------------------

def stooq_to_symbol(ticker):
    """AAPL.US -> AAPL, BRK-B.US -> BRK.B"""
    t = ticker.upper()
    return (t[:-3] if t.endswith(".US") else t).replace("-", ".")


def parse_stooq(text):
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 9 or not r[2].isdigit():
            continue
        d = r[2]
        rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "open": r[4], "high": r[5], "low": r[6],
                     "close": r[7], "volume": r[8]})
    return rows


def load_stooq_zip(path):
    """Load Stooq's US daily bulk file (d_us_txt.zip, downloaded by hand from stooq.com/db/h/;
    the site blocks scripted downloads). -> (symbols loaded, rows appended)."""
    syms = rows = 0
    fetched = store.now()
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if "/us/" not in name.replace("\\", "/") or not name.endswith(".txt"):
                continue
            text = z.read(name).decode("utf-8", "replace")
            bars = parse_stooq(text)
            if not bars:
                continue
            ticker = os.path.basename(name)[:-4]
            rows += store.write("bars", "stooq", stooq_to_symbol(ticker), bars, BAR, fetched)
            syms += 1
    store.log_run("stooq", ok=True, symbols=syms, rows=rows, file=os.path.basename(path))
    return syms, rows


def polygon_grouped(day):
    """-> {symbol: bar} for one trading day; {} on a market holiday."""
    (key,) = _env("POLYGON_API_KEY")
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{day}?adjusted=true"
    data = _json(url, f"polygon {day}", {"Authorization": "Bearer " + key})
    return {r["T"]: {"date": day, "open": r.get("o"), "high": r.get("h"), "low": r.get("l"),
                     "close": r.get("c"), "volume": r.get("v")}
            for r in data.get("results") or [] if "T" in r and r.get("c") is not None}


def refresh_polygon(backfill_days=7):
    """Fill every weekday from the last refreshed day up to yesterday. -> days fetched."""
    st = store.state()
    start = (dt.date.fromisoformat(st["polygon_last"]) + dt.timedelta(1) if st.get("polygon_last")
             else today() - dt.timedelta(backfill_days))
    days = rows = 0
    d = start
    while d < today():
        if d.weekday() < 5:
            fetched = store.now()
            for sym, bar in polygon_grouped(d.isoformat()).items():
                rows += store.write("bars", "polygon", sym, [bar], BAR, fetched)
            days += 1
        st["polygon_last"] = d.isoformat()
        store.save_state(st)
        d += dt.timedelta(1)
    store.log_run("polygon", ok=True, days=days, rows=rows, through=st.get("polygon_last"))
    return days


def alpaca_bars(symbols, start, end):
    """IEX-feed daily bars, split-adjusted. -> {symbol: [bars]}"""
    out = {}
    token = None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end,
             "feed": "iex", "adjustment": "split", "limit": 10000}
        if token:
            q["page_token"] = token
        data = _json(ALPACA_DATA + "/v2/stocks/bars?" + urllib.parse.urlencode(q), "alpaca bars", alpaca_headers())
        for sym, bars in (data.get("bars") or {}).items():
            out.setdefault(sym, []).extend({"date": b["t"][:10], "open": b["o"], "high": b["h"], "low": b["l"],
                                            "close": b["c"], "volume": b["v"]} for b in bars)
        token = data.get("next_page_token")
        if not token:
            return {s: final(b) for s, b in out.items()}


def backfill_alpaca(symbols, since="2016-01-01"):
    """History from Alpaca for catalogue symbols no other source covers. -> symbols that got bars."""
    got = 0
    end = (today() - dt.timedelta(1)).isoformat()
    for i in range(0, len(symbols), 100):
        fetched = store.now()
        for sym, bars in alpaca_bars(symbols[i:i + 100], since, end).items():
            store.write("bars", "alpaca", sym, bars, BAR, fetched)
            got += 1
    store.log_run("alpaca_backfill", ok=True, asked=len(symbols), got=got)
    return got


def cross_check(n=50, days=14, tol=0.01, seed=None):
    """Compare Alpaca IEX closes with the stored Stooq/Polygon closes for a random sample of symbols.
    IEX closes are the last IEX print, not the consolidated close, so a 1% tolerance. -> summary"""
    cutoff = (today() - dt.timedelta(10)).isoformat()
    poly = store.keys("bars", "polygon")
    pool = sorted(poly | store.keys("bars", "stooq"))
    rng = random.Random(seed or today().isoformat())
    rng.shuffle(pool)
    refs = {}
    for sym in pool:
        src = "polygon" if sym in poly else "stooq"
        ref = store.read("bars", src, sym)
        if ref and ref[-1]["date"] >= cutoff:
            refs[sym] = (src, {r["date"]: float(r["close"]) for r in ref})
        if len(refs) >= n:
            break
    if not refs:
        raise SourceError("cross-check: no recent Stooq/Polygon bars stored to compare against")
    start = (today() - dt.timedelta(days)).isoformat()
    end = (today() - dt.timedelta(1)).isoformat()
    alp = alpaca_bars(sorted(refs), start, end)
    results = []
    for sym, (src, ref) in sorted(refs.items()):
        diffs = sorted(abs(b["close"] / ref[b["date"]] - 1) for b in alp.get(sym, []) if ref.get(b["date"]))
        med = diffs[len(diffs) // 2] if diffs else None
        results.append({"symbol": sym, "ref": src, "days": len(diffs), "median_diff": med,
                        "match": med is not None and med <= tol})
    compared = [r for r in results if r["days"]]
    summary = {"sampled": len(results), "compared": len(compared),
               "matched": sum(r["match"] for r in compared), "tolerance": tol, "results": results}
    summary["match_rate"] = summary["matched"] / len(compared) if compared else None
    store.log_run("alpaca_check", ok=True, **{k: v for k, v in summary.items() if k != "results"})
    return summary


def coinbase_candles(product, since):
    """Daily candles from `since` (date) to yesterday, 300 per request."""
    rows = []
    end = today()
    while end > since:
        start = max(since, end - dt.timedelta(300))
        q = urllib.parse.urlencode({"granularity": 86400, "start": start.isoformat(), "end": end.isoformat()})
        data = _json(f"https://api.exchange.coinbase.com/products/{product}/candles?{q}", f"coinbase {product}")
        if not data:
            break
        rows += [{"date": dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date().isoformat(),
                  "low": c[1], "high": c[2], "open": c[3], "close": c[4], "volume": c[5]} for c in data]
        end = start
    return final(rows)


def kraken_ohlc(altname):
    """Last 720 daily candles; Kraken's final candle is the one still forming, so it is dropped."""
    data = _json(f"https://api.kraken.com/0/public/OHLC?pair={altname}&interval=1440", f"kraken {altname}")
    if data.get("error"):
        raise SourceError(f"kraken {altname}: {data['error']}")
    candles = next(v for k, v in data["result"].items() if k != "last")
    return final([{"date": dt.datetime.fromtimestamp(c[0], dt.timezone.utc).date().isoformat(),
                   "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[6]} for c in candles[:-1]])


def refresh_crypto(cat, history_days=1100):
    """Bars for every catalogued crypto symbol from each venue that lists it. -> (ok, failed)"""
    pairs = kraken_pairs()
    ok, failed = 0, []
    for sym, row in sorted(cat.items()):
        if row["asset_class"] != "crypto":
            continue
        for src in row["sources"].split(";"):
            if src not in CRYPTO_SOURCES:
                continue
            have = store.read("bars", src, sym)
            since = (dt.date.fromisoformat(have[-1]["date"]) - dt.timedelta(3) if have
                     else today() - dt.timedelta(history_days))
            try:
                bars = coinbase_candles(sym, since) if src == "coinbase" else kraken_ohlc(pairs[sym])
                store.write("bars", src, sym, bars, BAR)
                ok += 1
            except (SourceError, KeyError) as e:
                failed.append(f"{src}:{sym}: {e}")
    store.log_run("crypto", ok=not failed or ok > 0, fetched=ok, failed=len(failed), sample_errors=failed[:5])
    return ok, failed


def refresh_fred(series=FRED_SERIES):
    """FRED's public graph CSV (no key). Stored point-in-time from now on; past revisions are not
    recovered (ALFRED vintages would be the upgrade)."""
    fetched = store.now()
    done, failed = 0, []
    for sid in series:
        try:
            text = _text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", f"fred {sid}")
            rows = [{"date": r[0], "value": r[1]} for r in csv.reader(io.StringIO(text))
                    if len(r) == 2 and r[0][:1].isdigit() and r[1] not in ("", ".")]
            store.write("macro", "fred", sid, rows, ["value"], fetched)
            done += 1
        except SourceError as e:
            failed.append(str(e))
    store.log_run("fred", ok=not failed, series=done, failed=failed)
    return done, failed
