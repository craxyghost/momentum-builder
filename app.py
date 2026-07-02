"""
Momentum Builder — Flask Web Application
=========================================
Personal long-term momentum investing tool.
Supports BSE (.BO), NSE (.NS), NYSE/NASDAQ stocks via Yahoo Finance.

New in v2:
  • /screener/nse  — NSE Elite Screener (score 81-100), auto-refresh monthly
  • /screener/nyse — NYSE Elite Screener (score 81-100), auto-refresh monthly
  • /portfolio     — ₹100/$100 investment tracker for top-10 momentum stocks
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from indicators import calculate_all_indicators, INDICATOR_META, INDICATOR_WEIGHTS
from screener import (
    run_screener_background, load_screener, get_progress,
    run_screener, NSE_STOCKS, NYSE_STOCKS
)
from portfolio_tracker import (
    create_portfolio, update_portfolio, load_portfolio, load_history
)
from strategy_builder import (
    STRATEGIES, build_all, update_all, update_strategy, update_all_async,
    update_strategy_async, get_refresh_status,
    load_all_strategies, load_all_histories, load_strategy, load_history as load_strat_history
)
import traceback
import threading
from datetime import datetime, timedelta

# ── APScheduler for monthly auto-refresh ──────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler(daemon=True)

    def _monthly_nse():
        print(f'[Scheduler] Running monthly NSE screener — {datetime.now()}')
        run_screener('NSE')

    def _monthly_nyse():
        print(f'[Scheduler] Running monthly NYSE screener — {datetime.now()}')
        run_screener('NYSE')

    # Runs every 30 days (interval-based; use cron trigger in production)
    scheduler.add_job(_monthly_nse,  IntervalTrigger(days=30), id='nse_monthly',
                      next_run_time=None)   # won't fire immediately on start
    scheduler.add_job(_monthly_nyse, IntervalTrigger(days=30), id='nyse_monthly',
                      next_run_time=None)

    scheduler.start()
    SCHEDULER_ACTIVE = True
except ImportError:
    SCHEDULER_ACTIVE = False
    print('[Warning] APScheduler not installed. Monthly auto-refresh disabled.')
    print('          Run:  pip install apscheduler')


app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ── Simple HTTP Basic Auth (private personal use only) ─────────────
@app.before_request
def _require_basic_auth():
  import os
  from flask import Response
  auth_user = os.environ.get('BASIC_AUTH_USER')
  auth_pass = os.environ.get('BASIC_AUTH_PASS')
  if not auth_user or not auth_pass:
    return  # auth disabled unless both env vars are set
  auth = request.authorization
  if not auth or auth.username != auth_user or auth.password != auth_pass:
    return Response('Authentication required', 401,
                      {'WWW-Authenticate': 'Basic realm="Momentum Builder"'})
      

# ── Exchange detection helpers ─────────────────────────────────────

EXCHANGE_HINTS = {
    'BSE': '.BO', 'NSE': '.NS', 'NYSE': '', 'NASDAQ': '', 'AUTO': None
}


def build_ticker(symbol: str, exchange: str) -> str:
    symbol = symbol.strip().upper().replace(' ', '')
    exchange = exchange.strip().upper()
    if '.' in symbol:
        return symbol
    suffix = EXCHANGE_HINTS.get(exchange, '')
    return symbol + suffix if suffix else symbol


def try_ticker_variants(symbol: str, exchange: str) -> tuple[str, dict]:
    symbol = symbol.strip().upper().replace(' ', '')
    exchange = exchange.strip().upper()

    if exchange not in ('AUTO', ''):
        ticker = build_ticker(symbol, exchange)
        return ticker, calculate_all_indicators(ticker)

    variants = [symbol + '.NS', symbol + '.BO', symbol] if '.' not in symbol else [symbol]
    last_error = None
    for variant in variants:
        try:
            return variant, calculate_all_indicators(variant)
        except Exception as e:
            last_error = e
    raise last_error or ValueError(f"Could not find data for '{symbol}'")


# ── Helper: check if screener data is stale (> 30 days old) ───────

def _is_stale(screener_data: dict) -> bool:
    if not screener_data:
        return True
    try:
        last = datetime.strptime(screener_data['last_run'], '%Y-%m-%d %H:%M')
        return (datetime.now() - last).days >= 30
    except Exception:
        return True


# ── Routes ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ── Screener pages ─────────────────────────────────────────────────

@app.route('/screener/nse')
def screener_nse():
    data = load_screener('NSE')
    progress = get_progress('NSE')
    return render_template('screener.html',
                           exchange='NSE',
                           currency='INR',
                           currency_symbol='₹',
                           flag='🇮🇳',
                           screener=data,
                           progress=progress,
                           total_stocks=len(NSE_STOCKS))


@app.route('/screener/nyse')
def screener_nyse():
    data = load_screener('NYSE')
    progress = get_progress('NYSE')
    return render_template('screener.html',
                           exchange='NYSE',
                           currency='USD',
                           currency_symbol='$',
                           flag='🇺🇸',
                           screener=data,
                           progress=progress,
                           total_stocks=len(NYSE_STOCKS))


# ── Portfolio Tracker page ─────────────────────────────────────────

@app.route('/portfolio')
def portfolio():
    nse_pf  = load_portfolio('NSE')
    nyse_pf = load_portfolio('NYSE')
    nse_hist  = load_history('NSE')
    nyse_hist = load_history('NYSE')
    return render_template('portfolio.html',
                           nse_portfolio=nse_pf,
                           nyse_portfolio=nyse_pf,
                           nse_history=nse_hist,
                           nyse_history=nyse_hist)


# ── 5-Strategy Comparison page ─────────────────────────────────────
@app.route('/strategies')
def strategies_page():
    exchange = request.args.get('exchange', 'NSE').upper()
    all_pf   = load_all_strategies(exchange)
    all_hist = load_all_histories(exchange)
    nse_has  = load_screener('NSE') is not None
    nyse_has = load_screener('NYSE') is not None
    return render_template('strategies.html',
                           exchange=exchange,
                           strategies=STRATEGIES,
                           portfolios=all_pf,
                           histories=all_hist,
                           nse_has_screener=nse_has,
                           nyse_has_screener=nyse_has)


# ── Strategy API ───────────────────────────────────────────────────
@app.route('/api/strategies/<exchange>/build', methods=['POST'])
def api_strategies_build(exchange):
    exchange = exchange.upper()
    sc = load_screener(exchange)
    if not sc or not sc.get('stocks'):
        return jsonify({'error': f'Run the {exchange} screener first.'}), 400
    results = build_all(exchange, sc['stocks'])
    summary = {sid: {'positions': len(pf['positions']), 'invested': pf['total_invested']}
               for sid, pf in results.items() if pf}
    return jsonify({'success': True, 'exchange': exchange, 'summary': summary})


@app.route('/api/strategies/<exchange>/refresh', methods=['POST'])
def api_strategies_refresh(exchange):
    """
    Starts the refresh in the BACKGROUND and returns immediately.

    Why: Twelve Data's free plan allows only 8 credits/minute, so a full
    refresh across every strategy's unique tickers now waits a real ~65s
    between batches of 8 to respect that limit. For NYSE universes with
    100+ unique tickers that can take several minutes -- far longer than
    Render (or any web server) will hold a request open. Running it in
    the background and having the client poll /refresh/status avoids
    the request just timing out mid-refresh with nothing saved.
    """
    exchange = exchange.upper()
    status = update_all_async(exchange)
    return jsonify({'success': True, 'started': True, 'status': status})


@app.route('/api/strategies/<exchange>/refresh/status')
def api_strategies_refresh_status(exchange):
    exchange = exchange.upper()
    status = get_refresh_status(exchange)
    summary = None
    if not status.get('running'):
        # Refresh finished (or never ran) -- include the latest saved
        # P&L so the client can render results once done=true.
        portfolios = load_all_strategies(exchange)
        summary = {sid: {'pnl_pct': pf['total_pnl_pct'], 'pnl': pf['total_pnl'],
                          'price_sources': pf.get('price_sources', {})}
                   for sid, pf in portfolios.items() if pf}
    return jsonify({'status': status, 'summary': summary})


@app.route('/api/strategies/<exchange>/<sid>/refresh', methods=['POST'])
def api_strategy_single_refresh(exchange, sid):
    """Same background-job rationale as the bulk refresh above -- a
    single strategy can still hold 20-30 positions, which is enough
    Twelve Data chunks (each with a real ~65s rate-limit wait) to
    exceed a normal web request timeout."""
    exchange = exchange.upper()
    if not load_strategy(exchange, sid):
        return jsonify({'error': 'No portfolio found. Build strategies first.'}), 404
    status = update_strategy_async(exchange, sid)
    return jsonify({'success': True, 'started': True, 'status': status})


@app.route('/api/strategies/<exchange>/<sid>/refresh/status')
def api_strategy_single_refresh_status(exchange, sid):
    exchange = exchange.upper()
    status = get_refresh_status(exchange, sid)
    portfolio = None
    if not status.get('running'):
        portfolio = load_strategy(exchange, sid)
    return jsonify({'status': status, 'portfolio': portfolio})


@app.route('/api/strategies/<exchange>/data')
def api_strategies_data(exchange):
    exchange = exchange.upper()
    return jsonify({
        'portfolios': load_all_strategies(exchange),
        'histories':  load_all_histories(exchange),
    })


# ── Diagnostics ──────────────────────────────────────────────────
@app.route('/api/debug/price-sources')
def api_debug_price_sources():
    """
    Shows, for a quick sanity check, whether TWELVE_DATA_API_KEY is
    configured on this host and where the last refresh actually pulled
    each ticker's price from (yahoo_intraday / yahoo_daily / twelvedata /
    yahoo_individual / unavailable). Useful for confirming on Render
    whether Twelve Data is actually being reached, without needing to
    read server logs.
    """
    import os as _os
    key_set = bool(_os.environ.get('TWELVE_DATA_API_KEY'))
    out = {'twelve_data_key_configured': key_set, 'exchanges': {}}
    for exch in ('NSE', 'NYSE'):
        strategies = load_all_strategies(exch)
        exch_sources = {}
        for sid, pf in strategies.items():
            if pf and pf.get('price_sources'):
                exch_sources[sid] = pf['price_sources']
        out['exchanges'][exch] = exch_sources
    return jsonify(out)


# ── Screener API ───────────────────────────────────────────────────

@app.route('/api/screener/<exchange>/run', methods=['POST'])
def api_screener_run(exchange):
    exchange = exchange.upper()
    if exchange not in ('NSE', 'NYSE'):
        return jsonify({'error': 'Exchange must be NSE or NYSE'}), 400
    started = run_screener_background(exchange)
    if started:
        return jsonify({'status': 'started', 'exchange': exchange})
    return jsonify({'status': 'already_running', 'exchange': exchange})


@app.route('/api/screener/<exchange>/status')
def api_screener_status(exchange):
    exchange = exchange.upper()
    if exchange not in ('NSE', 'NYSE'):
        return jsonify({'error': 'Exchange must be NSE or NYSE'}), 400
    p = get_progress(exchange)
    data = load_screener(exchange)
    return jsonify({
        'progress': p,
        'has_data': data is not None,
        'last_run': data['last_run'] if data else None,
        'count':    data['high_momentum_count'] if data else 0,
    })


@app.route('/api/screener/<exchange>/data')
def api_screener_data(exchange):
    exchange = exchange.upper()
    data = load_screener(exchange)
    if not data:
        return jsonify({'error': 'No screener data. Run the screener first.'}), 404
    return jsonify(data)


# ── Portfolio API ──────────────────────────────────────────────────

@app.route('/api/portfolio/<exchange>/create', methods=['POST'])
def api_portfolio_create(exchange):
    exchange = exchange.upper()
    screener_data = load_screener(exchange)
    if not screener_data or not screener_data.get('stocks'):
        return jsonify({'error': 'Run the screener first to get top-10 stocks.'}), 400
    pf = create_portfolio(exchange, screener_data['stocks'])
    return jsonify({'success': True, 'portfolio': pf})


@app.route('/api/portfolio/<exchange>/update', methods=['POST'])
def api_portfolio_update(exchange):
    exchange = exchange.upper()
    pf = update_portfolio(exchange)
    if not pf:
        return jsonify({'error': 'No portfolio found. Create one first.'}), 404
    return jsonify({'success': True, 'portfolio': pf})


@app.route('/api/portfolio/<exchange>/data')
def api_portfolio_data(exchange):
    exchange = exchange.upper()
    pf = load_portfolio(exchange)
    hist = load_history(exchange)
    if not pf:
        return jsonify({'error': 'No portfolio found.'}), 404
    return jsonify({'portfolio': pf, 'history': hist})


# ── Existing single-stock API ──────────────────────────────────────

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    symbol   = data.get('symbol', '').strip()
    exchange = data.get('exchange', 'AUTO').strip()
    if not symbol:
        return jsonify({'error': 'Stock symbol is required'}), 400
    try:
        ticker, result = try_ticker_variants(symbol, exchange)
        result['resolved_ticker'] = ticker
        return jsonify({'success': True, 'data': result})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        print(f'Error analyzing {symbol}: {e}')
        print(traceback.format_exc())
        return jsonify({'error': f'Failed to analyze {symbol}. Check ticker and try again.',
                        'detail': str(e)}), 500


@app.route('/api/indicator-info', methods=['GET'])
def indicator_info():
    return jsonify({'indicators': INDICATOR_META, 'weights': INDICATOR_WEIGHTS})


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status':    'ok',
        'version':   '2.0.0',
        'scheduler': SCHEDULER_ACTIVE,
    })


# ── Strategy Comparison Chart ──────────────────────────────────────

@app.route('/strategy-comparison')
def strategy_comparison():
    """Serve the 10 strategies annual returns comparison chart."""
    return render_template('strategy-comparison.html')


# ── Backtest Dashboard ──────────────────────────────────────────────

@app.route('/backtest')
def backtest_dashboard():
    """Serve the 12-month historical backtest dashboard."""
    return render_template('backtest.html')


@app.route('/api/backtest/<exchange>/<sid>/run', methods=['POST'])
def api_backtest_run(exchange, sid):
    """Trigger a backtest in a background thread."""
    import threading
    from backtest_engine import run_strategy_backtest, set_progress

    exchange = exchange.upper()
    sid = sid.lower()

    def _run():
        try:
            run_strategy_backtest(exchange, sid)
        except Exception as e:
            import traceback
            print(f'[Backtest Error] {e}')
            print(traceback.format_exc())
            set_progress(exchange, sid, f'Error: {e}', 100)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'exchange': exchange, 'strategy': sid})


@app.route('/api/backtest/<exchange>/<sid>/status')
def api_backtest_status(exchange, sid):
    """Get live progress of a running backtest."""
    from backtest_engine import get_progress
    return jsonify(get_progress(exchange.upper(), sid.lower()))


@app.route('/api/backtest/<exchange>/<sid>/data')
def api_backtest_data(exchange, sid):
    """Return saved backtest results JSON."""
    from backtest_engine import load_backtest
    data = load_backtest(exchange.upper(), sid.lower())
    if not data:
        return jsonify({'error': 'No backtest data. Run the backtest first.'}), 404
    return jsonify(data)


# ── Rebalance Dashboard (Monthly Automation) ───────────────────────

@app.route('/rebalance')
def rebalance_dashboard():
    """Serve the monthly rebalance dashboard."""
    try:
        with open('templates/rebalance.html', 'r') as f:
            return f.read()
    except FileNotFoundError:
        return '''
        <html><body style="color: #ccc; font-family: Arial; padding: 20px;">
        <h1>📊 Rebalance Dashboard</h1>
        <p>Dashboard loading... Automation runs every 1st of month at 6 AM IST</p>
        </body></html>
        '''


@app.route('/api/rebalance/data')
def api_rebalance_data():
    """Get monthly rebalance report."""
    import json, os
    report_path = 'data/rebalance_report.json'
    if os.path.exists(report_path):
        try:
            with open(report_path) as f:
                return jsonify(json.load(f))
        except:
            pass
    return jsonify({'error': 'No report yet. Run the automation first.'}), 404


@app.route('/api/rebalance/run', methods=['POST'])
def api_rebalance_run():
    """Trigger monthly automation manually."""
    import os, threading, importlib.util

    def _run_automation():
        try:
            os.environ['FORCE_RUN'] = '1'
            spec = importlib.util.spec_from_file_location(
                'monthly_automation',
                'monthly_automation.py'
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, 'main'):
                mod.main()
        except Exception as e:
            print(f'[Error] Automation failed: {e}')

    t = threading.Thread(target=_run_automation, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': 'Automation running. Check /rebalance in 5-10 minutes.'})


@app.route('/api/rebalance/status')
def api_rebalance_status():
    """Get automation job status."""
    import json, os
    job_file = 'data/job_state.json'
    if os.path.exists(job_file):
        try:
            with open(job_file) as f:
                return jsonify(json.load(f))
        except:
            pass
    return jsonify({'status': 'idle', 'progress': 'Ready'})


# ── Entry Point ────────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '='*60)
    print('  🚀  Momentum Builder  v2.0')
    print('  Long-Term Momentum Investing Tool')
    print('='*60)
    print('  Supports: BSE (.BO) | NSE (.NS) | NYSE | NASDAQ')
    print('  Screener: NSE Elite + NYSE Elite (score 81-100)')
    print('  Portfolio: ₹100/$100 return tracker')
    print(f'  Scheduler: {"✅ Monthly auto-refresh active" if SCHEDULER_ACTIVE else "⚠️  Install apscheduler"}')
    print('-'*60)
    print('  Open your browser:')
    print('  ➜  http://127.0.0.1:5000          (Single stock)')
    print('  ➜  http://127.0.0.1:5000/screener/nse   (NSE Elite)')
    print('  ➜  http://127.0.0.1:5000/screener/nyse  (NYSE Elite)')
    print('  ➜  http://127.0.0.1:5000/portfolio      (Portfolio Tracker)')
    print('='*60 + '\n')
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
