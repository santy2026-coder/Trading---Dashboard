import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
from urllib.parse import quote
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title='Advanced Trading Dashboard', page_icon='[CHART]', layout='wide')

# ---------------- STATE ----------------
for k, v in {'logged_in':False,'paper_trades':[],'realized_pnl':0.0,'balance':100000.0,'risk_per_trade':1.0,'max_position_value':100000.0,'auto_exit':False,'last_data_status':'Unknown'}.items():
    if k not in st.session_state: st.session_state[k] = v
if 'demo_password' not in st.session_state: st.session_state.demo_password = 'admin123'

# ---------------- DISPLAY HELPERS ----------------
def fmt_num(value, decimals=2, prefix="", suffix=""):
    try:
        x=float(value)
        if not np.isfinite(x):
            return "N/A"
        return f"{prefix}{x:,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"

def fmt_price(value):
    return fmt_num(value, 2, "Rs. ")

def fmt_pct(value):
    return fmt_num(value, 2, "", "%")

# ---------------- LOGIN ----------------
def login_page():
    st.title('[CHART] Advanced Trading Dashboard')
    st.caption('Login')
    with st.form('login'):
        u = st.text_input('Username')
        p = st.text_input('Password', type='password')
        ok = st.form_submit_button('Login', use_container_width=True)
    if ok:
        if u == 'admin' and p == st.session_state.demo_password:
            st.session_state.logged_in = True
            st.rerun()
        st.error('Invalid username or password')
    with st.expander('Forgot Password / Demo Recovery'):
        st.caption('For this local/demo build only. Change credentials before production use.')
        recovery = st.text_input('Recovery PIN', type='password')
        new_pass = st.text_input('New Password', type='password')
        if st.button('Reset Password'):
            if recovery == os.getenv('TRADING_RECOVERY_PIN', '1234') and len(new_pass) >= 6:
                st.session_state.demo_password = new_pass
                st.success('Password reset for this session. Login again with username admin.')
            else:
                st.error('Invalid recovery PIN or password too short.')

if not st.session_state.logged_in:
    login_page(); st.stop()

# ---------------- SYMBOLS ----------------
NIFTY = {
'RELIANCE':'RELIANCE.NS','TCS':'TCS.NS','INFY':'INFY.NS','HDFCBANK':'HDFCBANK.NS','ICICIBANK':'ICICIBANK.NS','SBIN':'SBIN.NS','ITC':'ITC.NS','BHARTIARTL':'BHARTIARTL.NS','TATAMOTORS':'TATAMOTORS.NS','LT':'LT.NS','AXISBANK':'AXISBANK.NS','KOTAKBANK':'KOTAKBANK.NS','HINDUNILVR':'HINDUNILVR.NS','MARUTI':'MARUTI.NS','SUNPHARMA':'SUNPHARMA.NS','M&M':'M&M.NS','TITAN':'TITAN.NS','BAJFINANCE':'BAJFINANCE.NS','BAJAJFINSV':'BAJAJFINSV.NS','ADANIENT':'ADANIENT.NS','ADANIPORTS':'ADANIPORTS.NS','ASIANPAINT':'ASIANPAINT.NS','ULTRACEMCO':'ULTRACEMCO.NS','WIPRO':'WIPRO.NS','HCLTECH':'HCLTECH.NS','TECHM':'TECHM.NS','NTPC':'NTPC.NS','POWERGRID':'POWERGRID.NS','ONGC':'ONGC.NS','COALINDIA':'COALINDIA.NS','JSWSTEEL':'JSWSTEEL.NS','TATASTEEL':'TATASTEEL.NS','HINDALCO':'HINDALCO.NS','EICHERMOT':'EICHERMOT.NS','HEROMOTOCO':'HEROMOTOCO.NS','DRREDDY':'DRREDDY.NS','CIPLA':'CIPLA.NS','APOLLOHOSP':'APOLLOHOSP.NS','NESTLEIND':'NESTLEIND.NS','TRENT':'TRENT.NS','BEL':'BEL.NS','SHRIRAMFIN':'SHRIRAMFIN.NS'}
CRYPTO = {'BTC / USD':'BTC-USD','ETH / USD':'ETH-USD','SOL / USD':'SOL-USD','BNB / USD':'BNB-USD','XRP / USD':'XRP-USD','DOGE / USD':'DOGE-USD','ADA / USD':'ADA-USD','AVAX / USD':'AVAX-USD'}
INDEXES = {'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','NIFTY IT':'^CNXIT','NIFTY AUTO':'^CNXAUTO','NIFTY PHARMA':'^CNXPHARMA'}

@st.cache_data(ttl=30, show_spinner=False)
def data(symbol, period, interval):
    try:
        d=yf.download(symbol,period=period,interval=interval,auto_adjust=False,progress=False,threads=False)
        if d is None or d.empty:return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
        return d.dropna(subset=['Open','High','Low','Close']).copy()
    except Exception:return pd.DataFrame()

def indicators(d):
    d=d.copy(); c=d.Close.astype(float); h=d.High.astype(float); l=d.Low.astype(float); v=d.Volume.fillna(0).astype(float)
    for n in (5,21,50,200):
        d[f'EMA{n}']=c.ewm(span=n,adjust=False).mean(); d[f'SMA{n}']=c.rolling(n).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rs=gain/loss.replace(0,np.nan); d['RSI']=100-100/(1+rs)
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean(); d['MACD']=e12-e26; d['MACD_SIGNAL']=d.MACD.ewm(span=9,adjust=False).mean(); d['MACD_HIST']=d.MACD-d.MACD_SIGNAL
    pc=c.shift(1); tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); d['ATR']=tr.rolling(14).mean()
    tp=(h+l+c)/3; d['VWAP']=(tp*v).cumsum()/v.cumsum().replace(0,np.nan)
    mid=c.rolling(20).mean(); sd=c.rolling(20).std(); d['BB_MID']=mid; d['BB_UPPER']=mid+2*sd; d['BB_LOWER']=mid-2*sd
    lo=l.rolling(14).min(); hi=h.rolling(14).max(); d['STOCH_K']=100*(c-lo)/(hi-lo).replace(0,np.nan); d['STOCH_D']=d.STOCH_K.rolling(3).mean()
    tmean=tp.rolling(20).mean(); md=tp.rolling(20).apply(lambda x:np.mean(np.abs(x-np.mean(x))),raw=True); d['CCI']=(tp-tmean)/(0.015*md.replace(0,np.nan))
    up=h.diff(); down=-l.diff(); plus=pd.Series(np.where((up>down)&(up>0),up,0),index=d.index).rolling(14).sum(); minus=pd.Series(np.where((down>up)&(down>0),down,0),index=d.index).rolling(14).sum(); pdi=100*plus/d.ATR.replace(0,np.nan); mdi=100*minus/d.ATR.replace(0,np.nan); dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); d['ADX']=dx.rolling(14).mean()
    d['SUPPORT']=l.rolling(20).min(); d['RESISTANCE']=h.rolling(20).max(); ph=h.shift(1); pl=l.shift(1); pcc=c.shift(1); pivot=(ph+pl+pcc)/3; d['PIVOT']=pivot; d['R1']=2*pivot-pl; d['S1']=2*pivot-ph; d['R2']=pivot+(ph-pl); d['S2']=pivot-(ph-pl)
    diff=h.max()-l.min(); d.attrs['fib']={'0%':h.max(),'23.6%':h.max()-diff*.236,'38.2%':h.max()-diff*.382,'50%':h.max()-diff*.5,'61.8%':h.max()-diff*.618,'78.6%':h.max()-diff*.786,'100%':l.min()}
    return d

def candle_patterns(d):
    if len(d)<5:return []
    o,h,l,c=d.Open,d.High,d.Low,d.Close; body=(c-o).abs(); rng=(h-l).replace(0,np.nan); upper=h-pd.concat([o,c],axis=1).max(axis=1); lower=pd.concat([o,c],axis=1).min(axis=1)-l; i,p,p2=-1,-2,-3; out=[]
    if body.iloc[i]<=rng.iloc[i]*.1:out.append('Doji')
    if lower.iloc[i]>=body.iloc[i]*2 and upper.iloc[i]<=body.iloc[i]*.5:out.append('Hammer')
    if upper.iloc[i]>=body.iloc[i]*2 and lower.iloc[i]<=body.iloc[i]*.5:out.append('Shooting Star')
    if body.iloc[i]>=rng.iloc[i]*.85:out.append('Bullish Marubozu' if c.iloc[i]>o.iloc[i] else 'Bearish Marubozu')
    if c.iloc[i]>o.iloc[i] and c.iloc[p]<o.iloc[p] and o.iloc[i]<=c.iloc[p] and c.iloc[i]>=o.iloc[p]:out.append('Bullish Engulfing')
    if c.iloc[i]<o.iloc[i] and c.iloc[p]>o.iloc[p] and o.iloc[i]>=c.iloc[p] and c.iloc[i]<=o.iloc[p]:out.append('Bearish Engulfing')
    mid=(o.iloc[p]+c.iloc[p])/2
    if c.iloc[p]<o.iloc[p] and c.iloc[i]>o.iloc[i] and o.iloc[i]<c.iloc[p] and c.iloc[i]>mid:out.append('Piercing Pattern')
    if c.iloc[p]>o.iloc[p] and c.iloc[i]<o.iloc[i] and o.iloc[i]>c.iloc[p] and c.iloc[i]<mid:out.append('Dark Cloud Cover')
    if c.iloc[p2]<o.iloc[p2] and body.iloc[p]<body.iloc[p2]*.5 and c.iloc[i]>o.iloc[i] and c.iloc[i]>(o.iloc[p2]+c.iloc[p2])/2:out.append('Morning Star')
    if c.iloc[p2]>o.iloc[p2] and body.iloc[p]<body.iloc[p2]*.5 and c.iloc[i]<o.iloc[i] and c.iloc[i]<(o.iloc[p2]+c.iloc[p2])/2:out.append('Evening Star')
    if all(c.iloc[x]>o.iloc[x] for x in (i,p,p2)):out.append('Three White Soldiers')
    if all(c.iloc[x]<o.iloc[x] for x in (i,p,p2)):out.append('Three Black Crows')
    return list(dict.fromkeys(out))

def chart_patterns(d):
    if len(d)<30:return []
    c=d.Close; h=d.High; l=d.Low; r=c.tail(60); out=[]
    a=r.iloc[:len(r)//2].max(); b=r.iloc[len(r)//2:].max(); x=r.iloc[:len(r)//2].min(); y=r.iloc[len(r)//2:].min()
    if abs(a-b)/max(abs(a),1e-9)<.02:out.append('Possible Double Top')
    if abs(x-y)/max(abs(x),1e-9)<.02:out.append('Possible Double Bottom')
    xx=np.arange(len(r)); slope=np.polyfit(xx,r.values,1)[0]/r.mean()
    if slope>.001:out.append('Ascending Trend / Possible Channel')
    if slope<-.001:out.append('Descending Trend / Possible Channel')
    rh=h.tail(30).rolling(5).max().dropna(); rl=l.tail(30).rolling(5).min().dropna()
    if len(rh)>10 and len(rl)>10:
        hs=np.polyfit(np.arange(len(rh)),rh,1)[0]; ls=np.polyfit(np.arange(len(rl)),rl,1)[0]
        if abs(hs)<r.mean()*.002 and ls>0:out.append('Possible Ascending Triangle')
        if abs(ls)<r.mean()*.002 and hs<0:out.append('Possible Descending Triangle')
    if len(r)>=40:
        q=r.reset_index(drop=True); left=q.iloc[:15].mean(); middle=q.iloc[12:28].mean(); right=q.iloc[25:40].mean()
        if middle<left*.97 and abs(right/left-1)<.05:out.append('Possible Cup & Handle')
    return list(dict.fromkeys(out))

def signal(row):
    checks=[row.Close>row.EMA5,row.EMA5>row.EMA21,row.EMA21>row.EMA50,row.EMA50>row.EMA200,row.MACD>row.MACD_SIGNAL,row.RSI>50,row.Close>row.VWAP,row.ADX>20,row.Close>row.PIVOT]
    score=sum(bool(x) for x in checks if pd.notna(x))
    return ('BUY' if score>=7 else 'SELL' if score<=3 else 'WAIT'),score

@st.cache_data(ttl=60,show_spinner=False)
def quote(sym):
    d=data(sym,'5d','1d')
    if d.empty:return np.nan,np.nan
    q=float(d.Close.iloc[-1]); p=float(d.Close.iloc[-2]) if len(d)>1 else q; return q,(q-p)/p*100 if p else 0

@st.cache_data(ttl=120,show_spinner=False)
def news():
    try:
        u='https://query1.finance.yahoo.com/v1/finance/search?q='+quote_url('India stock market geopolitical crude oil')
        r=requests.get(u,headers={'User-Agent':'Mozilla/5.0'},timeout=10)
        return r.json().get('news',[])[:15] if r.ok else []
    except Exception:return []

def quote_url(s):return quote(s)

def pnl(t,price):
    return (price-t['Entry'])*t['Quantity'] if t['Side']=='BUY' else (t['Entry']-price)*t['Quantity']

def close_trade(i,price):
    t=st.session_state.paper_trades[i]; p=pnl(t,price); t['Exit']=price; t['Exit Time']=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); t['Final P/L']=p; t['Status']='CLOSED'; st.session_state.realized_pnl+=p



def trend_analysis(d):
    r = d.tail(30)
    last = d.iloc[-1]
    slope = np.polyfit(np.arange(len(r)), r['Close'].values, 1)[0] / max(float(r['Close'].mean()), 1e-9)
    adx = float(last['ADX']) if pd.notna(last['ADX']) else 0
    if last['EMA5'] > last['EMA21'] > last['EMA50'] and slope > 0.0008:
        return 'STRONG UPTREND' if adx >= 25 else 'BULLISH TREND'
    if last['EMA5'] < last['EMA21'] < last['EMA50'] and slope < -0.0008:
        return 'STRONG DOWNTREND' if adx >= 25 else 'BEARISH TREND'
    if abs(slope) < 0.0005:
        return 'SIDEWAYS / RANGE'
    return 'MIXED / TRANSITION'

def previous_day_trend(d):
    if len(d) < 3:
        return 'N/A'
    return trend_analysis(d.iloc[:-1].copy())

@st.cache_data(ttl=60, show_spinner=False)
def previous_day_levels(sym):
    q = data(sym, '5d', '1d')
    if q.empty or len(q) < 2:
        return {}
    p = q.iloc[-2]
    h, l, c = float(p.High), float(p.Low), float(p.Close)
    pivot = (h + l + c) / 3
    return {
        'Previous Close': c,
        'Previous High': h,
        'Previous Low': l,
        'Pivot': pivot,
        'R1': 2 * pivot - l,
        'S1': 2 * pivot - h,
        'R2': pivot + (h - l),
        'S2': pivot - (h - l),
    }

def volume_confirmation(d):
    if len(d) < 21:
        return 'NO DATA', 0.0
    v = float(d['Volume'].iloc[-1])
    avg = float(d['Volume'].tail(20).mean())
    ratio = v / avg if avg else 0
    if ratio >= 1.5:
        return 'STRONG VOLUME CONFIRMATION', ratio
    if ratio >= 1.1:
        return 'VOLUME CONFIRMATION', ratio
    return 'WEAK / NO VOLUME CONFIRMATION', ratio

def strength_from_score(score, volume_ok=True):
    if score >= 8 and volume_ok:
        return 'STRONG BUY'
    if score >= 6:
        return 'BUY BIAS'
    if score <= 1 and volume_ok:
        return 'STRONG SELL'
    if score <= 3:
        return 'SELL BIAS'
    return 'NO SIGNAL'

def multifactor_signal(row, d, pcr=None):
    base_sig, score = signal(row)
    volume_label, volume_ratio = volume_confirmation(d)
    vol_ok = volume_ratio >= 1.1
    pcr_score = 0
    pcr_label = 'PCR UNAVAILABLE'
    if pcr is not None and pd.notna(pcr):
        if pcr > 1.0:
            pcr_score = 1
            pcr_label = 'PCR bullish/put-heavy'
        elif pcr < 0.8:
            pcr_score = -1
            pcr_label = 'PCR bearish/call-heavy'
        else:
            pcr_label = 'PCR neutral'
    final_score = max(0, min(10, score + pcr_score))
    strength = strength_from_score(final_score, vol_ok)
    confirmation = (base_sig == 'BUY' and (vol_ok or pcr_score > 0)) or (base_sig == 'SELL' and (vol_ok or pcr_score < 0))
    return strength, final_score, volume_label, volume_ratio, pcr_label, confirmation



def trade_levels(price, row, strength):
    """Rule-based entry, SL, targets and dynamic trailing stop from live price/ATR/structure."""
    atr=float(row.get('ATR', np.nan)) if pd.notna(row.get('ATR', np.nan)) else price*0.01
    atr=max(atr, price*0.001)
    support=float(row.get('SUPPORT', np.nan)) if pd.notna(row.get('SUPPORT', np.nan)) else price-atr
    resistance=float(row.get('RESISTANCE', np.nan)) if pd.notna(row.get('RESISTANCE', np.nan)) else price+atr
    pivot=float(row.get('PIVOT', np.nan)) if pd.notna(row.get('PIVOT', np.nan)) else price
    direction='BUY' if 'BUY' in strength else 'SELL' if 'SELL' in strength else 'WAIT'
    if direction=='BUY':
        entry=price
        # Structure-aware initial stop: below recent support, but not excessively wide.
        sl=max(0.01, min(entry-atr, support-0.10*atr))
        risk=max(entry-sl, 0.5*atr)
        t1=entry+risk; t2=entry+2*risk; t3=max(entry+3*risk, resistance)
        trailing=max(sl, price-atr)
        be=entry
    elif direction=='SELL':
        entry=price
        sl=entry+atr
        if resistance>entry: sl=min(sl, resistance+0.10*atr)
        risk=max(sl-entry, 0.5*atr)
        t1=max(0.01, entry-risk); t2=max(0.01, entry-2*risk); t3=max(0.01, min(entry-3*risk, support))
        trailing=min(sl, price+atr)
        be=entry
    else:
        entry=price; sl=np.nan; t1=np.nan; t2=np.nan; t3=np.nan; trailing=np.nan; be=np.nan; risk=np.nan
    rr2=abs((t2-entry)/(entry-sl)) if direction in ('BUY','SELL') and entry!=sl else np.nan
    return {'Direction':direction,'Entry':entry,'Stop Loss':sl,'Target 1':t1,'Target 2':t2,'Target 3':t3,'Trailing Stop':trailing,'Break-even Stop':be,'Risk':risk,'RR to T2':rr2,'Support':support,'Resistance':resistance,'Pivot':pivot,'ATR':atr}

@st.cache_data(ttl=60, show_spinner=False)
def option_suggestions(symbol, underlying_price):
    """Return ATM and nearby OTM strikes with live option premium when Yahoo exposes a chain."""
    try:
        t=yf.Ticker(symbol); expiries=t.options
        if not expiries: return pd.DataFrame(), 'No option chain available'
        expiry=expiries[0]; chain=t.option_chain(expiry)
        calls=chain.calls.copy(); puts=chain.puts.copy()
        if calls.empty and puts.empty: return pd.DataFrame(), 'No contracts available'
        strikes=sorted(set(pd.to_numeric(pd.concat([calls.get('strike',pd.Series(dtype=float)),puts.get('strike',pd.Series(dtype=float))]),errors='coerce').dropna().tolist()))
        if not strikes: return pd.DataFrame(), 'No strikes available'
        atm=min(strikes,key=lambda x:abs(x-underlying_price))
        # Use the smallest listed strike interval around ATM.
        diffs=sorted({round(abs(strikes[i]-strikes[i-1]),8) for i in range(1,len(strikes)) if strikes[i]!=strikes[i-1]})
        step=diffs[0] if diffs else max(1, round(underlying_price*0.01))
        chosen=[]
        for side, mults in [('CALL',[0,1,2]),('PUT',[0,-1,-2])]:
            for m in mults:
                target=atm+m*step
                strike=min(strikes,key=lambda x:abs(x-target))
                if side=='CALL':
                    r=calls.iloc[(calls['strike']-strike).abs().argmin()] if not calls.empty else None
                else:
                    r=puts.iloc[(puts['strike']-strike).abs().argmin()] if not puts.empty else None
                premium=float(pd.to_numeric(r.get('lastPrice'),errors='coerce')) if r is not None and pd.notna(pd.to_numeric(r.get('lastPrice'),errors='coerce')) else np.nan
                label='ATM' if m==0 else ('OTM +1' if side=='CALL' else 'OTM -1') if abs(m)==1 else ('OTM +2' if side=='CALL' else 'OTM -2')
                chosen.append({'Type':side,'Moneyness':label,'Strike':strike,'Premium':premium,'Expiry':expiry})
        return pd.DataFrame(chosen), 'OK'
    except Exception:
        return pd.DataFrame(), 'Option chain unavailable'

@st.cache_data(ttl=300, show_spinner=False)
def backtest(symbol, period):
    d = data(symbol, period, '1d')
    if d.empty or len(d) < 80:
        return pd.DataFrame(), {}
    d = indicators(d).dropna(subset=['EMA5','EMA21','EMA50','EMA200','RSI','MACD','MACD_SIGNAL','VWAP','ADX','PIVOT']).copy()
    records = []
    for i in range(len(d) - 1):
        r = d.iloc[i]
        sig, score = signal(r)
        strength = 'STRONG BUY' if score >= 8 else 'BUY' if score >= 6 else 'STRONG SELL' if score <= 1 else 'SELL' if score <= 3 else 'NO SIGNAL'
        if sig == 'WAIT':
            continue
        nxt = float(d['Close'].iloc[i + 1])
        cur = float(r['Close'])
        ret = (nxt - cur) / cur if cur else 0
        win = ret > 0 if sig == 'BUY' else ret < 0
        records.append({
            'Date': d.index[i], 'Signal': sig, 'Strength': strength,
            'Score': score, 'Return %': ret * 100, 'Win': bool(win)
        })
    bt = pd.DataFrame(records)
    if bt.empty:
        return bt, {}
    total = len(bt)
    wins = int(bt['Win'].sum())
    losses = total - wins
    win_rate = wins / total * 100
    bt['Volume Confirmed'] = bt['Date'].map(lambda dt: True)
    buy = bt[bt['Signal'] == 'BUY']
    sell = bt[bt['Signal'] == 'SELL']
    # Volume confirmation is evaluated inside the backtest from the same historical bars.
    vol = d['Volume'].rolling(20).mean()
    vol_ratio = d['Volume'] / vol.replace(0, np.nan)
    ratio_map = vol_ratio.to_dict()
    bt['Volume Ratio'] = bt['Date'].map(ratio_map)
    bt['Volume Confirmed'] = bt['Volume Ratio'] >= 1.1
    buy_confirm = bt[(bt['Signal']=='BUY') & (bt['Volume Confirmed'])]
    sell_confirm = bt[(bt['Signal']=='SELL') & (bt['Volume Confirmed'])]
    stats = {
        'signals': total, 'wins': wins, 'losses': losses, 'win_rate': win_rate,
        'avg_return': float(bt['Return %'].mean()),
        'buy_win_rate': float(buy['Win'].mean() * 100) if len(buy) else np.nan,
        'sell_win_rate': float(sell['Win'].mean() * 100) if len(sell) else np.nan,
        'buy_confirm_win_rate': float(buy_confirm['Win'].mean() * 100) if len(buy_confirm) else np.nan,
        'sell_confirm_win_rate': float(sell_confirm['Win'].mean() * 100) if len(sell_confirm) else np.nan,
        'strong_buy_win_rate': float(bt.loc[bt['Strength']=='STRONG BUY','Win'].mean()*100) if (bt['Strength']=='STRONG BUY').any() else np.nan,
        'strong_sell_win_rate': float(bt.loc[bt['Strength']=='STRONG SELL','Win'].mean()*100) if (bt['Strength']=='STRONG SELL').any() else np.nan,
        'max_drawdown_pct': float((bt['Return %'].cumsum().cummax()-bt['Return %'].cumsum()).max()) if len(bt) else 0.0,
        'net_return_pct': float(bt['Return %'].sum()),
    }
    return bt, stats

@st.cache_data(ttl=60, show_spinner=False)
def option_pcr(symbol):
    try:
        t = yf.Ticker(symbol)
        expiries = t.options
        if not expiries:
            return np.nan, pd.DataFrame(), 'No option chain available'
        expiry = expiries[0]
        chain = t.option_chain(expiry)
        calls, puts = chain.calls.copy(), chain.puts.copy()
        call_oi = pd.to_numeric(calls.get('openInterest', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()
        put_oi = pd.to_numeric(puts.get('openInterest', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()
        call_vol = pd.to_numeric(calls.get('volume', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()
        put_vol = pd.to_numeric(puts.get('volume', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()
        pcr_oi = put_oi / call_oi if call_oi else np.nan
        pcr_vol = put_vol / call_vol if call_vol else np.nan
        summary = pd.DataFrame({
            'Metric': ['Expiry', 'Put OI', 'Call OI', 'OI PCR', 'Put Volume', 'Call Volume', 'Volume PCR'],
            'Value': [expiry, put_oi, call_oi, pcr_oi, put_vol, call_vol, pcr_vol]
        })
        return pcr_oi, summary, 'OK'
    except Exception as e:
        return np.nan, pd.DataFrame(), 'Option chain unavailable'

@st.cache_data(ttl=60, show_spinner=False)
def preopen_snapshot():
    # NSE's official pre-open page exposes indicative equilibrium price,
    # buy/sell quantities and imbalance; cloud requests can be blocked.
    try:
        headers = {'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*','Referer':'https://www.nseindia.com/'}
        s = requests.Session()
        s.get('https://www.nseindia.com/', headers=headers, timeout=8)
        r = s.get('https://www.nseindia.com/api/market-data-pre-open?key=ALL', headers=headers, timeout=8)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None

def opening_analysis(sym, d):
    daily = data(sym, '1mo', '1d')
    if daily.empty or len(daily) < 5:
        return {}
    prev = daily.iloc[-1]
    prev_close = float(prev.Close)
    atr = float(d['ATR'].iloc[-1]) if pd.notna(d['ATR'].iloc[-1]) else prev_close * 0.01
    # A transparent estimated range when official indicative pre-open data is unavailable.
    lower = prev_close - 0.35 * atr
    upper = prev_close + 0.35 * atr
    mid = prev_close
    global_scores = []
    for gs, direction in [('^NSEI', 0), ('^VIX', 0), ('INR=X', 0), ('CL=F', 0)]:
        q, ch = quote(gs)
        if pd.notna(ch):
            global_scores.append(ch)
    score = 0
    if float(d['Close'].iloc[-1]) > float(d['EMA21'].iloc[-1]): score += 1
    if float(d['MACD'].iloc[-1]) > float(d['MACD_SIGNAL'].iloc[-1]): score += 1
    if float(d['RSI'].iloc[-1]) > 50: score += 1
    if float(d['Close'].iloc[-1]) > float(d['VWAP'].iloc[-1]): score += 1
    if global_scores and np.nanmean(global_scores) > 0: score += 1
    bias = 'BULLISH' if score >= 4 else 'BEARISH' if score <= 1 else 'NEUTRAL / MIXED'
    return {'prev_close': prev_close, 'estimated_low': lower, 'estimated_high': upper, 'estimated_mid': mid, 'bias': bias, 'factor_score': score, 'factors': global_scores}


# ---------------- LIVE NEWS / CRITICAL ANALYSIS ----------------
@st.cache_data(ttl=90, show_spinner=False)
def live_news_feed(symbol, name):
    """Fetch recent Yahoo Finance search news for the selected instrument and India/global risk themes."""
    queries = [name, f"{name} India stock market", "India markets RBI Fed crude oil geopolitical"]
    out=[]; seen=set()
    headers={'User-Agent':'Mozilla/5.0'}
    for q in queries:
        try:
            u='https://query1.finance.yahoo.com/v1/finance/search?q='+quote(q)+'&newsCount=10'
            r=requests.get(u,headers=headers,timeout=8)
            if not r.ok: continue
            for item in r.json().get('news',[])[:10]:
                title=str(item.get('title','')).strip(); link=item.get('link',''); pub=item.get('publisher','')
                key=title.lower()
                if title and key not in seen:
                    seen.add(key); out.append({'title':title,'link':link,'publisher':pub,'published':item.get('providerPublishTime')})
        except Exception:
            continue
    return out[:25]

def critical_news_analysis(items):
    """Transparent keyword-based news risk/bias classifier; not an AI probability forecast."""
    bull=['beat','upgrade','buyback','growth','profit','surge','rally','strong demand','positive','approval','order win','capex']
    bear=['downgrade','miss','loss','fraud','default','war','sanction','attack','tariff','recession','inflation','rate hike','selloff','fall','drop','weak demand','geopolitical','conflict','crisis','strike','ban']
    risk=['war','sanction','tariff','attack','conflict','geopolitical','crude','oil','inflation','rbi','fed','rate','recession','election','default']
    bs=rs=0; rows=[]
    for x in items:
        t=x['title'].lower(); b=sum(t.count(k) for k in bull); be=sum(t.count(k) for k in bear); rr=sum(t.count(k) for k in risk)
        bs+=b; rs+=be
        label='Bullish' if b>be else 'Bearish' if be>b else 'Neutral'
        if b or be or rr: rows.append({'Headline':x['title'],'Bias':label,'Risk Flags':rr,'Publisher':x.get('publisher','')})
    net=bs-rs
    bias='BULLISH NEWS BIAS' if net>=2 else 'BEARISH NEWS BIAS' if net<=-2 else 'MIXED NEWS'
    risk_level='HIGH' if rs+bs>=8 and (rs>=3 or any(r['Risk Flags']>=2 for r in rows)) else 'MEDIUM' if rs+bs>=3 else 'LOW'
    return {'bias':bias,'risk':risk_level,'bull_points':bs,'bear_points':rs,'rows':rows}

def critical_market_analysis(strength, mf_score, trend, volume_ratio, pcr, news_info, price, pivot, prev_close):
    score=0; factors=[]
    if 'BUY' in strength: score+=2; factors.append('Technical bias bullish')
    elif 'SELL' in strength: score-=2; factors.append('Technical bias bearish')
    if trend in ('STRONG UPTREND','BULLISH TREND'): score+=2; factors.append('Trend supports upside')
    elif trend in ('STRONG DOWNTREND','BEARISH TREND'): score-=2; factors.append('Trend supports downside')
    if volume_ratio>=1.1: score += 1 if 'BUY' in strength else -1 if 'SELL' in strength else 0; factors.append(f'Volume {volume_ratio:.2f}x average')
    if pd.notna(pcr):
        if pcr>1.0: score+=1; factors.append('OI-PCR above 1')
        elif pcr<0.8: score-=1; factors.append('OI-PCR below 0.8')
    if news_info['bias']=='BULLISH NEWS BIAS': score+=1; factors.append('Recent news leans bullish')
    elif news_info['bias']=='BEARISH NEWS BIAS': score-=1; factors.append('Recent news leans bearish')
    if news_info['risk']=='HIGH': factors.append('High headline/geopolitical risk: use caution')
    if price>pivot: factors.append('Price above pivot')
    else: factors.append('Price below pivot')
    if score>=4: action='CALL BIAS / BULLISH SETUP'
    elif score<=-4: action='PUT BIAS / BEARISH SETUP'
    else: action='NO OPTION BIAS / WAIT'
    if abs(price-prev_close)/prev_close<0.002: factors.append('Price is close to previous close; confirmation preferred')
    return {'score':score,'action':action,'factors':factors}


# ---------------- REQUIREMENT HELPERS ----------------
def market_status():
    """NSE-style status using India local time; holidays are not hard-coded."""
    now = datetime.now(ZoneInfo('Asia/Kolkata'))
    if now.weekday() >= 5:
        return 'CLOSED / WEEKEND', now.strftime('%Y-%m-%d %H:%M:%S IST')
    mins = now.hour * 60 + now.minute
    if mins < 9*60:
        return 'PRE-OPEN', now.strftime('%Y-%m-%d %H:%M:%S IST')
    if mins < 9*60+15:
        return 'PRE-OPEN / AUCTION', now.strftime('%Y-%m-%d %H:%M:%S IST')
    if mins < 15*60+30:
        return 'MARKET OPEN', now.strftime('%Y-%m-%d %H:%M:%S IST')
    return 'CLOSED', now.strftime('%Y-%m-%d %H:%M:%S IST')

@st.cache_data(ttl=300, show_spinner=False)
def fii_dii_feed():
    """Fetch NSE FII/DII cash-market activity. Returns empty data when blocked/unavailable; never fabricates values."""
    try:
        h={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*','Referer':'https://www.nseindia.com/'}
        ss=requests.Session(); ss.get('https://www.nseindia.com/',headers=h,timeout=8)
        r=ss.get('https://www.nseindia.com/api/fiidiiTradeReact',headers=h,timeout=8)
        if not r.ok: return pd.DataFrame()
        payload=r.json()
        rows=payload if isinstance(payload,list) else payload.get('data',[])
        out=[]
        for x in rows:
            if not isinstance(x,dict): continue
            out.append(x)
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame()

def risk_position_size(entry, stop, balance, risk_pct, max_value):
    try:
        risk_cash=max(0,float(balance))*max(0,float(risk_pct))/100.0
        per_unit=abs(float(entry)-float(stop))
        if per_unit<=0: return 0,0.0
        qty=int(risk_cash/per_unit)
        qty=max(0, min(qty, int(max_value/max(float(entry),0.01))))
        return qty, qty*per_unit
    except Exception:
        return 0,0.0

def update_open_paper_positions(live_price):
    """Update trailing stops and auto-exit open paper positions when enabled."""
    if not st.session_state.get('auto_exit'): return []
    events=[]
    for i,t in enumerate(st.session_state.paper_trades):
        if t.get('Status')!='OPEN' or t.get('Symbol') != symbol: continue
        side=t['Side']; entry=float(t['Entry']); sl=float(t['Stop Loss']); target=float(t['Target'])
        atr=float(last['ATR']) if pd.notna(last['ATR']) else abs(entry)*0.01
        trail_gap=max(atr,abs(entry)*0.002)
        old_trail=float(t.get('Trailing Stop', sl))
        new_trail=max(old_trail, live_price-trail_gap) if side=='BUY' else min(old_trail, live_price+trail_gap)
        t['Trailing Stop']=new_trail
        hit_sl=(live_price<=new_trail) if side=='BUY' else (live_price>=new_trail)
        hit_target=(live_price>=target) if side=='BUY' else (live_price<=target)
        if hit_sl or hit_target:
            exit_price=live_price
            reason='TARGET' if hit_target else 'TRAILING SL'
            close_trade(i, exit_price)
            t['Exit Reason']=reason; events.append(f"Paper #{i+1} auto-exited: {reason} @ {exit_price:.2f}")
    return events

# ---------------- SIDEBAR ----------------
st.sidebar.title('[SETTINGS] Dashboard')
status, ist_now = market_status()
st.sidebar.info(f'**Market:** {status}\n\n{ist_now}')
st.sidebar.subheader('Risk Controls')
st.session_state.risk_per_trade = st.sidebar.number_input('Risk / Trade %', 0.1, 10.0, float(st.session_state.risk_per_trade), 0.1)
st.session_state.max_position_value = st.sidebar.number_input('Max Position Value Rs. ', 1000.0, 10000000.0, float(st.session_state.max_position_value), 1000.0)
st.session_state.auto_exit = st.sidebar.toggle('Paper Auto SL/Target/Trailing', value=st.session_state.auto_exit)
auto_refresh = st.sidebar.toggle('[AUTO] Auto Refresh', value=False, help='Refresh live data automatically')
refresh_seconds = st.sidebar.selectbox('Refresh Interval', [15,30,60,120,300], index=2, format_func=lambda x:f'{x} seconds', disabled=not auto_refresh)
if auto_refresh and st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds*1000, key='live_market_autorefresh')
elif auto_refresh and st_autorefresh is None:
    st.sidebar.warning('Install streamlit-autorefresh to enable automatic refresh.')
asset=st.sidebar.selectbox('Asset Type',['NSE Stocks','Indices','Crypto','Custom'])
if asset=='NSE Stocks': name=st.sidebar.selectbox('Stock',list(NIFTY)); symbol=NIFTY[name]
elif asset=='Indices': name=st.sidebar.selectbox('Index',list(INDEXES)); symbol=INDEXES[name]
elif asset=='Crypto': name=st.sidebar.selectbox('Crypto',list(CRYPTO)); symbol=CRYPTO[name]
else: symbol=st.sidebar.text_input('Custom Yahoo Symbol','RELIANCE.NS').upper().strip(); name=symbol
periods={'1 Day':'1d','5 Days':'5d','1 Month':'1mo','3 Months':'3mo','6 Months':'6mo','1 Year':'1y','2 Years':'2y','5 Years':'5y'}
period_name=st.sidebar.selectbox('Chart Period',list(periods),index=2)
ints={'5 Minutes':'5m','15 Minutes':'15m','30 Minutes':'30m','1 Hour':'60m','Daily':'1d'}
int_name=st.sidebar.selectbox('Timeframe',list(ints),index=1)
if st.sidebar.button('[REFRESH] Refresh Data',use_container_width=True):st.cache_data.clear();st.rerun()
if st.sidebar.button('[LOGOUT] Logout',use_container_width=True):st.session_state.logged_in=False;st.rerun()

# ---------------- MAIN ----------------
st.title('[CHART] Advanced Trading Dashboard')
st.caption(f'{name} | {symbol} | {int_name} | {datetime.now().strftime("%H:%M:%S")} | Auto Refresh: {"ON" if auto_refresh else "OFF"}')
d=data(symbol,periods[period_name],ints[int_name])
if d.empty:st.error('Data unavailable. Try another symbol or Daily timeframe.');st.stop()
d=indicators(d); last=d.iloc[-1]; prev=d.iloc[-2] if len(d)>1 else last
price=float(last.Close); change=price-float(prev.Close); pct=change/float(prev.Close)*100 if prev.Close else 0
auto_events=update_open_paper_positions(price)
if auto_events:
    for ev in auto_events: st.toast(ev)
pcr_oi, pcr_summary, pcr_status = option_pcr(symbol)
strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = multifactor_signal(last, d, pcr_oi)
sig,score=signal(last)
levels=trade_levels(price,last,strength)
option_suggest, option_suggest_status = option_suggestions(symbol, price)
news_items = live_news_feed(symbol, name)
news_info = critical_news_analysis(news_items)
trend_now = trend_analysis(d)
prev_levels_now = previous_day_levels(symbol)
prev_close_now = float(prev_levels_now.get('Previous Close', price)) if prev_levels_now else price
critical = critical_market_analysis(strength, mf_score, trend_now, vol_ratio, pcr_oi, news_info, price, levels.get('Pivot',price), prev_close_now)

m=st.columns(7); m[0].metric('Live Price',fmt_price(price),fmt_num(change,2,'',''));m[1].metric('Change',fmt_pct(pct));m[2].metric('RSI',fmt_num(last.RSI));m[3].metric('MACD',fmt_num(last.MACD));m[4].metric('VWAP',fmt_price(last.VWAP));m[5].metric('ATR',fmt_num(last.ATR));m[6].metric('ADX',fmt_num(last.ADX));
st.metric('Signal',sig,f'{strength} | Score {mf_score}/10')
st.caption('VWAP is shown as N/A when the selected data source does not provide usable volume (common for some index feeds).')

st.subheader('[TRADE] Trade Setup - Entry / Targets / Stop Loss')
if levels['Direction'] != 'WAIT':
    e1,e2,e3,e4,e5,e6=st.columns(6)
    e1.metric('Suggested Entry',fmt_price(levels['Entry']))
    e2.metric('Initial Stop Loss',fmt_price(levels['Stop Loss']))
    e3.metric('Target 1',fmt_price(levels['Target 1']))
    e4.metric('Target 2',fmt_price(levels['Target 2']))
    e5.metric('Target 3',fmt_price(levels['Target 3']))
    e6.metric('Trailing Stop',fmt_price(levels['Trailing Stop']))
    st.caption(f"{levels['Direction']} setup | Break-even stop after T1: {fmt_price(levels['Break-even Stop'])} | Approx. Risk/Reward to T2: 1:{fmt_num(levels['RR to T2'])}")
else:
    st.info('No clear BUY/SELL signal. Entry, targets and stop-loss are not activated until a directional signal appears.')

st.subheader('[ANALYSIS]  Live Critical Analysis')
ca,cb,cc,cd,ce=st.columns(5)
ca.metric('Critical Action', critical['action'])
cb.metric('Critical Score', f"{critical['score']:+d}")
cc.metric('News Bias', news_info['bias'])
cd.metric('News Risk', news_info['risk'])
ce.metric('Headline Count', len(news_items))
if critical['factors']:
    st.write('**Factors:** ' + ' | '.join(critical['factors']))
st.caption('Rule-based multifactor/news analysis. It is not a guaranteed prediction and does not execute real orders.')

st.subheader('[OPTIONS] CALL / PUT Candidate')
if critical['action'].startswith('CALL') and not option_suggest.empty:
    cand=option_suggest[option_suggest['Type']=='CALL'].copy()
    st.dataframe(cand,use_container_width=True,hide_index=True)
elif critical['action'].startswith('PUT') and not option_suggest.empty:
    cand=option_suggest[option_suggest['Type']=='PUT'].copy()
    st.dataframe(cand,use_container_width=True,hide_index=True)
else:
    st.info('No clear CALL/PUT candidate. Wait for technical + volume + news confirmation, or check the Options tab for available contracts.')

st.subheader('[DATA] Price & Indicators')
fig=go.Figure(go.Candlestick(x=d.index,open=d.Open,high=d.High,low=d.Low,close=d.Close,name='Price'))
for col in ['EMA5','EMA21','EMA50','EMA200','VWAP','BB_UPPER','BB_LOWER','SUPPORT','RESISTANCE','PIVOT','R1','S1']:
    if col in d:fig.add_trace(go.Scatter(x=d.index,y=d[col],name=col,mode='lines',line={'width':1}))
fig.update_layout(height=620,xaxis_rangeslider_visible=False,template='plotly_dark',hovermode='x unified');st.plotly_chart(fig,use_container_width=True)

# Live multi-factor dashboard strip
q1,q2,q3,q4,q5=st.columns(5)
q1.metric('Multi-factor Signal',strength)
q2.metric('Trend',trend_analysis(d))
q3.metric('Volume',vol_label,f'{vol_ratio:.2f}x')
q4.metric('PCR',f'{pcr_oi:.2f}' if pd.notna(pcr_oi) else 'N/A')
q5.metric('Confirmation','YES' if live_confirmation else 'WAIT')

tabs=st.tabs(['Indicators','Patterns','Backtest','Paper Trading','Market','Pre-Open','Options','News','Data','FII/DII & Risk','Settings'])
with tabs[0]:
    c1,c2,c3=st.columns(3);c1.metric('EMA 5',f'{last.EMA5:.2f}');c1.metric('EMA 21',f'{last.EMA21:.2f}');c1.metric('EMA 50',f'{last.EMA50:.2f}');c2.metric('EMA 200',f'{last.EMA200:.2f}');c2.metric('BB Upper',f'{last.BB_UPPER:.2f}');c2.metric('BB Lower',f'{last.BB_LOWER:.2f}');c3.metric('Stochastic K',f'{last.STOCH_K:.2f}');c3.metric('CCI',f'{last.CCI:.2f}');c3.metric('ADX',f'{last.ADX:.2f}')
    a,b=st.columns(2)
    with a:
        f=go.Figure();f.add_trace(go.Scatter(x=d.index,y=d.MACD,name='MACD'));f.add_trace(go.Scatter(x=d.index,y=d.MACD_SIGNAL,name='Signal'));f.add_bar(x=d.index,y=d.MACD_HIST,name='Histogram');f.update_layout(height=320,template='plotly_dark',title='MACD');st.plotly_chart(f,use_container_width=True)
    with b:
        f=go.Figure(go.Scatter(x=d.index,y=d.RSI,name='RSI'));f.add_hline(y=70);f.add_hline(y=30);f.update_layout(height=320,template='plotly_dark',title='RSI');st.plotly_chart(f,use_container_width=True)
    fib=d.attrs['fib'];st.subheader('Fibonacci');st.dataframe(pd.DataFrame({'Level':fib.keys(),'Price':fib.values()}),use_container_width=True,hide_index=True)
with tabs[1]:
    st.subheader('[CANDLE] Candlestick Patterns'); cp=candle_patterns(d)
    if cp:
        for x in cp:st.success(x)
    else:st.info('No strong candlestick pattern detected.')
    st.subheader('[PATTERN] Chart Patterns'); ch=chart_patterns(d)
    if ch:
        for x in ch:st.info(x)
    else:st.info('No strong chart structure detected.')
    st.caption('Pattern detection is quantitative/approximate and is not a guarantee.')
with tabs[2]:
    st.subheader('[ANALYSIS]? Strategy Backtest & Historical Win Rate')
    bt_periods={'1 Month':'1mo','2 Months':'2mo','3 Months':'3mo','4 Months':'4mo','5 Months':'5mo','6 Months':'6mo','1 Year':'1y','5 Years':'5y'}
    bt_choice=st.selectbox('Backtest period',list(bt_periods.keys()))
    bt,stats=backtest(symbol,bt_periods[bt_choice])
    if stats:
        b1,b2,b3,b4,b5=st.columns(5)
        b1.metric('Historical Win Rate',f"{stats['win_rate']:.2f}%")
        b2.metric('Signals',stats['signals'])
        b3.metric('BUY Win Rate',f"{stats['buy_win_rate']:.2f}%" if pd.notna(stats['buy_win_rate']) else 'N/A')
        b4.metric('SELL Win Rate',f"{stats['sell_win_rate']:.2f}%" if pd.notna(stats['sell_win_rate']) else 'N/A')
        b5.metric('Avg Next-Bar Return',f"{stats['avg_return']:.2f}%")
        bc1,bc2=st.columns(2)
        bc1.metric('BUY + Volume Confirm Win Rate',f"{stats['buy_confirm_win_rate']:.2f}%" if pd.notna(stats['buy_confirm_win_rate']) else 'N/A')
        bc2.metric('SELL + Volume Confirm Win Rate',f"{stats['sell_confirm_win_rate']:.2f}%" if pd.notna(stats['sell_confirm_win_rate']) else 'N/A')
        x1,x2=st.columns(2)
        x1.metric('Strong BUY Historical Win Rate',f"{stats['strong_buy_win_rate']:.2f}%" if pd.notna(stats['strong_buy_win_rate']) else 'N/A')
        x2.metric('Strong SELL Historical Win Rate',f"{stats['strong_sell_win_rate']:.2f}%" if pd.notna(stats['strong_sell_win_rate']) else 'N/A')
        st.dataframe(bt.tail(200),use_container_width=True,hide_index=True)
        st.caption('These are historical hit rates of the rule-based next-bar test over the selected period, not a forecast or guarantee of future win probability.')
    else:
        st.warning('Not enough historical data for this backtest period.')

    st.subheader('[TRADE] Current Signal Historical Win Rate')
    if stats:
        current_key='BUY' if strength in ['BUY BIAS','STRONG BUY'] else 'SELL' if strength in ['SELL BIAS','STRONG SELL'] else 'NO SIGNAL'
        current_hist=stats['buy_win_rate'] if current_key=='BUY' else stats['sell_win_rate'] if current_key=='SELL' else np.nan
        cc1,cc2,cc3=st.columns(3)
        cc1.metric('Current Signal',strength)
        cc2.metric('Historical Win Rate',f"{current_hist:.2f}%" if pd.notna(current_hist) else 'N/A')
        cc3.metric('Confidence Score',f"{mf_score*10}%")
        st.info('Confidence is a factor score, not a probability. Historical win rate describes past rule performance only.')

    st.subheader('[INFO] Live Confirmation')
    lc1,lc2,lc3=st.columns(3)
    lc1.metric('Volume Confirmation',vol_label,f"{vol_ratio:.2f}x avg")
    lc2.metric('PCR',f"{pcr_oi:.2f}" if pd.notna(pcr_oi) else 'N/A')
    lc3.metric('Live Confirmation', 'CONFIRMED' if live_confirmation else 'WAIT')

with tabs[3]:
    st.subheader('[PAPER] Paper Trading')
    a,b,c=st.columns(3)
    with a: side=st.selectbox('Side',['BUY','SELL']);qty=st.number_input('Quantity',1.0,step=1.0);entry=st.number_input('Entry Price',0.0,value=price,step=.05)
    with b:
        default_sl=levels['Stop Loss'] if levels['Direction']==side and pd.notna(levels['Stop Loss']) else (price-(float(last.ATR) if pd.notna(last.ATR) else price*.01) if side=='BUY' else price+(float(last.ATR) if pd.notna(last.ATR) else price*.01))
        default_target=levels['Target 1'] if levels['Direction']==side and pd.notna(levels['Target 1']) else (price+2*(float(last.ATR) if pd.notna(last.ATR) else price*.01) if side=='BUY' else max(.01,price-2*(float(last.ATR) if pd.notna(last.ATR) else price*.01)))
        sl=st.number_input('Stop Loss',0.0,value=float(max(.01,default_sl)),step=.05);target=st.number_input('Target 1',0.0,value=float(max(.01,default_target)),step=.05)
    with c:
        est=(price-entry)*qty if side=='BUY' else (entry-price)*qty;st.metric('Live P/L',f'{est:+,.2f}');st.metric('Paper Balance',f'Rs. {st.session_state.balance:,.2f}');st.metric('Realized P/L',f'Rs. {st.session_state.realized_pnl:+,.2f}')
    if st.button('??| Open Paper Position',use_container_width=True):
        st.session_state.paper_trades.append({'Symbol':symbol,'Side':side,'Quantity':float(qty),'Entry':float(entry),'Stop Loss':float(sl),'Target':float(target),'Opened':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'Status':'OPEN','Exit':np.nan,'Exit Time':'','Final P/L':np.nan});st.rerun()
    if st.session_state.paper_trades:
        rows=[]
        for i,t in enumerate(st.session_state.paper_trades):
            r=dict(t);r['Live Price']=price if r['Status']=='OPEN' else r['Exit'];r['Live P/L']=pnl(r,price) if r['Status']=='OPEN' else r['Final P/L'];r['_i']=i;rows.append(r)
        st.dataframe(pd.DataFrame(rows).drop(columns='_i'),use_container_width=True,hide_index=True)
        opens=[i for i,t in enumerate(st.session_state.paper_trades) if t['Status']=='OPEN']
        if opens:
            i=st.selectbox('Open position to exit',opens,format_func=lambda x:f"#{x+1} {st.session_state.paper_trades[x]['Symbol']} {st.session_state.paper_trades[x]['Side']}")
            ep=st.number_input('Exit Price',0.0,value=price,step=.05)
            if st.button('??" Exit Selected Position',use_container_width=True):close_trade(i,ep);st.rerun()
    else:st.info('No paper positions.')
with tabs[4]:
    st.subheader('[MARKET] Market Overview')
    tr=trend_analysis(d)
    prev_levels=previous_day_levels(symbol)
    a1,a2,a3=st.columns(3)
    a1.metric('Trend',tr)
    a2.metric('Previous Day Close',f"{prev_levels.get('Previous Close',np.nan):,.2f}" if prev_levels else 'N/A')
    a3.metric('Live Support',f"{float(last['SUPPORT']):,.2f}" if pd.notna(last['SUPPORT']) else 'N/A')
    s1,s2=st.columns(2)
    with s1:
        st.write('**Previous Day Levels**')
        st.dataframe(pd.DataFrame({'Level':list(prev_levels.keys()),'Price':list(prev_levels.values())}),use_container_width=True,hide_index=True)
    with s2:
        st.write('**Live Levels**')
        live_levels={'Live Support':float(last['SUPPORT']),'Live Resistance':float(last['RESISTANCE']),'Pivot':float(last['PIVOT']),'R1':float(last['R1']),'S1':float(last['S1']),'R2':float(last['R2']),'S2':float(last['S2'])}
        st.dataframe(pd.DataFrame({'Level':live_levels.keys(),'Price':live_levels.values()}),use_container_width=True,hide_index=True)
    hist_1m=data(symbol,'1mo','1d')
    st.write('**Previous-day trend:**', previous_day_trend(indicators(hist_1m)) if not hist_1m.empty else 'N/A')
    markets={'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','USD/INR':'INR=X','Crude Oil':'CL=F','Gold':'GC=F'}
    cs=st.columns(3)
    for i,(lab,sym) in enumerate(markets.items()):
        q,ch=quote(sym);cs[i%3].metric(lab,f'{q:,.2f}' if pd.notna(q) else 'N/A',f'{ch:+.2f}%' if pd.notna(ch) else None)
    st.subheader('[DATA] Advance / Decline'); rows=[];adv=dec=unch=0
    for n,s in NIFTY.items():
        q,ch=quote(s)
        if pd.notna(ch):
            status='Advance' if ch>.05 else 'Decline' if ch<-.05 else 'Unchanged';adv+=status=='Advance';dec+=status=='Decline';unch+=status=='Unchanged';rows.append({'Stock':n,'Change %':ch,'Status':status})
    a,b,c,e=st.columns(4);a.metric('Advances',adv);b.metric('Declines',dec);c.metric('Unchanged',unch);e.metric('A/D Ratio',f'{adv/dec:.2f}' if dec else 'INF')
    if rows:st.dataframe(pd.DataFrame(rows).sort_values('Change %',ascending=False),use_container_width=True,hide_index=True)
    st.subheader('[NEWS] Critical News Context')
    st.write(f"**{news_info['bias']}** | Risk: **{news_info['risk']}** | Bull points: {news_info['bull_points']} | Bear points: {news_info['bear_points']}")
    st.subheader('[FII/DII] FII / DII');st.warning('Live NSE FII/DII values are not fabricated here. Use an authorized NSE/broker feed for production values.')
    f1,f2=st.columns(2);f1.metric('FII','Feed required');f2.metric('DII','Feed required')
with tabs[5]:
    st.subheader('[PRE-OPEN] Pre-Open / Possible Opening Analysis')
    oa=opening_analysis(symbol,d)
    if oa:
        p1,p2,p3,p4=st.columns(4)
        p1.metric('Previous Close',f"Rs. {oa['prev_close']:,.2f}")
        p2.metric('Possible Open Mid',f"Rs. {oa['estimated_mid']:,.2f}")
        p3.metric('Possible Open Low',f"Rs. {oa['estimated_low']:,.2f}")
        p4.metric('Possible Open High',f"Rs. {oa['estimated_high']:,.2f}")
        st.metric('Pre-Session Multifactor Bias',oa['bias'],f"Factor score {oa['factor_score']}/5")
        pre=preopen_snapshot()
        if pre:
            st.success('Official NSE pre-open snapshot received.')
            st.json(pre)
        else:
            st.warning('Official NSE indicative pre-open data was not reachable from this Streamlit environment. The displayed range is an estimate from previous close/ATR and market factors, not the official equilibrium price.')
        st.caption("NSE pre-open session is 9:00 to 9:15 IST; when an equilibrium price is discovered, it becomes the day's open price. The app labels its fallback range as an estimate rather than official pre-open data.")
    else:
        st.info('Pre-open analysis unavailable for this symbol.')

with tabs[6]:
    st.subheader('[ANALYSIS]? Options Analysis')
    if not pcr_summary.empty:
        st.dataframe(pcr_summary,use_container_width=True,hide_index=True)
        st.metric('OI PCR',f"{pcr_oi:.2f}" if pd.notna(pcr_oi) else 'N/A')
    else:
        st.warning('Live option-chain PCR unavailable for this symbol. Select an index/option-enabled underlying.');st.warning('PCR, OI, IV and Max Pain require a reliable live option-chain/exchange or broker feed. This dashboard does not invent those values.')
    st.selectbox('Underlying',['NIFTY','BANKNIFTY','RELIANCE','TCS','INFY','HDFCBANK']);st.info('Production integration can populate expiry, strike-wise CE/PE OI, volume, IV, PCR and Max Pain using an authorized API.')
with tabs[7]:
    st.subheader('[NEWS] Market & Geopolitical News')
    st.metric('Critical News Bias',news_info['bias'],f"Risk {news_info['risk']}")
    ns=news_items
    if ns:
        for item in ns:
            title=item.get('title','Untitled');link=item.get('link','');pub=item.get('publisher','');st.markdown(f'### [{title}]({link})' if link else f'### {title}');st.caption(pub)
    else:st.info('News temporarily unavailable.')
with tabs[8]:
    cols=['Open','High','Low','Close','Volume','EMA5','EMA21','EMA50','EMA200','VWAP','RSI','MACD','MACD_SIGNAL','ATR','ADX','BB_UPPER','BB_MID','BB_LOWER','STOCH_K','STOCH_D','CCI','SUPPORT','RESISTANCE','PIVOT','R1','S1','R2','S2'];st.dataframe(d[[x for x in cols if x in d]].tail(50),use_container_width=True)


with tabs[9]:
    st.subheader('[FII/DII] FII / DII Activity')
    fd=fii_dii_feed()
    if not fd.empty:
        st.dataframe(fd.tail(20),use_container_width=True,hide_index=True)
        st.caption('Source: NSE endpoint when reachable. Values are shown only when the source responds; no synthetic values are generated.')
    else:
        st.warning('NSE FII/DII feed is unavailable from this environment. Connect an authorized broker/data feed for production reliability.')
    st.subheader('Risk / Position Sizing')
    rs_entry=st.number_input('Sizing Entry Price',0.01,float(levels['Entry']),0.05)
    rs_sl=st.number_input('Sizing Stop Loss',0.01,float(levels['Stop Loss']) if pd.notna(levels['Stop Loss']) else max(0.01,price-price*0.01),0.05)
    qty_s,risk_cash=risk_position_size(rs_entry,rs_sl,st.session_state.balance,st.session_state.risk_per_trade,st.session_state.max_position_value)
    r1,r2,r3=st.columns(3);r1.metric('Suggested Qty',qty_s);r2.metric('Max Risk Rs. ',f'{risk_cash:,.2f}');r3.metric('Risk %',f"{st.session_state.risk_per_trade:.2f}%")
    st.caption('Position size is a rule-based risk calculation, not a guarantee or order instruction.')

with tabs[10]:
    st.subheader('[SETTINGS] Settings & Data Controls')
    st.write('**Login:** username `admin`; initial password is `admin123`. The demo recovery PIN defaults to `1234` unless TRADING_RECOVERY_PIN is set.')
    st.write('**Auto refresh:** controlled from the sidebar. **Paper auto-exit:** can close positions at trailing SL/target using the current market price.')
    if st.button('Clear Cached Market Data'):
        st.cache_data.clear(); st.success('Cache cleared. Refresh the page to reload all feeds.')
    st.warning('Before real trading, replace demo authentication with secure hashed credentials/session management and connect a broker API with explicit order confirmation.')

st.divider();st.subheader('[BROKER] Broker Integration')
a,b,c,e=st.columns(4);a.metric('Angel One','API Ready');b.metric('Upstox','API Ready');c.metric('Delta Exchange','API Ready');e.metric('Sahi','API Ready')
st.caption('Paper trading is functional. Real broker order execution requires your authorized API credentials and the broker current official API/SDK contract; this app does not place real orders by itself.')
st.info('Market data can be delayed, incomplete, or unavailable. Signals are informational and are not guaranteed investment advice.')
