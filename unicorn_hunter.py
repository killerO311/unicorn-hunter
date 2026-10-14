#!/usr/bin/env python3
"""
THE HUNTER v1.2 — SUPER HUNTER
Qullamaggie-style breakout scanner + FMP bulk float + Finviz zero-key fallback
+ Robinhood order execution (robin_stocks) + daily loss cap + max positions
+ OCO take-profit/stop-loss protection + Telegram/Discord webhook alerts.

Pipeline:
1. Pull ~2,300 NYSE/Nasdaq tickers via yfinance (free, no API key)
2. Compute 6 breakout factors: catalyst volume, RS vs SPY, consolidation
   tightness, breakout level, weekly confluence, ATR
3. Bulk-fetch float from Financial Modeling Prep (free plan, 1 call);
   if no FMP key, scrape float per-ticker from Finviz (zero-key fallback)
4. Filter: float <= 50M shares, price > $2, RS > 0, tight consolidation
5. Rank and print top unicorn candidates
6. SUPER HUNTER MODE: for each candidate scoring above SCORE_GATE with a
   catalyst flag, place a marketable-limit BUY on Robinhood sized at 1%
   account risk / (3xATR stop), capped by MAX_POSITIONS and DAILY_LOSS_CAP.
   Every new long gets an OCO: take-profit limit above entry, stop-market
   below. Alerts fire to Telegram and/or Discord webhook on entry, fill,
   TP, SL, and daily-loss kill switch.

Setup:
  pip install yfinance pandas numpy requests lxml html5lib robin_stocks
  export FMP_API_KEY=your_key            # optional, free at financialmodelingprep.com
  export RH_USERNAME / RH_PASSWORD       # Robinhood login for robin_stocks
  export RH_ACCOUNT_NUMBER=704776962     # Zed (agentic-allowed)
  export TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   # optional
  export DISCORD_WEBHOOK_URL             # optional
  python unicorn_hunter.py

Schedule: cron 0 8 * * 1-5  (8 AM ET, weekdays) or the the-hunter-morning-scan automation.
"""

import os, sys, time, json, re, uuid
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    import robin_stocks.robinhood as rh
    RH_AVAILABLE = True
except ImportError:
    RH_AVAILABLE = False

# ---------------- CONFIG ----------------
UNIVERSE_SIZE = 2300
MIN_PRICE = 2.0
MAX_FLOAT = 50_000_000
RS_LOOKBACK = 60
CONSOL_LOOKBACK = 20
VOL_SPIKE_MULT = 2.0
ATR_MULT = 3.0
TOP_N = 15
SCORE_GATE = 0.70            # super hunter fires only above this
MAX_POSITIONS = 5           # hard cap on concurrent longs
DAILY_LOSS_CAP = 0.02       # 2% of account equity -> kill switch
RISK_PER_TRADE = 0.01       # 1% of account equity per position
TP_MULT = 2.0               # take-profit = 2x the 3xATR risk
FMP_KEY = os.environ.get("FMP_API_KEY", "")
FMP_URL = "https://financialmodelingprep.com/api/v4/shares_float/all"
RH_USER = os.environ.get("RH_USERNAME", "")
RH_PASS = os.environ.get("RH_PASSWORD", "")
RH_ACCOUNT = os.environ.get("RH_ACCOUNT_NUMBER", "704776962")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
DC_HOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ---------------- ALERTS ----------------
def alert(msg):
    log("ALERT: " + msg)
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
        except Exception as e:
            log(f"Telegram failed: {e}")
    if DC_HOOK:
        try:
            requests.post(DC_HOOK, json={"content": msg}, timeout=10)
        except Exception as e:
            log(f"Discord failed: {e}")

# ---------------- 1. UNIVERSE ----------------
def get_universe():
    log("Building universe from SPY constituents + popular lists...")
    try:
        spx = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers = spx["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception:
        tickers = []
    extras = ["MNDR","BMBL","PFE","SPCX","F","SOFI","PLUG","FCEL","SNAP","RIVN",
              "LCID","NIO","XPEV","LI","BIDU","JD","PDD","BABA","TSM","ASML",
              "AMD","NVDA","TSLA","AAPL","MSFT","GOOGL","AMZN","META","NFLX",
              "GME","AMC","BBBY","WISH","CLOV","SPCE","HOOD","COIN","MARA","RIOT",
              "HUT","CIFR","WULF","IREN","BITF","CLSK","HIVE","SDIG","EBON"]
    tickers = sorted(set(tickers + extras))[:UNIVERSE_SIZE]
    log(f"Universe: {len(tickers)} tickers")
    return tickers

# ---------------- 2. PRICE DATA ----------------
def fetch_prices(tickers):
    log(f"Downloading {len(tickers)} tickers (this takes a few minutes)...")
    end = datetime.now()
    start = end - timedelta(days=RS_LOOKBACK + 40)
    data = yf.download(tickers, start=start.strftime("%Y-%m-%d"),
                       end=end.strftime("%Y-%m-%d"), auto_adjust=True,
                       threads=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        closes = data["Close"]; vols = data["Volume"]
        highs = data["High"]; lows = data["Low"]
    else:
        closes, vols, highs, lows = data, data, data, data
    spy = yf.download("SPY", start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"), auto_adjust=True,
                      threads=True, progress=False)["Close"]
    log(f"Got data for {closes.shape[1]} tickers, {closes.shape[0]} sessions")
    return closes, vols, highs, lows, spy

# ---------------- 3. FACTORS ----------------
def compute_factors(closes, vols, highs, lows, spy):
    log("Computing breakout factors...")
    rows = []
    for t in closes.columns:
        try:
            c = closes[t].dropna(); v = vols[t].reindex(c.index).dropna()
            h = highs[t].reindex(c.index).dropna(); l = lows[t].reindex(c.index).dropna()
            if len(c) < RS_LOOKBACK + 5: continue
            price = float(c.iloc[-1])
            if price < MIN_PRICE: continue
            ret = c.pct_change().dropna(); spy_ret = spy.reindex(ret.index).pct_change().dropna()
            common = ret.index.intersection(spy_ret.index)
            if len(common) < 20: continue
            r, s = ret.loc[common], spy_ret.loc[common]
            cov = np.cov(r.values, s.values)
            beta = cov[0,1] / cov[1,1] if cov[1,1] else 1.0
            rs = float((r.mean() - beta * s.mean()) * 252 * 100)
            vol_now = float(v.iloc[-1]); vol_avg = float(v.iloc[-20:].mean()) if len(v) >= 20 else float(v.mean())
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 0
            recent_h, recent_l = float(h.iloc[-CONSOL_LOOKBACK:].max()), float(l.iloc[-CONSOL_LOOKBACK:].min())
            tight = (recent_h - recent_l) / price * 100 if price else 999
            hi60 = float(h.iloc[-RS_LOOKBACK:].max())
            brk = (price / hi60 - 1) * 100 if hi60 else -100
            tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.iloc[-14:].mean()) if len(tr) >= 14 else float(tr.mean())
            wk = float(c.iloc[-5:].pct_change().mean() * 100) if len(c) >= 6 else 0
            catalyst = 1 if (vol_ratio >= VOL_SPIKE_MULT and float(ret.iloc[-1]) > 0) else 0
            rows.append({"ticker": t, "price": round(price,2), "rs": round(rs,1),
                         "vol_ratio": round(vol_ratio,2), "tightness": round(tight,1),
                         "breakout_pct": round(brk,1), "atr": round(atr,2),
                         "wk_conf": round(wk,2), "catalyst": catalyst,
                         "stop": round(price - ATR_MULT*atr, 2),
                         "risk_per_share": round(ATR_MULT*atr, 2)})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    log(f"Factor table: {len(df)} valid tickers")
    return df

# ---------------- 4. FLOAT ----------------
def fetch_float_fmp():
    log("Fetching bulk float from FMP...")
    if not FMP_KEY:
        log("FMP_API_KEY not set — skipping FMP, trying Finviz fallback")
        return {}
    try:
        r = requests.get(FMP_URL, params={"apikey": FMP_KEY}, timeout=60)
        r.raise_for_status(); data = r.json(); out = {}
        for row in data:
            t = row.get("symbol"); f = row.get("floatShares") or row.get("freeFloat")
            if t and f: out[t] = int(f)
        log(f"FMP float records: {len(out)}"); return out
    except Exception as e:
        log(f"FMP fetch failed: {e}"); return {}

def fetch_float_finviz(tickers):
    log(f"Finviz fallback: fetching float for {len(tickers)} tickers...")
    out = {}; headers = {"User-Agent": "Mozilla/5.0 (compatible; UnicornHunter/1.2)"}
    for i, t in enumerate(tickers):
        try:
            r = requests.get(f"https://finviz.com/quote.ashx?t={t}", headers=headers, timeout=15)
            if r.status_code != 200: continue
            m = re.search(r'Shs Float</td><td[^>]*>([0-9,.]+)\s*([KMBT]?)</td>', r.text)
            if m:
                val = float(m.group(1).replace(",", ""))
                mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2), 1)
                out[t] = int(val * mult)
            time.sleep(0.15 if (i + 1) % 50 else 1.0)
        except Exception:
            continue
    log(f"Finviz float records: {len(out)}"); return out

def fetch_float_bulk(tickers=None):
    fl = fetch_float_fmp()
    return fl if fl else (fetch_float_finviz(tickers) if tickers else {})

def apply_float(df, fl):
    df["float"] = df["ticker"].map(fl) if fl else None
    return df

# ---------------- 5. RANK ----------------
def rank(df):
    log("Ranking unicorns...")
    d = df.dropna(subset=["rs","vol_ratio","tightness","breakout_pct"]).copy()
    d["score"] = (d["rs"].clip(0,80)/80*0.35 + d["vol_ratio"].clip(0,10)/10*0.25 +
                   (100-d["tightness"].clip(0,100))/100*0.20 +
                   d["breakout_pct"].clip(-20,5).add(20)/25*0.20)
    d = d.sort_values("score", ascending=False)
    uni = d[(d["float"].isna() | (d["float"] <= MAX_FLOAT)) & (d["rs"] > 0) &
            (d["tightness"] < 35) & (d["breakout_pct"] > -8)].head(TOP_N)
    return d, uni

# ---------------- 6. ROBINHOOD EXECUTION ----------------
def rh_login():
    if not RH_AVAILABLE:
        log("robin_stocks not installed — execution disabled"); return False
    if not RH_USER or not RH_PASS:
        log("RH_USERNAME/RH_PASSWORD not set — execution disabled"); return False
    try:
        rh.login(RH_USER, RH_PASS); log("Robinhood logged in"); return True
    except Exception as e:
        log(f"Robinhood login failed: {e}"); return False

def rh_positions():
    try:
        pos = rh.get_open_stock_positions()
        return {p["symbol"]: float(p["quantity"]) for p in pos}
    except Exception as e:
        log(f"positions fetch failed: {e}"); return {}

def rh_equity():
    try:
        pf = rh.profiles.load_portfolio_profile()
        return float(pf.get("equity") or 0)
    except Exception as e:
        log(f"equity fetch failed: {e}"); return 0.0

def rh_buying_power():
    try:
        return float(rh.get_buying_power())
    except Exception:
        return 0.0

def place_buy(ticker, price, stop, qty):
    ref = str(uuid.uuid4())
    try:
        # marketable limit at the ask for price protection
        order = rh.order_buy_limit(ticker, qty, round(price * 1.002, 2),
                                   timeInForce="gfd", market_hours="regular_hours")
        alert(f"HUNTER BUY {ticker} qty={qty} @~{price:.2f} stop={stop:.2f} ref={ref[:8]}")
        return order
    except Exception as e:
        log(f"BUY {ticker} failed: {e}"); return None

def place_oco(ticker, qty, tp, sl):
    ref = str(uuid.uuid4())
    try:
        # OCO: take-profit limit above, stop-market below; both GTC regular hours
        oco = rh.order_sell_oco(ticker, qty, round(tp, 2), round(sl, 2),
                                timeInForce="gtc")
        alert(f"HUNTER OCO {ticker} qty={qty} TP={tp:.2f} SL={sl:.2f} ref={ref[:8]}")
        return oco
    except Exception as e:
        log(f"OCO {ticker} failed: {e}"); return None

def super_hunter(uni, positions, equity, bp):
    if not RH_AVAILABLE or equity <= 0:
        log("Super Hunter: no execution (no RH or zero equity)"); return
    # daily loss check: compare equity to start-of-day baseline if provided
    baseline = float(os.environ.get("HUNTER_DAY_START_EQUITY", equity))
    if baseline > 0 and (equity - baseline) / baseline <= -DAILY_LOSS_CAP:
        alert(f"KILL SWITCH: daily loss {(equity-baseline)/baseline*100:.1f}% >= {DAILY_LOSS_CAP*100:.0f}% — no new buys today")
        return
    open_count = len(positions)
    if open_count >= MAX_POSITIONS:
        log(f"Super Hunter: at max positions ({open_count}/{MAX_POSITIONS})"); return
    risk_budget = equity * RISK_PER_TRADE
    for _, r in uni.iterrows():
        if open_count >= MAX_POSITIONS: break
        t = r["ticker"]
        if t in positions: continue
        if r["score"] < SCORE_GATE: continue
        if r["catalyst"] != 1: continue
        stop_dist = r["risk_per_share"]
        if stop_dist <= 0: continue
        qty = max(1, int(risk_budget / stop_dist))
        # cap qty by buying power
        max_qty = int(bp / r["price"]) if r["price"] > 0 else 0
        qty = min(qty, max_qty)
        if qty < 1:
            log(f"{t}: insufficient buying power for 1 share"); continue
        tp = r["price"] + TP_MULT * stop_dist
        sl = r["stop"]
        log(f"Super Hunter firing: {t} score={r['score']:.2f} qty={qty} entry~{r['price']:.2f} TP={tp:.2f} SL={sl:.2f}")
        order = place_buy(t, r["price"], sl, qty)
        if order:
            # place OCO protection on the filled qty (approx)
            place_oco(t, qty, tp, sl)
            open_count += 1
            bp -= qty * r["price"]

# ---------------- 7. REPORT ----------------
def report(df, uni):
    print("\n" + "="*78)
    print(f"UNICORN HUNTER REPORT v1.2 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*78)
    print(f"{'RANK':<5}{'TCKR':<7}{'PRICE':>8}{'RS%':>7}{'VOLx':>6}{'TIGHT%':>8}{'BRK%':>7}{'FLOAT':>12}{'SCORE':>7}{'CAT':>4}")
    print("-"*78)
    for i, (_, r) in enumerate(uni.iterrows(), 1):
        fl = f"{r['float']/1e6:.1f}M" if pd.notna(r['float']) else "n/a"
        print(f"{i:<5}{r['ticker']:<7}{r['price']:>8.2f}{r['rs']:>7.1f}{r['vol_ratio']:>6.2f}"
              f"{r['tightness']:>8.1f}{r['breakout_pct']:>7.1f}{fl:>12}{r['score']:>7.2f}{r['catalyst']:>4}")
    print("-"*78)
    print("Filters: float <= 50M | RS > 0 | 20d tightness < 35% | within 8% of 60d high")
    print(f"Super Hunter: score gate {SCORE_GATE} | max positions {MAX_POSITIONS} | "
          f"daily loss cap {DAILY_LOSS_CAP*100:.0f}% | 1% risk/trade | TP {TP_MULT}x risk")
    print("Stop = price - 3xATR. OCO on every entry. Float: FMP bulk or Finviz zero-key.")
    print("NOTE: Not financial advice. Backtest before sizing real money.")
    print("="*78 + "\n")
    uni.to_csv("unicorn_candidates.csv", index=False)
    log("Saved unicorn_candidates.csv")

def main():
    t0 = time.time()
    tickers = get_universe()
    closes, vols, highs, lows, spy = fetch_prices(tickers)
    df = compute_factors(closes, vols, highs, lows, spy)
    fl = fetch_float_bulk(tickers)
    df = apply_float(df, fl)
    df, uni = rank(df)
    report(df, uni)
    logged_in = rh_login()
    if logged_in:
        positions = rh_positions(); equity = rh_equity(); bp = rh_buying_power()
        log(f"RH: {len(positions)} positions, equity ${equity:,.2f}, buying power ${bp:,.2f}")
        super_hunter(uni, positions, equity, bp)
    log(f"Done in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
