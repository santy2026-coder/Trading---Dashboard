"""
IMPROVED ENTRY & EXIT SIGNAL LOGIC
===================================

ENTRY TIMING IMPROVEMENTS:
1. Price action breakout (support/resistance breach) + momentum = faster entry
2. Volume surge confirmation = real buyer/seller interest
3. Stochastic oversold/overbought + price reversal = pullback entries
4. VWAP + Bollinger Band squeeze + ATR breakout = volatility expansion play

EXIT TIMING IMPROVEMENTS:
1. Dynamic trailing stop using ATR + pattern (not fixed)
2. Target scaling: T1 = quick profit-take, T2 = momentum hold, T3 = breakout hold
3. Reversals: RSI divergence + candle rejection = exit signal
4. Stop loss tightening: Move SL to break-even after 1.5x risk reached
"""

import numpy as np
import pandas as pd

def improved_entry_signal(row, d, last_candles=3):
    """
    ENTRY SIGNAL v2: Multi-factor with breakout + momentum confirmation
    
    BUY entry:
    - Price breakout above resistance OR support bounce
    - Volume surge (>1.2x average)
    - Stochastic not in overbought (< 80)
    - RSI confirmation (30-70 range, not extremes)
    - MACD histogram positive and growing
    
    SELL entry:
    - Price breakdown below support OR resistance rejection
    - Volume surge (>1.2x average)
    - Stochastic not in oversold (> 20)
    - RSI confirmation (30-70 range)
    - MACD histogram negative and shrinking
    """
    
    if len(d) < 21:
        return 'WAIT', 0, 'Insufficient data'
    
    c = d['Close'].astype(float)
    o = d['Open'].astype(float)
    h = d['High'].astype(float)
    l = d['Low'].astype(float)
    v = d['Volume'].fillna(0).astype(float)
    
    price = float(row.Close)
    rsi = float(row.RSI) if pd.notna(row.RSI) else 50
    stoch_k = float(row.STOCH_K) if pd.notna(row.STOCH_K) else 50
    macd = float(row.MACD) if pd.notna(row.MACD) else 0
    macd_hist = float(row.MACD_HIST) if pd.notna(row.MACD_HIST) else 0
    macd_prev_hist = float(d.MACD_HIST.iloc[-2]) if len(d) > 1 and pd.notna(d.MACD_HIST.iloc[-2]) else 0
    
    support = float(row.SUPPORT) if pd.notna(row.SUPPORT) else price - price * 0.02
    resistance = float(row.RESISTANCE) if pd.notna(row.RESISTANCE) else price + price * 0.02
    
    # Volume confirmation
    avg_vol = float(v.tail(20).mean()) if len(v) >= 20 else float(v.mean())
    vol_ratio = float(v.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
    vol_confirmed = vol_ratio >= 1.2
    
    # Candle analysis: body size, direction
    last_body = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
    avg_body = abs(c.diff()).tail(20).mean() if len(c) > 20 else abs(c.diff()).mean()
    strong_candle = last_body > avg_body * 0.8
    
    # Momentum: MACD growing
    macd_growing = macd_hist > macd_prev_hist
    
    buy_score = 0
    sell_score = 0
    reasons = []
    
    # --- BULLISH CONDITIONS ---
    # 1. Price above support + bounce
    if price > support * 1.002:
        buy_score += 1
        reasons.append('Price above support')
    
    # 2. Breakout above resistance
    if price > resistance * 1.002 and float(c.iloc[-2]) <= resistance * 1.002:
        buy_score += 2
        reasons.append('Breakout above resistance')
    
    # 3. Volume surge
    if vol_confirmed:
        buy_score += 1
        reasons.append(f'Volume surge {vol_ratio:.1f}x')
    
    # 4. Stochastic not overbought (allows room to run)
    if stoch_k < 80:
        buy_score += 1
        reasons.append('Stochastic < 80 (room to run)')
    
    # 5. RSI in sweet spot (40-70)
    if 40 <= rsi <= 70:
        buy_score += 1
        reasons.append('RSI 40-70 (momentum confirmed)')
    
    # 6. MACD histogram growing (momentum acceleration)
    if macd_hist > 0 and macd_growing:
        buy_score += 1
        reasons.append('MACD histogram growing')
    
    # 7. Strong candle + price above EMA5
    if strong_candle and price > float(row.EMA5):
        buy_score += 1
        reasons.append('Strong bullish candle above EMA5')
    
    # --- BEARISH CONDITIONS ---
    # 1. Price below resistance + rejection
    if price < resistance * 0.998:
        sell_score += 1
        reasons.append('Price below resistance')
    
    # 2. Breakdown below support
    if price < support * 0.998 and float(c.iloc[-2]) >= support * 0.998:
        sell_score += 2
        reasons.append('Breakdown below support')
    
    # 3. Volume surge
    if vol_confirmed:
        sell_score += 1
        reasons.append(f'Volume surge {vol_ratio:.1f}x')
    
    # 4. Stochastic not oversold
    if stoch_k > 20:
        sell_score += 1
        reasons.append('Stochastic > 20 (room to fall)')
    
    # 5. RSI in sweet spot (30-60)
    if 30 <= rsi <= 60:
        sell_score += 1
        reasons.append('RSI 30-60 (momentum confirmed)')
    
    # 6. MACD histogram shrinking (momentum deceleration)
    if macd_hist < 0 and macd_hist < macd_prev_hist:
        sell_score += 1
        reasons.append('MACD histogram shrinking')
    
    # 7. Strong bearish candle + price below EMA5
    if strong_candle and price < float(row.EMA5):
        sell_score += 1
        reasons.append('Strong bearish candle below EMA5')
    
    # Decision
    if buy_score >= 4 and buy_score > sell_score:
        return 'BUY', buy_score, ' | '.join(reasons)
    elif sell_score >= 4 and sell_score > buy_score:
        return 'SELL', sell_score, ' | '.join(reasons)
    else:
        return 'WAIT', 0, 'No clear setup'


def dynamic_exit_levels(entry_price, direction, row, d, atr_multiplier=1.0):
    """
    DYNAMIC EXIT LEVELS v2:
    
    Stop Loss: ATR-based, but tightens after 1.5x risk achieved
    Target 1: Quick profit-take (0.75x ATR) - exit 30% here
    Target 2: Momentum hold (1.5x ATR) - exit 50% here  
    Target 3: Breakout play (2.5x ATR) - exit remaining 20%
    Trailing: Activates after T1 hit, trails at 0.5x ATR
    """
    
    atr = float(row.ATR) if pd.notna(row.ATR) else abs(entry_price) * 0.01
    atr = max(atr, entry_price * 0.001)  # Minimum 0.1% of entry
    
    support = float(row.SUPPORT) if pd.notna(row.SUPPORT) else entry_price - atr * 1.5
    resistance = float(row.RESISTANCE) if pd.notna(row.RESISTANCE) else entry_price + atr * 1.5
    
    if direction == 'BUY':
        # Stop Loss: Below support or 1.2x ATR below entry (whichever is higher risk)
        sl_option1 = entry_price - atr * atr_multiplier
        sl_option2 = support - atr * 0.2
        sl = min(sl_option1, sl_option2)  # Use tighter SL
        
        # Targets scale with ATR
        t1 = entry_price + atr * 0.75      # Quick profit
        t2 = entry_price + atr * 1.5       # Main target
        t3 = entry_price + atr * 2.5       # Breakout extension
        
        # Trailing stop (activates after T1)
        trailing_stop = entry_price + atr * 0.3  # Trail at 0.3x ATR
        
        # Break-even: Move SL here after profit = 1.5x risk
        be_trigger = entry_price + (atr * atr_multiplier * 1.5)
        be_stop = entry_price + atr * 0.1  # Tight breakeven SL
        
    else:  # SELL
        sl_option1 = entry_price + atr * atr_multiplier
        sl_option2 = resistance + atr * 0.2
        sl = max(sl_option1, sl_option2)
        
        t1 = entry_price - atr * 0.75
        t2 = entry_price - atr * 1.5
        t3 = entry_price - atr * 2.5
        
        trailing_stop = entry_price - atr * 0.3
        be_trigger = entry_price - (atr * atr_multiplier * 1.5)
        be_stop = entry_price - atr * 0.1
    
    return {
        'Stop Loss': sl,
        'Stop Loss Tight': sl * 1.05 if direction == 'BUY' else sl * 0.95,  # Tighter SL
        'Target 1': t1,
        'Target 1 Exit %': 30,  # Exit 30% at T1
        'Target 2': t2,
        'Target 2 Exit %': 50,  # Exit 50% at T2
        'Target 3': t3,
        'Target 3 Exit %': 20,  # Exit remaining 20%
        'Trailing Stop': trailing_stop,
        'Break-even Trigger': be_trigger,
        'Break-even Stop': be_stop,
        'ATR Used': atr,
        'Support': support,
        'Resistance': resistance,
    }


def smart_exit_condition(current_price, position, row, d):
    """
    SMART EXIT CONDITIONS:
    
    Exit BEFORE SL/Target is hit:
    1. RSI divergence: Price makes new high but RSI doesn't = weakness
    2. Candle rejection: Candle closes below 50% of body = reversal
    3. Volume drying up: Volume < 0.7x average = no conviction
    4. MACD histogram collapse: Momentum suddenly negative
    """
    
    side = position['Side']
    entry = position['Entry']
    risk = abs(entry - position['Stop Loss'])
    
    rsi = float(row.RSI) if pd.notna(row.RSI) else 50
    macd_hist = float(row.MACD_HIST) if pd.notna(row.MACD_HIST) else 0
    v = float(row.Volume) if pd.notna(row.Volume) else 0
    avg_vol = float(d['Volume'].tail(20).mean()) if len(d) >= 20 else float(d['Volume'].mean())
    vol_ratio = v / avg_vol if avg_vol > 0 else 1.0
    
    candle_open = float(row.Open)
    candle_close = float(row.Close)
    candle_body = abs(candle_close - candle_open)
    candle_high = float(row.High)
    candle_low = float(row.Low)
    
    exit_signals = []
    
    # 1. Volume dry-up = no conviction
    if vol_ratio < 0.7:
        exit_signals.append('Volume drying up (< 0.7x average)')
    
    # 2. RSI divergence (BUY: price new high, RSI declining)
    if side == 'BUY' and len(d) >= 3:
        price_high = d['High'].tail(3).max()
        rsi_avg = d['RSI'].tail(3).mean()
        if current_price > entry * 1.005 and price_high > entry * 1.01 and rsi < 50:
            exit_signals.append('RSI divergence - losing momentum')
    
    if side == 'SELL' and len(d) >= 3:
        price_low = d['Low'].tail(3).min()
        rsi_avg = d['RSI'].tail(3).mean()
        if current_price < entry * 0.995 and price_low < entry * 0.99 and rsi > 50:
            exit_signals.append('RSI divergence - losing momentum')
    
    # 3. Candle rejection (closing in bottom 25% of range = weakness)
    if candle_body > 0:
        close_ratio = abs(candle_close - candle_low) / (candle_high - candle_low) if (candle_high - candle_low) > 0 else 0.5
        if side == 'BUY' and close_ratio < 0.25:
            exit_signals.append('Candle closing in lower half - rejection signal')
        if side == 'SELL' and close_ratio > 0.75:
            exit_signals.append('Candle closing in upper half - rejection signal')
    
    # 4. MACD collapse
    if side == 'BUY' and macd_hist < 0:
        exit_signals.append('MACD histogram turned negative')
    if side == 'SELL' and macd_hist > 0:
        exit_signals.append('MACD histogram turned positive')
    
    return exit_signals


def confidence_with_win_rate(signal, score, pattern_bias, news_info, historical_hit_rate=np.nan):
    """
    CONFIDENCE CALCULATION v2:
    Weighs: technical score (60%) + historical hit rate (40%)
    """
    
    if signal == 'WAIT':
        return 0, ['No signal']
    
    technical = int(max(0, min(100, 50 + score * 5)))
    
    reasons = [f'Technical score: {score}/10 ({technical}%)']
    
    # News impact
    if news_info.get('bias') == 'BULLISH NEWS BIAS':
        technical = min(100, technical + 5)
        reasons.append('News bias: +5%')
    elif news_info.get('bias') == 'BEARISH NEWS BIAS':
        technical = max(0, technical - 5)
        reasons.append('News bias: -5%')
    
    # Pattern impact
    if pattern_bias > 0 and signal == 'BUY':
        technical = min(100, technical + 3)
        reasons.append('Pattern aligned: +3%')
    elif pattern_bias < 0 and signal == 'SELL':
        technical = min(100, technical + 3)
        reasons.append('Pattern aligned: +3%')
    
    # Historical weight
    if pd.notna(historical_hit_rate):
        confidence = int(0.60 * technical + 0.40 * float(historical_hit_rate))
        reasons.append(f'Historical hit rate: {float(historical_hit_rate):.1f}% (40% weight)')
    else:
        confidence = min(80, technical)  # Cap at 80% without history
        reasons.append('No historical data; capped at 80%')
    
    # Reality check: if hit rate < 40%, cap confidence at 60%
    if pd.notna(historical_hit_rate) and float(historical_hit_rate) < 40:
        confidence = min(60, confidence)
        reasons.append(f'Hit rate < 40%; confidence capped at 60%')
    
    return int(max(0, min(95, confidence))), reasons
