"""
Backtest Engine — 12-Month Historical Simulation (v3)
======================================================
Fix history:
  v1 — bulk download, threads=True  → deadlock on Railway
  v2 — individual per-ticker fetch  → Yahoo throttles after ~20 requests
  v3 — single bulk download, threads=False, 120-s thread wrapper
         ONE HTTP request → no throttling, no deadlock
"""

import json
import os
import logging
import threading
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

log = logging.getLogger('backtest')

# ── Universe: 20 liquid stocks + market ETF per exchange ──────────────────────
# Kept at 20 so the single bulk download is fast and reliable.
NSE_UNIVERSE = [
    'NIFTYBEES.NS',    # market ETF for regime detection
    'RELIANCE.NS',     # Energy
    'TCS.NS',          # IT
    'HDFCBANK.NS',     # Banking
    'INFY.NS',         # IT
    'ICICIBANK.NS',    # Banking
    'HINDUNILVR.NS',   # FMCG
    'SBIN.NS',         # Banking
    'BAJFINANCE.NS',   # NBFC
    'BHARTIARTL.NS',   # Telecom
    'KOTAKBANK.NS',    # Banking
    'ASIANPAINT.NS',   # Paints
    'MARUTI.NS',       # Auto
    'TITAN.NS',        # Consumer
    'SUNPHARMA.NS',    # Pharma
    'WIPRO.NS',        # IT
    'EICHERMOT.NS',    # Auto (TATAMOTORS.NS delisted from yfinance)
    'TATASTEEL.NS',    # Metals
    'NTPC.NS',         # Power
    'ITC.NS',          # FMCG
    'DRREDDY.NS',      # Pharma
]

NYSE_UNIVERSE = [
    'SPY',     # market ETF for regime detection
    'AAPL',    # Technology
    'MSFT',    # Technology
    'GOOGL',   # Technology
    'AMZN',    # E-Commerce
    'NVDA',    # Semiconductors
    'META',    # Social Media
    'TSLA',    # EV/Auto
    'JPM',     # Banking
    'JNJ',     # Healthcare
    'V',       # Payments
    'PG',      # Consumer
    'UNH',     # Healthcare
    'MA',      # Payments
    'ADBE',    # Software
    'NFLX',    # Streaming
    'AMD',     # Semiconductors
    'XOM',     # Energy
    'LLY',     # Pharma
    'CRWD',    # Cybersecurity
    'COST',    # Retail
]

NSE_SECTOR_MAP = {
    'NIFTYBEES.NS':'ETF',       'RELIANCE.NS':'Energy',    'TCS.NS':'IT',
    'HDFCBANK.NS':'Banking',    'INFY.NS':'IT',            'ICICIBANK.NS':'Banking',
    'HINDUNILVR.NS':'FMCG',     'SBIN.NS':'Banking',       'BAJFINANCE.NS':'NBFC',
    'BHARTIARTL.NS':'Telecom',  'KOTAKBANK.NS':'Banking',  'ASIANPAINT.NS':'Paints',
    'MARUTI.NS':'Auto',         'TITAN.NS':'Consumer',     'SUNPHARMA.NS':'Pharma',
    'WIPRO.NS':'IT',            'EICHERMOT.NS':'Auto',     'TATASTEEL.NS':'Metals',
    'NTPC.NS':'Power',          'ITC.NS':'FMCG',           'DRREDDY.NS':'Pharma',
}

NYSE_SECTOR_MAP = {
    'SPY':'ETF',         'AAPL':'Technology',  'MSFT':'Technology',
    'GOOGL':'Technology','AMZN':'E-Commerce',  'NVDA':'Semiconductors',
    'META':'Social Media','TSLA':'EV/Auto',    'JPM':'Banking',
    'JNJ':'Healthcare',  'V':'Payments',        'PG':'Consumer',
    'UNH':'Healthcare',  'MA':'Payments',       'ADBE':'Software',
    'NFLX':'Streaming',  'AMD':'Semiconductors','XOM':'Energy',
    'LLY':'Pharma',      'CRWD':'Cybersecurity','COST':'Retail',
}


# ── File-based progress (works across Railway workers) ────────────────────────
def _prog_path(exchange, sid):
    return os.path.join(DATA_DIR, f'bt_prog_{exchange.lower()}_{sid}.json')

def set_progress(exchange, sid, msg, pct=0):
    try:
        with open(_prog_path(exchange, sid), 'w') as f:
            json.dump({'message': msg, 'pct': int(pct),
                       'ts': datetime.now().isoformat()}, f)
    except Exception:
        pass
    log.info(f'[{exchange}/{sid}] {pct}% — {msg}')

def get_progress(exchange, sid):
    try:
        p = _prog_path(exchange, sid)
        if os.path.exists(p):
            return json.load(open(p))
    except Exception:
        pass
    return {'message': 'Idle', 'pct': 0}


# ── Single bulk download with thread-level timeout ────────────────────────────
def _bulk_download(tickers: list, timeout_sec: int = 120) -> pd.DataFrame | None:
    """
    Download 2 years of monthly OHLCV for all tickers in ONE HTTP request.
    threads=False → no yfinance internal thread pool → no deadlock.
    Wrapped in a daemon thread so we can enforce a hard timeout.
    Returns the raw DataFrame or None on timeout/error.
    """
    result = [None]
    error  = [None]

    def _do():
        try:
            result[0] = yf.download(
                tickers,
                period='2y',
                interval='1mo',
                progress=False,
                auto_adjust=True,
                threads=False,       # <-- key: single-threaded, no deadlock
            )
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        log.error(f'Bulk download timed out after {timeout_sec}s')
        return None
    if error[0]:
        log.error(f'Bulk download error: {error[0]}')
        return None
    return result[0]


def _extract_series(raw: pd.DataFrame, tickers: list) -> tuple[dict, dict, dict]:
    """
    Extract per-ticker Close / Open / High Series from a yfinance bulk result.
    Handles both MultiIndex (multiple tickers) and flat (single ticker) columns.
    Returns (closes_dict, opens_dict, highs_dict).
    """
    closes, opens, highs = {}, {}, {}

    if raw is None or raw.empty:
        return closes, opens, highs

    if isinstance(raw.columns, pd.MultiIndex):
        # MultiIndex shape: (price_type, ticker)
        # e.g. raw['Close']['AAPL']
        for price_type, df_name in [('Close', closes), ('Open', opens), ('High', highs)]:
            if price_type not in raw.columns.get_level_values(0):
                continue
            price_df = raw[price_type]
            for ticker in tickers:
                if ticker in price_df.columns:
                    s = price_df[ticker].dropna()
                    if len(s) >= 14:
                        df_name[ticker] = price_df[ticker]
    else:
        # Flat columns — happens when only one ticker was passed
        if len(tickers) == 1:
            t = tickers[0]
            if 'Close' in raw.columns and len(raw['Close'].dropna()) >= 14:
                closes[t] = raw['Close']
                opens[t]  = raw['Open']  if 'Open'  in raw.columns else raw['Close']
                highs[t]  = raw['High']  if 'High'  in raw.columns else raw['Close']

    return closes, opens, highs


# ── Indicator helpers ─────────────────────────────────────────────────────────
def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d  = s.diff()
    up = d.clip(lower=0);  dn = (-d).clip(lower=0)
    ag = up.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = dn.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    return (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(50.0)


def _score_stock(closes: pd.Series, highs: pd.Series) -> tuple[float, float, float, float]:
    """Return (final_score, macd_score, rsi_score, h52_score). Identical to live screener."""
    c = closes.dropna(); h = highs.dropna()
    n = len(c)
    if n < 6:
        return 0.0, 50.0, 50.0, 50.0

    # 1. 12-1M momentum → 0-100
    ret_12m = (float(c.iloc[-2]) / float(c.iloc[-13]) - 1) * 100 if n >= 13 \
              else (float(c.iloc[-2]) / float(c.iloc[0]) - 1) * 100 if n >= 2 else 0.0
    mom = min(100.0, max(0.0, (ret_12m + 30) / 70 * 100))

    # 2. RSI-14
    rsi_s = float(_rsi(c, 14).iloc[-1])

    # 3. MACD 12-26-9
    macd   = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    m, s, mp = float(macd.iloc[-1]), float(signal.iloc[-1]), \
                float(macd.iloc[-2]) if n >= 2 else float(macd.iloc[-1])
    macd_s = (90.0 if m > mp else 70.0) if m > s else (20.0 if m < mp else 40.0)

    # 4. 52W high ratio
    h52   = float((h.iloc[-13:] if len(h) >= 13 else h).max())
    h52_s = min(100.0, float(c.iloc[-1]) / h52 * 100) if h52 > 0 else 50.0

    # 5. 6M vs 12M SMA
    s6, s12 = float(c.rolling(6, 1).mean().iloc[-1]), float(c.rolling(12, 1).mean().iloc[-1])
    ma_s = min(100.0, max(0.0, ((s6 - s12) / s12 * 100 + 20) / 40 * 100)) if s12 else 50.0

    final = mom * .30 + rsi_s * .20 + macd_s * .20 + h52_s * .15 + ma_s * .15
    return round(final, 1), round(macd_s, 1), round(rsi_s, 1), round(h52_s, 1)


def _etf_12m(closes: dict, etf: str, up_to: int) -> float:
    if etf not in closes: return 5.0
    c = closes[etf].iloc[:up_to].dropna()
    if len(c) < 13: return 5.0
    return float((float(c.iloc[-2]) / float(c.iloc[-13]) - 1) * 100)


# ── Strategy selection ────────────────────────────────────────────────────────
def _apply_strategy(cands: list, sid: str, exchange: str, closes: dict, up_to: int) -> list:
    etf   = 'NIFTYBEES.NS' if exchange == 'NSE' else 'SPY'
    elite = [s for s in cands if s['score'] >= 81]

    if sid == 's1':
        return [] if _etf_12m(closes, etf, up_to) <= 0 else (elite or cands)[:15]

    if sid == 's2':
        sc, out = defaultdict(int), []
        for s in cands:
            if sc[s['sector']] < 3: out.append(s); sc[s['sector']] += 1
            if len(out) >= 20: break
        return out

    if sid == 's3':
        sc, out = defaultdict(int), []
        for s in cands:
            if sc[s['sector']] < 2: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's4':
        ranked = sorted(elite or cands, key=lambda x: x['h52_score']*.5 + x['score']*.5, reverse=True)
        sc, out = defaultdict(int), []
        for s in ranked:
            if sc[s['sector']] < 4: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's5':
        by_sec = defaultdict(list)
        for s in (elite or cands): by_sec[s['sector']].append(s)
        out = []
        for tier in range(2):
            for picks in by_sec.values():
                if len(out) >= 10: break
                if len(picks) > tier: out.append(picks[tier])
            if len(out) >= 10: break
        return sorted(out, key=lambda x: x['score'], reverse=True)[:10]

    if sid == 's6':
        sweet = [s for s in cands if 81 <= s['score'] <= 91] or cands[:10]
        sc, out = defaultdict(int), []
        for s in sweet:
            if sc[s['sector']] < 3: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's7':
        return (elite or cands)[:2]

    if sid == 's8':
        if _etf_12m(closes, etf, up_to) <= 0: return []
        buf = [s for s in cands if s['score'] >= 83] or elite
        by_sec = defaultdict(list)
        for s in (buf or cands): by_sec[s['sector']].append(s)
        out = []
        for tier in range(2):
            for picks in by_sec.values():
                if len(out) >= 10: break
                if len(picks) > tier: out.append(picks[tier])
            if len(out) >= 10: break
        return sorted(out, key=lambda x: x['score'], reverse=True)

    if sid == 's9':
        filt = [s for s in elite if s['macd_score'] >= 75 and s['rsi_score'] >= 65] \
               or [s for s in elite if s['score'] >= 85] or (elite or cands)[:8]
        for s in filt:
            s['ignition'] = s['macd_score']*.40 + s['rsi_score']*.25 \
                          + s['score']*.20 + s['h52_score']*.15
            if s['macd_score'] >= 80 and s['rsi_score'] >= 75 and s['h52_score'] >= 80:
                s['ignition'] += 5.0
        return sorted(filt, key=lambda x: x.get('ignition', 0), reverse=True)[:5]

    if sid == 's10':
        by_sec = defaultdict(list)
        for s in (elite or cands): by_sec[s['sector']].append(s)
        if not by_sec: return []
        def alpha(ss):
            top3 = sorted(ss, key=lambda x: x['score'], reverse=True)[:3]
            return sum(x['score'] for x in top3)/len(top3)*.70 \
                   + top3[0]['score']*.30 + min(len(ss),5)*.5
        best = max(by_sec, key=lambda sec: alpha(by_sec[sec]))
        return sorted(by_sec[best], key=lambda x: x['score'], reverse=True)[:3]

    return (elite or cands)[:10]


# ── Main backtest ─────────────────────────────────────────────────────────────
def run_strategy_backtest(exchange: str, sid: str, months: int = 12) -> dict:
    from strategy_builder import STRATEGIES

    universe   = NSE_UNIVERSE   if exchange == 'NSE' else NYSE_UNIVERSE
    sector_map = NSE_SECTOR_MAP if exchange == 'NSE' else NYSE_SECTOR_MAP

    set_progress(exchange, sid,
                 f'Downloading {len(universe)} {exchange} stocks in one batch…', 5)

    # ── Step 1: single bulk download ──────────────────────────────────────────
    raw = _bulk_download(universe, timeout_sec=120)

    if raw is None or (hasattr(raw, 'empty') and raw.empty):
        msg = 'Download timed out or returned empty. Yahoo Finance may be unavailable — try again in a minute.'
        set_progress(exchange, sid, f'❌ {msg}', 100)
        return {'error': msg}

    set_progress(exchange, sid, 'Parsing downloaded data…', 40)
    closes, opens, highs = _extract_series(raw, universe)

    n_tickers = len(closes)
    log.info(f'{exchange}: {n_tickers} tickers parsed successfully')
    set_progress(exchange, sid, f'Parsed {n_tickers} tickers. Running 12-month simulation…', 48)

    if n_tickers < 3:
        msg = (f'Only {n_tickers} ticker(s) returned valid data. '
               'Yahoo Finance may be blocking requests right now. '
               'Please wait 2-3 minutes and try again.')
        set_progress(exchange, sid, f'❌ {msg}', 100)
        return {'error': msg}

    # Use the time index from any valid ticker
    sample_series = next(iter(closes.values()))
    time_idx      = sample_series.index

    # ── Step 2: 12-month simulation ───────────────────────────────────────────
    now        = pd.Timestamp.now()
    last_month = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    targets    = [last_month - pd.DateOffset(months=i) for i in range(months - 1, -1, -1)]

    monthly_results  = []
    portfolio_values = [100.0]

    for step, target in enumerate(targets):
        pct = 48 + int(step / len(targets) * 48)
        set_progress(exchange, sid,
                     f'Simulating {target.strftime("%b %Y")} ({step+1}/{len(targets)})…', pct)

        # Locate this month in the data
        month_idx = next(
            (i for i, ts in enumerate(time_idx)
             if ts.year == target.year and ts.month == target.month), None)
        if month_idx is None:
            continue

        score_cutoff = month_idx   # data[:month_idx] = data up to previous month end

        # Score every stock
        records = []
        for ticker in closes:
            if ticker in (NSE_UNIVERSE[0], NYSE_UNIVERSE[0]):
                continue   # skip market ETF from picks

            c_sl = closes[ticker].iloc[:score_cutoff].dropna()
            h_sl = highs.get(ticker, closes[ticker]).iloc[:score_cutoff].dropna()
            if len(c_sl) < 6:
                continue

            score, macd_s, rsi_s, h52_s = _score_stock(c_sl, h_sl)

            try:
                ep = float(opens.get(ticker, closes[ticker]).iloc[month_idx])
                xp = float(closes[ticker].iloc[month_idx])
            except (IndexError, TypeError):
                continue
            if ep <= 0 or xp <= 0 or pd.isna(ep) or pd.isna(xp):
                continue

            records.append({
                'ticker':      ticker,
                'symbol':      ticker.replace('.NS','').replace('.BO',''),
                'sector':      sector_map.get(ticker, 'Other'),
                'score':       score,
                'macd_score':  macd_s,
                'rsi_score':   rsi_s,
                'h52_score':   h52_s,
                'entry_price': round(ep, 2),
                'exit_price':  round(xp, 2),
                'return_pct':  round((xp - ep) / ep * 100, 2),
            })

        records.sort(key=lambda x: x['score'], reverse=True)
        selected = _apply_strategy(records, sid, exchange, closes, score_cutoff)

        port_ret = round(sum(s['return_pct'] for s in selected) / len(selected), 2) \
                   if selected else 0.0
        new_val  = round(portfolio_values[-1] * (1 + port_ret / 100), 2)
        portfolio_values.append(new_val)

        sectors = defaultdict(int)
        for s in selected: sectors[s['sector']] += 1

        monthly_results.append({
            'month':                target.strftime('%b %Y'),
            'month_key':            target.strftime('%Y-%m'),
            'stocks':               selected,
            'portfolio_return_pct': port_ret,
            'portfolio_value':      new_val,
            'num_stocks':           len(selected),
            'sectors':              dict(sectors),
            'mode':                 'INVESTED' if selected else 'CASH',
            'best_stock':  max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'worst_stock': min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'best_return': round(max(s['return_pct'] for s in selected), 2) if selected else 0,
            'worst_return':round(min(s['return_pct'] for s in selected), 2) if selected else 0,
        })

    # ── Summary stats ─────────────────────────────────────────────────────────
    rets       = [m['portfolio_return_pct'] for m in monthly_results]
    n          = len(monthly_results)
    total_pct  = round((portfolio_values[-1] / 100 - 1) * 100, 2)
    annualised = round(((1 + total_pct/100)**(12/max(n,1)) - 1)*100, 1) if n else 0.0
    win_rate   = round(sum(1 for r in rets if r > 0) / max(len(rets),1) * 100, 1)

    peak = portfolio_values[0]; max_dd = 0.0
    for v in portfolio_values:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    sharpe = 0.0
    if len(rets) > 1:
        ar, sr = float(np.mean(rets)), float(np.std(rets, ddof=1))
        sharpe = round(ar / sr * 12**.5, 2) if sr > 0 else 0.0

    bmi = int(np.argmax(rets)) if rets else 0
    wmi = int(np.argmin(rets)) if rets else 0
    st  = STRATEGIES.get(sid, {})

    result = {
        'strategy_id':   sid,
        'strategy_name': st.get('name', sid.upper()),
        'strategy_icon': st.get('icon', '📊'),
        'exchange':      exchange,
        'generated_at':  datetime.now().strftime('%Y-%m-%d %H:%M'),
        'period': {
            'start':  monthly_results[0]['month']  if monthly_results else '',
            'end':    monthly_results[-1]['month'] if monthly_results else '',
            'months': n,
        },
        'monthly_results':  monthly_results,
        'portfolio_values': portfolio_values,
        'summary': {
            'total_return_pct':   total_pct,
            'annualized_return':  annualised,
            'win_rate':           win_rate,
            'max_drawdown':       round(max_dd, 2),
            'sharpe_ratio':       sharpe,
            'best_month':         monthly_results[bmi]['month']  if rets else '',
            'best_month_return':  max(rets) if rets else 0,
            'worst_month':        monthly_results[wmi]['month']  if rets else '',
            'worst_month_return': min(rets) if rets else 0,
            'total_trades':       sum(m['num_stocks'] for m in monthly_results),
            'universe_size':      n_tickers,
        },
    }

    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    with open(path, 'w') as f:
        json.dump(result, f, indent=2)

    set_progress(exchange, sid,
                 f'✅ Done — {total_pct:+.1f}% total | {annualised:+.0f}% annualised', 100)
    return result


def load_backtest(exchange: str, sid: str) -> dict | None:
    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    try:
        return json.load(open(path)) if os.path.exists(path) else None
    except Exception:
        return None


def run_all_backtests(exchange: str) -> dict:
    from strategy_builder import STRATEGIES
    return {sid: run_strategy_backtest(exchange, sid) for sid in STRATEGIES}
