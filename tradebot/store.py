"""Point-in-time on-disk store, catalogue file and run log.

Every stored row carries the UTC time it was fetched. A later fetch that changes a date's values
appends a new version instead of overwriting, so read(..., as_of=T) replays exactly what was known
at T. That is what keeps a backtest from seeing a revised price before the revision happened.
"""
import csv
import datetime as dt
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Big data lives on E: on Marlon's PCs; TRADEBOT_DATA overrides.
DATA = os.environ.get("TRADEBOT_DATA") or ("E:/TradeBot/data" if os.path.isdir("E:/") else os.path.join(_REPO, "data"))

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}

CATALOGUE_FIELDS = ["symbol", "name", "asset_class", "exchange", "status", "tradable", "fractionable",
                    "sources", "first_seen", "last_seen"]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(x):
    return "" if x is None or x == "" else "%.10g" % float(x)


def path_for(kind, source, key):
    name = key.replace("/", "-")
    if name.split(".")[0].upper() in _RESERVED:   # PRN is a real ETF; Windows refuses the file name
        name = "_" + name
    return os.path.join(DATA, kind, source, name + ".csv")


def keys(kind, source):
    """-> set of keys stored for a source (file names, reserved-name prefix undone)."""
    d = os.path.join(DATA, kind, source)
    if not os.path.isdir(d):
        return set()
    return {f[:-4].lstrip("_") for f in os.listdir(d) if f.endswith(".csv")}


def read(kind, source, key, as_of=None):
    """-> row dicts sorted by date: the latest version of each date fetched at or before as_of."""
    p = path_for(kind, source, key)
    if not os.path.exists(p):
        return []
    latest = {}
    with open(p, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if as_of is None or (r["fetched_at"] <= as_of and r["date"] <= as_of[:10]):
                latest[r["date"]] = r
    return [latest[d] for d in sorted(latest)]


def write(kind, source, key, rows, fields, fetched_at=None):
    """Append rows whose values differ from the latest stored version. -> number appended."""
    fetched_at = fetched_at or now()
    known = {r["date"]: tuple(r[k] for k in fields) for r in read(kind, source, key)}
    new = [r for r in rows if known.get(r["date"]) != tuple(_fmt(r[k]) for k in fields)]
    if not new:
        return 0
    p = path_for(kind, source, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fresh = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(["date", "fetched_at", *fields])
        for r in sorted(new, key=lambda r: r["date"]):
            w.writerow([r["date"], fetched_at, *(_fmt(r[k]) for k in fields)])
    return len(new)


def _replace_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def load_catalogue():
    p = os.path.join(DATA, "catalogue.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="", encoding="utf-8") as f:
        return {r["symbol"]: r for r in csv.DictReader(f)}


def save_catalogue(cat):
    _replace_csv(os.path.join(DATA, "catalogue.csv"), CATALOGUE_FIELDS, sorted(cat.values(), key=lambda r: r["symbol"]))


def state():
    p = os.path.join(DATA, "state.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_state(s):
    os.makedirs(DATA, exist_ok=True)
    tmp = os.path.join(DATA, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, os.path.join(DATA, "state.json"))


def log_run(step, **info):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "runs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), "step": step, **info}) + "\n")


def runs():
    p = os.path.join(DATA, "runs.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
