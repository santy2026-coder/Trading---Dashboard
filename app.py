import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

# Optional broker dependencies: Yahoo/demo mode can still run if SmartAPI is not installed.
try:
    from SmartApi import SmartConnect
    import pyotp
    SMARTAPI_AVAILABLE = True
except Exception:
    SmartConnect = None
    pyotp = None
    SMARTAPI_AVAILABLE = False

st.set_page_config(page_title="Trading Intelligence Terminal", page_icon="📈", layout="wide")

# Lightweight app gate. Configure APP_USERNAME and APP_PASSWORD as environment
# variables or Streamlit secrets before deploying publicly.
def _configured_credential(name, fallback):
    try:
        value = st.secrets.get(name, None)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, fallback)

_EXPECTED_USER = _configured_credential("APP_USERNAME", "admin")
_EXPECTED_PASS = _configured_credential("APP_PASSWORD", "admin123")
if "app_authenticated" not in st.session_state:
    st.session_state.app_authenticated = False
if not st.session_state.app_authenticated:
    st.title("🔐 Trading Intelligence Terminal — Login")
    st.caption("Set APP_USERNAME and APP_PASSWORD in deployment secrets before publishing this app.")
    with st.form("login_form"):
        _user = st.text_input("Username")
        _password = st.text_input("Password", type="password")
        _submit = st.form_submit_button("Login", use_container_width=True)
    if _submit:
        if _user == _EXPECTED_USER and _password == _EXPECTED_PASS:
            st.session_state.app_authenticated = True
            st.rerun()
        else:
            st.error("Incorrect username or password.")
    st.stop()

APP_DIR = Path(__file__).parent
JOURNAL_PATH = APP_DIR / "trade_journal.csv"
INSTRUMENT_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

INDIAN_WATCHLIST = {
    "NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "SENSEX": "^BSESN",
    "Reliance": "RELIANCE.NS", "HDFC Bank": "HDFCBANK.NS", "ICICI Bank": "ICICIBANK.NS",
    "SBI": "SBIN.NS", "Axis Bank": "AXISBANK.NS", "Kotak Bank": "KOTAKBANK.NS",
    "TCS": "TCS.NS", "Infosys": "INFY.NS", "HCL Tech": "HCLTECH.NS",
    "Wipro": "WIPRO.NS", "Tech Mahindra": "TECHM.NS",
    "Tata Motors": "TATAMOTORS.NS", "Maruti": "MARUTI.NS", "Mahindra & Mahindra": "M&M.NS",
    "Bajaj Auto": "BAJAJ-AUTO.NS", "Hero MotoCorp": "HEROMOTOCO.NS",
    "ITC": "ITC.NS", "Larsen & Toubro": "LT.NS", "Bharti Airtel": "BHARTIARTL.NS",
}
GLOBAL_WATCHLIST = {
    "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "Dow Jones": "^DJI",
    "Nikkei 225": "^N225", "Hang Seng": "^HSI", "Shanghai Composite": "000001.SS",
    "FTSE 100": "^FTSE", "DAX": "^GDAXI", "Gift Nifty (Yahoo symbol may vary)": "NIFTY=F",
    "USD/INR": "INR=X", "Brent Crude": "BZ=F", "Gold": "GC=F", "India VIX": "^INDIAVIX",
}

def init_state():
    defaults = {
        "angel_api": "", "angel_client": "", "angel_pin": "", "angel_totp": "",
        "angel_session": None, "angel_instruments": None, "angel_status": "Not connected",
        "paper_position": None, "paper_cash": 100000.0, "paper_realized": 0.0,
        "last_data": None, "source": "Yahoo Finance", "refresh_on": False, "mtf_bias": "N/A", "pcr_bias": "N/A",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_state()

def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default

@st.cache_data(ttl=20, show_spinner=False)
def get_yahoo_history(symbol, period="5d", interval="5m"):
    try:
        data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
        if data is None or data.empty:
            return pd.DataFrame()
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        data = data.rename(columns={c: str(c).title() for c in data.columns})
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in data.columns for c in required):
            return pd.DataFrame()
        data = data[required].dropna(subset=["Open", "High", "Low", "Close"])
        data.index = pd.to_datetime(data.index)
        return data
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def download_instrument_master():
    try:
        response = requests.get(INSTRUMENT_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
        return pd.DataFrame(payload)
    except Exception:
        return pd.DataFrame()

def connect_angel():
    if not SMARTAPI_AVAILABLE:
        raise RuntimeError("SmartAPI/pyotp package is unavailable. Install requirements.txt and redeploy.")
    api_key = st.session_state.angel_api.strip()
    client_code = st.session_state.angel_client.strip()
    pin = st.session_state.angel_pin.strip()
    totp_secret = st.session_state.angel_totp.strip()
    if not all([api_key, client_code, pin, totp_secret]):
        raise ValueError("Enter Angel One API Key, Client Code, PIN and TOTP secret.")
    otp = pyotp.TOTP(totp_secret).now()
    client = SmartConnect(api_key=api_key)
    result = client.generateSession(client_code, pin, otp)
    if not result or not result.get("status"):
        raise RuntimeError(f"Angel One login failed: {(result or {}).get('message', 'Unknown error')}")
    st.session_state.angel_session = client
    st.session_state.angel_status = "Connected"
    return result

def find_angel_token(symbol, exchange="NSE"):
    """Resolve a Yahoo/display ticker to an Angel One (token, trading_symbol).

    The public instrument master commonly uses `token` (not `symboltoken`),
    so support both schemas and prefer exact/index matches over derivatives.
    """
    master = st.session_state.get("angel_instruments")
    if master is None or master.empty:
        master = download_instrument_master()
        st.session_state.angel_instruments = master
    if master is None or master.empty:
        return None

    cols = {str(c).strip().lower(): c for c in master.columns}
    token_col = cols.get("symboltoken") or cols.get("token")
    exch_col = cols.get("exch_seg")
    name_col = cols.get("name")
    tsym_col = cols.get("symbol")
    type_col = cols.get("instrumenttype")
    if token_col is None or exch_col is None or tsym_col is None:
        return None

    raw = str(symbol).strip().upper()
    # Index labels on Yahoo Finance do not match Angel One's trading symbols.
    aliases = {
        "^NSEI": ["NIFTY 50", "NIFTY50", "NIFTY"],
        "NSEI": ["NIFTY 50", "NIFTY50", "NIFTY"],
        "^NSEBANK": ["NIFTY BANK", "BANKNIFTY", "NIFTYBANK"],
        "NSEBANK": ["NIFTY BANK", "BANKNIFTY", "NIFTYBANK"],
        "^BSESN": ["SENSEX"],
        "BSESN": ["SENSEX"],
    }
    base = raw.removesuffix(".NS")
    candidates = aliases.get(raw, [base])
    # NSE/BSE routing for index tickers; SENSEX is listed on BSE.
    if raw in ("^BSESN", "BSESN"):
        exchange = "BSE"

    def norm(value):
        return "".join(ch for ch in str(value).upper().strip() if ch.isalnum())

    frame = master[
        master[exch_col].astype(str).str.strip().str.upper().eq(str(exchange).upper())
    ].copy()
    if frame.empty:
        return None
    frame["_sym"] = frame[tsym_col].map(norm)
    frame["_name"] = frame[name_col].map(norm) if name_col is not None else ""
    frame["_type"] = frame[type_col].astype(str).str.upper() if type_col is not None else ""

    normalized = [norm(x) for x in candidates]
    is_index = raw in ("^NSEI", "NSEI", "^NSEBANK", "NSEBANK", "^BSESN", "BSESN")

    # Exact symbol/name matches first. For index tickers, prefer the index
    # instrument type and never resolve to a random option/futures contract.
    for candidate in normalized:
        hits = frame[(frame["_sym"] == candidate) | (frame["_name"] == candidate)].copy()
        if is_index and type_col is not None and not hits.empty:
            index_hits = hits[hits["_type"].str.contains(r"INDEX|AMXIDX", regex=True, na=False)]
            if not index_hits.empty:
                hits = index_hits
        if not hits.empty:
            row = hits.iloc[0]
            token = str(row[token_col]).strip()
            trading_symbol = str(row[tsym_col]).strip()
            if token and token.lower() != "nan":
                return token, trading_symbol

    # Some master versions put index labels only in `name`; use conservative
    # starts-with/contains matching, with index type filtering where available.
    for candidate in normalized:
        if candidate in ("NIFTY", "NIFTY50", "NIFTYBANK", "BANKNIFTY", "SENSEX"):
            if candidate in ("NIFTYBANK", "BANKNIFTY"):
                mask = frame["_sym"].isin(["NIFTYBANK", "BANKNIFTY"]) | frame["_name"].isin(["NIFTYBANK", "BANKNIFTY"])
            elif candidate == "SENSEX":
                mask = frame["_sym"].eq("SENSEX") | frame["_name"].eq("SENSEX")
            else:
                mask = frame["_sym"].isin(["NIFTY", "NIFTY50"]) | frame["_name"].isin(["NIFTY", "NIFTY50"])
            hits = frame[mask].copy()
            if type_col is not None and not hits.empty:
                index_hits = hits[hits["_type"].str.contains(r"INDEX|AMXIDX", regex=True, na=False)]
                if not index_hits.empty:
                    hits = index_hits
            if not hits.empty:
                row = hits.iloc[0]
                token = str(row[token_col]).strip()
                trading_symbol = str(row[tsym_col]).strip()
                if token and token.lower() != "nan":
                    return token, trading_symbol
        else:
            # Stock symbols generally use the -EQ suffix in Angel One's master.
            stock_candidates = [candidate]
            if not candidate.endswith("EQ"):
                stock_candidates.append(candidate + "EQ")
            hits = frame[frame["_sym"].isin(stock_candidates)].copy()
            if not hits.empty:
                if type_col is not None:
                    cash_hits = hits[~hits["_type"].astype(str).str.upper().str.contains(r"FUT|OPT|CE|PE", regex=True, na=False)]
                    if not cash_hits.empty:
                        hits = cash_hits
                row = hits.iloc[0]
                token = str(row[token_col]).strip()
                trading_symbol = str(row[tsym_col]).strip()
                if token and token.lower() != "nan":
                    return token, trading_symbol

    return None

def get_angel_history(symbol, interval="FIVE_MINUTE", exchange="NSE"):
    client = st.session_state.angel_session
    if client is None:
        return pd.DataFrame(), "Angel One is not connected."
    if str(symbol).strip().upper() in ("^BSESN", "BSESN"):
        exchange = "BSE"
    resolved = find_angel_token(symbol, exchange)
    if not resolved:
        return pd.DataFrame(), f"Could not auto-resolve Angel One token for {symbol}. Try selecting its exact exchange symbol."
    token, trading_symbol = resolved
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=5)
    params = {
        "exchange": exchange, "symboltoken": token,
        "interval": interval,
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        response = client.getCandleData(params)
        rows = (response or {}).get("data") or []
        if not rows:
            return pd.DataFrame(), f"No candle data returned for {trading_symbol}: {(response or {}).get('message', '')}"
        df = pd.DataFrame(rows, columns=["Datetime", "Open", "High", "Low", "Close", "Volume"])
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        df = df.set_index("Datetime")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"]), f"Angel One candles: {trading_symbol} (token auto-resolved)"
    except Exception as exc:
        return pd.DataFrame(), f"Angel One candle request failed: {exc}"

def add_indicators(df):
    if df.empty:
        return df
    d = df.copy()
    close, high, low, volume = d["Close"], d["High"], d["Low"], d["Volume"].fillna(0)
    d["EMA9"] = close.ewm(span=9, adjust=False).mean()
    d["EMA20"] = close.ewm(span=20, adjust=False).mean()
    d["EMA50"] = close.ewm(span=50, adjust=False).mean()
    d["SMA20"] = close.rolling(20).mean()
    d["SMA50"] = close.rolling(50).mean()
    d["SMA100"] = close.rolling(100).mean()
    d["SMA200"] = close.rolling(200).mean()
    d["MACD"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    d["MACD_Signal"] = d["MACD"].ewm(span=9, adjust=False).mean()
    d["BB_Mid"] = close.rolling(20).mean()
    std = close.rolling(20).std()
    d["BB_Upper"], d["BB_Lower"] = d["BB_Mid"] + 2 * std, d["BB_Mid"] - 2 * std
    d["BB_Width"] = (d["BB_Upper"] - d["BB_Lower"]) / d["BB_Mid"].replace(0, np.nan)
    d["BB_Squeeze"] = d["BB_Width"] <= d["BB_Width"].rolling(50, min_periods=15).quantile(0.20)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.rolling(14).mean()
    d["ATR_Pct"] = 100 * d["ATR"] / close.replace(0, np.nan)
    d["ADR"] = (100 * (high - low) / close.replace(0, np.nan)).rolling(20).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    d["RSI"] = 100 - (100 / (1 + rs))
    low14, high14 = low.rolling(14).min(), high.rolling(14).max()
    d["Stoch_K"] = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    d["Stoch_D"] = d["Stoch_K"].rolling(3).mean()
    typical = (high + low + close) / 3
    # True volume-weighted VWAP where volume exists. Index feeds sometimes return
    # zero volume; show a clearly labelled price-only proxy rather than N/A.
    vol = pd.to_numeric(volume, errors="coerce").fillna(0).clip(lower=0)
    cumulative_vol = vol.cumsum()
    true_vwap = (typical * vol).cumsum() / cumulative_vol.replace(0, np.nan)
    price_proxy = typical.expanding(min_periods=1).mean()
    d["VWAP"] = true_vwap.where(cumulative_vol > 0, price_proxy)
    d["VWAP_Is_Proxy"] = cumulative_vol <= 0
    d["Vol_Avg20"] = volume.rolling(20).mean()
    d["Vol_Ratio"] = volume / d["Vol_Avg20"].replace(0, np.nan)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_smooth = tr.rolling(14).mean().replace(0, np.nan)
    d["Plus_DI"] = 100 * plus_dm.rolling(14).mean() / atr_smooth
    d["Minus_DI"] = 100 * minus_dm.rolling(14).mean() / atr_smooth
    dx = 100 * (d["Plus_DI"] - d["Minus_DI"]).abs() / (d["Plus_DI"] + d["Minus_DI"]).replace(0, np.nan)
    d["ADX"] = dx.rolling(14).mean()
    d["Pivot"] = (high.shift(1) + low.shift(1) + close.shift(1)) / 3
    d["Support"] = low.rolling(20).min()
    d["Resistance"] = high.rolling(20).max()
    return d

def detect_candle_patterns(d):
    """Return pattern labels for each candle; multi-candle patterns are confirmed on the last candle."""
    out = pd.Series("", index=d.index, dtype=object)
    if len(d) == 0:
        return out
    for i in range(len(d)):
        a = d.iloc[i]
        o, h, l, c = map(float, (a["Open"], a["High"], a["Low"], a["Close"]))
        body = abs(c-o); rng = max(h-l, 1e-12)
        upper = h-max(o,c); lower = min(o,c)-l
        labels=[]
        if body/rng <= 0.08: labels.append("Doji")
        if body/rng <= 0.30 and upper > body and lower > body: labels.append("Spinning Top")
        if lower >= max(body*2, rng*0.45) and upper <= max(body*0.6, rng*0.12): labels.append("Hammer / Hanging Man shape")
        if upper >= max(body*2, rng*0.45) and lower <= max(body*0.6, rng*0.12): labels.append("Inverted Hammer / Shooting Star shape")
        if body/rng >= 0.88 and c > o: labels.append("Bullish Marubozu-like")
        if body/rng >= 0.88 and c < o: labels.append("Bearish Marubozu-like")
        if i >= 1:
            p = d.iloc[i-1]; po, ph, pl, pc = map(float,(p["Open"],p["High"],p["Low"],p["Close"]))
            if c>o and pc<po and o<=pc and c>=po: labels.append("Bullish Engulfing")
            if c<o and pc>po and o>=pc and c<=po: labels.append("Bearish Engulfing")
            if h<ph and l>pl: labels.append("Inside Bar")
            if h>ph and l<pl: labels.append("Outside Bar")
            if abs(h-ph) <= rng*0.08 and c<o and pc>po: labels.append("Possible Tweezer Top")
            if abs(l-pl) <= rng*0.08 and c>o and pc<po: labels.append("Possible Tweezer Bottom")
            mid=(po+pc)/2
            if c>o and pc<po and o<pl and c>mid and c<po: labels.append("Piercing Line candidate")
            if c<o and pc>po and o>ph and c<mid and c>po: labels.append("Dark Cloud Cover candidate")
        if i >= 2:
            p1=d.iloc[i-1]; p2=d.iloc[i-2]
            o1,c1=float(p1["Open"]),float(p1["Close"]); o2,c2=float(p2["Open"]),float(p2["Close"])
            b0=abs(c2-o2); b1=abs(c1-o1)
            if c2<o2 and b1 < b0*0.55 and c>o and c>(o2+c2)/2: labels.append("Morning Star candidate")
            if c2>o2 and b1 < b0*0.55 and c<o and c<(o2+c2)/2: labels.append("Evening Star candidate")
            if all(float(d.iloc[j]["Close"])>float(d.iloc[j]["Open"]) for j in [i-2,i-1,i]) and c>c1>c2: labels.append("Three White Soldiers candidate")
            if all(float(d.iloc[j]["Close"])<float(d.iloc[j]["Open"]) for j in [i-2,i-1,i]) and c<c1<c2: labels.append("Three Black Crows candidate")
        out.iloc[i] = "; ".join(dict.fromkeys(labels))
    return out

def candle_patterns(d):
    if len(d) < 1: return []
    label = detect_candle_patterns(d).iloc[-1]
    return label.split("; ") if label else ["No major pattern detected"]

def add_price_action_columns(d):
    x=d.copy()
    x["Candle Type"] = np.where(x["Close"]>x["Open"],"Bullish",np.where(x["Close"]<x["Open"],"Bearish","Doji"))
    x["Body"]=(x["Close"]-x["Open"]).abs()
    x["Upper Wick"]=x["High"]-x[["Open","Close"]].max(axis=1)
    x["Lower Wick"]=x[["Open","Close"]].min(axis=1)-x["Low"]
    x["Range"]=x["High"]-x["Low"]
    x["Body %"]=(100*x["Body"]/x["Range"].replace(0,np.nan)).round(1)
    x["Pattern"]=detect_candle_patterns(x)
    prev_high=x["High"].shift(1).rolling(10).max()
    prev_low=x["Low"].shift(1).rolling(10).min()
    x["Price Action"]="Range / no confirmed break"
    x.loc[x["Close"]>prev_high,"Price Action"]="Bullish BOS / 10-bar breakout"
    x.loc[x["Close"]<prev_low,"Price Action"]="Bearish BOS / 10-bar breakdown"
    # HH/HL/LH/LL are simple rolling comparisons, not swing-point-perfect labels.
    x["Structure"]="—"
    x.loc[(x["High"]>x["High"].shift(1)) & (x["Low"]>x["Low"].shift(1)),"Structure"]="HH + HL"
    x.loc[(x["High"]<x["High"].shift(1)) & (x["Low"]<x["Low"].shift(1)),"Structure"]="LH + LL"
    x["Volume Status"] = np.where(x["Vol_Ratio"]>=1.2,"High volume",np.where(x["Vol_Ratio"]<0.8,"Low volume","Normal / unavailable"))
    x["VWAP Position"] = np.where(x["Close"]>x["VWAP"],"Above VWAP", "Below VWAP")
    prior_hi = x["High"].shift(1).rolling(10, min_periods=5).max()
    prior_lo = x["Low"].shift(1).rolling(10, min_periods=5).min()
    prior_struct = x["Structure"].shift(1).fillna("—")
    x["CHoCH"] = "—"
    x.loc[(prior_struct.str.contains("LH|LL", regex=True)) & (x["Close"] > prior_hi), "CHoCH"] = "Bullish CHoCH candidate"
    x.loc[(prior_struct.str.contains("HH|HL", regex=True)) & (x["Close"] < prior_lo), "CHoCH"] = "Bearish CHoCH candidate"
    x["Liquidity Sweep"] = "—"
    x.loc[(x["High"] > prior_hi) & (x["Close"] < prior_hi), "Liquidity Sweep"] = "Buy-side sweep candidate"
    x.loc[(x["Low"] < prior_lo) & (x["Close"] > prior_lo), "Liquidity Sweep"] = "Sell-side sweep candidate"
    return x

def get_daily_change(ticker):
    """Cached daily percent change for macro context; returns NaN when unavailable."""
    try:
        hist = get_yahoo_history(ticker, period="10d", interval="1d")
        if hist is None or hist.empty or len(hist) < 2:
            return np.nan
        previous = safe_float(hist["Close"].iloc[-2])
        current = safe_float(hist["Close"].iloc[-1])
        return (current / previous - 1.0) * 100.0 if np.isfinite(previous) and previous else np.nan
    except Exception:
        return np.nan


def build_macro_context():
    """Build transparent macro snapshot. Not a substitute for live exchange feeds."""
    tickers = {
        "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "Dow Jones": "^DJI",
        "NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "Brent Crude": "BZ=F",
        "USD/INR": "INR=X", "India VIX": "^INDIAVIX",
    }
    changes = {name: get_daily_change(ticker) for name, ticker in tickers.items()}
    # Positive global equities help risk appetite; rising crude, USD/INR and VIX are
    # treated as India-risk headwinds. Missing feeds contribute zero, not a guess.
    score = 0
    for name in ("S&P 500", "NASDAQ", "Dow Jones", "NIFTY 50", "BANK NIFTY"):
        value = changes.get(name, np.nan)
        if np.isfinite(value): score += 1 if value > 0.15 else -1 if value < -0.15 else 0
    for name in ("Brent Crude", "USD/INR", "India VIX"):
        value = changes.get(name, np.nan)
        if np.isfinite(value): score += -1 if value > 0.30 else 1 if value < -0.30 else 0
    bias = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Mixed / neutral"
    return changes, score, bias


def analyze_signal(d, rr_multiple=2):
    if len(d) < 30:
        return {"signal": "WAIT", "score": 0, "confidence": 0, "reasons": ["Not enough candles for stable indicators."], "patterns": []}
    a = d.iloc[-1]
    bull = bear = 0
    reasons = []
    def add(cond, weight, bullish_text, bearish_text):
        nonlocal bull, bear
        if pd.isna(cond):
            reasons.append("Unavailable: " + bullish_text)
        elif bool(cond):
            bull += weight; reasons.append(f"Bullish +{weight}: {bullish_text}")
        else:
            bear += weight; reasons.append(f"Bearish +{weight}: {bearish_text}")
    add(a["EMA9"] > a["EMA20"], 1, "EMA9 above EMA20", "EMA9 below EMA20")
    add(a["Close"] > a["VWAP"], 1, "Price above VWAP", "Price below VWAP")
    add(a["MACD"] > a["MACD_Signal"], 1, "MACD bullish", "MACD bearish")
    add(a["RSI"] >= 52, 1, "RSI above 52", "RSI below bullish threshold")
    add((a["Plus_DI"] > a["Minus_DI"]) and (a["ADX"] >= 18), 1, "ADX/DI bullish trend", "ADX/DI bearish or weak")
    add(a["Close"] > a["BB_Mid"], 1, "Above Bollinger midline", "Below Bollinger midline")
    add(a["Vol_Ratio"] >= 1.2, 1, "Volume expansion >1.2x", "No strong volume expansion")
    # Stochastic: reward momentum turns without blindly chasing overbought readings.
    prev = d.iloc[-2]
    stoch_bull = (a["Stoch_K"] > a["Stoch_D"]) and (a["Stoch_K"] < 80) and (prev["Stoch_K"] <= prev["Stoch_D"] or a["Stoch_K"] < 35)
    stoch_bear = (a["Stoch_K"] < a["Stoch_D"]) and (a["Stoch_K"] > 20) and (prev["Stoch_K"] >= prev["Stoch_D"] or a["Stoch_K"] > 65)
    if stoch_bull: bull += 1; reasons.append("Bullish +1: Stochastic bullish turn")
    elif stoch_bear: bear += 1; reasons.append("Bearish +1: Stochastic bearish turn")
    else: reasons.append("Stochastic: no clear trigger / avoid chasing extremes")
    # Breakout/breakdown uses prior candles only to avoid including current candle in the level.
    prior_high = d["High"].iloc[-11:-1].max() if len(d) >= 11 else np.nan
    prior_low = d["Low"].iloc[-11:-1].min() if len(d) >= 11 else np.nan
    if pd.notna(prior_high) and a["Close"] > prior_high and a["Vol_Ratio"] >= 1.0:
        bull += 2; reasons.append("Bullish +2: 10-candle resistance breakout with volume")
    elif pd.notna(prior_low) and a["Close"] < prior_low and a["Vol_Ratio"] >= 1.0:
        bear += 2; reasons.append("Bearish +2: 10-candle support breakdown with volume")
    else:
        reasons.append("No confirmed high-volume 10-candle breakout/breakdown")
    # Bollinger squeeze release confirmation.
    was_squeeze = bool(d["BB_Squeeze"].iloc[-2]) if "BB_Squeeze" in d and pd.notna(d["BB_Squeeze"].iloc[-2]) else False
    if was_squeeze and a["Close"] > a["BB_Upper"]:
        bull += 1; reasons.append("Bullish +1: Bollinger squeeze release above upper band")
    elif was_squeeze and a["Close"] < a["BB_Lower"]:
        bear += 1; reasons.append("Bearish +1: Bollinger squeeze release below lower band")
    # Approximate RSI divergence; only a context hint, not a perfect swing detector.
    if len(d) >= 12:
        old = d.iloc[-12]
        if a["Low"] < d["Low"].iloc[-12:-1].min() and a["RSI"] > d["RSI"].iloc[-12:-1].min():
            bull += 1; reasons.append("Bullish +1: possible bullish RSI divergence")
        if a["High"] > d["High"].iloc[-12:-1].max() and a["RSI"] < d["RSI"].iloc[-12:-1].max():
            bear += 1; reasons.append("Bearish +1: possible bearish RSI divergence")
    patterns = candle_patterns(d)
    pat = " ".join(patterns).lower()
    if any(k in pat for k in ["bullish engulfing", "hammer", "morning star", "piercing line", "tweezer bottom", "three white soldiers"]):
        bull += 1; reasons.append("Bullish +1: bullish candle-pattern context")
    if any(k in pat for k in ["bearish engulfing", "shooting star", "evening star", "dark cloud", "tweezer top", "three black crows"]):
        bear += 1; reasons.append("Bearish +1: bearish candle-pattern context")
    # MTF bias is populated from the fetched 5m/15m EMA context when available.
    mtf_bias = st.session_state.get("mtf_bias", "N/A")
    if mtf_bias == "Bullish": bull += 1; reasons.append("Bullish +1: higher-timeframe EMA alignment")
    elif mtf_bias == "Bearish": bear += 1; reasons.append("Bearish +1: higher-timeframe EMA alignment")
    # PCR is only included if a valid Angel One option-chain snapshot exists.
    pcr_bias = st.session_state.get("pcr_bias", "N/A")
    if pcr_bias == "Bullish": bull += 1; reasons.append("Bullish +1: option-chain PCR/OI context (prior successful refresh)")
    elif pcr_bias == "Bearish": bear += 1; reasons.append("Bearish +1: option-chain PCR/OI context (prior successful refresh)")
    # Macro context is a low-weight confirmation only; unavailable macro data is ignored.
    macro_bias = st.session_state.get("macro_bias", "N/A")
    if macro_bias == "Bullish":
        bull += 1; reasons.append("Macro +1: global/Indian risk context is supportive")
    elif macro_bias == "Bearish":
        bear += 1; reasons.append("Macro +1: global/Indian risk context is risk-off")
    score = bull - bear
    # Require a directional edge and minimum number of supporting checks.
    signal = "BUY" if score >= 4 and bull >= 5 else "SELL" if score <= -4 and bear >= 5 else "WAIT"
    total = max(bull + bear, 1)
    confidence = int(round(100 * max(bull, bear) / total)) if signal != "WAIT" else int(round(100 * abs(score) / max(total, 1)))
    confidence = min(95, confidence)
    entry = float(a["Close"])
    atr = safe_float(a["ATR"], entry * 0.005)
    if not np.isfinite(atr) or atr <= 0: atr = entry * 0.005
    risk = 1.5 * atr
    rr_multiple = float(rr_multiple) if rr_multiple in (1, 2, 3, 5) else 2.0
    if signal == "BUY":
        sl, t1, t2, t3 = entry-risk, entry+risk*rr_multiple, entry+risk*2, entry+risk*3
    elif signal == "SELL":
        sl, t1, t2, t3 = entry+risk, entry-risk*rr_multiple, entry-risk*2, entry-risk*3
    else: sl = t1 = t2 = t3 = np.nan
    trend = "Bullish" if a["EMA9"] > a["EMA20"] and a["Close"] > a["EMA50"] else "Bearish" if a["EMA9"] < a["EMA20"] and a["Close"] < a["EMA50"] else "Mixed"
    return {"signal": signal, "score": score, "bullish_score": bull, "bearish_score": bear, "confidence": confidence,
            "reasons": reasons, "entry": entry, "sl": sl, "t1": t1, "t2": t2, "t3": t3, "atr": atr,
            "rr": f"1:{rr_multiple:g} (T1), 1:2 (T2), 1:3 (T3)", "patterns": patterns, "trend": trend, "risk": risk}

def calc_backtest(d, cost_bps=10):
    """Full-bar EMA 9/20 proxy backtest with one-bar execution lag and costs."""
    if len(d) < 40: return pd.DataFrame()
    x = d.copy()
    x["fast"] = x["Close"].ewm(span=9, adjust=False).mean()
    x["slow"] = x["Close"].ewm(span=20, adjust=False).mean()
    x["position"] = np.where(x["fast"] > x["slow"], 1, -1)
    x["position_lag"] = x["position"].shift(1).fillna(0)
    x["market_return"] = x["Close"].pct_change().fillna(0)
    x["turnover"] = x["position"].diff().abs().fillna(x["position"].abs())
    x["strategy_return"] = x["position_lag"] * x["market_return"] - x["turnover"] * (float(cost_bps) / 10000.0)
    x["equity_curve"] = (1 + x["strategy_return"]).cumprod()
    x["drawdown"] = x["equity_curve"] / x["equity_curve"].cummax() - 1
    x["P&L % (bar proxy)"] = x["strategy_return"] * 100
    x["Side"] = np.where(x["position"] > 0, "LONG", "SHORT")
    x["Trade Change"] = x["position"].diff().fillna(0).ne(0)
    return x.tail(500)

def load_journal():
    if JOURNAL_PATH.exists():
        try:
            return pd.read_csv(JOURNAL_PATH)
        except Exception:
            pass
    return pd.DataFrame(columns=["time", "symbol", "side", "entry", "exit", "quantity", "pnl", "notes"])

def save_journal(df):
    df.to_csv(JOURNAL_PATH, index=False)



def get_option_chain_snapshot(index_name, spot_price, max_strikes=12):
    """Fetch a best-effort Angel One NFO option-chain snapshot using FULL market data.
    Returns rows with OI and polling-based OI change; never fabricates PCR/OI.
    """
    client = st.session_state.angel_session
    master = st.session_state.angel_instruments
    if client is None:
        return pd.DataFrame(), "Connect Angel One to load live option-chain OI/PCR."
    if master is None or master.empty:
        master = download_instrument_master()
        st.session_state.angel_instruments = master
    if master.empty:
        return pd.DataFrame(), "Could not download Angel One instrument master."
    lower = {str(c).lower(): c for c in master.columns}
    needed = ["symbol", "name", "exch_seg", "instrumenttype", "expiry", "strike"]
    if not all(k in lower for k in needed) or not ("token" in lower or "symboltoken" in lower):
        return pd.DataFrame(), "Instrument master schema did not include the required option fields."
    def col(k): return lower[k]
    token_col = lower.get("symboltoken") or lower.get("token")
    optiontype_col = lower.get("optiontype")
    frame = master.copy()
    frame = frame[frame[col("exch_seg")].astype(str).str.upper().eq("NFO")]
    frame = frame[frame[col("instrumenttype")].astype(str).str.upper().str.contains("OPTIDX", na=False)]
    names = frame[col("name")].astype(str).str.upper().str.replace(" ", "", regex=False)
    wanted = "BANKNIFTY" if index_name.upper() == "BANKNIFTY" else "NIFTY"
    frame = frame[names.eq(wanted)].copy()
    if frame.empty:
        return pd.DataFrame(), f"No NFO index options found for {wanted} in the current instrument master."
    frame["_expiry"] = pd.to_datetime(frame[col("expiry")], errors="coerce")
    today = pd.Timestamp.now().normalize()
    frame = frame[frame["_expiry"] >= today]
    if frame.empty:
        return pd.DataFrame(), f"No unexpired option contracts found for {wanted}."
    expiry = frame["_expiry"].min()
    frame = frame[frame["_expiry"].eq(expiry)].copy()
    frame["_strike"] = pd.to_numeric(frame[col("strike")], errors="coerce")
    # Angel One instrument master strike values are often scaled by 100.
    max_strike = frame["_strike"].max()
    if pd.notna(max_strike) and max_strike > spot_price * 20:
        frame["_strike"] = frame["_strike"] / 100.0
    # Some official instrument-master versions omit a dedicated optiontype column;
    # derive CE/PE from the trading symbol suffix in that case.
    if optiontype_col is not None:
        frame["_otype"] = frame[optiontype_col].astype(str).str.upper()
    else:
        frame["_otype"] = frame[col("symbol")].astype(str).str.upper().str.extract(r"(CE|PE)$", expand=False)
    frame = frame[frame["_otype"].isin(["CE", "PE"])]
    frame["_distance"] = (frame["_strike"] - float(spot_price)).abs()
    strikes = sorted(frame.dropna(subset=["_strike"]).drop_duplicates("_strike")["_strike"].tolist(), key=lambda x: abs(x - spot_price))[:max_strikes]
    frame = frame[frame["_strike"].isin(strikes)].copy()
    frame = frame.sort_values("_strike")
    if frame.empty:
        return pd.DataFrame(), f"No strikes found around {wanted} spot price."
    # Keep request size bounded for broker API limits.
    frame = frame.head(50)
    token_list = frame[token_col].astype(str).tolist()
    try:
        response = client.getMarketData("FULL", {"NFO": token_list})
        payload = (response or {}).get("data") or {}
        fetched = payload.get("fetched") or []
        if not fetched:
            return pd.DataFrame(), f"Angel One returned no option quotes: {(response or {}).get('message', '')}"
        # SmartAPI quote payload key casing can vary; normalize tokens and quote keys.
        quote_by_token = {}
        for q in fetched:
            if not isinstance(q, dict):
                continue
            qnorm = {str(k).strip().lower(): v for k, v in q.items()}
            qtoken = qnorm.get("symboltoken", qnorm.get("token", qnorm.get("symbol_token", "")))
            if qtoken is not None and str(qtoken).strip():
                quote_by_token[str(qtoken).strip()] = qnorm
        records = []
        for _, row in frame.iterrows():
            token = str(row[token_col]).strip()
            q = quote_by_token.get(token, {})
            # Angel One commonly returns `opnInterest`; accept documented/common aliases too.
            oi = q.get("opninterest", q.get("openinterest", q.get("open_interest", q.get("oi", np.nan))))
            ltp = q.get("ltp", q.get("lasttradedprice", np.nan))
            records.append({
                "Expiry": str(row["_expiry"].date()),
                "Strike": float(row["_strike"]),
                "Type": row["_otype"],
                "Symbol": str(row[col("symbol")]),
                "Token": token,
                "LTP": safe_float(ltp),
                "OI": safe_float(oi),
            })
        result = pd.DataFrame(records)
        if result.empty or result["OI"].isna().all():
            return pd.DataFrame(), "Quotes arrived but OI was not included. Check Angel One market-data API permissions/response."
        now = datetime.now()
        cache_key = f"oi_{index_name}"
        history = st.session_state.get(cache_key, [])
        previous = history[-1]["quotes"] if history else {}
        # First successful snapshot has no baseline: report N/A, not a misleading zero.
        # From the second snapshot onward, compare only tokens present in the previous snapshot.
        result["Change in OI"] = result.apply(
            lambda r: (r["OI"] - previous[r["Token"]])
            if pd.notna(r["OI"]) and r["Token"] in previous else np.nan,
            axis=1,
        )
        quote_map = {r["Token"]: r["OI"] for _, r in result.iterrows() if pd.notna(r["OI"])}
        history.append({"time": now, "quotes": quote_map})
        history = [h for h in history if now - h["time"] <= timedelta(minutes=20)]
        st.session_state[cache_key] = history
        result.attrs["history"] = history
        return result, f"Angel One option quotes · expiry {expiry.date()} · OI change is since the previous successful refresh."
    except Exception as exc:
        return pd.DataFrame(), f"Angel One option quote request failed: {exc}"


def render_option_panel(index_name, spot_price):
    st.markdown(f"#### INTRADAY DATA — {index_name}")
    interval_label = st.radio(
        f"{index_name} interval", ["3 mins", "5 mins", "15 mins"],
        horizontal=True, key=f"oi_interval_{index_name}"
    )
    _run_cache = st.session_state.get("_option_chain_run_cache", {})
    if index_name in _run_cache:
        chain, note, _cached_spot = _run_cache[index_name]
        if np.isfinite(_cached_spot) and _cached_spot > 0:
            spot_price = float(_cached_spot)
    else:
        chain, note = get_option_chain_snapshot(index_name, spot_price)
        _run_cache[index_name] = (chain, note, spot_price)
        st.session_state["_option_chain_run_cache"] = _run_cache
    if chain.empty:
        st.session_state.pcr_bias = "N/A"
        st.info(note)
        st.caption("PCR/OI values are intentionally left unavailable until verified option-chain data is returned; they are not estimated from stock prices.")
        return
    call_oi = chain.loc[chain["Type"].eq("CE"), "OI"].sum(min_count=1)
    put_oi = chain.loc[chain["Type"].eq("PE"), "OI"].sum(min_count=1)
    pcr = (put_oi / call_oi) if pd.notna(call_oi) and call_oi > 0 and pd.notna(put_oi) else np.nan
    call_chg = chain.loc[chain["Type"].eq("CE"), "Change in OI"].sum(min_count=1)
    put_chg = chain.loc[chain["Type"].eq("PE"), "Change in OI"].sum(min_count=1)
    oi_diff = put_chg - call_chg if pd.notna(put_chg) and pd.notna(call_chg) else np.nan
    # Store a conservative option-context bias for the signal engine.
    st.session_state.pcr_bias = "Bullish" if pd.notna(pcr) and pd.notna(oi_diff) and pcr >= 1.15 and oi_diff > 0 else "Bearish" if pd.notna(pcr) and pd.notna(oi_diff) and pcr <= 0.85 and oi_diff < 0 else "Neutral"
    if pd.notna(pcr) and pd.notna(oi_diff):
        option_signal = "BUY" if pcr >= 1.15 and oi_diff > 0 else "SELL" if pcr <= 0.85 and oi_diff < 0 else "WAIT"
    else:
        option_signal = "WAIT"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spot reference", f"{spot_price:,.2f}")
    c2.metric("PCR (displayed strikes)", f"{pcr:.2f}" if pd.notna(pcr) else "N/A")
    c3.metric("Net OI change (PE−CE)", f"{oi_diff:,.0f}" if pd.notna(oi_diff) else "N/A")
    c4.metric("Option signal", option_signal)
    st.caption(note + f" Selected view: {interval_label}. Change in OI is calculated from consecutive successful Angel One snapshots. The first snapshot is the baseline (N/A); the next successful refresh calculates the difference. This is not broker-provided historical OI for the selected timeframe.")
    view = chain[["Strike", "Type", "LTP", "OI", "Change in OI"]].copy()
    view["LTP"] = view["LTP"].map(lambda x: round(x, 2) if pd.notna(x) else np.nan)
    st.dataframe(view, use_container_width=True, hide_index=True)
    history = st.session_state.get(f"oi_{index_name}", [])
    if len(history) >= 2:
        hist_df = pd.DataFrame([{"Time": h["time"].strftime("%H:%M:%S"), "Tokens with OI": len(h["quotes"])} for h in history])
        st.caption("Captured refresh history (current app session)")
        st.dataframe(hist_df.tail(10), use_container_width=True, hide_index=True)


def render_stock_scanner():
    st.subheader("📉 Major Stocks Scanner — CMP & % Change")
    st.caption("Yahoo mode shows underlying spot % change, not futures % change. For genuine futures % change, a valid Angel One futures contract/quote is required.")
    rows = []
    stocks = {
        "HDFC BANK": "HDFCBANK.NS", "RELIANCE": "RELIANCE.NS", "ICICI BANK": "ICICIBANK.NS",
        "INFY": "INFY.NS", "LT": "LT.NS", "TCS": "TCS.NS", "ITC": "ITC.NS",
        "BHARTI AIRTEL": "BHARTIARTL.NS", "AXIS BANK": "AXISBANK.NS", "SBI": "SBIN.NS",
    }
    for name, ticker in stocks.items():
        history = get_yahoo_history(ticker, period="5d", interval="5m")
        if history.empty:
            history = get_yahoo_history(ticker, period="5d", interval="1d")
        if history.empty:
            rows.append({"S.NO": len(rows)+1, "STOCKS": name, "CMP": np.nan, "% Change": np.nan, "Direction": "N/A"})
            continue
        cmp = float(history["Close"].iloc[-1])
        prev = float(history["Close"].iloc[-2]) if len(history) > 1 else np.nan
        change = (cmp / prev - 1) * 100 if pd.notna(prev) and prev else np.nan
        rows.append({"S.NO": len(rows)+1, "STOCKS": name, "CMP": cmp, "% Change": change,
                     "Direction": "BULLISH" if pd.notna(change) and change > 0 else "BEARISH" if pd.notna(change) and change < 0 else "FLAT"})
    table = pd.DataFrame(rows)
    def color_change(value):
        if pd.isna(value): return ""
        return "background-color: #8bd34f; color: black" if value >= 0 else "background-color: #ff2424; color: white"
    st.dataframe(
        table.style.format({"CMP": "{:.2f}", "% Change": "{:+.2f}%"}, na_rep="N/A").map(color_change, subset=["% Change"]),
        use_container_width=True, hide_index=True
    )


def market_cards(title, items):
    st.subheader(title)
    cols = st.columns(min(4, len(items)))
    for i, (name, ticker) in enumerate(items.items()):
        with cols[i % len(cols)]:
            data = get_yahoo_history(ticker, period="5d", interval="1d")
            if not data.empty and len(data) >= 2:
                last = float(data["Close"].iloc[-1])
                prev = float(data["Close"].iloc[-2])
                change = (last / prev - 1) * 100 if prev else 0
                st.metric(name, f"{last:,.2f}", f"{change:+.2f}%")
            else:
                st.metric(name, "N/A", "Data unavailable")

st.title("📈 Trading Intelligence Terminal")
st.caption("Research and paper-trading dashboard. Signals are rule-based, not guaranteed; verify data and risk before any real order.")

with st.sidebar:
    st.header("⚙️ Settings")
    if st.button("Logout", use_container_width=True):
        st.session_state.app_authenticated = False
        st.rerun()
    source = st.selectbox("Market data source", ["Yahoo Finance", "Angel One", "Demo (synthetic)"])
    st.session_state.source = source
    symbol_label = st.selectbox("Instrument", list(INDIAN_WATCHLIST.keys()))
    symbol = INDIAN_WATCHLIST[symbol_label]
    interval_label = st.selectbox("Chart timeframe", ["1 minute", "3 minutes", "5 minutes", "15 minutes", "Daily"], index=2, help="Yahoo Finance does not provide a true 3-minute interval here; its 3-minute selection is mapped to 5-minute data. Angel One uses THREE_MINUTE.")
    yahoo_interval = {"1 minute": "1m", "3 minutes": "5m", "5 minutes": "5m", "15 minutes": "15m", "Daily": "1d"}[interval_label]
    angel_interval = {"1 minute": "ONE_MINUTE", "3 minutes": "THREE_MINUTE", "5 minutes": "FIVE_MINUTE", "15 minutes": "FIFTEEN_MINUTE", "Daily": "ONE_DAY"}[interval_label]
    rr_multiple = st.selectbox("Risk : Reward target (T1)", [1, 2, 3, 5], index=1, format_func=lambda x: f"1:{x}")
    refresh_enabled = st.toggle("Auto-refresh", value=True, help="Keeps live quote snapshots updating so Change in OI can compare the latest snapshot with the previous one.")
    refresh_seconds = st.selectbox("Refresh interval", [10, 15, 30, 60], index=2)
    st.session_state.refresh_on = refresh_enabled
    if refresh_enabled:
        st_autorefresh(interval=refresh_seconds * 1000, key="market_refresh")
    st.divider()
    st.subheader("Angel One connection")
    st.caption("Credentials stay in this Streamlit session. Never share API secrets or deploy them in public code.")
    if not SMARTAPI_AVAILABLE:
        st.warning("SmartAPI package not loaded. Yahoo/demo mode is available.")
    st.text_input("API Key", key="angel_api", type="password")
    st.text_input("Client Code", key="angel_client")
    st.text_input("PIN", key="angel_pin", type="password")
    st.text_input("TOTP secret (not the 6-digit OTP)", key="angel_totp", type="password")
    if st.button("Connect Angel One", use_container_width=True):
        try:
            with st.spinner("Connecting to Angel One..."):
                connect_angel()
            st.success("Angel One session connected.")
        except Exception as exc:
            st.session_state.angel_status = "Connection failed"
            st.error(str(exc))
    st.caption(f"Status: {st.session_state.angel_status}")
    if st.button("Refresh instrument master", use_container_width=True):
        download_instrument_master.clear()
        st.session_state.angel_instruments = download_instrument_master()
        st.info(f"Instrument master rows: {len(st.session_state.angel_instruments)}")

if source == "Angel One":
    with st.spinner("Loading Angel One candles..."):
        candles, data_note = get_angel_history(symbol, angel_interval)
    if candles.empty:
        st.warning(data_note)
        st.info("Use Yahoo Finance temporarily, or check the Angel One login, API permissions, market hours and instrument availability.")
        data = get_yahoo_history(symbol, period="5d", interval=yahoo_interval)
        actual_source = "Yahoo fallback"
    else:
        data, actual_source = candles, data_note
elif source == "Demo (synthetic)":
    idx = pd.date_range(end=pd.Timestamp.now().floor("min"), periods=300, freq="5min")
    rng = np.random.default_rng(42)
    close = 22000 + np.cumsum(rng.normal(0, 22, len(idx)))
    op = np.r_[close[0], close[:-1]]
    data = pd.DataFrame({"Open": op, "High": np.maximum(op, close) + rng.uniform(1, 12, len(idx)),
                         "Low": np.minimum(op, close) - rng.uniform(1, 12, len(idx)),
                         "Close": close, "Volume": rng.integers(1000, 15000, len(idx))}, index=idx)
    actual_source = "Synthetic demo data — not live"
else:
    period = "60d" if yahoo_interval in ["1d", "15m"] else "5d"
    data = get_yahoo_history(symbol, period=period, interval=yahoo_interval)
    actual_source = "Yahoo Finance"

if data.empty:
    st.error("No candles received for this instrument/timeframe. Try another symbol, timeframe, or data source.")
    st.stop()

d = add_indicators(data)
# Fetch/calculate multi-timeframe context from the selected live data source.
# When Angel One is active, request each timeframe from Angel One (not Yahoo).
mtf_votes = []
_mtf_source_is_angel = source == "Angel One" and actual_source != "Yahoo fallback"
_mtf_fetch_plan = [("5m", "FIVE_MINUTE", "5d"), ("15m", "FIFTEEN_MINUTE", "5d")]
for _label, _angel_tf, _period in _mtf_fetch_plan:
    if _mtf_source_is_angel:
        _mtf_df, _mtf_note = get_angel_history(symbol, _angel_tf)
    else:
        _mtf_interval = {"FIVE_MINUTE": "5m", "FIFTEEN_MINUTE": "15m"}[_angel_tf]
        _mtf_df = get_yahoo_history(symbol, period=_period, interval=_mtf_interval)
    if not _mtf_df.empty and len(_mtf_df) >= 25:
        _fast = _mtf_df["Close"].ewm(span=9, adjust=False).mean().iloc[-1]
        _slow = _mtf_df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
        mtf_votes.append(1 if _fast > _slow else -1)
st.session_state.mtf_bias = "Bullish" if len(mtf_votes) >= 2 and sum(mtf_votes) == 2 else "Bearish" if len(mtf_votes) >= 2 and sum(mtf_votes) == -2 else "Mixed" if mtf_votes else "N/A"
_macro_changes, _macro_score, _macro_bias = build_macro_context()
st.session_state["macro_changes"] = _macro_changes
st.session_state["macro_bias"] = _macro_bias
# Snapshot NIFTY option-chain once per rerun so Overview PCR/OI and the panel use
# the same quote snapshot (prevents accidental double-polling from zeroing OI change).
st.session_state["_option_chain_run_cache"] = {}
try:
    if source == "Angel One" and actual_source != "Yahoo fallback":
        _nifty_spot_df, _nifty_spot_note = get_angel_history("^NSEI", "FIVE_MINUTE")
        _nifty_spot = float(_nifty_spot_df["Close"].iloc[-1]) if not _nifty_spot_df.empty else np.nan
    else:
        _nifty_spot_df = get_yahoo_history("^NSEI", period="5d", interval="5m")
        _nifty_spot = float(_nifty_spot_df["Close"].iloc[-1]) if not _nifty_spot_df.empty else np.nan
    if np.isfinite(_nifty_spot) and _nifty_spot > 0:
        _nifty_chain, _nifty_chain_note = get_option_chain_snapshot("NIFTY", _nifty_spot)
        st.session_state["_option_chain_run_cache"]["NIFTY"] = (_nifty_chain, _nifty_chain_note, _nifty_spot)
        if not _nifty_chain.empty:
            _call_oi = _nifty_chain.loc[_nifty_chain["Type"].eq("CE"), "OI"].sum(min_count=1)
            _put_oi = _nifty_chain.loc[_nifty_chain["Type"].eq("PE"), "OI"].sum(min_count=1)
            _call_chg = _nifty_chain.loc[_nifty_chain["Type"].eq("CE"), "Change in OI"].sum(min_count=1)
            _put_chg = _nifty_chain.loc[_nifty_chain["Type"].eq("PE"), "Change in OI"].sum(min_count=1)
            st.session_state["overview_pcr"] = (_put_oi / _call_oi) if pd.notna(_put_oi) and pd.notna(_call_oi) and _call_oi > 0 else np.nan
            st.session_state["overview_oi_change"] = (_put_chg - _call_chg) if pd.notna(_put_chg) and pd.notna(_call_chg) else np.nan
            _pcr_now = safe_float(st.session_state["overview_pcr"])
            _oi_now = safe_float(st.session_state["overview_oi_change"])
            st.session_state["pcr_bias"] = ("Bullish" if np.isfinite(_pcr_now) and np.isfinite(_oi_now) and _pcr_now >= 1.15 and _oi_now > 0
                else "Bearish" if np.isfinite(_pcr_now) and np.isfinite(_oi_now) and _pcr_now <= 0.85 and _oi_now < 0
                else "Neutral" if np.isfinite(_pcr_now) and np.isfinite(_oi_now) else "N/A")
        else:
            st.session_state["overview_pcr"] = np.nan
            st.session_state["overview_oi_change"] = np.nan
    else:
        st.session_state["overview_pcr"] = np.nan
        st.session_state["overview_oi_change"] = np.nan
except Exception:
    st.session_state["overview_pcr"] = np.nan
    st.session_state["overview_oi_change"] = np.nan
signal = analyze_signal(d, rr_multiple)
last = d.iloc[-1]
st.caption(f"Selected: **{symbol_label}** · Data source: **{actual_source}** · Last candle: {d.index[-1]}")

# Dashboard overview styled to match the user's reference screenshots.
# These are display-only metrics; confidence is an indicator score, not a guaranteed win probability.
bt_preview = calc_backtest(d)
_bt_returns = bt_preview["strategy_return"].dropna() if not bt_preview.empty else pd.Series(dtype=float)
_bt_wins = int((_bt_returns > 0).sum())
_bt_losses = int((_bt_returns < 0).sum())
_bt_total = len(_bt_returns)
_bt_win_rate = (_bt_wins / _bt_total * 100) if _bt_total else np.nan

def _metric_value(row, col, fmt="{:.2f}"):
    try:
        value = row.get(col, np.nan)
        return fmt.format(float(value)) if pd.notna(value) else "N/A"
    except Exception:
        return "N/A"

st.subheader("📊 Trading Dashboard Overview")
# Side-specific score shares are live rule-strength indicators, not statistically
# calibrated trade-winning probabilities. Keep backtest statistics in Backtest only.
_bull_score = int(signal.get("bullish_score", 0))
_bear_score = int(signal.get("bearish_score", 0))
_score_total = max(_bull_score + _bear_score, 1)
_bull_conf = round(100 * _bull_score / _score_total)
_bear_conf = round(100 * _bear_score / _score_total)
summary1 = st.columns(7)
summary1[0].metric("Current Price", f"{float(last['Close']):,.2f}")
summary1[1].metric("EMA20", _metric_value(last, "EMA20"))
summary1[2].metric("RSI (14)", _metric_value(last, "RSI", "{:.1f}"))
summary1[3].metric("MACD", _metric_value(last, "MACD"))
summary1[4].metric("Bullish Score", _bull_score, f"Score confidence {_bull_conf}%")
summary1[5].metric("Bearish Score", _bear_score, f"Score confidence {_bear_conf}%")
summary1[6].metric("Signal", signal.get("signal", "WAIT"), f"Signal confidence {signal.get('confidence', 0)}%")
summary2 = st.columns(7)
_vwap_label = "VWAP (price proxy)" if bool(last.get("VWAP_Is_Proxy", False)) else "VWAP"
summary2[0].metric(_vwap_label, _metric_value(last, "VWAP"))
summary2[1].metric("EMA50", _metric_value(last, "EMA50"))
summary2[2].metric("ATR (14)", _metric_value(last, "ATR"))
summary2[3].metric("ADX (14)", _metric_value(last, "ADX"))
summary2[4].metric("MTF Status", st.session_state.get("mtf_bias", "N/A"))
summary2[5].metric("Bullish score confidence", f"{_bull_conf}%")
summary2[6].metric("Bearish score confidence", f"{_bear_conf}%")
if bool(last.get("VWAP_Is_Proxy", False)):
    st.caption("VWAP note: this instrument's candle feed returned no usable volume; the displayed value is a price-only typical-price proxy, not true volume-weighted VWAP.")
summary3 = st.columns(7)
summary3[0].metric("Support", _metric_value(last, "Support"))
summary3[1].metric("Resistance", _metric_value(last, "Resistance"))
_summary_pcr = safe_float(st.session_state.get("overview_pcr", np.nan))
_summary_oi = safe_float(st.session_state.get("overview_oi_change", np.nan))
summary3[2].metric("NIFTY PCR", f"{_summary_pcr:.2f}" if np.isfinite(_summary_pcr) else "N/A")
summary3[3].metric("Net OI Change (PE−CE)", f"{_summary_oi:,.0f}" if np.isfinite(_summary_oi) else "N/A")
summary3[4].metric("Volume / Avg", _metric_value(last, "Vol_Ratio", "{:.2f}x"))
summary3[5].metric("Signal Score", f"{signal.get('score', 0):+d}")
summary3[6].metric("RR target", signal.get("rr", "N/A"))

# Macro snapshot requested by user: current daily changes, with source availability visible.
_macro_changes = st.session_state.get("macro_changes", {})
_macro_bias = st.session_state.get("macro_bias", "N/A")
st.markdown("#### 🌍 Global sentiment & macro effect")
macro_cols = st.columns(6)
for _col, _name in zip(macro_cols, ["S&P 500", "NASDAQ", "NIFTY 50", "Brent Crude", "USD/INR", "India VIX"]):
    _val = safe_float(_macro_changes.get(_name, np.nan))
    _ticker_val = f"{_val:+.2f}%" if np.isfinite(_val) else "N/A"
    _col.metric(_name, _ticker_val)
st.info(f"Macro sentiment: **{_macro_bias}** · Calculated from available daily index, Brent, USD/INR and VIX changes; missing feeds are excluded.")

st.markdown("#### 🎯 Suggested two entry prices")
_entry1, _entry2 = st.columns(2)
_px = safe_float(last.get("Close", np.nan)); _atr = safe_float(last.get("ATR", np.nan))
if signal.get("signal") in ("BUY", "SELL") and np.isfinite(_px):
    _offset = max(_atr * 0.25, _px * 0.0005) if np.isfinite(_atr) and _atr > 0 else _px * 0.001
    if signal["signal"] == "BUY":
        _entry_a, _entry_b = _px, _px - _offset
        _entry1.metric("Entry 1 — breakout/current reference", f"{_entry_a:,.2f}")
        _entry2.metric("Entry 2 — pullback reference", f"{_entry_b:,.2f}")
    else:
        _entry_a, _entry_b = _px, _px + _offset
        _entry1.metric("Entry 1 — breakdown/current reference", f"{_entry_a:,.2f}")
        _entry2.metric("Entry 2 — pullback/retest reference", f"{_entry_b:,.2f}")
else:
    _entry1.metric("Entry 1", "WAIT — no confirmed setup")
    _entry2.metric("Entry 2", "WAIT — no confirmed setup")
st.caption("Entry prices are calculated reference levels, not guaranteed fills or broker orders. Wait for price/volume confirmation; avoid treating them as certain predictions.")

st.markdown("#### 🧪 Backtest winning details")
_bt_cols = st.columns(5)
_bt_cols[0].metric("Tested bars", _bt_total if _bt_total else "N/A")
_bt_cols[1].metric("Positive bars", _bt_wins if _bt_total else "N/A")
_bt_cols[2].metric("Negative bars", _bt_losses if _bt_total else "N/A")
_bt_cols[3].metric("Positive-bar ratio", f"{_bt_win_rate:.1f}%" if np.isfinite(_bt_win_rate) else "N/A")
_bt_cols[4].metric("Backtest net return", f"{(bt_preview['equity_curve'].dropna().iloc[-1]-1)*100:.2f}%" if not bt_preview.empty and not bt_preview['equity_curve'].dropna().empty else "N/A")
st.caption("Backtest winning percentage and results belong to this section only. These are historical EMA 9/20 proxy statistics, not a calibrated future win probability for the current signal.")

st.subheader("📊 Multi Timeframe Alignment")
_mtf_rows = []
_mtf_display_plan = [
    ("1m", "ONE_MINUTE", "1m", "1d"),
    ("3m", "THREE_MINUTE", "5m", "5d"),
    ("5m", "FIVE_MINUTE", "5m", "5d"),
    ("15m", "FIFTEEN_MINUTE", "15m", "5d"),
]
for _label, _angel_tf, _yahoo_interval, _period in _mtf_display_plan:
    if source == "Angel One" and actual_source != "Yahoo fallback":
        _frame, _mtf_note = get_angel_history(symbol, _angel_tf)
    else:
        _frame = get_yahoo_history(symbol, period=_period, interval=_yahoo_interval)
    if not _frame.empty and len(_frame) >= 20:
        _f = _frame["Close"].ewm(span=9, adjust=False).mean().iloc[-1]
        _sl = _frame["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
        _trend = "Bullish" if _f > _sl else "Bearish" if _f < _sl else "Neutral"
        _close = float(_frame["Close"].iloc[-1])
    else:
        _trend, _close = "N/A", np.nan
    _mtf_rows.append({"Timeframe": _label, "Trend": _trend, "Latest Close": f"{_close:,.2f}" if np.isfinite(_close) else "N/A"})
_mtf_table = pd.DataFrame(_mtf_rows)
st.dataframe(_mtf_table, use_container_width=True, hide_index=True)
_bull_tf = sum(r["Trend"] == "Bullish" for r in _mtf_rows)
_bear_tf = sum(r["Trend"] == "Bearish" for r in _mtf_rows)
_mtf_status = "Strong Bullish Alignment" if _bull_tf >= 3 else "Strong Bearish Alignment" if _bear_tf >= 3 else "Mixed / Partial Alignment"
if _bull_tf >= 3:
    st.success(f"🔥 {_mtf_status} · Bullish TF: {_bull_tf} · Bearish TF: {_bear_tf}")
elif _bear_tf >= 3:
    st.error(f"🧊 {_mtf_status} · Bullish TF: {_bull_tf} · Bearish TF: {_bear_tf}")
else:
    st.info(f"ℹ️ {_mtf_status} · Bullish TF: {_bull_tf} · Bearish TF: {_bear_tf}")

# Global / Indian market snapshot
with st.expander("🌍 Global markets and macro instruments", expanded=True):
    market_cards("Global / macro performance (latest daily close)", GLOBAL_WATCHLIST)
with st.expander("🇮🇳 Major Indian stocks — Banks, IT, Auto and index leaders", expanded=True):
    market_cards("Indian market performance (latest daily close)", {k:v for k,v in INDIAN_WATCHLIST.items() if k not in ["NIFTY 50", "BANK NIFTY", "SENSEX"]})

render_stock_scanner()

st.subheader("🧭 NIFTY & BANKNIFTY — Option Chain / Intraday OI")
idx1, idx2 = st.columns(2)
with idx1:
    nifty_hist = get_yahoo_history("^NSEI", period="5d", interval="5m")
    nifty_spot = float(nifty_hist["Close"].iloc[-1]) if not nifty_hist.empty else 0.0
    render_option_panel("NIFTY", nifty_spot)
with idx2:
    bank_hist = get_yahoo_history("^NSEBANK", period="5d", interval="5m")
    bank_spot = float(bank_hist["Close"].iloc[-1]) if not bank_hist.empty else 0.0
    render_option_panel("BANKNIFTY", bank_spot)

# Option-chain context is now available for this refresh.
signal = analyze_signal(d, rr_multiple)

st.divider()
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Last price", f"{last['Close']:,.2f}")
m2.metric("Signal", signal["signal"], f"Score {signal['score']:+d}")
m3.metric("RSI (14)", f"{last['RSI']:.1f}" if pd.notna(last["RSI"]) else "N/A")
m4.metric("ADX (14)", f"{last['ADX']:.1f}" if pd.notna(last["ADX"]) else "N/A")
m5.metric("Volume / avg", f"{last['Vol_Ratio']:.2f}x" if pd.notna(last["Vol_Ratio"]) else "N/A")

if signal["signal"] != "WAIT":
    a, b, c, e, f = st.columns(5)
    a.metric("Entry reference", f"{signal['entry']:,.2f}")
    b.metric("Stop loss", f"{signal['sl']:,.2f}")
    c.metric("Target 1", f"{signal['t1']:,.2f}")
    e.metric("Target 2", f"{signal['t2']:,.2f}")
    f.metric("Target 3", f"{signal['t3']:,.2f}")
    st.caption("ATR-based reference levels only. They are not broker orders and do not account for slippage, charges, gaps or liquidity.")
else:
    st.info("Current combined score is mixed. WAIT is not a trade recommendation.")

left, right = st.columns([2, 1])
with left:
    st.subheader("Candlestick chart + EMA + Bollinger Bands")
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Candles"))
    for col, name in [("EMA9","EMA 9"), ("EMA20","EMA 20"), ("EMA50","EMA 50"), ("BB_Upper","BB Upper"), ("BB_Lower","BB Lower"), ("VWAP","VWAP")]:
        fig.add_trace(go.Scatter(x=d.index, y=d[col], mode="lines", name=name))
    fig.update_layout(height=580, xaxis_rangeslider_visible=False, legend_orientation="h", margin=dict(l=10,r=10,t=30,b=10))
    # Annotate every detected candle pattern on chart; multi-candle patterns label the confirmation candle.
    pa = add_price_action_columns(d)
    marked = pa[pa["Pattern"].astype(str).str.len() > 0]
    if not marked.empty:
        fig.add_trace(go.Scatter(x=marked.index, y=marked["High"] * 1.002,
            mode="markers+text", text=marked["Pattern"].str.slice(0, 38), textposition="top center",
            marker=dict(symbol="diamond", size=7), name="Candlestick patterns",
            hovertemplate="Time: %{x}<br>Pattern: %{text}<extra></extra>"))
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Pattern labels are heuristic; some patterns need trend/context confirmation. Multi-candle patterns are labelled on their confirmation candle.")
with right:
    st.subheader("Combined signal checks")
    for item in signal.get("reasons", []):
        st.write(("🟢 " if "above" in item.lower() or "bullish" in item.lower() else "⚪ ") + item)
    st.subheader("Candlestick patterns")
    for pattern in signal.get("patterns", []):
        st.write("• " + pattern)
    st.metric("Rule-score strength (not win probability)", f"{signal.get('confidence', 0)}%")
    sc1, sc2 = st.columns(2)
    sc1.metric("Bullish score", signal.get("bullish_score", 0))
    sc2.metric("Bearish score", signal.get("bearish_score", 0))
    st.write(f"**Selected RR:** {signal.get('rr', 'N/A')}")
    st.write(f"**ADR (20 bars):** {last['ADR']:.2f}%" if pd.notna(last.get("ADR", np.nan)) else "ADR: N/A")
    st.write(f"**MTF context:** {st.session_state.get('mtf_bias', 'N/A')} · **PCR context:** {st.session_state.get('pcr_bias', 'N/A')}")
    st.caption("This percentage is a normalized indicator score, not a tested probability of winning a trade.")
    st.write(f"**Trend:** {'Bullish' if signal['score'] >= 3 else 'Bearish' if signal['score'] <= -3 else 'Sideways / mixed'}")
    st.write(f"**Support (rolling 20):** {last['Support']:.2f}" if pd.notna(last["Support"]) else "Support: N/A")
    st.write(f"**Resistance (rolling 20):** {last['Resistance']:.2f}" if pd.notna(last["Resistance"]) else "Resistance: N/A")
    st.write(f"**Previous pivot:** {last['Pivot']:.2f}" if pd.notna(last["Pivot"]) else "Pivot: N/A")

st.subheader("🕯️ Candle-by-Candle Candlestick Pattern & Price Action")
pa_all = add_price_action_columns(d)
show_n = st.selectbox("Candles to inspect", [20, 50, 100, 200], index=1, key="candle_rows")
pa_view = pa_all.tail(show_n).copy()
pa_view.insert(0, "Time", pa_view.index.astype(str))
cols = ["Time", "Open", "High", "Low", "Close", "Candle Type", "Body", "Upper Wick", "Lower Wick", "Body %", "Pattern", "Price Action", "Structure", "CHoCH", "Liquidity Sweep", "Volume Status", "VWAP Position"]
st.dataframe(pa_view[cols].sort_values("Time", ascending=False), use_container_width=True, hide_index=True)
st.caption("BOS labels use a simple rolling 10-candle high/low break. CHoCH/liquidity sweeps are not asserted without a swing-point confirmation algorithm; pattern names such as 'candidate' require context and are not standalone trade signals.")

st.subheader("Momentum and volume")
ind1, ind2 = st.columns(2)
with ind1:
    fig_rsi = go.Figure()
    fig_rsi.add_trace(go.Scatter(x=d.index, y=d["RSI"], name="RSI"))
    fig_rsi.add_hline(y=70, line_dash="dash")
    fig_rsi.add_hline(y=30, line_dash="dash")
    fig_rsi.update_layout(height=260, title="RSI (14)", yaxis_range=[0,100])
    st.plotly_chart(fig_rsi, use_container_width=True)
with ind2:
    fig_macd = go.Figure()
    fig_macd.add_trace(go.Scatter(x=d.index, y=d["MACD"], name="MACD"))
    fig_macd.add_trace(go.Scatter(x=d.index, y=d["MACD_Signal"], name="Signal"))
    fig_macd.update_layout(height=260, title="MACD")
    st.plotly_chart(fig_macd, use_container_width=True)

st.subheader("Timeframe / trend context")
st.info(f"Signal-integrated higher-timeframe context (5m + 15m): {st.session_state.get('mtf_bias', 'N/A')}")
mtf_cols = st.columns(3)
for col, (label, interval, period) in zip(mtf_cols, [("1-minute", "1m", "1d"), ("5-minute", "5m", "5d"), ("15-minute", "15m", "5d")]):
    with col:
        mtf = get_yahoo_history(symbol, period=period, interval=interval)
        if not mtf.empty and len(mtf) >= 20:
            fast = mtf["Close"].ewm(span=9, adjust=False).mean().iloc[-1]
            slow = mtf["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
            st.metric(label, "Bullish" if fast > slow else "Bearish", f"Close {mtf['Close'].iloc[-1]:,.2f}")
        else:
            st.metric(label, "N/A", "Data unavailable")

st.subheader("📊 Backtest (EMA 9/20 crossover proxy, full bar series)")
bt = bt_preview
if bt.empty:
    st.info("Not enough historical candles for this backtest.")
else:
    returns = bt["strategy_return"].dropna()
    equity = bt["equity_curve"].dropna()
    wins = int((returns > 0).sum()); losses = int((returns < 0).sum()); total = len(returns)
    win_rate = wins / total * 100 if total else 0
    gross_profit = returns[returns > 0].sum(); gross_loss = abs(returns[returns < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.nan
    b1,b2,b3,b4,b5 = st.columns(5)
    b1.metric("Bars tested", total)
    b2.metric("Positive bars", wins)
    b3.metric("Positive-bar ratio", f"{win_rate:.1f}%")
    b4.metric("Net strategy return", f"{(equity.iloc[-1]-1)*100:.2f}%" if len(equity) else "N/A")
    b5.metric("Max drawdown", f"{bt['drawdown'].min()*100:.2f}%")
    st.caption(f"Profit factor (bar-level proxy): {profit_factor:.2f}" if pd.notna(profit_factor) else "Profit factor: N/A. Educational proxy; it is not the full signal engine and does not model realistic fills, options, stops, taxes or position sizing.")
    st.dataframe(bt.tail(30)[["Close", "fast", "slow", "Side", "strategy_return", "equity_curve", "drawdown", "Trade Change"]], use_container_width=True)

st.subheader("🧾 Paper trading — no real orders are sent")
p1,p2,p3 = st.columns(3)
with p1:
    qty = st.number_input("Quantity", min_value=1, value=1, step=1)
with p2:
    st.metric("Paper cash", f"₹{st.session_state.paper_cash:,.2f}")
with p3:
    st.metric("Realized paper P&L", f"₹{st.session_state.paper_realized:,.2f}")

pos = st.session_state.paper_position
if pos:
    side_mult = 1 if pos["side"] == "BUY" else -1
    px = float(last["Close"])
    atr_now = safe_float(last.get("ATR", np.nan), px * 0.005)
    if not np.isfinite(atr_now) or atr_now <= 0: atr_now = px * 0.005
    # Paper-only ATR trailing stop: it tightens in the favorable direction and never loosens.
    pos.setdefault("peak", float(pos["entry"]))
    pos.setdefault("trail_sl", float(pos["entry"]) - side_mult * 1.5 * atr_now)
    pos["peak"] = max(float(pos["peak"]), px) if side_mult == 1 else min(float(pos["peak"]), px)
    candidate_sl = float(pos["peak"]) - side_mult * 1.5 * atr_now
    pos["trail_sl"] = max(float(pos["trail_sl"]), candidate_sl) if side_mult == 1 else min(float(pos["trail_sl"]), candidate_sl)
    st.session_state.paper_position = pos
    current_pnl = (px - pos["entry"]) * pos["qty"] * side_mult
    st.info(f"Open paper position: {pos['side']} {pos['qty']} × {pos['symbol']} @ {pos['entry']:.2f} · ATR trailing SL: {pos['trail_sl']:.2f}")
    st.metric("Unrealized paper P&L", f"₹{current_pnl:,.2f}")
    stop_hit = (side_mult == 1 and px <= pos["trail_sl"]) or (side_mult == -1 and px >= pos["trail_sl"])
    if stop_hit:
        journal = load_journal()
        pnl = (pos["trail_sl"] - pos["entry"]) * pos["qty"] * side_mult
        row = {"time": datetime.now().isoformat(timespec="seconds"), "symbol": pos["symbol"], "side": pos["side"], "entry": pos["entry"], "exit": float(pos["trail_sl"]), "quantity": pos["qty"], "pnl": pnl, "notes": "Paper ATR trailing stop"}
        save_journal(pd.concat([journal, pd.DataFrame([row])], ignore_index=True))
        st.session_state.paper_realized += pnl
        st.session_state.paper_position = None
        st.warning("Paper position closed by ATR trailing stop. No broker order was sent.")
        st.rerun()
    if st.button("Close paper position"):
        exit_price = float(last["Close"])
        pnl = (exit_price - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "BUY" else -1)
        journal = load_journal()
        row = {"time": datetime.now().isoformat(timespec="seconds"), "symbol": pos["symbol"], "side": pos["side"],
               "entry": pos["entry"], "exit": exit_price, "quantity": pos["qty"], "pnl": pnl, "notes": "Paper close"}
        journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
        save_journal(journal)
        st.session_state.paper_realized += pnl
        st.session_state.paper_position = None
        st.rerun()
else:
    buy_col, sell_col = st.columns(2)
    with buy_col:
        if st.button("Open paper BUY", use_container_width=True):
            st.session_state.paper_position = {"side": "BUY", "symbol": symbol_label, "entry": float(last["Close"]), "qty": int(qty), "peak": float(last["Close"]), "trail_sl": float(last["Close"])-1.5*max(safe_float(last.get("ATR", np.nan), float(last["Close"])*0.005), 0.01)}
            st.rerun()
    with sell_col:
        if st.button("Open paper SELL", use_container_width=True):
            st.session_state.paper_position = {"side": "SELL", "symbol": symbol_label, "entry": float(last["Close"]), "qty": int(qty), "peak": float(last["Close"]), "trail_sl": float(last["Close"])+1.5*max(safe_float(last.get("ATR", np.nan), float(last["Close"])*0.005), 0.01)}
            st.rerun()

st.subheader("🧭 FII / DII flow tracker (manual verified input)")
st.caption("No dependable official live FII/DII feed is configured in this app. Enter values from a verified exchange/depository report; the dashboard will not fabricate them.")
with st.expander("Enter today's reported cash-market flows", expanded=False):
    fii_col, dii_col, date_col = st.columns(3)
    with fii_col: fii_value = st.number_input("FII net flow (₹ crore)", value=0.0, step=100.0, key="manual_fii")
    with dii_col: dii_value = st.number_input("DII net flow (₹ crore)", value=0.0, step=100.0, key="manual_dii")
    with date_col: flow_date = st.date_input("Report date", value=datetime.now().date(), key="manual_flow_date")
    if st.button("Save flow snapshot"):
        st.session_state["flow_snapshot"] = {"date": str(flow_date), "FII net (₹ cr)": fii_value, "DII net (₹ cr)": dii_value, "Combined (₹ cr)": fii_value+dii_value}
    snap = st.session_state.get("flow_snapshot")
    if snap:
        f1,f2,f3 = st.columns(3)
        f1.metric("FII net (₹ cr)", f"{snap['FII net (₹ cr)']:,.0f}")
        f2.metric("DII net (₹ cr)", f"{snap['DII net (₹ cr)']:,.0f}")
        f3.metric("Combined net (₹ cr)", f"{snap['Combined (₹ cr)']:,.0f}")
        st.caption(f"Manually entered report date: {snap['date']}")

st.subheader("📰 Headline sentiment (best-effort Yahoo Finance feed)")
try:
    news_items = yf.Ticker(symbol).news or []
    news_rows = []
    positive_terms = {"beats", "surge", "growth", "profit", "upgrade", "strong", "record", "rally", "buyback", "bullish", "gains"}
    negative_terms = {"miss", "fall", "drop", "loss", "downgrade", "weak", "fraud", "selloff", "bearish", "decline", "cuts"}
    for item in news_items[:12]:
        content = item.get("content", item) if isinstance(item, dict) else {}
        title = content.get("title", "")
        provider = content.get("provider", {}) or {}
        publisher = provider.get("displayName", item.get("publisher", "Yahoo Finance")) if isinstance(provider, dict) else str(provider)
        words = set(str(title).lower().replace(":", " ").replace(",", " ").split())
        pos_n, neg_n = len(words & positive_terms), len(words & negative_terms)
        sent = "Positive" if pos_n > neg_n else "Negative" if neg_n > pos_n else "Neutral / unclear"
        news_rows.append({"Headline": title, "Publisher": publisher, "Heuristic sentiment": sent})
    if news_rows:
        news_df = pd.DataFrame(news_rows)
        pos_count = int((news_df["Heuristic sentiment"] == "Positive").sum())
        neg_count = int((news_df["Heuristic sentiment"] == "Negative").sum())
        st.metric("Headline tone (not a trade signal)", "Positive" if pos_count > neg_count else "Negative" if neg_count > pos_count else "Mixed / neutral", f"{pos_count} positive · {neg_count} negative")
        st.dataframe(news_df, use_container_width=True, hide_index=True)
    else:
        st.info("No Yahoo headlines were returned for this instrument.")
except Exception as exc:
    st.info(f"News feed unavailable for this instrument: {exc}")

st.subheader("Trade journal")
journal = load_journal()
if not journal.empty:
    wins_j = int((pd.to_numeric(journal["pnl"], errors="coerce") > 0).sum())
    closed = len(journal)
    j1,j2,j3 = st.columns(3)
    j1.metric("Closed paper trades", closed)
    j2.metric("Winning trades", wins_j)
    j3.metric("Journal win rate", f"{wins_j/closed*100:.1f}%" if closed else "N/A")
    st.dataframe(journal.tail(100), use_container_width=True)
    st.download_button("Download trade journal CSV", journal.to_csv(index=False).encode("utf-8"), "trade_journal.csv", "text/csv")
else:
    st.caption("No closed paper trades yet. Paper trades are stored in trade_journal.csv beside app.py.")

with st.expander("Data availability notes / features not falsely estimated"):
    st.markdown("""
- **Angel One tokens:** the app downloads the public instrument master and tries to resolve the selected instrument automatically. Not every Yahoo label maps exactly to an Angel One symbol; index symbols and derivative contracts can need specific mapping.
- **Live data:** refresh is polling, not a guaranteed zero-delay feed. Yahoo intraday history can be delayed, restricted, or unavailable. Angel One candle data depends on broker API access and rate limits.
- **PCR / option-chain OI:** best-effort Angel One snapshot; included in scoring only when verified quote fields are returned. The context is snapshot-based, not a historical 3/5/15-minute feed.
- **FII/DII:** manual verified-input tracker is included; it is not a live official feed. Yahoo headline sentiment is a simple keyword heuristic, not NLP or an official news calendar.
- **Real orders:** deliberately not implemented. Paper-trading buttons only record simulated positions.
- **Win chance:** a real probability requires out-of-sample validation. The displayed rule-score strength is not a calibrated win probability.
- **Trailing SL:** implemented for paper positions only; no broker orders are sent.
- **Live ticks/orders:** polling candles are not zero-delay WebSocket ticks. Live broker order placement is intentionally not implemented; verify everything before any manual trade.
""")
st.caption(f"Updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · For education/research only; not investment advice.")
