# Trade Bot plan

Full outline: https://claude.ai/artifact/WVqCqHYaVo4csUWYdnuhcM
Evidence behind every tactic: [evidence-review.md](evidence-review.md)

## Ground rules

- Alpaca paper account, $100k, until Marlon explicitly approves real money. `live_trading` stays False.
- Target 10%/yr net. Benchmark: SPY. A strategy trades (even on paper) only after it clears the
  backtest gate on years it never saw.
- Max 20 positions. Fractional shares, so any account size runs the same logic.
- Free data only: Stooq bulk history, Polygon free tier (grouped daily), Alpaca free IEX feed and
  news, Coinbase/Kraken, SEC EDGAR, FRED. No Yahoo.
- No caps on the catalogue (Panscriptum rule): every tradable symbol is catalogued and assayed.
  Ranking is allowed; truncating is not.
- No band, no number: a symbol without enough evidence is "unrated", never guessed.
- LLM work (news scoring, research memos) goes through Marlon's ModelRouter and Claude API credits,
  as his CLAUDE.md describes.
- Never fake results. If data is unavailable, say so.

## Target blend (from the evidence review)

| Sleeve | Capital | Positions |
| --- | --- | --- |
| Stock composite: residual/vol-scaled momentum + quality + value | 50% | 12 |
| Cross-asset trend ETFs (Faber 200-day style, tranched) | 30% | 5 |
| Cash-secured puts | 12% | 1-2 |
| BTC/ETH trend | 5-8% | 1-2 |

Filters (can force exits, never buys): opportunistic insider buying boost, LLM news sentiment,
short-interest exclusion, IPO-lockup avoidance. Overlays: 10-12% vol target capped at 1.0x,
200-day trend cut on the stock sleeve.

Rotation rule: enter in the top 12, exit only below the top 30 (buy/hold band); a replacement must
beat the weakest holding's forecast by more than round-trip cost plus a margin; minimum hold period.

## Phases and gates

1. **Data, catalogue and Assay.** Universe (Alpaca assets, SEC tickers fallback, delisted kept),
   Stooq bulk history loader, Polygon grouped-daily refresher, Alpaca bars cross-check, crypto
   bars, FRED macro, point-in-time cache. First Assay ranking of every symbol with a band.
   *Gate:* ≥95% of the catalogue has history, daily refresh runs unattended, spot checks match
   Alpaca prices.
2. **Strategy library and backtest lab.** Walk-forward by year (tune on past years, test on the
   next, NFL-style), purged gap, next-open fills with cost model, deflated Sharpe ≥ 0.95, PBO,
   Monte Carlo, crisis replay, known-truth synthetic market test of the harness.
   *Gate:* harness passes the known-truth test; the blend clears 10%/yr net on held-out years.
3. **Research, news, portfolio engine on paper.** EDGAR Form 4 / 8-K monitor, LLM news scoring via
   ModelRouter, regime engine, 20-position rotation engine, kill switches, daily report.
   *Gate:* 3 months of paper results inside the backtest's predicted range.
4. **Real money** only with Marlon's explicit go-ahead.
