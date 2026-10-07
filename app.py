if callable(globals().get('update_open_paper_positions')):
    auto_events = update_open_paper_positions(price)
else:
    auto_events = []

if auto_events:
    for ev in auto_events:
        st.toast(ev)

pcr_oi, pcr_summary, pcr_status = option_pcr(symbol)
pattern_bias, current_patterns = candle_pattern_bias(d)

# =============== UPGRADED SIGNAL ENGINE ===============
if improved_entry_signal is not None:
    # Use improved signal with volume + breakout + momentum confirmation
    sig, score, signal_reason = improved_entry_signal(last, d)

    # Map improved signal to strength labels
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
    # Fallback to legacy signal if upgrade module is not available
    strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = multifactor_signal(last, d, pcr_oi, pattern_bias)
    sig, score = signal(last, pattern_bias)

# Use improved exit levels if available
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
critical = critical_market_analysis(strength, mf_score, trend_now, vol_ratio, pcr_oi, news_info, price, levels.get('Pivot',price), prev_close_now)
summary_bt, summary_stats = backtest(symbol, '1y')

macro=macro_snapshot()
fd_live=fii_dii_feed()
institutional_score,institutional_text=institutional_bias(fd_live)
basket=top10_performance()
basket_score=0
if not basket.empty:
    valid=basket['Change %'].dropna()
    if len(valid): basket_score=1 if valid.mean()>0.15 else -1 if valid.mean()<-0.15 else 0
macro_score=0
for key in ('USD/INR','Crude Oil'):
    ch=macro.get(key,{}).get('change',np.nan)
    if pd.notna(ch):
        macro_score += (-1 if key=='USD/INR' and ch>0.2 else 1 if key=='USD/INR' and ch<-0.2 else 0)
        macro_score += (-1 if key=='Crude Oil' and ch>0.8 else 1 if key=='Crude Oil' and ch<-0.8 else 0)

# Apply enhanced signal only if using legacy mode
if improved_entry_signal is None:
    sig,mf_score,strength,live_confirmation=enhanced_signal(sig, mf_score, pattern_bias, news_info, macro_score, institutional_score, basket_score, pcr_oi)

# Fast MTF trigger: allows earlier entries without making PCR/news/macro the primary trigger.
if fast_mtf['signal']=='BUY' and fast_mtf['score']>=75 and fast_mtf['trend_15m'] in ('BULLISH TREND','STRONG UPTREND') and sig in ('WAIT','BUY'):
    sig='BUY'
    mf_score=max(float(mf_score), min(10.0, 6.5 + (fast_mtf['score']-70)/20))
    strength='STRONG BUY' if fast_mtf['score']>=85 else 'BUY BIAS'
elif fast_mtf['signal']=='SELL' and fast_mtf['score']>=75 and fast_mtf['trend_15m'] in ('BEARISH TREND','STRONG DOWNTREND') and sig in ('WAIT','SELL'):
    sig='SELL'
    mf_score=min(float(mf_score), max(0.0, 3.5 - (fast_mtf['score']-70)/20))
    strength='STRONG SELL' if fast_mtf['score']>=85 else 'SELL BIAS'

score=mf_score

# Use improved exit levels if available and signal is active
if dynamic_exit_levels is not None and sig in ('BUY', 'SELL'):
    levels = dynamic_exit_levels(price, sig, last, d)
else:
    levels = trade_levels(price, last, strength)

option_suggest, option_suggest_status = option_suggestions(symbol, price)
hit_rate=summary_stats.get('buy_win_rate' if sig=='BUY' else 'sell_win_rate',np.nan) if summary_stats else np.nan

# Use improved confidence if available
if confidence_with_win_rate is not None and sig != 'WAIT':
    confidence, confidence_reasons = confidence_with_win_rate(sig, score, pattern_bias, news_info, hit_rate)
else:
    confidence, confidence_reasons = confidence_score(last, d, pcr_oi, news_info, pattern_bias, trend_now, hit_rate, macro_score, institutional_score, basket_score, sig)

if pd.notna(hit_rate) and float(hit_rate)<50:
    confidence=min(confidence,60)
    confidence_reasons.append('Historical hit rate below 50%; confidence capped at 60%')
trend_idx, trend_upper, trend_lower, trend_slope = trendline_values(d)
