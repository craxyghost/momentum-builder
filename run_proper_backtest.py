"""
PROPER Backtest — Uses full screener universe (494 NSE + 869 NYSE stocks)
=========================================================================
Correct process (matches the live screener exactly):

  Step 1 — Download 2 years of monthly OHLCV for ALL stocks in batches of 25
  Step 2 — At each of the 12 historical month-ends, score EVERY stock using
            the exact same 5-indicator formula the live screener uses:
              Price Momentum (30%) + RSI (20%) + MACD (20%) + 52W High (15%) + MA Cross (15%)
  Step 3 — Filter to ELITE zone (score >= 81) — same threshold as live screener
  Step 4 — Apply strategy selection rules to the elite pool
  Step 5 — Record entry (Open) and exit (Close) for the following month

This gives honest, representative backtest results.
"""

import sys, os, time, json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))
os.makedirs('data', exist_ok=True)

from screener import NSE_STOCKS, NYSE_STOCKS
import backtest_engine as be
from strategy_builder import STRATEGIES


# ── Build full ticker lists ────────────────────────────────────────────────────
NSE_TICKERS  = ['NIFTYBEES.NS'] + [s + '.NS' for s in NSE_STOCKS]   # 495 tickers
NYSE_TICKERS = ['SPY']          + list(NYSE_STOCKS)                  # 870 tickers

NSE_SECTOR_MAP  = be.NSE_SECTOR_MAP   # will be extended below
NYSE_SECTOR_MAP = be.NYSE_SECTOR_MAP  # will be extended below


def fetch_batch(tickers: list, label: str, retry: int = 1) -> pd.DataFrame | None:
    """Download one batch of ≤25 tickers. Returns raw MultiIndex DataFrame."""
    for attempt in range(retry + 1):
        try:
            raw = yf.download(
                tickers,
                period='2y',
                interval='1mo',
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if raw is not None and not raw.empty:
                print(f'    {label}: {len(raw)} rows OK')
                return raw
        except Exception as e:
            print(f'    {label} attempt {attempt+1} failed: {e}')
            time.sleep(3)
    return None


def download_universe(tickers: list, exchange: str, batch_size: int = 25,
                      pause: float = 2.0) -> tuple[dict, dict, dict]:
    """
    Download 2 years of monthly OHLCV for ALL tickers in batches of 25.
    Returns (closes_dict, opens_dict, highs_dict) keyed by ticker.
    """
    closes, opens, highs = {}, {}, {}
    batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
    n = len(batches)

    print(f'\n  Downloading {len(tickers)} {exchange} tickers in {n} batches of {batch_size}...')

    for idx, batch in enumerate(batches):
        raw = fetch_batch(batch, f'Batch {idx+1}/{n}', retry=1)
        if raw is None or raw.empty:
            print(f'    Batch {idx+1} empty — skipping')
        else:
            if isinstance(raw.columns, pd.MultiIndex):
                price_types = raw.columns.get_level_values(0).unique()
                if 'Close' in price_types:
                    for ticker in batch:
                        try:
                            c = raw['Close'][ticker].dropna()
                            if len(c) >= 14:
                                closes[ticker] = raw['Close'][ticker]
                                opens[ticker]  = raw['Open'][ticker]  if 'Open' in price_types else raw['Close'][ticker]
                                highs[ticker]  = raw['High'][ticker]  if 'High' in price_types else raw['Close'][ticker]
                        except (KeyError, TypeError):
                            pass
            elif len(batch) == 1:
                t = batch[0]
                if len(raw) >= 14 and 'Close' in raw.columns:
                    closes[t] = raw['Close']
                    opens[t]  = raw['Open']  if 'Open' in raw.columns  else raw['Close']
                    highs[t]  = raw['High']  if 'High'  in raw.columns else raw['Close']

        if idx < n - 1:
            time.sleep(pause)

    print(f'  Done: {len(closes)}/{len(tickers)} tickers with valid data')
    return closes, opens, highs


def run_proper_backtest(exchange: str, closes: dict, opens: dict, highs: dict,
                        sector_map: dict, etf_skip: str) -> dict:
    """
    Run 12-month backtest using the full scored universe.
    At each month:
      1. Score ALL stocks (exact same 5-indicator formula)
      2. Filter to ELITE >= 81 (same as live screener)
      3. Apply strategy selection
    Returns dict of {sid: result} for all 10 strategies.
    """
    if not closes:
        print(f'  No data for {exchange}!')
        return {}

    sample   = next(iter(closes.values()))
    time_idx = sample.index

    now        = pd.Timestamp.now()
    last_month = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    targets    = [last_month - pd.DateOffset(months=i) for i in range(11, -1, -1)]

    # Pre-compute monthly records for all months (score once, reuse for all strategies)
    print(f'  Scoring {len(closes)} stocks across {len(targets)} months...')
    all_month_records = []

    for target in targets:
        month_idx = next(
            (i for i, ts in enumerate(time_idx)
             if ts.year == target.year and ts.month == target.month), None)
        if month_idx is None:
            all_month_records.append(None)
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

            score, macd_s, rsi_s, h52_s = be._score_stock(c_sl, h_sl)

            try:
                ep = float(opens.get(ticker, closes[ticker]).iloc[month_idx])
                xp = float(closes[ticker].iloc[month_idx])
            except (IndexError, TypeError):
                continue
            if ep <= 0 or xp <= 0 or pd.isna(ep) or pd.isna(xp):
                continue

            records.append({
                'ticker':      ticker,
                'symbol':      ticker.replace('.NS', '').replace('.BO', ''),
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

        # --- KEY STEP: Elite filter (score >= 81), exactly like live screener
        elite_count = sum(1 for r in records if r['score'] >= 81)
        all_month_records.append({
            'target':      target,
            'month_idx':   month_idx,
            'records':     records,
            'elite_count': elite_count,
            'total_count': len(records),
        })

        print(f'    {target.strftime("%b %Y")}: {len(records)} stocks scored, '
              f'{elite_count} elite (>=81)')

    # Run each strategy over the pre-scored months
    results = {}
    for sid in STRATEGIES:
        print(f'\n  Running {exchange} {sid.upper()}...')
        monthly_results  = []
        portfolio_values = [100.0]

        for month_data in all_month_records:
            if month_data is None:
                continue

            records   = month_data['records']
            month_idx = month_data['month_idx']
            target    = month_data['target']

            selected = be._apply_strategy(
                records, sid, exchange, closes, month_idx)

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
                'elite_pool_size':      month_data['elite_count'],
                'total_scored':         month_data['total_count'],
                'best_stock':  max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
                'worst_stock': min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
                'best_return': round(max(s['return_pct'] for s in selected), 2) if selected else 0,
                'worst_return':round(min(s['return_pct'] for s in selected), 2) if selected else 0,
            })

        rets  = [m['portfolio_return_pct'] for m in monthly_results]
        n     = len(monthly_results)
        total = round((portfolio_values[-1] / 100 - 1) * 100, 2)
        ann   = round(((1 + total/100)**(12/max(n,1)) - 1)*100, 1) if n else 0.0
        win   = round(sum(1 for r in rets if r > 0) / max(len(rets),1) * 100, 1)

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
            'strategy_id': sid, 'strategy_name': st.get('name', sid.upper()),
            'strategy_icon': st.get('icon', '📊'), 'exchange': exchange,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'backtest_note': f'Full universe: {len(closes)} stocks scored, elite >=81 filter applied',
            'period': {
                'start':  monthly_results[0]['month']  if monthly_results else '',
                'end':    monthly_results[-1]['month'] if monthly_results else '',
                'months': n,
            },
            'monthly_results': monthly_results, 'portfolio_values': portfolio_values,
            'summary': {
                'total_return_pct': total, 'annualized_return': ann,
                'win_rate': win, 'max_drawdown': round(max_dd, 2),
                'sharpe_ratio': sharpe,
                'best_month': monthly_results[bmi]['month']  if rets else '',
                'best_month_return': max(rets) if rets else 0,
                'worst_month': monthly_results[wmi]['month'] if rets else '',
                'worst_month_return': min(rets) if rets else 0,
                'total_trades': sum(m['num_stocks'] for m in monthly_results),
                'universe_size': len(closes),
                'avg_elite_pool': round(
                    sum(m.get('elite_pool_size', 0) for m in monthly_results) / max(n, 1), 0),
            },
        }

        path = f'data/backtest_{exchange.lower()}_{sid}.json'
        with open(path, 'w') as f:
            json.dump(result, f, indent=2)

        print(f'    {exchange} {sid.upper()}: {total:+.1f}% total | '
              f'{ann:+.0f}% ann | Sharpe {sharpe:.2f} | '
              f'Win {win:.0f}% | Avg elite pool: '
              f'{result["summary"]["avg_elite_pool"]:.0f} stocks')
        results[sid] = result

    return results


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('='*65)
    print('  PROPER BACKTEST — Full Screener Universe')
    print(f'  NSE: {len(NSE_TICKERS)} tickers | NYSE: {len(NYSE_TICKERS)} tickers')
    print('='*65)

    # ── NSE ───────────────────────────────────────────────────────────────────
    print('\n[NSE] Downloading full universe...')
    nse_closes, nse_opens, nse_highs = download_universe(
        NSE_TICKERS, 'NSE', batch_size=25, pause=2.0)

    print('\n[NSE] Running all 10 strategies...')
    nse_results = run_proper_backtest(
        'NSE', nse_closes, nse_opens, nse_highs,
        NSE_SECTOR_MAP, 'NIFTYBEES.NS')

    # Polite pause between exchanges
    print('\nPausing 10s before NYSE download...')
    time.sleep(10)

    # ── NYSE ──────────────────────────────────────────────────────────────────
    print('\n[NYSE] Downloading full universe...')
    nyse_closes, nyse_opens, nyse_highs = download_universe(
        NYSE_TICKERS, 'NYSE', batch_size=25, pause=2.0)

    print('\n[NYSE] Running all 10 strategies...')
    nyse_results = run_proper_backtest(
        'NYSE', nyse_closes, nyse_opens, nyse_highs,
        NYSE_SECTOR_MAP, 'SPY')

    # ── Summary ───────────────────────────────────────────────────────────────
    print('\n' + '='*65)
    print('  FINAL RESULTS SUMMARY')
    print('='*65)
    print(f'\n{"Strategy":<20} {"NSE Total":>10} {"NYSE Total":>11} {"NSE Ann":>9} {"NYSE Ann":>10}')
    print('-'*65)
    for sid in STRATEGIES:
        nr = nse_results.get(sid, {}).get('summary', {})
        yr = nyse_results.get(sid, {}).get('summary', {})
        name = STRATEGIES[sid]['name'][:18]
        print(f'{name:<20} '
              f'{nr.get("total_return_pct",0):>+9.1f}%  '
              f'{yr.get("total_return_pct",0):>+10.1f}%  '
              f'{nr.get("annualized_return",0):>+8.0f}%  '
              f'{yr.get("annualized_return",0):>+9.0f}%')

    print('\nAll JSON files saved to data/ folder.')
