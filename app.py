import json
import time
import os
import hashlib
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
        "last_data": None, "source": "Yahoo Finance", "refresh_on": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_state()

# Basic app gate. Configure APP_USERNAME and APP_PASSWORD in deployment secrets/env.
# Defaults are for first-run convenience only and MUST be changed before sharing publicly.
def _configured_secret(name, default):
    try:
        value = st.secrets.get(name, None)
        if value is not None and str(value).strip():
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)

APP_USERNAME = _configured_secret("APP_USERNAME", "admin")
APP_PASSWORD = _configured_secret("APP_PASSWORD", "admin123")

def require_login():
    if st.session_state.get("authenticated", False):
        with st.sidebar:
            st.caption("Login: authenticated")
            if st.button("Log out", use_container_width=True):
                st.session_state["authenticated"] = False
                st.rerun()
        return
    st.title("🔐 Trading Intelligence Terminal — Login")
    st.caption("Set APP_USERNAME and APP_PASSWORD in Streamlit secrets/environment before public deployment.")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", use_container_width=True)
    if submitted:
        if username == APP_USERNAME and hashlib.sha256(password.encode()).hexdigest() == hashlib.sha256(APP_PASSWORD.encode()).hexdigest():
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect username or password.")
    st.stop()

require_login()

def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default

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
    master = st.session_state.angel_instruments
    if master is None or master.empty:
        master = download_instrument_master()
        st.session_state.angel_instruments = master
    if master.empty:
        return None
    cols = {str(c).lower(): c for c in master.columns}
    symbol_col = cols.get("symboltoken")
    exch_col = cols.get("exch_seg")
    name_col = cols.get("name")
    tsym_col = cols.get("symbol")
    if not symbol_col or not exch_col or not tsym_col:
        return None
    frame = master[master[exch_col].astype(str).str.upper() == exchange.upper()].copy()
    wanted = symbol.upper().replace(".NS", "")
    exact = frame[frame[tsym_col].astype(str).str.upper() == wanted]
    if exact.empty and name_col:
        exact = frame[frame[name_col].astype(str).str.upper() == wanted]
    if exact.empty:
        # Yahoo labels are mapped to common Angel One trading symbols.
        aliases = {"^NSEI": "NIFTY", "^NSEBANK": "BANKNIFTY", "^BSESN": "SENSEX"}
        wanted = aliases.get(symbol, wanted)
        exact = frame[frame[tsym_col].astype(str).str.upper().str.startswith(wanted)]
    if exact.empty:
        return None
    return str(exact.iloc[0][symbol_col]), str(exact.iloc[0][tsym_col])

def get_angel_history(symbol, interval="FIVE_MINUTE", exchange="NSE"):
    client = st.session_state.angel_session
    if client is None:
        return pd.DataFrame(), "Angel One is not connected."
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
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.rolling(14).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    d["RSI"] = 100 - (100 / (1 + rs))
    low14, high14 = low.rolling(14).min(), high.rolling(14).max()
    d["Stoch_K"] = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    d["Stoch_D"] = d["Stoch_K"].rolling(3).mean()
    typical = (high + low + close) / 3
    cumulative_vol = volume.replace(0, np.nan).cumsum()
    d["VWAP"] = (typical * volume).cumsum() / cumulative_vol
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
    # Previous-candle levels are available on intraday feeds; daily timeframe means prior day.
    d["Previous High"] = high.shift(1)
    d["Previous Low"] = low.shift(1)
    d["Previous Close"] = close.shift(1)
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
    return x

def analyze_signal(d, rr_multiple=1.0):
    if len(d) < 30:
        return {"signal": "WAIT", "score": 0, "confidence": 0, "reason": "Not enough candles for stable indicators."}
    a = d.iloc[-1]
    score, reasons = 0, []
    checks = [
        (a["EMA9"] > a["EMA20"], 1, "EMA9 above EMA20", "EMA9 below EMA20"),
        (a["Close"] > a["VWAP"], 1, "Price above VWAP", "Price below VWAP"),
        (a["MACD"] > a["MACD_Signal"], 1, "MACD bullish", "MACD bearish"),
        (a["RSI"] > 52, 1, "RSI bullish", "RSI not bullish"),
        (a["Plus_DI"] > a["Minus_DI"] and a["ADX"] >= 18, 1, "ADX/DI bullish trend", "ADX/DI not bullish"),
        (a["Close"] > a["BB_Mid"], 1, "Above Bollinger midline", "Below Bollinger midline"),
        (a["Vol_Ratio"] >= 1.2, 1, "Volume above 1.2x average", "No strong volume expansion"),
    ]
    for condition, weight, bull, bear in checks:
        if pd.notna(condition) and condition:
            score += weight
            reasons.append(bull)
        else:
            score -= weight
            reasons.append(bear)
    patterns = candle_patterns(d)
    # Normalize pattern names so detector labels and scoring labels cannot silently disagree.
    pattern_text = " ".join(patterns).lower()
    bullish_pattern = any(term in pattern_text for term in [
        "bullish engulfing", "hammer / hanging man", "inverted hammer",
        "piercing line", "morning star", "three white soldiers", "tweezer bottom",
        "bullish marubozu"
    ])
    bearish_pattern = any(term in pattern_text for term in [
        "bearish engulfing", "shooting star", "dark cloud cover", "evening star",
        "three black crows", "tweezer top", "bearish marubozu"
    ])
    if bullish_pattern and not bearish_pattern:
        score += 1
        reasons.append("Bullish candlestick pattern confirmation")
    elif bearish_pattern and not bullish_pattern:
        score -= 1
        reasons.append("Bearish candlestick pattern confirmation")
    signal = "BUY" if score >= 3 else "SELL" if score <= -3 else "WAIT"
    # This is a heuristic score, NOT a statistically calibrated win probability.
    confidence = int(min(95, abs(score) / 8 * 100))
    entry = float(a["Close"])
    atr = safe_float(a["ATR"], entry * 0.005)
    if not np.isfinite(atr) or atr <= 0:
        atr = entry * 0.005
    risk_distance = 1.5 * atr
    rr_multiple = float(rr_multiple) if float(rr_multiple) > 0 else 1.0
    if signal == "BUY":
        sl = entry - risk_distance
        t1, t2, t3 = entry + risk_distance*rr_multiple, entry + risk_distance*rr_multiple*2, entry + risk_distance*rr_multiple*3
    elif signal == "SELL":
        sl = entry + risk_distance
        t1, t2, t3 = entry - risk_distance*rr_multiple, entry - risk_distance*rr_multiple*2, entry - risk_distance*rr_multiple*3
    else:
        sl = t1 = t2 = t3 = np.nan
    return {"signal": signal, "score": score, "confidence": confidence, "reasons": reasons,
            "entry": entry, "sl": sl, "t1": t1, "t2": t2, "t3": t3, "atr": atr,
            "rr": f"1:{rr_multiple:g} (T1); T2/T3 are 2x/3x selected RR", "patterns": patterns}

def calc_backtest(d, cost_bps=10):
    """Crossover proxy backtest with next-bar returns and estimated costs; not fill-level simulation."""
    if len(d) < 40:
        return pd.DataFrame()
    x = d.copy()
    x["fast"] = x["Close"].ewm(span=9, adjust=False).mean()
    x["slow"] = x["Close"].ewm(span=20, adjust=False).mean()
    x["position"] = np.where(x["fast"] > x["slow"], 1, -1)
    x["position_prev"] = x["position"].shift(1).fillna(0)
    x["gross_return"] = x["position_prev"] * x["Close"].pct_change()
    x["turnover"] = (x["position"] - x["position_prev"]).abs().fillna(0)
    x["estimated_cost"] = x["turnover"] * (float(cost_bps) / 10000.0)
    x["net_return"] = x["gross_return"].fillna(0) - x["estimated_cost"]
    x["equity_curve"] = (1 + x["net_return"]).cumprod()
    x["drawdown_pct"] = (x["equity_curve"] / x["equity_curve"].cummax() - 1) * 100
    x["trade_change"] = x["position"].ne(x["position"].shift(1))
    trades = x.loc[x["trade_change"], ["Close", "position", "gross_return", "estimated_cost", "net_return", "equity_curve", "drawdown_pct"]].copy()
    trades["P&L % (estimated net, bar proxy)"] = trades["net_return"] * 100
    trades["Side"] = np.where(trades["position"] > 0, "LONG", "SHORT")
    return trades.tail(200)

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
    needed = ["symbol", "name", "exch_seg", "instrumenttype", "expiry", "strike", "optiontype", "token"]
    if not all(k in lower for k in needed):
        return pd.DataFrame(), "Instrument master schema did not include the required option fields."
    def col(k): return lower[k]
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
    frame["_otype"] = frame[col("optiontype")].astype(str).str.upper()
    frame = frame[frame["_otype"].isin(["CE", "PE"])]
    frame["_distance"] = (frame["_strike"] - float(spot_price)).abs()
    strikes = sorted(frame.dropna(subset=["_strike"]).drop_duplicates("_strike")["_strike"].tolist(), key=lambda x: abs(x - spot_price))[:max_strikes]
    frame = frame[frame["_strike"].isin(strikes)].copy()
    frame = frame.sort_values("_strike")
    if frame.empty:
        return pd.DataFrame(), f"No strikes found around {wanted} spot price."
    # Keep request size bounded for broker API limits.
    frame = frame.head(50)
    token_list = frame[col("token")].astype(str).tolist()
    try:
        response = client.getMarketData("FULL", {"NFO": token_list})
        payload = (response or {}).get("data") or {}
        fetched = payload.get("fetched") or []
        if not fetched:
            return pd.DataFrame(), f"Angel One returned no option quotes: {(response or {}).get('message', '')}"
        quote_by_token = {str(q.get("symbolToken", q.get("token", ""))): q for q in fetched}
        records = []
        for _, row in frame.iterrows():
            token = str(row[col("token")])
            q = quote_by_token.get(token, {})
            oi = q.get("opnInterest", q.get("openInterest", q.get("open_interest", np.nan)))
            ltp = q.get("ltp", np.nan)
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
        result["Change in OI"] = result.apply(
            lambda r: r["OI"] - previous.get(r["Token"], r["OI"]) if pd.notna(r["OI"]) else np.nan, axis=1
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
    chain, note = get_option_chain_snapshot(index_name, spot_price)
    if chain.empty:
        st.info(note)
        st.caption("PCR/OI values are intentionally left unavailable until verified option-chain data is returned; they are not estimated from stock prices.")
        return
    call_oi = chain.loc[chain["Type"].eq("CE"), "OI"].sum(min_count=1)
    put_oi = chain.loc[chain["Type"].eq("PE"), "OI"].sum(min_count=1)
    pcr = (put_oi / call_oi) if pd.notna(call_oi) and call_oi > 0 and pd.notna(put_oi) else np.nan
    call_chg = chain.loc[chain["Type"].eq("CE"), "Change in OI"].sum(min_count=1)
    put_chg = chain.loc[chain["Type"].eq("PE"), "Change in OI"].sum(min_count=1)
    oi_diff = put_chg - call_chg if pd.notna(put_chg) and pd.notna(call_chg) else np.nan
    # Conservative heuristic; not a standalone option recommendation.
    if pd.notna(pcr) and pd.notna(oi_diff):
        option_signal = "BUY" if pcr >= 1.15 and oi_diff > 0 else "SELL" if pcr <= 0.85 and oi_diff < 0 else "WAIT"
    else:
        option_signal = "WAIT"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Spot reference", f"{spot_price:,.2f}" if spot_price else "N/A")
    c2.metric("PCR (displayed strikes)", f"{pcr:.2f}" if pd.notna(pcr) else "N/A")
    c3.metric("Net OI change (PE−CE)", f"{oi_diff:,.0f}" if pd.notna(oi_diff) else "N/A")
    c4.metric("Option signal", option_signal)
    st.caption(note + f" Selected view: {interval_label}. This is a snapshot collected when the app refreshes; it is not historical data from before the app was running.")
    # Keep a session-local time series of verified snapshots, labelled by the selected interval.
    snap_key = f"pcr_snapshots_{index_name}"
    snapshots = st.session_state.get(snap_key, [])
    snapshot = {"Time": datetime.now().strftime("%H:%M:%S"), "View": interval_label,
                "PCR": round(float(pcr), 3) if pd.notna(pcr) else np.nan,
                "Call OI": float(call_oi) if pd.notna(call_oi) else np.nan,
                "Put OI": float(put_oi) if pd.notna(put_oi) else np.nan,
                "Net OI change (PE−CE)": float(oi_diff) if pd.notna(oi_diff) else np.nan,
                "Option signal": option_signal}
    if not snapshots or snapshots[-1].get("Time") != snapshot["Time"]:
        snapshots.append(snapshot)
    st.session_state[snap_key] = snapshots[-200:]
    view = chain[["Strike", "Type", "LTP", "OI", "Change in OI"]].copy()
    view["LTP"] = view["LTP"].map(lambda x: round(x, 2) if pd.notna(x) else np.nan)
    st.dataframe(view, use_container_width=True, hide_index=True)
    history = st.session_state.get(f"oi_{index_name}", [])
    if len(history) >= 2:
        hist_df = pd.DataFrame([{"Time": h["time"].strftime("%H:%M:%S"), "Tokens with OI": len(h["quotes"])} for h in history])
        st.caption("Captured quote refresh history (current app session)")
        st.dataframe(hist_df.tail(10), use_container_width=True, hide_index=True)
    snapshots_df = pd.DataFrame(st.session_state.get(f"pcr_snapshots_{index_name}", []))
    if not snapshots_df.empty:
        st.markdown("**Time-wise PCR / OI signal history (captured since app opened)**")
        st.dataframe(snapshots_df.tail(20).iloc[::-1], use_container_width=True, hide_index=True)


def _angel_nearest_future_change(underlying_name):
    """Return nearest-expiry Angel One futures LTP and % change, or N/A when unavailable."""
    client = st.session_state.get("angel_session")
    master = st.session_state.get("angel_instruments")
    if client is None:
        return np.nan, np.nan
    if master is None or master.empty:
        master = download_instrument_master()
        st.session_state.angel_instruments = master
    if master.empty:
        return np.nan, np.nan
    lower = {str(c).lower(): c for c in master.columns}
    required = ["symbol", "name", "exch_seg", "instrumenttype", "expiry", "token"]
    if not all(k in lower for k in required):
        return np.nan, np.nan
    col = lambda k: lower[k]
    frame = master.copy()
    frame = frame[frame[col("exch_seg")].astype(str).str.upper().eq("NFO")]
    frame = frame[frame[col("instrumenttype")].astype(str).str.upper().str.contains("FUT", na=False)]
    frame = frame[frame[col("name")].astype(str).str.upper().str.replace(" ", "", regex=False).eq(underlying_name.upper().replace(" ", ""))]
    frame["_expiry"] = pd.to_datetime(frame[col("expiry")], errors="coerce")
    frame = frame[frame["_expiry"] >= pd.Timestamp.now().normalize()].sort_values("_expiry")
    if frame.empty:
        return np.nan, np.nan
    row = frame.iloc[0]
    try:
        response = client.getMarketData("FULL", {"NFO": [str(row[col("token")])]})
        payload = (response or {}).get("data") or {}
        quotes = payload.get("fetched") or []
        if not quotes:
            return np.nan, np.nan
        quote = quotes[0]
        ltp = safe_float(quote.get("ltp"))
        previous_close = safe_float(quote.get("close", quote.get("closePrice", np.nan)))
        change = (ltp / previous_close - 1) * 100 if pd.notna(ltp) and pd.notna(previous_close) and previous_close else np.nan
        return ltp, change
    except Exception:
        return np.nan, np.nan


def render_stock_scanner():
    st.subheader("📉 Major Stocks Strength Scanner — CMP & % Change")
    st.caption("Spot % change is shown from the latest available candle versus the previous daily close. Futures % change is shown only when Angel One returns a valid nearest-expiry futures quote; otherwise it stays N/A.")
    stocks = [
        ("HDFC BANK", "HDFCBANK.NS", "HDFCBANK"), ("RELIANCE", "RELIANCE.NS", "RELIANCE"),
        ("ICICI BANK", "ICICIBANK.NS", "ICICIBANK"), ("INFY", "INFY.NS", "INFY"),
        ("LT", "LT.NS", "LT"), ("TCS", "TCS.NS", "TCS"), ("ITC", "ITC.NS", "ITC"),
        ("BHARTI AIRTEL", "BHARTIARTL.NS", "BHARTIARTL"), ("AXIS BANK", "AXISBANK.NS", "AXISBANK"),
        ("SBI", "SBIN.NS", "SBIN"),
    ]
    rows = []
    for name, ticker, angel_name in stocks:
        daily = get_yahoo_history(ticker, period="5d", interval="1d")
        intraday = get_yahoo_history(ticker, period="5d", interval="5m")
        history = intraday if not intraday.empty else daily
        cmp = float(history["Close"].iloc[-1]) if not history.empty else np.nan
        prev_close = float(daily["Close"].iloc[-2]) if len(daily) >= 2 else np.nan
        spot_change = (cmp / prev_close - 1) * 100 if pd.notna(cmp) and pd.notna(prev_close) and prev_close else np.nan
        fut_cmp, fut_change = _angel_nearest_future_change(angel_name)
        rows.append({"S.NO": len(rows)+1, "STOCKS": name, "CMP (Spot)": cmp,
                     "Spot % Change": spot_change, "Futures CMP": fut_cmp,
                     "%chg fut": fut_change,
                     "Strength": "BULLISH" if pd.notna(spot_change) and spot_change > 0 else "BEARISH" if pd.notna(spot_change) and spot_change < 0 else "N/A"})
    table = pd.DataFrame(rows)
    def color_change(value):
        if pd.isna(value): return ""
        return "background-color: #8bd34f; color: black" if value >= 0 else "background-color: #ff2424; color: white"
    st.dataframe(table.style.format({"CMP (Spot)": "{:.2f}", "Spot % Change": "{:+.2f}%", "Futures CMP": "{:.2f}", "%chg fut": "{:+.2f}%"}, na_rep="N/A")
                 .map(color_change, subset=["Spot % Change", "%chg fut"]), use_container_width=True, hide_index=True)
    if st.session_state.get("angel_session") is None:
        st.caption("Connect Angel One to populate actual futures quotes. Yahoo spot movement is not represented as futures movement.")

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
    source = st.selectbox("Market data source", ["Yahoo Finance", "Angel One", "Demo (synthetic)"])
    st.session_state.source = source
    symbol_label = st.selectbox("Instrument", list(INDIAN_WATCHLIST.keys()))
    symbol = INDIAN_WATCHLIST[symbol_label]
    interval_label = st.selectbox("Chart timeframe", ["1 minute", "3 minutes", "5 minutes", "15 minutes", "Daily"], index=2)
    yahoo_interval = {"1 minute": "1m", "3 minutes": "5m", "5 minutes": "5m", "15 minutes": "15m", "Daily": "1d"}[interval_label]
    angel_interval = {"1 minute": "ONE_MINUTE", "3 minutes": "THREE_MINUTE", "5 minutes": "FIVE_MINUTE", "15 minutes": "FIFTEEN_MINUTE", "Daily": "ONE_DAY"}[interval_label]
    rr_choice = st.selectbox("Target 1 Risk:Reward", [1, 2, 3, 5], index=1, format_func=lambda x: f"1:{x}")
    refresh_enabled = st.toggle("Auto-refresh", value=False)
    refresh_seconds = st.selectbox("Refresh interval", [5, 10, 15, 30, 60], index=2)
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
signal = analyze_signal(d, rr_multiple=rr_choice)
last = d.iloc[-1]
st.caption(f"Selected: **{symbol_label}** · Data source: **{actual_source}** · Last candle: {d.index[-1]}")

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
    st.caption(f"Selected target RR: {signal['rr']}. ATR-based reference levels only; these are not broker orders and do not account for slippage, charges, gaps or liquidity.")
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
    st.caption("This percentage is a normalized indicator score, not a tested probability of winning a trade.")
    st.write(f"**Trend:** {'Bullish' if signal['score'] >= 3 else 'Bearish' if signal['score'] <= -3 else 'Sideways / mixed'}")
    st.write(f"**Support (rolling 20):** {last['Support']:.2f}" if pd.notna(last["Support"]) else "Support: N/A")
    st.write(f"**Resistance (rolling 20):** {last['Resistance']:.2f}" if pd.notna(last["Resistance"]) else "Resistance: N/A")
    st.write(f"**Previous pivot:** {last['Pivot']:.2f}" if pd.notna(last["Pivot"]) else "Pivot: N/A")
    st.write(f"**Previous candle high / low:** {last['Previous High']:.2f} / {last['Previous Low']:.2f}" if pd.notna(last["Previous High"]) and pd.notna(last["Previous Low"]) else "Previous candle high/low: N/A")

st.subheader("🕯️ Candle-by-Candle Candlestick Pattern & Price Action")
pa_all = add_price_action_columns(d)
show_n = st.selectbox("Candles to inspect", [20, 50, 100, 200], index=1, key="candle_rows")
pa_view = pa_all.tail(show_n).copy()
pa_view.insert(0, "Time", pa_view.index.astype(str))
cols = ["Time", "Open", "High", "Low", "Close", "Candle Type", "Body", "Upper Wick", "Lower Wick", "Body %", "Pattern", "Price Action", "Structure", "Volume Status", "VWAP Position"]
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

st.subheader("📊 Backtest (EMA 9/20 crossover proxy with estimated costs)")
cost_bps = st.number_input("Estimated cost per position change (basis points)", min_value=0.0, max_value=100.0, value=10.0, step=1.0, help="Approximate total costs/slippage assumption. Actual charges vary by instrument and broker.")
bt = calc_backtest(d, cost_bps=cost_bps)
if bt.empty:
    st.info("Not enough historical candles for this backtest.")
else:
    returns = bt["P&L % (estimated net, bar proxy)"].dropna()
    wins = int((returns > 0).sum())
    losses = int((returns < 0).sum())
    total = len(returns)
    win_rate = (wins / total * 100) if total else 0
    gross_net_pct = (float((1 + bt["net_return"].fillna(0)).prod()) - 1) * 100
    positive_sum = returns[returns > 0].sum()
    negative_sum = abs(returns[returns < 0].sum())
    profit_factor = positive_sum / negative_sum if negative_sum > 0 else np.nan
    max_dd = float(bt["drawdown_pct"].min()) if not bt["drawdown_pct"].empty else 0.0
    b1,b2,b3,b4,b5 = st.columns(5)
    b1.metric("Signal changes", total)
    b2.metric("Positive rows", wins)
    b3.metric("Negative rows", losses)
    b4.metric("Positive-row ratio", f"{win_rate:.1f}%")
    b5.metric("Max drawdown (proxy)", f"{max_dd:.2f}%")
    st.metric("Compounded net return (proxy)", f"{gross_net_pct:.2f}%", f"Profit factor: {profit_factor:.2f}" if pd.notna(profit_factor) else "Profit factor: N/A")
    st.caption("Educational proxy, not fill-level backtesting. It does not simulate candle-by-candle stop/target fills, realistic position sizing, taxes, funding, market impact or gaps. Past results do not predict future returns.")
    st.dataframe(bt.tail(30), use_container_width=True)

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
    st.info(f"Open paper position: {pos['side']} {pos['qty']} × {pos['symbol']} @ {pos['entry']:.2f}")
    current_pnl = (last["Close"] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "BUY" else -1)
    st.metric("Unrealized paper P&L", f"₹{current_pnl:,.2f}")
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
            st.session_state.paper_position = {"side": "BUY", "symbol": symbol_label, "entry": float(last["Close"]), "qty": int(qty)}
            st.rerun()
    with sell_col:
        if st.button("Open paper SELL", use_container_width=True):
            st.session_state.paper_position = {"side": "SELL", "symbol": symbol_label, "entry": float(last["Close"]), "qty": int(qty)}
            st.rerun()

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
- **Angel One tokens:** the app downloads the public instrument master and tries to resolve symbols automatically. Some symbols/derivative contracts can still require mapping depending on the current instrument master.
- **Live data:** refresh is polling, not a guaranteed zero-delay feed. Yahoo intraday history can be delayed, restricted, or unavailable. Angel One candle data depends on broker API access and rate limits.
- **PCR / option-chain OI:** displayed only when Angel One returns option quotes with OI. Time-wise PCR/OI rows are captured from the moment the app is open and refreshes; historical snapshots from before launch are not available. FII/DII flows and official event/news feeds are not included.
- **Real orders:** deliberately not implemented. Paper-trading buttons only record simulated positions. No actual Angel One order is placed by this app.
- **Login:** basic session login is included. Configure APP_USERNAME and APP_PASSWORD in Streamlit secrets/environment and change the default demo credentials before public deployment.
- **Win chance:** a real probability requires out-of-sample validation. The displayed score strength is not a calibrated win probability.
""")
st.caption(f"Updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · For education/research only; not investment advice.")
