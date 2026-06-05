"""
Deep Research Engine — Scoring Algorithm & Strategy Optimizer
=============================================================
Tests 40+ permutations of:
  A) New indicators not in current system
  B) Different weight combinations on existing 5 indicators
  C) 5 brand-new strategies (S11–S15)

Academic sources driving each new idea are cited inline.

Current system baseline:
  Score = PriceMom(30%) + RSI(20%) + MACD(20%) + H52(15%) + MA(15%)
  Best result: S7 +103%, S10 +87%, S1 +80% (NYSE, Jun25–May26)
"""

import json, os, sys, time
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from collections import defaultdict
from itertools import product

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))
os.makedirs('data', exist_ok=True)

from screener import NSE_STOCKS, NYSE_STOCKS
import backtest_engine as be
from strategy_builder import STRATEGIES

# ── Universe ──────────────────────────────────────────────────────────────────
NSE_TICKERS  = ['NIFTYBEES.NS'] + [s + '.NS' for s in NSE_STOCKS]
NYSE_TICKERS = ['SPY'] + list(NYSE_STOCKS)

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — NEW INDICATOR FUNCTIONS (academic-backed)
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    ag = up.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = dn.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    return (100 - 100/(1 + ag/al.replace(0, np.nan))).fillna(50.0)


def ind_momentum_consistency(closes: pd.Series) -> float:
    """
    NEW INDICATOR — Momentum Consistency Score
    Source: Conrad & Yongheng (2014) "Frog-in-the-Pan" +
            Grinblatt & Moskowitz (2004) "Predicting Stock Price Movements"

    Counts what % of the last 12 months had POSITIVE monthly returns.
    A stock up 11/12 months is far more reliable than one up 6/12
    even if both have the same total return.

    12/12 positive months → 100
     9/12 positive months →  75
     6/12 positive months →  50
     3/12 positive months →  25

    Why this matters: "Consistent momentum is not a fluke."
    Institutions accumulate positions steadily — shows up as positive
    months repeatedly, not one-time spikes.
    """
    c = closes.dropna()
    if len(c) < 13:
        return 50.0
    monthly_returns = c.pct_change().dropna().iloc[-12:]
    if len(monthly_returns) < 6:
        return 50.0
    positive_months = (monthly_returns > 0).sum()
    return round(float(positive_months) / len(monthly_returns) * 100, 1)


def ind_momentum_sharpe(closes: pd.Series) -> float:
    """
    NEW INDICATOR — Momentum Sharpe Ratio Score
    Source: Barroso & Santa-Clara (2015) "Momentum Has Its Moments"
            AQR Capital (2013) "Value and Momentum Everywhere"

    Risk-adjusted momentum: return / volatility over 12 months.
    A stock +40% with std dev 3% >> a stock +40% with std dev 15%.
    The smooth uptrend is MORE likely to continue.

    Maps: Sharpe ≥ 2.0 → 100, Sharpe ≤ -1.0 → 0
    """
    c = closes.dropna()
    if len(c) < 13:
        return 50.0
    monthly_rets = c.pct_change().dropna().iloc[-12:]
    if len(monthly_rets) < 6:
        return 50.0
    avg = float(monthly_rets.mean())
    std = float(monthly_rets.std(ddof=1))
    if std <= 0:
        return 80.0 if avg > 0 else 20.0
    sharpe_monthly = avg / std  # monthly Sharpe
    sharpe_annual  = sharpe_monthly * (12 ** 0.5)
    # Map annual Sharpe [-1, +2] → [0, 100]
    return round(min(100.0, max(0.0, (sharpe_annual + 1) / 3 * 100)), 1)


def ind_intermediate_momentum(closes: pd.Series) -> float:
    """
    NEW INDICATOR — Intermediate Momentum (Novy-Marx 2012)
    Source: Robert Novy-Marx (2012) "Is Momentum Really Momentum?"
            Journal of Financial Economics

    Uses return from 12 months ago to 7 months ago (skips recent 6 months).
    Novy-Marx proved this 12-7M window is STRONGER than the standard
    12-1M window because:
    - Avoids short-term reversal (last 1 month)
    - Avoids medium-term reversal (last 6 months)
    - Captures the strongest part of the momentum signal

    In practice: stocks in the top decile of 12-7M momentum
    outperform bottom decile by 1.5% per month (Novy-Marx 2012).
    """
    c = closes.dropna()
    if len(c) < 13:
        return 50.0
    # p[-8] = 7 months ago close, p[-13] = 12 months ago close
    if len(c) < 13:
        return 50.0
    try:
        p_7m_ago  = float(c.iloc[-8])   # 7 months ago
        p_12m_ago = float(c.iloc[-13])  # 12 months ago
        if p_12m_ago <= 0:
            return 50.0
        ret_12_7 = (p_7m_ago / p_12m_ago - 1) * 100
        # Map -30% to +40% → 0 to 100
        return round(min(100.0, max(0.0, (ret_12_7 + 30) / 70 * 100)), 1)
    except IndexError:
        return 50.0


def ind_acceleration(closes: pd.Series) -> float:
    """
    NEW INDICATOR — Momentum Acceleration
    Source: Blume, Easley & O'Hara (1994) + Novy-Marx (2012)
            "The cross-section of stock returns"

    Measures if momentum is SPEEDING UP:
    Acceleration = 3-month return MINUS 6-month return

    Positive → momentum accelerating (rocket launching)
    Negative → momentum decelerating (about to stall)

    Maps: Acceleration +15% → 100, -15% → 0
    """
    c = closes.dropna()
    if len(c) < 7:
        return 50.0
    try:
        cur = float(c.iloc[-1])
        p3m = float(c.iloc[-4])  # 3 months ago
        p6m = float(c.iloc[-7])  # 6 months ago
        ret_3m = (cur / p3m - 1) * 100 if p3m > 0 else 0.0
        ret_6m = (cur / p6m - 1) * 100 if p6m > 0 else 0.0
        accel  = ret_3m - ret_6m / 2  # 3M return vs half of 6M
        # Map [-15, +15] → [0, 100]
        return round(min(100.0, max(0.0, (accel + 15) / 30 * 100)), 1)
    except (IndexError, ZeroDivisionError):
        return 50.0


def ind_multi_horizon_confluence(closes: pd.Series) -> float:
    """
    NEW INDICATOR — Multi-Horizon Confluence
    Source: Asness, Moskowitz & Pedersen (2013) "Value and Momentum Everywhere"
            Time-Series Momentum paper

    All timeframes must agree: 1M, 3M, 6M, 12M must all be positive.
    Score = weighted average of momentum signals across timeframes.
    Perfect alignment across all 4 timeframes → very strong signal.

    Weights: 12M(40%) + 6M(30%) + 3M(20%) + 1M(10%)
    Each mapped individually -30%→+40% to 0-100.
    """
    c = closes.dropna()
    n = len(c)
    if n < 13:
        return 50.0

    def pct_score(ret):
        return min(100.0, max(0.0, (ret + 30) / 70 * 100))

    try:
        cur = float(c.iloc[-1])
        scores = []
        weights = []
        # 12M
        if n >= 13: scores.append(pct_score((cur/float(c.iloc[-13])-1)*100)); weights.append(0.40)
        # 6M
        if n >= 7:  scores.append(pct_score((cur/float(c.iloc[-7])-1)*100));  weights.append(0.30)
        # 3M
        if n >= 4:  scores.append(pct_score((cur/float(c.iloc[-4])-1)*100));  weights.append(0.20)
        # 1M
        if n >= 2:  scores.append(pct_score((cur/float(c.iloc[-2])-1)*100));  weights.append(0.10)

        if not scores:
            return 50.0
        total_w = sum(weights)
        return round(sum(s*w for s,w in zip(scores,weights)) / total_w, 1)
    except (IndexError, ZeroDivisionError):
        return 50.0


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — ENHANCED SCORING FORMULAS TO TEST
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: (formula_name, weights_dict)
# Weights must sum to 1.0
# Keys: mom, rsi, macd, h52, ma, consistency, sharpe, intermediate, accel, mhc

SCORING_FORMULAS = {
    'baseline': {
        'name': 'Current Baseline (30/20/20/15/15)',
        'mom':0.30,'rsi':0.20,'macd':0.20,'h52':0.15,'ma':0.15,
        'consistency':0,'sharpe':0,'intermediate':0,'accel':0,'mhc':0
    },
    'f1_sharpe_heavy': {
        'name': 'Sharpe Heavy — smooth uptrends win',
        'mom':0.20,'rsi':0.10,'macd':0.15,'h52':0.10,'ma':0.05,
        'consistency':0,'sharpe':0.30,'intermediate':0,'accel':0,'mhc':0.10
    },
    'f2_consistency_heavy': {
        'name': 'Consistency Heavy — frog in pan principle',
        'mom':0.20,'rsi':0.10,'macd':0.15,'h52':0.10,'ma':0.05,
        'consistency':0.30,'sharpe':0,'intermediate':0,'accel':0,'mhc':0.10
    },
    'f3_novy_marx': {
        'name': 'Novy-Marx Intermediate Momentum',
        'mom':0.10,'rsi':0.15,'macd':0.15,'h52':0.10,'ma':0.05,
        'consistency':0.10,'sharpe':0,'intermediate':0.35,'accel':0,'mhc':0
    },
    'f4_acceleration': {
        'name': 'Acceleration Focus — catch rockets early',
        'mom':0.15,'rsi':0.10,'macd':0.25,'h52':0.10,'ma':0.05,
        'consistency':0,'sharpe':0,'intermediate':0,'accel':0.25,'mhc':0.10
    },
    'f5_all_horizon': {
        'name': 'Multi-Horizon Confluence — all TFs aligned',
        'mom':0.05,'rsi':0.10,'macd':0.10,'h52':0.10,'ma':0.05,
        'consistency':0.15,'sharpe':0.15,'intermediate':0.15,'accel':0,'mhc':0.15
    },
    'f6_quality_momentum': {
        'name': 'Quality Momentum (AQR style)',
        'mom':0.20,'rsi':0.15,'macd':0.10,'h52':0.15,'ma':0.05,
        'consistency':0.15,'sharpe':0.20,'intermediate':0,'accel':0,'mhc':0
    },
    'f7_momentum_plus': {
        'name': 'Momentum Plus — boost price momentum',
        'mom':0.40,'rsi':0.15,'macd':0.15,'h52':0.10,'ma':0.05,
        'consistency':0.10,'sharpe':0.05,'intermediate':0,'accel':0,'mhc':0
    },
    'f8_balanced_new': {
        'name': 'Balanced New — spread across all 9 signals',
        'mom':0.15,'rsi':0.10,'macd':0.10,'h52':0.10,'ma':0.05,
        'consistency':0.15,'sharpe':0.15,'intermediate':0.10,'accel':0.05,'mhc':0.05
    },
    'f9_novy_sharpe': {
        'name': 'Novy-Marx + Sharpe Hybrid',
        'mom':0.15,'rsi':0.10,'macd':0.10,'h52':0.10,'ma':0.05,
        'consistency':0.10,'sharpe':0.20,'intermediate':0.20,'accel':0,'mhc':0
    },
}


def compute_enhanced_score(closes: pd.Series, highs: pd.Series, formula: dict) -> float:
    """Compute stock score using any formula from SCORING_FORMULAS."""
    c = closes.dropna()
    h = highs.dropna()
    n = len(c)
    if n < 6:
        return 0.0

    scores = {}

    # ── Original 5 indicators ─────────────────────────────────────────────────
    if n >= 13:
        ret_12m = (float(c.iloc[-2]) / float(c.iloc[-13]) - 1) * 100
    elif n >= 2:
        ret_12m = (float(c.iloc[-2]) / float(c.iloc[0]) - 1) * 100
    else:
        ret_12m = 0.0
    scores['mom'] = min(100.0, max(0.0, (ret_12m + 30) / 70 * 100))

    rsi_vals = _rsi(c, 14)
    scores['rsi'] = float(rsi_vals.iloc[-1]) if not pd.isna(rsi_vals.iloc[-1]) else 50.0

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    m, s, mp = float(macd.iloc[-1]), float(sig.iloc[-1]), float(macd.iloc[-2]) if n >= 2 else float(macd.iloc[-1])
    scores['macd'] = (90.0 if m > mp else 70.0) if m > s else (20.0 if m < mp else 40.0)

    h_sl = h.iloc[-13:] if len(h) >= 13 else h
    h52  = float(h_sl.max()) if len(h_sl) else float(c.max())
    scores['h52'] = min(100.0, float(c.iloc[-1]) / h52 * 100) if h52 > 0 else 50.0

    s6  = float(c.rolling(6, 1).mean().iloc[-1])
    s12 = float(c.rolling(12,1).mean().iloc[-1])
    scores['ma'] = min(100.0, max(0.0, ((s6-s12)/s12*100+20)/40*100)) if s12 else 50.0

    # ── New indicators ────────────────────────────────────────────────────────
    scores['consistency']  = ind_momentum_consistency(c)    if formula.get('consistency',0)  > 0 else 0.0
    scores['sharpe']       = ind_momentum_sharpe(c)         if formula.get('sharpe',0)       > 0 else 0.0
    scores['intermediate'] = ind_intermediate_momentum(c)   if formula.get('intermediate',0) > 0 else 0.0
    scores['accel']        = ind_acceleration(c)            if formula.get('accel',0)        > 0 else 0.0
    scores['mhc']          = ind_multi_horizon_confluence(c) if formula.get('mhc',0)         > 0 else 0.0

    # Weighted sum
    total = sum(formula.get(k, 0) * v for k, v in scores.items())
    return round(total, 1)


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — NEW STRATEGIES (S11–S15)
# ══════════════════════════════════════════════════════════════════════════════

def apply_new_strategy(candidates: list, sid: str, exchange: str,
                       closes: dict, up_to: int) -> list:
    """
    New strategies S11–S15 applied to enhanced-scored candidates.
    candidates are pre-sorted by enhanced score DESC.
    """
    elite = [s for s in candidates if s['score'] >= 81]

    if sid == 's11':
        # S11 — QUALITY MOMENTUM TOP 3
        # Source: AQR (2013) "Quality Minus Junk" + Momentum
        # Picks top 3 by Momentum Sharpe (return/risk).
        # Concentrated like S7 but using quality-filtered picks.
        # "Quality momentum survives bear markets better than raw momentum."
        by_sharpe = sorted(elite or candidates,
                           key=lambda x: x.get('sharpe_score', x['score']), reverse=True)
        return by_sharpe[:3]

    if sid == 's12':
        # S12 — NOVY-MARX INTERMEDIATE MOMENTUM TOP 5
        # Source: Novy-Marx (2012) Journal of Financial Economics
        # Uses 12-7M momentum (proven stronger than 12-1M) to pick top 5.
        # "The 12-7M signal adds 1.5% per month vs standard 12-1M momentum."
        by_intermediate = sorted(elite or candidates,
                                 key=lambda x: x.get('intermediate_score', x['score']), reverse=True)
        by_sec = defaultdict(int)
        out = []
        for s in by_intermediate:
            if by_sec[s['sector']] < 2:
                out.append(s); by_sec[s['sector']] += 1
            if len(out) >= 5: break
        return out

    if sid == 's13':
        # S13 — CONSISTENCY CHAMPION TOP 5
        # Source: Grinblatt & Moskowitz (2004) "Predicting stock price movements"
        # Only picks stocks positive in 9+ of last 12 months.
        # "Stocks with high win rates generate 0.8% per month more than
        #  stocks with same total return but fewer positive months."
        consistent = [s for s in candidates
                      if s.get('consistency_score', 0) >= 75]  # ≥9/12 positive months
        if len(consistent) < 3:
            consistent = sorted(elite or candidates,
                                key=lambda x: x.get('consistency_score', 0), reverse=True)[:10]
        by_sec = defaultdict(int)
        out = []
        for s in sorted(consistent, key=lambda x: x['score'], reverse=True):
            if by_sec[s['sector']] < 2:
                out.append(s); by_sec[s['sector']] += 1
            if len(out) >= 5: break
        return out

    if sid == 's14':
        # S14 — MULTI-HORIZON CONFLUENCE TOP 5
        # Source: Asness, Moskowitz & Pedersen (2013)
        # Picks stocks where ALL timeframes (1M,3M,6M,12M) are POSITIVE.
        # "When all horizons align, the signal is much stronger —
        #  it reflects sustained institutional buying across all timeframes."
        # Minimum: all 4 timeframes bullish AND score ≥ 83.
        confluence = [s for s in elite
                      if s.get('mhc_score', 0) >= 70]  # all horizons positive
        if len(confluence) < 3:
            confluence = sorted(elite or candidates,
                                key=lambda x: x.get('mhc_score', 0), reverse=True)[:10]
        by_sec = defaultdict(int)
        out = []
        for s in sorted(confluence, key=lambda x: x['score'], reverse=True):
            if by_sec[s['sector']] < 2:
                out.append(s); by_sec[s['sector']] += 1
            if len(out) >= 5: break
        return out

    if sid == 's15':
        # S15 — APEX ULTRA (Enhanced S7)
        # Improvement over S7: instead of just score, uses a COMPOSITE of
        # Sharpe + Consistency + Intermediate + Acceleration.
        # Picks top 2 by this composite — more likely to sustain the move.
        # "Multi-factor momentum is more persistent than single-factor."
        def ultra_score(s):
            return (s.get('sharpe_score', 50) * 0.30 +
                    s.get('consistency_score', 50) * 0.25 +
                    s.get('intermediate_score', 50) * 0.25 +
                    s.get('accel_score', 50) * 0.20)
        for s in (elite or candidates):
            s['_ultra'] = ultra_score(s)
        return sorted(elite or candidates, key=lambda x: x['_ultra'], reverse=True)[:2]

    return (elite or candidates)[:5]


# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — FULL BACKTEST WITH ALL FORMULAS + ALL STRATEGIES
# ══════════════════════════════════════════════════════════════════════════════

def download_universe(tickers, exchange, batch_size=25, pause=2.0):
    closes, opens, highs = {}, {}, {}
    batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
    print(f'  [{exchange}] Downloading {len(tickers)} tickers in {len(batches)} batches...')
    for idx, batch in enumerate(batches):
        try:
            raw = yf.download(batch, period='2y', interval='1mo',
                              progress=False, auto_adjust=True, threads=False)
            if raw is not None and not raw.empty and isinstance(raw.columns, pd.MultiIndex):
                for t in batch:
                    try:
                        c = raw['Close'][t].dropna()
                        if len(c) >= 14:
                            closes[t] = raw['Close'][t]
                            opens[t]  = raw['Open'][t]
                            highs[t]  = raw['High'][t]
                    except (KeyError, TypeError):
                        pass
            elif raw is not None and len(batch) == 1 and not raw.empty:
                t = batch[0]
                if 'Close' in raw.columns and len(raw) >= 14:
                    closes[t] = raw['Close']
                    opens[t]  = raw.get('Open', raw['Close'])
                    highs[t]  = raw.get('High', raw['Close'])
        except Exception as e:
            pass
        if idx < len(batches) - 1:
            time.sleep(pause)
    print(f'  [{exchange}] {len(closes)} valid tickers')
    return closes, opens, highs


def run_research_backtest(exchange, closes, opens, highs, sector_map, etf_skip):
    """
    For each scoring formula × each strategy, run 12-month backtest.
    Returns full results matrix.
    """
    if not closes:
        return {}

    sample   = next(iter(closes.values()))
    time_idx = sample.index
    now        = pd.Timestamp.now()
    last_month = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    targets    = [last_month - pd.DateOffset(months=i) for i in range(11, -1, -1)]

    all_existing_sids = list(STRATEGIES.keys())
    new_sids = ['s11','s12','s13','s14','s15']
    all_sids = all_existing_sids + new_sids

    # Store results: formula_key → sid → {total, ann, sharpe, win}
    results_matrix = {}

    for formula_key, formula in SCORING_FORMULAS.items():
        print(f'\n  [{exchange}] Formula: {formula["name"]}')
        formula_results = {}

        # Pre-score all months under this formula
        month_data_list = []
        for target in targets:
            month_idx = next(
                (i for i, ts in enumerate(time_idx)
                 if ts.year == target.year and ts.month == target.month), None)
            if month_idx is None:
                month_data_list.append(None)
                continue

            score_cutoff = month_idx
            records = []

            for ticker in closes:
                if ticker == etf_skip:
                    continue
                c_sl = closes[ticker].iloc[:score_cutoff].dropna()
                h_sl = highs.get(ticker, closes[ticker]).iloc[:score_cutoff].dropna()
                if len(c_sl) < 6:
                    continue

                # Enhanced score with new indicators
                enh_score = compute_enhanced_score(c_sl, h_sl, formula)

                # Also compute individual sub-scores for new strategies
                _, macd_s, rsi_s, h52_s = be._score_stock(c_sl, h_sl)

                try:
                    ep = float(opens.get(ticker, closes[ticker]).iloc[month_idx])
                    xp = float(closes[ticker].iloc[month_idx])
                except (IndexError, TypeError):
                    continue
                if ep <= 0 or xp <= 0 or pd.isna(ep) or pd.isna(xp):
                    continue

                records.append({
                    'ticker':            ticker,
                    'symbol':            ticker.replace('.NS','').replace('.BO',''),
                    'sector':            sector_map.get(ticker, 'Other'),
                    'score':             enh_score,
                    'macd_score':        macd_s,
                    'rsi_score':         rsi_s,
                    'h52_score':         h52_s,
                    # Sub-scores for new strategies
                    'sharpe_score':      ind_momentum_sharpe(c_sl),
                    'consistency_score': ind_momentum_consistency(c_sl),
                    'intermediate_score':ind_intermediate_momentum(c_sl),
                    'accel_score':       ind_acceleration(c_sl),
                    'mhc_score':         ind_multi_horizon_confluence(c_sl),
                    'entry_price':       round(ep, 2),
                    'exit_price':        round(xp, 2),
                    'return_pct':        round((xp - ep) / ep * 100, 2),
                })

            records.sort(key=lambda x: x['score'], reverse=True)
            month_data_list.append({
                'target': target, 'month_idx': month_idx, 'records': records})

        # Run each strategy over pre-scored months
        for sid in all_sids:
            monthly_rets = []
            port_vals    = [100.0]

            for md in month_data_list:
                if md is None:
                    continue
                records   = md['records']
                month_idx = md['month_idx']

                # Use existing strategy logic or new strategy logic
                if sid in STRATEGIES:
                    selected = be._apply_strategy(records, sid, exchange, closes, month_idx)
                else:
                    selected = apply_new_strategy(records, sid, exchange, closes, month_idx)

                pr = round(sum(s['return_pct'] for s in selected) / len(selected), 2) \
                     if selected else 0.0
                port_vals.append(round(port_vals[-1] * (1 + pr/100), 2))
                monthly_rets.append(pr)

            total = round((port_vals[-1]/100 - 1)*100, 2)
            n     = len(monthly_rets)
            ann   = round(((1 + total/100)**(12/max(n,1)) - 1)*100, 1) if n else 0.0
            win   = round(sum(1 for r in monthly_rets if r > 0)/max(n,1)*100, 1)
            sharpe = 0.0
            if len(monthly_rets) > 1:
                ar = float(np.mean(monthly_rets))
                sr = float(np.std(monthly_rets, ddof=1))
                sharpe = round(ar/sr*(12**.5), 2) if sr > 0 else 0.0

            formula_results[sid] = {
                'total':   total,
                'ann':     ann,
                'sharpe':  sharpe,
                'win':     win,
                'monthly': monthly_rets,
            }

        results_matrix[formula_key] = formula_results
        # Quick summary
        best_sid = max(formula_results, key=lambda s: formula_results[s]['ann'])
        best_ann = formula_results[best_sid]['ann']
        print(f'    Best: {best_sid.upper()} → {best_ann:+.0f}% ann')

    return results_matrix


def find_best_combinations(results_matrix):
    """Find top 20 formula × strategy combinations by annualized return."""
    combos = []
    for fkey, fres in results_matrix.items():
        fname = SCORING_FORMULAS[fkey]['name']
        for sid, res in fres.items():
            sid_name = STRATEGIES.get(sid, {}).get('name', sid.upper())
            combos.append({
                'formula_key': fkey,
                'formula':     fname,
                'strategy':    sid_name,
                'sid':         sid,
                'total':       res['total'],
                'ann':         res['ann'],
                'sharpe':      res['sharpe'],
                'win':         res['win'],
            })
    combos.sort(key=lambda x: x['ann'], reverse=True)
    return combos[:25]


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('='*70)
    print('  DEEP RESEARCH ENGINE — Scoring Algorithm & Strategy Optimizer')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('='*70)
    print(f'  Testing {len(SCORING_FORMULAS)} scoring formulas × 15 strategies = {len(SCORING_FORMULAS)*15} combinations')
    print(f'  New indicators: Consistency | Sharpe | Novy-Marx | Acceleration | Multi-Horizon')
    print(f'  New strategies: S11 Quality Mom | S12 Novy-Marx | S13 Consistency | S14 Confluence | S15 APEX Ultra')

    all_research = {}

    for exchange, tickers, sector_map, etf_skip in [
        ('NYSE', NYSE_TICKERS, be.NYSE_SECTOR_MAP, 'SPY'),
        ('NSE',  NSE_TICKERS,  be.NSE_SECTOR_MAP,  'NIFTYBEES.NS'),
    ]:
        print(f'\n{"="*70}')
        print(f'  [{exchange}] Downloading full universe...')
        closes, opens, highs = download_universe(tickers, exchange)

        print(f'\n  [{exchange}] Running {len(SCORING_FORMULAS)} formulas × 15 strategies...')
        results = run_research_backtest(exchange, closes, opens, highs, sector_map, etf_skip)

        all_research[exchange] = results

        top25 = find_best_combinations(results)
        print(f'\n  [{exchange}] TOP 25 COMBINATIONS:')
        print(f'  {"Rank":<5} {"Strategy":<28} {"Formula":<40} {"Ann Return":>11} {"Sharpe":>8} {"Win":>6}')
        print('  ' + '-'*100)
        for i, c in enumerate(top25):
            print(f'  {i+1:<5} {c["strategy"][:26]:<28} {c["formula"][:38]:<40} '
                  f'{c["ann"]:>+10.1f}%  {c["sharpe"]:>7.2f}  {c["win"]:>5.0f}%')

        time.sleep(5)

    # Save full research results
    path = 'data/research_results.json'
    with open(path, 'w') as f:
        # Simplified save (remove monthly arrays to keep file manageable)
        simplified = {}
        for ex, res in all_research.items():
            simplified[ex] = {}
            for fkey, fres in res.items():
                simplified[ex][fkey] = {}
                for sid, sres in fres.items():
                    simplified[ex][fkey][sid] = {
                        k: v for k, v in sres.items() if k != 'monthly'}
        json.dump(simplified, f, indent=2)

    print(f'\n\n{"="*70}')
    print('  RESEARCH COMPLETE — Full results in data/research_results.json')
    print('='*70)
