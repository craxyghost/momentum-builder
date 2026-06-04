"""Run backtests locally and save JSON results to data/ folder."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

from backtest_engine import run_strategy_backtest

strategies = ['s1','s2','s3','s4','s5','s6','s7','s8','s9','s10']
exchanges  = ['NSE', 'NYSE']

for exchange in exchanges:
    for sid in strategies:
        print(f'\n{"="*50}')
        print(f'  Running {exchange} {sid.upper()}...')
        print(f'{"="*50}')
        try:
            r = run_strategy_backtest(exchange, sid)
            if 'error' in r:
                print(f'  ERROR: {r["error"]}')
            else:
                s = r.get('summary', {})
                print(f'  Total: {s.get("total_return_pct",0):+.1f}%  '
                      f'Annualised: {s.get("annualized_return",0):+.0f}%  '
                      f'Sharpe: {s.get("sharpe_ratio",0):.2f}  '
                      f'Stocks: {s.get("universe_size",0)}')
        except Exception as e:
            print(f'  FAILED: {e}')
            import traceback; traceback.print_exc()

print('\n\nAll done! Check data/ folder for JSON files.')
