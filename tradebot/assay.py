"""First Assay: rank every catalogued symbol on 12-1 momentum, each score with its uncertainty band.

Score = annualised Sharpe of daily log returns over the past year, skipping the latest month
(the classic 12-1 momentum window, scaled by its own volatility). Band = 95% interval from Lo's
(2002) iid standard error of a Sharpe ratio. A year of data gives a band about +-2 wide, which is
the honest size of what one year of prices can say. No band, no number: a symbol without a full,
fresh window is listed as unrated with the reason, never scored by guess. Nothing is truncated.
"""
import csv
import datetime as dt
import math
import os

from . import sources, store

Z = 1.96
STALE_DAYS = 10
YEAR = {"us_equity": 252, "crypto": 365}
FIELDS = ["rank", "class_rank", "symbol", "asset_class", "status", "score", "band_lo", "band_hi",
          "bars", "last_date", "source", "adv_usd", "reason"]


def score(closes, per_year):
    """-> (score, band_lo, band_hi), or None when the window is short or flat."""
    skip = round(per_year / 12)
    if len(closes) < per_year + skip + 1:
        return None
    window = closes[-(per_year + skip + 1):-skip]
    r = [math.log(b / a) for a, b in zip(window, window[1:]) if a > 0 and b > 0]
    n = len(r)
    if n < 0.9 * per_year:
        return None
    m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    if sd == 0:
        return None
    sr = m / sd
    se = math.sqrt((1 + 0.5 * sr * sr) / n)
    k = math.sqrt(per_year)
    return sr * k, (sr - Z * se) * k, (sr + Z * se) * k


def best_series(sym, asset_class, as_of):
    """The source with the longest fresh series. -> (source, rows) or (None, reason)"""
    cutoff = (dt.date.fromisoformat(as_of[:10]) - dt.timedelta(STALE_DAYS)).isoformat()
    best, stale = None, False
    for src in sources.CRYPTO_SOURCES if asset_class == "crypto" else sources.EQUITY_SOURCES:
        rows = store.read("bars", src, sym, as_of)
        if not rows:
            continue
        if rows[-1]["date"] < cutoff:
            stale = True
        elif best is None or len(rows) > len(best[1]):
            best = (src, rows)
    return best or (None, "stale" if stale else "no history")


def run(cat, as_of=None):
    """Assay every catalogue row. -> (rows, path written)"""
    as_of = as_of or store.now()
    out = []
    for sym, c in sorted(cat.items()):
        row = {"symbol": sym, "asset_class": c["asset_class"], "status": c["status"]}
        src, series = best_series(sym, c["asset_class"], as_of)
        if src is None:
            row["reason"] = series
            out.append(row)
            continue
        closes = [float(r["close"]) for r in series if r["close"]]
        tail = series[-63:]
        dv = sorted(float(r["close"]) * float(r["volume"] or 0) for r in tail if r["close"])
        row.update(source=src, bars=len(series), last_date=series[-1]["date"],
                   adv_usd=round(dv[len(dv) // 2]) if dv else "")
        s = score(closes, YEAR.get(c["asset_class"], 252))
        if s is None:
            row["reason"] = "window too short"
        else:
            row.update(score=round(s[0], 3), band_lo=round(s[1], 3), band_hi=round(s[2], 3),
                       reason="band spans zero" if s[1] < 0 < s[2] else "")
        out.append(row)
    rated = sorted((r for r in out if "score" in r), key=lambda r: -r["score"])
    per_class = {}
    for i, r in enumerate(rated, 1):
        r["rank"] = i
        per_class[r["asset_class"]] = r["class_rank"] = per_class.get(r["asset_class"], 0) + 1
    out.sort(key=lambda r: (r.get("rank") or 10**9, r["symbol"]))
    path = os.path.join(store.DATA, "assay", f"assay_{as_of[:10]}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(out)
    store.log_run("assay", ok=True, symbols=len(out), rated=len(rated), file=path)
    return out, path
