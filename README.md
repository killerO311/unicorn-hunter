# THE HUNTER v1.2 — SUPER HUNTER

Qullamaggie-style breakout scanner that now **hunts and trades**, not just observes.

## What it does

1. Scans ~2,300 NYSE/Nasdaq names every morning via free yfinance data
2. Scores six breakout factors: RS vs SPY, volume spike, consolidation tightness, breakout level, weekly confluence, ATR
3. Float filter: Financial Modeling Prep bulk call if `FMP_API_KEY` is set, otherwise auto-falls back to scraping Finviz (zero-key, polite delays)
4. Filters: float ≤ 50M shares, price > $2, RS > 0, 20-day tightness < 35%, within 8% of 60-day high
5. Ranks top 15 and prints stops (3xATR) and 1%-risk position sizes
6. **SUPER HUNTER MODE (v1.2):** for candidates scoring above 0.70 with a catalyst volume spike, places a marketable-limit buy on Robinhood sized at 1% account risk per trade, hard-capped at 5 concurrent positions and a 2% daily loss kill switch. Every entry gets an OCO — take-profit limit at 2x risk, stop-market at the 3xATR stop. Alerts push to Telegram and/or Discord on entry, TP, SL, and kill switch.

## Setup

```bash
pip install -r requirements.txt
export FMP_API_KEY=your_key            # optional — free at financialmodelingprep.com
export RH_USERNAME=your_rh_email
export RH_PASSWORD=your_rh_password
export RH_ACCOUNT_NUMBER=704776962     # Zed account
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export DISCORD_WEBHOOK_URL=...
python unicorn_hunter.py
```

## Schedule

- Cron: `0 8 * * 1-5` (8 AM ET weekdays)
- Or the `the-hunter-morning-scan` automation (already live, fires 8 AM ET)

## Config knobs (top of unicorn_hunter.py)

| Knob | Default | Meaning |
|---|---|---|
| SCORE_GATE | 0.70 | Min score to fire a trade |
| MAX_POSITIONS | 5 | Concurrent long cap |
| DAILY_LOSS_CAP | 2% | Kill switch |
| RISK_PER_TRADE | 1% | Account risk per position |
| TP_MULT | 2.0 | Take-profit distance in units of 3xATR risk |
| ATR_MULT | 3.0 | Stop distance |

## Risk notes

- robin_stocks uses Robinhood's unofficial API — against ToS, account risk exists
- The bot never shorts, never uses leverage beyond your margin, and respects buying power
- Backtest before trusting it with size
- Not financial advice
