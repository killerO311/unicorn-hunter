# THE HUNTER
**Unicorn breakout scanner — Qullamaggie-style momentum + FMP bulk float + Finviz zero-key fallback**

Hunts low-float, high-relative-strength breakouts across NYSE and Nasdaq.
Free data. No paid APIs required. One Python file.

## Pipeline
1. **Universe** — ~2,300 tickers (S&P 500 + momentum supplements)
2. **Factors** — 60-day RS vs SPY, volume spike vs 20-day avg, 20-day consolidation tightness, distance from 60-day high, weekly confluence, 14-day ATR
3. **Float** — bulk fetch from Financial Modeling Prep (free plan, single call, up to 5,000 records). If no FMP key is set, automatically falls back to scraping float from Finviz quote pages (zero-key, ~3 sec/ticker with polite delays).
4. **Filter** — float ≤ 50M shares, RS > 0, tightness < 35%, within 8% of 60-day high
5. **Rank** — weighted score, top 15 printed + saved to CSV

## Quick start
```bash
git clone https://github.com/killerO311/unicorn-hunter.git
cd unicorn-hunter
pip install yfinance pandas numpy requests lxml html5lib
export FMP_API_KEY=your_key   # optional — free at financialmodelingprep.com
python unicorn_hunter.py
```

## Schedule it
```cron
0 8 * * 1-5  cd /path/to/unicorn-hunter && python unicorn_hunter.py
```
Runs 8 AM ET on weekdays — ninety minutes before the bell, when premarket volume and spreads reveal the real unicorns.

## Output
Ranked table: ticker, price, RS%, volume multiple, tightness%, breakout%, float, score.
Stop = price − 3×ATR. Size = 1% account risk ÷ risk-per-share.
Float column shows source: FMP bulk or Finviz scrape.

## Notes
- FMP key optional: scanner runs without it via the Finviz fallback.
- Finviz fallback is slower (~1-2 hours for the full universe) but needs no key.
- Not financial advice. Backtest before sizing real money.
- Named by Travis. Built by Grok. 🦄
