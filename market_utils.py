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
# 12. COMPREHENSIVE ENRICHMENT
# =============================
def enrich_signal(symbol, direction, reasons=None, deal_value_cr=0, sheet_history=None):
    """
    One-call function to get ALL market intelligence for a signal.
    Used by both wordf and multi_ai for consistent enrichment.

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
        "details": [],
    }

    try:
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

        # 4. Technical confirmation
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

    except Exception as e:
        result["details"].append(f"enrichment error: {str(e)[:50]}")

    return result
