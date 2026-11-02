# Phase 1 gate

Generated 2026-11-02T18:13:03Z from `E:/TradeBot/data`.

| Check | Result | Pass |
| --- | --- | --- |
| Catalogue has history (>= 95%) | 801 of 859 = 93.2% | no |
| Polygon refresh current | MissingKey: POLYGON_API_KEY | no |
| Daily refresh scheduled (TradeBot-Daily) | not registered | no |
| Alpaca spot check (>= 95% within 1%) | SourceError: cross-check: no recent Stooq/Polygon bars stored to compare against | no |

## Coverage by class and status

| Group | With history | Total | % |
| --- | --- | --- | --- |
| crypto | 801 | 859 | 93.2% |
| crypto / active | 794 | 794 | 100.0% |
| crypto / inactive | 7 | 65 | 10.8% |

## Last run of each step

- **catalogue**: {"at": "2026-11-01T17:25:38Z", "ok": true, "total": 859, "listed": {"coinbase": 495, "kraken": 680}, "notes": ["alpaca: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY; SEC fallback", "sec tickers: http-403", "no equity listing this run; equities keep their last status"]}
- **stooq**: never ran
- **polygon**: {"at": "2026-11-01T17:24:46Z", "ok": false, "error": "MissingKey: POLYGON_API_KEY"}
- **alpaca_backfill**: never ran
- **alpaca_check**: {"at": "2026-11-01T17:24:51Z", "ok": false, "error": "SourceError: cross-check: no recent Stooq/Polygon bars stored to compare against"}
- **crypto**: {"at": "2026-11-01T17:38:55Z", "ok": true, "fetched": 1175, "failed": 0, "sample_errors": []}
- **fred**: {"at": "2026-11-01T17:24:51Z", "ok": true, "series": 15, "failed": []}
- **assay**: {"at": "2026-11-01T17:39:06Z", "ok": true, "symbols": 859, "rated": 547, "file": "E:/TradeBot/data\\assay\\assay_2026-11-01.csv"}

## Recent failures

- 2026-11-01T17:24:46Z catalogue: SourceError: sec tickers: http-403
- 2026-11-01T17:24:46Z polygon: MissingKey: POLYGON_API_KEY
- 2026-11-01T17:24:51Z alpaca_check: SourceError: cross-check: no recent Stooq/Polygon bars stored to compare against

**Gate: NOT PASSED**
