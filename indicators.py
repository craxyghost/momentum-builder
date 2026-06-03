"""
Momentum Indicators Engine
===========================
5 Proven Long-Term Momentum Indicators using Monthly/Quarterly Data

References:
1. 12-1 Month Price Momentum  → Jegadeesh & Titman (1993), Fama & French (1996)
2. Monthly RSI (14-period)    → Wilder (1978); validated for long-term by multiple studies
3. MACD on Monthly Data       → Gerald Appel (1979); proven for long-term trend following
4. 52-Week High Ratio         → George & Hwang (2004) "The 52-Week High and Momentum Investing"
5. Moving Average Momentum    → Faber (2007), Antonacci (2014) Dual Momentum
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


# ─────────────────────────────────────────────
# Ticker Resolver
# ─────────────────────────────────────────────

def resolve_ticker(symbol: str) -> str:
    """
    Resolve user input to a valid yfinance ticker.
    BSE stocks: append .BO  (e.g., RELIANCE → RELIANCE.BO)
    NSE stocks: append .NS  (e.g., RELIANCE → RELIANCE.NS)
    NYSE/NASDAQ: keep as-is (e.g., AAPL)
    """
    symbol = symbol.strip().upper()
    # Already has exchange suffix
    if '.' in symbol:
        return symbol
    return symbol  # Will be detected by caller


def fetch_stock_data(ticker: str, period_years: int = 5) -> dict:
    """
    Fetch monthly stock data for a given ticker.
    Returns a dict with stock info and monthly OHLCV data.
    """
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365 * period_years)

    # Try to fetch data
    stock = yf.Ticker(ticker)

    # Get monthly data
    monthly_data = stock.history(start=start_date, end=end_date, interval='1mo')

    if monthly_data.empty or len(monthly_data) < 14:
        raise ValueError(f"Insufficient data for ticker '{ticker}'. Need at least 14 months of history.")

    # ── Drop the current INCOMPLETE monthly bar ─────────────────────
    # Monthly data includes the current in-progress month (e.g. June with
    # only 2 days of data). This causes 70% of indicator weights to change
    # daily based on noise. Stripping it gives stable, consistent scores
    # throughout the month — only updating when a month fully closes.
    today = datetime.today()
    if (not monthly_data.empty and
            monthly_data.index[-1].month == today.month and
            monthly_data.index[-1].year  == today.year):
        monthly_data = monthly_data.iloc[:-1]

    # Get stock info
    try:
        info = stock.info
        company_name = info.get('longName', info.get('shortName', ticker))
        sector = info.get('sector', 'N/A')
        currency = info.get('currency', 'USD')
        exchange = info.get('exchange', 'N/A')
        market_cap = info.get('marketCap', None)
        current_price = info.get('currentPrice', info.get('regularMarketPrice', monthly_data['Close'].iloc[-1]))
    except Exception:
        company_name = ticker
        sector = 'N/A'
        currency = 'N/A'
        exchange = 'N/A'
        market_cap = None
        current_price = monthly_data['Close'].iloc[-1]

    return {
        'ticker': ticker,
        'company_name': company_name,
        'sector': sector,
        'currency': currency,
        'exchange': exchange,
        'market_cap': market_cap,
        'current_price': float(current_price),
        'monthly_data': monthly_data,
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M')
    }


# ─────────────────────────────────────────────
# Indicator 1: 12-1 Month Price Momentum
# ─────────────────────────────────────────────

def indicator_price_momentum(monthly_data: pd.DataFrame) -> dict:
    """
    12-1 Month Price Momentum (Skip-Month Momentum)
    ------------------------------------------------
    Formula: Return over past 12 months, skipping the most recent month
             to avoid short-term reversal (1-month reversal anomaly).

    Academic backing: Jegadeesh & Titman (1993), Fama & French (1996)
    Proven to generate 1-2% monthly excess returns in long-term portfolios.

    Score interpretation:
    - > 20%  momentum  → Very Strong (90-100)
    - 10-20% momentum  → Strong (70-89)
    - 0-10%  momentum  → Moderate (50-69)
    - -10-0% momentum  → Weak (30-49)
    - < -10% momentum  → Very Weak (0-29)
    """
    close = monthly_data['Close']

    if len(close) < 14:
        return {'score': 50, 'value': 0, 'signal': 'Insufficient data', 'detail': 'Need 14+ months'}

    # 12-month return, skipping last month (t-1 to t-13)
    price_now = close.iloc[-2]      # 1 month ago (skip last month)
    price_12m = close.iloc[-14]     # 13 months ago

    momentum_return = ((price_now / price_12m) - 1) * 100

    # Also compute 6-month and 3-month momentum for signal richness
    price_6m = close.iloc[-8] if len(close) >= 8 else close.iloc[0]
    price_3m = close.iloc[-5] if len(close) >= 5 else close.iloc[0]

    mom_6m = ((price_now / price_6m) - 1) * 100
    mom_3m = ((price_now / price_3m) - 1) * 100

    # Score: map -30% to +40% → 0 to 100
    score = max(0, min(100, (momentum_return + 30) / 70 * 100))

    # Signal
    if momentum_return > 20:
        signal = 'Very Strong Momentum'
    elif momentum_return > 10:
        signal = 'Strong Momentum'
    elif momentum_return > 0:
        signal = 'Moderate Momentum'
    elif momentum_return > -10:
        signal = 'Weak / Fading Momentum'
    else:
        signal = 'Negative Momentum'

    return {
        'score': round(score, 1),
        'value': round(momentum_return, 2),
        'signal': signal,
        'detail': f'12M Return: {momentum_return:.1f}% | 6M: {mom_6m:.1f}% | 3M: {mom_3m:.1f}%',
        'sub_values': {
            '12M Return (skip-1M)': f'{momentum_return:.2f}%',
            '6M Return': f'{mom_6m:.2f}%',
            '3M Return': f'{mom_3m:.2f}%'
        }
    }


# ─────────────────────────────────────────────
# Indicator 2: Monthly RSI (14-period)
# ─────────────────────────────────────────────

def indicator_rsi(monthly_data: pd.DataFrame, period: int = 14) -> dict:
    """
    Relative Strength Index on Monthly Data
    ----------------------------------------
    Uses 14-period RSI on monthly closes.
    For long-term investing, RSI > 50 indicates upward momentum.

    Academic backing: Wilder (1978). Studies show monthly RSI > 50
    is a reliable filter for sustained price trends.

    Score = RSI value directly (already 0-100)
    """
    close = monthly_data['Close']

    if len(close) < period + 1:
        return {'score': 50, 'value': 50, 'signal': 'Insufficient data', 'detail': 'Need more months'}

    # Calculate RSI using Wilder's smoothing method
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    current_rsi = float(rsi.iloc[-1])
    prev_rsi = float(rsi.iloc[-2]) if len(rsi) >= 2 else current_rsi

    rsi_trend = 'Rising' if current_rsi > prev_rsi else 'Falling'

    if current_rsi >= 70:
        signal = 'Overbought / Very Strong'
    elif current_rsi >= 60:
        signal = 'Strong Momentum Zone'
    elif current_rsi >= 50:
        signal = 'Bullish Territory'
    elif current_rsi >= 40:
        signal = 'Bearish Territory'
    elif current_rsi >= 30:
        signal = 'Weak / Oversold Watch'
    else:
        signal = 'Deeply Oversold'

    return {
        'score': round(current_rsi, 1),
        'value': round(current_rsi, 2),
        'signal': f'{signal} | RSI Trend: {rsi_trend}',
        'detail': f'Monthly RSI(14): {current_rsi:.1f} | Previous Month: {prev_rsi:.1f}',
        'sub_values': {
            'Current RSI(14)': f'{current_rsi:.2f}',
            'Previous Month RSI': f'{prev_rsi:.2f}',
            'RSI Trend': rsi_trend
        }
    }


# ─────────────────────────────────────────────
# Indicator 3: MACD on Monthly Data
# ─────────────────────────────────────────────

def indicator_macd(monthly_data: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    MACD (Moving Average Convergence Divergence) on Monthly Data
    -------------------------------------------------------------
    Uses standard 12-26-9 MACD on monthly closes.
    For long-term momentum: MACD line above signal line is bullish.
    Histogram direction shows momentum acceleration/deceleration.

    Academic backing: Gerald Appel (1979). Many quantitative studies
    confirm MACD monthly crossovers as reliable long-term signals.

    Scoring: Based on histogram sign, magnitude, and trend direction.
    """
    close = monthly_data['Close']

    if len(close) < slow + signal:
        return {'score': 50, 'value': 0, 'signal': 'Insufficient data', 'detail': 'Need more months'}

    # Calculate MACD
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else 0
    current_macd = float(macd_line.iloc[-1])
    current_signal = float(signal_line.iloc[-1])
    current_price = float(close.iloc[-1])

    # Normalize histogram as % of price for scoring
    hist_pct = (current_hist / current_price) * 100 if current_price != 0 else 0

    # Score calculation
    # Positive histogram + rising = strong; Negative + falling = weak
    hist_increasing = current_hist > prev_hist
    macd_above_signal = current_macd > current_signal

    if macd_above_signal:
        base_score = 65  # Bullish
        if hist_increasing:
            bonus = min(20, abs(hist_pct) * 100)  # Accelerating momentum
            score = min(100, base_score + bonus)
        else:
            score = max(50, base_score - min(15, abs(hist_pct) * 50))  # Decelerating
    else:
        base_score = 35  # Bearish
        if not hist_increasing:
            penalty = min(20, abs(hist_pct) * 100)
            score = max(0, base_score - penalty)
        else:
            score = min(50, base_score + min(15, abs(hist_pct) * 50))  # Recovering

    if macd_above_signal and hist_increasing:
        sig_text = 'Bullish & Accelerating'
    elif macd_above_signal and not hist_increasing:
        sig_text = 'Bullish but Decelerating'
    elif not macd_above_signal and hist_increasing:
        sig_text = 'Bearish but Recovering'
    else:
        sig_text = 'Bearish & Weakening'

    return {
        'score': round(score, 1),
        'value': round(hist_pct, 4),
        'signal': sig_text,
        'detail': (f'MACD: {current_macd:.2f} | Signal: {current_signal:.2f} | '
                   f'Histogram: {current_hist:.2f} ({"↑" if hist_increasing else "↓"})'),
        'sub_values': {
            'MACD Line': f'{current_macd:.4f}',
            'Signal Line': f'{current_signal:.4f}',
            'Histogram': f'{current_hist:.4f}',
            'Histogram vs Prev': '↑ Increasing' if hist_increasing else '↓ Decreasing',
            'MACD vs Signal': 'Above ✓' if macd_above_signal else 'Below ✗'
        }
    }


# ─────────────────────────────────────────────
# Indicator 4: 52-Week High Ratio
# ─────────────────────────────────────────────

def indicator_52week_high(monthly_data: pd.DataFrame) -> dict:
    """
    52-Week High Proximity Ratio
    ----------------------------
    Measures how close current price is to its 52-week high.
    The closer to the 52-week high, the stronger the momentum.

    Formula: Current Price / 52-Week High × 100

    Academic backing: George & Hwang (2004) "The 52-Week High and Momentum Investing"
    Found that stocks near their 52-week high generate significantly higher
    future returns. Outperforms traditional price momentum in many markets.

    Score = ratio × 100 (already 0-100 scale)
    """
    close = monthly_data['Close']
    high = monthly_data['High']
    low = monthly_data['Low']

    if len(close) < 13:
        return {'score': 50, 'value': 50, 'signal': 'Insufficient data', 'detail': 'Need 13+ months'}

    # Use last 12 months of data
    recent_high = high.iloc[-13:-1].max()  # 52-week high (excluding this month)
    recent_low = low.iloc[-13:-1].min()    # 52-week low
    current_price = float(close.iloc[-1])

    # Ratio: how close to 52-week high (0-100%)
    if recent_high > 0:
        ratio = (current_price / recent_high) * 100
    else:
        ratio = 50.0

    # Position within 52-week range (0=at low, 100=at high)
    price_range = recent_high - recent_low
    if price_range > 0:
        position_in_range = ((current_price - recent_low) / price_range) * 100
    else:
        position_in_range = 50.0

    # Score: weighted combo of ratio and position in range
    score = (ratio * 0.7) + (position_in_range * 0.3)
    score = max(0, min(100, score))

    dist_from_high_pct = ((recent_high - current_price) / recent_high) * 100

    if ratio >= 95:
        signal = 'Near 52-Week High (Very Strong)'
    elif ratio >= 85:
        signal = 'Strong — Near 52-Week High'
    elif ratio >= 70:
        signal = 'Moderate — Mid-Range'
    elif ratio >= 55:
        signal = 'Weak — Below Midpoint'
    else:
        signal = 'Very Weak — Near 52-Week Low'

    return {
        'score': round(score, 1),
        'value': round(ratio, 2),
        'signal': signal,
        'detail': (f'Price/52W-High: {ratio:.1f}% | '
                   f'52W High: {recent_high:.2f} | 52W Low: {recent_low:.2f} | '
                   f'Distance from High: {dist_from_high_pct:.1f}%'),
        'sub_values': {
            'Current Price': f'{current_price:.2f}',
            '52-Week High': f'{recent_high:.2f}',
            '52-Week Low': f'{recent_low:.2f}',
            'Price/52W-High %': f'{ratio:.2f}%',
            'Position in Range': f'{position_in_range:.1f}%'
        }
    }


# ─────────────────────────────────────────────
# Indicator 5: Moving Average Momentum Cross
# ─────────────────────────────────────────────

def indicator_ma_momentum(monthly_data: pd.DataFrame) -> dict:
    """
    Moving Average Momentum (6-Month vs 12-Month SMA)
    --------------------------------------------------
    Compares 6-month SMA to 12-month SMA on monthly data.
    When 6M SMA > 12M SMA, the stock is in a long-term uptrend.
    The spread magnitude indicates trend strength.

    Academic backing:
    - Faber (2007) "A Quantitative Approach to Tactical Asset Allocation"
    - Antonacci (2014) "Dual Momentum Investing"
    Both show SMA crossover systems with monthly data produce
    superior risk-adjusted returns over the long term.

    Additional: 200-day equivalent trend check on monthly scale.
    """
    close = monthly_data['Close']

    if len(close) < 13:
        return {'score': 50, 'value': 0, 'signal': 'Insufficient data', 'detail': 'Need 13+ months'}

    # Calculate SMAs
    sma_6 = close.rolling(window=6).mean()
    sma_12 = close.rolling(window=12).mean()

    current_sma6 = float(sma_6.iloc[-1])
    current_sma12 = float(sma_12.iloc[-1])
    prev_sma6 = float(sma_6.iloc[-2]) if len(sma_6) >= 2 else current_sma6
    prev_sma12 = float(sma_12.iloc[-2]) if len(sma_12) >= 2 else current_sma12

    current_price = float(close.iloc[-1])

    # Spread as % of 12M SMA
    if current_sma12 > 0:
        spread_pct = ((current_sma6 - current_sma12) / current_sma12) * 100
    else:
        spread_pct = 0

    # Is the cross positive or recently crossed?
    was_above = prev_sma6 > prev_sma12
    is_above = current_sma6 > current_sma12

    # Price above both MAs check
    price_above_sma6 = current_price > current_sma6
    price_above_sma12 = current_price > current_sma12

    # Score: base on spread + price position
    # Spread -20% to +20% → 0 to 100
    base_score = max(0, min(100, (spread_pct + 20) / 40 * 100))

    # Bonus/penalty for price position
    if price_above_sma6 and price_above_sma12:
        base_score = min(100, base_score + 5)
    elif not price_above_sma6 and not price_above_sma12:
        base_score = max(0, base_score - 5)

    # Detect crossover
    if is_above and not was_above:
        crossover_note = '🟢 Golden Cross (Bullish Crossover!)'
    elif not is_above and was_above:
        crossover_note = '🔴 Death Cross (Bearish Crossover!)'
    elif is_above:
        crossover_note = 'Bullish Alignment (6M > 12M SMA)'
    else:
        crossover_note = 'Bearish Alignment (6M < 12M SMA)'

    if spread_pct > 10:
        signal = f'Very Strong Uptrend | {crossover_note}'
    elif spread_pct > 3:
        signal = f'Strong Uptrend | {crossover_note}'
    elif spread_pct > 0:
        signal = f'Mild Uptrend | {crossover_note}'
    elif spread_pct > -3:
        signal = f'Mild Downtrend | {crossover_note}'
    elif spread_pct > -10:
        signal = f'Downtrend | {crossover_note}'
    else:
        signal = f'Strong Downtrend | {crossover_note}'

    return {
        'score': round(base_score, 1),
        'value': round(spread_pct, 4),
        'signal': signal,
        'detail': (f'6M SMA: {current_sma6:.2f} | 12M SMA: {current_sma12:.2f} | '
                   f'Spread: {spread_pct:.2f}% | Price vs SMAs: '
                   f'{"Above 6M ✓" if price_above_sma6 else "Below 6M ✗"} / '
                   f'{"Above 12M ✓" if price_above_sma12 else "Below 12M ✗"}'),
        'sub_values': {
            '6-Month SMA': f'{current_sma6:.2f}',
            '12-Month SMA': f'{current_sma12:.2f}',
            'Spread (6M-12M)': f'{spread_pct:.2f}%',
            'Price vs 6M SMA': 'Above ✓' if price_above_sma6 else 'Below ✗',
            'Price vs 12M SMA': 'Above ✓' if price_above_sma12 else 'Below ✗'
        }
    }


# ─────────────────────────────────────────────
# Aggregator: Final Momentum Score
# ─────────────────────────────────────────────

INDICATOR_WEIGHTS = {
    '12_1_momentum': 0.30,    # Strongest academic evidence
    'rsi': 0.20,              # Reliable trend filter
    'macd': 0.20,             # Trend strength
    '52_week_high': 0.15,     # George & Hwang signal
    'ma_momentum': 0.15,      # Long-term trend confirmation
}

INDICATOR_META = {
    '12_1_momentum': {
        'name': '12-1 Month Price Momentum',
        'short': 'Price Momentum',
        'icon': '📈',
        'academic': 'Jegadeesh & Titman (1993)',
        'description': (
            'Measures return over the past 12 months, skipping the most recent month '
            'to avoid the 1-month reversal anomaly. The seminal Jegadeesh & Titman (1993) '
            'paper showed this strategy generates ~1% monthly excess returns. '
            'Confirmed by Fama & French (1996) as one of the most robust anomalies in finance.'
        ),
        'data_used': 'Monthly closing prices (13 months)',
        'weight_pct': '30%'
    },
    'rsi': {
        'name': 'Monthly RSI (14-period)',
        'short': 'Monthly RSI',
        'icon': '⚡',
        'academic': 'Wilder (1978)',
        'description': (
            'RSI applied to monthly data — a much more reliable signal than daily RSI for '
            'long-term investors. RSI > 50 indicates positive momentum; RSI > 60 shows '
            'strong trend. Academic research confirms monthly RSI > 50 as a reliable '
            'bull-market filter with reduced whipsaws.'
        ),
        'data_used': 'Monthly closing prices (14+ months)',
        'weight_pct': '20%'
    },
    'macd': {
        'name': 'Monthly MACD (12-26-9)',
        'short': 'MACD Monthly',
        'icon': '〰️',
        'academic': 'Gerald Appel (1979)',
        'description': (
            'MACD applied to monthly data removes the noise of daily/weekly oscillations. '
            'A positive histogram indicates bullish momentum; rising histogram shows '
            'acceleration. Multiple quantitative studies show monthly MACD crossovers '
            'generate statistically significant alpha over long investment horizons.'
        ),
        'data_used': 'Monthly closing prices (26+ months)',
        'weight_pct': '20%'
    },
    '52_week_high': {
        'name': '52-Week High Proximity',
        'short': '52W High Ratio',
        'icon': '🏔️',
        'academic': 'George & Hwang (2004)',
        'description': (
            'Stocks near their 52-week high tend to continue rising — the "52-week high '
            'effect." George & Hwang (2004) showed this signal outperforms traditional '
            'price momentum in several markets because it captures investor anchoring '
            'behavior: investors are reluctant to push prices past recent highs, then '
            'capitulate, driving sustained breakouts.'
        ),
        'data_used': 'Monthly High/Low/Close (13 months)',
        'weight_pct': '15%'
    },
    'ma_momentum': {
        'name': '6M vs 12M SMA Cross',
        'short': 'MA Momentum',
        'icon': '📊',
        'academic': 'Faber (2007) / Antonacci (2014)',
        'description': (
            'Compares 6-month and 12-month Simple Moving Averages using monthly data. '
            'Faber (2007) showed monthly SMA crossover strategies outperform buy-and-hold '
            'with lower drawdowns. Antonacci\'s Dual Momentum (2014) uses a similar concept '
            'with strong out-of-sample evidence across multiple asset classes since 1927.'
        ),
        'data_used': 'Monthly closing prices (12+ months)',
        'weight_pct': '15%'
    }
}


def calculate_all_indicators(ticker: str) -> dict:
    """
    Main function: fetches data and calculates all 5 momentum indicators.
    Returns complete analysis with individual scores and final weighted score.
    """
    # Fetch data
    stock_data = fetch_stock_data(ticker, period_years=5)
    monthly = stock_data['monthly_data']

    # Calculate all 5 indicators
    results = {
        '12_1_momentum': indicator_price_momentum(monthly),
        'rsi': indicator_rsi(monthly),
        'macd': indicator_macd(monthly),
        '52_week_high': indicator_52week_high(monthly),
        'ma_momentum': indicator_ma_momentum(monthly),
    }

    # Attach metadata to each result
    for key in results:
        results[key].update(INDICATOR_META[key])
        results[key]['weight'] = INDICATOR_WEIGHTS[key]
        results[key]['weighted_score'] = round(results[key]['score'] * INDICATOR_WEIGHTS[key], 2)

    # Calculate final weighted score
    final_score = sum(results[key]['score'] * INDICATOR_WEIGHTS[key] for key in results)
    final_score = round(final_score, 1)

    # Momentum classification
    if final_score >= 80:
        momentum_class = 'Very Strong'
        momentum_color = '#00c853'
        recommendation = 'High momentum. Strong candidate for long-term position.'
    elif final_score >= 65:
        momentum_class = 'Strong'
        momentum_color = '#64dd17'
        recommendation = 'Good momentum. Consider building a position with standard position sizing.'
    elif final_score >= 50:
        momentum_class = 'Moderate'
        momentum_color = '#ffd600'
        recommendation = 'Mixed signals. Wait for confirmation before entering.'
    elif final_score >= 35:
        momentum_class = 'Weak'
        momentum_color = '#ff6d00'
        recommendation = 'Momentum fading. Caution advised; consider reducing exposure.'
    else:
        momentum_class = 'Very Weak'
        momentum_color = '#d50000'
        recommendation = 'Negative momentum. Avoid or consider exit.'

    # Build price history for chart (last 36 months)
    chart_data = []
    price_history = monthly['Close'].tail(36)
    for date, price in price_history.items():
        chart_data.append({
            'date': date.strftime('%Y-%m'),
            'price': round(float(price), 2)
        })

    return {
        'stock': {
            'ticker': stock_data['ticker'],
            'company_name': stock_data['company_name'],
            'sector': stock_data['sector'],
            'currency': stock_data['currency'],
            'exchange': stock_data['exchange'],
            'market_cap': stock_data['market_cap'],
            'current_price': stock_data['current_price'],
            'last_updated': stock_data['last_updated']
        },
        'indicators': results,
        'final_score': final_score,
        'momentum_class': momentum_class,
        'momentum_color': momentum_color,
        'recommendation': recommendation,
        'chart_data': chart_data,
        'data_months': len(monthly)
    }
