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
ANGEL_PCR_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/marketData/v1/putCallRatio"

# PCR is fetched from the authenticated Angel One SmartAPI (not scraped from NSE).
PCR_ALIASES = {
    "NIFTY 50": ("NIFTY",), "BANK NIFTY": ("BANKNIFTY", "NIFTYBANK"),
    "NIFTY IT": ("CNXIT", "NIFTYIT"), "NIFTY AUTO": ("CNXAUTO", "NIFTYAUTO"),
    "RELIANCE": ("RELIANCE",), "HDFC BANK": ("HDFCBANK",), "ICICI BANK": ("ICICIBANK",),
    "SBI": ("SBIN",), "AXIS BANK": ("AXISBANK",), "KOTAK BANK": ("KOTAKBANK",),
    "TCS": ("TCS",), "INFOSYS": ("INFY", "INFOSYS"), "HCLTECH": ("HCLTECH",),
    "WIPRO": ("WIPRO",), "TECH MAHINDRA": ("TECHM",), "MARUTI": ("MARUTI",),
    "M&M": ("M&M", "MM"), "TATA MOTORS": ("TATAMOTORS", "TMPV"),
    "BAJAJ AUTO": ("BAJAJ-AUTO", "BAJAJAUTO"), "EICHER MOTORS": ("EICHERMOT",),
}

@st.cache_data(ttl=60, show_spinner=False)
def fetch_angel_pcr_snapshot(jwt_token, api_key, client_code, market_name):
    """Fetch cumulative options PCR from Angel One's authenticated SmartAPI."""
    if not jwt_token or not api_key:
        return {"ok": False, "error": "Connect Angel One to load automatic PCR."}
    try:
        public_ip = "127.0.0.1"
        try:
            public_ip = requests.get("https://api.ipify.org", timeout=3).text.strip() or public_ip
        except Exception:
            pass
        headers = {
            "Authorization": "Bearer " + str(jwt_token).replace("Bearer ", ""),
            "Content-Type": "application/json", "Accept": "application/json",
            "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": public_ip,
            "X-MACAddress": "00:00:00:00:00:00", "X-PrivateKey": str(api_key),
        }
        response = requests.get(ANGEL_PCR_URL, headers=headers, timeout=12)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("status"):
            return {"ok": False, "error": str(payload.get("message") or payload.get("errorcode") or "Angel One PCR request failed")}
        rows = payload.get("data") or []
        aliases = PCR_ALIASES.get(market_name)
        if market_name == "Custom Yahoo ticker":
            aliases = ()
        if not aliases:
            return {"ok": False, "error": "Automatic PCR mapping is not available for this instrument."}
        matched = []
        for row in rows:
            tsym = str(row.get("tradingSymbol", "")).upper()
            # PCR API maps option PCR to the underlying futures trading symbol.
            if any(tsym.startswith(a.upper()) for a in aliases):
                try:
                    val = float(row.get("pcr"))
                    if np.isfinite(val) and val > 0:
                        matched.append({"pcr": val, "symbol": tsym})
                except Exception:
                    pass
        if not matched:
            return {"ok": False, "error": "Angel One returned no PCR row for the selected underlying."}
        # Prefer the first exact underlying match from the API's response list.
        return {"ok": True, **matched[0], "source": "Angel One SmartAPI", "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as exc:
        return {"ok": False, "error": f"Angel One PCR feed unavailable: {exc}"}


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
def adaptive_atr_multiplier(price_value, atr_value, adx_value=np.nan):
    """Return a volatility-adjusted ATR stop multiplier; this is a rule, not a guarantee."""
    pv=safe(price_value); av=safe(atr_value); ax=safe(adx_value)
    if not np.isfinite(pv) or pv<=0 or not np.isfinite(av) or av<=0:
        return 1.5
    atr_pct=av/pv*100
    if atr_pct < 0.35: mult=1.20
    elif atr_pct < 0.75: mult=1.50
    elif atr_pct < 1.25: mult=1.80
    elif atr_pct < 2.00: mult=2.20
    else: mult=2.60
    if np.isfinite(ax) and ax>=30: mult+=0.20
    elif np.isfinite(ax) and ax<15: mult-=0.10
    return float(np.clip(mult,1.0,3.0))

@st.cache_data(ttl=300, show_spinner=False)
def global_context_changes():
    """Best-effort daily changes; missing/failed quotes stay unavailable."""
    mapping={"S&P 500":"^GSPC", "Nasdaq":"^IXIC", "Brent":"BZ=F", "USD/INR":"INR=X", "India VIX":"^INDIAVIX"}
    out={}
    for label,tick in mapping.items():
        q=yahoo(tick,"5d","1d")
        out[label]=pct(q.close) if not q.empty else np.nan
    return out

def combined_confidence(last_row, bull_score, bear_score, pcr_value, fii_value, dii_value, news_bias, event_risk, pattern_list):
    """Directional confluence score (not a probability of profit). Missing feeds lower coverage."""
    factors=[]
    # Technical direction from the same indicators used by the signal engine.
    tech_dir=1 if bull_score>bear_score else -1 if bear_score>bull_score else 0
    factors.append(("Technical score",tech_dir,2.0,True))
    factors.append(("EMA trend",1 if last_row.ema20>last_row.ema50 else -1 if last_row.ema20<last_row.ema50 else 0,1.0,pd.notna(last_row.ema20) and pd.notna(last_row.ema50)))
    factors.append(("MACD",1 if last_row.macd>last_row.macd_signal else -1 if last_row.macd<last_row.macd_signal else 0,1.0,pd.notna(last_row.macd) and pd.notna(last_row.macd_signal)))
    rsi=safe(last_row.rsi); factors.append(("RSI",1 if 52<=rsi<=70 else -1 if 30<=rsi<=48 else 0,.7,np.isfinite(rsi)))
    pat_bull=any("Bullish" in x or "Hammer" in x or "Morning Star" in x or "White Soldiers" in x for x in pattern_list)
    pat_bear=any("Bearish" in x or "Shooting Star" in x or "Evening Star" in x or "Black Crows" in x for x in pattern_list)
    factors.append(("Candlestick patterns",1 if pat_bull and not pat_bear else -1 if pat_bear and not pat_bull else 0,1.0,not (pattern_list==['No major pattern detected'] or pattern_list==['Insufficient candles'])))
    factors.append(("PCR",1 if np.isfinite(pcr_value) and pcr_value>=1.15 else -1 if np.isfinite(pcr_value) and pcr_value<=.80 else 0,.6,np.isfinite(pcr_value)))
    # Cash-market FII/DII values are user-entered fallback unless a separate authorized feed is connected.
    factors.append(("FII/FPI",1 if np.isfinite(fii_value) and fii_value>0 else -1 if np.isfinite(fii_value) and fii_value<0 else 0,.5,np.isfinite(fii_value)))
    factors.append(("DII",1 if np.isfinite(dii_value) and dii_value>0 else -1 if np.isfinite(dii_value) and dii_value<0 else 0,.35,np.isfinite(dii_value)))
    glob=global_context_changes()
    for label,weight in [("S&P 500",.35),("Nasdaq",.35)]:
        v=glob.get(label,np.nan); factors.append((label,1 if np.isfinite(v) and v>.15 else -1 if np.isfinite(v) and v<-.15 else 0,weight,np.isfinite(v)))
    # These are context flags, not hard-coded claims about market direction.
    for label in ("Brent","USD/INR","India VIX"):
        factors.append((label,0,.2,np.isfinite(glob.get(label,np.nan))))
    if news_bias=='Positive': factors.append(("News",1,.5,True))
    elif news_bias=='Negative': factors.append(("News",-1,.5,True))
    elif news_bias=='Mixed': factors.append(("News",0,.5,True))
    else: factors.append(("News",0,.5,False))
    if event_risk=='High': factors.append(("Event risk",0,1.0,True))
    elif event_risk in ('Low','Moderate'): factors.append(("Event risk",0,.5,True))
    else: factors.append(("Event risk",0,.5,False))
    available=[f for f in factors if f[3]]
    if not available: return 5,0,0,len(factors),0,glob
    directional_weight=sum(w for _,d,w,_ in available if d!=0)
    agree_weight=sum(w for _,d,w,_ in available if d==tech_dir and d!=0) if tech_dir else 0
    coverage=sum(w for _,_,w,_ in available)/sum(w for _,_,w,_ in factors)
    agreement=(agree_weight/directional_weight) if directional_weight else .5
    # A conservative bounded confluence index; not a calibrated probability.
    score=round(float(np.clip((50+35*(agreement-.5))*coverage,5,95)))
    return score, int(sum(1 for _,d,_,ok in available if ok and d==1)), int(sum(1 for _,d,_,ok in available if ok and d==-1)), len(factors), round(coverage*100), glob

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
                    current_mult=adaptive_atr_multiplier(r.close,av,r.adx)
                    pos['stop']=max(pos['stop'],r.close-current_mult*av) if pos['side']=='LONG' else min(pos['stop'],r.close+current_mult*av)
            if exitp is not None:
                gross=(exitp-pos['entry'])/pos['entry'] if pos['side']=='LONG' else (pos['entry']-exitp)/pos['entry']; net=(gross-2*fee)*100
                trades.append({'Entry Time':pos['time'],'Exit Time':str(d.index[i]),'Side':pos['side'],'Entry':pos['entry'],'Exit':exitp,'Net Return %':net,'Result':'Win' if net>0 else 'Loss','Exit Reason':reason});pos=None
        if pos is None and sig in ('BUY','SELL'):
            av=safe(r.atr); ep=safe(nxt.open)
            if not np.isfinite(av) or av<=0 or not np.isfinite(ep) or ep<=0:continue
            side='LONG' if sig=='BUY' else 'SHORT'; entry_mult=adaptive_atr_multiplier(ep,av,r.adx); risk=av*entry_mult
            pos={'side':side,'entry':ep,'stop':ep-risk if side=='LONG' else ep+risk,'target':ep+risk*rr if side=='LONG' else ep-risk*rr,'time':str(d.index[i+1]),'ATR Multiplier':entry_mult}
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
rr=st.sidebar.selectbox('Target risk : reward',[1.0,1.5,2.0,3.0],index=2)
st.sidebar.info('ATR stop multiplier: AUTO — adapts to ATR volatility and ADX trend strength.')
risk_pct=st.sidebar.slider('Paper risk per trade (%)',0.25,2.0,1.0,0.25); capital=st.sidebar.number_input('Paper capital (₹)',min_value=1000.0,value=100000.0,step=5000.0)
st.sidebar.divider()
with st.sidebar.expander('Manual fallback (only if an automatic feed is unavailable)'):
    use_manual_context=st.checkbox('Use manual PCR / FII-DII fallback',value=False)
    manual_pcr=st.number_input('Fallback PCR',0.0,10.0,1.0,.05)
    manual_fii=st.number_input('Fallback FII/FPI net (₹ crore)',value=0.0,step=100.0)
    manual_dii=st.number_input('Fallback DII net (₹ crore)',value=0.0,step=100.0)
news=st.sidebar.selectbox('Verified news bias',['Neutral/unknown','Positive','Negative','Mixed'])
event=st.sidebar.selectbox('Government/geopolitical event risk',['Unknown/not checked','Low','Moderate','High'])

# Automatic institutional flow is intentionally not fabricated: Angel One SmartAPI does not expose cash-market FII/DII net flows.
# PCR uses Angel One's authenticated PCR endpoint when the user has connected SmartAPI.
angel_api_for_pcr=st.session_state.get('angel_api')
pcr_snapshot={"ok": False, "error": "Select Angel One API and connect to load automatic PCR."}
if angel_api_for_pcr is not None:
    pcr_snapshot=fetch_angel_pcr_snapshot(
        getattr(angel_api_for_pcr, 'access_token', None),
        getattr(angel_api_for_pcr, 'api_key', None),
        st.session_state.get('angel_client_code',''), market)
if pcr_snapshot.get('ok'):
    pcr=float(pcr_snapshot['pcr'])
    old_pcr=st.session_state.get('previous_auto_pcr')
    pcr_delta=(pcr-float(old_pcr)) if old_pcr is not None else np.nan
    st.session_state.previous_auto_pcr=pcr
    pcr_source=pcr_snapshot.get('source','Angel One SmartAPI')
    pcr_status='LIVE API' if data_source=='Angel One API' else 'Angel One connected'
else:
    pcr=float(manual_pcr) if use_manual_context else np.nan
    pcr_delta=np.nan
    pcr_source='Manual fallback' if use_manual_context else 'Unavailable'
    pcr_status=pcr_snapshot.get('error','Unavailable')
# SmartAPI PCR endpoint does not provide a prior-refresh OI-change series; do not invent it.
call_oi=np.nan; put_oi=np.nan
fii=float(manual_fii) if use_manual_context else np.nan
dii=float(manual_dii) if use_manual_context else np.nan
pcr_bias=('Bullish' if pcr>=1.15 else 'Bearish' if np.isfinite(pcr) and pcr<=0.80 else 'Mixed') if np.isfinite(pcr) else 'Not assessed'
oi_bias='Not assessed'

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
# Current-bar context only. PCR is a soft confirmation, not a standalone trading signal.
# FII/DII daily net flows are not part of Angel One SmartAPI, so unavailable values are not replaced with zero.
if pcr_bias=='Bullish': bull+=1; reasons.append('Automatic PCR context: higher put/call ratio')
elif pcr_bias=='Bearish': bear+=1; reasons.append('Automatic PCR context: lower put/call ratio')
if news=='Positive': bull+=1; reasons.append('Manual news context: positive')
elif news=='Negative': bear+=1; reasons.append('Manual news context: negative')
if event=='High':
    signal='WAIT'; reasons.append('High event risk: confirmation withheld')
else:
    signal='BUY' if bull>=4 and bull>=bear+2 else 'SELL' if bear>=4 and bear>=bull+2 else 'WAIT'
atr=safe(last.atr)
if not np.isfinite(atr) or atr<=0: atr=price*.005
# Volatility-aware stop distance: ATR% sets the base multiplier, ADX slightly widens it in strong trends.
atr_mult=adaptive_atr_multiplier(price,atr,last.adx)
entry=price
entry2=price-0.5*atr if signal=='BUY' else price+0.5*atr if signal=='SELL' else np.nan
risk_distance=atr_mult*atr
stop=price-risk_distance if signal=='BUY' else price+risk_distance if signal=='SELL' else np.nan
target1=price+risk_distance if signal=='BUY' else price-risk_distance if signal=='SELL' else np.nan
target2=price+risk_distance*rr if signal=='BUY' else price-risk_distance*rr if signal=='SELL' else np.nan
target=target2
confidence, conf_bull, conf_bear, conf_factor_count, conf_coverage, global_changes = combined_confidence(last,bull,bear,pcr,fii,dii,news,event,pats)

st.subheader('1. Current Signal')
c=st.columns(6); c[0].metric('Instrument',market);c[1].metric('Last Price',f'{price:,.2f}');c[2].metric('Signal',signal);c[3].metric('Bullish Score',bull);c[4].metric('Bearish Score',bear);c[5].metric('Multi-Factor Confidence',f'{confidence}%')
st.caption(f'Source: {data_source} • Last candle: {data.index[-1]} • Confidence is a rule-based confluence index, not a calibrated probability of profit. Available-factor coverage: {conf_coverage}%.')
st.write('Signal notes: '+' • '.join(reasons) if reasons else 'Signal notes: mixed/insufficient confirmation.')
trend_now='Bullish' if last.ema20>last.ema50 else 'Bearish' if last.ema20<last.ema50 else 'Sideways'
ctx=st.columns(6);ctx[0].metric('Trend',trend_now);ctx[1].metric('Volume ratio',fmt(last.vol_ratio));ctx[2].metric('PCR (auto)',fmt(pcr));ctx[3].metric('PCR change',fmt(pcr_delta));ctx[4].metric('Auto ATR multiplier',f'{atr_mult:.2f}x');ctx[5].metric('ADX',fmt(last.adx))

st.subheader('2. Entry, Exit, Targets, Stop Loss & Trailing Stop')
c=st.columns(7);c[0].metric('Entry 1',fmt(entry) if signal!='WAIT' else 'WAIT');c[1].metric('Entry 2 (ATR pullback)',fmt(entry2));c[2].metric('Stop Loss',fmt(stop));c[3].metric('Target 1 (1R)',fmt(target1));c[4].metric(f'Target 2 ({rr:g}R)',fmt(target2));c[5].metric('ATR',fmt(atr));c[6].metric('Risk : Reward',f'1 : {rr:g}' if signal!='WAIT' else 'N/A')
trail=price-atr_mult*atr if signal=='BUY' else price+atr_mult*atr if signal=='SELL' else np.nan
partial_exit_pct=st.select_slider('Suggested partial exit at Target 1 (%)',options=[25,33,50,75],value=50)
st.metric('Indicative Trailing Stop',fmt(trail));st.caption(f'Paper/manual planning only: consider partial exit of {partial_exit_pct}% at Target 1. Entry 2 is an ATR pullback reference, not a guaranteed fill. No orders are sent to a broker.')
with st.expander('Confidence factor details'):
    st.write(f'Bullish directional factors: {conf_bull} • Bearish directional factors: {conf_bear} • Factors evaluated: {conf_factor_count} • Data coverage: {conf_coverage}%')
    st.dataframe(pd.DataFrame([{'Factor':k,'Daily change %':v} for k,v in global_changes.items()]),use_container_width=True,hide_index=True)

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

st.subheader('7. Automatic PCR, FII / DII and Event Context')
if pcr_snapshot.get('ok'):
    st.success(f"PCR source: {pcr_source} • matched contract: {pcr_snapshot.get('symbol','N/A')} • updated: {pcr_snapshot.get('updated','N/A')}")
else:
    st.warning(f"Automatic PCR unavailable: {pcr_status}")
if np.isfinite(fii) or np.isfinite(dii):
    st.info('FII/DII values below are manual fallback inputs. They are not live values from Angel One.')
else:
    st.info('FII/FPI and DII cash-market net flows are not provided by the Angel One SmartAPI endpoint used here. To avoid fake values, the dashboard shows N/A until a licensed/authorized FII-DII data feed is connected.')
c=st.columns(6)
c[0].metric('PCR',fmt(pcr))
c[1].metric('PCR change since refresh',fmt(pcr_delta))
c[2].metric('Call OI change',fmt(call_oi))
c[3].metric('Put OI change',fmt(put_oi))
c[4].metric('FII/FPI net (₹ cr)',fmt(fii,0))
c[5].metric('DII net (₹ cr)',fmt(dii,0))
st.write(f'PCR interpretation: {pcr_bias} • OI interpretation: {oi_bias} • Verified news bias: {news} • Government/geopolitical event risk: {event}')
st.caption('PCR is an options-market context metric, not a guaranteed direction predictor. PCR change is measured against the previous successful app refresh; it is N/A on the first fetch or when the feed is unavailable.')

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
st.caption('Final signal is a rule-based confluence of technical indicators plus automatic PCR context when available and user-entered news/event context. A BUY/SELL label is not a guarantee or a calibrated probability; use this as an educational/paper-trading dashboard.')

st.subheader('12. Current Data / Signal Caveats')
st.write('• Angel One mode resolves the instrument master and fetches candles. PCR is attempted through the authenticated SmartAPI endpoint. FII/DII net flows are manual fallback only in this build; live OI-change percentages and automatic news/geopolitical verification are not integrated and remain N/A/manual. Global daily changes use Yahoo Finance and may be delayed.')
st.write('• “Win chance” is not inferred from score. The backtest win rate is historical for the selected ticker, period and rules and may not predict future results.')
st.write('• Yahoo ticker coverage varies; some index symbols or intraday intervals may be unavailable. Always verify latest timestamps and use paper trading first.')
st.caption('For public deployment, change the default login and use secure secrets. This app is not financial advice.')
