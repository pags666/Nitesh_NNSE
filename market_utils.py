"""
market_utils.py — Shared Market Intelligence
=============================================
Provides market data functions used by both wordf and multi_ai engines:
- Stock info (price, volume, market cap)
- Freshness check (already priced in?)
- Volume spike detection
- Market cap weighting
- Technical indicators (RSI, MACD)
- Sector classification
- Signal stacking detection
"""

import time
from datetime import datetime, timedelta

# =============================
# CACHE — avoids redundant yfinance API calls
# TTL = 5 minutes per symbol
# =============================
_CACHE = {}
_CACHE_TTL = 300  # seconds

def _cache_key(symbol, func_name):
    return f"{symbol}:{func_name}"

def _cache_get(symbol, func_name):
    key = _cache_key(symbol, func_name)
    if key in _CACHE:
        val, ts = _CACHE[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        del _CACHE[key]
    return None

def _cache_set(symbol, func_name, value):
    _CACHE[_cache_key(symbol, func_name)] = (value, time.time())

# =============================
# YFINANCE TICKER HELPER
# =============================
def _get_ticker(symbol):
    """Try .NS then .BO suffix, return yfinance Ticker object."""
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t = yf.Ticker(f"{symbol}{suffix}")
                hist = t.history(period="5d")
                if len(hist) >= 1:
                    return t, f"{symbol}{suffix}"
            except Exception:
                continue
    except ImportError:
        pass
    return None, None

def _get_history(symbol, period="1mo"):
    """Get price history with caching."""
    cached = _cache_get(symbol, f"history_{period}")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t = yf.Ticker(f"{symbol}{suffix}")
                hist = t.history(period=period)
                if len(hist) >= 2:
                    _cache_set(symbol, f"history_{period}", hist)
                    return hist
            except Exception:
                continue
    except ImportError:
        pass
    return None

# =============================
# 1. GET STOCK INFO
# =============================
def get_stock_info(symbol):
    """
    Returns dict with market_cap, avg_volume, current_price, today_change_pct.
    Returns None values on failure.
    """
    cached = _cache_get(symbol, "info")
    if cached is not None:
        return cached

    result = {
        "market_cap": None,
        "avg_volume": None,
        "current_price": None,
        "today_change_pct": None,
        "sector": None,
    }

    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t = yf.Ticker(f"{symbol}{suffix}")
                info = t.info
                hist = t.history(period="5d")

                if len(hist) >= 2:
                    result["current_price"] = round(float(hist['Close'].iloc[-1]), 2)
                    prev_close = float(hist['Close'].iloc[-2])
                    result["today_change_pct"] = round(
                        ((result["current_price"] - prev_close) / prev_close) * 100, 2
                    )

                result["market_cap"] = info.get("marketCap", None)
                result["avg_volume"] = info.get("averageVolume", None)
                result["sector"] = info.get("sector", None)

                if result["current_price"]:
                    _cache_set(symbol, "info", result)
                    return result
            except Exception:
                continue
    except ImportError:
        pass

    return result

# =============================
# 2. CHECK FRESHNESS (Already Priced In?)
# =============================
def check_freshness(symbol, threshold_pct=3.0):
    """
    Returns True if stock already moved more than threshold_pct today.
    Indicates the news is likely already priced in.

    Returns:
        (is_stale, change_pct)
        is_stale: True if already moved significantly
        change_pct: today's change %
    """
    info = get_stock_info(symbol)
    change = info.get("today_change_pct")

    if change is None:
        return False, 0.0

    is_stale = abs(change) >= threshold_pct
    return is_stale, change

# =============================
# 3. VOLUME RATIO
# =============================
def get_volume_ratio(symbol):
    """
    Returns current volume / 20-day average volume.
    Ratio > 2.0 = volume spike (smart money acting).
    Ratio > 5.0 = extreme volume (major event).

    Returns:
        (ratio, score_boost)
        ratio: volume ratio float
        score_boost: confidence adjustment (-5 to +8)
    """
    cached = _cache_get(symbol, "volume_ratio")
    if cached is not None:
        return cached

    try:
        hist = _get_history(symbol, "1mo")
        if hist is None or len(hist) < 5:
            return 1.0, 0

        current_vol = float(hist['Volume'].iloc[-1])
        avg_vol = float(hist['Volume'].iloc[:-1].mean())

        if avg_vol <= 0:
            return 1.0, 0

        ratio = round(current_vol / avg_vol, 2)

        # Score boost based on volume
        if ratio >= 5.0:
            boost = 8    # Extreme volume — very strong confirmation
        elif ratio >= 3.0:
            boost = 5    # High volume — strong confirmation
        elif ratio >= 2.0:
            boost = 3    # Above average — moderate confirmation
        elif ratio >= 1.5:
            boost = 1    # Slightly above average
        elif ratio <= 0.3:
            boost = -5   # Very low volume — weak signal
        elif ratio <= 0.5:
            boost = -2   # Below average — somewhat weak
        else:
            boost = 0    # Normal volume

        result = (ratio, boost)
        _cache_set(symbol, "volume_ratio", result)
        return result

    except Exception:
        return 1.0, 0

# =============================
# 4. MARKET CAP WEIGHT
# =============================
def get_market_cap_weight(symbol, deal_value_cr=0):
    """
    Compute deal size relative to market cap.
    A ₹500 Cr order is massive for a ₹500 Cr company but irrelevant for TCS.

    Returns:
        score_boost: 0 (tiny/unknown) to 8 (massive deal vs mcap)
    """
    if deal_value_cr <= 0:
        return 0

    info = get_stock_info(symbol)
    mcap = info.get("market_cap")

    if not mcap or mcap <= 0:
        return 0

    # Convert market cap from INR to Crores
    mcap_cr = mcap / 1e7

    if mcap_cr <= 0:
        return 0

    ratio = deal_value_cr / mcap_cr

    if ratio >= 0.20:
        return 8    # Deal is 20%+ of market cap — massive
    elif ratio >= 0.10:
        return 6    # 10-20% — very significant
    elif ratio >= 0.05:
        return 4    # 5-10% — significant
    elif ratio >= 0.02:
        return 2    # 2-5% — moderate
    elif ratio >= 0.01:
        return 1    # 1-2% — minor
    return 0        # <1% — irrelevant

# =============================
# 5. RSI (Relative Strength Index)
# =============================
def get_rsi(symbol, period=14):
    """
    Compute RSI from price history.
    RSI < 30 = oversold (potential bounce)
    RSI > 70 = overbought (potential pullback)

    Returns: RSI value (0-100) or None
    """
    cached = _cache_get(symbol, f"rsi_{period}")
    if cached is not None:
        return cached

    try:
        hist = _get_history(symbol, "3mo")
        if hist is None or len(hist) < period + 5:
            return None

        closes = hist['Close']
        delta = closes.diff()

        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        # Use last valid values
        ag = avg_gain.iloc[-1]
        al = avg_loss.iloc[-1]

        if al == 0:
            rsi = 100.0
        else:
            rs = ag / al
            rsi = round(100 - (100 / (1 + rs)), 1)

        _cache_set(symbol, f"rsi_{period}", rsi)
        return rsi

    except Exception:
        return None

# =============================
# 6. MACD SIGNAL
# =============================
def get_macd_signal(symbol):
    """
    Compute MACD (12, 26, 9).
    Returns: "BULLISH", "BEARISH", or "NEUTRAL"
    """
    cached = _cache_get(symbol, "macd")
    if cached is not None:
        return cached

    try:
        hist = _get_history(symbol, "3mo")
        if hist is None or len(hist) < 35:
            return "NEUTRAL"

        closes = hist['Close']

        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        # Check last 2 values for crossover
        macd_now = macd_line.iloc[-1]
        macd_prev = macd_line.iloc[-2]
        sig_now = signal_line.iloc[-1]
        sig_prev = signal_line.iloc[-2]

        if macd_prev <= sig_prev and macd_now > sig_now:
            result = "BULLISH"   # MACD crossed above signal
        elif macd_prev >= sig_prev and macd_now < sig_now:
            result = "BEARISH"   # MACD crossed below signal
        elif macd_now > sig_now:
            result = "BULLISH"   # MACD is above signal
        elif macd_now < sig_now:
            result = "BEARISH"   # MACD is below signal
        else:
            result = "NEUTRAL"

        _cache_set(symbol, "macd", result)
        return result

    except Exception:
        return "NEUTRAL"

# =============================
# 7. GET SECTOR
# =============================
def get_sector(symbol):
    """Return sector classification from yfinance."""
    info = get_stock_info(symbol)
    return info.get("sector", "Unknown")

# =============================
# 8. SIGNAL STACK COUNT
# =============================
def get_signal_stack_count(symbol, sheet_data, days=7):
    """
    Count how many signals the same stock received in the last N days.
    Multiple signals in a short period = stronger conviction.

    Args:
        symbol: stock symbol
        sheet_data: list of past signal rows (from wordf or multi_ai sheet)
        days: lookback window

    Returns:
        (count, boost)
        count: number of recent signals
        boost: score adjustment (0 to 10)
    """
    if not sheet_data:
        return 0, 0

    cutoff = datetime.now() - timedelta(days=days)
    count = 0

    for row in sheet_data:
        if len(row) < 6:
            continue

        past_stock = str(row[1]).strip().upper()
        if past_stock != symbol.upper():
            continue

        # Try to parse date
        date_str = str(row[0]).strip()
        past_date = None
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%d %b %Y"]:
            try:
                past_date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue

        if past_date and past_date >= cutoff:
            past_signal = str(row[5]).strip().upper() if len(row) > 5 else ""
            if past_signal in ("BUY", "SELL", "STRONG BUY", "STRONG SELL"):
                count += 1

    # Boost based on stacking
    if count >= 4:
        boost = 10   # 4+ signals in 7 days — very strong
    elif count >= 3:
        boost = 7    # 3 signals
    elif count >= 2:
        boost = 4    # 2 signals
    elif count >= 1:
        boost = 1    # 1 signal (some history)
    else:
        boost = 0

    return count, boost

# =============================
# 9. TECHNICAL CONFIRMATION
# =============================
def get_technical_confirmation(symbol, direction):
    """
    Combined RSI + MACD check. Returns confidence adjustment.

    For BUY signals:
        RSI < 35 + MACD bullish = +15 (oversold + momentum turning up)
        RSI < 35 alone = +8 (oversold)
        MACD bullish alone = +5
        RSI > 75 = -10 (overbought — risky to buy here)

    For SELL signals:
        RSI > 70 + MACD bearish = +15 (overbought + momentum turning down)
        RSI > 70 alone = +8
        MACD bearish alone = +5
        RSI < 25 = -10 (oversold — risky to sell here)

    Returns:
        (adjustment, detail_str)
    """
    rsi = get_rsi(symbol)
    macd = get_macd_signal(symbol)

    if rsi is None:
        return 0, "no RSI data"

    adjustment = 0
    details = []

    if direction.upper() in ("BUY", "STRONG BUY"):
        # BUY confirmation
        if rsi < 35 and macd == "BULLISH":
            adjustment = 15
            details.append(f"RSI={rsi} oversold + MACD bullish")
        elif rsi < 35:
            adjustment = 8
            details.append(f"RSI={rsi} oversold")
        elif macd == "BULLISH":
            adjustment = 5
            details.append(f"MACD bullish")
        elif rsi > 75:
            adjustment = -10
            details.append(f"RSI={rsi} OVERBOUGHT (risky buy)")
        elif macd == "BEARISH":
            adjustment = -3
            details.append(f"MACD bearish (weak momentum)")
        else:
            details.append(f"RSI={rsi} MACD={macd}")

    elif direction.upper() in ("SELL", "STRONG SELL"):
        # SELL confirmation
        if rsi > 70 and macd == "BEARISH":
            adjustment = 15
            details.append(f"RSI={rsi} overbought + MACD bearish")
        elif rsi > 70:
            adjustment = 8
            details.append(f"RSI={rsi} overbought")
        elif macd == "BEARISH":
            adjustment = 5
            details.append(f"MACD bearish")
        elif rsi < 25:
            adjustment = -10
            details.append(f"RSI={rsi} OVERSOLD (risky sell)")
        elif macd == "BULLISH":
            adjustment = -3
            details.append(f"MACD bullish (momentum against)")
        else:
            details.append(f"RSI={rsi} MACD={macd}")

    detail_str = " | ".join(details)
    return adjustment, detail_str

# =============================
# 10. TIME DECAY WEIGHT
# =============================
def get_time_weight():
    """
    Returns weight based on current time (IST):
    - Market hours (9:15-15:30): 1.0 (full weight)
    - Pre-market (8:00-9:15): 0.9
    - Post-market (15:30-18:00): 0.85
    - After hours (18:00-23:59): 0.7
    - Night (00:00-08:00): 0.6
    """
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        hour = now.hour
        minute = now.minute
        t = hour + minute / 60.0

        if 9.25 <= t <= 15.5:    # 9:15 AM - 3:30 PM
            return 1.0
        elif 8.0 <= t < 9.25:    # 8:00 AM - 9:15 AM
            return 0.9
        elif 15.5 < t <= 18.0:   # 3:30 PM - 6:00 PM
            return 0.85
        elif 18.0 < t <= 24.0:   # 6:00 PM - midnight
            return 0.7
        else:                     # midnight - 8:00 AM
            return 0.6
    except Exception:
        return 1.0

# =============================
# 11. PATTERN RELIABILITY SCORES
# =============================
def load_pattern_scores():
    """
    Load pattern reliability scores from pattern_scores.json.
    Returns dict of pattern → {win_rate, count, avg_return} or empty dict.
    """
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pattern_scores.json")
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("patterns", {})
    except Exception:
        return {}

def get_pattern_reliability_boost(reasons, pattern_scores=None):
    """
    Adjust confidence based on historical pattern reliability.

    Args:
        reasons: list of matched reason strings
        pattern_scores: dict from load_pattern_scores()

    Returns:
        (adjustment, detail_str)
    """
    if not pattern_scores:
        pattern_scores = load_pattern_scores()

    if not pattern_scores:
        return 0, ""

    total_boost = 0
    details = []

    for reason in reasons:
        # Extract clean label from "[STRONG BUY] label" format
        clean = reason.split("] ")[-1].strip().lower() if "]" in reason else reason.lower()

        for pattern_key, scores in pattern_scores.items():
            if pattern_key.lower() in clean or clean in pattern_key.lower():
                win_rate = scores.get("win_rate_t3", 0.5)
                count = scores.get("count", 0)

                if count < 3:
                    continue  # Not enough data

                if win_rate >= 0.80:
                    total_boost += 10
                    details.append(f"{pattern_key}: {win_rate:.0%} win ({count}x)")
                elif win_rate >= 0.65:
                    total_boost += 5
                    details.append(f"{pattern_key}: {win_rate:.0%} win ({count}x)")
                elif win_rate <= 0.35:
                    total_boost -= 10
                    details.append(f"{pattern_key}: {win_rate:.0%} win WEAK ({count}x)")
                elif win_rate <= 0.45:
                    total_boost -= 5
                    details.append(f"{pattern_key}: {win_rate:.0%} win BELOW AVG ({count}x)")
                break  # Only match first pattern

    total_boost = max(-15, min(20, total_boost))
    return total_boost, " | ".join(details[:3])


# =============================
# 12. LIQUIDITY FILTER
# =============================
def check_liquidity(symbol, min_avg_volume=50000, min_mcap_cr=100):
    """
    Skip illiquid/penny stocks that can't be traded reliably.

    Returns:
        (is_liquid, reason)
        is_liquid: True if tradeable
        reason: explanation if illiquid
    """
    info = get_stock_info(symbol)

    avg_vol = info.get("avg_volume")
    if avg_vol and avg_vol < min_avg_volume:
        return False, f"avg volume {avg_vol:,.0f} < {min_avg_volume:,.0f}"

    mcap = info.get("market_cap")
    if mcap:
        mcap_cr = mcap / 1e7
        if mcap_cr < min_mcap_cr:
            return False, f"mcap {mcap_cr:.0f}Cr < {min_mcap_cr}Cr"

    return True, ""

# =============================
# 13. 52-WEEK CONTEXT
# =============================
def get_52week_context(symbol, direction):
    """
    Same news has different impact based on where price sits
    relative to 52-week range.

    BUY at 52w low = strong turnaround play (+10)
    BUY at 52w high = risky, already priced in (-5)
    SELL at 52w high = distribution, strong sell (+8)
    SELL at 52w low = already beaten, risky sell (-5)

    Returns: (adjustment, detail_str)
    """
    cached = _cache_get(symbol, "52w_context")
    if cached is not None:
        high52, low52, current = cached
    else:
        try:
            hist = _get_history(symbol, "1y")
            if hist is None or len(hist) < 20:
                return 0, "no 52w data"
            high52 = float(hist['High'].max())
            low52 = float(hist['Low'].min())
            current = float(hist['Close'].iloc[-1])
            _cache_set(symbol, "52w_context", (high52, low52, current))
        except Exception:
            return 0, "52w data error"

    if high52 <= low52 or high52 == 0:
        return 0, ""

    # Position in range (0 = at low, 1 = at high)
    range_pos = (current - low52) / (high52 - low52)
    pct_from_high = ((high52 - current) / high52) * 100
    pct_from_low = ((current - low52) / low52) * 100 if low52 > 0 else 0

    adjustment = 0
    detail = f"52w pos: {range_pos:.0%}"

    if direction.upper() in ("BUY", "STRONG BUY"):
        if range_pos <= 0.15:
            adjustment = 10
            detail = f"Near 52w LOW ({pct_from_low:.0f}% above) - turnaround play"
        elif range_pos <= 0.30:
            adjustment = 5
            detail = f"Low zone ({range_pos:.0%} of range)"
        elif range_pos >= 0.95:
            adjustment = -8
            detail = f"At 52w HIGH ({pct_from_high:.0f}% below) - risky buy"
        elif range_pos >= 0.85:
            adjustment = -4
            detail = f"Near 52w high ({range_pos:.0%} of range)"

    elif direction.upper() in ("SELL", "STRONG SELL"):
        if range_pos >= 0.90:
            adjustment = 8
            detail = f"Near 52w HIGH - distribution zone"
        elif range_pos >= 0.75:
            adjustment = 4
            detail = f"High zone ({range_pos:.0%} of range)"
        elif range_pos <= 0.10:
            adjustment = -8
            detail = f"Near 52w LOW - risky to sell"
        elif range_pos <= 0.20:
            adjustment = -4
            detail = f"Low zone ({range_pos:.0%} of range) - bouncing?"

    return adjustment, detail

# =============================
# 14. DELIVERY % PROXY
# =============================
def get_delivery_proxy(symbol):
    """
    Approximate delivery quality using volume stability.
    High volume with low volatility = institutional (high delivery).
    Spiky volume with high volatility = speculative (low delivery).

    Returns: (quality_score, detail)
        quality_score: -5 to +5
    """
    cached = _cache_get(symbol, "delivery_proxy")
    if cached is not None:
        return cached

    try:
        hist = _get_history(symbol, "1mo")
        if hist is None or len(hist) < 10:
            return 0, "no data"

        import numpy as np

        volumes = hist['Volume'].values
        closes = hist['Close'].values

        # Volume consistency (std/mean — lower = more institutional)
        vol_cv = float(np.std(volumes) / np.mean(volumes)) if np.mean(volumes) > 0 else 2.0

        # Price volatility (daily returns std)
        returns = np.diff(closes) / closes[:-1]
        price_vol = float(np.std(returns))

        # Institutional score
        if vol_cv < 0.5 and price_vol < 0.02:
            score, detail = 5, f"Institutional pattern (vol CV={vol_cv:.2f})"
        elif vol_cv < 0.8 and price_vol < 0.03:
            score, detail = 3, f"Moderate delivery (vol CV={vol_cv:.2f})"
        elif vol_cv > 1.5 and price_vol > 0.04:
            score, detail = -5, f"Speculative pattern (vol CV={vol_cv:.2f})"
        elif vol_cv > 1.2:
            score, detail = -2, f"Inconsistent volume (vol CV={vol_cv:.2f})"
        else:
            score, detail = 0, f"Normal pattern (vol CV={vol_cv:.2f})"

        result = (score, detail)
        _cache_set(symbol, "delivery_proxy", result)
        return result

    except Exception:
        return 0, "delivery calc error"

# =============================
# 15. PUT/CALL RATIO PROXY
# =============================
def get_pcr_proxy(symbol, direction):
    """
    Approximate PCR using price momentum and volume pressure.
    Sustained buying pressure with rising prices = bullish PCR.
    Sustained selling pressure with falling prices = bearish PCR.

    Returns: (adjustment, detail)
    """
    cached = _cache_get(symbol, "pcr_proxy")
    if cached is not None:
        momentum_score = cached
    else:
        try:
            hist = _get_history(symbol, "1mo")
            if hist is None or len(hist) < 10:
                return 0, "no data"

            closes = hist['Close'].values
            volumes = hist['Volume'].values

            # Last 5 days vs previous 5 days
            recent_close = closes[-5:]
            prev_close = closes[-10:-5] if len(closes) >= 10 else closes[:5]
            recent_vol = volumes[-5:]
            prev_vol = volumes[-10:-5] if len(volumes) >= 10 else volumes[:5]

            import numpy as np

            price_trend = (float(np.mean(recent_close)) - float(np.mean(prev_close))) / float(np.mean(prev_close)) * 100
            vol_trend = float(np.mean(recent_vol)) / float(np.mean(prev_vol)) if float(np.mean(prev_vol)) > 0 else 1.0

            # Momentum score: positive = bullish, negative = bearish
            if price_trend > 2 and vol_trend > 1.3:
                momentum_score = 2  # Strong bullish
            elif price_trend > 1:
                momentum_score = 1  # Mild bullish
            elif price_trend < -2 and vol_trend > 1.3:
                momentum_score = -2  # Strong bearish
            elif price_trend < -1:
                momentum_score = -1  # Mild bearish
            else:
                momentum_score = 0  # Neutral

            _cache_set(symbol, "pcr_proxy", momentum_score)

        except Exception:
            return 0, "pcr calc error"

    # Apply direction-based scoring
    if direction.upper() in ("BUY", "STRONG BUY"):
        if momentum_score >= 2:
            return 5, "Bullish momentum (PCR proxy)"
        elif momentum_score <= -2:
            return -5, "Bearish momentum against BUY"
        return 0, ""
    elif direction.upper() in ("SELL", "STRONG SELL"):
        if momentum_score <= -2:
            return 5, "Bearish momentum (PCR proxy)"
        elif momentum_score >= 2:
            return -5, "Bullish momentum against SELL"
        return 0, ""

    return 0, ""

# =============================
# 16. WEEKLY TECHNICAL ALIGNMENT
# =============================
def get_weekly_technical(symbol, direction):
    """
    Multi-timeframe alignment: daily + weekly RSI/MACD.
    Daily BUY + Weekly BUY = much stronger.
    Daily BUY + Weekly SELL = conflicting.

    Returns: (adjustment, detail)
    """
    cached = _cache_get(symbol, "weekly_tech")
    if cached is not None:
        weekly_rsi, weekly_macd = cached
    else:
        try:
            import yfinance as yf
            for suffix in [".NS", ".BO"]:
                try:
                    t = yf.Ticker(f"{symbol}{suffix}")
                    hist = t.history(period="6mo", interval="1wk")
                    if hist is not None and len(hist) >= 20:
                        # Weekly RSI
                        closes = hist['Close']
                        delta = closes.diff()
                        gain = delta.where(delta > 0, 0.0)
                        loss = (-delta).where(delta < 0, 0.0)
                        ag = gain.rolling(14, min_periods=14).mean().iloc[-1]
                        al = loss.rolling(14, min_periods=14).mean().iloc[-1]
                        weekly_rsi = 100 - (100 / (1 + ag / al)) if al != 0 else 100.0

                        # Weekly MACD
                        ema12 = closes.ewm(span=12, adjust=False).mean()
                        ema26 = closes.ewm(span=26, adjust=False).mean()
                        macd_line = ema12 - ema26
                        signal_line = macd_line.ewm(span=9, adjust=False).mean()
                        weekly_macd = "BULLISH" if macd_line.iloc[-1] > signal_line.iloc[-1] else "BEARISH"

                        _cache_set(symbol, "weekly_tech", (round(weekly_rsi, 1), weekly_macd))
                        break
                except Exception:
                    continue
            else:
                return 0, "no weekly data"
        except Exception:
            return 0, "weekly calc error"

        weekly_rsi, weekly_macd = _cache_get(symbol, "weekly_tech") or (None, None)
        if weekly_rsi is None:
            return 0, "no weekly data"

    # Get daily signals for comparison
    daily_rsi = get_rsi(symbol)
    daily_macd = get_macd_signal(symbol)

    if daily_rsi is None:
        return 0, "no daily data"

    if direction.upper() in ("BUY", "STRONG BUY"):
        daily_bullish = daily_rsi < 50 or daily_macd == "BULLISH"
        weekly_bullish = weekly_rsi < 50 or weekly_macd == "BULLISH"

        if daily_bullish and weekly_bullish:
            return 8, f"D+W aligned BUY (wRSI={weekly_rsi:.0f} wMACD={weekly_macd})"
        elif daily_bullish and not weekly_bullish:
            return -5, f"Weekly AGAINST buy (wRSI={weekly_rsi:.0f} wMACD={weekly_macd})"
        return 0, f"wRSI={weekly_rsi:.0f} wMACD={weekly_macd}"

    elif direction.upper() in ("SELL", "STRONG SELL"):
        daily_bearish = daily_rsi > 50 or daily_macd == "BEARISH"
        weekly_bearish = weekly_rsi > 50 or weekly_macd == "BEARISH"

        if daily_bearish and weekly_bearish:
            return 8, f"D+W aligned SELL (wRSI={weekly_rsi:.0f} wMACD={weekly_macd})"
        elif daily_bearish and not weekly_bearish:
            return -5, f"Weekly AGAINST sell (wRSI={weekly_rsi:.0f} wMACD={weekly_macd})"
        return 0, f"wRSI={weekly_rsi:.0f} wMACD={weekly_macd}"

    return 0, ""

# =============================
# 17. SECTOR MOMENTUM
# =============================
def get_sector_momentum(symbol, all_signals_today=None):
    """
    If multiple stocks in the same sector are generating BUY signals,
    the sector has momentum — boost all signals in that sector.

    Args:
        symbol: current stock
        all_signals_today: list of (stock, direction) tuples from current run

    Returns: (adjustment, detail)
    """
    if not all_signals_today:
        return 0, ""

    my_sector = get_sector(symbol)
    if my_sector == "Unknown":
        return 0, ""

    same_sector = 0
    same_direction = 0
    for sig_stock, sig_dir in all_signals_today:
        if sig_stock == symbol:
            continue
        sig_sector = get_sector(sig_stock)
        if sig_sector == my_sector:
            same_sector += 1
            same_direction += 1

    if same_sector >= 3:
        return 8, f"Sector momentum: {same_sector} stocks in {my_sector}"
    elif same_sector >= 2:
        return 4, f"Sector signal: {same_sector} in {my_sector}"
    return 0, ""

# =============================
# 18. NEWS AGE DETECTION
# =============================
def check_news_age(text, sheet_history, days=3):
    """
    Check if the same/similar announcement was filed before.
    If so, the price has already reacted — skip.

    Args:
        text: current announcement text
        sheet_history: past signal rows
        days: lookback window

    Returns:
        (is_old, detail)
        is_old: True if duplicate/old news
    """
    if not sheet_history or not text:
        return False, ""

    # Normalize text for comparison
    text_clean = text[:100].lower().strip()
    cutoff = datetime.now() - timedelta(days=days)

    for row in sheet_history:
        if len(row) < 8:
            continue

        date_str = str(row[0]).strip()
        past_text = str(row[7]).strip().lower()[:100] if len(row) > 7 else ""
        past_stock = str(row[1]).strip().upper()

        # Parse date
        past_date = None
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%d %b %Y"]:
            try:
                past_date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue

        if not past_date or past_date < cutoff:
            continue

        # Check text similarity (simple overlap)
        if past_text and len(past_text) > 20:
            # Check if >60% of words overlap
            words_new = set(text_clean.split())
            words_old = set(past_text.split())
            if len(words_new) > 3 and len(words_old) > 3:
                overlap = len(words_new & words_old) / max(len(words_new), len(words_old))
                if overlap > 0.6:
                    return True, f"Similar news filed {date_str[:10]} ({overlap:.0%} overlap)"

    return False, ""

# =============================
# 19. EARNINGS SURPRISE
# =============================
def get_earnings_context(symbol):
    """
    Check recent earnings trajectory from yfinance financials.
    Revenue/profit growth > 20% YoY = strong BUY context.
    Revenue/profit decline > 20% = SELL context.

    Returns: (score, detail)
        score: -5 to +8
    """
    cached = _cache_get(symbol, "earnings")
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t = yf.Ticker(f"{symbol}{suffix}")
                fin = t.quarterly_financials
                if fin is None or fin.empty or len(fin.columns) < 2:
                    continue

                # Get latest and YoY quarter revenue
                latest = fin.iloc[:, 0]  # Most recent quarter
                yoy = fin.iloc[:, min(3, len(fin.columns)-1)]  # Same quarter last year (or oldest)

                revenue_now = latest.get("Total Revenue", 0)
                revenue_prev = yoy.get("Total Revenue", 0)

                if revenue_now and revenue_prev and revenue_prev > 0:
                    growth = ((revenue_now - revenue_prev) / abs(revenue_prev)) * 100

                    if growth > 30:
                        result = (8, f"Revenue +{growth:.0f}% YoY (strong growth)")
                    elif growth > 15:
                        result = (4, f"Revenue +{growth:.0f}% YoY")
                    elif growth < -20:
                        result = (-5, f"Revenue {growth:.0f}% YoY (declining)")
                    elif growth < -10:
                        result = (-3, f"Revenue {growth:.0f}% YoY")
                    else:
                        result = (0, f"Revenue {growth:+.0f}% YoY")

                    _cache_set(symbol, "earnings", result)
                    return result
            except Exception:
                continue
    except Exception:
        pass

    result = (0, "no earnings data")
    _cache_set(symbol, "earnings", result)
    return result

# =============================
# 20. DYNAMIC MODEL WEIGHTS
# =============================
def load_model_weights():
    """
    Load dynamic model weights from model_weights.json.
    These are computed by tracker.py based on per-model accuracy.

    Returns: dict of model_name -> weight, or None if file not found.
    """
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_weights.json")
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("weights", None)
    except Exception:
        return None

# =============================
# 21. BULK/BLOCK DEAL DETECTION
# =============================
def detect_bulk_block_deal(text):
    """
    Detect bulk/block deal mentions in announcement text.

    Returns: (is_deal, direction, boost, detail)
    """
    import re
    t = text.lower()

    # Block deal patterns
    block_patterns = [
        r'block\s*deal', r'bulk\s*deal', r'large\s*trade',
        r'acquired\s+\d+[\.,]?\d*\s*%', r'purchased\s+\d+[\.,]?\d*\s*lakh',
        r'open\s*market\s*purchas', r'open\s*market\s*acquisit',
    ]

    is_deal = any(re.search(p, t) for p in block_patterns)
    if not is_deal:
        return False, "", 0, ""

    # Determine direction
    buy_words = ["acquired", "purchased", "bought", "buying", "accumulation", "increase"]
    sell_words = ["sold", "divested", "disposed", "selling", "offloaded", "decrease"]

    is_buy = any(w in t for w in buy_words)
    is_sell = any(w in t for w in sell_words)

    # Promoter involvement is stronger signal
    is_promoter = any(w in t for w in ["promoter", "promotor", "chairman", "managing director"])

    if is_buy and is_promoter:
        return True, "BUY", 10, "Promoter/insider block BUY"
    elif is_buy:
        return True, "BUY", 6, "Block deal BUY detected"
    elif is_sell and is_promoter:
        return True, "SELL", 10, "Promoter/insider block SELL"
    elif is_sell:
        return True, "SELL", 6, "Block deal SELL detected"

    return True, "", 3, "Block/bulk deal detected"


# =============================
# 22. COMPREHENSIVE ENRICHMENT (v2 — includes all 10 new checks)
# =============================
def enrich_signal(symbol, direction, reasons=None, deal_value_cr=0,
                  sheet_history=None, announcement_text="", all_signals_today=None):
    """
    One-call function to get ALL market intelligence for a signal.
    Used by both wordf and multi_ai for consistent enrichment.
    v2: Now includes liquidity, 52w context, delivery, PCR, weekly tech,
        sector momentum, news age, earnings, block deals.

    Returns dict with all adjustments and details.
    """
    result = {
        "total_adjustment": 0,
        "freshness_stale": False,
        "freshness_change": 0.0,
        "volume_ratio": 1.0,
        "volume_boost": 0,
        "mcap_boost": 0,
        "technical_boost": 0,
        "technical_detail": "",
        "stack_count": 0,
        "stack_boost": 0,
        "time_weight": 1.0,
        "pattern_boost": 0,
        "pattern_detail": "",
        "is_liquid": True,
        "liquidity_reason": "",
        "week52_boost": 0,
        "delivery_boost": 0,
        "pcr_boost": 0,
        "weekly_tech_boost": 0,
        "sector_boost": 0,
        "is_old_news": False,
        "earnings_boost": 0,
        "block_deal": False,
        "details": [],
    }

    try:
        # ── GATE 1: Liquidity filter ──
        is_liquid, liq_reason = check_liquidity(symbol)
        result["is_liquid"] = is_liquid
        result["liquidity_reason"] = liq_reason
        if not is_liquid:
            result["details"].append(f"ILLIQUID: {liq_reason}")
            return result  # Skip all further checks for illiquid stocks

        # ── GATE 2: News age check ──
        if announcement_text and sheet_history:
            is_old, old_detail = check_news_age(announcement_text, sheet_history)
            result["is_old_news"] = is_old
            if is_old:
                result["details"].append(f"OLD NEWS: {old_detail}")
                return result  # Skip — already priced in

        # 1. Freshness
        is_stale, change = check_freshness(symbol)
        result["freshness_stale"] = is_stale
        result["freshness_change"] = change
        if is_stale:
            result["details"].append(f"STALE: already {change:+.1f}% today")

        # 2. Volume
        ratio, v_boost = get_volume_ratio(symbol)
        result["volume_ratio"] = ratio
        result["volume_boost"] = v_boost
        result["total_adjustment"] += v_boost
        if v_boost != 0:
            result["details"].append(f"Vol {ratio:.1f}x ({v_boost:+d})")

        # 3. Market cap weight
        mcap_boost = get_market_cap_weight(symbol, deal_value_cr)
        result["mcap_boost"] = mcap_boost
        result["total_adjustment"] += mcap_boost
        if mcap_boost > 0:
            result["details"].append(f"MCap boost +{mcap_boost}")

        # 4. Technical confirmation (daily)
        tech_adj, tech_detail = get_technical_confirmation(symbol, direction)
        result["technical_boost"] = tech_adj
        result["technical_detail"] = tech_detail
        result["total_adjustment"] += tech_adj
        if tech_adj != 0:
            result["details"].append(f"Tech {tech_adj:+d} ({tech_detail})")

        # 5. Signal stacking
        if sheet_history:
            count, s_boost = get_signal_stack_count(symbol, sheet_history)
            result["stack_count"] = count
            result["stack_boost"] = s_boost
            result["total_adjustment"] += s_boost
            if count > 0:
                result["details"].append(f"Stack {count}x ({s_boost:+d})")

        # 6. Time weight
        result["time_weight"] = get_time_weight()

        # 7. Pattern reliability
        if reasons:
            p_boost, p_detail = get_pattern_reliability_boost(reasons)
            result["pattern_boost"] = p_boost
            result["pattern_detail"] = p_detail
            result["total_adjustment"] += p_boost
            if p_boost != 0:
                result["details"].append(f"Pattern {p_boost:+d}")

        # ── NEW CHECKS (v2) ──

        # 8. 52-week context
        w52_adj, w52_detail = get_52week_context(symbol, direction)
        result["week52_boost"] = w52_adj
        result["total_adjustment"] += w52_adj
        if w52_adj != 0:
            result["details"].append(f"52w {w52_adj:+d} ({w52_detail})")

        # 9. Delivery % proxy
        del_score, del_detail = get_delivery_proxy(symbol)
        result["delivery_boost"] = del_score
        result["total_adjustment"] += del_score
        if del_score != 0:
            result["details"].append(f"Delivery {del_score:+d} ({del_detail})")

        # 10. PCR proxy
        pcr_adj, pcr_detail = get_pcr_proxy(symbol, direction)
        result["pcr_boost"] = pcr_adj
        result["total_adjustment"] += pcr_adj
        if pcr_adj != 0:
            result["details"].append(f"PCR {pcr_adj:+d} ({pcr_detail})")

        # 11. Weekly technical alignment
        wt_adj, wt_detail = get_weekly_technical(symbol, direction)
        result["weekly_tech_boost"] = wt_adj
        result["total_adjustment"] += wt_adj
        if wt_adj != 0:
            result["details"].append(f"Weekly {wt_adj:+d} ({wt_detail})")

        # 12. Sector momentum
        if all_signals_today:
            sec_adj, sec_detail = get_sector_momentum(symbol, all_signals_today)
            result["sector_boost"] = sec_adj
            result["total_adjustment"] += sec_adj
            if sec_adj != 0:
                result["details"].append(f"Sector {sec_adj:+d} ({sec_detail})")

        # 13. Earnings context
        earn_score, earn_detail = get_earnings_context(symbol)
        result["earnings_boost"] = earn_score
        result["total_adjustment"] += earn_score
        if earn_score != 0:
            result["details"].append(f"Earnings {earn_score:+d} ({earn_detail})")

        # 14. Block/bulk deal detection
        if announcement_text:
            is_deal, deal_dir, deal_boost, deal_detail = detect_bulk_block_deal(announcement_text)
            result["block_deal"] = is_deal
            if is_deal and deal_boost > 0:
                # Only boost if deal direction matches signal direction
                if deal_dir == "" or deal_dir == direction.upper().replace("STRONG ", ""):
                    result["total_adjustment"] += deal_boost
                    result["details"].append(f"Block {deal_boost:+d} ({deal_detail})")
                elif deal_dir and deal_dir != direction.upper().replace("STRONG ", ""):
                    result["total_adjustment"] -= deal_boost
                    result["details"].append(f"Block CONFLICT -{deal_boost} ({deal_detail})")

    except Exception as e:
        result["details"].append(f"enrichment error: {str(e)[:50]}")

    return result
