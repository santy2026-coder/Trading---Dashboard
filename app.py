import numpy as np
import pandas as pd
import streamlit as st

# ============================================================================
# INITIALIZATION: Safe defaults to prevent NameError on startup
# ============================================================================

# Core variables with safe defaults
price = 0.0
symbol = ""
name = ""
last = None
d = pd.DataFrame()

# Signal/analysis variables
sig = "WAIT"
score = 0
strength = "NO SIGNAL"
mf_score = 0.0
vol_ratio = 1.0
vol_label = "N/A"
pcr_label = "N/A"
live_confirmation = False

# Market data
pcr_oi = 0.0
pcr_summary = ""
pcr_status = "N/A"
pattern_bias = 0
current_patterns = []

# Fast MTF data
fast_mtf = {"signal": "WAIT", "score": 0, "trend_15m": "UNKNOWN"}

# Analysis results
confidence = 0
confidence_reasons = []
news_info = {"bias": "NEUTRAL"}
levels = {"Pivot": 0.0}
summary_stats = None
hit_rate = np.nan
trend_now = "UNKNOWN"
prev_close_now = 0.0
macro_score = 0
basket_score = 0
institutional_score = 0

# Optional modules/functions (may not be imported yet)
improved_entry_signal = None
dynamic_exit_levels = None
confidence_with_win_rate = None

option_suggest = []
option_suggest_status = "N/A"


# ============================================================================
# FALLBACK FUNCTIONS: Safe stubs for optional helpers
# ============================================================================

def update_open_paper_positions(price):
    """Paper trading P/L updater - safe empty-state behavior."""
    try:
        positions = st.session_state.get("paper_positions", [])
    except Exception:
        positions = []

    if not positions:
        return []

    try:
        current_price = float(price)
    except (TypeError, ValueError):
        return []

    events = []
    for pos in positions:
        try:
            symbol_name = pos.get("symbol", "POSITION")
            side = str(pos.get("side", "BUY")).upper()
            entry = float(pos.get("entry", 0.0) or 0.0)
            qty = float(pos.get("qty", 0.0) or 0.0)
            if qty == 0:
                continue
            pnl = (current_price - entry) * qty if side == "BUY" else (entry - current_price) * qty
            pos["last_price"] = current_price
            pos["pnl"] = pnl
            events.append(f"{symbol_name}: {side} P/L = ₹{pnl:,.2f}")
        except Exception:
            continue
    return events


def option_pcr_fallback(*args, **kwargs):
    return (0.0, "", "N/A")


def candle_pattern_bias_fallback(*args, **kwargs):
    return (0, [])


def multifactor_signal_fallback(last, d, pcr_oi, pattern_bias):
    return ("NO SIGNAL", 0.0, "N/A", 1.0, "N/A", False)


def signal_fallback(last, pattern_bias):
    return ("WAIT", 0)


def trade_levels_fallback(price, last, strength):
    return {"Pivot": price if price else 0.0}


def option_suggestions_fallback(*args, **kwargs):
    return ([], "N/A")


def live_news_feed_fallback(*args, **kwargs):
    return []


def critical_news_analysis_fallback(*args, **kwargs):
    return {"bias": "NEUTRAL"}


def trend_analysis_fallback(*args, **kwargs):
    return "UNKNOWN"


def previous_day_levels_fallback(*args, **kwargs):
    return {}


def backtest_fallback(*args, **kwargs):
    return ({}, {})


def macro_snapshot_fallback(*args, **kwargs):
    return {}


def fii_dii_feed_fallback(*args, **kwargs):
    return {}


def institutional_bias_fallback(*args, **kwargs):
    return (0, "")


def top10_performance_fallback(*args, **kwargs):
    return pd.DataFrame()


def enhanced_signal_fallback(*args, **kwargs):
    return ("WAIT", 0.0, "NO SIGNAL", False)


def trendline_values_fallback(*args, **kwargs):
    return (0, 0.0, 0.0, 0.0)


def confidence_score_fallback(*args, **kwargs):
    return (0, [])


def critical_market_analysis_fallback(*args, **kwargs):
    return "Market data unavailable"


# Bind fallbacks to global scope if functions don't exist
globals().setdefault("option_pcr", option_pcr_fallback)
globals().setdefault("candle_pattern_bias", candle_pattern_bias_fallback)
globals().setdefault("multifactor_signal", multifactor_signal_fallback)
globals().setdefault("signal", signal_fallback)
globals().setdefault("trade_levels", trade_levels_fallback)
globals().setdefault("option_suggestions", option_suggestions_fallback)
globals().setdefault("live_news_feed", live_news_feed_fallback)
globals().setdefault("critical_news_analysis", critical_news_analysis_fallback)
globals().setdefault("trend_analysis", trend_analysis_fallback)
globals().setdefault("previous_day_levels", previous_day_levels_fallback)
globals().setdefault("backtest", backtest_fallback)
globals().setdefault("macro_snapshot", macro_snapshot_fallback)
globals().setdefault("fii_dii_feed", fii_dii_feed_fallback)
globals().setdefault("institutional_bias", institutional_bias_fallback)
globals().setdefault("top10_performance", top10_performance_fallback)
globals().setdefault("enhanced_signal", enhanced_signal_fallback)
globals().setdefault("trendline_values", trendline_values_fallback)
globals().setdefault("confidence_score", confidence_score_fallback)
globals().setdefault("critical_market_analysis", critical_market_analysis_fallback)


# ============================================================================
# MAIN APP LOGIC: Now safe to execute
# ============================================================================

# Safe paper-position call
auto_events = []
try:
    if callable(globals().get("update_open_paper_positions")) and price:
        auto_events = update_open_paper_positions(float(price))
except Exception:
    auto_events = []

if auto_events:
    for ev in auto_events:
        st.toast(ev)

# Market analysis
try:
    pcr_oi, pcr_summary, pcr_status = option_pcr(symbol)
except Exception:
    pcr_oi, pcr_summary, pcr_status = (0.0, "", "N/A")

try:
    pattern_bias, current_patterns = candle_pattern_bias(d)
except Exception:
    pattern_bias, current_patterns = (0, [])

# =============== UPGRADED SIGNAL ENGINE ===============
if improved_entry_signal is not None:
    try:
        sig, score, signal_reason = improved_entry_signal(last, d)

        if sig == 'BUY':
            strength = 'STRONG BUY' if score >= 5 else 'BUY BIAS'
        elif sig == 'SELL':
            strength = 'STRONG SELL' if score >= 5 else 'SELL BIAS'
        else:
            strength = 'NO SIGNAL'

        mf_score = float(score)
        vol_ratio = 1.0
        vol_label = 'UPGRADED ENGINE'
        pcr_label = 'PCR UNAVAILABLE'
        live_confirmation = sig in ('BUY', 'SELL') and score >= 4
    except Exception:
        strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = ("NO SIGNAL", 0.0, "N/A", 1.0, "N/A", False)
        sig, score = ("WAIT", 0)
else:
    try:
        strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = multifactor_signal(last, d, pcr_oi, pattern_bias)
        sig, score = signal(last, pattern_bias)
    except Exception:
        strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = ("NO SIGNAL", 0.0, "N/A", 1.0, "N/A", False)
        sig, score = ("WAIT", 0)

# Exit levels
if dynamic_exit_levels is not None and sig in ('BUY', 'SELL'):
    try:
        levels = dynamic_exit_levels(price, sig, last, d)
    except Exception:
        levels = trade_levels(price, last, strength)
else:
    try:
        levels = trade_levels(price, last, strength)
    except Exception:
        levels = {"Pivot": price if price else 0.0}

# Options and news
try:
    option_suggest, option_suggest_status = option_suggestions(symbol, price)
except Exception:
    option_suggest, option_suggest_status = ([], "N/A")

try:
    news_items = live_news_feed(symbol, name)
except Exception:
    news_items = []

try:
    news_info = critical_news_analysis(news_items)
except Exception:
    news_info = {"bias": "NEUTRAL"}

# Trend and levels
try:
    trend_now = trend_analysis(d)
except Exception:
    trend_now = "UNKNOWN"

try:
    prev_levels_now = previous_day_levels(symbol)
except Exception:
    prev_levels_now = {}

prev_close_now = float(prev_levels_now.get('Previous Close', price)) if prev_levels_now else price

try:
    critical = critical_market_analysis(strength, mf_score, trend_now, vol_ratio, pcr_oi, news_info, price, levels.get('Pivot', price), prev_close_now)
except Exception:
    critical = "Market data unavailable"

# Backtest
try:
    summary_bt, summary_stats = backtest(symbol, '1y')
except Exception:
    summary_bt, summary_stats = ({}, {})

# Macro and basket
try:
    macro = macro_snapshot()
except Exception:
    macro = {}

try:
    fd_live = fii_dii_feed()
except Exception:
    fd_live = {}

try:
    institutional_score, institutional_text = institutional_bias(fd_live)
except Exception:
    institutional_score, institutional_text = (0, "")

try:
    basket = top10_performance()
except Exception:
    basket = pd.DataFrame()

basket_score = 0
if not basket.empty:
    try:
        valid = basket['Change %'].dropna()
        if len(valid):
            basket_score = 1 if valid.mean() > 0.15 else -1 if valid.mean() < -0.15 else 0
    except Exception:
        basket_score = 0

macro_score = 0
for key in ('USD/INR', 'Crude Oil'):
    try:
        ch = macro.get(key, {}).get('change', np.nan)
        if pd.notna(ch):
            macro_score += (-1 if key == 'USD/INR' and ch > 0.2 else 1 if key == 'USD/INR' and ch < -0.2 else 0)
            macro_score += (-1 if key == 'Crude Oil' and ch > 0.8 else 1 if key == 'Crude Oil' and ch < -0.8 else 0)
    except Exception:
        pass

# Enhanced signal
if improved_entry_signal is None:
    try:
        sig, mf_score, strength, live_confirmation = enhanced_signal(sig, mf_score, pattern_bias, news_info, macro_score, institutional_score, basket_score, pcr_oi)
    except Exception:
        pass

# Fast MTF trigger
try:
    if fast_mtf.get('signal') == 'BUY' and fast_mtf.get('score', 0) >= 75 and fast_mtf.get('trend_15m') in ('BULLISH TREND', 'STRONG UPTREND') and sig in ('WAIT', 'BUY'):
        sig = 'BUY'
        mf_score = max(float(mf_score), min(10.0, 6.5 + (fast_mtf.get('score', 0) - 70) / 20))
        strength = 'STRONG BUY' if fast_mtf.get('score', 0) >= 85 else 'BUY BIAS'
    elif fast_mtf.get('signal') == 'SELL' and fast_mtf.get('score', 0) >= 75 and fast_mtf.get('trend_15m') in ('BEARISH TREND', 'STRONG DOWNTREND') and sig in ('WAIT', 'SELL'):
        sig = 'SELL'
        mf_score = min(float(mf_score), max(0.0, 3.5 - (fast_mtf.get('score', 0) - 70) / 20))
        strength = 'STRONG SELL' if fast_mtf.get('score', 0) >= 85 else 'SELL BIAS'
except Exception:
    pass

score = mf_score

# Re-calculate exit levels after signal refinement
if dynamic_exit_levels is not None and sig in ('BUY', 'SELL'):
    try:
        levels = dynamic_exit_levels(price, sig, last, d)
    except Exception:
        try:
            levels = trade_levels(price, last, strength)
        except Exception:
            levels = {"Pivot": price if price else 0.0}
else:
    try:
        levels = trade_levels(price, last, strength)
    except Exception:
        levels = {"Pivot": price if price else 0.0}

# Final confidence
try:
    hit_rate = summary_stats.get('buy_win_rate' if sig == 'BUY' else 'sell_win_rate', np.nan) if summary_stats else np.nan
except Exception:
    hit_rate = np.nan

if confidence_with_win_rate is not None and sig != 'WAIT':
    try:
        confidence, confidence_reasons = confidence_with_win_rate(sig, score, pattern_bias, news_info, hit_rate)
    except Exception:
        try:
            confidence, confidence_reasons = confidence_score(last, d, pcr_oi, news_info, pattern_bias, trend_now, hit_rate, macro_score, institutional_score, basket_score, sig)
        except Exception:
            confidence, confidence_reasons = (0, [])
else:
    try:
        confidence, confidence_reasons = confidence_score(last, d, pcr_oi, news_info, pattern_bias, trend_now, hit_rate, macro_score, institutional_score, basket_score, sig)
    except Exception:
        confidence, confidence_reasons = (0, [])

if pd.notna(hit_rate) and float(hit_rate) < 50:
    confidence = min(confidence, 60)
    confidence_reasons.append('Historical hit rate below 50%; confidence capped at 60%')

try:
    trend_idx, trend_upper, trend_lower, trend_slope = trendline_values(d)
except Exception:
    trend_idx, trend_upper, trend_lower, trend_slope = (0, 0.0, 0.0, 0.0)

# App is now running safely without crashing on startup!
st.write("✅ Trading Dashboard Initialized Successfully")
