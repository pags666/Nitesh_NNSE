"""
alerts.py — Telegram Real-Time Alerts
======================================
Sends instant BUY/SELL alerts to Telegram when signals are generated.

Setup:
  1. Create a Telegram bot via @BotFather
  2. Get your Chat ID via @userinfobot
  3. Set env vars:
     $env:TELEGRAM_BOT_TOKEN="your_bot_token"
     $env:TELEGRAM_CHAT_ID="your_chat_id"

Usage:
  from alerts import send_alert, send_batch_alerts
  send_alert("RELIANCE", "STRONG BUY", 92, "NCLT approves resolution plan")

Test:
  python alerts.py
"""

import os
import sys
import time
from datetime import datetime

import requests
import pytz

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# =============================
# CONFIG
# =============================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
IST = pytz.timezone("Asia/Kolkata")
MIN_SCORE_DEFAULT = 80  # Only alert for score >= this
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Dedup: track recently sent alerts
_sent_alerts = {}  # key = (stock, action) → timestamp
DEDUP_WINDOW = 3600  # 1 hour — don't re-send same stock+action within this

# =============================
# CHECK SETUP
# =============================
def is_configured():
    """Check if Telegram is properly configured."""
    return bool(BOT_TOKEN) and bool(CHAT_ID)

# =============================
# SEND MESSAGE (low-level)
# =============================
def _send_telegram(text, parse_mode="HTML", max_retries=3):
    """Send a message to Telegram with retry logic."""
    if not is_configured():
        print(f"[TELEGRAM] Not configured — printing to console only")
        # Strip HTML tags for console output
        import re
        clean = re.sub(r'<[^>]+>', '', text)
        print(clean)
        return False

    for attempt in range(max_retries):
        try:
            resp = requests.post(API_URL, json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10)

            if resp.status_code == 200:
                return True
            elif resp.status_code == 429:
                # Rate limited
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(retry_after)
                continue
            else:
                print(f"[TELEGRAM] Error {resp.status_code}: {resp.text[:100]}")
                return False
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[TELEGRAM] Network error: {e}")
            return False

    return False

# =============================
# FORMAT ALERT MESSAGE
# =============================
def _format_alert(stock, action, score, reason, source="system"):
    """Format a premium-looking alert message."""
    now = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")

    # Choose emoji and styling
    if action == "STRONG BUY":
        icon = "🔥🟢"
        header = "STRONG BUY Signal"
    elif action == "BUY":
        icon = "🟢"
        header = "BUY Signal"
    elif action == "STRONG SELL":
        icon = "🔥🔴"
        header = "STRONG SELL Signal"
    elif action == "SELL":
        icon = "🔴"
        header = "SELL Signal"
    else:
        icon = "⚪"
        header = action

    msg = f"""{icon} <b>{header}</b>
━━━━━━━━━━━━━━━━━━━
<b>Stock:</b>  {stock}
<b>Score:</b>  {score}%
<b>Source:</b> {source}
<b>Reason:</b> {reason[:200]}
<b>Time:</b>   {now}
━━━━━━━━━━━━━━━━━━━"""

    return msg

# =============================
# SEND ALERT
# =============================
def send_alert(stock, action, score, reason, source="system", min_score=None):
    """
    Send a single alert for a stock signal.

    Args:
        stock: stock symbol
        action: "BUY", "SELL", "STRONG BUY", "STRONG SELL"
        score: confidence score (0-100)
        reason: reason string
        source: "wordf", "multi_ai", etc.
        min_score: minimum score to trigger alert (default: MIN_SCORE_DEFAULT)

    Returns: True if sent successfully
    """
    if min_score is None:
        min_score = MIN_SCORE_DEFAULT

    # Score filter
    if score < min_score:
        return False

    # Dedup check
    key = (stock.upper(), action.upper())
    if key in _sent_alerts:
        elapsed = time.time() - _sent_alerts[key]
        if elapsed < DEDUP_WINDOW:
            return False  # Already sent recently

    msg = _format_alert(stock, action, score, reason, source)
    success = _send_telegram(msg)

    if success:
        _sent_alerts[key] = time.time()
        print(f"[ALERT] Sent: {stock} {action} ({score}%)")

    return success

# =============================
# SEND BATCH ALERTS
# =============================
def send_batch_alerts(signals, source="system", min_score=None):
    """
    Send alerts for multiple signals with rate limiting.

    Args:
        signals: list of dicts with keys: stock, action, score, reason
        source: source name
        min_score: minimum score threshold

    Returns: number of alerts sent
    """
    sent = 0
    for sig in signals:
        stock = sig.get("stock", sig.get("ticker", ""))
        action = sig.get("action", "")
        score = sig.get("score", sig.get("confidence", 0))
        reason = sig.get("reason", sig.get("reasoning", ""))

        if isinstance(score, str):
            try:
                score = int(score)
            except (ValueError, TypeError):
                score = 0

        if send_alert(stock, action, score, reason, source, min_score):
            sent += 1
            time.sleep(1)  # Telegram rate limit: ~30 msgs/sec, be safe

    return sent

# =============================
# SEND SUMMARY
# =============================
def send_summary(buy_count, sell_count, top_picks, timestamp=None, source="system"):
    """Send a run summary to Telegram."""
    if not timestamp:
        timestamp = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")

    total = buy_count + sell_count

    if total == 0:
        msg = f"""📊 <b>Signal Scan Complete</b>
━━━━━━━━━━━━━━━━━━━
No actionable signals found
<b>Source:</b> {source}
<b>Time:</b> {timestamp}
━━━━━━━━━━━━━━━━━━━"""
    else:
        top_str = ", ".join(top_picks[:5]) if top_picks else "—"
        emoji = "🟢" if buy_count > sell_count else ("🔴" if sell_count > buy_count else "⚪")

        msg = f"""{emoji} <b>Signal Scan Complete</b>
━━━━━━━━━━━━━━━━━━━
<b>BUY Signals:</b>  {buy_count}
<b>SELL Signals:</b> {sell_count}
<b>Top Picks:</b>    {top_str}
<b>Source:</b>        {source}
<b>Time:</b>          {timestamp}
━━━━━━━━━━━━━━━━━━━"""

    return _send_telegram(msg)

# =============================
# INTEGRATION HELPERS
# =============================
def alert_from_wordf(signal_row):
    """Parse a wordf output row and send alert."""
    if len(signal_row) < 8:
        return False
    stock = str(signal_row[1]).strip()
    signal = str(signal_row[5]).strip()
    score = int(signal_row[4]) if str(signal_row[4]).isdigit() else 0
    reason = str(signal_row[7]).strip()
    return send_alert(stock, signal, score, reason, source="wordf v2")

def alert_from_multi_ai(signal_dict):
    """Parse a multi_ai result dict and send alert."""
    stock = signal_dict.get("ticker", "")
    action = signal_dict.get("action", "")
    score = signal_dict.get("score", 0)
    reason = signal_dict.get("reasoning", "")
    return send_alert(stock, action, score, reason, source="multi_ai v4")

# =============================
# TEST
# =============================
if __name__ == "__main__":
    print("=" * 50)
    print("  TELEGRAM ALERT SYSTEM — TEST")
    print("=" * 50)

    if not is_configured():
        print("\n  NOT CONFIGURED!")
        print("  Set these environment variables:")
        print("    $env:TELEGRAM_BOT_TOKEN=\"your_bot_token\"")
        print("    $env:TELEGRAM_CHAT_ID=\"your_chat_id\"")
        print("\n  To get a bot token: message @BotFather on Telegram")
        print("  To get your chat ID: message @userinfobot on Telegram")
    else:
        print(f"\n  Bot Token: {BOT_TOKEN[:10]}...")
        print(f"  Chat ID: {CHAT_ID}")
        print("\n  Sending test alert...")

        success = send_alert(
            "TEST_STOCK", "STRONG BUY", 95,
            "This is a test alert from your signal engine",
            source="test"
        )

        if success:
            print("  Test alert sent successfully!")
        else:
            print("  Failed to send test alert.")

    print("=" * 50)
