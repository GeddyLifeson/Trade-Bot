"""Phase 1 commands.  python -m tradebot.cli <command>

  catalogue        list every symbol (Alpaca, else SEC; Coinbase, Kraken); delisted kept
  stooq ZIP        load Stooq's US daily bulk file (d_us_txt.zip)
  polygon          grouped-daily refresh from the last refreshed day to yesterday
  backfill         Alpaca history for catalogued equities no other source covers
  check            Alpaca price spot check against stored closes
  crypto | fred    crypto bars | FRED macro series
  assay            rank every catalogued symbol with a band
  gate             write reports/phase1_gate.md
  daily            catalogue, polygon, crypto, fred, check, assay, gate; each step independent

Paper only: nothing here places orders.
"""
import collections
import datetime as dt
import json
import os
import subprocess
import sys
import traceback

from . import assay, sources, store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK = "TradeBot-Daily"


def cmd_catalogue():
    cat = store.load_catalogue()
    listings, notes = {}, []
    try:
        listings["alpaca"] = sources.list_alpaca()
    except (sources.MissingKey, sources.SourceError) as e:
        notes.append(f"alpaca: {e}; SEC fallback")
        try:
            listings["sec"] = sources.list_sec()
        except sources.SourceError as e2:
            notes.append(str(e2))
    for name, fn in (("coinbase", sources.list_coinbase), ("kraken", sources.list_kraken)):
        try:
            listings[name] = fn()
        except sources.SourceError as e:
            notes.append(str(e))
    if not any(r["asset_class"] == "us_equity" for rows in listings.values() for r in rows):
        notes.append("no equity listing this run; equities keep their last status")
    sources.merge_catalogue(cat, listings)
    store.save_catalogue(cat)
    counts = collections.Counter((r["asset_class"], r["status"]) for r in cat.values())
    store.log_run("catalogue", ok=True, total=len(cat), listed={k: len(v) for k, v in listings.items()}, notes=notes)
    print(f"catalogue: {len(cat)} symbols {dict(counts)}", *notes, sep="\n  ")


def cmd_backfill():
    have = set().union(*(store.keys("bars", s) for s in sources.EQUITY_SOURCES))
    missing = sorted(s for s, r in store.load_catalogue().items() if r["asset_class"] == "us_equity" and s not in have)
    print(f"backfill: {sources.backfill_alpaca(missing)} of {len(missing)} uncovered symbols got Alpaca bars")


def cmd_check():
    s = sources.cross_check()
    path = os.path.join(store.DATA, "checks", f"alpaca_{sources.today()}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    print(f"check: {s['matched']}/{s['compared']} sampled symbols match Alpaca within {s['tolerance']:.0%}")


def cmd_crypto():
    ok, failed = sources.refresh_crypto(store.load_catalogue())
    print(f"crypto: {ok} series refreshed, {len(failed)} failed", *failed[:5], sep="\n  ")


def cmd_fred():
    done, failed = sources.refresh_fred()
    print(f"fred: {done} series", *failed, sep="\n  ")


def cmd_assay():
    rows, path = assay.run(store.load_catalogue())
    rated = [r for r in rows if "score" in r]
    print(f"assay: {len(rows)} symbols, {len(rated)} rated -> {path}")
    for r in rated[:10]:
        print(f"  {r['rank']:>4} {r['symbol']:<10} {r['score']:>7} [{r['band_lo']}, {r['band_hi']}]")


def _last(step):
    return next((r for r in reversed(store.runs()) if r["step"] == step), None)


def gate():
    """-> (markdown, passed). Gate: >=95% of the catalogue has history, daily refresh runs
    unattended, spot checks match Alpaca prices."""
    cat = store.load_catalogue()
    have = {c: set().union(*(store.keys("bars", s) for s in srcs))
            for c, srcs in (("us_equity", sources.EQUITY_SOURCES), ("crypto", sources.CRYPTO_SOURCES))}
    by = collections.defaultdict(lambda: [0, 0])
    for sym, r in cat.items():
        for key in (r["asset_class"], f"{r['asset_class']} / {r['status'] or '?'}"):
            by[key][0] += sym in have.get(r["asset_class"], set())
            by[key][1] += 1
    covered = sum(sym in have.get(r["asset_class"], set()) for sym, r in cat.items())
    cov = covered / len(cat) if cat else 0.0

    poly = _last("polygon")
    task = subprocess.run(["schtasks", "/query", "/tn", TASK], capture_output=True).returncode == 0 if os.name == "nt" else False
    fresh_ok = bool(poly and poly.get("ok") and (poly.get("through") or "") >= str(sources.today() - dt.timedelta(4)))
    chk = _last("alpaca_check")
    chk_ok = bool(chk and chk.get("compared") and chk["match_rate"] >= 0.95)
    chk_text = f"{chk['matched']}/{chk['compared']} matched" if chk and chk.get("ok") else (chk or {}).get("error", "never ran")
    errors = [r for r in store.runs()[-40:] if not r.get("ok")]

    lines = ["# Phase 1 gate", "", f"Generated {store.now()} from `{store.DATA}`.", "",
             "| Check | Result | Pass |", "| --- | --- | --- |",
             f"| Catalogue has history (>= 95%) | {covered:,} of {len(cat):,} = {cov:.1%} | {'yes' if cov >= 0.95 else 'no'} |",
             f"| Polygon refresh current | {('through ' + poly['through']) if poly and poly.get('through') else (poly or {}).get('error', 'never ran')} | {'yes' if fresh_ok else 'no'} |",
             f"| Daily refresh scheduled ({TASK}) | {'registered' if task else 'not registered'} | {'yes' if task else 'no'} |",
             f"| Alpaca spot check (>= 95% within 1%) | {chk_text} | {'yes' if chk_ok else 'no'} |", "", "## Coverage by class and status", "",
             "| Group | With history | Total | % |", "| --- | --- | --- | --- |"]
    lines += [f"| {k} | {a:,} | {t:,} | {a / t:.1%} |" for k, (a, t) in sorted(by.items())]
    last = {s: _last(s) for s in ("catalogue", "stooq", "polygon", "alpaca_backfill", "alpaca_check", "crypto", "fred", "assay")}
    lines += ["", "## Last run of each step", ""]
    lines += [f"- **{s}**: " + (json.dumps({k: v for k, v in r.items() if k != "step"}) if r else "never ran") for s, r in last.items()]
    if errors:
        lines += ["", "## Recent failures", ""] + [f"- {r['at']} {r['step']}: {r.get('error', '')}" for r in errors]
    passed = cov >= 0.95 and fresh_ok and task and chk_ok
    lines += ["", f"**Gate: {'PASSED' if passed else 'NOT PASSED'}**", ""]
    return "\n".join(lines), passed


def cmd_gate():
    md, passed = gate()
    os.makedirs(os.path.join(REPO, "reports"), exist_ok=True)
    with open(os.path.join(REPO, "reports", "phase1_gate.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    return 0 if passed else 1


def _step(name, fn, *args):
    try:
        fn(*args)
    except Exception as e:   # one source down must not stop the others; the failure is logged and gated
        store.log_run(name, ok=False, error=f"{type(e).__name__}: {e}")
        print(f"{name}: FAILED {type(e).__name__}: {e}")
        if not isinstance(e, (sources.MissingKey, sources.SourceError)):
            traceback.print_exc()


def cmd_daily():
    for name, fn in (("catalogue", cmd_catalogue), ("polygon", lambda: sources.refresh_polygon()),
                     ("crypto", cmd_crypto), ("fred", cmd_fred), ("alpaca_check", cmd_check), ("assay", cmd_assay)):
        _step(name, fn)
    return cmd_gate()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmds = {"catalogue": cmd_catalogue, "polygon": lambda: print(f"polygon: {sources.refresh_polygon()} days"),
            "backfill": cmd_backfill, "check": cmd_check, "crypto": cmd_crypto, "fred": cmd_fred,
            "assay": cmd_assay, "gate": cmd_gate, "daily": cmd_daily}
    if argv[:1] == ["stooq"] and len(argv) == 2:
        syms, rows = sources.load_stooq_zip(argv[1])
        print(f"stooq: {syms} symbols, {rows} rows")
        return 0
    if len(argv) != 1 or argv[0] not in cmds:
        print(__doc__)
        return 2
    name = argv[0]
    if name in ("gate", "daily"):
        return cmds[name]()
    _step(name, cmds[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
