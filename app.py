import numpy as np
import pandas as pd
import streamlit as st

# -----------------------------------------------------------------------------
# STARTUP SAFETY: avoid crashes when a value or helper is defined later in the app.
# -----------------------------------------------------------------------------
for _name, _value in {
    "price": 0.0,
    "symbol": "",
    "name": "",
    "last": None,
    "d": pd.DataFrame(),
    "fast_mtf": {"signal": "WAIT", "score": 0, "trend_15m": "UNKNOWN"},
    "improved_entry_signal": None,
    "dynamic_exit_levels": None,
    "confidence_with_win_rate": None,
    "summary_stats": None,
    "pattern_bias": 0,
    "macro_score": 0,
    "basket_score": 0,
    "institutional_score": 0,
    "signal": "WAIT",
    "score": 0,
    "strength": "NO SIGNAL",
    "mf_score": 0.0,
    "vol_ratio": 1.0,
    "vol_label": "N/A",
    "pcr_label": "N/A",
    "live_confirmation": False,
    "confidence": 0,
    "confidence_reasons": [],
    "news_info": {"bias": "NEUTRAL"},
    "levels": {},
    "pcr_oi": 0.0,
    "pcr_summary": "",
    "pcr_status": "N/A",
    "hit_rate": np.nan,
    "trend_now": "UNKNOWN",
    "prev_close_now": 0.0,
    "current_patterns": [],
    "option_suggest": [],
    "option_suggest_status": "N/A",
}.items():
    globals().setdefault(_name, _value)


def update_open_paper_positions(price):
    """Paper trading P/L updater with safe empty-state behavior."""
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


# If helper functions are not imported yet, bind safe placeholders so the app loads.
for _name, _func in {
    "option_pcr": lambda *args, **kwargs: (0.0, "", "N/A"),
    "candle_pattern_bias": lambda *args, **kwargs: (0, []),
    "multifactor_signal": lambda *args, **kwargs: ("NO SIGNAL", 0.0, "N/A", 1.0, "N/A", False),
    "signal": lambda *args, **kwargs: ("WAIT", 0),
    "trade_levels": lambda *args, **kwargs: {"Pivot": args[1] if len(args) > 1 else 0.0},
    "option_suggestions": lambda *args, **kwargs: ([], "N/A"),
    "live_news_feed": lambda *args, **kwargs: [],
    "critical_news_analysis": lambda *args, **kwargs: {"bias": "NEUTRAL"},
    "trend_analysis": lambda *args, **kwargs: "UNKNOWN",
    "previous_day_levels": lambda *args, **kwargs: {},
    "backtest": lambda *args, **kwargs: ({}, {}),
    "macro_snapshot": lambda *args, **kwargs: {},
    "fii_dii_feed": lambda *args, **kwargs: {},
    "institutional_bias": lambda *args, **kwargs: (0, ""),
    "top10_performance": lambda *args, **kwargs: pd.DataFrame(),
    "enhanced_signal": lambda *args, **kwargs: ("WAIT", 0.0, "NO SIGNAL", False),
    "trendline_values": lambda *args, **kwargs: (0, 0.0, 0.0, 0.0),
    "confidence_score": lambda *args, **kwargs: (0, []),
    "critical_market_analysis": lambda *args, **kwargs: "Market data unavailable",
}.items():
    globals().setdefault(_name, _func)


# Safe paper-position call.
auto_events = []
try:
    if callable(globals().get("update_open_paper_positions")):
        auto_events = update_open_paper_positions(float(price))
except Exception:
    auto_events = []

if auto_events:
    for ev in auto_events:
        st.toast(ev)

pcr_oi, pcr_summary, pcr_status = option_pcr(symbol)
pattern_bias, current_patterns = candle_pattern_bias(d)

# =============== UPGRADED SIGNAL ENGINE ===============
if improved_entry_signal is not None:
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
else:
    strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = multifactor_signal(last, d, pcr_oi, pattern_bias)
    sig, score = signal(last, pattern_bias)

if dynamic_exit_levels is not None and sig in ('BUY', 'SELL'):
    levels = dynamic_exit_levels(price, sig, last, d)
else:
    levels = trade_levels(price, last, strength)

option_suggest, option_suggest_status = option_suggestions(symbol, price)
news_items = live_news_feed(symbol, name)
news_info = critical_news_analysis(news_items)
trend_now = trend_analysis(d)
prev_levels_now = previous_day_levels(symbol)
prev_close_now = float(prev_levels_now.get('Previous Close', price)) if prev_levels_now else price
critical = critical_market_analysis(strength, mf_score, trend_now, vol_ratio, pcr_oi, news_info, price, levels.get('Pivot', price), prev_close_now)
summary_bt, summary_stats = backtest(symbol, '1y')

macro = macro_snapshot()
fd_live = fii_dii_feed()
institutional_score, institutional_text = institutional_bias(fd_live)
basket = top10_performance()
basket_score = 0
if not basket.empty:
    valid = basket['Change %'].dropna()
    if len(valid):
        basket_score = 1 if valid.mean() > 0.15 else -1 if valid.mean() < -0.15 else 0
macro_score = 0
for key in ('USD/INR', 'Crude Oil'):
    ch = macro.get(key, {}).get('change', np.nan)
    if pd.notna(ch):
        macro_score += (-1 if key == 'USD/INR' and ch > 0.2 else 1 if key == 'USD/INR' and ch < -0.2 else 0)
        macro_score += (-1 if key == 'Crude Oil' and ch > 0.8 else 1 if key == 'Crude Oil' and ch < -0.8 else 0)

if improved_entry_signal is None:
    sig, mf_score, strength, live_confirmation = enhanced_signal(sig, mf_score, pattern_bias, news_info, macro_score, institutional_score, basket_score, pcr_oi)

if fast_mtf['signal'] == 'BUY' and fast_mtf['score'] >= 75 and fast_mtf['trend_15m'] in ('BULLISH TREND', 'STRONG UPTREND') and sig in ('WAIT', 'BUY'):
    sig = 'BUY'
    mf_score = max(float(mf_score), min(10.0, 6.5 + (fast_mtf['score'] - 70) / 20))
    strength = 'STRONG BUY' if fast_mtf['score'] >= 85 else 'BUY BIAS'
elif fast_mtf['signal'] == 'SELL' and fast_mtf['score'] >= 75 and fast_mtf['trend_15m'] in ('BEARISH TREND', 'STRONG DOWNTREND') and sig in ('WAIT', 'SELL'):
    sig = 'SELL'
    mf_score = min(float(mf_score), max(0.0, 3.5 - (fast_mtf['score'] - 70) / 20))
    strength = 'STRONG SELL' if fast_mtf['score'] >= 85 else 'SELL BIAS'

score = mf_score

if dynamic_exit_levels is not None and sig in ('BUY', 'SELL'):
    levels = dynamic_exit_levels(price, sig, last, d)
else:
    levels = trade_levels(price, last, strength)

option_suggest, option_suggest_status = option_suggestions(symbol, price)
hit_rate = summary_stats.get('buy_win_rate' if sig == 'BUY' else 'sell_win_rate', np.nan) if summary_stats else np.nan

if confidence_with_win_rate is not None and sig != 'WAIT':
    confidence, confidence_reasons = confidence_with_win_rate(sig, score, pattern_bias, news_info, hit_rate)
else:
    confidence, confidence_reasons = confidence_score(last, d, pcr_oi, news_info, pattern_bias, trend_now, hit_rate, macro_score, institutional_score, basket_score, sig)

if pd.notna(hit_rate) and float(hit_rate) < 50:
    confidence = min(confidence, 60)
    confidence_reasons.append('Historical hit rate below 50%; confidence capped at 60%')

trend_idx, trend_upper, trend_lower, trend_slope = trendline_values(d)
