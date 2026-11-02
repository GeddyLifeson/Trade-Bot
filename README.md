# Trade Bot

Autonomous investing system on Alpaca (paper first). See [docs/PLAN.md](docs/PLAN.md).

## Phase 1: data, catalogue and Assay

Stdlib only (Python 3.12+). Data lives in `E:\TradeBot\data` (or `TRADEBOT_DATA`); nothing there is committed.

```bash
python -m tradebot.cli daily              # catalogue, polygon, crypto, fred, check, assay, gate
python -m tradebot.cli stooq d_us_txt.zip # load Stooq's US bulk history
python -m tradebot.cli backfill           # Alpaca history for equities nothing else covers
python -m unittest discover -s tests
```

`reports/phase1_gate.md` is the gate: >=95% of the catalogue has history, the refresh runs
unattended (`daily.bat` as scheduled task `TradeBot-Daily`), Alpaca spot checks match.

### Keys (user environment variables, never in files or chat)

| Variable | Where to get it | Used for |
| --- | --- | --- |
| `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | alpaca.markets, paper account, API Keys | asset listing (incl. delisted), IEX bars, spot check |
| `POLYGON_API_KEY` | polygon.io free plan dashboard | grouped-daily refresh |
| `POLITE_FETCH_UA` | your name and email, e.g. `TradeBot Your Name you@example.com` | SEC refuses requests without a contact email |

Set each in Windows: Settings, System, About, Advanced system settings, Environment Variables,
User variables, New. Open a new terminal afterwards.

Stooq blocks scripted downloads, so its bulk file is fetched by hand: stooq.com/db/h/, "U.S." daily
ASCII (`d_us_txt.zip`), then `python -m tradebot.cli stooq <path>`.

### Point-in-time store

Every bar row records when it was fetched. A changed value is appended as a new version, and
`store.read(..., as_of=T)` returns what was known at T. Bars dated today are never stored (still forming).

### Assay

Every catalogued symbol is listed (no caps). Score = 12-1 momentum as an annualised Sharpe;
band = 95% interval from Lo's Sharpe standard error. Symbols without a full, fresh window are
"unrated" with the reason, never guessed.

Paper only: no code in this repo places orders.
