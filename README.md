# THE HUNTER
**Unicorn breakout scanner — Qullamaggie-style momentum + FMP bulk float filter**

Hunts low-float, high-relative-strength breakouts across NYSE and Nasdaq.
Free data. No paid APIs. One Python file.

## Pipeline
1. **Universe** — ~2,300 tickers (S&P 500 + momentum supplements)
2. **Factors** — 60-day RS vs SPY, volume spike vs 20-day avg, 20-day consolidation tightness, distance from 60-day high, weekly confluence, 14-day ATR
3. **Float** — bulk fetch from Financial Modeling Prep (free plan, single call, up to 5,000 records)
4. **Filter** — float ≤ 50M shares, RS > 0, tightness < 35%, within 8% of 60-day high
5. **Rank** — weighted score, top 15 printed + saved to CSV

## Quick start
```bash
git clone https://github.com/killerO311/unicorn-hunter.git
cd unicorn-hunter
pip install yfinance pandas numpy requests
export FMP_API_KEY=your_key   # free at financialmodelingprep.com
python unicorn_hunter.py
```

## Schedule it
```cron
0 9 * * 1-5  cd /path/to/unicorn-hunter && python unicorn_hunter.py
```
Runs 9 AM ET on weekdays — right when the open's dust settles.

## Output
Ranked table: ticker, price, RS%, volume multiple, tightness%, breakout%, float, score.
Stop = price − 3×ATR. Size = 1% account risk ÷ risk-per-share.

## Notes
- FMP key optional: scanner runs without it, float column just shows n/a.
- Not financial advice. Backtest before sizing real money.
- Named by Travis. Built by Grok. 🦄