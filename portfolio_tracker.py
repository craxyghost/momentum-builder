"""
Portfolio Tracker
=================
Simulates a ₹100 (NSE) / $100 (NYSE) investment in the top-10
momentum stocks at the time of each screener run.

Tracks:
  - Entry price, units bought, current price
  - P&L (absolute + %) per position
  - Portfolio-level totals
  - Monthly history snapshots for return validation

Data stored in data/<exchange>_portfolio.json
History stored in data/<exchange>_portfolio_history.json
"""

import json
import os
import yfinance as yf
from datetime import datetime
from strategy_builder import _batch_prices

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

INVESTMENT_PER_STOCK = 100   # ₹100 or $100


def _portfolio_path(exchange: str) -> str:
    return os.path.join(DATA_DIR, f'{exchange.lower()}_portfolio.json')

def _history_path(exchange: str) -> str:
    return os.path.join(DATA_DIR, f'{exchange.lower()}_portfolio_history.json')


# ── Load / Save ────────────────────────────────────────────────────

def load_portfolio(exchange: str) -> dict | None:
    path = _portfolio_path(exchange)
    return json.load(open(path)) if os.path.exists(path) else None

def load_history(exchange: str) -> list:
    path = _history_path(exchange)
    return json.load(open(path)) if os.path.exists(path) else []


# ── Create ─────────────────────────────────────────────────────────

def create_portfolio(exchange: str, top_stocks: list) -> dict:
    """
    Create a new portfolio from the top-10 momentum stocks.
    Entry price = current price at screener run time.
    Investment = ₹100 or $100 per stock.
    """
    currency = 'INR' if exchange == 'NSE' else 'USD'
    top10 = top_stocks[:10]

    positions = []
    for stock in top10:
        ep = stock['current_price']
        units = round(INVESTMENT_PER_STOCK / ep, 6) if ep > 0 else 0
        positions.append({
            'rank':          stock.get('rank', 0),
            'ticker':        stock['ticker'],
            'symbol':        stock['symbol'],
            'company_name':  stock['company_name'],
            'sector':        stock['sector'],
            'score_at_entry': stock['final_score'],
            'entry_price':   round(ep, 4),
            'units':         units,
            'investment':    INVESTMENT_PER_STOCK,
            'current_price': round(ep, 4),
            'current_value': INVESTMENT_PER_STOCK,
            'pnl':           0.0,
            'pnl_pct':       0.0,
            'entry_date':    datetime.now().strftime('%Y-%m-%d'),
        })

    total_invested = INVESTMENT_PER_STOCK * len(positions)

    portfolio = {
        'exchange':           exchange,
        'currency':           currency,
        'investment_per_stock': INVESTMENT_PER_STOCK,
        'entry_date':         datetime.now().strftime('%Y-%m-%d'),
        'last_updated':       datetime.now().strftime('%Y-%m-%d %H:%M'),
        'positions':          positions,
        'total_invested':     total_invested,
        'total_current_value': total_invested,
        'total_pnl':          0.0,
        'total_pnl_pct':      0.0,
        'best_performer':     None,
        'worst_performer':    None,
    }

    with open(_portfolio_path(exchange), 'w') as f:
        json.dump(portfolio, f, indent=2)

    # Seed history
    _append_history(exchange, portfolio)
    return portfolio


# ── Update ─────────────────────────────────────────────────────────

def update_portfolio(exchange: str) -> dict | None:
    """
    Fetch live prices for every position and recalculate P&L.
    Returns the updated portfolio dict, or None if no portfolio exists.
    """
    pf = load_portfolio(exchange)
    if not pf:
        return None

    inv = pf['investment_per_stock']

    # Batch-fetch all prices in ONE yfinance call (fast, avoids timeout)
    tickers = [pos['ticker'] for pos in pf['positions']]
    prices  = _batch_prices(tickers)

    for pos in pf['positions']:
        cp = prices.get(pos['ticker'], 0)
        if cp <= 0:
            cp = pos['current_price']   # keep last known if fetch failed

        cv  = round(pos['units'] * cp, 4)
        pnl = round(cv - inv, 4)
        pct = round((pnl / inv) * 100, 2) if inv else 0

        pos['current_price'] = round(cp, 4)
        pos['current_value'] = cv
        pos['pnl']           = pnl
        pos['pnl_pct']       = pct

    # Portfolio totals
    total_inv = sum(p['investment'] for p in pf['positions'])
    total_cv  = sum(p['current_value'] for p in pf['positions'])
    total_pnl = round(total_cv - total_inv, 4)
    total_pct = round((total_pnl / total_inv) * 100, 2) if total_inv else 0

    pf['total_invested']      = round(total_inv, 2)
    pf['total_current_value'] = round(total_cv, 4)
    pf['total_pnl']           = total_pnl
    pf['total_pnl_pct']       = total_pct
    pf['last_updated']        = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Best / worst performers
    sorted_pos = sorted(pf['positions'], key=lambda x: x['pnl_pct'], reverse=True)
    pf['best_performer']  = sorted_pos[0]['symbol']  if sorted_pos else None
    pf['worst_performer'] = sorted_pos[-1]['symbol'] if sorted_pos else None

    with open(_portfolio_path(exchange), 'w') as f:
        json.dump(pf, f, indent=2)

    _append_history(exchange, pf)
    return pf


# ── History ────────────────────────────────────────────────────────

def _append_history(exchange: str, pf: dict):
    """Append a snapshot to the monthly history file."""
    history = load_history(exchange)
    snapshot = {
        'date':               datetime.now().strftime('%Y-%m-%d'),
        'total_invested':     pf['total_invested'],
        'total_current_value': pf['total_current_value'],
        'total_pnl':          pf['total_pnl'],
        'total_pnl_pct':      pf['total_pnl_pct'],
        'positions': [
            {
                'symbol':        p['symbol'],
                'pnl_pct':       p['pnl_pct'],
                'current_price': p['current_price'],
            } for p in pf['positions']
        ],
    }
    # Avoid duplicate same-day entries
    if history and history[-1]['date'] == snapshot['date']:
        history[-1] = snapshot
    else:
        history.append(snapshot)

    with open(_history_path(exchange), 'w') as f:
        json.dump(history, f, indent=2)
