import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
from urllib.parse import quote as url_quote
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

# ---------------- ANGEL ONE SMARTAPI (OPTIONAL) ----------------
try:
    from SmartApi import SmartConnect
    import pyotp
    ANGEL_SDK_AVAILABLE = True
except Exception:
    SmartConnect = None
    pyotp = None
    ANGEL_SDK_AVAILABLE = False

st.set_page_config(page_title='Advanced Trading Dashboard', page_icon='[CHART]', layout='wide')

# ---------------- STATE ----------------
for k, v in {
    'logged_in':False,'paper_trades':[],'realized_pnl':0.0,'balance':100000.0,
    'risk_per_trade':1.0,'max_position_value':100000.0,'auto_exit':False,
    'last_data_status':'Unknown',
    'angel_connected':False,'angel_client':None,'angel_profile':{},
    'angel_feed_token':None,'angel_refresh_token':None,
    'angel_selected_instrument':None,'angel_last_order':None
}.items():
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


# ---------------- ANGEL ONE HELPERS ----------------
def angel_connect(api_key, client_code, pin, totp_value):
    """Create an Angel One SmartAPI session. Credentials are kept only in Streamlit session state."""
    if not ANGEL_SDK_AVAILABLE:
        return False, "SmartAPI SDK is not installed. Run: pip install smartapi-python pyotp logzero websocket-client"
    api_key = str(api_key or "").strip()
    client_code = str(client_code or "").strip()
    pin = str(pin or "").strip()
    totp_value = str(totp_value or "").replace(" ", "").strip()
    if not all([api_key, client_code, pin, totp_value]):
        return False, "API key, client code, PIN and TOTP/QR secret are required."
    try:
        # Accept either a current 6-digit TOTP or the TOTP secret from Angel One.
        if totp_value.isdigit() and len(totp_value) == 6:
            otp = totp_value
        else:
            otp = pyotp.TOTP(totp_value).now()
        client = SmartConnect(api_key=api_key)
        session = client.generateSession(client_code, pin, otp)
        if not isinstance(session, dict) or session.get("status") is False or not session.get("data"):
            msg = session.get("message", "Angel One login failed.") if isinstance(session, dict) else "Angel One login failed."
            return False, str(msg)
        refresh_token = session["data"].get("refreshToken")
        feed_token = client.getfeedToken()
        profile = client.getProfile(refresh_token) if refresh_token else {}
        st.session_state.angel_client = client
        st.session_state.angel_connected = True
        st.session_state.angel_refresh_token = refresh_token
        st.session_state.angel_feed_token = feed_token
        st.session_state.angel_profile = profile.get("data", profile) if isinstance(profile, dict) else {}
        return True, "Angel One connected."
    except Exception as exc:
        st.session_state.angel_connected = False
        st.session_state.angel_client = None
        return False, f"Angel One connection failed: {exc}"

def angel_disconnect():
    client = st.session_state.get("angel_client")
    profile = st.session_state.get("angel_profile") or {}
    client_code = profile.get("clientcode") or profile.get("clientCode")
    try:
        if client is not None and client_code:
            client.terminateSession(str(client_code))
    except Exception:
        pass
    st.session_state.angel_connected = False
    st.session_state.angel_client = None
    st.session_state.angel_profile = {}
    st.session_state.angel_feed_token = None
    st.session_state.angel_refresh_token = None
    st.session_state.angel_selected_instrument = None

def angel_search(exchange, query):
    client = st.session_state.get("angel_client")
    if not st.session_state.get("angel_connected") or client is None:
        return pd.DataFrame(), "Connect Angel One first."
    try:
        res = client.searchScrip(exchange, str(query).strip())
        rows = res.get("data", []) if isinstance(res, dict) else []
        if not rows:
            return pd.DataFrame(), "No matching instrument found."
        out = pd.DataFrame(rows)
        keep = [c for c in ["exchange","tradingsymbol","symboltoken"] if c in out.columns]
        return out[keep].drop_duplicates().head(100), "OK"
    except Exception as exc:
        return pd.DataFrame(), f"Symbol search failed: {exc}"

def angel_ltp(instrument):
    client = st.session_state.get("angel_client")
    if client is None or not instrument:
        return np.nan, {}
    try:
        exchange = str(instrument["exchange"])
        tradingsymbol = str(instrument["tradingsymbol"])
        symboltoken = str(instrument["symboltoken"])
        res = client.ltpData(exchange, tradingsymbol, symboltoken)
        data_obj = res.get("data", {}) if isinstance(res, dict) else {}
        ltp = pd.to_numeric(data_obj.get("ltp"), errors="coerce")
        return (float(ltp) if pd.notna(ltp) else np.nan), data_obj
    except Exception:
        return np.nan, {}

def angel_place_manual_order(instrument, side, qty, order_type, product_type, limit_price=0.0):
    """Place a user-confirmed manual order through Angel One SmartAPI."""
    client = st.session_state.get("angel_client")
    if client is None or not st.session_state.get("angel_connected"):
        return False, "Angel One is not connected.", None
    if not instrument:
        return False, "Select an Angel One instrument first.", None
    try:
        qty = int(qty)
        if qty <= 0:
            return False, "Quantity must be at least 1.", None
        order_type = str(order_type).upper()
        price_value = 0 if order_type == "MARKET" else float(limit_price)
        params = {
            "variety": "NORMAL",
            "tradingsymbol": str(instrument["tradingsymbol"]),
            "symboltoken": str(instrument["symboltoken"]),
            "transactiontype": str(side).upper(),
            "exchange": str(instrument["exchange"]),
            "ordertype": order_type,
            "producttype": str(product_type).upper(),
            "duration": "DAY",
            "price": str(price_value),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }
        if hasattr(client, "placeOrderFullResponse"):
            response = client.placeOrderFullResponse(params)
            ok = isinstance(response, dict) and response.get("status", True) is not False
            order_id = None
            if isinstance(response, dict):
                d = response.get("data") or {}
                order_id = d.get("orderid") or d.get("orderId") if isinstance(d, dict) else None
            return ok, ("Order submitted." if ok else str(response.get("message", "Order rejected."))), {"request": params, "response": response, "order_id": order_id}
        order_id = client.placeOrder(params)
        return bool(order_id), ("Order submitted." if order_id else "Order rejected."), {"request": params, "order_id": order_id}
    except Exception as exc:
        return False, f"Order placement failed: {exc}", None

def angel_order_book():
    client = st.session_state.get("angel_client")
    if client is None:
        return pd.DataFrame()
    try:
        res = client.orderBook()
        rows = res.get("data", []) if isinstance(res, dict) else []
        return pd.DataFrame(rows or [])
    except Exception:
        return pd.DataFrame()

def angel_positions():
    client = st.session_state.get("angel_client")
    if client is None:
        return pd.DataFrame()
    try:
        res = client.position()
        rows = res.get("data", []) if isinstance(res, dict) else []
        return pd.DataFrame(rows or [])
    except Exception:
        return pd.DataFrame()


# ---------------- SYMBOLS ----------------
NIFTY = {
'RELIANCE':'RELIANCE.NS','TCS':'TCS.NS','INFY':'INFY.NS','HDFCBANK':'HDFCBANK.NS','ICICIBANK':'ICICIBANK.NS','SBIN':'SBIN.NS','ITC':'ITC.NS','BHARTIARTL':'BHARTIARTL.NS','TATAMOTORS':'TATAMOTORS.NS','LT':'LT.NS','AXISBANK':'AXISBANK.NS','KOTAKBANK':'KOTAKBANK.NS','SUNPHARMA':'SUNPHARMA.NS','TECHM':'TECHM.NS','WIPRO':'WIPRO.NS','ASIANPAINT':'ASIANPAINT.NS','HINDUNILVR':'HINDUNILVR.NS','MARUTI':'MARUTI.NS','ULTRACEMCO':'ULTRACEMCO.NS','TITAN':'TITAN.NS'}
CRYPTO = {'BTC / USD':'BTC-USD','ETH / USD':'ETH-USD','SOL / USD':'SOL-USD','BNB / USD':'BNB-USD','XRP / USD':'XRP-USD','DOGE / USD':'DOGE-USD','ADA / USD':'ADA-USD','AVAX / USD':'AVAX-USD'}
INDEXES = {'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','NIFTY IT':'^CNXIT','NIFTY AUTO':'^CNXAUTO','NIFTY PHARMA':'^CNXPHARMA'}
TOP10_MONITOR = {'RELIANCE':'RELIANCE.NS','TCS':'TCS.NS','HDFCBANK':'HDFCBANK.NS','ICICIBANK':'ICICIBANK.NS','INFY':'INFY.NS','BHARTIARTL':'BHARTIARTL.NS','SBIN':'SBIN.NS','LT':'LT.NS','ITC':'ITC.NS','TATAMOTORS':'TATAMOTORS.NS'}

@st.cache_data(ttl=10, show_spinner=False)
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

def macro_news_queries():
    return ['India government support sector announcement infrastructure defence semiconductor renewable energy','India RBI government policy market announcement','India major company earnings or revenue growth and investment plan','India market volatility geopolitical risk and crude oil outlook']

# --- Market quote helper (kept distinct from URL-encoding helper to avoid collisions) ---
@st.cache_data(ttl=60,show_spinner=False)
def quote(sym):
    d=data(sym,'5d','1d')
    if d.empty:return np.nan,np.nan
    q=float(d.Close.iloc[-1]); p=float(d.Close.iloc[-2]) if len(d)>1 else q; return q,(q-p)/p*100 if p else 0


def indicators(d):
    d=d.copy(); c=d.Close.astype(float); h=d.High.astype(float); l=d.Low.astype(float); v=d.Volume.fillna(0).astype(float)
    for n in (5,21,50,200):
        d[f'EMA{n}']=c.ewm(span=n,adjust=False).mean(); d[f'SMA{n}']=c.rolling(n).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rs=gain/loss.replace(0,np.nan); d['RSI']=100-(100/(1+rs))
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean(); d['MACD']=e12-e26; d['MACD_SIGNAL']=d.MACD.ewm(span=9,adjust=False).mean(); d['MACD_HIST']=d.MACD-d.MACD_SIGNAL
    pc=c.shift(1); tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); d['ATR']=tr.rolling(14).mean()
    tp=(h+l+c)/3
    vol_sum=float(v.sum()) if len(v) else 0.0
    if vol_sum>0:
        d['VWAP']=(tp*v).cumsum()/v.cumsum().replace(0,np.nan)
        d.attrs['vwap_status']='VOLUME VWAP'
    else:
        d['VWAP']=tp.rolling(20,min_periods=1).mean()
        d.attrs['vwap_status']='VWAP PROXY (NO VOLUME)'
    mid=c.rolling(20).mean(); sd=c.rolling(20).std(); d['BB_MID']=mid; d['BB_UPPER']=mid+2*sd; d['BB_LOWER']=mid-2*sd
    lo=l.rolling(14).min(); hi=h.rolling(14).max(); d['STOCH_K']=100*(c-lo)/(hi-lo).replace(0,np.nan); d['STOCH_D']=d.STOCH_K.rolling(3).mean()
    tmean=tp.rolling(20).mean(); md=tp.rolling(20).apply(lambda x:np.mean(np.abs(x-np.mean(x))),raw=True); d['CCI']=(tp-tmean)/(0.015*md.replace(0,np.nan))
    up=h.diff(); down=-l.diff(); plus=pd.Series(np.where((up>down)&(up>0),up,0),index=d.index).rolling(14).sum(); minus=pd.Series(np.where((down>up)&(down>0),down,0),index=d.index).rolling(14).sum(); d['ADX']=100*(plus-minus)/(plus+minus).replace(0,np.nan)
    d['SUPPORT']=l.rolling(20).min(); d['RESISTANCE']=h.rolling(20).max(); ph=h.shift(1); pl=l.shift(1); pcc=c.shift(1); pivot=(ph+pl+pcc)/3; d['PIVOT']=pivot; d['R1']=2*pivot-pl; d['S1']=2*pivot-h; d['R2']=pivot+(h-l); d['S2']=pivot-(h-l)
    diff=h.max()-l.min(); d.attrs['fib']={'0%':h.max(),'23.6%':h.max()-diff*.236,'38.2%':h.max()-diff*.382,'50%':h.max()-diff*.5,'61.8%':h.max()-diff*.618,'78.6%':h.max()-diff*.786,'100%':l.min()}
    return d


@st.cache_data(ttl=10, show_spinner=False)
def fast_data(symbol, period, interval):
    """Short-TTL intraday feed used only for fast entry/exit confirmation."""
    try:
        d=yf.download(symbol, period=period, interval=interval, auto_adjust=False,
                      progress=False, threads=False)
        if d is None or d.empty:
            return pd.DataFrame()
        if isinstance(d.columns, pd.MultiIndex):
            d.columns=d.columns.get_level_values(0)
        return d.dropna(subset=['Open','High','Low','Close']).copy()
    except Exception:
        return pd.DataFrame()

def mtf_trend_label(d):
    if d is None or d.empty:
        return 'N/A'
    try:
        return trend_analysis(indicators(d))
    except Exception:
        return 'N/A'

def fast_mtf_engine(symbol, current_price):
    """1m entry timing + 5m confirmation + 15m direction."""
    frames={}
    for label,period,interval in [('1m','5d','1m'),('5m','1mo','5m'),('15m','1mo','15m')]:
        raw=fast_data(symbol,period,interval)
        frames[label]=indicators(raw) if not raw.empty else pd.DataFrame()

    d1,d5,d15=frames['1m'],frames['5m'],frames['15m']
    result={
        'signal':'WAIT','score':0,'entry_score':0,'exit_buy':False,'exit_sell':False,
        'trend_1m':mtf_trend_label(d1),'trend_5m':mtf_trend_label(d5),
        'trend_15m':mtf_trend_label(d15),'reason':[],
        'data_ok':not d1.empty
    }
    if d1.empty:
        return result

    r=d1.iloc[-1]
    score=0
    bullish=0
    bearish=0
    vol_ratio=0.0
    if len(d1)>=21 and pd.notna(r.get('Volume')):
        avg=float(d1['Volume'].tail(20).mean())
        vol_ratio=float(r['Volume'])/avg if avg else 0.0

    bullish_checks=[
        (pd.notna(r.get('Close')) and pd.notna(r.get('EMA5')) and r.Close>r.EMA5,10,'Price > EMA5'),
        (pd.notna(r.get('EMA5')) and pd.notna(r.get('EMA21')) and r.EMA5>r.EMA21,10,'EMA5 > EMA21'),
        (pd.notna(r.get('MACD_HIST')) and r.MACD_HIST>0,10,'MACD momentum positive'),
        (pd.notna(r.get('RSI')) and 52<=r.RSI<=72,10,'RSI bullish zone'),
        (pd.notna(r.get('VWAP')) and r.Close>r.VWAP,10,'Price > VWAP'),
        (vol_ratio>=1.2,10,'Volume expansion'),
        (result['trend_5m'] in ('BULLISH TREND','STRONG UPTREND'),15,'5m trend aligned'),
        (result['trend_15m'] in ('BULLISH TREND','STRONG UPTREND'),15,'15m trend aligned'),
    ]
    bearish_checks=[
        (pd.notna(r.get('Close')) and pd.notna(r.get('EMA5')) and r.Close<r.EMA5,10,'Price < EMA5'),
        (pd.notna(r.get('EMA5')) and pd.notna(r.get('EMA21')) and r.EMA5<r.EMA21,10,'EMA5 < EMA21'),
        (pd.notna(r.get('MACD_HIST')) and r.MACD_HIST<0,10,'MACD momentum negative'),
        (pd.notna(r.get('RSI')) and 28<=r.RSI<=48,10,'RSI bearish zone'),
        (pd.notna(r.get('VWAP')) and r.Close<r.VWAP,10,'Price < VWAP'),
        (vol_ratio>=1.2,10,'Volume expansion'),
        (result['trend_5m'] in ('BEARISH TREND','STRONG DOWNTREND'),15,'5m trend aligned'),
        (result['trend_15m'] in ('BEARISH TREND','STRONG DOWNTREND'),15,'15m trend aligned'),
    ]
    bull_score=sum(w for ok,w,_ in bullish_checks if ok)
    bear_score=sum(w for ok,w,_ in bearish_checks if ok)
    if bull_score>=70 and bull_score>bear_score:
        result['signal']='BUY'; result['score']=bull_score
        result['reason']=[label for ok,_,label in bullish_checks if ok]
    elif bear_score>=70 and bear_score>bull_score:
        result['signal']='SELL'; result['score']=bear_score
        result['reason']=[label for ok,_,label in bearish_checks if ok]
    else:
        result['score']=max(bull_score,bear_score)
        result['reason']=['MTF confirmation incomplete']

    # Fast exit: momentum/trend deterioration, independent of slow news/macro calls.
    result['exit_buy']=(
        pd.notna(r.get('MACD_HIST')) and r.MACD_HIST<0 and
        pd.notna(r.get('Close')) and pd.notna(r.get('EMA5')) and r.Close<r.EMA5 and
        (pd.isna(r.get('VWAP')) or r.Close<r.VWAP)
    )
    result['exit_sell']=(
        pd.notna(r.get('MACD_HIST')) and r.MACD_HIST>0 and
        pd.notna(r.get('Close')) and pd.notna(r.get('EMA5')) and r.Close>r.EMA5 and
        (pd.isna(r.get('VWAP')) or r.Close>r.VWAP)
    )
    result['volume_ratio']=vol_ratio
    return result


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
    if c.iloc[p]<o.iloc[p] and c.iloc[i]>o.iloc[i] and o.iloc[i]<c.iloc[p] and o.iloc[i]>mid:out.append('Piercing Pattern')
    if c.iloc[p]>o.iloc[p] and c.iloc[i]<o.iloc[i] and o.iloc[i]>c.iloc[p] and o.iloc[i]<mid:out.append('Dark Cloud Cover')
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

def signal(row, pattern_bias=0):
    checks=[row.Close>row.EMA5,row.EMA5>row.EMA21,row.EMA21>row.EMA50,row.EMA50>row.EMA200,row.MACD>row.MACD_SIGNAL,row.RSI>50,row.Close>row.VWAP,row.ADX>20,row.Close>row.PIVOT]
    score=sum(bool(x) for x in checks if pd.notna(x))
    score=int(max(0,min(10,score+int(pattern_bias))))
    return ('BUY' if score>=7 else 'SELL' if score<=3 else 'WAIT'),score

def candle_pattern_bias(d):
    patterns=candle_patterns(d)
    bullish={'Hammer','Bullish Marubozu','Bullish Engulfing','Piercing Pattern','Morning Star','Three White Soldiers','Possible Double Bottom'}
    bearish={'Shooting Star','Bearish Marubozu','Bearish Engulfing','Dark Cloud Cover','Evening Star','Three Black Crows','Possible Double Top'}
    bull=sum(x in bullish for x in patterns)
    bear=sum(x in bearish for x in patterns)
    return (1 if bull>bear else -1 if bear>bull else 0), patterns

def trendline_values(d, lookback=60):
    r=d.tail(min(lookback,len(d))).copy()
    if len(r)<10:
        return r.index, np.full(len(r),np.nan), np.full(len(r),np.nan), 0.0
    x=np.arange(len(r),dtype=float)
    hi=np.polyfit(x,r['High'].astype(float).values,1)
    lo=np.polyfit(x,r['Low'].astype(float).values,1)
    upper=np.polyval(hi,x); lower=np.polyval(lo,x)
    slope=((hi[0]+lo[0])/2)/max(float(r['Close'].mean()),1e-9)*100
    return r.index, upper, lower, slope

def confidence_score(row, d, pcr, news_info, pattern_bias, trend, historical_hit_rate=np.nan, macro_score=0, institutional_score=0, basket_score=0, direction='WAIT'):
    points=0; reasons=[]
    bullish = direction == 'BUY'; bearish = direction == 'SELL'
    checks=[
        (((row.Close>row.EMA5) if bullish else (row.Close<row.EMA5) if bearish else False), 'Price/EMA5 aligned'),
        (((row.EMA5>row.EMA21) if bullish else (row.EMA5<row.EMA21) if bearish else False), 'EMA5/EMA21 aligned'),
        (((row.EMA21>row.EMA50) if bullish else (row.EMA21<row.EMA50) if bearish else False), 'EMA21/EMA50 aligned'),
        (((row.EMA50>row.EMA200) if bullish else (row.EMA50<row.EMA200) if bearish else False), 'EMA50/EMA200 aligned'),
        (((row.MACD>row.MACD_SIGNAL) if bullish else (row.MACD<row.MACD_SIGNAL) if bearish else False), 'MACD aligned'),
        (((row.RSI>50) if bullish else (row.RSI<50) if bearish else False), 'RSI aligned'),
        (((row.Close>row.VWAP) if bullish else (row.Close<row.VWAP) if bearish else False), 'VWAP aligned'),
        (((row.Close>row.PIVOT) if bullish else (row.Close<row.PIVOT) if bearish else False), 'Pivot aligned'),
        (pd.notna(row.ADX) and row.ADX>20, 'Trend strength ADX > 20'),
    ]
    for ok,label in checks:
        if ok: points+=1; reasons.append(label)
    if pattern_bias>0 and bullish: points+=1; reasons.append('Bullish candlestick pattern')
    elif pattern_bias<0 and bearish: points+=1; reasons.append('Bearish candlestick pattern')
    elif pattern_bias!=0 and direction!='WAIT': points-=1; reasons.append('Candlestick pattern conflicts with signal')
    if pd.notna(pcr):
        if (pcr>1.0 and bullish) or (pcr<0.8 and bearish): points+=1; reasons.append('PCR aligned')
        elif (pcr>1.0 and bearish) or (pcr<0.8 and bullish): points-=1; reasons.append('PCR conflicts with signal')
    trend_bull=trend in ('STRONG UPTREND','BULLISH TREND'); trend_bear=trend in ('STRONG DOWNTREND','BEARISH TREND')
    if (trend_bull and bullish) or (trend_bear and bearish): points+=1; reasons.append('Trendline/trend aligned')
    elif (trend_bull or trend_bear) and direction!='WAIT': points-=1; reasons.append('Trend conflicts with signal')
    news_bull=news_info.get('bias')=='BULLISH NEWS BIAS'; news_bear=news_info.get('bias')=='BEARISH NEWS BIAS'
    if (news_bull and bullish) or (news_bear and bearish): points+=1; reasons.append('News aligned')
    elif (news_bull or news_bear) and direction!='WAIT': points-=1; reasons.append('News conflicts with signal')
    technical=int(max(0,min(100,50+points*4)))
    if pd.notna(historical_hit_rate):
        confidence=0.60*technical+0.40*float(historical_hit_rate)
        reasons.append(f'Historical directional hit rate {float(historical_hit_rate):.1f}%')
    else:
        confidence=min(85,technical)
        reasons.append('Historical hit rate unavailable; confidence capped at 85%')
    confidence += max(-5,min(5,macro_score)) + max(-5,min(5,institutional_score)) + max(-5,min(5,basket_score))
    confidence=int(round(max(0,min(95,confidence))))
    return confidence,reasons


def quote_url(s):return url_quote(s)

def pnl(t,price):
    return (price-t['Entry'])*t['Quantity'] if t['Side']=='BUY' else (t['Entry']-price)*t['Quantity']

def close_trade(i,price):
    t=st.session_state.paper_trades[i]; p=pnl(t,price); t['Exit']=price; t['Exit Time']=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); t['Final P/L']=p; t['Status']='CLOSED'; st.session_state.realized_pnl += p


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

def multifactor_signal(row, d, pcr=None, pattern_bias=0):
    base_sig, score = signal(row, pattern_bias)
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


def enhanced_signal(base_sig, base_score, pattern_bias, news_info, macro_score, institutional_score, basket_score, pcr):
    score=float(base_score)
    if news_info.get('bias')=='BULLISH NEWS BIAS': score += 0.5
    elif news_info.get('bias')=='BEARISH NEWS BIAS': score -= 0.5
    score += max(-1.0,min(1.0,float(macro_score)*0.5))
    score += max(-1.0,min(1.0,float(institutional_score)*0.5))
    score += max(-0.5,min(0.5,float(basket_score)*0.5))
    if pd.notna(pcr):
        if pcr>1.05: score += 0.5
        elif pcr<0.75: score -= 0.5
    if pattern_bias>0: score += 0.5
    elif pattern_bias<0: score -= 0.5
    score=max(0,min(10,score))
    if score>=7.5: sig='BUY'
    elif score<=2.5: sig='SELL'
    else: sig='WAIT'
    strength='STRONG BUY' if score>=8.5 else 'BUY BIAS' if score>=6.5 else 'STRONG SELL' if score<=1.5 else 'SELL BIAS' if score<=3.5 else 'NO SIGNAL'
    confirmation=(sig in ('BUY','SELL') and abs(score-5)>=2.0)
    return sig,round(score,1),strength,confirmation

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
    rr1=abs((t1-entry)/(entry-sl)) if direction in ('BUY','SELL') and entry!=sl else np.nan
    rr2=abs((t2-entry)/(entry-sl)) if direction in ('BUY','SELL') and entry!=sl else np.nan
    rr3=abs((t3-entry)/(entry-sl)) if direction in ('BUY','SELL') and entry!=sl else np.nan
    # Recommended RR: prefer 1:2 when the structure allows it; otherwise 1:1.5.
    # 1:1 is displayed only as a lower-quality fallback, never as the preferred setup.
    if direction in ('BUY','SELL'):
        if pd.notna(rr3) and rr3 >= 2.0:
            recommended_rr = 2.0
            rr_label = '1:2'
        elif pd.notna(rr2) and rr2 >= 1.5:
            recommended_rr = 1.5
            rr_label = '1:1.5'
        elif pd.notna(rr1) and rr1 >= 1.0:
            recommended_rr = 1.0
            rr_label = '1:1 (LOW QUALITY)'
        else:
            recommended_rr = np.nan
            rr_label = 'NO TRADE'
    else:
        recommended_rr = np.nan
        rr_label = 'NO TRADE'
    return {'Direction':direction,'Entry':entry,'Stop Loss':sl,'Target 1':t1,'Target 2':t2,'Target 3':t3,'Trailing Stop':trailing,'Break-even Stop':be,'Risk':risk,
            'RR to T1': rr1,'RR to T2':rr2,'RR to T3':rr3,
            'Recommended RR':recommended_rr,'Recommended RR Label':rr_label,
            'Support':support,'Resistance':resistance,'Pivot':pivot}

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
    d=data(symbol, period, '1d')
    if d.empty or len(d)<80:
        return pd.DataFrame(), {}
    d=indicators(d).copy()
    records=[]
    for i in range(60, len(d)-1):
        hist=d.iloc[:i+1]
        row=d.iloc[i]
        pb,_=candle_pattern_bias(hist)
        sig,score=signal(row,pb)
        trend=trend_analysis(hist) if len(hist)>=30 else 'MIXED / TRANSITION'
        if sig=='BUY' and trend not in ('STRONG UPTREND','BULLISH TREND'): sig='WAIT'
        if sig=='SELL' and trend not in ('STRONG DOWNTREND','BEARISH TREND'): sig='WAIT'
        if sig=='BUY' and score<7: sig='WAIT'
        if sig=='SELL' and score>3: sig='WAIT'
        if sig=='WAIT':
            continue
        entry=float(d['Open'].iloc[i+1])
        atr=float(row['ATR']) if pd.notna(row['ATR']) else max(entry*0.01,0.01)
        atr=max(atr,entry*0.001)
        sl=entry-atr if sig=='BUY' else entry+atr
        target=entry+2*atr if sig=='BUY' else max(0.01,entry-2*atr)
        exit_price=float(d['Close'].iloc[min(i+10,len(d)-1)])
        exit_date=d.index[min(i+10,len(d)-1)]
        outcome='TIME EXIT'
        for j in range(i+1,min(i+11,len(d))):
            hi=float(d['High'].iloc[j]); lo=float(d['Low'].iloc[j])
            if sig=='BUY':
                if lo<=sl:
                    exit_price=sl; exit_date=d.index[j]; outcome='LOSS'; break
                if hi>=target:
                    exit_price=target; exit_date=d.index[j]; outcome='WIN'; break
            else:
                if hi>=sl:
                    exit_price=sl; exit_date=d.index[j]; outcome='LOSS'; break
                if lo<=target:
                    exit_price=target; exit_date=d.index[j]; outcome='WIN'; break
        ret=(exit_price-entry)/entry*100 if sig=='BUY' else (entry-exit_price)/entry*100
        if outcome=='TIME EXIT': outcome='WIN' if ret>0 else 'LOSS' if ret<0 else 'FLAT'
        records.append({'Signal Date':d.index[i],'Entry Date':d.index[i+1],'Exit Date':exit_date,'Signal':sig,'Score':score,'Entry':entry,'Stop Loss':sl,'Target':target,'Exit':exit_price,'Return %':ret,'Outcome':outcome})
    bt=pd.DataFrame(records)
    if bt.empty:return bt,{}
    wins=int((bt['Outcome']=='WIN').sum()); losses=int((bt['Outcome']=='LOSS').sum()); flats=int((bt['Outcome']=='FLAT').sum()); total=len(bt)
    stats={'signals':total,'wins':wins,'losses':losses,'flats':flats,'win_rate':wins/total*100,'loss_rate':losses/total*100,'avg_return':float(bt['Return %'].mean()),'net_return_pct':float(bt['Return %'].sum()),'max_drawdown_pct':float((bt['Return %'].cumsum().cummax()-bt['Return %'].cumsum()).max()) if len(bt) else 0.0,'buy_trades':int((bt[bt.Signal=='BUY']).shape[0]),'sell_trades':int((bt[bt.Signal=='SELL']).shape[0]),'buy_wins':int((bt[(bt.Signal=='BUY') & (bt.Outcome=='WIN')]).shape[0]),'buy_losses':int((bt[(bt.Signal=='BUY') & (bt.Outcome=='LOSS')]).shape[0]),'sell_wins':int((bt[(bt.Signal=='SELL') & (bt.Outcome=='WIN')]).shape[0]),'sell_losses':int((bt[(bt.Signal=='SELL') & (bt.Outcome=='LOSS')]).shape[0]),'buy_win_rate':float((bt[(bt.Signal=='BUY') & (bt.Outcome=='WIN')].shape[0]/max(1, (bt.Signal=='BUY').sum())*100)) if (bt.Signal=='BUY').sum() else np.nan,'sell_win_rate':float((bt[(bt.Signal=='SELL') & (bt.Outcome=='WIN')].shape[0]/max(1, (bt.Signal=='SELL').sum())*100)) if (bt.Signal=='SELL').sum() else np.nan}
    return bt,stats


def backtest_diagnostics(bt):
    if bt is None or bt.empty:
        return pd.DataFrame(), 'No losing trades to diagnose.'
    losses=bt[bt['Outcome']=='LOSS'].copy()
    if losses.empty:
        return pd.DataFrame(), 'No losing trades in this sample.'
    buckets=[]
    if 'ADX' in losses: buckets.append(('Weak trend ADX < 20', int((losses['ADX']<20).sum())))
    if 'RSI' in losses: buckets.append(('RSI extreme (<30 or >70)', int(((losses['RSI']<30)|(losses['RSI']>70)).sum())))
    if 'VWAP Gap ATR' in losses: buckets.append(('Close within 0.25 ATR of VWAP', int((losses['VWAP Gap ATR']<0.25).sum())))
    if 'Score' in losses: buckets.append(('Borderline ensemble score (<=7)', int((losses['Score']<=7).sum())))
    if 'Trend' in losses: buckets.append(('Trend was mixed/sideways', int(losses['Trend'].isin(['MIXED / TRANSITION','SIDEWAYS / RANGE']).sum())))
    if 'Pattern Bias' in losses: buckets.append(('Candlestick bias conflicted/neutral', int((losses['Pattern Bias']<=0).sum())))
    buckets.append(('Stop Loss Hit', len(losses)))
    diag=pd.DataFrame(buckets,columns=['Diagnostic','Count']).sort_values('Count',ascending=False)
    return diag, 'These are observable conditions present in losing trades, not proof that any single factor caused the loss. The ensemble gate is designed to filter several weaker setups.'


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
    except Exception:
        return np.nan, pd.DataFrame(), 'Option chain unavailable'

@st.cache_data(ttl=60, show_spinner=False)
def preopen_snapshot():
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
    queries = [name, f"{name} India stock market", "India markets RBI Fed crude oil geopolitical", "India government support sector infrastructure defence semiconductor renewable energy", "India market volatility geopolitical risk and crude oil outlook"]
    out=[]; seen=set(); headers={'User-Agent':'Mozilla/5.0'}
    for q in queries:
        try:
            u='https://query1.finance.yahoo.com/v1/finance/search?q='+url_quote(q)+'&newsCount=10'
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
    bull=['beat','upgrade','buyback','growth','profit','surge','rally','strong demand','positive','approval','order win','capex','government support','support package','incentive','pli','subsidy']
    bear=['downgrade','miss','loss','fraud','default','war','sanction','attack','tariff','recession','inflation','rate hike','selloff','fall','drop','weak demand','geopolitical','conflict','crisis']
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
    if abs(price-prev_close)/prev_close < 0.002: factors.append('Price is close to previous close; confirmation preferred')
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
        payload=r.json(); rows=payload if isinstance(payload,list) else payload.get('data',[])
        out=[]
        for x in rows:
            if isinstance(x,dict): out.append(x)
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame()

def institutional_bias(fd):
    """Extract a cautious FII/DII directional score from whatever column names NSE returns."""
    if fd is None or fd.empty: return 0,'FII/DII unavailable'
    text=' '.join(map(str,fd.columns)).lower()
    try:
        row=fd.iloc[-1]
        def val(keys):
            for c in fd.columns:
                lc=str(c).lower().replace(' ','')
                if any(k in lc for k in keys):
                    x=pd.to_numeric(row[c],errors='coerce')
                    if pd.notna(x): return float(x)
            return np.nan
        fii_net=val(['fii_net','fii/fpi_net','fpi_net','net_fii','netfii'])
        dii_net=val(['dii_net','net_dii','netdii'])
        if pd.notna(fii_net) and pd.notna(dii_net):
            score=(1 if fii_net>0 else -1)+(1 if dii_net>0 else -1)
            return score,f'FII net {fii_net:.0f}; DII net {dii_net:.0f}'
    except Exception: pass
    return 0,'FII/DII columns not recognized'

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
st.session_state.max_position_value = st.sidebar.number_input('Max Position Value', 1000.0, 10000000.0, float(st.session_state.max_position_value), 1000.0)
st.session_state.auto_exit = st.sidebar.toggle('Paper Auto SL/Target/Trailing', value=st.session_state.auto_exit)
auto_refresh = st.sidebar.toggle('[AUTO] Auto Refresh', value=False, help='Refresh live data automatically')
refresh_seconds = st.sidebar.selectbox('Refresh Interval', [10,15,30,60,120,300], index=1, format_func=lambda x:f'{x} seconds', disabled=not auto_refresh)
if auto_refresh and st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds*1000, key='live_market_autorefresh')
elif auto_refresh and st_autorefresh is None:
    st.sidebar.warning('Install streamlit-autorefresh to enable automatic refresh.')
asset=st.sidebar.selectbox('Asset Type',['NSE Stocks','Indices','Crypto','Custom'])
if asset=='NSE Stocks': name=st.sidebar.selectbox('Stock',list(NIFTY)); symbol=NIFTY[name]
elif asset=='Indices': name=st.sidebar.selectbox('Index',list(INDEXES)); symbol=INDEXES[name]
elif asset=='Crypto': name=st.sidebar.selectbox('Crypto',list(CRYPTO)); symbol=CRYPTO[name]
else: symbol=st.sidebar.text_input('Custom Yahoo Symbol','RELIANCE.NS').upper().strip(); name=symbol
chart_mode=st.sidebar.radio('Chart Mode',['Current Live','Historical'],horizontal=True)
periods={'1 Day':'1d','5 Days':'5d','1 Month':'1mo','3 Months':'3mo','6 Months':'6mo','1 Year':'1y','2 Years':'2y','5 Years':'5y'}
period_name=st.sidebar.selectbox('Chart Period',list(periods),index=2)
ints={'5 Minutes':'5m','15 Minutes':'15m','30 Minutes':'30m','1 Hour':'60m','Daily':'1d'}
int_name=st.sidebar.selectbox('Timeframe',list(ints),index=1)
selected_interval=ints[int_name]
if chart_mode=='Current Live':
    fetch_period='2d' if asset=='Crypto' else '1d'
else:
    fetch_period=periods[period_name]
    if selected_interval in ('5m','15m','30m') and fetch_period in ('3mo','6mo','1y','2y','5y'):
        selected_interval='1d'
        st.sidebar.info('Long historical range uses Daily candles because intraday history is provider-limited.')

if st.sidebar.button('[REFRESH] Refresh Data',use_container_width=True):st.cache_data.clear();st.rerun()
if st.sidebar.button('[LOGOUT] Logout',use_container_width=True):st.session_state.logged_in=False;st.rerun()

# ---------------- MAIN ----------------
st.title('[CHART] Advanced Trading Dashboard')
st.caption(f'{name} | {symbol} | {selected_interval} | {chart_mode} | {datetime.now().strftime("%H:%M:%S")} | Auto Refresh: {"ON" if auto_refresh else "OFF"}')
d=data(symbol,fetch_period,selected_interval)
if d.empty:st.error('Data unavailable. Try another symbol or Daily timeframe.');st.stop()
d=indicators(d); last=d.iloc[-1]; prev=d.iloc[-2] if len(d)>1 else last
price=float(last.Close); change=price-float(prev.Close); pct=change/float(prev.Close)*100 if prev.Close else 0
fast_mtf = fast_mtf_engine(symbol, price)
if asset=='Crypto':
    cprice,cpct=crypto_24h_quote(symbol)
    if pd.notna(cprice):
        price=float(cprice); pct=float(cpct) if pd.notna(cpct) else pct; change=price*(pct/100)

auto_events=update_open_paper_positions(price)
if auto_events:
    for ev in auto_events: st.toast(ev)
pcr_oi, pcr_summary, pcr_status = option_pcr(symbol)
pattern_bias, current_patterns = candle_pattern_bias(d)
strength, mf_score, vol_label, vol_ratio, pcr_label, live_confirmation = multifactor_signal(last, d, pcr_oi, pattern_bias)
sig,score=signal(last, pattern_bias)
levels=trade_levels(price,last,strength)
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
levels=trade_levels(price,last,strength)
option_suggest, option_suggest_status = option_suggestions(symbol, price)
hit_rate=summary_stats.get('buy_win_rate' if sig=='BUY' else 'sell_win_rate',np.nan) if summary_stats else np.nan
confidence, confidence_reasons = confidence_score(last, d, pcr_oi, news_info, pattern_bias, trend_now, hit_rate, macro_score, institutional_score, basket_score, sig)
if pd.notna(hit_rate) and float(hit_rate)<50:
    confidence=min(confidence,60)
    confidence_reasons.append('Historical hit rate below 50%; confidence capped at 60%')
trend_idx, trend_upper, trend_lower, trend_slope = trendline_values(d)

m=st.columns(7); m[0].metric('Live Price',fmt_price(price),fmt_num(change,2,'',''));m[1].metric('Change',fmt_pct(pct));m[2].metric('RSI',fmt_num(last.RSI));m[3].metric('MACD',fmt_num(last.MACD));m[4].metric('Signal',sig,f'{strength}');m[5].metric('Volume',vol_label,f'{vol_ratio:.2f}x');m[6].metric('Trend',trend_now)
st.metric('Signal',sig,f'{strength} | Score {mf_score}/10')
st.caption(f"VWAP status: {d.attrs.get('vwap_status','N/A')} | PCR status: {pcr_status}")

st.subheader('[SIGNAL] Signal Quality, PCR, VWAP, Pattern and Confidence')
q1,q2,q3,q4,q5,q6=st.columns(6)
q1.metric('Signal',sig,f'{strength}')
q2.metric('Confidence',f'{confidence}%')
q3.metric('PCR (OI)',fmt_num(pcr_oi))
q4.metric('VWAP',fmt_price(last.VWAP))
q5.metric('Trend',trend_now)
q6.metric('Pattern Bias','BULLISH' if pattern_bias>0 else 'BEARISH' if pattern_bias<0 else 'NEUTRAL')
if current_patterns:
    st.write('Candlestick patterns used in signal: **' + ', '.join(current_patterns) + '**')
else:
    st.write('Candlestick patterns used in signal: **No strong pattern detected**')
st.caption('Confidence is a rule-based score, not a guaranteed probability. PCR is shown only when an option-chain feed is available; VWAP becomes a clearly-labelled proxy when volume is unavailable.')

st.subheader('[BACKTEST] 1-Year Historical Result')
if summary_stats:
    b1,b2,b3,b4,b5,b6=st.columns(6)
    b1.metric('Total Trades',summary_stats['signals'])
    b2.metric('Wins',summary_stats['wins'])
    b3.metric('Losses',summary_stats['losses'])
    b4.metric('Win Rate',f"{summary_stats['win_rate']:.1f}%")
    b5.metric('Loss Rate',f"{summary_stats['loss_rate']:.1f}%")
    b6.metric('Max Drawdown',f"{summary_stats['max_drawdown_pct']:.2f}%")
    c1,c2,c3,c4=st.columns(4)
    c1.metric('BUY Trades',summary_stats['buy_trades'],f"W {summary_stats['buy_wins']} / L {summary_stats['buy_losses']}")
    c2.metric('SELL Trades',summary_stats['sell_trades'],f"W {summary_stats['sell_wins']} / L {summary_stats['sell_losses']}")
    c3.metric('BUY Hit Rate',f"{summary_stats['buy_win_rate']:.1f}%" if pd.notna(summary_stats['buy_win_rate']) else 'N/A')
    c4.metric('SELL Hit Rate',f"{summary_stats['sell_win_rate']:.1f}%" if pd.notna(summary_stats['sell_win_rate']) else 'N/A')
    st.caption('Backtest uses next-day-open entry, 1 ATR stop, 2 ATR target and up to 10 trading bars. Historical hit rate is not a future win probability.')
else:
    st.warning('Backtest needs at least about 80 daily candles. Select a longer chart/data period or a symbol with sufficient history.')

st.subheader('[TRADE] Trade Setup - Entry / Targets / Stop Loss / RR')
if levels['Direction'] != 'WAIT':
    e1,e2,e3,e4,e5,e6,e7=st.columns(7)
    e1.metric('Suggested Entry',fmt_price(levels['Entry']))
    e2.metric('Initial Stop Loss',fmt_price(levels['Stop Loss']))
    e3.metric('Target 1',fmt_price(levels['Target 1']))
    e4.metric('Target 2',fmt_price(levels['Target 2']))
    e5.metric('Target 3',fmt_price(levels['Target 3']))
    e6.metric('Risk / Unit',fmt_price(levels['Risk']))
    e7.metric('Recommended RR',levels.get('Recommended RR Label','NO TRADE'))
    rr_ok=pd.notna(levels.get('Recommended RR')) and float(levels.get('Recommended RR'))>=1.5
    rr_t1 = levels.get('RR to T1', np.nan)
    rr_t2 = levels.get('RR to T2', np.nan)
    rr_t3 = levels.get('RR to T3', np.nan)
    st.write(f"**RR Map:** T1 = 1:{fmt_num(rr_t1)} | T2 = 1:{fmt_num(rr_t2)} | T3 = 1:{fmt_num(rr_t3)} | **Recommended = {levels.get('Recommended RR Label','NO TRADE')}**")
    st.caption(f"{levels['Direction']} setup | RR quality: {'PASS' if rr_ok else 'FAIL / LOW QUALITY'} | Break-even after T1: {fmt_price(levels['Break-even Stop'])} | Trailing Stop: {fmt_price(levels['Trailing Stop'])}")
else:
    st.info('No clear BUY/SELL signal. Entry, targets, stop-loss and RR are not activated until a directional signal appears.')

st.subheader('[QUALITY] Recommended RR & Accuracy Filter')
if levels['Direction'] != 'WAIT':
    recommended_rr = levels.get('Recommended RR', np.nan)
    rr_label = levels.get('Recommended RR Label', 'NO TRADE')
    quality_cols = st.columns(4)
    quality_cols[0].metric('Recommended RR', rr_label)
    quality_cols[1].metric('RR to T1', f"1:{fmt_num(levels.get('RR to T1', np.nan))}")
    quality_cols[2].metric('RR to T2', f"1:{fmt_num(levels.get('RR to T2', np.nan))}")
    quality_cols[3].metric('RR to T3', f"1:{fmt_num(levels.get('RR to T3', np.nan))}")
    if rr_label == '1:2':
        st.success('HIGHER-QUALITY RR setup: 1:2 or better structure available.')
    elif rr_label == '1:1.5':
        st.info('ACCEPTABLE RR setup: 1:1.5. Prefer confirmation before entry.')
    elif rr_label == '1:1 (LOW QUALITY)':
        st.warning('LOW RR setup: 1:1. The dashboard should avoid treating this as a high-quality entry.')
    else:
        st.error('NO TRADE: available structure does not provide the minimum 1:1 RR.')
else:
    st.info('No directional setup, so no RR recommendation is active.')

st.subheader('[MTF] Fast Entry / Exit Confirmation')
f1,f2,f3,f4,f5=st.columns(5)
f1.metric('Fast Signal',fast_mtf['signal'],f"{fast_mtf['score']}/100")
f2.metric('1m Trend',fast_mtf['trend_1m'])
f3.metric('5m Trend',fast_mtf['trend_5m'])
f4.metric('15m Trend',fast_mtf['trend_15m'])
f5.metric('Fast Volume',f"{fast_mtf.get('volume_ratio',0):.2f}x")
st.caption("Fast engine: 1m entry timing + 5m confirmation + 15m direction. PCR/news/macro remain confirmation layers, not the primary trigger.")
if fast_mtf['signal']!='WAIT':
    st.write("**Fast confirmation:** " + " | ".join(fast_mtf['reason'][:8]))
if fast_mtf['exit_buy'] or fast_mtf['exit_sell']:
    st.warning("Fast exit condition detected: momentum/EMA/VWAP deterioration. Review the open position before holding further.")

st.subheader('[ANALYSIS]  Live Critical Analysis')
ca,cb,cc,cd,ce=st.columns(5)
ca.metric('Critical Action', critical['action'])
cb.metric('Critical Score', f"{critical['score']:+d}")
cc.metric('News Bias', news_info['bias'])
cd.metric('News Risk', news_info['risk'])
ce.metric('Headline Count', len(news_items))
if critical['factors']:
    st.write('**Factors:** ' + ' | '.join(critical['factors']))
st.write(f'**Macro / Institutional confirmation:** {institutional_text} | Basket bias: {basket_score:+d} | Macro score: {macro_score:+d}')
st.caption('Rule-based multifactor/news analysis. It is not a guaranteed prediction and does not execute real orders.')

st.subheader('[OPTIONS] Suggested CALL / PUT with Historical Hit Rate and Confidence')
if not option_suggest.empty:
    option_view=option_suggest.copy()
    if sig=='BUY':
        option_view=option_view[option_view['Type']=='CALL'].copy()
    elif sig=='SELL':
        option_view=option_view[option_view['Type']=='PUT'].copy()
    option_view['Historical Hit Rate']=np.nan
    option_view['Confidence']=confidence
    option_view['Win Chance (historical)']=option_view['Historical Hit Rate']
    for idx in option_view.index:
        if option_view.loc[idx,'Type']=='CALL': option_view.loc[idx,'Historical Hit Rate']=summary_stats.get('buy_win_rate',np.nan) if summary_stats else np.nan
        else: option_view.loc[idx,'Historical Hit Rate']=summary_stats.get('sell_win_rate',np.nan) if summary_stats else np.nan
    option_view['Premium']=pd.to_numeric(option_view['Premium'],errors='coerce')
    st.dataframe(option_view[['Type','Moneyness','Strike','Premium','Expiry','Historical Hit Rate','Win Chance (historical)','Confidence']],use_container_width=True,hide_index=True)
    st.caption('Historical Hit Rate is the underlying BUY/SELL setup hit rate from the selected backtest, used as context for CALL/PUT. It is not an option-specific probability or guarantee.')
else:
    st.warning('Option-chain data is unavailable for this symbol, so live ATM/OTM premium and PCR cannot be fabricated.')

st.subheader('[MACRO] Live Macro + Institutional + Top Company Context')
mc=st.columns(6)
for i,key in enumerate(['USD/INR','Crude Oil','India VIX','S&P 500','Nasdaq','Dow Jones']):
    obj=macro.get(key,{})
    mc[i].metric(key,fmt_num(obj.get('price')),fmt_pct(obj.get('change')))
st.write(f'**FII/DII:** {institutional_text}')
if not basket.empty:
    st.dataframe(basket,use_container_width=True,hide_index=True)

st.subheader('[GOV/NEWS] Government, Sector and Major Company Announcements')
st.caption('Government policy, sector support, major company announcements and geopolitical headlines are included in the news feed and critical analysis when the provider returns them.')
st.subheader('[DATA] Price & Indicators')
fig=go.Figure(go.Candlestick(x=d.index,open=d.Open,high=d.High,low=d.Low,close=d.Close,name='Price'))
for col in ['EMA5','EMA21','EMA50','EMA200','VWAP','BB_UPPER','BB_LOWER','SUPPORT','RESISTANCE','PIVOT','R1','S1']:
    if col in d:fig.add_trace(go.Scatter(x=d.index,y=d[col],name=col,mode='lines',line={'width':1}))
if len(trend_idx):
    fig.add_trace(go.Scatter(x=trend_idx,y=trend_upper,name='Trendline High',mode='lines',line={'width':3,'dash':'dash'}))
    fig.add_trace(go.Scatter(x=trend_idx,y=trend_lower,name='Trendline Low',mode='lines',line={'width':3,'dash':'dash'}))
fig.update_layout(height=620,xaxis_rangeslider_visible=False,template='plotly_dark',hovermode='x unified');st.plotly_chart(fig,use_container_width=True)

q1,q2,q3,q4,q5=st.columns(5)
q1.metric('Multi-factor Signal',strength)
q2.metric('Trend',trend_analysis(d))
q3.metric('Volume',vol_label,f'{vol_ratio:.2f}x')
q4.metric('PCR',f'{pcr_oi:.2f}' if pd.notna(pcr_oi) else 'N/A')
q5.metric('Confirmation','YES' if live_confirmation else 'WAIT')

tabs=st.tabs(['Indicators','Patterns','Backtest','Paper Trading','Market','Pre-Open','Options','News','Data','FII/DII & Risk','Settings','Angel One'])
with tabs[0]:
    c1,c2,c3=st.columns(3);c1.metric('EMA 5',f'{last.EMA5:.2f}');c1.metric('EMA 21',f'{last.EMA21:.2f}');c1.metric('EMA 50',f'{last.EMA50:.2f}');c2.metric('EMA 200',f'{last.EMA200:.2f}');c2.metric('VWAP',f'{last.VWAP:.2f}');c3.metric('ATR',f'{last.ATR:.2f}')
    a,b=st.columns(2)
    with a:
        f=go.Figure();f.add_trace(go.Scatter(x=d.index,y=d.MACD,name='MACD'));f.add_trace(go.Scatter(x=d.index,y=d.MACD_SIGNAL,name='Signal'));f.add_bar(x=d.index,y=d.MACD_HIST,name='Histogram');f.update_layout(height=320,template='plotly_dark',title='MACD');st.plotly_chart(f,use_container_width=True)
    with b:
        f=go.Figure(go.Scatter(x=d.index,y=d.RSI,name='RSI'));f.add_hline(y=70);f.add_hline(y=30);f.update_layout(height=320,template='plotly_dark',title='RSI');st.plotly_chart(f,use_container_width=True)
    fib=d.attrs['fib'];st.subheader('Fibonacci');st.dataframe(pd.DataFrame({'Level':fib.keys(),'Price':fib.values()}),use_container_width=True,hide_index=True)
with tabs[1]:
    st.subheader('[CANDLE] Candlestick Patterns Used by Signal')
    cp=candle_patterns(d)
    if cp:
        for x in cp:
            if x in {'Hammer','Bullish Marubozu','Bullish Engulfing','Piercing Pattern','Morning Star','Three White Soldiers'}: st.success(x + ' | Bullish input')
            elif x in {'Shooting Star','Bearish Marubozu','Bearish Engulfing','Dark Cloud Cover','Evening Star','Three Black Crows'}: st.error(x + ' | Bearish input')
            else: st.info(x)
    else:st.info('No strong candlestick pattern detected.')
    st.metric('Current Pattern Bias','BULLISH' if pattern_bias>0 else 'BEARISH' if pattern_bias<0 else 'NEUTRAL')
    st.subheader('[PATTERN] Chart Patterns'); ch=chart_patterns(d)
    if ch:
        for x in ch:st.info(x)
    else:st.info('No strong chart structure detected.')
    st.subheader('[TRENDLINE] Trendline Analysis')
    st.write(f'Trendline slope: {trend_slope:+.4f}% per bar')
with tabs[2]:
    st.subheader('[BACKTEST] Detailed Win / Loss Result')
    bt_periods={'1 Month':'1mo','2 Months':'2mo','3 Months':'3mo','6 Months':'6mo','1 Year':'1y','2 Years':'2y','5 Years':'5y'}
    bt_choice=st.selectbox('Backtest period',list(bt_periods.keys()),index=4)
    bt,stats=backtest(symbol,bt_periods[bt_choice])
    if stats:
        b1,b2,b3,b4,b5,b6=st.columns(6)
        b1.metric('Total Trades',stats['signals'])
        b2.metric('Wins',stats['wins'])
        b3.metric('Losses',stats['losses'])
        b4.metric('Win Rate',f"{stats['win_rate']:.2f}%")
        b5.metric('Loss Rate',f"{stats['loss_rate']:.2f}%")
        b6.metric('Max Drawdown',f"{stats['max_drawdown_pct']:.2f}%")
        x1,x2,x3,x4=st.columns(4)
        x1.metric('BUY W / L',f"{stats['buy_wins']} / {stats['buy_losses']}")
        x2.metric('SELL W / L',f"{stats['sell_wins']} / {stats['sell_losses']}")
        x3.metric('BUY Hit Rate',f"{stats['buy_win_rate']:.2f}%" if pd.notna(stats['buy_win_rate']) else 'N/A')
        x4.metric('SELL Hit Rate',f"{stats['sell_win_rate']:.2f}%" if pd.notna(stats['sell_win_rate']) else 'N/A')
        bt_view=bt.copy()
        bt_view['Cumulative Return %']=pd.to_numeric(bt_view['Return %'],errors='coerce').fillna(0).cumsum()
        eq=go.Figure()
        eq.add_trace(go.Scatter(x=bt_view['Exit Date'],y=bt_view['Cumulative Return %'],name='Cumulative Return %',mode='lines'))
        eq.update_layout(height=300,template='plotly_dark',title='Backtest Equity Curve (simple cumulative %)',xaxis_title='Exit Date',yaxis_title='Cumulative Return %')
        st.plotly_chart(eq,use_container_width=True)
        st.dataframe(bt_view.tail(300),use_container_width=True,hide_index=True)
        st.download_button('Download Backtest CSV',bt_view.to_csv(index=False).encode('utf-8'),file_name=f'backtest_{str(name).replace(" ","_")}_{bt_choice.replace(" ","_")}.csv',mime='text/csv',use_container_width=True)

    else:
        st.warning('Not enough historical data for this backtest period.')
    st.subheader('[CONFIDENCE] Current Setup')
    cc1,cc2,cc3=st.columns(3)
    cc1.metric('Current Signal',sig)
    cc2.metric('Confidence',f'{confidence}%')
    cc3.metric('Historical Direction Hit Rate',f"{summary_stats.get('buy_win_rate' if sig=='BUY' else 'sell_win_rate',np.nan):.2f}%" if summary_stats and pd.notna(summary_stats.get('buy_win_rate' if sig=='BUY' else 'sell_win_rate',np.nan)) else 'N/A')
    st.write('Why:', ' | '.join(confidence_reasons[:10]) if confidence_reasons else 'No strong confirming factors')
with tabs[3]:
    st.subheader('[PAPER] Paper Trading')
    a,b,c=st.columns(3)
    with a: side=st.selectbox('Side',['BUY','SELL']);qty=st.number_input('Quantity',1.0,step=1.0);entry=st.number_input('Entry Price',0.0,value=price,step=.05)
    with b:
        default_sl=levels['Stop Loss'] if levels['Direction']==side and pd.notna(levels['Stop Loss']) else (price-(float(last.ATR) if pd.notna(last.ATR) else price*.01) if side=='BUY' else price+(float(last.ATR) if pd.notna(last.ATR) else price*.01))
        default_target=levels['Target 1'] if levels['Direction']==side and pd.notna(levels['Target 1']) else (price+2*(float(last.ATR) if pd.notna(last.ATR) else price*.01) if side=='BUY' else price-2*(float(last.ATR) if pd.notna(last.ATR) else price*.01))
        sl=st.number_input('Stop Loss',0.0,value=float(max(.01,default_sl)),step=.05);target=st.number_input('Target 1',0.0,value=float(max(.01,default_target)),step=.05)
    with c:
        est=(price-entry)*qty if side=='BUY' else (entry-price)*qty;st.metric('Live P/L',f'{est:+,.2f}');st.metric('Paper Balance',f'{st.session_state.balance:,.2f}');st.metric('Realized P/L',f'{st.session_state.realized_pnl:,.2f}')
    if st.button('??| Open Paper Position',use_container_width=True):
        st.session_state.paper_trades.append({'Symbol':symbol,'Side':side,'Quantity':float(qty),'Entry':float(entry),'Stop Loss':float(sl),'Target':float(target),'Opened':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'Status':'OPEN'})
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
            status='Advance' if ch>.05 else 'Decline' if ch<-.05 else 'Unchanged';adv+=status=='Advance';dec+=status=='Decline';unch+=status=='Unchanged';rows.append({'Stock':n,'Change %':ch})
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
        p1.metric('Previous Close',f"{oa['prev_close']:,.2f}")
        p2.metric('Possible Open Mid',f"{oa['estimated_mid']:,.2f}")
        p3.metric('Possible Open Low',f"{oa['estimated_low']:,.2f}")
        p4.metric('Possible Open High',f"{oa['estimated_high']:,.2f}")
        st.metric('Pre-Session Multifactor Bias',oa['bias'],f"Factor score {oa['factor_score']}/5")
        pre=preopen_snapshot()
        if pre:
            st.success('Official NSE pre-open snapshot received.')
            st.json(pre)
        else:
            st.warning('Official NSE indicative pre-open data was not reachable from this Streamlit environment. The displayed range is an estimate from previous close/ATR and market factors, not an official quote.')
        st.caption("NSE pre-open session is 9:00 to 9:15 IST; when an equilibrium price is discovered, it becomes the day's open price.")
    else:
        st.info('Pre-open analysis unavailable for this symbol.')
with tabs[6]:
    st.subheader('[ANALYSIS]? Options Analysis')
    if not pcr_summary.empty:
        st.dataframe(pcr_summary,use_container_width=True,hide_index=True)
        st.metric('OI PCR',f"{pcr_oi:.2f}" if pd.notna(pcr_oi) else 'N/A')
    else:
        st.warning('Live option-chain PCR unavailable for this symbol. Select an index/option-enabled underlying.')
    st.selectbox('Underlying',['NIFTY','BANKNIFTY','RELIANCE','TCS','INFY','HDFCBANK']);st.info('Production integration can populate expiry, strike-wise CE/PE OI, volume, IV, PCR and Max Pain using a broker/data API.')
with tabs[7]:
    st.subheader('[NEWS] Market & Geopolitical News')
    st.metric('Critical News Bias',news_info['bias'],f"Risk {news_info['risk']}")
    ns=news_items
    if ns:
        for item in ns:
            title=item.get('title','Untitled');link=item.get('link','');pub=item.get('publisher','');st.markdown(f'### [{title}]({link})' if link else f'### {title}');st.caption(pub)
    else:st.info('News temporarily unavailable.')
with tabs[8]:
    cols=['Open','High','Low','Close','Volume','EMA5','EMA21','EMA50','EMA200','VWAP','RSI','MACD','MACD_SIGNAL','ATR','ADX','BB_UPPER','BB_MID','BB_LOWER','STOCH_K','STOCH_D','CCI','SUPPORT','RESISTANCE','PIVOT','R1','S1']
    st.dataframe(d[cols].tail(500) if all(c in d.columns for c in cols) else d.tail(500),use_container_width=True,hide_index=True)
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
    r1,r2,r3=st.columns(3);r1.metric('Suggested Qty',qty_s);r2.metric('Max Risk',f'{risk_cash:,.2f}');r3.metric('Risk %',f"{st.session_state.risk_per_trade:.2f}%")
    st.caption('Position size is a rule-based risk calculation, not a guarantee or order instruction.')
with tabs[10]:
    st.subheader('[SETTINGS] Settings & Data Controls')
    st.write('**Login:** username `admin`; initial password is `admin123`. The demo recovery PIN defaults to `1234` unless TRADING_RECOVERY_PIN is set.')
    st.write('**Auto refresh:** controlled from the sidebar. **Paper auto-exit:** can close positions at trailing SL/target using the current market price.')
    if st.button('Clear Cached Market Data'):
        st.cache_data.clear(); st.success('Cache cleared. Refresh the page to reload all feeds.')
    st.warning('Before real trading, replace demo authentication with secure hashed credentials/session management and connect a broker API with explicit order confirmation.')


with tabs[11]:
    st.subheader('[ANGEL ONE] Live Market + Manual Trading')
    st.caption('Live execution is manual only. The app will not auto-place an order from a signal.')

    if not ANGEL_SDK_AVAILABLE:
        st.error('Angel One SDK is not installed in this environment.')
        st.code('pip install smartapi-python pyotp logzero websocket-client', language='bash')
    else:
        if not st.session_state.get('angel_connected'):
            st.info('Enter credentials for this Streamlit session. Do not hard-code API keys, PINs or TOTP secrets in the Python file.')
            with st.form('angel_login_form'):
                ac1,ac2=st.columns(2)
                with ac1:
                    angel_api_key=st.text_input('Angel One API Key',value=os.getenv('ANGEL_API_KEY',''),type='password')
                    angel_client_code=st.text_input('Client Code',value=os.getenv('ANGEL_CLIENT_CODE',''))
                with ac2:
                    angel_pin=st.text_input('PIN',value=os.getenv('ANGEL_PIN',''),type='password')
                    angel_totp=st.text_input('Current 6-digit TOTP OR TOTP secret',value=os.getenv('ANGEL_TOTP_SECRET',''),type='password')
                connect_clicked=st.form_submit_button('Connect Angel One',use_container_width=True)
            if connect_clicked:
                ok,msg=angel_connect(angel_api_key,angel_client_code,angel_pin,angel_totp)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            topa,topb,topc=st.columns([2,1,1])
            profile=st.session_state.get('angel_profile') or {}
            topa.success(f"Connected: {profile.get('name') or profile.get('clientcode') or profile.get('clientCode') or 'Angel One account'}")
            if topb.button('Disconnect Angel One',use_container_width=True):
                angel_disconnect(); st.rerun()
            if topc.button('Refresh Broker Data',use_container_width=True):
                st.rerun()

            st.markdown('#### 1) Select live instrument')
            default_search = str(name).split('/')[0].strip()
            exchange = st.selectbox('Angel Exchange',['NSE','BSE','NFO','BFO','MCX'],index=0,key='angel_exchange')
            search_query = st.text_input('Search Angel One symbol',value=default_search,key='angel_search_query')
            if st.button('Search Instrument',use_container_width=True):
                found,msg=angel_search(exchange,search_query)
                st.session_state.angel_search_results=found
                if found.empty: st.warning(msg)

            found = st.session_state.get('angel_search_results', pd.DataFrame())
            if isinstance(found,pd.DataFrame) and not found.empty:
                labels=[f"{r['exchange']} | {r['tradingsymbol']} | token {r['symboltoken']}" for _,r in found.iterrows()]
                selected_label=st.selectbox('Instrument',labels,key='angel_instrument_label')
                selected_idx=labels.index(selected_label)
                r=found.iloc[selected_idx]
                st.session_state.angel_selected_instrument={'exchange':str(r['exchange']),'tradingsymbol':str(r['tradingsymbol']),'symboltoken':str(r['symboltoken'])}

            instrument=st.session_state.get('angel_selected_instrument')
            if instrument:
                ltp,ltp_info=angel_ltp(instrument)
                l1,l2,l3,l4=st.columns(4)
                l1.metric('Angel LTP',fmt_price(ltp))
                l2.metric('Instrument',instrument['tradingsymbol'])
                l3.metric('Exchange',instrument['exchange'])
                l4.metric('Signal',sig)
                if pd.notna(ltp):
                    st.caption(f"Angel One live LTP: {fmt_price(ltp)} | Dashboard/Yahoo reference: {fmt_price(price)}")
                with st.expander('Live quote details'):
                    st.json(ltp_info if ltp_info else {'status':'No quote details returned'})

                st.markdown('#### 2) Manual order ticket')
                mode=st.radio('Trading Mode',['PAPER ONLY','LIVE MANUAL'],horizontal=True,key='angel_trade_mode')
                oc1,oc2,oc3=st.columns(3)
                with oc1:
                    broker_side=st.selectbox('Order Side',['BUY','SELL'],key='angel_side')
                    broker_qty=st.number_input('Quantity',min_value=1,value=1,step=1,key='angel_qty')
                with oc2:
                    broker_order_type=st.selectbox('Order Type',['MARKET','LIMIT'],key='angel_order_type')
                    broker_product=st.selectbox('Product',['INTRADAY','DELIVERY'],key='angel_product')
                with oc3:
                    default_limit=float(ltp) if pd.notna(ltp) else float(price)
                    broker_limit=st.number_input('Limit Price',min_value=0.0,value=default_limit,step=0.05,key='angel_limit',disabled=(broker_order_type=='MARKET'))
                    st.metric('Recommended RR',levels.get('Recommended RR Label','NO TRADE'))

                if mode=='PAPER ONLY':
                    st.info('PAPER ONLY mode never sends an order to Angel One. Use the Paper Trading tab to record simulated positions.')
                else:
                    st.warning('LIVE MANUAL sends a real broker order only after you explicitly confirm it. Use only on an account you are legally authorized to operate.')
                    live_confirm=st.checkbox(
                        f"I confirm: send a REAL {broker_side} order for {int(broker_qty)} x {instrument['tradingsymbol']}.",
                        key='angel_live_confirm'
                    )
                    if st.button('Send Manual Live Order',type='primary',use_container_width=True,disabled=not live_confirm):
                        ok,msg,details=angel_place_manual_order(
                            instrument,broker_side,broker_qty,broker_order_type,broker_product,broker_limit
                        )
                        st.session_state.angel_last_order=details
                        if ok: st.success(msg)
                        else: st.error(msg)

                if st.session_state.get('angel_last_order'):
                    with st.expander('Last manual order response'):
                        st.json(st.session_state.angel_last_order)

                st.markdown('#### 3) Broker positions & order book')
                pcol,ocol=st.columns(2)
                with pcol:
                    st.write('**Positions**')
                    posdf=angel_positions()
                    if not posdf.empty: st.dataframe(posdf,use_container_width=True,hide_index=True)
                    else: st.info('No position data returned.')
                with ocol:
                    st.write('**Order Book**')
                    obdf=angel_order_book()
                    if not obdf.empty: st.dataframe(obdf,use_container_width=True,hide_index=True)
                    else: st.info('No order-book data returned.')
            else:
                st.info('Search and select an Angel One instrument to load live LTP and the manual order ticket.')

    st.markdown('#### Backtest status')
    st.write('The Backtest tab shows total trades, wins/losses, win rate, drawdown, BUY/SELL hit rate, equity curve, full trade table and CSV download.')
    st.caption('Backtests are historical simulations and do not guarantee future results. Validate with paper trading before considering live use.')


st.divider();st.subheader('[BROKER] Broker Integration')
a,b,c,e=st.columns(4);a.metric('Angel One','API Ready');b.metric('Upstox','API Ready');c.metric('Delta Exchange','API Ready');e.metric('Sahi','API Ready')
st.caption('Paper trading is functional. Angel One manual live-order integration is available in its own tab when the official SmartAPI SDK and authorized credentials are configured. Auto-ordering from signals remains disabled.')
st.info('Market data can be delayed, incomplete, or unavailable. Signals are informational and are not guaranteed investment advice.')
