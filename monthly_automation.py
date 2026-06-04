#!/usr/bin/env python3
"""
Monthly Momentum Builder Automation
=====================================
Runs on the 1st of every month (scheduled via APScheduler in app.py).

What it does automatically:
  1. Checks market regime (NIFTYBEES 12M + India VIX)
  2. Runs NSE + NYSE screeners
  3. Builds all 8 strategies (S1-S8)
  4. Generates rebalance report (what to buy/sell/hold)
  5. Saves report to JSON for display on website
"""

import sys
import os
import json
import logging
from datetime import datetime, date

# Setup
BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, 'automation.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('automation')

REPORT_PATH = os.path.join(DATA_DIR, 'rebalance_report.json')
PREV_HOLD_PATH = os.path.join(DATA_DIR, 'previous_holdings.json')

# Force-run flag
FORCE = os.environ.get('FORCE_RUN', '0') == '1'


def should_run() -> bool:
    """Only run on the 1st of the month (or if forced)."""
    if FORCE:
        log.info('FORCE_RUN=1 — running regardless of date')
        return True
    today = date.today()
    if today.day == 1:
        log.info(f'1st of {today.strftime("%B %Y")} — running monthly automation')
        return True
    log.info(f'Day {today.day} — skipping (not 1st of month)')
    return False


def get_market_regime() -> dict:
    """Fetch NIFTYBEES 12M return and India VIX to determine market mode."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning('yfinance not available — using defaults')
        return {
            'market_12m': 0, 'india_vix': 0, 'is_bull': False,
            'vix_regime': 'normal', 'market_mode': 'BULL',
            'max_stocks': 10, 'action': 'Check market manually'
        }

    log.info('Checking market regime...')

    # NIFTYBEES 12M return
    market_12m = 0.0
    try:
        df = yf.Ticker('NIFTYBEES.NS').history(period='14mo', interval='1mo')
        if len(df) >= 13:
            p_now = float(df['Close'].iloc[-2])
            p_12m = float(df['Close'].iloc[-14]) if len(df) >= 14 else float(df['Close'].iloc[0])
            market_12m = round(((p_now / p_12m) - 1) * 100, 2)
    except Exception as e:
        log.warning(f'NIFTYBEES 12M failed: {e}')

    # India VIX
    india_vix = 0.0
    try:
        tk = yf.Ticker('^INDIAVIX')
        fi = tk.fast_info
        india_vix = float(fi.get('lastPrice', 0) or fi.get('last_price', 0) or 0)
        if india_vix <= 0:
            hist = tk.history(period='5d')
            if not hist.empty:
                india_vix = float(hist['Close'].iloc[-1])
    except Exception as e:
        log.warning(f'India VIX fetch failed: {e}')

    # Determine modes
    is_bull = market_12m > 0
    if india_vix >= 28:
        vix_regime = 'emergency'
        max_stocks = 0
    elif india_vix >= 22:
        vix_regime = 'caution'
        max_stocks = 5
    elif india_vix >= 16:
        vix_regime = 'watchful'
        max_stocks = 7
    else:
        vix_regime = 'normal'
        max_stocks = 10

    # Final market mode
    if not is_bull or vix_regime == 'emergency':
        market_mode = 'BEAR'
        action = 'HOLD LIQUIDBEES — do not buy stocks'
    elif vix_regime == 'caution':
        market_mode = 'CAUTION'
        action = f'Buy only {max_stocks} stocks + keep rest in cash'
    elif vix_regime == 'watchful':
        market_mode = 'WATCHFUL'
        action = f'Buy {max_stocks} stocks (VIX elevated)'
    else:
        market_mode = 'BULL'
        action = f'Full positions — buy top {max_stocks} stocks'

    regime = {
        'market_12m': market_12m,
        'india_vix': round(india_vix, 2),
        'is_bull': is_bull,
        'vix_regime': vix_regime,
        'market_mode': market_mode,
        'max_stocks': max_stocks,
        'action': action,
    }
    log.info(f'Market: {market_mode} | NIFTYBEES 12M={market_12m:+.1f}% | VIX={india_vix:.1f}')
    return regime


def run_screeners() -> dict:
    """Run NSE and NYSE screeners."""
    try:
        from screener import run_screener, load_screener
    except ImportError as e:
        log.error(f'Cannot import screener: {e}')
        return {'NSE': 0, 'NYSE': 0}

    results = {}
    for exchange in ['NSE', 'NYSE']:
        log.info(f'Running {exchange} screener...')
        try:
            run_screener(exchange)
            data = load_screener(exchange)
            count = data['high_momentum_count'] if data else 0
            log.info(f'{exchange} screener done: {count} elite stocks found')
            results[exchange] = count
        except Exception as e:
            log.error(f'{exchange} screener failed: {e}')
            results[exchange] = 0
    return results


def build_strategies() -> dict:
    """Build all 8 strategies for both exchanges."""
    try:
        from screener import load_screener
        from strategy_builder import build_all
    except ImportError as e:
        log.error(f'Cannot import modules: {e}')
        return {}

    results = {}
    for exchange in ['NSE', 'NYSE']:
        sc = load_screener(exchange)
        if not sc or not sc.get('stocks'):
            log.warning(f'{exchange}: no screener data — skipping build')
            continue
        log.info(f'Building all 8 strategies for {exchange}...')
        try:
            pfs = build_all(exchange, sc['stocks'])
            built = [sid for sid, pf in pfs.items() if pf]
            log.info(f'{exchange}: built {built}')
            results[exchange] = pfs
        except Exception as e:
            log.error(f'{exchange} build failed: {e}')
    return results


def generate_rebalance_report(regime: dict, portfolios: dict) -> dict:
    """Generate the actionable rebalance report."""
    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M IST'),
        'month': datetime.now().strftime('%B %Y'),
        'regime': regime,
        'exchanges': {},
    }

    for exchange in ['NSE', 'NYSE']:
        pfs = portfolios.get(exchange, {})
        s8 = pfs.get('s8')
        s5 = pfs.get('s5')
        s7 = pfs.get('s7')

        exch_report = {
            'market_mode': regime['market_mode'],
            'india_vix': regime['india_vix'],
            'market_12m': regime['market_12m'],
            'action_summary': regime['action'],
            'max_stocks': regime['max_stocks'],
        }

        if s8:
            positions = s8.get('positions', [])
            buys = [
                {
                    'symbol': p['symbol'],
                    'company': p.get('company_name', p['symbol']),
                    'sector': p.get('sector', 'N/A'),
                    'score': p.get('score_at_entry', 0),
                    'price': p.get('entry_price', 0),
                }
                for p in positions[:3]
            ]

            sells = []
            holds = []

            exch_report.update({
                'mode': 'INVESTED',
                'buys': buys,
                'sells': sells,
                'holds': holds,
                'total_changes': len(buys) + len(sells),
                'positions': positions,
            })

        if s5:
            exch_report['s5_picks'] = [
                {'symbol': p['symbol'], 'sector': p.get('sector', ''), 'score': p.get('score_at_entry', 0)}
                for p in s5.get('positions', [])[:5]
            ]

        if s7:
            exch_report['s7_picks'] = [
                {'symbol': p['symbol'], 'score': p.get('score_at_entry', 0), 'price': p.get('entry_price', 0)}
                for p in s7.get('positions', [])[:2]
            ]

        report['exchanges'][exchange] = exch_report

    # Save report
    with open(REPORT_PATH, 'w') as f:
        json.dump(report, f, indent=2)
    log.info(f'Rebalance report saved')

    return report


def main():
    log.info('Automation script started')

    if not should_run():
        return

    log.info('=== MONTHLY AUTOMATION STARTING ===')

    try:
        # Step 1: Market regime
        regime = get_market_regime()

        # Step 2: Run screeners
        screener_counts = run_screeners()

        # Step 3: Build strategies
        portfolios = build_strategies()

        # Step 4: Generate report
        report = generate_rebalance_report(regime, portfolios)

        log.info('=== MONTHLY AUTOMATION COMPLETE ===')
        log.info(f'Report: {REPORT_PATH}')

    except Exception as e:
        log.error(f'Automation failed: {e}')
        import traceback
        log.error(traceback.format_exc())


if __name__ == '__main__':
    main()
