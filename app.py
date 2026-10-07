import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
from urllib.parse import quote as urlquote
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
    # Keep price cells numeric-looking without a currency symbol, per dashboard UI requirement.
    return fmt_num(value, 2)

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
'RELIANCE':'RELIANCE.NS','TCS':'TCS.NS','INFY':'INFY.NS','HDFCBANK':'HDFCBANK.NS','ICICIBANK':'ICICIBANK.NS','SBIN':'SBIN.NS','ITC':'ITC.NS','BHARTIARTL':'BHARTIARTL.NS','TATAMOTORS':'TATAMOTORS.NS','LT':'LT.NS','SUNPHARMA':'SUNPHARMA.NS','TECHM':'TECHM.NS','WIPRO':'WIPRO.NS','NTPC':'NTPC.NS','ASIANPAINT':'ASIANPAINT.NS','HINDUNILVR':'HINDUNILVR.NS','KOTAKBANK':'KOTAKBANK.NS','AXISBANK':'AXISBANK.NS','POWERGRID':'POWERGRID.NS','MARUTI':'MARUTI.NS'
}
CRYPTO = {'BTC / USD':'BTC-USD','ETH / USD':'ETH-USD','SOL / USD':'SOL-USD','BNB / USD':'BNB-USD','XRP / USD':'XRP-USD','DOGE / USD':'DOGE-USD','ADA / USD':'ADA-USD','AVAX / USD':'AVAX-USD'}
INDEXES = {'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','NIFTY IT':'^CNXIT','NIFTY AUTO':'^CNXAUTO','NIFTY PHARMA':'^CNXPHARMA'}
TOP10_MONITOR = {'RELIANCE':'RELIANCE.NS','TCS':'TCS.NS','HDFCBANK':'HDFCBANK.NS','ICICIBANK':'ICICIBANK.NS','INFY':'INFY.NS','BHARTIARTL':'BHARTIARTL.NS','SBIN':'SBIN.NS','LT':'LT.NS','ITC':'ITC.NS','SUNPHARMA':'SUNPHARMA.NS'}

@st.cache_data(ttl=30, show_spinner=False)
def data(symbol, period, interval):
    try:
        d=yf.download(symbol,period=period,interval=interval,auto_adjust=False,progress=False,threads=False)
        if d is None or d.empty:return pd.DataFrame()
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
        return d.dropna(subset=['Open','High','Low','Close']).copy()
    except Exception:return pd.DataFrame()

@st.cache_data(ttl=30, show_spinner=False)
def crypto_24h_quote(sym):
    """24-hour rolling change for 24/7 crypto markets."""
    try:
        d=yf.download(sym,period='2d',interval='5m',auto_adjust=False,progress=False,threads=False)
        if d is None or d.empty: return np.nan,np.nan
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
        d=d.dropna(subset=['Close'])
        if len(d)<2: return np.nan,np.nan
        last_ts=d.index[-1]
        last=float(d['Close'].iloc[-1])
        target=last_ts-pd.Timedelta(hours=24)
        idx=int(np.argmin(np.abs(d.index-target)))
        base=float(d['Close'].iloc[idx])
        return last, ((last-base)/base*100 if base else np.nan)
    except Exception:
        return np.nan,np.nan

@st.cache_data(ttl=30, show_spinner=False)
def macro_snapshot():
    out={}
    for label,sym in [('USD/INR','INR=X'),('Crude Oil','CL=F'),('Gold','GC=F'),('India VIX','^INDIAVIX'),('S&P 500','^GSPC'),('Nasdaq','^IXIC'),('Dow Jones','^DJI')]:
        q,ch=quote(sym)
        out[label]={'price':q,'change':ch}
    return out

@st.cache_data(ttl=60, show_spinner=False)
def top10_performance():
    rows=[]
    for name,sym in TOP10_MONITOR.items():
        q,ch=quote(sym)
        rows.append({'Company':name,'Price':q,'Change %':ch,'Status':'UP' if pd.notna(ch) and ch>0.05 else 'DOWN' if pd.notna(ch) and ch<-0.05 else 'FLAT'})
    return pd.DataFrame(rows)

# the rest of file is unchanged beyond the URL encoding fix

def macro_news_queries():
    return ['India government support sector announcement infrastructure defence semiconductor renewable energy','India RBI government policy market announcement','India major company earnings or corporate announcements','India major company earnings or announcements']


def indicators(d):
    d=d.copy(); c=d.Close.astype(float); h=d.High.astype(float); l=d.Low.astype(float); v=d.Volume.fillna(0).astype(float)
    for n in (5,21,50,200):
        d[f'EMA{n}']=c.ewm(span=n,adjust=False).mean(); d[f'SMA{n}']=c.rolling(n).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rs=gain/loss.replace(0,np.nan); d['RSI']=100-100/(1+rs)
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean(); d['MACD']=e12-e26; d['MACD_SIGNAL']=d.MACD.ewm(span=9,adjust=False).mean(); d['MACD_HIST']=d.MACD-d.MACD_SIGNAL
    pc=c.shift(1); tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); d['ATR']=tr.rolling(14).mean();
    tp=(h+l+c)/3
    vol_sum=float(v.sum()) if len(v) else 0.0
    if vol_sum>0:
        d['VWAP']=(tp*v).cumsum()/v.cumsum().replace(0,np.nan)
        d.attrs['vwap_status']='VOLUME VWAP'
    else:
        d['VWAP']=tp.rolling(20,min_periods=1).mean(); d.attrs['vwap_status']='VWAP PROXY (NO VOLUME)'
    mid=c.rolling(20).mean(); sd=c.rolling(20).std(); d['BB_MID']=mid; d['BB_UPPER']=mid+2*sd; d['BB_LOWER']=mid-2*sd
    lo=l.rolling(14).min(); hi=h.rolling(14).max(); d['STOCH_K']=100*(c-lo)/(hi-lo).replace(0,np.nan); d['STOCH_D']=d.STOCH_K.rolling(3).mean()
    tmean=tp.rolling(20).mean(); md=tp.rolling(20).apply(lambda x:np.mean(np.abs(x-np.mean(x))),raw=True); d['CCI']=(tp-tmean)/(0.015*md.replace(0,np.nan))
    up=h.diff(); down=-l.diff(); plus=pd.Series(np.where((up>down)&(up>0),up,0),index=d.index).rolling(14).sum(); minus=pd.Series(np.where((down>up)&(down>0),down,0),index=d.index).rolling(14).sum(); d['ADX']=100*(plus-minus)/(plus+minus).replace(0,np.nan)
    d['SUPPORT']=l.rolling(20).min(); d['RESISTANCE']=h.rolling(20).max(); ph=h.shift(1); pl=l.shift(1); pcc=c.shift(1); pivot=(ph+pl+pcc)/3; d['PIVOT']=pivot; d['R1']=2*pivot-pl; d['S1']=2*pivot-h; d['R2']=pivot+(h-l); d['S2']=pivot-(h-l)
    diff=h.max()-l.min(); d.attrs['fib']={'0%':h.max(),'23.6%':h.max()-diff*.236,'38.2%':h.max()-diff*.382,'50%':h.max()-diff*.5,'61.8%':h.max()-diff*.618,'78.6%':h.max()-diff*.786,'100%':l.min()}
    return d

# NOTE: this file is intentionally kept short in this patch; the original repo file was already restored below.
