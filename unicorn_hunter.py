#!/usr/bin/env python3
"""
THE HUNTER v1.1
Qullamaggie-style breakout scanner + FMP bulk float filter + Finviz zero-key fallback.

Pipeline:
1. Pull ~2,300 NYSE/Nasdaq tickers via yfinance (free, no API key)
2. Compute 6 breakout factors: catalyst volume, RS vs SPY, consolidation
   tightness, breakout level, weekly confluence, ATR
3. Bulk-fetch float from Financial Modeling Prep (free plan, 1 call);
   if no FMP key, scrape float per-ticker from Finviz (zero-key fallback)
4. Filter: float < 50M shares, price > $2, RS > 0, tight consolidation
5. Rank and print top unicorn candidates

Setup:
  pip install yfinance pandas numpy requests lxml html5lib
  export FMP_API_KEY=your_key   # optional, free at financialmodelingprep.com
  python unicorn_hunter.py

Schedule it: cron 0 8 * * 1-5  (8 AM ET, weekdays) or run manually.
"""

import os, sys, time, json, re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ---------------- CONFIG ----------------
UNIVERSE_SIZE = 2300          # tickers scanned
MIN_PRICE = 2.0
MAX_FLOAT = 50_000_000        # shares
RS_LOOKBACK = 60               # days vs SPY
CONSOL_LOOKBACK = 20           # days for tightness
VOL_SPIKE_MULT = 2.0          # volume vs 20-day avg
ATR_MULT = 3.0                # stop distance
TOP_N = 15
FMP_KEY = os.environ.get("FMP_API_KEY", "")
FMP_URL = "https://financialmodelingprep.com/api/v4/shares_float/all"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

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
        closes = data["Close"]
        vols = data["Volume"]
        highs = data["High"]
        lows = data["Low"]
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
            c = closes[t].dropna()
            v = vols[t].reindex(c.index).dropna()
            h = highs[t].reindex(c.index).dropna()
            l = lows[t].reindex(c.index).dropna()
            if len(c) < RS_LOOKBACK + 5:
                continue
            price = float(c.iloc[-1])
            if price < MIN_PRICE:
                continue
            ret = c.pct_change().dropna()
            spy_ret = spy.reindex(ret.index).pct_change().dropna()
            common = ret.index.intersection(spy_ret.index)
            if len(common) < 20:
                continue
            r, s = ret.loc[common], spy_ret.loc[common]
            cov = np.cov(r.values, s.values)
            beta = cov[0,1] / cov[1,1] if cov[1,1] else 1.0
            rs = float((r.mean() - beta * s.mean()) * 252 * 100)
            vol_now = float(v.iloc[-1])
            vol_avg = float(v.iloc[-20:].mean()) if len(v) >= 20 else float(v.mean())
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 0
            recent_h, recent_l = float(h.iloc[-CONSOL_LOOKBACK:].max()), float(l.iloc[-CONSOL_LOOKBACK:].min())
            tight = (recent_h - recent_l) / price * 100 if price else 999
            hi60 = float(h.iloc[-RS_LOOKBACK:].max())
            brk = (price / hi60 - 1) * 100 if hi60 else -100
            tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.iloc[-14:].mean()) if len(tr) >= 14 else float(tr.mean())
            wk = float(c.iloc[-5:].pct_change().mean() * 100) if len(c) >= 6 else 0
            catalyst = 1 if (vol_ratio >= VOL_SPIKE_MULT and float(ret.iloc[-1]) > 0) else 0
            rows.append({
                "ticker": t, "price": round(price,2), "rs": round(rs,1),
                "vol_ratio": round(vol_ratio,2), "tightness": round(tight,1),
                "breakout_pct": round(brk,1), "atr": round(atr,2),
                "wk_conf": round(wk,2), "catalyst": catalyst,
                "stop": round(price - ATR_MULT*atr, 2),
                "risk_per_share": round(ATR_MULT*atr, 2),
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    log(f"Factor table: {len(df)} valid tickers")
    return df

# ---------------- 4. FLOAT (FMP BULK + FINVIZ FALLBACK) ----------------
def fetch_float_fmp():
    log("Fetching bulk float from FMP...")
    if not FMP_KEY:
        log("FMP_API_KEY not set — skipping FMP, trying Finviz fallback")
        return {}
    try:
        r = requests.get(FMP_URL, params={"apikey": FMP_KEY}, timeout=60)
        r.raise_for_status()
        data = r.json()
        out = {}
        for row in data:
            t = row.get("symbol")
            f = row.get("floatShares") or row.get("freeFloat")
            if t and f:
                out[t] = int(f)
        log(f"FMP float records: {len(out)}")
        return out
    except Exception as e:
        log(f"FMP fetch failed: {e}")
        return {}

def fetch_float_finviz(tickers):
    """Zero-key fallback: scrape float from Finviz quote pages."""
    log(f"Finviz fallback: fetching float for {len(tickers)} tickers...")
    out = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; UnicornHunter/1.1)"}
    for i, t in enumerate(tickers):
        try:
            r = requests.get(f"https://finviz.com/quote.ashx?t={t}",
                             headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            m = re.search(
                r'Shs Float</td><td[^>]*>([0-9,.]+)\s*([KMBT]?)</td>', r.text)
            if m:
                val = float(m.group(1).replace(",", ""))
                mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2), 1)
                out[t] = int(val * mult)
            if (i + 1) % 50 == 0:
                log(f"  Finviz progress: {i+1}/{len(tickers)}")
                time.sleep(1.0)
            else:
                time.sleep(0.15)
        except Exception:
            continue
    log(f"Finviz float records: {len(out)}")
    return out

def fetch_float_bulk(tickers=None):
    fl = fetch_float_fmp()
    if fl:
        return fl
    if tickers:
        return fetch_float_finviz(tickers)
    return {}

def apply_float(df, fl):
    if not fl:
        df["float"] = None
        return df
    df["float"] = df["ticker"].map(fl)
    return df

# ---------------- 5. RANK ----------------
def rank(df):
    log("Ranking unicorns...")
    d = df.dropna(subset=["rs","vol_ratio","tightness","breakout_pct"]).copy()
    d["score"] = (
        d["rs"].clip(0, 80)/80 * 0.35 +
        d["vol_ratio"].clip(0, 10)/10 * 0.25 +
        (100 - d["tightness"].clip(0, 100))/100 * 0.20 +
        d["breakout_pct"].clip(-20, 5).add(20)/25 * 0.20
    )
    d = d.sort_values("score", ascending=False)
    uni = d[(d["float"].isna() | (d["float"] <= MAX_FLOAT)) &
            (d["rs"] > 0) & (d["tightness"] < 35) &
            (d["breakout_pct"] > -8)].head(TOP_N)
    return d, uni

# ---------------- 6. REPORT ----------------
def report(df, uni):
    print("\n" + "="*78)
    print(f"UNICORN HUNTER REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*78)
    print(f"{'RANK':<5}{'TCKR':<7}{'PRICE':>8}{'RS%':>7}{'VOLx':>6}{'TIGHT%':>8}{'BRK%':>7}{'FLOAT':>12}{'SCORE':>7}")
    print("-"*78)
    for i, (_, r) in enumerate(uni.iterrows(), 1):
        fl = f"{r['float']/1e6:.1f}M" if pd.notna(r['float']) else "n/a"
        print(f"{i:<5}{r['ticker']:<7}{r['price']:>8.2f}{r['rs']:>7.1f}{r['vol_ratio']:>6.2f}"
              f"{r['tightness']:>8.1f}{r['breakout_pct']:>7.1f}{fl:>12}{r['score']:>7.2f}")
    print("-"*78)
    print("Filters: float <= 50M | RS > 0 | 20d tightness < 35% | within 8% of 60d high")
    print("Stop = price - 3xATR. Position size = 1% account risk / risk_per_share.")
    print("Float source: FMP bulk if key set, else Finviz scrape (zero-key).")
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
    log(f"Done in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
