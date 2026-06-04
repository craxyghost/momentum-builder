"""
Backtest Engine — 12-Month Historical Simulation (v2 — Railway-safe)
=====================================================================
Key fixes vs v1:
  • Universe shrunk to 25 stocks/exchange — avoids bulk-download timeouts
  • Each ticker fetched individually with a 20-second thread timeout
  • Progress written to a JSON FILE (not in-memory) — works across workers
  • threads=False in yfinance — prevents internal deadlock on Railway
  • Gracefully skips any ticker that fails or times out
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

# ── Lean universe: only the most liquid 25 stocks per exchange ─────────────────
# Small enough to complete in < 2 minutes; large enough for meaningful results.

NSE_UNIVERSE = [
    'NIFTYBEES.NS',        # Market ETF — needed for S1/S8 market-regime check
    # Nifty 50 mega-caps across all major sectors
    'RELIANCE.NS',         # Energy / Conglomerate
    'TCS.NS',              # IT
    'HDFCBANK.NS',         # Banking
    'INFY.NS',             # IT
    'ICICIBANK.NS',        # Banking
    'HINDUNILVR.NS',       # FMCG
    'SBIN.NS',             # Banking
    'BAJFINANCE.NS',       # NBFC
    'BHARTIARTL.NS',       # Telecom
    'KOTAKBANK.NS',        # Banking
    'ASIANPAINT.NS',       # Paints
    'MARUTI.NS',           # Auto
    'TITAN.NS',            # Consumer
    'SUNPHARMA.NS',        # Pharma
    'WIPRO.NS',            # IT
    'TATAMOTORS.NS',       # Auto
    'TATASTEEL.NS',        # Metals
    'POWERGRID.NS',        # Power
    'NTPC.NS',             # Power
    'ONGC.NS',             # Energy
    'ITC.NS',              # FMCG
    'ADANIPORTS.NS',       # Infra
    'DRREDDY.NS',          # Pharma
    'HCLTECH.NS',          # IT
]

NYSE_UNIVERSE = [
    'SPY',                 # Market ETF — needed for S1/S8 market-regime check
    # S&P 500 mega-caps across all major sectors
    'AAPL',                # Technology
    'MSFT',                # Technology
    'GOOGL',               # Technology
    'AMZN',                # E-Commerce
    'NVDA',                # Semiconductors
    'META',                # Social Media
    'TSLA',                # EV/Auto
    'JPM',                 # Banking
    'JNJ',                 # Healthcare
    'V',                   # Payments
    'PG',                  # Consumer
    'UNH',                 # Healthcare
    'MA',                  # Payments
    'BAC',                 # Banking
    'ADBE',                # Software
    'NFLX',                # Streaming
    'AMD',                 # Semiconductors
    'AVGO',                # Semiconductors
    'COST',                # Retail
    'MRK',                 # Pharma
    'LLY',                 # Pharma
    'XOM',                 # Energy
    'CVX',                 # Energy
    'CRWD',                # Cybersecurity
]

NSE_SECTOR_MAP = {
    'NIFTYBEES.NS': 'ETF',   'RELIANCE.NS': 'Energy',   'TCS.NS': 'IT',
    'HDFCBANK.NS': 'Banking', 'INFY.NS': 'IT',           'ICICIBANK.NS': 'Banking',
    'HINDUNILVR.NS': 'FMCG', 'SBIN.NS': 'Banking',      'BAJFINANCE.NS': 'NBFC',
    'BHARTIARTL.NS': 'Telecom','KOTAKBANK.NS': 'Banking', 'ASIANPAINT.NS': 'Paints',
    'MARUTI.NS': 'Auto',      'TITAN.NS': 'Consumer',    'SUNPHARMA.NS': 'Pharma',
    'WIPRO.NS': 'IT',         'TATAMOTORS.NS': 'Auto',   'TATASTEEL.NS': 'Metals',
    'POWERGRID.NS': 'Power',  'NTPC.NS': 'Power',        'ONGC.NS': 'Energy',
    'ITC.NS': 'FMCG',         'ADANIPORTS.NS': 'Infra',  'DRREDDY.NS': 'Pharma',
    'HCLTECH.NS': 'IT',
}

NYSE_SECTOR_MAP = {
    'SPY': 'ETF',        'AAPL': 'Technology',    'MSFT': 'Technology',
    'GOOGL': 'Technology','AMZN': 'E-Commerce',   'NVDA': 'Semiconductors',
    'META': 'Social Media','TSLA': 'EV/Auto',     'JPM': 'Banking',
    'JNJ': 'Healthcare', 'V': 'Payments',          'PG': 'Consumer',
    'UNH': 'Healthcare', 'MA': 'Payments',         'BAC': 'Banking',
    'ADBE': 'Software',  'NFLX': 'Streaming',      'AMD': 'Semiconductors',
    'AVGO': 'Semiconductors','COST': 'Retail',     'MRK': 'Pharma',
    'LLY': 'Pharma',     'XOM': 'Energy',          'CVX': 'Energy',
    'CRWD': 'Cybersecurity',
}


# ── File-based progress (works across Railway workers) ────────────────────────

def _prog_path(exchange: str, sid: str) -> str:
    return os.path.join(DATA_DIR, f'bt_prog_{exchange.lower()}_{sid}.json')

def set_progress(exchange: str, sid: str, msg: str, pct: int = 0):
    try:
        with open(_prog_path(exchange, sid), 'w') as f:
            json.dump({'message': msg, 'pct': pct, 'ts': datetime.now().isoformat()}, f)
    except Exception:
        pass
    log.info(f'[{exchange}/{sid}] {pct}% — {msg}')

def get_progress(exchange: str, sid: str) -> dict:
    try:
        p = _prog_path(exchange, sid)
        if os.path.exists(p):
            return json.load(open(p))
    except Exception:
        pass
    return {'message': 'Idle', 'pct': 0}


# ── Per-ticker fetch with hard timeout ────────────────────────────────────────

def _fetch_ticker(ticker: str, timeout_sec: int = 20) -> pd.DataFrame | None:
    """
    Fetch 2 years of monthly OHLCV for a single ticker.
    Returns None if the fetch times out or fails.
    """
    result = [None]

    def _do():
        try:
            hist = yf.Ticker(ticker).history(
                period='2y', interval='1mo',
                auto_adjust=True, actions=False
            )
            if hist is not None and not hist.empty:
                result[0] = hist
        except Exception as e:
            log.warning(f'Fetch failed for {ticker}: {e}')

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        log.warning(f'Timeout fetching {ticker} — skipping')
        return None
    return result[0]


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta  = series.diff()
    up     = delta.clip(lower=0)
    dn     = (-delta).clip(lower=0)
    avg_up = up.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    avg_dn = dn.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    rs     = avg_up / avg_dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _score_stock(closes: pd.Series, highs: pd.Series) -> tuple[float, float, float, float]:
    """
    Returns (final_score, macd_score, rsi_score, h52_score).
    Identical to the live screener formula — no look-ahead bias.
    """
    c = closes.dropna()
    h = highs.dropna()
    n = len(c)
    if n < 6:
        return 0.0, 50.0, 50.0, 50.0

    # 1. 12-1M Price Momentum → 0-100
    if n >= 13:
        ret_12m = (float(c.iloc[-2]) / float(c.iloc[-13]) - 1) * 100
    elif n >= 2:
        ret_12m = (float(c.iloc[-2]) / float(c.iloc[0])  - 1) * 100
    else:
        ret_12m = 0.0
    mom_score = min(100.0, max(0.0, (ret_12m + 30) / 70 * 100))

    # 2. Monthly RSI-14
    rsi_vals  = _rsi(c, 14)
    rsi_score = float(rsi_vals.iloc[-1]) if not pd.isna(rsi_vals.iloc[-1]) else 50.0

    # 3. MACD 12-26-9
    ema12   = c.ewm(span=12, adjust=False).mean()
    ema26   = c.ewm(span=26, adjust=False).mean()
    macd    = ema12 - ema26
    signal  = macd.ewm(span=9, adjust=False).mean()
    m_now   = float(macd.iloc[-1])
    s_now   = float(signal.iloc[-1])
    m_prev  = float(macd.iloc[-2]) if n >= 2 else m_now
    if m_now > s_now:
        macd_score = 90.0 if m_now > m_prev else 70.0   # bullish + accel / bullish
    else:
        macd_score = 20.0 if m_now < m_prev else 40.0   # bearish + falling / bearish

    # 4. 52-Week High Ratio
    h_sl  = h.iloc[-13:] if len(h) >= 13 else h
    h52   = float(h_sl.max()) if len(h_sl) else float(c.max())
    cur   = float(c.iloc[-1])
    h52_score = min(100.0, cur / h52 * 100) if h52 > 0 else 50.0

    # 5. 6M vs 12M SMA Cross → 0-100
    s6    = float(c.rolling(6, min_periods=1).mean().iloc[-1])
    s12   = float(c.rolling(12, min_periods=1).mean().iloc[-1])
    spread = (s6 - s12) / s12 * 100 if s12 > 0 else 0.0
    ma_score = min(100.0, max(0.0, (spread + 20) / 40 * 100))

    final = (mom_score * 0.30 + rsi_score * 0.20 + macd_score * 0.20
             + h52_score * 0.15 + ma_score * 0.15)
    return round(final, 1), round(macd_score, 1), round(rsi_score, 1), round(h52_score, 1)


def _etf_12m(closes_dict: dict, etf: str, up_to: int) -> float:
    if etf not in closes_dict:
        return 5.0
    c = closes_dict[etf].iloc[:up_to].dropna()
    if len(c) < 13:
        return 5.0
    return float((float(c.iloc[-2]) / float(c.iloc[-13]) - 1) * 100)


# ── Strategy selection logic ──────────────────────────────────────────────────

def _apply_strategy(candidates: list, sid: str, exchange: str,
                    closes_dict: dict, up_to: int) -> list:
    """Apply strategy selection rules. `candidates` is sorted by score DESC."""
    etf = 'NIFTYBEES.NS' if exchange == 'NSE' else 'SPY'
    elite = [s for s in candidates if s['score'] >= 81]

    if sid == 's1':
        mkt = _etf_12m(closes_dict, etf, up_to)
        return [] if mkt <= 0 else (elite or candidates)[:15]

    if sid == 's2':
        sc, out = defaultdict(int), []
        for s in candidates:
            if sc[s['sector']] < 3: out.append(s); sc[s['sector']] += 1
            if len(out) >= 20: break
        return out

    if sid == 's3':
        sc, out = defaultdict(int), []
        for s in candidates:
            if sc[s['sector']] < 2: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's4':
        ranked = sorted(elite or candidates,
                        key=lambda x: x['h52_score'] * 0.5 + x['score'] * 0.5,
                        reverse=True)
        sc, out = defaultdict(int), []
        for s in ranked:
            if sc[s['sector']] < 4: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's5':
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
        sweet = [s for s in candidates if 81 <= s['score'] <= 91]
        if not sweet: sweet = candidates[:10]
        sc, out = defaultdict(int), []
        for s in sweet:
            if sc[s['sector']] < 3: out.append(s); sc[s['sector']] += 1
            if len(out) >= 15: break
        return out

    if sid == 's7':
        return (elite or candidates)[:2]

    if sid == 's8':
        mkt = _etf_12m(closes_dict, etf, up_to)
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
        filt = [s for s in elite if s['macd_score'] >= 75 and s['rsi_score'] >= 65]
        if len(filt) < 2: filt = [s for s in elite if s['score'] >= 85]
        if not filt:      filt = (elite or candidates)[:8]
        for s in filt:
            s['ignition'] = (s['macd_score'] * 0.40 + s['rsi_score'] * 0.25
                             + s['score'] * 0.20 + s['h52_score'] * 0.15)
            if s['macd_score'] >= 80 and s['rsi_score'] >= 75 and s['h52_score'] >= 80:
                s['ignition'] += 5.0
        return sorted(filt, key=lambda x: x.get('ignition', 0), reverse=True)[:5]

    if sid == 's10':
        by_sec = defaultdict(list)
        for s in (elite or candidates): by_sec[s['sector']].append(s)
        if not by_sec: return []
        alphas = {sec: (sum(x['score'] for x in sorted(ss, key=lambda x: x['score'], reverse=True)[:3])
                        / min(len(ss), 3) * 0.70
                        + sorted(ss, key=lambda x: x['score'], reverse=True)[0]['score'] * 0.30
                        + min(len(ss), 5) * 0.5)
                  for sec, ss in by_sec.items()}
        best = max(alphas, key=alphas.get)
        return sorted(by_sec[best], key=lambda x: x['score'], reverse=True)[:3]

    return (elite or candidates)[:10]


# ── Main backtest ─────────────────────────────────────────────────────────────

def run_strategy_backtest(exchange: str, sid: str, months: int = 12) -> dict:
    """
    Simulate a strategy over the past 12 months using real historical data.
    Writes result to data/backtest_<exchange>_<sid>.json.
    """
    from strategy_builder import STRATEGIES

    universe   = NSE_UNIVERSE   if exchange == 'NSE' else NYSE_UNIVERSE
    sector_map = NSE_SECTOR_MAP if exchange == 'NSE' else NYSE_SECTOR_MAP
    total_tix  = len(universe)

    set_progress(exchange, sid, f'Downloading data for {total_tix} stocks…', 3)

    # ── Step 1: fetch each ticker individually (timeout-safe) ─────────────────
    closes_dict: dict[str, pd.Series] = {}
    opens_dict:  dict[str, pd.Series] = {}
    highs_dict:  dict[str, pd.Series] = {}

    for i, ticker in enumerate(universe):
        pct = 3 + int(i / total_tix * 45)
        set_progress(exchange, sid,
                     f'Downloading {ticker} ({i+1}/{total_tix})…', pct)

        hist = _fetch_ticker(ticker, timeout_sec=20)
        if hist is None or hist.empty:
            log.warning(f'No data for {ticker} — skipping')
            continue
        if len(hist) < 15:
            log.warning(f'{ticker} has only {len(hist)} months — skipping')
            continue

        closes_dict[ticker] = hist['Close']
        opens_dict[ticker]  = hist['Open']
        highs_dict[ticker]  = hist['High']

    if len(closes_dict) < 5:
        err = f'Too few tickers with data ({len(closes_dict)}). Yahoo Finance may be throttling.'
        set_progress(exchange, sid, f'❌ {err}', 100)
        return {'error': err}

    log.info(f'{exchange}: {len(closes_dict)} tickers fetched successfully')

    # Build a shared time index from the first valid ticker
    sample   = next(iter(closes_dict.values()))
    time_idx = sample.index

    set_progress(exchange, sid, 'Running month-by-month simulation…', 50)

    # ── Step 2: 12-month backtest window ──────────────────────────────────────
    now        = pd.Timestamp.now()
    last_month = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    targets    = [last_month - pd.DateOffset(months=i) for i in range(months - 1, -1, -1)]

    monthly_results = []
    portfolio_values = [100.0]

    for step, target in enumerate(targets):
        pct = 50 + int(step / len(targets) * 45)
        set_progress(exchange, sid,
                     f'Simulating {target.strftime("%b %Y")}… ({step+1}/{len(targets)})', pct)

        # Find this month's position in the shared index
        month_idx = None
        for i, ts in enumerate(time_idx):
            if ts.year == target.year and ts.month == target.month:
                month_idx = i
                break
        if month_idx is None:
            log.warning(f'No data row for {target.strftime("%b %Y")} — skipping')
            continue

        score_cutoff = month_idx  # data[:month_idx] = data up to previous month-end

        # ── Score every stock ────────────────────────────────────────────────
        records = []
        for ticker in closes_dict:
            if ticker in (NSE_UNIVERSE[0], NYSE_UNIVERSE[0]):
                continue  # skip ETF from stock picks

            c_sl = closes_dict[ticker].iloc[:score_cutoff].dropna()
            h_sl = highs_dict.get(ticker, closes_dict[ticker]).iloc[:score_cutoff].dropna()

            if len(c_sl) < 6:
                continue

            score, macd_s, rsi_s, h52_s = _score_stock(c_sl, h_sl)

            # Get this month's entry (Open) and exit (Close)
            try:
                entry_p = float(opens_dict[ticker].iloc[month_idx])
                exit_p  = float(closes_dict[ticker].iloc[month_idx])
            except (IndexError, KeyError, TypeError):
                continue
            if not entry_p or not exit_p or pd.isna(entry_p) or pd.isna(exit_p):
                continue
            if entry_p <= 0 or exit_p <= 0:
                continue

            ret = round((exit_p - entry_p) / entry_p * 100, 2)
            sym = ticker.replace('.NS', '').replace('.BO', '')

            records.append({
                'ticker':      ticker,
                'symbol':      sym,
                'sector':      sector_map.get(ticker, 'Other'),
                'score':       score,
                'macd_score':  macd_s,
                'rsi_score':   rsi_s,
                'h52_score':   h52_s,
                'entry_price': round(entry_p, 2),
                'exit_price':  round(exit_p,  2),
                'return_pct':  ret,
            })

        records.sort(key=lambda x: x['score'], reverse=True)

        # ── Apply strategy ───────────────────────────────────────────────────
        selected = _apply_strategy(records, sid, exchange, closes_dict, score_cutoff)

        if selected:
            port_ret = round(sum(s['return_pct'] for s in selected) / len(selected), 2)
            mode = 'INVESTED'
        else:
            port_ret = 0.0
            mode     = 'CASH'

        new_val = round(portfolio_values[-1] * (1 + port_ret / 100), 2)
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
            'mode':                 mode,
            'best_stock':  max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'worst_stock': min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'best_return': round(max(s['return_pct'] for s in selected), 2) if selected else 0,
            'worst_return':round(min(s['return_pct'] for s in selected), 2) if selected else 0,
        })

    set_progress(exchange, sid, 'Computing summary statistics…', 97)

    # ── Summary stats ─────────────────────────────────────────────────────────
    rets = [m['portfolio_return_pct'] for m in monthly_results]
    n    = len(monthly_results)
    total_pct  = round((portfolio_values[-1] / 100 - 1) * 100, 2)
    annualised = round(((1 + total_pct / 100) ** (12 / max(n, 1)) - 1) * 100, 1) if n else 0.0
    win_rate   = round(sum(1 for r in rets if r > 0) / max(len(rets), 1) * 100, 1)

    peak = portfolio_values[0]; max_dd = 0.0
    for v in portfolio_values:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    sharpe = 0.0
    if len(rets) > 1:
        avg_r = float(np.mean(rets)); std_r = float(np.std(rets, ddof=1))
        sharpe = round((avg_r / std_r) * (12 ** 0.5), 2) if std_r > 0 else 0.0

    bm_idx = int(np.argmax(rets)) if rets else 0
    wm_idx = int(np.argmin(rets)) if rets else 0
    strat  = STRATEGIES.get(sid, {})

    result = {
        'strategy_id':   sid,
        'strategy_name': strat.get('name', sid.upper()),
        'strategy_icon': strat.get('icon', '📊'),
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
            'best_month':         monthly_results[bm_idx]['month']  if rets else '',
            'best_month_return':  max(rets) if rets else 0,
            'worst_month':        monthly_results[wm_idx]['month']  if rets else '',
            'worst_month_return': min(rets) if rets else 0,
            'total_trades':       sum(m['num_stocks'] for m in monthly_results),
            'universe_size':      len(closes_dict),
        },
    }

    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    with open(path, 'w') as f:
        json.dump(result, f, indent=2)

    set_progress(exchange, sid,
                 f'Done ✅ — {total_pct:+.1f}% total / {annualised:+.0f}% annualised', 100)
    return result


def load_backtest(exchange: str, sid: str) -> dict | None:
    path = os.path.join(DATA_DIR, f'backtest_{exchange.lower()}_{sid}.json')
    try:
        if os.path.exists(path):
            return json.load(open(path))
    except Exception:
        pass
    return None


def run_all_backtests(exchange: str) -> dict:
    from strategy_builder import STRATEGIES
    return {sid: run_strategy_backtest(exchange, sid) for sid in STRATEGIES}
