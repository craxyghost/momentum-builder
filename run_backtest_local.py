"""
Run backtests locally with expanded 50-stock universe.
Uses 2 batches of 25 to avoid Yahoo Finance throttling.
"""
import sys, os, time, json
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

os.makedirs('data', exist_ok=True)

# ── Expanded 50-stock universes (2 batches each) ─────────────────────────────

NSE_B1 = [
    'NIFTYBEES.NS',
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'HINDUNILVR.NS', 'SBIN.NS', 'BAJFINANCE.NS', 'BHARTIARTL.NS', 'KOTAKBANK.NS',
    'ASIANPAINT.NS', 'MARUTI.NS', 'TITAN.NS', 'SUNPHARMA.NS', 'WIPRO.NS',
    'HCLTECH.NS', 'TATASTEEL.NS', 'NTPC.NS', 'ITC.NS', 'DRREDDY.NS',
    'EICHERMOT.NS', 'BAJAJFINSV.NS', 'POWERGRID.NS', 'ONGC.NS',
]
NSE_B2 = [
    'NESTLEIND.NS', 'ULTRACEMCO.NS', 'DIVISLAB.NS', 'CIPLA.NS', 'AXISBANK.NS',
    'INDUSINDBK.NS', 'HEROMOTOCO.NS', 'BRITANNIA.NS', 'PIDILITIND.NS', 'HAVELLS.NS',
    'DABUR.NS', 'MARICO.NS', 'COLPAL.NS', 'TATACONSUM.NS', 'APOLLOHOSP.NS',
    'MUTHOOTFIN.NS', 'CHOLAFIN.NS', 'TORNTPHARM.NS', 'LUPIN.NS', 'AUROPHARMA.NS',
    'TATAPOWER.NS', 'SIEMENS.NS', 'BEL.NS', 'HAL.NS', 'ADANIPORTS.NS',
]

NYSE_B1 = [
    'SPY',
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'JPM', 'JNJ',
    'V', 'PG', 'UNH', 'MA', 'BAC', 'ADBE', 'NFLX', 'AMD', 'XOM', 'LLY',
    'CRWD', 'COST', 'AVGO', 'QCOM', 'CRM',
]
NYSE_B2 = [
    'MRK', 'ABBV', 'WMT', 'CVX', 'PFE', 'NKE', 'MCD', 'KO', 'ORCL', 'TXN',
    'HON', 'CAT', 'GS', 'MS', 'AXP', 'BLK', 'AMGN', 'GILD', 'REGN', 'VRTX',
    'NEE', 'DUK', 'AMT', 'EQIX', 'SNOW',
]


def fetch_batch(tickers, label):
    print(f'  Fetching {label} ({len(tickers)} tickers)...')
    try:
        raw = yf.download(
            tickers, period='2y', interval='1mo',
            progress=False, auto_adjust=True, threads=False
        )
        print(f'  Got {len(raw)} rows')
        return raw
    except Exception as e:
        print(f'  ERROR: {e}')
        return None


def merge_batches(raw1, raw2, tickers1, tickers2):
    """Merge two MultiIndex DataFrames into combined closes/opens/highs dicts."""
    closes, opens, highs = {}, {}, {}

    for raw, tickers in [(raw1, tickers1), (raw2, tickers2)]:
        if raw is None or raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            for ticker in tickers:
                try:
                    c = raw['Close'][ticker].dropna()
                    if len(c) >= 14:
                        closes[ticker] = raw['Close'][ticker]
                        opens[ticker]  = raw['Open'][ticker]
                        highs[ticker]  = raw['High'][ticker]
                except (KeyError, TypeError):
                    pass
        else:
            # Single ticker
            if len(tickers) == 1 and len(raw) >= 14:
                t = tickers[0]
                closes[t] = raw['Close']
                opens[t]  = raw['Open']
                highs[t]  = raw['High']

    print(f'  Valid tickers: {len(closes)}')
    return closes, opens, highs


# Patch backtest_engine to accept pre-fetched data
import numpy as np
from datetime import datetime
from collections import defaultdict
import backtest_engine as be

def run_with_data(exchange, sid, closes, opens, highs):
    """Run backtest using pre-fetched data dicts."""
    from strategy_builder import STRATEGIES

    sector_map = be.NSE_SECTOR_MAP if exchange == 'NSE' else be.NYSE_SECTOR_MAP
    etf_skip   = 'NIFTYBEES.NS'   if exchange == 'NSE' else 'SPY'

    sample   = next(iter(closes.values()))
    time_idx = sample.index

    now        = pd.Timestamp.now()
    last_month = pd.Timestamp(year=now.year, month=now.month, day=1) - pd.DateOffset(months=1)
    targets    = [last_month - pd.DateOffset(months=i) for i in range(11, -1, -1)]

    monthly_results  = []
    portfolio_values = [100.0]

    for target in targets:
        month_idx = next(
            (i for i, ts in enumerate(time_idx)
             if ts.year == target.year and ts.month == target.month), None)
        if month_idx is None:
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
                'ticker': ticker,
                'symbol': ticker.replace('.NS','').replace('.BO',''),
                'sector': sector_map.get(ticker, 'Other'),
                'score':  score, 'macd_score': macd_s,
                'rsi_score': rsi_s, 'h52_score': h52_s,
                'entry_price': round(ep, 2), 'exit_price': round(xp, 2),
                'return_pct':  round((xp - ep) / ep * 100, 2),
            })

        records.sort(key=lambda x: x['score'], reverse=True)
        selected  = be._apply_strategy(records, sid, exchange, closes, score_cutoff)
        port_ret  = round(sum(s['return_pct'] for s in selected) / len(selected), 2) if selected else 0.0
        new_val   = round(portfolio_values[-1] * (1 + port_ret / 100), 2)
        portfolio_values.append(new_val)

        sectors = defaultdict(int)
        for s in selected: sectors[s['sector']] += 1

        monthly_results.append({
            'month': target.strftime('%b %Y'), 'month_key': target.strftime('%Y-%m'),
            'stocks': selected, 'portfolio_return_pct': port_ret,
            'portfolio_value': new_val, 'num_stocks': len(selected),
            'sectors': dict(sectors), 'mode': 'INVESTED' if selected else 'CASH',
            'best_stock':  max(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'worst_stock': min(selected, key=lambda x: x['return_pct'])['symbol'] if selected else None,
            'best_return': round(max(s['return_pct'] for s in selected), 2) if selected else 0,
            'worst_return':round(min(s['return_pct'] for s in selected), 2) if selected else 0,
        })

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
        'strategy_id': sid, 'strategy_name': st.get('name', sid.upper()),
        'strategy_icon': st.get('icon', '📊'), 'exchange': exchange,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'period': {
            'start':  monthly_results[0]['month']  if monthly_results else '',
            'end':    monthly_results[-1]['month'] if monthly_results else '',
            'months': n,
        },
        'monthly_results': monthly_results, 'portfolio_values': portfolio_values,
        'summary': {
            'total_return_pct': total_pct, 'annualized_return': annualised,
            'win_rate': win_rate, 'max_drawdown': round(max_dd, 2),
            'sharpe_ratio': sharpe,
            'best_month': monthly_results[bmi]['month']  if rets else '',
            'best_month_return': max(rets) if rets else 0,
            'worst_month': monthly_results[wmi]['month'] if rets else '',
            'worst_month_return': min(rets) if rets else 0,
            'total_trades': sum(m['num_stocks'] for m in monthly_results),
            'universe_size': len(closes),
        },
    }

    path = f'data/backtest_{exchange.lower()}_{sid}.json'
    with open(path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f'  {exchange} {sid.upper()}: {total_pct:+.1f}% total | '
          f'{annualised:+.0f}% ann | Sharpe {sharpe:.2f} | '
          f'Win {win_rate:.0f}% | Stocks/mo: {len(closes)-1}')
    return result


# ── Main ──────────────────────────────────────────────────────────────────────
strategies = ['s1','s2','s3','s4','s5','s6','s7','s8','s9','s10']

print('\n' + '='*60)
print('  NSE Backtest (50-stock universe, 2 batches)')
print('='*60)
nse_raw1 = fetch_batch(NSE_B1, 'NSE Batch 1/2')
time.sleep(3)
nse_raw2 = fetch_batch(NSE_B2, 'NSE Batch 2/2')
nse_closes, nse_opens, nse_highs = merge_batches(nse_raw1, nse_raw2, NSE_B1, NSE_B2)

print()
for sid in strategies:
    run_with_data('NSE', sid, nse_closes, nse_opens, nse_highs)

print('\n' + '='*60)
print('  NYSE Backtest (50-stock universe, 2 batches)')
print('='*60)
time.sleep(5)   # be polite to Yahoo Finance between exchanges
nyse_raw1 = fetch_batch(NYSE_B1, 'NYSE Batch 1/2')
time.sleep(3)
nyse_raw2 = fetch_batch(NYSE_B2, 'NYSE Batch 2/2')
nyse_closes, nyse_opens, nyse_highs = merge_batches(nyse_raw1, nyse_raw2, NYSE_B1, NYSE_B2)

print()
for sid in strategies:
    run_with_data('NYSE', sid, nyse_closes, nyse_opens, nyse_highs)

print('\n\nAll 20 JSON files updated in data/ folder.')
