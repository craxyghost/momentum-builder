"""
June 2026 Partial-Month Backtest (Jun 1–4, 2026)
=================================================
Since June 2026 is in progress (only 4 trading days), this shows:
  - Scores computed from May 2026 month-end data (what you'd have on Jun 1)
  - Entry price = June 1 Open (or first available day)
  - Current price = June 4 Close (latest)
  - Partial return = (current - entry) / entry × 100
Full universe: 494 NSE stocks + 869 NYSE stocks scored.
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

NSE_TICKERS  = ['NIFTYBEES.NS'] + [s + '.NS' for s in NSE_STOCKS]
NYSE_TICKERS = ['SPY'] + list(NYSE_STOCKS)
NSE_SM  = be.NSE_SECTOR_MAP
NYSE_SM = be.NYSE_SECTOR_MAP


def fetch_batch(tickers, label):
    try:
        raw = yf.download(tickers, period='2y', interval='1mo',
                          progress=False, auto_adjust=True, threads=False)
        print(f'    {label}: {len(raw)} monthly rows')
        return raw
    except Exception as e:
        print(f'    {label} error: {e}')
        return None


def fetch_daily_june(tickers, label):
    """Fetch daily data for June 2026 to get actual entry and current prices."""
    try:
        raw = yf.download(tickers, start='2026-06-01', end='2026-06-10',
                          progress=False, auto_adjust=True, threads=False)
        print(f'    {label} daily: {len(raw)} days of June data')
        return raw
    except Exception as e:
        print(f'    {label} daily error: {e}')
        return None


def download_universe(tickers, exchange, batch_size=25, pause=2.0):
    closes, opens, highs = {}, {}, {}
    batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
    print(f'  [{exchange}] {len(tickers)} tickers in {len(batches)} batches...')
    for idx, batch in enumerate(batches):
        raw = fetch_batch(batch, f'Batch {idx+1}/{len(batches)}')
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                for t in batch:
                    try:
                        c = raw['Close'][t].dropna()
                        if len(c) >= 14:
                            closes[t] = raw['Close'][t]
                            opens[t]  = raw['Open'][t]
                            highs[t]  = raw['High'][t]
                    except (KeyError, TypeError):
                        pass
            elif len(batch) == 1:
                t = batch[0]
                if len(raw) >= 14:
                    closes[t] = raw.get('Close', raw.iloc[:,0])
                    opens[t]  = raw.get('Open',  raw.iloc[:,0])
                    highs[t]  = raw.get('High',  raw.iloc[:,0])
        if idx < len(batches)-1:
            time.sleep(pause)
    print(f'  [{exchange}] {len(closes)} valid tickers')
    return closes, opens, highs


def download_daily_prices(tickers, exchange, batch_size=25, pause=2.0):
    """Fetch June 2026 daily prices to get entry (Jun 1 open) and current (Jun 4 close)."""
    entry_prices   = {}
    current_prices = {}
    batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
    for idx, batch in enumerate(batches):
        try:
            raw = yf.download(batch, start='2026-06-01', end='2026-06-06',
                              interval='1d', progress=False, auto_adjust=True, threads=False)
            if raw is not None and not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    for t in batch:
                        try:
                            op = raw['Open'][t].dropna()
                            cl = raw['Close'][t].dropna()
                            if len(op) > 0: entry_prices[t]   = float(op.iloc[0])
                            if len(cl) > 0: current_prices[t] = float(cl.iloc[-1])
                        except (KeyError, TypeError):
                            pass
                else:
                    if len(batch) == 1:
                        t = batch[0]
                        if 'Open'  in raw.columns and len(raw) > 0: entry_prices[t]   = float(raw['Open'].iloc[0])
                        if 'Close' in raw.columns and len(raw) > 0: current_prices[t] = float(raw['Close'].iloc[-1])
        except Exception as e:
            pass
        if idx < len(batches)-1:
            time.sleep(pause)
    return entry_prices, current_prices


def run_june_backtest(exchange, closes, opens, highs, sector_map, etf_skip,
                      entry_prices, current_prices):
    """
    Score using May 2026 data (last complete month).
    Entry = June 1 open, Current = June 4 close.
    """
    sample   = next(iter(closes.values()))
    time_idx = sample.index

    # Find May 2026 position (last complete month)
    may_idx = next((i for i, ts in enumerate(time_idx)
                    if ts.year == 2026 and ts.month == 5), None)
    if may_idx is None:
        # Try April 2026 as fallback
        may_idx = next((i for i, ts in enumerate(time_idx)
                        if ts.year == 2026 and ts.month == 4), None)
    if may_idx is None:
        print(f'  [{exchange}] Cannot find May 2026 in data!')
        return {}

    score_cutoff = may_idx + 1   # Include May, score using all data up to May end

    print(f'\n  [{exchange}] Scoring {len(closes)} stocks using May 2026 month-end data...')

    records = []
    for ticker in closes:
        if ticker == etf_skip:
            continue
        c_sl = closes[ticker].iloc[:score_cutoff].dropna()
        h_sl = highs.get(ticker, closes[ticker]).iloc[:score_cutoff].dropna()
        if len(c_sl) < 6:
            continue

        score, macd_s, rsi_s, h52_s = be._score_stock(c_sl, h_sl)

        ep = entry_prices.get(ticker, 0)
        xp = current_prices.get(ticker, 0)

        if ep <= 0 or xp <= 0:
            # Fallback: use May close as entry approximation
            try:
                ep = float(closes[ticker].iloc[may_idx]) if may_idx < len(closes[ticker]) else 0
                xp = ep
            except:
                continue

        if ep <= 0:
            continue

        ret = round((xp - ep) / ep * 100, 2) if ep > 0 and xp > 0 else 0.0
        sym = ticker.replace('.NS','').replace('.BO','')
        curr_sym = '₹' if exchange == 'NSE' else '$'

        records.append({
            'ticker':        ticker,
            'symbol':        sym,
            'sector':        sector_map.get(ticker, 'Other'),
            'score':         score,
            'macd_score':    macd_s,
            'rsi_score':     rsi_s,
            'h52_score':     h52_s,
            'entry_price':   round(ep, 2),
            'exit_price':    round(xp, 2),
            'return_pct':    ret,
        })

    records.sort(key=lambda x: x['score'], reverse=True)
    elite_count = sum(1 for r in records if r['score'] >= 81)
    print(f'  [{exchange}] {len(records)} scored | {elite_count} elite (>=81)')

    results = {}
    for sid in STRATEGIES:
        selected = be._apply_strategy(records, sid, exchange, closes, score_cutoff)
        port_ret = round(sum(s['return_pct'] for s in selected) / len(selected), 2) if selected else 0.0

        st = STRATEGIES.get(sid, {})
        result = {
            'strategy_id':   sid,
            'strategy_name': st.get('name', sid.upper()),
            'strategy_icon': st.get('icon', '📊'),
            'exchange':      exchange,
            'generated_at':  datetime.now().strftime('%Y-%m-%d %H:%M'),
            'note':          'PARTIAL MONTH — Jun 1–4 2026 only (4 trading days)',
            'period':        {'start': 'Jun 2026', 'end': 'Jun 4 2026 (partial)', 'months': 1},
            'monthly_results': [{
                'month':                'Jun 2026 (partial)',
                'month_key':            '2026-06',
                'stocks':               selected,
                'portfolio_return_pct': port_ret,
                'portfolio_value':      round(100 * (1 + port_ret/100), 2),
                'num_stocks':           len(selected),
                'sectors':              dict(defaultdict(int, {s['sector']: 1 for s in selected})),
                'mode':                 'INVESTED' if selected else 'CASH',
                'best_stock':  max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
                'worst_stock': min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
                'best_return': round(max(s['return_pct'] for s in selected), 2) if selected else 0,
                'worst_return':round(min(s['return_pct'] for s in selected), 2) if selected else 0,
            }],
            'portfolio_values': [100.0, round(100 * (1 + port_ret/100), 2)],
            'summary': {
                'total_return_pct':   port_ret,
                'annualized_return':  round(port_ret * 12, 1),   # rough annualisation
                'win_rate':           100.0 if port_ret > 0 else 0.0,
                'max_drawdown':       min(port_ret, 0),
                'sharpe_ratio':       0.0,
                'best_month':         'Jun 2026',
                'best_month_return':  port_ret,
                'worst_month':        'Jun 2026',
                'worst_month_return': port_ret,
                'total_trades':       len(selected),
                'universe_size':      len(closes),
                'elite_pool_size':    elite_count,
            },
        }

        mode = 'INVESTED' if selected else 'CASH'
        print(f'    {sid.upper()}: {port_ret:+.2f}% ({len(selected)} stocks, {mode})')
        results[sid] = result

    # Save as special June file (don't overwrite full 12M backtest)
    path = f'data/backtest_{exchange.lower()}_june2026.json'
    with open(path, 'w') as f:
        json.dump({
            'exchange': exchange,
            'month': 'June 2026 (partial — Jun 1-4)',
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'elite_pool_size': elite_count,
            'universe_size': len(closes),
            'strategies': {sid: r['monthly_results'][0] | {'summary': r['summary']}
                           for sid, r in results.items()},
            'top_elite_stocks': records[:20],   # top 20 by score right now
        }, f, indent=2)

    return results


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('='*65)
    print('  JUNE 2026 PARTIAL BACKTEST + MARKET REGIME CHECK')
    print(f'  Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('='*65)

    # Market regime
    def get_12m(ticker):
        try:
            df = yf.Ticker(ticker).history(period='14mo', interval='1mo', auto_adjust=True)
            if len(df) >= 13:
                return round((float(df['Close'].iloc[-1]) / float(df['Close'].iloc[-13]) - 1)*100, 2)
        except: pass
        return 0.0

    def get_vix(ticker):
        try:
            df = yf.Ticker(ticker).history(period='5d', auto_adjust=True)
            return round(float(df['Close'].iloc[-1]), 1) if not df.empty else 0.0
        except: return 0.0

    nse_12m    = get_12m('NIFTYBEES.NS')
    india_vix  = get_vix('^INDIAVIX')
    nyse_12m   = get_12m('SPY')
    us_vix     = get_vix('^VIX')

    print(f'\n  🇮🇳 NSE: 12M={nse_12m:+.1f}% | VIX={india_vix:.1f} | '
          f'Mode={"BULL 🟢" if nse_12m > 0 else "BEAR 🔴"}')
    print(f'  🇺🇸 NYSE: 12M={nyse_12m:+.1f}% | VIX={us_vix:.1f} | '
          f'Mode={"BULL 🟢" if nyse_12m > 0 else "BEAR 🔴"}')

    # NSE
    print('\n[NSE] Downloading full universe for June scoring...')
    nse_c, nse_o, nse_h = download_universe(NSE_TICKERS, 'NSE')
    print('\n[NSE] Getting June 1-4 daily prices...')
    nse_entry, nse_cur = download_daily_prices(NSE_TICKERS, 'NSE')
    print(f'  NSE daily prices: {len(nse_entry)} entry / {len(nse_cur)} current')
    print('\n[NSE] Running June 2026 strategies...')
    nse_res = run_june_backtest('NSE', nse_c, nse_o, nse_h, NSE_SM,
                                 'NIFTYBEES.NS', nse_entry, nse_cur)

    time.sleep(10)

    # NYSE
    print('\n[NYSE] Downloading full universe for June scoring...')
    nyse_c, nyse_o, nyse_h = download_universe(NYSE_TICKERS, 'NYSE')
    print('\n[NYSE] Getting June 1-4 daily prices...')
    nyse_entry, nyse_cur = download_daily_prices(NYSE_TICKERS, 'NYSE')
    print(f'  NYSE daily prices: {len(nyse_entry)} entry / {len(nyse_cur)} current')
    print('\n[NYSE] Running June 2026 strategies...')
    nyse_res = run_june_backtest('NYSE', nyse_c, nyse_o, nyse_h, NYSE_SM,
                                  'SPY', nyse_entry, nyse_cur)

    print('\n' + '='*65)
    print('  JUNE 2026 SUMMARY (4 trading days)')
    print('='*65)
    print(f'\n  Market: NSE {"BEAR 🔴" if nse_12m < 0 else "BULL 🟢"} ({nse_12m:+.1f}% 12M) | '
          f'NYSE {"BULL 🟢" if nyse_12m > 0 else "BEAR 🔴"} ({nyse_12m:+.1f}% 12M)\n')
    print(f'  {"Strategy":<22} {"NSE Jun":>9}  {"NYSE Jun":>10}  {"NSE Picks":>10}  {"NYSE Picks":>11}')
    print('  ' + '-'*65)
    for sid in STRATEGIES:
        nr = nse_res.get(sid, {})
        yr = nyse_res.get(sid, {})
        nm = nr.get('monthly_results', [{}])[0]
        ym = yr.get('monthly_results', [{}])[0]
        np_ = nm.get('portfolio_return_pct', 0)
        yp  = ym.get('portfolio_return_pct', 0)
        nn  = nm.get('num_stocks', 0)
        yn  = ym.get('num_stocks', 0)
        name = STRATEGIES[sid]['name'][:20]
        print(f'  {name:<22} {np_:>+8.2f}%  {yp:>+9.2f}%  {nn:>9} stocks  {yn:>10} stocks')

    print(f'\n  Files saved: data/backtest_nse_june2026.json, data/backtest_nyse_june2026.json')
