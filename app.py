import os, hashlib, time, requests
from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title='Trading Intelligence Terminal', page_icon='📈', layout='wide')

def sha256(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def login():
    if st.session_state.get('authenticated'): return True
    st.title('🔐 Trading Intelligence Terminal')
    st.subheader('Application Login')
    user_expected = os.getenv('APP_USERNAME', 'admin')
    hash_expected = os.getenv('APP_PASSWORD_SHA256', sha256('admin123'))
    with st.form('login_form'):
        user = st.text_input('Username')
        password = st.text_input('Password', type='password')
        submit = st.form_submit_button('Login')
    if submit:
        if user == user_expected and sha256(password) == hash_expected:
            st.session_state.authenticated = True
            st.rerun()
        else: st.error('Username or password is incorrect.')
    st.caption('Testing default: admin / admin123. Change before public deployment.')
    return False
if not login(): st.stop()

st.title('📈 Trading Intelligence Terminal')
st.caption('Technical analysis • Global markets • PCR/OI context • Backtesting • Paper trading')
st.warning('Research and paper-trading tool only. Yahoo Finance data may be delayed or incomplete. No live broker orders are placed. Scores are not guaranteed win probabilities.')

for k,v in {'paper_trades': [], 'paper_position': None, 'angel_candles': None}.items():
    if k not in st.session_state: st.session_state[k] = v

SYMBOLS = {
 'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','NIFTY IT':'^CNXIT','NIFTY AUTO':'^CNXAUTO',
 'RELIANCE':'RELIANCE.NS','HDFC BANK':'HDFCBANK.NS','ICICI BANK':'ICICIBANK.NS','SBI':'SBIN.NS','AXIS BANK':'AXISBANK.NS','KOTAK BANK':'KOTAKBANK.NS',
 'TCS':'TCS.NS','INFOSYS':'INFY.NS','HCLTECH':'HCLTECH.NS','WIPRO':'WIPRO.NS','TECH MAHINDRA':'TECHM.NS',
 'MARUTI':'MARUTI.NS','M&M':'M&M.NS','TATA MOTORS':'TMPV.NS','BAJAJ AUTO':'BAJAJ-AUTO.NS','EICHER MOTORS':'EICHERMOT.NS','RELIANCE ENERGY CONTEXT':'RELIANCE.NS'
}
GLOBAL = {'US S&P 500':'^GSPC','US Nasdaq':'^IXIC','US Dow Jones':'^DJI','Japan Nikkei':'^N225','Hong Kong Hang Seng':'^HSI','China Shanghai':'000001.SS','South Korea KOSPI':'^KS11','Brent Crude':'BZ=F','USD/INR':'INR=X','India VIX':'^INDIAVIX'}

ANGEL_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

@st.cache_data(ttl=3600, show_spinner=False)
def load_angel_master():
    """Download Angel One's public instrument master and cache it for one hour."""
    response = requests.get(ANGEL_MASTER_URL, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Angel One instrument master returned an unexpected format.")
    frame = pd.DataFrame(payload)
    required = {"symbol", "token", "exch_seg"}
    if not required.issubset(frame.columns):
        raise RuntimeError("Instrument master is missing symbol/token/exchange fields.")
    for col in ("symbol", "name", "exch_seg", "instrumenttype", "token"):
        if col not in frame.columns: frame[col] = ""
        frame[col] = frame[col].fillna("").astype(str)
    frame["_sym_norm"] = frame["symbol"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    frame["_name_norm"] = frame["name"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    frame["_exchange_norm"] = frame["exch_seg"].str.upper()
    return frame

ANGEL_ALIASES = {
    "NIFTY 50": ["NIFTY 50", "NIFTY50"],
    "BANK NIFTY": ["NIFTY BANK", "BANKNIFTY", "NIFTYBANK"],
    "SENSEX": ["SENSEX"],
    "NIFTY IT": ["NIFTY IT", "NIFTYIT"],
    "NIFTY AUTO": ["NIFTY AUTO", "NIFTYAUTO"],
    "RELIANCE": ["RELIANCE-EQ", "RELIANCE"],
    "HDFC BANK": ["HDFCBANK-EQ", "HDFCBANK"],
    "ICICI BANK": ["ICICIBANK-EQ", "ICICIBANK"],
    "SBI": ["SBIN-EQ", "SBIN"],
    "AXIS BANK": ["AXISBANK-EQ", "AXISBANK"],
    "KOTAK BANK": ["KOTAKBANK-EQ", "KOTAKBANK"],
    "TCS": ["TCS-EQ", "TCS"],
    "INFOSYS": ["INFY-EQ", "INFY"],
    "HCLTECH": ["HCLTECH-EQ", "HCLTECH"],
    "WIPRO": ["WIPRO-EQ", "WIPRO"],
    "TECH MAHINDRA": ["TECHM-EQ", "TECHM"],
    "MARUTI": ["MARUTI-EQ", "MARUTI"],
    "M&M": ["M&M-EQ", "MM-EQ", "M&M"],
    "TATA MOTORS": ["TMPV-EQ", "TATAMOTORS-EQ", "TMPV", "TATAMOTORS"],
    "BAJAJ AUTO": ["BAJAJ-AUTO-EQ", "BAJAJAUTO-EQ", "BAJAJ-AUTO"],
    "EICHER MOTORS": ["EICHERMOT-EQ", "EICHERMOT"],
}

def resolve_angel_instrument(master, display_name, custom_query=""):
    """Resolve a selected display name to its exchange, trading symbol and token."""
    if master is None or master.empty: raise RuntimeError("Angel One instrument master is empty.")
    if display_name == "Custom Yahoo ticker":
        query = custom_query.strip().upper()
        if not query: raise RuntimeError("Enter an instrument name/symbol to search; token is resolved automatically.")
        candidates = [query, query + "-EQ"]
        preferred_exchange = "NSE"
    else:
        candidates = ANGEL_ALIASES.get(display_name, [display_name])
        preferred_exchange = "BSE" if display_name == "SENSEX" else "NSE"
    norm_candidates = ["".join(ch for ch in c.upper() if ch.isalnum()) for c in candidates]
    subset = master[master["_exchange_norm"].isin([preferred_exchange, "BSE" if preferred_exchange == "NSE" else "NSE"])].copy()
    # Exact symbol match first; stock entries are preferred over derivative contracts.
    for candidate in norm_candidates:
        hits = subset[subset["_sym_norm"] == candidate]
        if not hits.empty:
            hits = hits.copy()
            hits["_exchange_rank"] = (hits["_exchange_norm"] != preferred_exchange).astype(int)
            hits["_derivative_rank"] = hits["_sym_norm"].str.contains(r"FUT|CE|PE", regex=True).astype(int)
            hits = hits.sort_values(["_exchange_rank", "_derivative_rank"])
            row = hits.iloc[0]
            return {"exchange": row["exch_seg"].upper(), "symbol": row["symbol"], "token": row["token"], "name": row["name"] or row["symbol"]}
    # Index symbols sometimes use the display name in the master instead of the symbol field.
    for candidate in norm_candidates:
        hits = subset[(subset["_name_norm"] == candidate) | (subset["_sym_norm"] == candidate)]
        if not hits.empty:
            hits = hits.copy(); hits["_exchange_rank"] = (hits["_exchange_norm"] != preferred_exchange).astype(int)
            row = hits.sort_values("_exchange_rank").iloc[0]
            return {"exchange": row["exch_seg"].upper(), "symbol": row["symbol"], "token": row["token"], "name": row["name"] or row["symbol"]}
    raise RuntimeError(f"'{display_name if display_name != 'Custom Yahoo ticker' else custom_query}' was not found in Angel One's instrument master. Try another listed instrument or refresh the master.")

def fetch_angel_candles(api, instrument, interval, lookback_days):
    now = datetime.now()
    start = now - pd.Timedelta(days=int(lookback_days))
    response = api.getCandleData({
        "exchange": instrument["exchange"],
        "symboltoken": str(instrument["token"]),
        "interval": interval,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": now.strftime("%Y-%m-%d %H:%M"),
    })
    if not response or response.get("status") is False or not response.get("data"):
        message = (response or {}).get("message") or (response or {}).get("errorcode") or "No candles returned"
        raise RuntimeError(f"Angel One candle request failed: {message}. Check API permissions, market hours and selected interval.")
    cdf = pd.DataFrame(response["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
    cdf["timestamp"] = pd.to_datetime(cdf["timestamp"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]: cdf[col] = pd.to_numeric(cdf[col], errors="coerce")
    return cdf.dropna(subset=["timestamp", "open", "high", "low", "close"]).set_index("timestamp").sort_index()

def norm(raw):
    if raw is None or raw.empty: return pd.DataFrame()
    x=raw.copy()
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x.columns=[str(c).lower().replace(' ','') for c in x.columns]
    if not all(c in x.columns for c in ['open','high','low','close']): return pd.DataFrame()
    if 'volume' not in x: x['volume']=0.0
    for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna(subset=['open','high','low','close']).loc[lambda z: ~z.index.duplicated(keep='last')].sort_index()
@st.cache_data(ttl=15,show_spinner=False)
def yahoo(ticker,period,interval):
    try: return norm(yf.download(ticker,period=period,interval=interval,auto_adjust=False,progress=False,threads=False))
    except Exception: return pd.DataFrame()
def resample(x,rule):
    if x.empty:return x
    return x.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['open','high','low','close'])
def indicators(x):
    d=x.copy().sort_index()
    d['ema20']=d.close.ewm(span=20,adjust=False).mean(); d['ema50']=d.close.ewm(span=50,adjust=False).mean()
    # MACD and Bollinger Bands are calculated on the same candle series used for signal scoring
    d['macd']=d.close.ewm(span=12,adjust=False).mean()-d.close.ewm(span=26,adjust=False).mean()
    d['macd_signal']=d.macd.ewm(span=9,adjust=False).mean(); d['macd_hist']=d.macd-d.macd_signal
    d['bb_mid']=d.close.rolling(20,min_periods=20).mean(); d['bb_std']=d.close.rolling(20,min_periods=20).std(ddof=0)
    d['bb_upper']=d.bb_mid+2*d.bb_std; d['bb_lower']=d.bb_mid-2*d.bb_std
    for n in [20,50,100,200]: d[f'sma{n}']=d.close.rolling(n,min_periods=n).mean()
    delta=d.close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=gain/loss.replace(0,np.nan); d['rsi']=100-100/(1+rs); d.loc[(loss==0)&(gain>0),'rsi']=100; d.loc[(gain==0)&(loss>0),'rsi']=0
    pc=d.close.shift(); tr=pd.concat([d.high-d.low,(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1); d['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    up=d.high.diff(); down=-d.low.diff(); pdm=pd.Series(np.where((up>down)&(up>0),up,0),index=d.index); mdm=pd.Series(np.where((down>up)&(down>0),down,0),index=d.index)
    d['di_plus']=100*pdm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/d.atr.replace(0,np.nan); d['di_minus']=100*mdm.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/d.atr.replace(0,np.nan)
    d['dx']=100*(d.di_plus-d.di_minus).abs()/(d.di_plus+d.di_minus).replace(0,np.nan); d['adx']=d.dx.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    lo=d.low.rolling(14).min(); hi=d.high.rolling(14).max(); d['stoch_k']=100*(d.close-lo)/(hi-lo).replace(0,np.nan); d['stoch_d']=d.stoch_k.rolling(3).mean()
    typical=(d.high+d.low+d.close)/3; sess=pd.Series(pd.DatetimeIndex(d.index).date,index=d.index); pv=typical*d.volume
    d['vwap']=pv.groupby(sess).cumsum()/d.volume.groupby(sess).cumsum().replace(0,np.nan)
    d['vol_avg20']=d.volume.rolling(20,min_periods=5).mean(); d['vol_ratio']=d.volume/d.vol_avg20.replace(0,np.nan)
    d['prior_high20']=d.high.rolling(20,min_periods=10).max().shift(1); d['prior_low20']=d.low.rolling(20,min_periods=10).min().shift(1)
    # Simplified, vectorised candlestick confirmation features for scoring
    po=d.open.shift(1); pc=d.close.shift(1); body=(d.close-d.open).abs(); rng=(d.high-d.low).replace(0,np.nan)
    prev_body=(pc-po).abs()
    d['bull_pattern_score']=0
    d['bear_pattern_score']=0
    bull_eng=(d.close>d.open)&(pc<po)&(d.close>=po)&(d.open<=pc)
    bear_eng=(d.close<d.open)&(pc>po)&(d.open>=pc)&(d.close<=po)
    lower=d[['open','close']].min(axis=1)-d.low; upper=d.high-d[['open','close']].max(axis=1)
    hammer=(lower>=2*body.clip(lower=1e-9))&(upper<=body.clip(lower=1e-9)*1.2)&(d.close>d.open)
    shooting=(upper>=2*body.clip(lower=1e-9))&(lower<=body.clip(lower=1e-9)*1.2)&(d.close<d.open)
    doji=(body/rng<=.1)
    # Single-candle and two-candle pattern scores. These scores feed get_signal().
    bull_harami=(pc<po)&(d.close>d.open)&(d.open>=pc)&(d.close<=po)
    bear_harami=(pc>po)&(d.close<d.open)&(d.open<=pc)&(d.close>=po)
    midpoint_prev=(po+pc)/2
    piercing=(pc<po)&(d.close>d.open)&(d.open<pc)&(d.close>midpoint_prev)&(d.close<po)
    dark_cloud=(pc>po)&(d.close<d.open)&(d.open>pc)&(d.close<midpoint_prev)&(d.close>po)
    d['bull_pattern_score'] = (bull_eng.astype(int) + hammer.astype(int) + bull_harami.astype(int) + piercing.astype(int))
    d['bear_pattern_score'] = (bear_eng.astype(int) + shooting.astype(int) + bear_harami.astype(int) + dark_cloud.astype(int))
    # Three-candle reversal patterns; simplified rules for screening, not guarantees.
    po2=d.open.shift(2); pc2=d.close.shift(2); body2=(pc2-po2).abs()
    morning=(pc2<po2)&(prev_body<body2*.6)&(d.close>d.open)&(d.close>(po2+pc2)/2)
    evening=(pc2>po2)&(prev_body<body2*.6)&(d.close<d.open)&(d.close<(po2+pc2)/2)
    bull_soldiers=(d.close>d.open)&(pc>po)&(pc2>po2)&(d.close>pc)&(pc>pc2)
    bear_crows=(d.close<d.open)&(pc<po)&(pc2<po2)&(d.close<pc)&(pc<pc2)
    d.loc[morning|bull_soldiers,'bull_pattern_score'] += 1
    d.loc[evening|bear_crows,'bear_pattern_score'] += 1
    d.loc[doji,'bull_pattern_score']=0; d.loc[doji,'bear_pattern_score']=0
    return d
def patterns(d):
    if len(d)<3:return ['Insufficient candles']
    a,b,c=d.iloc[-1],d.iloc[-2],d.iloc[-3]
    body=abs(a.close-a.open); rng=max(a.high-a.low,1e-9)
    upper=a.high-max(a.open,a.close); lower=min(a.open,a.close)-a.low; out=[]
    # Two-candle patterns
    if a.close>a.open and b.close<b.open and a.close>=b.open and a.open<=b.close: out.append('Bullish Engulfing')
    if a.close<a.open and b.close>b.open and a.open>=b.close and a.close<=b.open: out.append('Bearish Engulfing')
    if b.close<b.open and a.close>a.open and a.open<=b.close and a.close>=(b.open+b.close)/2: out.append('Piercing Pattern')
    if b.close>b.open and a.close<a.open and a.open>=b.close and a.close<=(b.open+b.close)/2: out.append('Dark Cloud Cover')
    if b.close<b.open and a.close>a.open and a.open>=b.close and a.close<=b.open: out.append('Bullish Harami')
    if b.close>b.open and a.close<a.open and a.open<=b.close and a.close>=b.open: out.append('Bearish Harami')
    # Single-candle patterns
    if body/rng<=.1: out.append('Doji')
    if lower>=2*max(body,1e-9) and upper<=max(body,1e-9)*1.2: out.append('Hammer')
    if upper>=2*max(body,1e-9) and lower<=max(body,1e-9)*1.2: out.append('Shooting Star')
    # Three-candle patterns
    if c.close<c.open and abs(b.close-b.open)<abs(c.close-c.open)*.6 and a.close>a.open and a.close>(c.open+c.close)/2: out.append('Morning Star (possible)')
    if c.close>c.open and abs(b.close-b.open)<abs(c.close-c.open)*.6 and a.close<a.open and a.close<(c.open+c.close)/2: out.append('Evening Star (possible)')
    if c.close<c.open and b.close>b.open and a.close>a.open and b.close>c.close and a.close>b.close: out.append('Three White Soldiers (possible)')
    if c.close>c.open and b.close<b.open and a.close<a.open and b.close<c.close and a.close<b.close: out.append('Three Black Crows (possible)')
    if a.high<b.high and a.low>b.low: out.append('Inside Bar')
    if a.high>b.high and a.low<b.low: out.append('Outside Bar')
    return out or ['No major pattern detected']
def safe(v):
    try: return float(v) if pd.notna(v) and np.isfinite(float(v)) else np.nan
    except Exception:return np.nan
def fmt(v,n=2): return 'N/A' if not np.isfinite(safe(v)) else f'{safe(v):,.{n}f}'
def get_signal(r):
    bull=bear=0; reasons=[]
    if r.close>r.ema20: bull+=1; reasons.append('Price > EMA20')
    elif r.close<r.ema20: bear+=1; reasons.append('Price < EMA20')
    if r.ema20>r.ema50: bull+=1
    elif r.ema20<r.ema50: bear+=1
    if pd.notna(r.vwap):
        if r.close>r.vwap: bull+=1
        elif r.close<r.vwap: bear+=1
    if pd.notna(r.rsi):
        if 52<=r.rsi<=70: bull+=1
        elif 30<=r.rsi<=48: bear+=1
    if pd.notna(r.adx) and r.adx>=18:
        if r.di_plus>r.di_minus: bull+=1
        elif r.di_minus>r.di_plus: bear+=1
    if pd.notna(r.vol_ratio) and r.vol_ratio>=1.2:
        if r.close>r.open: bull+=1
        elif r.close<r.open: bear+=1
    if pd.notna(r.prior_high20) and r.close>r.prior_high20: bull+=2; reasons.append('20-candle breakout')
    if pd.notna(r.prior_low20) and r.close<r.prior_low20: bear+=2; reasons.append('20-candle breakdown')
    if 'bull_pattern_score' in r.index and r.bull_pattern_score>0: bull+=1; reasons.append('Bullish candle pattern')
    if 'bear_pattern_score' in r.index and r.bear_pattern_score>0: bear+=1; reasons.append('Bearish candle pattern')
    # EMA, MACD and Bollinger confirmation all contribute to the combined signal.
    if pd.notna(r.get('macd',np.nan)) and pd.notna(r.get('macd_signal',np.nan)):
        if r.macd>r.macd_signal and r.macd_hist>0: bull+=1; reasons.append('MACD bullish')
        elif r.macd<r.macd_signal and r.macd_hist<0: bear+=1; reasons.append('MACD bearish')
    if pd.notna(r.get('bb_mid',np.nan)) and pd.notna(r.get('bb_upper',np.nan)) and pd.notna(r.get('bb_lower',np.nan)):
        if r.close>r.bb_mid and r.close<r.bb_upper: bull+=1; reasons.append('Bollinger midline bullish')
        elif r.close<r.bb_mid and r.close>r.bb_lower: bear+=1; reasons.append('Bollinger midline bearish')
        elif r.close>=r.bb_upper and r.close>r.open: bull+=1; reasons.append('Upper-band momentum')
        elif r.close<=r.bb_lower and r.close<r.open: bear+=1; reasons.append('Lower-band momentum')
    sig='BUY' if bull>=4 and bull>=bear+2 else 'SELL' if bear>=4 and bear>=bull+2 else 'WAIT'
    return sig,bull,bear,reasons
def pct(s):
    if len(s)<2:return np.nan
    p=safe(s.iloc[-2]); q=safe(s.iloc[-1]); return (q/p-1)*100 if np.isfinite(p) and p else np.nan
def daily_info(ticker):
    d=yahoo(ticker,'2y','1d')
    if d.empty:return d,{}
    d=indicators(d); prev=d.iloc[-2] if len(d)>1 else d.iloc[-1]; last=d.iloc[-1]
    lev={'Previous Close':safe(prev.close),'Previous High':safe(prev.high),'Previous Low':safe(prev.low)}
    for n in [20,50,100,200]:lev[f'{n} DMA']=safe(last[f'sma{n}'])
    return d,lev
def mtf(ticker):
    out=[]; cache={}
    for label,iv in [('1m','1m'),('3m','1m'),('5m','5m'),('15m','15m')]:
        if iv not in cache:cache[iv]=yahoo(ticker,'5d',iv)
        x=cache[iv].copy()
        if label=='3m' and not x.empty:x=resample(x,'3min')
        if len(x)<30:
            out.append({'Timeframe':label,'Trend':'N/A','Signal':'N/A','RSI':np.nan,'ADX':np.nan,'Last candle':'N/A'}); continue
        z=indicators(x); r=z.iloc[-1]; sig,_,_,_=get_signal(r)
        out.append({'Timeframe':label,'Trend':'Bullish' if r.ema20>r.ema50 else 'Bearish' if r.ema20<r.ema50 else 'Sideways','Signal':sig,'RSI':safe(r.rsi),'ADX':safe(r.adx),'Last candle':str(z.index[-1])})
    return pd.DataFrame(out)
def backtest(x,rr,atrmult,cost_bps):
    d=indicators(x); trades=[]; pos=None; fee=cost_bps/10000
    if len(d)<60:return pd.DataFrame(),{'Total Trades':0,'Wins':0,'Losses':0,'Win Rate %':0,'Net Return %':0,'Profit Factor':np.nan,'Max Drawdown %':0}
    for i in range(55,len(d)-1):
        r=d.iloc[i]; nxt=d.iloc[i+1]; sig,_,_,_=get_signal(r)
        if pos:
            stop_hit=(r.low<=pos['stop']) if pos['side']=='LONG' else (r.high>=pos['stop'])
            target_hit=(r.high>=pos['target']) if pos['side']=='LONG' else (r.low<=pos['target'])
            exitp=reason=None
            if stop_hit:exitp=pos['stop'];reason='Stop Loss'
            elif target_hit:exitp=pos['target'];reason='Target'
            elif (pos['side']=='LONG' and sig=='SELL') or (pos['side']=='SHORT' and sig=='BUY'):exitp=r.close;reason='Opposite signal'
            else:
                av=safe(r.atr)
                if np.isfinite(av) and av>0:
                    pos['stop']=max(pos['stop'],r.close-atrmult*av) if pos['side']=='LONG' else min(pos['stop'],r.close+atrmult*av)
            if exitp is not None:
                gross=(exitp-pos['entry'])/pos['entry'] if pos['side']=='LONG' else (pos['entry']-exitp)/pos['entry']; net=(gross-2*fee)*100
                trades.append({'Entry Time':pos['time'],'Exit Time':str(d.index[i]),'Side':pos['side'],'Entry':pos['entry'],'Exit':exitp,'Net Return %':net,'Result':'Win' if net>0 else 'Loss','Exit Reason':reason});pos=None
        if pos is None and sig in ('BUY','SELL'):
            av=safe(r.atr); ep=safe(nxt.open)
            if not np.isfinite(av) or av<=0 or not np.isfinite(ep) or ep<=0:continue
            side='LONG' if sig=='BUY' else 'SHORT'; risk=av*atrmult
            pos={'side':side,'entry':ep,'stop':ep-risk if side=='LONG' else ep+risk,'target':ep+risk*rr if side=='LONG' else ep-risk*rr,'time':str(d.index[i+1])}
    t=pd.DataFrame(trades)
    if t.empty:return t,{'Total Trades':0,'Wins':0,'Losses':0,'Win Rate %':0,'Net Return %':0,'Profit Factor':np.nan,'Max Drawdown %':0}
    wins=int((t['Net Return %']>0).sum()); losses=len(t)-wins; gp=t.loc[t['Net Return %']>0,'Net Return %'].sum(); gl=abs(t.loc[t['Net Return %']<=0,'Net Return %'].sum()); eq=(1+t['Net Return %']/100).cumprod(); dd=(eq/eq.cummax()-1).min()*100
    return t,{'Total Trades':len(t),'Wins':wins,'Losses':losses,'Win Rate %':wins/len(t)*100,'Net Return %':(eq.iloc[-1]-1)*100,'Profit Factor':gp/gl if gl else np.inf,'Max Drawdown %':abs(dd)}

# Sidebar controls
st.sidebar.header('Market Controls')
market=st.sidebar.selectbox('Instrument',list(SYMBOLS)+['Custom Yahoo ticker'])
ticker=st.sidebar.text_input('Yahoo ticker',value='RELIANCE.NS') if market=='Custom Yahoo ticker' else SYMBOLS[market]
chart_tf=st.sidebar.selectbox('Chart timeframe',['1 minute','3 minutes','5 minutes','15 minutes','30 minutes','60 minutes','1 day'],index=2)
period_label=st.sidebar.selectbox('History',['1 day','5 days','1 month','3 months','6 months','1 year','2 years'],index=1)
periods={'1 day':'1d','5 days':'5d','1 month':'1mo','3 months':'3mo','6 months':'6mo','1 year':'1y','2 years':'2y'}
period=periods[period_label]; intervals={'1 minute':'1m','3 minutes':'1m','5 minutes':'5m','15 minutes':'15m','30 minutes':'30m','60 minutes':'60m','1 day':'1d'}; interval=intervals[chart_tf]
if chart_tf in ('1 minute','3 minutes') and period not in ('1d','5d'): period='5d'
data_source=st.sidebar.radio('Candle data source',['Yahoo Finance','Angel One API'],index=0)
angel_interval={'1 minute':'ONE_MINUTE','3 minutes':'THREE_MINUTE','5 minutes':'FIVE_MINUTE','15 minutes':'FIFTEEN_MINUTE','30 minutes':'THIRTY_MINUTE','60 minutes':'ONE_HOUR','1 day':'ONE_DAY'}[chart_tf]
angel_search = ''
lookback_days = st.sidebar.selectbox('Angel One history days',[1,3,5,10,20,30],index=2) if data_source=='Angel One API' else 5
if data_source=='Angel One API':
    st.sidebar.success('Symbol token and exchange will be looked up automatically from Angel One instrument master.')
    if market == 'Custom Yahoo ticker':
        angel_search = st.sidebar.text_input('Angel One instrument search (symbol/name)', value='RELIANCE')
    st.sidebar.warning('Only enter your own Angel One API credentials. Do not share API key, Client Code, PIN or TOTP secret. Credentials remain in this app session.')
    with st.sidebar.form('angel_connect_form'):
        akey=st.text_input('Angel One API Key',type='password')
        client=st.text_input('Client Code')
        apin=st.text_input('Trading PIN',type='password')
        totp_secret=st.text_input('TOTP Secret',type='password')
        connect_angel=st.form_submit_button('Connect Angel One')
    if connect_angel:
        try:
            import pyotp
            from SmartApi import SmartConnect
            if not all([akey.strip(), client.strip(), apin.strip(), totp_secret.strip()]):
                raise RuntimeError('Fill API Key, Client Code, Trading PIN and TOTP Secret.')
            api=SmartConnect(api_key=akey.strip())
            response=api.generateSession(client.strip(), apin.strip(), pyotp.TOTP(totp_secret.strip()).now())
            if not response or response.get('status') is False:
                raise RuntimeError((response or {}).get('message') or 'Login failed. Check credentials and TOTP.')
            st.session_state.angel_api=api
            st.session_state.angel_connected=True
            st.session_state.angel_client_code=client.strip()
            st.session_state.angel_candles=None
            st.session_state.angel_candle_key=None
            st.sidebar.success('Angel One connected. Resolving symbol and token automatically…')
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f'Angel One connection failed: {exc}')
    if st.sidebar.button('Refresh Angel One instrument master'):
        load_angel_master.clear()
        st.rerun()
auto=st.sidebar.toggle('Auto refresh ON/OFF',value=False); secs=st.sidebar.selectbox('Refresh interval (seconds)',[5,10,15,30,60],index=2)
if auto:
    if st_autorefresh:st_autorefresh(interval=secs*1000,key='refresh')
    else:st.sidebar.error('Install streamlit-autorefresh to enable automatic refresh.')
if st.sidebar.button('Refresh now',type='primary'):
    st.cache_data.clear()
    st.session_state.force_angel_refresh=True
    st.rerun()
rr=st.sidebar.selectbox('Target risk : reward',[1.0,1.5,2.0,3.0],index=2); atr_mult=st.sidebar.slider('ATR stop multiplier',0.5,4.0,1.5,0.25); risk_pct=st.sidebar.slider('Paper risk per trade (%)',0.25,2.0,1.0,0.25); capital=st.sidebar.number_input('Paper capital (₹)',min_value=1000.0,value=100000.0,step=5000.0)
st.sidebar.divider()
st.sidebar.subheader('Manual context data (if unavailable from feed)')
pcr=st.sidebar.number_input('PCR',0.0,10.0,1.0,.05)
pcr_delta=st.sidebar.number_input('PCR change',value=0.0,step=.01)
pcr_bias=st.sidebar.selectbox('PCR interpretation',['Not assessed','Bullish','Bearish','Mixed'])
call_oi=st.sidebar.number_input('Call OI change (%)',value=0.0,step=1.0)
put_oi=st.sidebar.number_input('Put OI change (%)',value=0.0,step=1.0)
fii=st.sidebar.number_input('FII/FPI net (₹ crore)',value=0.0,step=100.0)
dii=st.sidebar.number_input('DII net (₹ crore)',value=0.0,step=100.0)
oi_bias=st.sidebar.selectbox('OI interpretation',['Not assessed','Bullish','Bearish','Mixed'])
news=st.sidebar.selectbox('Verified news bias',['Neutral/unknown','Positive','Negative','Mixed'])
event=st.sidebar.selectbox('Government/geopolitical event risk',['Unknown/not checked','Low','Moderate','High'])

# Main data fetch
if data_source=='Yahoo Finance':
    with st.spinner('Fetching Yahoo Finance data...'):
        raw=yahoo(ticker,period,interval); data=resample(raw,'3min') if chart_tf=='3 minutes' else raw
else:
    data=st.session_state.get('angel_candles')
    if not st.session_state.get('angel_connected') or st.session_state.get('angel_api') is None:
        st.info('Angel One selected. Enter your Angel One API Key, Client Code, Trading PIN and TOTP Secret in the sidebar, then press Connect Angel One. Symbol and token are automatic; you do not need to enter a token.')
        st.stop()
    try:
        master=load_angel_master()
        instrument=resolve_angel_instrument(master, market, angel_search)
        st.session_state.angel_resolved_instrument=instrument
        st.sidebar.caption(f"Resolved: {instrument['symbol']} • {instrument['exchange']} • token auto-loaded")
        candle_key=(instrument['exchange'], instrument['symbol'], str(instrument['token']), angel_interval, int(lookback_days))
        now_ts=time.time()
        old_key=st.session_state.get('angel_candle_key')
        last_fetch=st.session_state.get('angel_last_fetch_ts',0)
        force_refresh=st.session_state.pop('force_angel_refresh', False)
        should_fetch=(data is None or data.empty or old_key != candle_key or force_refresh or (auto and now_ts-last_fetch>=secs))
        if should_fetch:
            with st.spinner(f"Fetching Angel One candles for {instrument['symbol']}…"):
                data=fetch_angel_candles(st.session_state.angel_api, instrument, angel_interval, lookback_days)
                st.session_state.angel_candles=data
                st.session_state.angel_candle_key=candle_key
                st.session_state.angel_last_fetch_ts=now_ts
        else:
            data=st.session_state.angel_candles
        ticker=instrument['symbol']
    except Exception as exc:
        st.error(f'Angel One data error: {exc}')
        st.stop()
if data.empty:
    st.error(f'No data returned for {ticker}. Verify ticker/network/interval or broker symbol token.');st.stop()
data=indicators(data); last=data.iloc[-1]; price=safe(last.close)
if not np.isfinite(price):st.error('Latest price unavailable.');st.stop()
daily,levels=daily_info(ticker); pats=patterns(data); signal,bull,bear,reasons=get_signal(last)
# Current signal only: apply manually supplied options/institutional/news context.
# Historical backtest intentionally does not reuse today's manual context for past candles.
if pcr_bias=='Bullish': bull+=1; reasons.append('Manual PCR context: bullish')
elif pcr_bias=='Bearish': bear+=1; reasons.append('Manual PCR context: bearish')
if oi_bias=='Bullish': bull+=1; reasons.append('Manual OI context: bullish')
elif oi_bias=='Bearish': bear+=1; reasons.append('Manual OI context: bearish')
if news=='Positive': bull+=1; reasons.append('Manual news context: positive')
elif news=='Negative': bear+=1; reasons.append('Manual news context: negative')
if event=='High':
    signal='WAIT'; reasons.append('High event risk: confirmation withheld')
else:
    signal='BUY' if bull>=4 and bull>=bear+2 else 'SELL' if bear>=4 and bear>=bull+2 else 'WAIT'
atr=safe(last.atr)
if not np.isfinite(atr) or atr<=0:atr=price*.005
entry=price; stop=price-atr_mult*atr if signal=='BUY' else price+atr_mult*atr if signal=='SELL' else np.nan; target=price+atr_mult*atr*rr if signal=='BUY' else price-atr_mult*atr*rr if signal=='SELL' else np.nan

st.subheader('1. Current Signal')
c=st.columns(5); c[0].metric('Instrument',market);c[1].metric('Last Price',f'{price:,.2f}');c[2].metric('Signal',signal);c[3].metric('Bullish Score',bull);c[4].metric('Bearish Score',bear)
st.caption(f'Source: {data_source} • Last candle: {data.index[-1]} • Score is rule alignment, not a calibrated winning probability.')
st.write('Signal notes: '+' • '.join(reasons) if reasons else 'Signal notes: mixed/insufficient confirmation.')
trend_now='Bullish' if last.ema20>last.ema50 else 'Bearish' if last.ema20<last.ema50 else 'Sideways'
ctx=st.columns(6);ctx[0].metric('Trend',trend_now);ctx[1].metric('Volume ratio',fmt(last.vol_ratio));ctx[2].metric('PCR (manual)',f'{pcr:.2f}');ctx[3].metric('Call OI change',f'{call_oi:+.1f}%');ctx[4].metric('Put OI change',f'{put_oi:+.1f}%');ctx[5].metric('ADX',fmt(last.adx))

st.subheader('2. Entry, Exit, Target, Stop Loss & Trailing Stop')
c=st.columns(5);c[0].metric('Entry',fmt(entry) if signal!='WAIT' else 'WAIT');c[1].metric('Stop Loss',fmt(stop));c[2].metric('Target',fmt(target));c[3].metric('ATR',fmt(atr));c[4].metric('Risk : Reward',f'1 : {rr:g}' if signal!='WAIT' else 'N/A')
trail=price-atr_mult*atr if signal=='BUY' else price+atr_mult*atr if signal=='SELL' else np.nan;st.metric('Indicative Trailing Stop',fmt(trail));st.caption('Trailing stop is an indicative calculation; it is not sent to any broker.')

st.subheader('3. Price Chart & Indicators')
st.caption('EMA, MACD and Bollinger Bands are included in the combined BUY/SELL/WAIT scoring, along with price action, RSI, ADX, VWAP, volume and candle patterns.')
choices=st.multiselect('Overlay indicators',['EMA20','EMA50','VWAP','Bollinger Upper','Bollinger Mid','Bollinger Lower','SMA20','SMA50','SMA100','SMA200'],default=['EMA20','EMA50','Bollinger Upper','Bollinger Mid','Bollinger Lower','VWAP'])
cmap={'EMA20':'ema20','EMA50':'ema50','VWAP':'vwap','Bollinger Upper':'bb_upper','Bollinger Mid':'bb_mid','Bollinger Lower':'bb_lower','SMA20':'sma20','SMA50':'sma50','SMA100':'sma100','SMA200':'sma200'}
st.line_chart(data[['close']+[cmap[x] for x in choices]].tail(300),use_container_width=True)
st.markdown('**MACD (12, 26, 9)**')
st.line_chart(data[['macd','macd_signal']].tail(300),use_container_width=True)
st.bar_chart(data[['macd_hist']].tail(100),use_container_width=True)
c=st.columns(8);c[0].metric('RSI',fmt(last.rsi));c[1].metric('ADX',fmt(last.adx));c[2].metric('DI+',fmt(last.di_plus));c[3].metric('DI−',fmt(last.di_minus));c[4].metric('Stochastic %K',fmt(last.stoch_k));c[5].metric('Volume / Avg',fmt(last.vol_ratio));c[6].metric('MACD',fmt(last.macd));c[7].metric('MACD Signal',fmt(last.macd_signal))
bbcols=st.columns(3);bbcols[0].metric('Bollinger Upper',fmt(last.bb_upper));bbcols[1].metric('Bollinger Mid',fmt(last.bb_mid));bbcols[2].metric('Bollinger Lower',fmt(last.bb_lower))

st.subheader('4. Candlestick Patterns')
st.write('Latest detected: '+' • '.join(pats));st.caption('Pattern rules are simplified. Confirm with context; a candle pattern alone is not a trade recommendation.')

st.subheader('5. Multi-Timeframe Analysis (1m / 3m / 5m / 15m)')
with st.spinner('Loading MTF data...'): st.dataframe(mtf(ticker),use_container_width=True,hide_index=True)
st.caption('Yahoo may restrict intraday history. 3-minute bars are resampled from 1-minute data.')

st.subheader('6. Previous Close, Support / Resistance & DMA')
if levels:
    rows=[]
    for k,v in levels.items():rows.append({'Level':k,'Price':v,'Type':'Support candidate' if np.isfinite(v) and v<price else 'Resistance candidate' if np.isfinite(v) and v>price else 'Near price / N/A'})
    if len(daily)>1:
        p=(daily.high.iloc[-2]+daily.low.iloc[-2]+daily.close.iloc[-2])/3;rows += [{'Level':'Classic Pivot','Price':p,'Type':'Pivot'},{'Level':'Pivot R1','Price':2*p-daily.low.iloc[-2],'Type':'Resistance candidate'},{'Level':'Pivot S1','Price':2*p-daily.high.iloc[-2],'Type':'Support candidate'}]
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    adr=(daily.high-daily.low).tail(14).mean() if len(daily)>1 else np.nan; adr_pct=adr/daily.close.iloc[-1]*100 if len(daily)>1 and daily.close.iloc[-1] else np.nan
    c=st.columns(3);c[0].metric('ADR (14-day average range)',fmt(adr));c[1].metric('ADR %',fmt(adr_pct)+'%');c[2].metric('Previous Close',fmt(levels.get('Previous Close')))
else:st.info('Daily levels unavailable.')

st.subheader('7. PCR, OI Change, FII / DII and Event Context')
st.info('Yahoo Finance does not reliably provide live NSE index PCR/OI changes or official FII/DII flows. Sidebar values are manually entered context, not verified live data.')
c=st.columns(6)
c[0].metric('PCR (manual)',f'{pcr:.2f}')
c[1].metric('PCR change',f'{pcr_delta:+.2f}')
c[2].metric('Call OI change',f'{call_oi:+.1f}%')
c[3].metric('Put OI change',f'{put_oi:+.1f}%')
c[4].metric('FII/FPI net',f'₹{fii:,.0f} cr')
c[5].metric('DII net',f'₹{dii:,.0f} cr')
st.write(f'PCR interpretation: {pcr_bias} • OI interpretation: {oi_bias} • Verified news bias: {news} • Government/geopolitical event risk: {event}')

st.subheader('8. Indian Major Stocks & Global Markets — Horizontal Performance Board')
watch_groups={
 'INDIAN INDICES':{'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','NIFTY IT':'^CNXIT','NIFTY AUTO':'^CNXAUTO'},
 'BANKING':{'HDFC Bank':'HDFCBANK.NS','ICICI Bank':'ICICIBANK.NS','SBI':'SBIN.NS','Axis Bank':'AXISBANK.NS','Kotak Bank':'KOTAKBANK.NS'},
 'IT / TECHNOLOGY':{'TCS':'TCS.NS','Infosys':'INFY.NS','HCLTech':'HCLTECH.NS','Wipro':'WIPRO.NS','Tech Mahindra':'TECHM.NS'},
 'AUTOMOBILE':{'Maruti':'MARUTI.NS','M&M':'M&M.NS','Tata Motors':'TMPV.NS','Bajaj Auto':'BAJAJ-AUTO.NS','Eicher Motors':'EICHERMOT.NS'},
 'OTHER MAJOR STOCKS':{'Reliance':'RELIANCE.NS','L&T':'LT.NS','Bharti Airtel':'BHARTIARTL.NS','ITC':'ITC.NS'}
}
def snapshot(mapping):
    out=[]
    for name,tick in mapping.items():
        q=yahoo(tick,'5d','1d')
        last_close=safe(q.close.iloc[-1]) if not q.empty else np.nan
        change=pct(q.close) if not q.empty else np.nan
        out.append({'Instrument':name,'Ticker':tick,'Last Price':last_close,'Day Change %':change,'Performance':'▲ Positive' if np.isfinite(change) and change>0 else '▼ Negative' if np.isfinite(change) and change<0 else '— Flat / N/A' if np.isfinite(change) else 'Unavailable','Source':'Yahoo Finance' if not q.empty else 'Unavailable'})
    return pd.DataFrame(out)
def horizontal_board(title,mapping):
    st.markdown(f'#### {title}')
    snap=snapshot(mapping)
    if snap.empty:
        st.info('No data available for this group.')
        return snap
    # Each instrument gets a horizontal card with last price and daily performance.
    for start in range(0,len(snap),4):
        row=snap.iloc[start:start+4]
        cols=st.columns(4)
        for col,(_,item) in zip(cols,row.iterrows()):
            change=item['Day Change %']
            change_text=f"{change:+.2f}%" if np.isfinite(change) else 'N/A'
            with col:
                st.metric(str(item['Instrument']),fmt(item['Last Price']),change_text)
    st.dataframe(snap,use_container_width=True,hide_index=True)
    return snap
with st.spinner('Loading Indian stocks and global markets...'):
    all_india=[]
    for group_name,group_map in watch_groups.items():
        result=horizontal_board(group_name,group_map)
        if not result.empty: all_india.append(result.assign(Sector=group_name))
    st.markdown('#### GLOBAL MARKETS')
    global_groups={
      'US MARKETS':{'S&P 500':'^GSPC','Nasdaq':'^IXIC','Dow Jones':'^DJI'},
      'ASIA MARKETS':{'Japan Nikkei':'^N225','Hong Kong Hang Seng':'^HSI','China Shanghai':'000001.SS','South Korea KOSPI':'^KS11'},
      'MACRO / VOLATILITY':{'Brent Crude':'BZ=F','USD/INR':'INR=X','India VIX':'^INDIAVIX'}
    }
    for group_name,group_map in global_groups.items(): horizontal_board(group_name,group_map)
st.caption('Daily performance is based on the latest two available daily closes, not necessarily a live tick. Closed markets and Yahoo data delays may affect timestamps. The combined signal uses the selected instrument indicators; this market board is contextual and does not silently force a trade.')

st.subheader('9. Historical Backtest')
st.caption('Simplified rule-based OHLC backtest: closed-bar signal, next-bar-open entry, ATR stop/target, one position at a time. Costs are an estimate; taxes, latency and exact fills are not fully modelled.')
cost=st.slider('Estimated one-way cost + slippage (basis points)',0.0,50.0,5.0,1.0)
if st.button('Run Backtest',type='primary'):
    trades,stats=backtest(data,float(rr),float(atr_mult),float(cost));st.session_state.bt_trades=trades;st.session_state.bt_stats=stats
if 'bt_stats' in st.session_state:
    q=st.session_state.bt_stats;c=st.columns(6);c[0].metric('Total Trades',q['Total Trades']);c[1].metric('Wins',q['Wins']);c[2].metric('Losses',q['Losses']);c[3].metric('Win Rate',f"{q['Win Rate %']:.2f}%");c[4].metric('Net Return',f"{q['Net Return %']:.2f}%");pf=q['Profit Factor'];c[5].metric('Profit Factor','∞' if pf==np.inf else fmt(pf));st.metric('Maximum Drawdown',f"{q['Max Drawdown %']:.2f}%")
    trades=st.session_state.bt_trades
    if not trades.empty:
        st.dataframe(trades.tail(200),use_container_width=True,hide_index=True);st.download_button('Download backtest CSV',trades.to_csv(index=False),'backtest_trades.csv','text/csv')
    else:st.info('No completed trades for this period/settings. Try a longer period or different ticker.')

st.subheader('10. Paper Trading & P/L')
st.caption('Paper trades only. No real orders are placed.')
if st.toggle('Show paper trading panel',value=True):
    pos=st.session_state.paper_position
    if pos is None:
        with st.form('paper_open'):
            side=st.selectbox('Direction',['LONG','SHORT']); pe=st.number_input('Entry price',min_value=.01,value=float(price)); qty=st.number_input('Quantity',min_value=1,value=1,step=1)
            default_sl=float(max(.01,price-atr_mult*atr)) if side=='LONG' else float(price+atr_mult*atr)
            default_tg=float(price+atr_mult*atr*rr) if side=='LONG' else float(max(.01,price-atr_mult*atr*rr))
            sl=st.number_input('Stop loss',min_value=.01,value=default_sl);tg=st.number_input('Target',min_value=.01,value=default_tg);openp=st.form_submit_button('Open Paper Position')
        if openp:
            valid=(sl<pe<tg) if side=='LONG' else (tg<pe<sl)
            if not valid:st.error('Stop/entry/target levels do not match selected direction.')
            else:
                cashrisk=abs(pe-sl)*qty
                if cashrisk>capital*risk_pct/100:st.warning(f'Paper risk ₹{cashrisk:,.2f} exceeds configured budget ₹{capital*risk_pct/100:,.2f}.')
                st.session_state.paper_position={'Symbol':ticker,'Side':side,'Entry':float(pe),'Quantity':int(qty),'Stop Loss':float(sl),'Target':float(tg),'Opened At':datetime.now().strftime('%Y-%m-%d %H:%M:%S')};st.rerun()
    else:
        side=pos['Side'];qty=pos['Quantity'];pnl=(price-pos['Entry'])*qty if side=='LONG' else (pos['Entry']-price)*qty;trail=price-atr_mult*atr if side=='LONG' else price+atr_mult*atr
        c=st.columns(4);c[0].metric('Open position',f"{side} {qty} {pos['Symbol']}");c[1].metric('Entry',fmt(pos['Entry']));c[2].metric('Unrealised P&L',f'₹{pnl:,.2f}');c[3].metric('Indicative trailing SL',fmt(trail));st.write(f"Initial/current paper SL: {pos['Stop Loss']:.2f} • Target: {pos['Target']:.2f}")
        if (side=='LONG' and price<=pos['Stop Loss']) or (side=='SHORT' and price>=pos['Stop Loss']):st.warning('Price crossed paper stop-loss; close/adjust manually.')
        if (side=='LONG' and price>=pos['Target']) or (side=='SHORT' and price<=pos['Target']):st.success('Price reached paper target; close/adjust manually.')
        c1,c2=st.columns(2)
        if c1.button('Close Paper Position'):
            st.session_state.paper_trades.append({'Symbol':ticker,'Side':side,'Entry':pos['Entry'],'Exit':price,'Quantity':qty,'P&L ₹':pnl,'Opened At':pos['Opened At'],'Closed At':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'Result':'Win' if pnl>0 else 'Loss' if pnl<0 else 'Breakeven'});st.session_state.paper_position=None;st.rerun()
        if c2.button('Move stop to trailing level'):
            improve=trail>pos['Stop Loss'] if side=='LONG' else trail<pos['Stop Loss']
            safe_side=(trail<price) if side=='LONG' else (trail>price)
            if improve and safe_side:pos['Stop Loss']=float(trail);st.session_state.paper_position=pos;st.rerun()
            else:st.info('Trailing stop does not improve current stop.')
    if st.session_state.paper_trades:
        pt=pd.DataFrame(st.session_state.paper_trades);wins=int((pt['P&L ₹']>0).sum());losses=int((pt['P&L ₹']<0).sum());net=pt['P&L ₹'].sum();c=st.columns(4);c[0].metric('Closed trades',len(pt));c[1].metric('Wins',wins);c[2].metric('Losses',losses);c[3].metric('Realised P&L',f'₹{net:,.2f}');st.dataframe(pt,use_container_width=True,hide_index=True);st.download_button('Download paper trades CSV',pt.to_csv(index=False),'paper_trades.csv','text/csv')

st.subheader('11. Combined Indicator Confirmation')
macd_state='Bullish' if safe(last.macd)>safe(last.macd_signal) else 'Bearish' if safe(last.macd)<safe(last.macd_signal) else 'Neutral'
bb_state='Above midline' if safe(last.close)>safe(last.bb_mid) else 'Below midline' if safe(last.close)<safe(last.bb_mid) else 'At midline'
ema_state='Bullish' if safe(last.ema20)>safe(last.ema50) else 'Bearish' if safe(last.ema20)<safe(last.ema50) else 'Neutral'
cc=st.columns(4);cc[0].metric('EMA20 / EMA50',ema_state);cc[1].metric('MACD Direction',macd_state);cc[2].metric('Bollinger Position',bb_state);cc[3].metric('Final Combined Signal',signal)
st.caption('Final signal is a rule-based confluence of technical indicators plus the manually entered PCR/OI/news context. A BUY/SELL signal is not a guarantee; validate on historical data and paper trading.')

st.subheader('12. Current Data / Signal Caveats')
st.write('• Angel One mode automatically downloads the instrument master and resolves the selected instrument to its exchange and symbol token. You still need to authenticate with your own API Key, Client Code, Trading PIN and TOTP Secret. The global/sector performance board and MTF table currently use Yahoo Finance; PCR/OI, FII/DII, verified news, government announcements and geopolitical events remain manual context fields.')
st.write('• “Win chance” is not inferred from score. The backtest win rate is historical for the selected ticker, period and rules and may not predict future results.')
st.write('• Yahoo ticker coverage varies; some index symbols or intraday intervals may be unavailable. Always verify latest timestamps and use paper trading first.')
st.caption('For public deployment, change the default login and use secure secrets. This app is not financial advice.')
