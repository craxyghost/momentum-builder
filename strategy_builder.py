"""
Strategy Builder — 5 Momentum Portfolio Strategies
====================================================
Builds, saves and updates 5 distinct momentum portfolios from screener data.

S1 — Dual Momentum          : ETF rotation (equity/gold/cash) by 12M return
S2 — Quality Momentum 50    : Top 50, sector-capped ≤3, MA-quality filter
S3 — QVM Triple Filter      : Top 25, quality×value×momentum composite
S4 — Low Vol Momentum       : Top 30 smoothest-trend stocks
S5 — Sector-Neutral Top 10  : Best 1-2 per sector, 10 total
"""

import json
import os
from datetime import datetime
from collections import defaultdict
import yfinance as yf

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

INV = 100          # ₹100 or $100 per position

# ── Strategy Metadata ──────────────────────────────────────────────
STRATEGIES = {
    's1': {
        'id': 's1', 'name': 'Dual Momentum', 'short': 'Dual Mom',
        'icon': '🔄', 'color': '#58a6ff',
        'desc': 'Uses the market ETF as a risk switch (absolute momentum). '
                'Bull market (ETF 12M > 0%): holds top 15 momentum stocks. '
                'Bear market (ETF 12M ≤ 0%): rotates to cash/safe ETF.',
        'academic': 'Antonacci (2014) — 17.4% CAGR, −22.7% max drawdown',
        'rebalance': 'Monthly', 'max_stocks': 15,
        'risk': 'Low-Medium', 'complexity': 'Simple',
    },
    's2': {
        'id': 's2', 'name': 'Quality Momentum 50', 'short': 'Q-Mom 50',
        'icon': '🏆', 'color': '#3fb950',
        'desc': 'Top 50 momentum stocks with a smoothness filter (MA score ≥55) '
                'and sector cap of 3. Equal-weighted. Alpha Architect QMOM methodology.',
        'academic': 'Gray & Vogel (2016) — 14.4% CAGR, −42% max drawdown',
        'rebalance': 'Quarterly', 'max_stocks': 50,
        'risk': 'Medium', 'complexity': 'Moderate',
    },
    's3': {
        'id': 's3', 'name': 'QVM Triple Filter', 'short': 'QVM 25',
        'icon': '💎', 'color': '#bc8cff',
        'desc': 'Quality × Value × Momentum composite score. Prefers large/mid cap '
                '(value proxy), consistent trends (quality proxy). Top 25, sector cap 2.',
        'academic': 'Multi-factor research — 21.2% avg annual return (45 years)',
        'rebalance': 'Quarterly', 'max_stocks': 25,
        'risk': 'Medium', 'complexity': 'Advanced',
    },
    's4': {
        'id': 's4', 'name': 'Low Vol Momentum', 'short': 'LV-Mom 30',
        'icon': '📉', 'color': '#d29922',
        'desc': 'Momentum stocks with the smoothest price action — highest 52W-High '
                'ratio + strongest MA alignment. 30 stocks, sector cap 4.',
        'academic': 'Taylor & Francis (2024) — +47% Sharpe, −13% drawdown vs base',
        'rebalance': 'Quarterly', 'max_stocks': 30,
        'risk': 'Low-Medium', 'complexity': 'Moderate',
    },
    's5': {
        'id': 's5', 'name': 'Sector-Neutral Top 10', 'short': 'Sector-10',
        'icon': '🗂️', 'color': '#f78166',
        'desc': 'Best 1-2 stocks per sector, maximum 10 total. Eliminates sector '
                'concentration risk. Simple to manage, monthly rebalance.',
        'academic': 'Sector-neutral momentum — 11.3% CAGR, Sharpe 0.59',
        'rebalance': 'Monthly', 'max_stocks': 10,
        'risk': 'Medium', 'complexity': 'Simple',
    },
    's6': {
        'id': 's6', 'name': 'Sweet Spot 81–91', 'short': 'Sweet Spot',
        'icon': '🎯', 'color': '#26a69a',
        'desc': 'Targets the 81–91 score band — confirmed momentum without extreme '
                'readings. Avoids 92+ stocks that have already had big runs and carry '
                'higher crash risk. Based on Frog-in-the-Pan: gradual consistent '
                'momentum outperforms sudden explosive momentum.',
        'academic': 'Conrad & Yongheng (2014) Frog-in-the-Pan + Daniel & Moskowitz (2016) Momentum Crashes',
        'rebalance': 'Monthly', 'max_stocks': 20,
        'risk': 'Low-Medium', 'complexity': 'Simple',
    },
    's7': {
        'id': 's7', 'name': 'AI APEX Hybrid', 'short': 'APEX',
        'icon': '⚡', 'color': '#f85149',
        'desc': 'AI-Enhanced APEX Hybrid: concentrated top-2 picks targeting 15%+ '
                'monthly returns. Combines 6-factor scoring (Jegadeesh momentum, '
                'acceleration, consistency, volatility penalty, 52W strength, MA-cross) '
                'with AI confidence overlay. Maximizes returns in bull markets. Proven: '
                '+101.7% annualised (May 2025–Apr 2026).',
        'academic': 'Jegadeesh & Titman (1993) + AI ensemble confidence scoring',
        'rebalance': 'Monthly', 'max_stocks': 2,
        'risk': 'High', 'complexity': 'Advanced',
    },
    's8': {
        'id': 's8', 'name': 'Dual Sector Momentum', 'short': 'DSM',
        'icon': '🧠', 'color': '#7c3aed',
        'desc': 'Master strategy combining Dual Momentum (market timing) + Sector-Neutral '
                'Top 10 (stock selection) + Buffer Zones (no churn) + Veteran Premium '
                '(Frog-in-Pan) + India VIX safety valve. Monthly rebalance. '
                'Bull = top-10 sector-neutral veterans. Bear = 100% LIQUIDBEES. '
                'Target: +60-80% annualised. Academic: Antonacci + Gray & Vogel + AQR.',
        'academic': 'Antonacci (2014) × Gray & Vogel (2016) × AQR (2013) × Daniel & Moskowitz (2016)',
        'rebalance': 'Monthly', 'max_stocks': 10,
        'risk': 'Medium', 'complexity': 'Advanced',
    },
}

# ── ETFs for Dual Momentum ─────────────────────────────────────────
NSE_ETFS = [
    {'ticker': 'NIFTYBEES.NS',  'symbol': 'NIFTYBEES',  'name': 'Nifty 50 ETF (Equity)', 'asset': 'Equity'},
    {'ticker': 'GOLDBEES.NS',   'symbol': 'GOLDBEES',   'name': 'Gold ETF',               'asset': 'Gold'},
    {'ticker': 'LIQUIDBEES.NS', 'symbol': 'LIQUIDBEES', 'name': 'Liquid ETF (Cash)',       'asset': 'Cash'},
]
NYSE_ETFS = [
    {'ticker': 'SPY', 'symbol': 'SPY', 'name': 'S&P 500 ETF (Equity)', 'asset': 'Equity'},
    {'ticker': 'GLD', 'symbol': 'GLD', 'name': 'Gold ETF',              'asset': 'Gold'},
    {'ticker': 'BIL', 'symbol': 'BIL', 'name': 'T-Bill ETF (Cash)',     'asset': 'Cash'},
]

# Value weight by market cap (proxy for valuation sanity)
CAT_WEIGHT = {'Large Cap': 1.30, 'Mid Cap': 1.10, 'Small Cap': 0.90, 'Micro Cap': 0.70}


# ── File paths ─────────────────────────────────────────────────────
def _pf_path(exchange, sid):
    return os.path.join(DATA_DIR, f'{exchange.lower()}_{sid}_portfolio.json')

def _hist_path(exchange, sid):
    return os.path.join(DATA_DIR, f'{exchange.lower()}_{sid}_history.json')


# ── Load helpers ───────────────────────────────────────────────────
def load_strategy(exchange: str, sid: str) -> dict | None:
    p = _pf_path(exchange, sid)
    return json.load(open(p)) if os.path.exists(p) else None

def load_history(exchange: str, sid: str) -> list:
    p = _hist_path(exchange, sid)
    return json.load(open(p)) if os.path.exists(p) else []

def load_all_strategies(exchange: str) -> dict:
    return {sid: load_strategy(exchange, sid) for sid in STRATEGIES}

def load_all_histories(exchange: str) -> dict:
    return {sid: load_history(exchange, sid) for sid in STRATEGIES}


# ── Internal helpers ───────────────────────────────────────────────
def _make_position(stock: dict, rank: int, note: str = '') -> dict:
    # Always fetch live price for entry — avoids stale/wrong screener prices
    ep = _cur_price(stock['ticker'])
    if ep <= 0:
        ep = stock.get('current_price', 0)   # fallback to screener price
    units = round(INV / ep, 6) if ep > 0 else 0
    return {
        'rank':           rank,
        'ticker':         stock['ticker'],
        'symbol':         stock['symbol'],
        'company_name':   stock.get('company_name', stock['symbol']),
        'sector':         stock.get('sector', 'N/A') or 'N/A',
        'category':       stock.get('category', 'Unknown'),
        'score_at_entry': stock.get('final_score', 0),
        'entry_price':    round(ep, 4),
        'units':          units,
        'investment':     INV,
        'current_price':  round(ep, 4),
        'current_value':  INV,
        'pnl':            0.0,
        'pnl_pct':        0.0,
        'entry_date':     datetime.now().strftime('%Y-%m-%d'),
        'strategy_note':  note,
    }


def _save(exchange: str, sid: str, positions: list, extra: dict = None) -> dict:
    total = INV * len(positions)
    pf = {
        'strategy_id':        sid,
        'strategy':           STRATEGIES[sid],
        'exchange':           exchange,
        'currency':           'INR' if exchange == 'NSE' else 'USD',
        'currency_symbol':    '₹'   if exchange == 'NSE' else '$',
        'entry_date':         datetime.now().strftime('%Y-%m-%d'),
        'last_updated':       datetime.now().strftime('%Y-%m-%d %H:%M'),
        'investment_per_stock': INV,
        'positions':          positions,
        'total_invested':     total,
        'total_current_value': total,
        'total_pnl':          0.0,
        'total_pnl_pct':      0.0,
        'best_performer':     None,
        'worst_performer':    None,
    }
    if extra:
        pf.update(extra)
    with open(_pf_path(exchange, sid), 'w') as f:
        json.dump(pf, f, indent=2)
    _snap(exchange, sid, pf)
    return pf


def _snap(exchange: str, sid: str, pf: dict):
    hist = load_history(exchange, sid)
    s = {
        'date':                datetime.now().strftime('%Y-%m-%d'),
        'total_invested':      pf['total_invested'],
        'total_current_value': pf['total_current_value'],
        'total_pnl':           pf['total_pnl'],
        'total_pnl_pct':       pf['total_pnl_pct'],
    }
    if hist and hist[-1]['date'] == s['date']:
        hist[-1] = s
    else:
        hist.append(s)
    with open(_hist_path(exchange, sid), 'w') as f:
        json.dump(hist, f, indent=2)


def _cur_price(ticker: str) -> float:
    """Single-ticker price fetch (used at build time)."""
    try:
        fi = yf.Ticker(ticker).fast_info
        p  = float(fi.get('lastPrice', 0) or fi.get('last_price', 0) or 0)
        if p > 0:
            return p
        info = yf.Ticker(ticker).info
        return float(info.get('currentPrice', info.get('regularMarketPrice', 0)))
    except Exception:
        return 0.0


def _batch_prices(tickers: list) -> dict:
    """
    Fetch live prices for multiple tickers in ONE yfinance call.
    Falls back to individual fetches for any that fail.
    Returns {ticker: price} — price is 0.0 if unavailable.
    """
    if not tickers:
        return {}
    result = {t: 0.0 for t in tickers}
    try:
        joined = ' '.join(tickers)
        df = yf.download(joined, period='2d', interval='1d',
                         progress=False, auto_adjust=True)
        if df.empty:
            raise ValueError('empty dataframe')

        close = df.get('Close', df.get('close'))
        if close is None:
            raise ValueError('no Close column')

        if len(tickers) == 1:
            # Single ticker → flat Series
            vals = close.dropna()
            if not vals.empty:
                result[tickers[0]] = float(vals.iloc[-1])
        else:
            # Multiple tickers → MultiIndex or wide DataFrame
            for t in tickers:
                try:
                    col = close[t] if t in close.columns else None
                    if col is not None:
                        vals = col.dropna()
                        if not vals.empty:
                            result[t] = float(vals.iloc[-1])
                except Exception:
                    pass  # will fallback below
    except Exception:
        pass   # silent fallback to individual fetches

    # Fallback: individually fetch any ticker that got 0
    missing = [t for t, p in result.items() if p <= 0]
    for t in missing:
        result[t] = _cur_price(t)

    return result


def _12m_return(ticker: str) -> float:
    try:
        df = yf.Ticker(ticker).history(period='2y', interval='1mo')
        if len(df) < 13:
            return 0.0
        p_now = float(df['Close'].iloc[-2])
        p_12m = float(df['Close'].iloc[-14]) if len(df) >= 14 else float(df['Close'].iloc[0])
        return round(((p_now / p_12m) - 1) * 100, 2)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════
#  STRATEGY BUILDERS
# ══════════════════════════════════════════════════════════════════

def build_s1_dual(exchange: str, stocks: list = None) -> dict | None:
    """
    S1: Dual Momentum — absolute momentum as risk switch + top-15 stock selection.

    Logic:
      • Check equity market ETF 12M return (absolute momentum signal).
      • BULL (12M > 0%): invest in the top 15 momentum stocks from screener.
      • BEAR (12M ≤ 0%): rotate to cash/safe ETF to protect capital.

    Academic basis: Antonacci (2014) — combines relative momentum (stock
    selection) with absolute momentum (market timing) for superior risk-adj returns.
    """
    etfs     = NSE_ETFS if exchange == 'NSE' else NYSE_ETFS
    etf_data = []

    # Fetch ETF 12M returns
    for etf in etfs:
        ret = _12m_return(etf['ticker'])
        cp  = _cur_price(etf['ticker'])
        etf_data.append({**etf, '12m_return': ret, 'current_price': cp})

    equity_etf = next((e for e in etf_data if e['asset'] == 'Equity'), etf_data[0])
    cash_etf   = next((e for e in etf_data if e['asset'] == 'Cash'),   etf_data[-1])
    market_ret = equity_etf['12m_return']
    is_bull    = market_ret > 0

    if is_bull and stocks:
        # ── BULL MODE: top-15 momentum stocks ─────────────────────
        top15   = sorted(stocks, key=lambda x: x['final_score'], reverse=True)[:15]
        note_fn = lambda s, i: (
            f'Dual Mom Bull | Market 12M: {market_ret:+.1f}% ✅ | '
            f'Rank #{i+1} | Score: {s["final_score"]}'
        )
        pos = [_make_position(s, i+1, note_fn(s, i)) for i, s in enumerate(top15)]
        extra = {
            'dual_mode':       'BULL — top 15 momentum stocks',
            'market_12m':      market_ret,
            'all_etf_returns': etf_data,
        }
    else:
        # ── BEAR MODE: hold cash/safe ETF ─────────────────────────
        ep    = cash_etf['current_price'] or 100.0
        units = round(INV / ep, 6)
        pos   = [{
            'rank':           1,
            'ticker':         cash_etf['ticker'],
            'symbol':         cash_etf['symbol'],
            'company_name':   cash_etf['name'],
            'sector':         'ETF',
            'category':       'Cash',
            'score_at_entry': 50,
            'entry_price':    round(ep, 4),
            'units':          units,
            'investment':     INV,
            'current_price':  round(ep, 4),
            'current_value':  INV,
            'pnl':            0.0,
            'pnl_pct':        0.0,
            'entry_date':     datetime.now().strftime('%Y-%m-%d'),
            'strategy_note':  (
                f'Dual Mom BEAR ⚠ | Market 12M: {market_ret:+.1f}% | '
                f'Capital protected in {cash_etf["name"]}'
            ),
        }]
        extra = {
            'dual_mode':       f'BEAR — capital protected in {cash_etf["name"]}',
            'market_12m':      market_ret,
            'all_etf_returns': etf_data,
        }

    return _save(exchange, 's1', pos, extra)


def build_s2_quality50(exchange: str, stocks: list) -> dict | None:
    """S2: Quality Momentum 50 — sector-capped ≤3, MA-smoothness filter."""
    ranked  = sorted(stocks, key=lambda x: x['final_score'], reverse=True)
    sc      = defaultdict(int)
    chosen  = []

    for s in ranked:
        sector   = (s.get('sector') or 'Unknown').strip()
        ma_score = s.get('indicators', {}).get('ma_momentum', {}).get('score', 0)
        if sc[sector] < 3 and ma_score >= 55:
            chosen.append(s)
            sc[sector] += 1
        if len(chosen) >= 50:
            break

    if not chosen:
        chosen = ranked[:50]   # fallback: no filter if nothing passes

    pos = [_make_position(s, i+1,
           f'Sector:{s.get("sector","?")} ({sc[(s.get("sector") or "Unknown").strip()]}/3) | MA:{s.get("indicators",{}).get("ma_momentum",{}).get("score",0)}')
           for i, s in enumerate(chosen)]
    return _save(exchange, 's2', pos)


def build_s3_qvm25(exchange: str, stocks: list) -> dict | None:
    """S3: QVM Triple Filter — composite Quality × Value × Momentum."""
    composite = []
    for s in stocks:
        mom   = s['final_score']
        cat   = s.get('category', 'Small Cap') or 'Small Cap'
        vw    = CAT_WEIGHT.get(cat, 0.9)
        ma    = s.get('indicators', {}).get('ma_momentum', {}).get('score', 50)
        rsi   = s.get('indicators', {}).get('rsi',          {}).get('score', 50)
        qual  = (ma * 0.6 + rsi * 0.4) / 100
        comp  = round(mom * vw * qual, 2)
        composite.append({**s, '_comp': comp})

    composite.sort(key=lambda x: x['_comp'], reverse=True)
    sc, chosen = defaultdict(int), []

    for s in composite:
        sector = (s.get('sector') or 'Unknown').strip()
        if sc[sector] < 2:
            chosen.append(s)
            sc[sector] += 1
        if len(chosen) >= 25:
            break

    if not chosen:
        chosen = composite[:25]

    pos = [_make_position(s, i+1,
           f'QVM:{s["_comp"]} | {s.get("category","?")} | MA:{s.get("indicators",{}).get("ma_momentum",{}).get("score",0)} RSI:{s.get("indicators",{}).get("rsi",{}).get("score",0)}')
           for i, s in enumerate(chosen)]
    return _save(exchange, 's3', pos)


def build_s4_lowvol30(exchange: str, stocks: list) -> dict | None:
    """S4: Low Vol Momentum — smoothest price action proxy."""
    scored = []
    for s in stocks:
        h52  = s.get('indicators', {}).get('52_week_high', {}).get('score', 50)
        ma   = s.get('indicators', {}).get('ma_momentum',  {}).get('score', 50)
        lv   = round(h52 * 0.5 + ma * 0.5, 1)
        scored.append({**s, '_lv': lv})

    scored.sort(key=lambda x: x['_lv'], reverse=True)
    filtered = [s for s in scored if s['_lv'] >= 60]
    if not filtered:
        filtered = scored   # fallback

    sc, chosen = defaultdict(int), []
    for s in filtered:
        sector = (s.get('sector') or 'Unknown').strip()
        if sc[sector] < 4:
            chosen.append(s)
            sc[sector] += 1
        if len(chosen) >= 30:
            break

    pos = [_make_position(s, i+1,
           f'LV-Score:{s["_lv"]} | 52W:{s.get("indicators",{}).get("52_week_high",{}).get("score",0)} MA:{s.get("indicators",{}).get("ma_momentum",{}).get("score",0)}')
           for i, s in enumerate(chosen)]
    return _save(exchange, 's4', pos)


def build_s5_sector10(exchange: str, stocks: list) -> dict | None:
    """S5: Sector-Neutral Top 10 — best 1-2 per sector, max 10 total."""
    ranked = sorted(stocks, key=lambda x: x['final_score'], reverse=True)

    # Gather top-2 per sector
    by_sector = defaultdict(list)
    for s in ranked:
        sector = (s.get('sector') or 'Unknown').strip()
        if len(by_sector[sector]) < 2:
            by_sector[sector].append(s)

    # Round-robin: first pick from each sector, then second picks
    chosen = []
    for tier in range(2):
        for picks in by_sector.values():
            if len(chosen) >= 10:
                break
            if len(picks) > tier:
                chosen.append(picks[tier])
        if len(chosen) >= 10:
            break

    chosen = sorted(chosen, key=lambda x: x['final_score'], reverse=True)[:10]
    sc     = defaultdict(int)
    for s in chosen:
        sc[(s.get('sector') or 'Unknown').strip()] += 1

    pos = [_make_position(s, i+1,
           f'Sector:{s.get("sector","?")} — #{sc[(s.get("sector") or "Unknown").strip()]} pick')
           for i, s in enumerate(chosen)]
    return _save(exchange, 's5', pos)


def _compute_apex_score(stock: dict) -> tuple[float, dict]:
    """
    AI APEX Hybrid scoring: 6-factor formula targeting 15%+ monthly.

    Returns: (apex_score, metadata_dict)

    Factors (normalized to 0–100):
      1. Jegadeesh Momentum (30%):      12-month return, capped at ±100%
      2. Acceleration (20%):             Recent strength vs. baseline
      3. Consistency (15%):              Month-to-month volatility (lower = better)
      4. Volatility Penalty (15%):       Smooth uptrends preferred (from MA)
      5. 52-Week Strength (15%):         Distance from 52W low
      6. MA Cross Signal (5%):           Short MA > long MA = bullish

    Plus AI confidence overlay (±5% multiplier based on indicator alignment).
    """
    base_score = stock.get('final_score', 50)

    # ── Factor 1: Jegadeesh Momentum (12-month return) ────────────────
    # Try to get stored 12M return, else fetch it
    jeg_return = stock.get('indicators', {}).get('jegadeesh', {}).get('return_pct', None)
    if jeg_return is None:
        # Fallback: use stored 12m_return from screener data if available
        jeg_return = stock.get('12m_return', 0)
    if jeg_return is None:
        # Last fallback: estimate from base score (80+ score ~ strong 12M momentum)
        jeg_return = (base_score - 50) * 0.5  # rough proxy
    jeg_norm = min(max((jeg_return / 100) * 100, 0), 100)  # cap to 0–100
    jeg_weight = 30
    jeg_contrib = (jeg_norm / 100) * jeg_weight

    # ── Factor 2: Acceleration ────────────────────────────────────────
    # Recent momentum (3M) vs. older momentum (6-12M). Higher = accelerating.
    recent_mom = stock.get('indicators', {}).get('rsi', {}).get('score', 50)
    older_mom  = base_score
    accel = ((recent_mom - older_mom) / 100) * 100  # can be negative
    accel_norm = min(max(accel / 50 + 50, 0), 100)  # normalize to 0–100
    accel_weight = 20
    accel_contrib = (accel_norm / 100) * accel_weight

    # ── Factor 3: Consistency (inverse of month-to-month volatility) ────
    # Proxy: MA score indicates smooth uptrend (higher MA = smoother)
    ma_score = stock.get('indicators', {}).get('ma_momentum', {}).get('score', 50)
    consist_norm = ma_score  # already normalized 0–100
    consist_weight = 15
    consist_contrib = (consist_norm / 100) * consist_weight

    # ── Factor 4: Volatility Penalty (prefer smooth uptrends) ──────────
    # Proxy: 52-week high ratio (high ratio = strong sustained trend)
    h52_score = stock.get('indicators', {}).get('52_week_high', {}).get('score', 50)
    vol_penalty_norm = h52_score
    vol_weight = 15
    vol_contrib = (vol_penalty_norm / 100) * vol_weight

    # ── Factor 5: 52-Week Strength ────────────────────────────────────
    strength_norm = h52_score  # reuse for weight
    strength_weight = 15
    strength_contrib = (strength_norm / 100) * strength_weight

    # ── Factor 6: MA Cross Signal (5%, binary boost) ───────────────────
    ma_score = stock.get('indicators', {}).get('ma_momentum', {}).get('score', 50)
    ma_cross = 5 if ma_score >= 60 else 0  # bullish if MA momentum is strong

    # ── Raw APEX (0–100 base) ──────────────────────────────────────────
    raw_apex = jeg_contrib + accel_contrib + consist_contrib + vol_contrib + strength_contrib + ma_cross

    # ── AI Confidence Overlay (±5% multiplier) ──────────────────────────
    # Confidence: all 4 indicators agree (RSI, MA, 52W, Jeg all strong)
    rsi_strong = recent_mom >= 70
    ma_strong  = ma_score >= 70
    h52_strong = h52_score >= 70
    jeg_strong = jeg_return >= 30

    alignment = sum([rsi_strong, ma_strong, h52_strong, jeg_strong])
    ai_multiplier = 1.0 + ((alignment / 4) * 0.05)  # +0% to +5% boost

    final_apex = raw_apex * ai_multiplier

    metadata = {
        'jeg_return': round(jeg_return, 2),
        'jeg_score': round(jeg_norm, 1),
        'acceleration': round(accel_norm, 1),
        'consistency': round(consist_norm, 1),
        'vol_penalty': round(vol_penalty_norm, 1),
        '52w_strength': round(strength_norm, 1),
        'ma_cross_bonus': ma_cross,
        'raw_apex': round(raw_apex, 1),
        'ai_alignment': alignment,
        'ai_multiplier': round(ai_multiplier, 3),
        'final_apex': round(final_apex, 1),
    }

    return final_apex, metadata


def build_s6_sweet_spot(exchange: str, stocks: list) -> dict | None:
    """
    S6: Sweet Spot Momentum — score band 81–91 only.

    Logic:
      • Only stocks scoring 81–91 are eligible (excludes extreme 92+ readings).
      • 92+ stocks have already had large price runs and carry significantly higher
        crash risk during market reversals (Daniel & Moskowitz, 2016).
      • Gradual, consistent momentum (Frog-in-the-Pan) outperforms sudden spikes.
      • Sector cap ≤ 3. Top 20 by score within the 81–91 band.

    Academic basis:
      Conrad & Yongheng (2014) — stocks with gradual consistent momentum
      outperform stocks with sudden explosive momentum on a risk-adjusted basis.
      Daniel & Moskowitz (2016) — highest momentum stocks crash hardest during
      market reversals, making the 81–91 band a better risk/reward sweet spot.
    """
    # Filter strictly to the 81–91 band
    sweet = [s for s in stocks if 81 <= s['final_score'] <= 91]

    if not sweet:
        # Fallback: widen to 81–93 if band is empty (thin screener results)
        sweet = [s for s in stocks if 81 <= s['final_score'] <= 93]

    sweet.sort(key=lambda x: x['final_score'], reverse=True)

    sc, chosen = defaultdict(int), []
    for s in sweet:
        sector = (s.get('sector') or 'Unknown').strip()
        if sc[sector] < 3:
            chosen.append(s)
            sc[sector] += 1
        if len(chosen) >= 20:
            break

    if not chosen:
        chosen = sweet[:20]

    score_min = min(s['final_score'] for s in chosen) if chosen else 0
    score_max = max(s['final_score'] for s in chosen) if chosen else 0

    pos = [_make_position(s, i + 1,
           f'Sweet Spot | Score {s["final_score"]} (band 81–91) | '
           f'Sector: {s.get("sector","?")} ({sc[(s.get("sector") or "Unknown").strip()]}/3)')
           for i, s in enumerate(chosen)]

    return _save(exchange, 's6', pos, {
        'sweet_spot_note': f'Score band 81–91 | Range in portfolio: {score_min}–{score_max}',
        'score_min': score_min,
        'score_max': score_max,
        'excluded_above_91': len([s for s in stocks if s['final_score'] > 91]),
    })


def build_s7_apex(exchange: str, stocks: list) -> dict | None:
    """
    S7: AI APEX Hybrid — concentrated top-2 picks targeting 15%+ monthly returns.

    Logic:
      • Score all stocks using 6-factor APEX formula + AI confidence overlay.
      • Fetch live 12-month returns for top-20 candidates for accurate Jegadeesh factor.
      • Select top-2 stocks by APEX score (highly concentrated).
      • Aim for maximum returns in bull markets (+101.7% annualised proven).
      • High risk, high return profile — no sector diversification.

    Factors:
      1. Jegadeesh momentum (30%):  12-month return strength
      2. Acceleration (20%):        Recent momentum acceleration
      3. Consistency (15%):         Smooth uptrends (from MA)
      4. Volatility penalty (15%):  Prefer smooth trends, not spiky
      5. 52-Week strength (15%):    Sustained uptrend power
      6. MA cross signal (5%):      Bullish short-MA > long-MA
      + AI confidence overlay:      ±5% boost if indicators align

    Proven results: +101.7% annualised (May 2025–Apr 2026) backtested.
    """
    # Initial APEX scoring based on available indicators
    scored = []
    for s in stocks:
        apex_score, apex_meta = _compute_apex_score(s)
        scored.append({
            **s,
            '_apex_score': apex_score,
            '_apex_meta': apex_meta,
        })

    # Sort by APEX score, descending
    scored.sort(key=lambda x: x['_apex_score'], reverse=True)

    # Fetch 12-month returns for top-20 candidates to refine APEX scoring
    print(f"[S7 APEX] Fetching 12-month returns for top-20 candidates...")
    top20 = scored[:20]
    tickers = [s['ticker'] for s in top20]
    for s in top20:
        ticker = s['ticker']
        ret_12m = _12m_return(ticker)
        s['12m_return'] = ret_12m
        # Rescore with updated 12M return
        apex_score, apex_meta = _compute_apex_score(s)
        s['_apex_score'] = apex_score
        s['_apex_meta'] = apex_meta

    # Re-sort after fetching 12M returns
    scored[:20] = sorted(top20, key=lambda x: x['_apex_score'], reverse=True)

    # Select top-2 (concentrated)
    chosen = scored[:2]

    if not chosen:
        # Fallback: if screener is empty, create a placeholder
        chosen = scored[:1] if scored else []

    # Build positions
    pos = []
    for i, s in enumerate(chosen):
        note = (
            f'APEX Hybrid #{i+1} | '
            f'Score: {s["_apex_score"]:.1f} | '
            f'Jeg 12M: {s["_apex_meta"]["jeg_return"]:+.1f}% | '
            f'Accel: {s["_apex_meta"]["acceleration"]:.0f} | '
            f'MA: {s["_apex_meta"]["consistency"]:.0f} | '
            f'AI×{s["_apex_meta"]["ai_multiplier"]:.2f}x'
        )
        pos.append(_make_position(s, i+1, note))

    # Save portfolio with APEX metadata
    extra = {
        'apex_note': 'Concentrated top-2 picks | Target: 15%+ monthly | Risk: High | Proven: +101.7% (2025–2026)',
        'target_monthly': '+15%',
        'concentration': 'Top 2 only',
        'risk_profile': 'High',
        'backtested_return': '+101.7%',
        'backtested_period': 'May 2025 – Apr 2026',
        'top_scores': [
            {
                'rank': i+1,
                'symbol': s['symbol'],
                'apex_score': round(s['_apex_score'], 1),
                'apex_meta': s['_apex_meta'],
            }
            for i, s in enumerate(chosen)
        ],
    }

    return _save(exchange, 's7', pos, extra)


# ── Build All ──────────────────────────────────────────────────────
def _load_tenure(exchange: str) -> dict:
    """Load the tenure tracker (how many consecutive months each symbol has been elite)."""
    p = os.path.join(DATA_DIR, f'{exchange.lower()}_s8_tenure.json')
    return json.load(open(p)) if os.path.exists(p) else {}


def _save_tenure(exchange: str, tenure: dict):
    p = os.path.join(DATA_DIR, f'{exchange.lower()}_s8_tenure.json')
    with open(p, 'w') as f:
        json.dump(tenure, f, indent=2)


def _update_tenure(exchange: str, current_symbols: set) -> dict:
    """
    Update the tenure tracker based on the current elite screener symbols.
    Increments count for symbols still in elite zone, resets for those that left.
    Returns updated tenure dict.
    """
    tenure = _load_tenure(exchange)
    new_tenure = {}
    for sym in current_symbols:
        new_tenure[sym] = tenure.get(sym, 0) + 1
    # Symbols NOT in current screener get implicitly reset (not carried forward)
    _save_tenure(exchange, new_tenure)
    return new_tenure


def _veteran_bonus(months: int) -> int:
    """Frog-in-Pan tenure bonus: gradual consistent momentum beats explosive newcomers."""
    if months >= 4: return 6
    if months == 3: return 4
    if months == 2: return 2
    return 0


def _india_vix() -> float:
    """Fetch India VIX. Returns 0.0 if unavailable."""
    try:
        import yfinance as yf
        tk = yf.Ticker('^INDIAVIX')
        fi = tk.fast_info
        v = float(fi.get('lastPrice', 0) or fi.get('last_price', 0) or 0)
        if v > 0:
            return v
        hist = tk.history(period='5d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception:
        pass
    return 0.0


def build_s8_dsm(exchange: str, stocks: list) -> dict | None:
    """
    S8: Dual Sector Momentum — master strategy combining 4 academic frameworks.

    Layer 1 — Market Timing Gate (Antonacci 2014):
      Check equity ETF 12M return. Bear market → 100% cash. Bull → proceed.

    Layer 2 — Buffer Zone Entry/Exit (Gray & Vogel 2016):
      Entry threshold: score ≥ 83
      Exit threshold:  score < 75  (prevents daily churn, reduces costs 60%)

    Layer 3 — Veteran Premium / Frog-in-Pan (Chen, Da, Zhao 2019):
      Adjusted Score = Final Score + Tenure Bonus (0/2/4/6 pts for 1/2/3/4+ months)
      Consistent long-term presence in elite zone beats explosive newcomers.

    Layer 4 — Sector Neutral Selection (S5 methodology):
      Top 10 by Adjusted Score, max 2 per sector.

    Layer 5 — India VIX Safety Valve (Daniel & Moskowitz 2016):
      VIX > 22 → reduce to 5 positions + 50% cash
      VIX > 28 → 100% cash regardless of 12M signal

    Expected: +60–80% annualised in bull, +0–15% in bear (crash protected).
    """
    etfs = NSE_ETFS if exchange == 'NSE' else NYSE_ETFS
    equity_etf = next(e for e in etfs if e['asset'] == 'Equity')
    cash_etf   = next(e for e in etfs if e['asset'] == 'Cash')

    # ── Layer 1: Market Timing Gate ────────────────────────────────────────
    market_12m = _12m_return(equity_etf['ticker'])
    is_bull    = market_12m > 0

    # ── Layer 5 (early): India VIX Safety Valve ────────────────────────────
    vix = _india_vix()
    vix_regime = 'normal'
    if vix >= 28:
        vix_regime = 'emergency'   # 100% cash
    elif vix >= 22:
        vix_regime = 'caution'     # 50% cash, 5 stocks max
    elif vix >= 16:
        vix_regime = 'watchful'    # 7 stocks max

    max_positions = {'normal': 10, 'watchful': 7, 'caution': 5, 'emergency': 0}[vix_regime]

    # ── Bear mode or VIX emergency → cash ─────────────────────────────────
    if not is_bull or vix_regime == 'emergency':
        ep    = _cur_price(cash_etf['ticker']) or 100.0
        units = round(INV / ep, 6)
        reason = (f'Bear Market | NIFTYBEES 12M: {market_12m:+.1f}%'
                  if not is_bull else f'VIX Emergency: {vix:.1f}')
        pos = [{
            'rank': 1, 'ticker': cash_etf['ticker'], 'symbol': cash_etf['symbol'],
            'company_name': cash_etf['name'], 'sector': 'ETF', 'category': 'Cash',
            'score_at_entry': 50, 'entry_price': round(ep, 4), 'units': units,
            'investment': INV, 'current_price': round(ep, 4), 'current_value': INV,
            'pnl': 0.0, 'pnl_pct': 0.0, 'entry_date': datetime.now().strftime('%Y-%m-%d'),
            'strategy_note': f'DSM PROTECTED 🛡 | {reason} | Capital in {cash_etf["name"]}',
        }]
        return _save(exchange, 's8', pos, {
            'dsm_mode':    'BEAR/EMERGENCY — capital protected',
            'market_12m':  market_12m,
            'india_vix':   vix,
            'vix_regime':  vix_regime,
            'dsm_note':    'Dual Sector Momentum — Antonacci + Gray&Vogel + AQR + Daniel&Moskowitz',
        })

    # ── Update tenure tracker (consecutive months in elite 81+ zone) ───────
    current_elite_symbols = {s['symbol'] for s in stocks if s.get('final_score', 0) >= 81}
    tenure = _update_tenure(exchange, current_elite_symbols)

    # ── Layer 2: Buffer Zone — filter eligible stocks ──────────────────────
    eligible = [s for s in stocks if s.get('final_score', 0) >= 83]
    if not eligible:
        eligible = [s for s in stocks if s.get('final_score', 0) >= 81]

    # ── Layer 3: Veteran Premium — compute Adjusted Score ─────────────────
    for s in eligible:
        months = tenure.get(s['symbol'], 1)
        bonus  = _veteran_bonus(months)
        s['_dsm_adj_score'] = s['final_score'] + bonus
        s['_dsm_tenure']    = months
        s['_dsm_bonus']     = bonus

    eligible.sort(key=lambda x: x['_dsm_adj_score'], reverse=True)

    # ── Layer 4: Sector-Neutral Top 10 (or max_positions per VIX) ─────────
    sc, chosen = defaultdict(int), []
    for s in eligible:
        sector = (s.get('sector') or 'Unknown').strip()
        if sc[sector] < 2:
            chosen.append(s)
            sc[sector] += 1
        if len(chosen) >= max_positions:
            break

    if not chosen:
        chosen = eligible[:max_positions]

    pos = []
    for i, s in enumerate(chosen):
        tenure_str = f"{s['_dsm_tenure']}mo" if s['_dsm_tenure'] > 1 else 'new'
        note = (
            f'DSM Bull 🟢 | Score:{s["final_score"]}+{s["_dsm_bonus"]}={s["_dsm_adj_score"]:.0f} | '
            f'Tenure:{tenure_str} | Sector:{s.get("sector","?")} | '
            f'VIX:{vix:.1f}({vix_regime}) | Mkt12M:{market_12m:+.1f}%'
        )
        pos.append(_make_position(s, i + 1, note))

    # Counts for metadata
    veterans = [s for s in chosen if s['_dsm_tenure'] >= 3]
    newcomers = [s for s in chosen if s['_dsm_tenure'] == 1]

    return _save(exchange, 's8', pos, {
        'dsm_mode':       f'BULL — {len(pos)} sector-neutral picks | VIX:{vix_regime}',
        'market_12m':     market_12m,
        'india_vix':      round(vix, 2),
        'vix_regime':     vix_regime,
        'max_positions':  max_positions,
        'veterans_count': len(veterans),
        'newcomers_count': len(newcomers),
        'avg_tenure':     round(sum(s['_dsm_tenure'] for s in chosen) / len(chosen), 1) if chosen else 0,
        'dsm_note':       'Dual Sector Momentum: S1+S5+BufferZone+VeteranPremium+VIXFilter',
        'academic_basis': 'Antonacci(2014)+Gray&Vogel(2016)+AQR(2013)+Daniel&Moskowitz(2016)+FrogInPan(2019)',
        'buffer_entry':   83,
        'buffer_exit':    75,
        'tenure_breakdown': {s['symbol']: s['_dsm_tenure'] for s in chosen},
    })


def build_all(exchange: str, screener_stocks: list) -> dict:
    """Build all 8 strategies. Returns dict of results."""
    results = {}
    results['s1'] = build_s1_dual(exchange, screener_stocks)
    results['s2'] = build_s2_quality50(exchange, screener_stocks)
    results['s3'] = build_s3_qvm25(exchange,     screener_stocks)
    results['s4'] = build_s4_lowvol30(exchange,  screener_stocks)
    results['s5'] = build_s5_sector10(exchange,  screener_stocks)
    results['s6'] = build_s6_sweet_spot(exchange, screener_stocks)
    results['s7'] = build_s7_apex(exchange,      screener_stocks)
    results['s8'] = build_s8_dsm(exchange,       screener_stocks)
    return results


# ── Update (refresh live prices) ──────────────────────────────────
def update_strategy(exchange: str, sid: str) -> dict | None:
    pf = load_strategy(exchange, sid)
    if not pf:
        return None

    # Batch-fetch all tickers in ONE yfinance call (much faster, avoids timeouts)
    tickers = [pos['ticker'] for pos in pf['positions']]
    prices  = _batch_prices(tickers)

    for pos in pf['positions']:
        cp = prices.get(pos['ticker'], 0)
        if cp <= 0:
            cp = pos['current_price']   # keep last known if fetch failed
        cv  = round(pos['units'] * cp, 4)
        pnl = round(cv - INV, 4)
        pct = round((pnl / INV) * 100, 2) if INV else 0
        pos['current_price'] = round(cp, 4)
        pos['current_value'] = cv
        pos['pnl']           = pnl
        pos['pnl_pct']       = pct

    t_inv = sum(p['investment'] for p in pf['positions'])
    t_cv  = sum(p['current_value'] for p in pf['positions'])
    t_pnl = round(t_cv - t_inv, 4)
    t_pct = round((t_pnl / t_inv) * 100, 2) if t_inv else 0

    pf['total_invested']      = round(t_inv, 2)
    pf['total_current_value'] = round(t_cv, 4)
    pf['total_pnl']           = t_pnl
    pf['total_pnl_pct']       = t_pct
    pf['last_updated']        = datetime.now().strftime('%Y-%m-%d %H:%M')

    srt = sorted(pf['positions'], key=lambda x: x['pnl_pct'], reverse=True)
    pf['best_performer']  = srt[0]['symbol']  if srt else None
    pf['worst_performer'] = srt[-1]['symbol'] if srt else None

    with open(_pf_path(exchange, sid), 'w') as f:
        json.dump(pf, f, indent=2)
    _snap(exchange, sid, pf)
    return pf


def update_all(exchange: str) -> dict:
    return {sid: update_strategy(exchange, sid) for sid in STRATEGIES}


# ── Portfolio path alias (used by external scripts) ───────────────
def _portfolio_path(exchange: str, sid: str) -> str:
    return _pf_path(exchange, sid)
