"""
tracker.py — Auto-Tracking + Feedback Loop
============================================
Tracks every signal's price performance at T+1, T+3, T+7 days.
Computes win rates per pattern and exports pattern_scores.json
for wordf and multi_ai to read on their next run.

Run daily after market hours:
  python tracker.py

Output:
  - 'tracker' Google Sheet with price tracking dashboard
  - pattern_scores.json with reliability scores
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta
from collections import defaultdict

import gspread
from gspread.exceptions import APIError
from oauth2client.service_account import ServiceAccountCredentials
import pytz

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# =============================
# CONFIG
# =============================
SHEET_ID = "1EQAhrCWmOzDD6VhVig4f3AffWMVZmrsrZKkgUc6h6WQ"
IST = pytz.timezone('Asia/Kolkata')

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# =============================
# AUTH
# =============================
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
ss = gc.open_by_key(SHEET_ID)

def with_backoff(op, max_attempts=5, base_delay=2.0):
    for attempt in range(max_attempts):
        try:
            return op()
        except APIError as e:
            if attempt < max_attempts - 1 and ("429" in str(e) or "quota" in str(e).lower()):
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise

# =============================
# YFINANCE HELPERS
# =============================
_price_cache = {}

def get_price_on_date(symbol, target_date, days_after=0):
    """
    Get closing price on or after target_date + days_after.
    Returns (price, actual_date) or (None, None).
    """
    cache_key = f"{symbol}:{target_date}:{days_after}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    try:
        import yfinance as yf

        start = target_date + timedelta(days=days_after)
        end = start + timedelta(days=5)  # Buffer for weekends

        for suffix in [".NS", ".BO"]:
            try:
                t = yf.Ticker(f"{symbol}{suffix}")
                hist = t.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
                if len(hist) >= 1:
                    price = round(float(hist['Close'].iloc[0]), 2)
                    actual = hist.index[0].strftime("%Y-%m-%d")
                    _price_cache[cache_key] = (price, actual)
                    return price, actual
            except Exception:
                continue
    except ImportError:
        print("[ERROR] yfinance not installed!")
    except Exception:
        pass

    _price_cache[cache_key] = (None, None)
    return None, None

# =============================
# READ SIGNALS FROM SHEETS
# =============================
def read_wordf_signals():
    """Read signals from wordf sheet."""
    signals = []
    try:
        ws = ss.worksheet("wordf")
        rows = ws.get_all_values()
        for row in rows[1:]:  # Skip header
            if len(row) < 8:
                continue
            time_str = str(row[0]).strip()
            stock = str(row[1]).strip().upper()
            signal = str(row[5]).strip().upper() if len(row) > 5 else ""
            score = str(row[4]).strip() if len(row) > 4 else "0"
            reasons = str(row[7]).strip() if len(row) > 7 else ""

            # Skip separator rows
            if stock in ("---", "", "LAST UPDATED") or "LAST UPDATED" in stock:
                continue
            if signal not in ("BUY", "SELL", "STRONG BUY", "STRONG SELL"):
                continue

            # Parse date
            sig_date = None
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    sig_date = datetime.strptime(time_str, fmt)
                    break
                except ValueError:
                    continue

            if sig_date:
                signals.append({
                    "date": sig_date,
                    "date_str": time_str,
                    "stock": stock,
                    "signal": signal,
                    "score": score,
                    "source": "wordf",
                    "reason": reasons[:200],
                })
    except Exception as e:
        print(f"[WARN] Could not read wordf: {e}")

    return signals

def read_multi_ai_signals():
    """Read signals from multi_ai sheet (rows 10+)."""
    signals = []
    try:
        ws = ss.worksheet("multi_ai")
        rows = ws.get_all_values()

        for row in rows[9:]:  # Skip dashboard rows (1-9)
            if len(row) < 7:
                continue
            date_str = str(row[0]).strip()
            time_str = str(row[1]).strip()
            stock = str(row[2]).strip().upper()
            signal = str(row[4]).strip().upper()
            score = str(row[5]).strip()
            reason = str(row[6]).strip() if len(row) > 6 else ""

            if stock in ("---", "", "NO SIGNALS"):
                continue
            if signal not in ("BUY", "SELL", "STRONG BUY", "STRONG SELL"):
                continue

            # Parse date
            sig_date = None
            combined = f"{date_str} {time_str}"
            for fmt in ["%d %b %Y %H:%M:%S", "%d %b %Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    sig_date = datetime.strptime(combined.strip(), fmt)
                    break
                except ValueError:
                    continue
            # Try date_str alone
            if not sig_date:
                for fmt in ["%d %b %Y", "%Y-%m-%d"]:
                    try:
                        sig_date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue

            if sig_date:
                signals.append({
                    "date": sig_date,
                    "date_str": f"{date_str} {time_str}",
                    "stock": stock,
                    "signal": signal,
                    "score": score,
                    "source": "multi_ai",
                    "reason": reason[:200],
                })
    except Exception as e:
        print(f"[WARN] Could not read multi_ai: {e}")

    return signals

# =============================
# READ EXISTING TRACKER DATA
# =============================
def read_existing_tracker():
    """Read already-tracked signals to avoid re-tracking."""
    tracked = set()
    try:
        ws = ss.worksheet("tracker")
        rows = ws.get_all_values()
        for row in rows[1:]:  # Skip header
            if len(row) >= 5:
                # Key = (date, stock, source)
                key = (str(row[0]).strip(), str(row[1]).strip().upper(), str(row[4]).strip().lower())
                tracked.add(key)
    except Exception:
        pass
    return tracked

# =============================
# CLASSIFY RESULT
# =============================
def classify_result(signal, change_pct):
    """Classify a price change as WIN, LOSS, or FLAT relative to signal direction."""
    if change_pct is None:
        return "PENDING"

    is_buy = "BUY" in signal.upper()

    if is_buy:
        if change_pct >= 1.0:
            return "WIN"
        elif change_pct <= -1.0:
            return "LOSS"
        return "FLAT"
    else:  # SELL signal
        if change_pct <= -1.0:
            return "WIN"
        elif change_pct >= 1.0:
            return "LOSS"
        return "FLAT"

# =============================
# TRACK ALL SIGNALS
# =============================
def track_signals():
    """Main tracking function."""
    now = datetime.now(IST)
    print("=" * 70)
    print("  SIGNAL TRACKER — Auto-Tracking + Feedback Loop")
    print(f"  {now.strftime('%d %b %Y %H:%M IST')}")
    print("=" * 70)

    # 1. Read all signals
    wordf_signals = read_wordf_signals()
    multi_ai_signals = read_multi_ai_signals()
    all_signals = wordf_signals + multi_ai_signals

    print(f"\n  Signals found: {len(wordf_signals)} wordf + {len(multi_ai_signals)} multi_ai = {len(all_signals)} total")

    if not all_signals:
        print("  No signals to track.")
        return

    # 2. Read existing tracker data
    already_tracked = read_existing_tracker()
    print(f"  Already tracked: {len(already_tracked)}")

    # 3. Track each signal
    results = []
    tracked_count = 0
    skipped_count = 0

    for sig in all_signals:
        key = (sig["date_str"][:10], sig["stock"], sig["source"])
        if key in already_tracked:
            skipped_count += 1
            continue

        stock = sig["stock"]
        sig_date = sig["date"]
        days_elapsed = (now.replace(tzinfo=None) - sig_date).days

        print(f"\n  Tracking: {stock} ({sig['signal']}) from {sig['date_str'][:10]} ({days_elapsed}d ago)")

        # Get T+0 price
        p0, _ = get_price_on_date(stock, sig_date, 0)
        if p0 is None:
            print(f"    No price data for {stock}")
            continue

        # Get T+1 price (if 1+ days elapsed)
        p1, change1, result1 = None, None, "PENDING"
        if days_elapsed >= 1:
            p1, _ = get_price_on_date(stock, sig_date, 1)
            if p1 and p0:
                change1 = round(((p1 - p0) / p0) * 100, 2)
                result1 = classify_result(sig["signal"], change1)
                print(f"    T+1: {p0} -> {p1} ({change1:+.2f}%) {result1}")

        # Get T+3 price (if 3+ days elapsed)
        p3, change3, result3 = None, None, "PENDING"
        if days_elapsed >= 3:
            p3, _ = get_price_on_date(stock, sig_date, 3)
            if p3 and p0:
                change3 = round(((p3 - p0) / p0) * 100, 2)
                result3 = classify_result(sig["signal"], change3)
                print(f"    T+3: {p0} -> {p3} ({change3:+.2f}%) {result3}")

        # Get T+7 price (if 7+ days elapsed)
        p7, change7, result7 = None, None, "PENDING"
        if days_elapsed >= 7:
            p7, _ = get_price_on_date(stock, sig_date, 7)
            if p7 and p0:
                change7 = round(((p7 - p0) / p0) * 100, 2)
                result7 = classify_result(sig["signal"], change7)
                print(f"    T+7: {p0} -> {p7} ({change7:+.2f}%) {result7}")

        results.append({
            "date": sig["date_str"][:16],
            "stock": stock,
            "signal": sig["signal"],
            "score": sig["score"],
            "source": sig["source"],
            "reason": sig["reason"][:150],
            "p0": p0 or "",
            "p1": p1 or "", "c1": f"{change1:+.2f}%" if change1 is not None else "", "r1": result1,
            "p3": p3 or "", "c3": f"{change3:+.2f}%" if change3 is not None else "", "r3": result3,
            "p7": p7 or "", "c7": f"{change7:+.2f}%" if change7 is not None else "", "r7": result7,
        })
        tracked_count += 1
        time.sleep(0.5)  # Rate limit yfinance

    print(f"\n  Tracked: {tracked_count} | Skipped (already done): {skipped_count}")

    # 4. Write to tracker sheet
    write_tracker_sheet(results)

    # 5. Compute pattern scores
    all_results = read_all_tracker_results()
    compute_pattern_scores(all_results)

def read_all_tracker_results():
    """Read all results from tracker sheet for pattern scoring."""
    results = []
    try:
        ws = ss.worksheet("tracker")
        rows = ws.get_all_values()
        for row in rows[1:]:
            if len(row) >= 16:
                results.append({
                    "stock": str(row[1]).strip().upper(),
                    "signal": str(row[2]).strip().upper(),
                    "source": str(row[4]).strip(),
                    "reason": str(row[5]).strip(),
                    "r1": str(row[9]).strip().upper(),
                    "c1": str(row[8]).strip().replace("%", "").replace("+", ""),
                    "r3": str(row[12]).strip().upper(),
                    "c3": str(row[11]).strip().replace("%", "").replace("+", ""),
                    "r7": str(row[15]).strip().upper(),
                    "c7": str(row[14]).strip().replace("%", "").replace("+", ""),
                })
    except Exception as e:
        print(f"  [WARN] Could not read tracker results: {e}")
    return results

# =============================
# WRITE TRACKER SHEET
# =============================
def write_tracker_sheet(results):
    """Write tracking results to Google Sheet."""
    if not results:
        print("  No new results to write.")
        return

    try:
        ws = ss.worksheet("tracker")
    except Exception:
        ws = ss.add_worksheet(title="tracker", rows="2000", cols="20")

    # Check if header exists
    existing = ws.get_all_values()
    if not existing:
        header = [
            "Date", "Stock", "Signal", "Score", "Source", "Reason",
            "Price T+0", "Price T+1", "Change T+1", "Result T+1",
            "Price T+3", "Change T+3", "Result T+3",
            "Price T+7", "Change T+7", "Result T+7",
        ]
        with_backoff(lambda: ws.append_row(header))

    # Append results
    rows_to_add = []
    for r in results:
        rows_to_add.append([
            r["date"], r["stock"], r["signal"], r["score"], r["source"],
            r["reason"],
            r["p0"], r["p1"], r["c1"], r["r1"],
            r["p3"], r["c3"], r["r3"],
            r["p7"], r["c7"], r["r7"],
        ])

    if rows_to_add:
        with_backoff(lambda: ws.append_rows(rows_to_add))
        print(f"  Written {len(rows_to_add)} rows to tracker sheet")

    # Apply formatting
    try:
        format_tracker_sheet(ws, len(existing or []), len(rows_to_add))
    except Exception as e:
        print(f"  [WARN] Formatting skipped: {e}")

def format_tracker_sheet(ws, start_row, num_rows):
    """Apply color formatting to tracker results."""
    C_WIN  = {"red": 0.04, "green": 0.22, "blue": 0.12}
    C_LOSS = {"red": 0.28, "green": 0.04, "blue": 0.04}
    C_FLAT = {"red": 0.15, "green": 0.15, "blue": 0.18}
    C_GREEN_TEXT = {"red": 0.3, "green": 1.0, "blue": 0.5}
    C_RED_TEXT = {"red": 1.0, "green": 0.35, "blue": 0.35}
    C_GRAY_TEXT = {"red": 0.6, "green": 0.6, "blue": 0.65}

    formats = []
    for i in range(num_rows):
        row_num = start_row + i + 1  # 1-indexed
        rows = ws.get_all_values()
        if row_num >= len(rows):
            continue
        row = rows[row_num]

        # Color result columns (J, M, P = indices 9, 12, 15)
        for col_idx, col_letter in [(9, "J"), (12, "M"), (15, "P")]:
            if col_idx < len(row):
                result = str(row[col_idx]).strip().upper()
                if result == "WIN":
                    formats.append({"range": f"{col_letter}{row_num+1}", "format": {
                        "backgroundColor": C_WIN,
                        "textFormat": {"foregroundColor": C_GREEN_TEXT, "bold": True}
                    }})
                elif result == "LOSS":
                    formats.append({"range": f"{col_letter}{row_num+1}", "format": {
                        "backgroundColor": C_LOSS,
                        "textFormat": {"foregroundColor": C_RED_TEXT, "bold": True}
                    }})
                elif result == "FLAT":
                    formats.append({"range": f"{col_letter}{row_num+1}", "format": {
                        "backgroundColor": C_FLAT,
                        "textFormat": {"foregroundColor": C_GRAY_TEXT}
                    }})

    if formats:
        with_backoff(lambda: ws.batch_format(formats))

# =============================
# COMPUTE PATTERN SCORES
# =============================
def compute_pattern_scores(all_results):
    """Compute win rates per pattern and export to JSON."""
    if not all_results:
        print("  No results to compute pattern scores from.")
        return

    # Group by pattern keywords
    pattern_stats = defaultdict(lambda: {
        "wins_t1": 0, "losses_t1": 0, "flat_t1": 0,
        "wins_t3": 0, "losses_t3": 0, "flat_t3": 0,
        "wins_t7": 0, "losses_t7": 0, "flat_t7": 0,
        "returns_t3": [], "count": 0,
    })

    # Source-level stats
    source_stats = defaultdict(lambda: {
        "wins_t3": 0, "losses_t3": 0, "flat_t3": 0, "count": 0
    })

    # Overall stats
    overall = {"wins_t1": 0, "losses_t1": 0, "wins_t3": 0, "losses_t3": 0,
               "wins_t7": 0, "losses_t7": 0, "total": 0}

    # Common pattern keywords to extract
    PATTERN_KEYS = [
        "nclt approval", "resolution plan", "order secured", "order received",
        "contract awarded", "work order", "letter of award", "l1 bidder",
        "record profit", "ebitda", "buyback", "bonus issue", "stock split",
        "debt-free", "deleveraging", "promoter buys", "turnaround",
        "acquisition", "demerger", "spin-off", "govt order", "defence",
        "sebi action", "fraud", "insolvency", "cirp", "default",
        "auditor resign", "pledge invoked", "production halt",
        "capacity expansion", "joint venture", "strategic partner",
        "revenue growth", "margin expansion", "rights issue", "qip",
    ]

    for r in all_results:
        reason = r.get("reason", "").lower()
        source = r.get("source", "unknown")

        # Find matching pattern
        matched_pattern = "other"
        for pk in PATTERN_KEYS:
            if pk in reason:
                matched_pattern = pk
                break

        stats = pattern_stats[matched_pattern]
        stats["count"] += 1

        # T+1
        if r["r1"] == "WIN": stats["wins_t1"] += 1; overall["wins_t1"] += 1
        elif r["r1"] == "LOSS": stats["losses_t1"] += 1; overall["losses_t1"] += 1
        else: stats["flat_t1"] += 1

        # T+3
        if r["r3"] == "WIN": stats["wins_t3"] += 1; overall["wins_t3"] += 1
        elif r["r3"] == "LOSS": stats["losses_t3"] += 1; overall["losses_t3"] += 1
        else: stats["flat_t3"] += 1
        try:
            if r["c3"]:
                stats["returns_t3"].append(float(r["c3"]))
        except (ValueError, TypeError):
            pass

        # T+7
        if r["r7"] == "WIN": stats["wins_t7"] += 1; overall["wins_t7"] += 1
        elif r["r7"] == "LOSS": stats["losses_t7"] += 1; overall["losses_t7"] += 1
        else: stats["flat_t7"] += 1

        # Source stats (T+3)
        src = source_stats[source]
        src["count"] += 1
        if r["r3"] == "WIN": src["wins_t3"] += 1
        elif r["r3"] == "LOSS": src["losses_t3"] += 1
        else: src["flat_t3"] += 1

        overall["total"] += 1

    # Build output JSON
    patterns_json = {}
    for pattern, stats in sorted(pattern_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        total_t3 = stats["wins_t3"] + stats["losses_t3"]
        total_t1 = stats["wins_t1"] + stats["losses_t1"]

        patterns_json[pattern] = {
            "win_rate_t1": round(stats["wins_t1"] / total_t1, 2) if total_t1 > 0 else 0.5,
            "win_rate_t3": round(stats["wins_t3"] / total_t3, 2) if total_t3 > 0 else 0.5,
            "count": stats["count"],
            "avg_return_t3": round(sum(stats["returns_t3"]) / len(stats["returns_t3"]), 2) if stats["returns_t3"] else 0,
        }

    total_t1 = overall["wins_t1"] + overall["losses_t1"]
    total_t3 = overall["wins_t3"] + overall["losses_t3"]
    total_t7 = overall["wins_t7"] + overall["losses_t7"]

    output = {
        "patterns": patterns_json,
        "overall": {
            "win_rate_t1": round(overall["wins_t1"] / total_t1, 2) if total_t1 > 0 else 0,
            "win_rate_t3": round(overall["wins_t3"] / total_t3, 2) if total_t3 > 0 else 0,
            "win_rate_t7": round(overall["wins_t7"] / total_t7, 2) if total_t7 > 0 else 0,
            "total_signals": overall["total"],
        },
        "by_source": {
            src: {
                "win_rate_t3": round(s["wins_t3"] / (s["wins_t3"] + s["losses_t3"]), 2) if (s["wins_t3"] + s["losses_t3"]) > 0 else 0,
                "count": s["count"],
            }
            for src, s in source_stats.items()
        },
        "last_updated": datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }

    # Write JSON file
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pattern_scores.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Pattern scores exported to: {json_path}")

    # Print summary dashboard
    print(f"\n{'='*70}")
    print(f"  TRACKER SUMMARY")
    print(f"{'='*70}")
    print(f"  Total signals tracked: {overall['total']}")
    if total_t1 > 0:
        print(f"  T+1 Win Rate: {overall['wins_t1']}/{total_t1} = {overall['wins_t1']/total_t1:.0%}")
    if total_t3 > 0:
        print(f"  T+3 Win Rate: {overall['wins_t3']}/{total_t3} = {overall['wins_t3']/total_t3:.0%}")
    if total_t7 > 0:
        print(f"  T+7 Win Rate: {overall['wins_t7']}/{total_t7} = {overall['wins_t7']/total_t7:.0%}")

    # Best patterns
    print(f"\n  TOP PATTERNS (by T+3 win rate, min 3 signals):")
    sorted_patterns = sorted(
        [(k, v) for k, v in patterns_json.items() if v["count"] >= 3],
        key=lambda x: x[1]["win_rate_t3"], reverse=True
    )
    for i, (name, stats) in enumerate(sorted_patterns[:10]):
        wr = stats["win_rate_t3"]
        icon = "+++" if wr >= 0.75 else ("++" if wr >= 0.60 else ("+" if wr >= 0.50 else "-"))
        print(f"    {icon} {name:<25} {wr:.0%} win rate ({stats['count']} signals, avg {stats['avg_return_t3']:+.1f}%)")

    # Worst patterns
    worst = [p for p in sorted_patterns if p[1]["win_rate_t3"] < 0.50]
    if worst:
        print(f"\n  WEAK PATTERNS (below 50% win rate):")
        for name, stats in worst[:5]:
            print(f"    - {name:<25} {stats['win_rate_t3']:.0%} win rate ({stats['count']} signals)")

    # By source
    print(f"\n  BY SOURCE:")
    for src, s in output["by_source"].items():
        print(f"    {src:<12} {s['win_rate_t3']:.0%} win rate ({s['count']} signals)")

    print(f"{'='*70}")

# =============================
# ENTRY POINT
# =============================
if __name__ == "__main__":
    track_signals()
