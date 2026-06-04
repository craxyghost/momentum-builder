"""
Backtest Engine — 12-Month Historical Simulation
=================================================
Simulates all 10 strategies using real historical price data from Yahoo Finance.

For each month in the past 12 months:
  1. Download 2 years of monthly OHLCV data in ONE bulk yfinance call
  2. Compute momentum scores using only data available up to that month-end
     (no look-ahead bias — exactly what you would have seen on rebalance day)
  3. Apply strategy-specific selection rules (sector caps, MACD filters, etc.)
  4. Entry price  = monthly Open  (first trading day of month)
  5. Exit price   = monthly Close (last trading day of month)
  6. Return       = (exit − entry) / entry × 100

Output: JSON with per-month stock picks, sector breakdown, entry/exit/return.
"""

import json
import os
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

log = logging.getLogger('backtest')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')

# ── Universe: curated liquid stocks per exchange ───────────────────────────────
NSE_UNIVERSE = [
    'NIFTYBEES.NS',                                                # Market ETF (for S1/S8 regime)
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'HINDUNILVR.NS', 'SBIN.NS', 'BAJFINANCE.NS', 'BHARTIARTL.NS', 'KOTAKBANK.NS',
    'AXISBANK.NS', 'ASIANPAINT.NS', 'MARUTI.NS', 'TITAN.NS', 'SUNPHARMA.NS',
    'ULTRACEMCO.NS', 'NESTLEIND.NS', 'WIPRO.NS', 'HCLTECH.NS', 'TATAMOTORS.NS',
    'TATASTEEL.NS', 'POWERGRID.NS', 'NTPC.NS', 'ONGC.NS', 'COALINDIA.NS',
    'ADANIENT.NS', 'ADANIPORTS.NS', 'JSWSTEEL.NS', 'BPCL.NS', 'TECHM.NS',
    'DIVISLAB.NS', 'DRREDDY.NS', 'CIPLA.NS', 'EICHERMOT.NS', 'BAJAJFINSV.NS',
    'INDUSINDBK.NS', 'HEROMOTOCO.NS', 'BRITANNIA.NS', 'ITC.NS', 'PIDILITIND.NS',
    'HAVELLS.NS', 'DABUR.NS', 'MARICO.NS', 'COLPAL.NS', 'TATACONSUM.NS',
    'APOLLOHOSP.NS', 'MUTHOOTFIN.NS', 'CHOLAFIN.NS', 'TORNTPHARM.NS', 'LUPIN.NS',
    'AUROPHARMA.NS', 'BIOCON.NS', 'TATAPOWER.NS', 'SIEMENS.NS', 'BEL.NS',
    'HAL.NS', 'IRCTC.NS', 'ZOMATO.NS', 'DMART.NS', 'JUBLFOOD.NS',
    'PIIND.NS', 'DEEPAKNI.NS', 'UPL.NS', 'M&M.NS', 'BAJAJ-AUTO.NS',
    'TVSMOTORS.NS', 'SBILIFE.NS', 'HDFCLIFE.NS', 'ICICIGI.NS', 'MAXHEALTH.NS',
    'FORTIS.NS', 'VEDL.NS', 'GRASIM.NS', 'ASHOKLEY.NS', 'ESCORTS.NS',
    'COROMANDEL.NS', 'BERGEPAINT.NS', 'PAGEIND.NS', 'MFSL.NS', 'ABB.NS',
]

NYSE_UNIVERSE = [
    'SPY',                                                          # Market ETF (for S1/S8 regime)
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'JPM', 'JNJ', 'V',
    'PG', 'UNH', 'HD', 'MA', 'BAC', 'ADBE', 'CRM', 'NFLX', 'AMD', 'PYPL',
    'CSCO', 'PEP', 'AVGO', 'COST', 'TMO', 'ACN', 'MRK', 'ABT', 'LLY', 'ABBV',
    'WMT', 'CVX', 'XOM', 'PFE', 'NKE', 'MCD', 'KO', 'ORCL', 'QCOM', 'TXN',
    'HON', 'CAT', 'BA', 'GE', 'RTX', 'LMT', 'MS', 'GS', 'C', 'WFC',
    'AXP', 'BLK', 'SCHW', 'AMGN', 'GILD', 'REGN', 'VRTX', 'BSX', 'SYK', 'MDT',
    'NEE', 'DUK', 'SO', 'AMT', 'EQIX', 'PLD', 'UBER', 'ABNB', 'SQ', 'SHOP',
    'CRWD', 'SNOW', 'DDOG', 'NET', 'PLTR', 'COIN', 'INTC', 'IBM', 'EMR', 'ETN',
    'DE', 'UNP', 'MMM', 'SPG', 'PSA', 'DLR', 'ZM', 'DOCU', 'OKTA', 'MDB',
]

NSE_SECTOR_MAP = {
    'NIFTYBEES.NS': 'ETF', 'RELIANCE.NS': 'Energy', 'TCS.NS': 'IT',
    'HDFCBANK.NS': 'Banking', 'INFY.NS': 'IT', 'ICICIBANK.NS': 'Banking',
    'HINDUNILVR.NS': 'FMCG', 'SBIN.NS': 'Banking', 'BAJFINANCE.NS': 'NBFC',
    'BHARTIARTL.NS': 'Telecom', 'KOTAKBANK.NS': 'Banking', 'AXISBANK.NS': 'Banking',
    'ASIANPAINT.NS': 'Paints', 'MARUTI.NS': 'Auto', 'TITAN.NS': 'Consumer',
    'SUNPHARMA.NS': 'Pharma', 'ULTRACEMCO.NS': 'Cement', 'NESTLEIND.NS': 'FMCG',
    'WIPRO.NS': 'IT', 'HCLTECH.NS': 'IT', 'TATAMOTORS.NS': 'Auto',
    'TATASTEEL.NS': 'Metals', 'POWERGRID.NS': 'Power', 'NTPC.NS': 'Power',
    'ONGC.NS': 'Energy', 'COALINDIA.NS': 'Energy', 'ADANIENT.NS': 'Conglomerate',
    'ADANIPORTS.NS': 'Infra', 'JSWSTEEL.NS': 'Metals', 'BPCL.NS': 'Energy',
    'TECHM.NS': 'IT', 'DIVISLAB.NS': 'Pharma', 'DRREDDY.NS': 'Pharma',
    'CIPLA.NS': 'Pharma', 'EICHERMOT.NS': 'Auto', 'BAJAJFINSV.NS': 'NBFC',
    'INDUSINDBK.NS': 'Banking', 'HEROMOTOCO.NS': 'Auto', 'BRITANNIA.NS': 'FMCG',
    'ITC.NS': 'FMCG', 'PIDILITIND.NS': 'Chemicals', 'HAVELLS.NS': 'Electricals',
    'DABUR.NS': 'FMCG', 'MARICO.NS': 'FMCG', 'COLPAL.NS': 'FMCG',
    'TATACONSUM.NS': 'FMCG', 'APOLLOHOSP.NS': 'Healthcare', 'MUTHOOTFIN.NS': 'NBFC',
    'CHOLAFIN.NS': 'NBFC', 'TORNTPHARM.NS': 'Pharma', 'LUPIN.NS': 'Pharma',
    'AUROPHARMA.NS': 'Pharma', 'BIOCON.NS': 'Pharma', 'TATAPOWER.NS': 'Power',
    'SIEMENS.NS': 'Electricals', 'BEL.NS': 'Defense', 'HAL.NS': 'Defense',
    'IRCTC.NS': 'Travel', 'ZOMATO.NS': 'Consumer Tech', 'DMART.NS': 'Retail',
    'JUBLFOOD.NS': 'Food', 'PIIND.NS': 'Chemicals', 'DEEPAKNI.NS': 'Chemicals',
    'UPL.NS': 'Agro', 'M&M.NS': 'Auto', 'BAJAJ-AUTO.NS': 'Auto',
    'TVSMOTORS.NS': 'Auto', 'SBILIFE.NS': 'Insurance', 'HDFCLIFE.NS': 'Insurance',
    'ICICIGI.NS': 'Insurance', 'MAXHEALTH.NS': 'Healthcare', 'FORTIS.NS': 'Healthcare',
    'VEDL.NS': 'Metals', 'GRASIM.NS': 'Cement', 'ASHOKLEY.NS': 'Auto',
    'ESCORTS.NS': 'Auto', 'COROMANDEL.NS': 'Agro', 'BERGEPAINT.NS': 'Paints',
    'PAGEIND.NS': 'Apparel', 'MFSL.NS': 'Insurance', 'ABB.NS': 'Electricals',
}

NYSE_SECTOR_MAP = {
    'SPY': 'ETF', 'AAPL': 'Technology', 'MSFT': 'Technology', 'GOOGL': 'Technology',
    'AMZN': 'E-Commerce', 'NVDA': 'Semiconductors', 'META': 'Social Media',
    'TSLA': 'EV/Auto', 'JPM': 'Banking', 'JNJ': 'Healthcare', 'V': 'Payments',
    'PG': 'Consumer', 'UNH': 'Healthcare', 'HD': 'Retail', 'MA': 'Payments',
    'BAC': 'Banking', 'ADBE': 'Software', 'CRM': 'Software', 'NFLX': 'Streaming',
    'AMD': 'Semiconductors', 'PYPL': 'Payments', 'CSCO': 'Networking', 'PEP': 'Consumer',
    'AVGO': 'Semiconductors', 'COST': 'Retail', 'TMO': 'Life Sciences',
    'ACN': 'IT Services', 'MRK': 'Pharma', 'ABT': 'Healthcare', 'LLY': 'Pharma',
    'ABBV': 'Pharma', 'WMT': 'Retail', 'CVX': 'Energy', 'XOM': 'Energy',
    'PFE': 'Pharma', 'NKE': 'Consumer', 'MCD': 'Food', 'KO': 'Consumer',
    'ORCL': 'Software', 'QCOM': 'Semiconductors', 'TXN': 'Semiconductors',
    'HON': 'Industrial', 'CAT': 'Industrial', 'BA': 'Aerospace', 'GE': 'Industrial',
    'RTX': 'Aerospace', 'LMT': 'Defense', 'MS': 'Banking', 'GS': 'Banking',
    'C': 'Banking', 'WFC': 'Banking', 'AXP': 'Financial', 'BLK': 'Asset Mgmt',
    'SCHW': 'Financial', 'AMGN': 'Biotech', 'GILD': 'Biotech', 'REGN': 'Biotech',
    'VRTX': 'Biotech', 'BSX': 'Med Devices', 'SYK': 'Med Devices', 'MDT': 'Med Devices',
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities', 'AMT': 'REIT',
    'EQIX': 'REIT', 'PLD': 'REIT', 'UBER': 'Consumer Tech', 'ABNB': 'Consumer Tech',
    'SQ': 'Fintech', 'SHOP': 'E-Commerce', 'CRWD': 'Cybersecurity', 'SNOW': 'Cloud',
    'DDOG': 'Cloud', 'NET': 'Cloud', 'PLTR': 'AI/Data', 'COIN': 'Crypto',
    'INTC': 'Semiconductors', 'IBM': 'Technology', 'EMR': 'Industrial', 'ETN': 'Industrial',
    'DE': 'Industrial', 'UNP': 'Transportation', 'MMM': 'Industrial', 'SPG': 'REIT',
    'PSA': 'REIT', 'DLR': 'REIT', 'ZM': 'Software', 'DOCU': 'Software',
    'OKTA': 'Cybersecurity', 'MDB': 'Cloud',
}

# Progress tracker (in-memory, per exchange+strategy)
_progress: dict = {}


def set_progress(exchange: str, sid: str, msg: str, pct: int = 0):
    key = f'{exchange}_{sid}'
    _progress[key] = {'message': msg, 'pct': pct, 'ts': datetime.now().isoformat()}
    log.info(f'[{key}] {pct}% — {msg}')


def get_progress(exchange: str, sid: str) -> dict:
    return _progress.get(f'{exchange}_{sid}', {'message': 'Idle', 'pct': 0})


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    up  = delta.clip(lower=0)
    dn  = (-delta).clip(lower=0)
    avg_up = up.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    avg_dn = dn.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _score_stock(closes: pd.Series, highs: pd.Series) -> tuple[float, float, float, float]:
    """
    Compute (final_score, macd_score, rsi_score, h52_score) from historical data.
    Uses exactly the same 5-indicator formula as the live screener.
    NO look-ahead: only data in the passed series is used.
    """
    n = len(closes)
    if n < 6 or closes.isna().all():
        return 0.0, 0.0, 50.0, 50.0

    c = closes.dropna()
    h = highs.dropna()
    n = len(c)
    if n < 6:
        return 0.0, 0.0, 50.0, 50.0

    # ── 1. 12-1M Price Momentum ─────────────────────────────────────────────
    if n >= 13:
        ret_12m = (c.iloc[-2] / c.iloc[-13] - 1) * 100
    elif n >= 2:
        ret_12m = (c.iloc[-2] / c.iloc[0]  - 1) * 100
    else:
        ret_12m = 0.0
    mom_score = min(100.0, max(0.0, (ret_12m + 30) / 70 * 100))

    # ── 2. RSI (14-period monthly) ──────────────────────────────────────────
    rsi_vals  = _rsi(c, 14)
    rsi_score = float(rsi_vals.iloc[-1]) if not pd.isna(rsi_vals.iloc[-1]) else 50.0

    # ── 3. MACD (12-26-9) ───────────────────────────────────────────────────
    ema12  = c.ewm(span=12, adjust=False).mean()
    ema26  = c.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    m_now  = float(macd.iloc[-1])
    s_now  = float(signal.iloc[-1])
    m_prev = float(macd.iloc[-2]) if n >= 2 else m_now

    if m_now > s_now:   # Bullish
        macd_score = 90.0 if m_now > m_prev else 70.0   # accelerating vs steady
    else:               # Bearish
        macd_score = 20.0 if m_now < m_prev else 40.0   # falling vs stabilising

    # ── 4. 52-Week High Ratio ───────────────────────────────────────────────
    h_slice = h.iloc[-13:] if len(h) >= 13 else h
    h52     = float(h_slice.max()) if len(h_slice) else float(c.max())
    cur     = float(c.iloc[-1])
    h52_score = min(100.0, cur / h52 * 100) if h52 > 0 else 50.0

    # ── 5. 6M vs 12M SMA Cross ──────────────────────────────────────────────
    sma6  = c.rolling(6,  min_periods=1).mean()
    sma12 = c.rolling(12, min_periods=1).mean()
    s6  = float(sma6.iloc[-1])
    s12 = float(sma12.iloc[-1])
    spread  = (s6 - s12) / s12 * 100 if s12 > 0 else 0.0
    ma_score = min(100.0, max(0.0, (spread + 20) / 40 * 100))

    final = (mom_score * 0.30 + rsi_score * 0.20 + macd_score * 0.20
             + h52_score * 0.15 + ma_score * 0.15)
    return round(final, 1), round(macd_score, 1), round(rsi_score, 1), round(h52_score, 1)


def _etf_12m(closes: pd.DataFrame, etf: str, up_to: int) -> float:
    """12-month return for market ETF up to data index `up_to`."""
    if etf not in closes.columns:
        return 5.0                     # default: assume bull
    c = closes[etf].iloc[:up_to].dropna()
    if len(c) < 13:
        return 5.0
    return float((c.iloc[-2] / c.iloc[-13] - 1) * 100)


# ── Strategy selection (historical simulation) ─────────────────────────────────

def _apply_strategy(candidates: list, sid: str, exchange: str,
                    closes: pd.DataFrame, up_to: int) -> list:
    """
    Apply strategy-specific selection rules to a list of scored stocks.
    `candidates` is sorted by score DESC. Each item is a dict with keys:
        ticker, symbol, sector, score, macd_score, rsi_score, h52_score,
        entry_price, exit_price, return_pct
    """
    etf_ticker = 'NIFTYBEES.NS' if exchange == 'NSE' else 'SPY'
    elite = [s for s in candidates if s['score'] >= 81]

    if sid == 's1':
        # Dual Momentum — market gate, then top 15
        mkt = _etf_12m(closes, etf_ticker, up_to)
        if mkt <= 0:
            return []           # BEAR: cash
        return (elite or candidates)[:15]

    if sid == 's2':
        # Quality 50 — sector cap ≤ 3
        sc, out = defaultdict(int), []
        for s in candidates:
            if sc[s['sector']] < 3:
                out.append(s); sc[s['sector']] += 1
            if len(out) >= 50: break
        return out

    if sid == 's3':
        # QVM 25 — sector cap ≤ 2
        sc, out = defaultdict(int), []
        for s in candidates:
            if sc[s['sector']] < 2:
                out.append(s); sc[s['sector']] += 1
            if len(out) >= 25: break
        return out

    if sid == 's4':
        # Low Vol 30 — prefer smooth (h52 + score blend), sector cap ≤ 4
        ranked = sorted(elite or candidates,
                        key=lambda x: x['h52_score'] * 0.5 + x['score'] * 0.5,
                        reverse=True)
        sc, out = defaultdict(int), []
        for s in ranked:
            if sc[s['sector']] < 4:
                out.append(s); sc[s['sector']] += 1
            if len(out) >= 30: break
        return out

    if sid == 's5':
        # Sector-Neutral 10 — best 1-2 per sector
        by_sec = defaultdict(list)
        for s in (elite or candidates): by_sec[s['sector']].append(s)
        out = []
        for tier in range(2):
            for picks in by_sec.values():
                if len(out) >= 10: break
                if len(picks) > tier: out.append(picks[tier])
            if len(out) >= 10: break
        return sorted(out, key=lambda x: x['score'], reverse=True)[:10]

    if sid == 's6':
        # Sweet Spot 81-91
        sweet = [s for s in candidates if 81 <= s['score'] <= 91]
        if not sweet: sweet = [s for s in candidates if 81 <= s['score'] <= 93]
        sc, out = defaultdict(int), []
        for s in sweet:
            if sc[s['sector']] < 3:
                out.append(s); sc[s['sector']] += 1
            if len(out) >= 20: break
        return out

    if sid == 's7':
        # AI APEX — top 2
        return (elite or candidates)[:2]

    if sid == 's8':
        # DSM — market gate + buffer ≥ 83 + sector neutral
        mkt = _etf_12m(closes, etf_ticker, up_to)
        if mkt <= 0: return []
        buf = [s for s in candidates if s['score'] >= 83]
        if not buf: buf = elite
        by_sec = defaultdict(list)
        for s in (buf or candidates): by_sec[s['sector']].append(s)
        out = []
        for tier in range(2):
            for picks in by_sec.values():
                if len(out) >= 10: break
                if len(picks) > tier: out.append(picks[tier])
            if len(out) >= 10: break
        return sorted(out, key=lambda x: x['score'], reverse=True)

    if sid == 's9':
        # Ignition — MACD 40% + RSI 25% + score 20% + h52 15%
        filt = [s for s in elite if s['macd_score'] >= 75 and s['rsi_score'] >= 65]
        if len(filt) < 3: filt = [s for s in elite if s['score'] >= 85]
        if not filt:      filt = elite[:10]
        for s in filt:
            s['ignition'] = (s['macd_score'] * 0.40 + s['rsi_score'] * 0.25
                             + s['score'] * 0.20 + s['h52_score'] * 0.15)
            if s['macd_score'] >= 80 and s['rsi_score'] >= 75 and s['h52_score'] >= 80:
                s['ignition'] += 5.0
        return sorted(filt, key=lambda x: x.get('ignition', 0), reverse=True)[:5]

    if sid == 's10':
        # Sector Dominator — top 3 from best sector
        by_sec = defaultdict(list)
        for s in (elite or candidates): by_sec[s['sector']].append(s)
        if not by_sec: return []
        alphas = {}
        for sec, ss in by_sec.items():
            top3 = sorted(ss, key=lambda x: x['score'], reverse=True)[:3]
            avg  = sum(x['score'] for x in top3) / len(top3)
            topv = top3[0]['score']
            alphas[sec] = avg * 0.70 + topv * 0.30 + min(len(ss), 5) * 0.5
        best = max(alphas, key=alphas.get)
        return sorted(by_sec[best], key=lambda x: x['score'], reverse=True)[:3]

    return elite[:10]  # default


# ── Main backtest function ─────────────────────────────────────────────────────

def run_strategy_backtest(exchange: str, sid: str, months: int = 12) -> dict:
    """
    Run a 12-month historical backtest for a single strategy.
    Returns a full result dict and saves it to data/backtest_<exchange>_<sid>.json.
    """
    from strategy_builder import STRATEGIES

    set_progress(exchange, sid, 'Fetching universe data from Yahoo Finance…', 5)

    universe    = NSE_UNIVERSE  if exchange == 'NSE' else NYSE_UNIVERSE
    sector_map  = NSE_SECTOR_MAP if exchange == 'NSE' else NYSE_SECTOR_MAP

    # ── Step 1: bulk download 2 years of monthly data ─────────────────────────
    try:
        raw = yf.download(
            universe,
            period='2y',
            interval='1mo',
            progress=False,
            auto_adjust=True,
            threads=True,
        )
    except Exception as e:
        log.error(f'Data download failed: {e}')
        return {'error': str(e)}

    if raw.empty:
        return {'error': 'No data returned from Yahoo Finance. Try again later.'}

    set_progress(exchange, sid, f'Downloaded {len(universe)} stocks. Computing scores…', 20)

    # Handle MultiIndex columns (multiple tickers) vs flat (single ticker)
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw['Close']
        opens  = raw['Open']
        highs  = raw['High']
    else:
        # Single ticker fallback
        closes = raw[['Close']].rename(columns={'Close': universe[0]})
        opens  = raw[['Open']].rename(columns={'Open': universe[0]})
        highs  = raw[['High']].rename(columns={'High': universe[0]})

    # Drop stocks with too little history (< 15 months)
    valid = [t for t in closes.columns if closes[t].dropna().count() >= 15]
    closes = closes[valid]
    opens  = opens[[c for c in valid if c in opens.columns]]
    highs  = highs[[c for c in valid if c in highs.columns]]

    log.info(f'{exchange}: {len(valid)} valid tickers after filtering')

    # ── Step 2: build 12-month backtest window ────────────────────────────────
    # Current month is Jun 2026, last complete month = May 2026
    now = pd.Timestamp.now()
    last_complete = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    target_months = [last_complete - pd.DateOffset(months=i) for i in range(months - 1, -1, -1)]

    monthly_results = []
    portfolio_values = [100.0]          # Start ₹100 / $100

    total_steps = len(target_months)
    for step, target_month in enumerate(target_months):
        pct = 20 + int(step / total_steps * 75)
        set_progress(exchange, sid,
                     f'Simulating {target_month.strftime("%b %Y")}… ({step+1}/{total_steps})',
                     pct)

        # Find this month's index in the data
        month_idx = None
        for i, ts in enumerate(closes.index):
            if ts.year == target_month.year and ts.month == target_month.month:
                month_idx = i
                break

        if month_idx is None:
            log.warning(f'No data for {target_month.strftime("%b %Y")} — skipping')
            continue

        # Score cutoff = data up to (but NOT including) this month
        # i.e. we use info available at end of previous month
        score_cutoff = month_idx           # slice is [:score_cutoff] = excludes current month

        # ── Score every stock ─────────────────────────────────────────────────
        stock_records = []
        for ticker in valid:
            if ticker in (NSE_UNIVERSE[0], NYSE_UNIVERSE[0]):
                continue               # skip market ETF from stock picks

            c_slice = closes[ticker].iloc[:score_cutoff].dropna()
            h_slice = highs[ticker].iloc[:score_cutoff].dropna() if ticker in highs else c_slice

            if len(c_slice) < 6:
                continue

            score, macd_s, rsi_s, h52_s = _score_stock(c_slice, h_slice)

            # Get this month's entry (Open) and exit (Close)
            try:
                entry_p = float(opens[ticker].iloc[month_idx])  if ticker in opens.columns  else 0.0
                exit_p  = float(closes[ticker].iloc[month_idx]) if ticker in closes.columns else 0.0
            except (IndexError, KeyError):
                continue

            if entry_p <= 0 or exit_p <= 0 or pd.isna(entry_p) or pd.isna(exit_p):
                continue

            ret = round((exit_p - entry_p) / entry_p * 100, 2)
            sym = ticker.replace('.NS', '').replace('.BO', '')

            stock_records.append({
                'ticker':       ticker,
                'symbol':       sym,
                'sector':       sector_map.get(ticker, 'Other'),
                'score':        score,
                'macd_score':   macd_s,
                'rsi_score':    rsi_s,
                'h52_score':    h52_s,
                'entry_price':  round(entry_p, 2),
                'exit_price':   round(exit_p,  2),
                'return_pct':   ret,
            })

        # Sort by score descending
        stock_records.sort(key=lambda x: x['score'], reverse=True)

        # ── Apply strategy selection ──────────────────────────────────────────
        selected = _apply_strategy(stock_records, sid, exchange, closes, score_cutoff)

        # ── Compute monthly portfolio return (equal-weight) ───────────────────
        if selected:
            port_return = round(sum(s['return_pct'] for s in selected) / len(selected), 2)
            mode = 'INVESTED'
        else:
            port_return = 0.0
            mode = 'CASH'

        new_val = round(portfolio_values[-1] * (1 + port_return / 100), 2)
        portfolio_values.append(new_val)

        # Sector breakdown
        sectors = defaultdict(int)
        for s in selected: sectors[s['sector']] += 1

        monthly_results.append({
            'month':                target_month.strftime('%b %Y'),
            'month_key':            target_month.strftime('%Y-%m'),
            'stocks':               selected,
            'portfolio_return_pct': port_return,
            'portfolio_value':      new_val,
            'num_stocks':           len(selected),
            'sectors':              dict(sectors),
            'mode':                 mode,
            'best_stock':           max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'worst_stock':          min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'best_return':          round(max(s['return_pct'] for s in selected), 2) if selected else 0,
            'worst_return':         round(min(s['return_pct'] for s in selected), 2) if selected else 0,
        })

    set_progress(exchange, sid, 'Computing summary statistics…', 95)

    # ── Summary statistics ────────────────────────────────────────────────────
    returns = [m['portfolio_return_pct'] for m in monthly_results]
    final_val = portfolio_values[-1]
    total_return_pct = round((final_val / 100 - 1) * 100, 2)

    n_months = len(monthly_results)
    annualized = round(((1 + total_return_pct / 100) ** (12 / max(n_months, 1)) - 1) * 100, 1) if n_months else 0.0
    win_rate   = round(sum(1 for r in returns if r > 0) / max(len(returns), 1) * 100, 1)

    # Max drawdown
    peak, max_dd = portfolio_values[0], 0.0
    for v in portfolio_values:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    # Sharpe ratio (monthly)
    sharpe = 0.0
    if len(returns) > 1:
        avg_r = np.mean(returns)
        std_r = np.std(returns, ddof=1)
        sharpe = round((avg_r / std_r) * (12 ** 0.5), 2) if std_r > 0 else 0.0

    best_m_idx  = int(np.argmax(returns))  if returns else 0
    worst_m_idx = int(np.argmin(returns))  if returns else 0

    strat_meta = STRATEGIES.get(sid, {})

    result = {
        'strategy_id':    sid,
        'strategy_name':  strat_meta.get('name',  sid.upper()),
        'strategy_icon':  strat_meta.get('icon',  '📊'),
        'exchange':       exchange,
        'generated_at':   datetime.now().strftime('%Y-%m-%d %H:%M'),
        'period': {
            'start':  monthly_results[0]['month']  if monthly_results else '',
            'end':    monthly_results[-1]['month'] if monthly_results else '',
            'months': n_months,
        },
        'monthly_results':  monthly_results,
        'portfolio_values': portfolio_values,
        'summary': {
            'total_return_pct':   total_return_pct,
            'annualized_return':  annualized,
            'win_rate':           win_rate,
            'max_drawdown':       round(max_dd, 2),
            'sharpe_ratio':       sharpe,
            'best_month':         monthly_results[best_m_idx]['month']  if returns else '',
            'best_month_return':  max(returns) if returns else 0,
            'worst_month':        monthly_results[worst_m_idx]['month'] if returns else '',
            'worst_month_return': min(returns) if returns else 0,
            'total_trades':       sum(m['num_stocks'] for m in monthly_results),
            'universe_size':      len(valid),
        },
    }

    # Save to disk
    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    with open(path, 'w') as f:
        json.dump(result, f, indent=2)

    set_progress(exchange, sid, 'Done ✅', 100)
    log.info(f'{exchange} {sid.upper()} backtest complete: '
             f'{total_return_pct:+.1f}% total | {annualized:+.0f}% annualised')
    return result


def load_backtest(exchange: str, sid: str) -> dict | None:
    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass
    return None


def run_all_backtests(exchange: str) -> dict:
    """Run backtest for all 10 strategies (called from background thread)."""
    from strategy_builder import STRATEGIES
    results = {}
    for sid in STRATEGIES:
        try:
            results[sid] = run_strategy_backtest(exchange, sid)
        except Exception as e:
            log.error(f'{exchange} {sid} failed: {e}')
            results[sid] = {'error': str(e)}
    return results
