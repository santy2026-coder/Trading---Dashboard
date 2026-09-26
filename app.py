import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from datetime import datetime, timedelta

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📈",
    layout="wide"
)

# =========================================================
# LOGIN
# =========================================================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "paper_trades" not in st.session_state:
    st.session_state.paper_trades = []

if "balance" not in st.session_state:
    st.session_state.balance = 100000.0


def login_page():
    st.title("📈 Trading Dashboard")
    st.subheader("Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("Login", use_container_width=True):
            if username == "admin" and password == "admin123":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Invalid username or password")

    with c2:
        if st.button("Forgot Password", use_container_width=True):
            st.info("Default username: admin | Default password: admin123")


if not st.session_state.logged_in:
    login_page()
    st.stop()

# =========================================================
# FUNCTIONS
# =========================================================
@st.cache_data(ttl=30)
def get_data(symbol, period="1mo", interval="15m"):
    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False
        )

        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data = data.dropna()
        return data

    except Exception:
        return pd.DataFrame()


def calculate_indicators(df):
    if df.empty:
        return df

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # EMA
    df["EMA5"] = close.ewm(span=5, adjust=False).mean()
    df["EMA21"] = close.ewm(span=21, adjust=False).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False).mean()
    df["EMA200"] = close.ewm(span=200, adjust=False).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

    # ATR
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = true_range.rolling(14).mean()

    # VWAP
    typical_price = (high + low + close) / 3

    cumulative_volume = volume.cumsum()
    cumulative_pv = (typical_price * volume).cumsum()

    df["VWAP"] = cumulative_pv / cumulative_volume.replace(0, np.nan)

    return df


def detect_patterns(df):
    patterns = []

    if len(df) < 5:
        return patterns

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Bullish / bearish candle
    if last["Close"] > last["Open"]:
        patterns.append("Bullish Candle")

    if last["Close"] < last["Open"]:
        patterns.append("Bearish Candle")

    # EMA trend
    if last["EMA5"] > last["EMA21"] > last["EMA50"]:
        patterns.append("Bullish EMA Alignment")

    if last["EMA5"] < last["EMA21"] < last["EMA50"]:
        patterns.append("Bearish EMA Alignment")

    # Golden / Death cross
    if prev["EMA50"] <= prev["EMA200"] and last["EMA50"] > last["EMA200"]:
        patterns.append("Golden Cross")

    if prev["EMA50"] >= prev["EMA200"] and last["EMA50"] < last["EMA200"]:
        patterns.append("Death Cross")

    # RSI
    if last["RSI"] < 30:
        patterns.append("RSI Oversold")

    if last["RSI"] > 70:
        patterns.append("RSI Overbought")

    # MACD
    if last["MACD"] > last["MACD_SIGNAL"]:
        patterns.append("MACD Bullish")

    if last["MACD"] < last["MACD_SIGNAL"]:
        patterns.append("MACD Bearish")

    return patterns


def market_signal(row):
    score = 0

    if row["Close"] > row["EMA21"]:
        score += 1

    if row["EMA5"] > row["EMA21"]:
        score += 1

    if row["EMA21"] > row["EMA50"]:
        score += 1

    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 1

    if row["RSI"] > 50:
        score += 1

    if row["Close"] > row["VWAP"]:
        score += 1

    if score >= 5:
        return "BULLISH"

    if score <= 2:
        return "BEARISH"

    return "NEUTRAL"


def get_news():
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search?q=India%20stock%20market"
        response = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        data = response.json()

        news = []

        for item in data.get("news", [])[:8]:
            title = item.get("title", "")
            link = item.get("link", "")

            if title:
                news.append((title, link))

        return news

    except Exception:
        return []


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.title("⚙️ Dashboard")

symbol = st.sidebar.text_input(
    "Symbol",
    value="RELIANCE.NS"
).upper()

period = st.sidebar.selectbox(
    "Chart Period",
    ["1d", "5d", "1mo", "3mo", "6mo", "1y"],
    index=2
)

interval_options = {
    "1 Minute": "1m",
    "5 Minutes": "5m",
    "15 Minutes": "15m",
    "30 Minutes": "30m",
    "1 Hour": "60m",
    "Daily": "1d"
}

interval_name = st.sidebar.selectbox(
    "Timeframe",
    list(interval_options.keys()),
    index=2
)

interval = interval_options[interval_name]

if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

if st.sidebar.button("🚪 Logout", use_container_width=True):
    st.session_state.logged_in = False
    st.rerun()

# =========================================================
# MAIN HEADER
# =========================================================
st.title("📈 Advanced Trading Dashboard")
st.caption(f"Symbol: {symbol} | Timeframe: {interval_name}")

# =========================================================
# LOAD DATA
# =========================================================
df = get_data(symbol, period, interval)

if df.empty:
    st.error(
        "Market data available nahi hai. "
        "Symbol check karein, example: RELIANCE.NS, TCS.NS, INFY.NS, ^NSEI"
    )
    st.stop()

df = calculate_indicators(df)
last = df.iloc[-1]

# =========================================================
# TOP METRICS
# =========================================================
price = float(last["Close"])
change = price - float(df["Close"].iloc[-2])
change_pct = (change / float(df["Close"].iloc[-2])) * 100

signal = market_signal(last)

c1, c2, c3, c4, c5, c6 = st.columns(6)

c1.metric("Live Price", f"₹{price:,.2f}", f"{change:+.2f}")
c2.metric("Change %", f"{change_pct:+.2f}%")
c3.metric("RSI", f"{last['RSI']:.2f}")
c4.metric("MACD", f"{last['MACD']:.2f}")
c5.metric("ATR", f"{last['ATR']:.2f}")
c6.metric("Signal", signal)

# =========================================================
# CHART
# =========================================================
st.subheader("📊 Price & Indicators")

fig = go.Figure()

fig.add_trace(
    go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name="Price"
    )
)

fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["EMA5"],
        name="EMA 5",
        line=dict(width=1)
    )
)

fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["EMA21"],
        name="EMA 21",
        line=dict(width=1)
    )
)

fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["EMA50"],
        name="EMA 50",
        line=dict(width=1)
    )
)

fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["EMA200"],
        name="EMA 200",
        line=dict(width=1)
    )
)

fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["VWAP"],
        name="VWAP",
        line=dict(width=2)
    )
)

fig.update_layout(
    height=600,
    xaxis_rangeslider_visible=False,
    template="plotly_dark"
)

st.plotly_chart(fig, use_container_width=True)

# =========================================================
# INDICATOR PANEL
# =========================================================
col1, col2 = st.columns(2)

with col1:
    st.subheader("📈 RSI")

    rsi_fig = go.Figure()

    rsi_fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["RSI"],
            name="RSI"
        )
    )

    rsi_fig.add_hline(y=70)
    rsi_fig.add_hline(y=30)

    rsi_fig.update_layout(
        height=300,
        yaxis_title="RSI",
        template="plotly_dark"
    )

    st.plotly_chart(rsi_fig, use_container_width=True)

with col2:
    st.subheader("📊 MACD")

    macd_fig = go.Figure()

    macd_fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MACD"],
            name="MACD"
        )
    )

    macd_fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["MACD_SIGNAL"],
            name="Signal"
        )
    )

    macd_fig.update_layout(
        height=300,
        template="plotly_dark"
    )

    st.plotly_chart(macd_fig, use_container_width=True)

# =========================================================
# PATTERNS
# =========================================================
st.subheader("🔎 Technical Analysis")

patterns = detect_patterns(df)

if patterns:
    for pattern in patterns:
        st.info(f"• {pattern}")
else:
    st.info("No major pattern detected")

# =========================================================
# PAPER TRADING
# =========================================================
st.subheader("📝 Paper Trading")

trade_col1, trade_col2 = st.columns(2)

with trade_col1:
    quantity = st.number_input(
        "Quantity",
        min_value=1,
        value=1,
        step=1
    )

    entry_price = st.number_input(
        "Entry Price",
        min_value=0.0,
        value=float(price),
        step=0.05
    )

    stop_loss = st.number_input(
        "Stop Loss",
        min_value=0.0,
        value=max(0.0, float(price - last["ATR"])),
        step=0.05
    )

    target = st.number_input(
        "Target",
        min_value=0.0,
        value=float(price + last["ATR"] * 2),
        step=0.05
    )

with trade_col2:
    side = st.selectbox(
        "Position",
        ["BUY", "SELL"]
    )

    live_pnl = 0.0

    if side == "BUY":
        live_pnl = (price - entry_price) * quantity
    else:
        live_pnl = (entry_price - price) * quantity

    st.metric(
        "Live P/L",
        f"₹{live_pnl:,.2f}"
    )

    st.write(f"**Live Price:** ₹{price:,.2f}")
    st.write(f"**Entry:** ₹{entry_price:,.2f}")
    st.write(f"**Stop Loss:** ₹{stop_loss:,.2f}")
    st.write(f"**Target:** ₹{target:,.2f}")

    if st.button("➕ Open Paper Trade", use_container_width=True):
        trade = {
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Symbol": symbol,
            "Side": side,
            "Quantity": quantity,
            "Entry": entry_price,
            "Stop Loss": stop_loss,
            "Target": target,
            "Live Price": price,
            "P/L": live_pnl
        }

        st.session_state.paper_trades.append(trade)
        st.success("Paper trade opened")

# =========================================================
# PAPER TRADE TABLE
# =========================================================
if st.session_state.paper_trades:
    st.subheader("📋 Paper Trade Positions")

    trades_df = pd.DataFrame(st.session_state.paper_trades)

    # Update live P/L
    for i in range(len(trades_df)):
        trade_side = trades_df.loc[i, "Side"]
        entry = float(trades_df.loc[i, "Entry"])
        qty = float(trades_df.loc[i, "Quantity"])

        trades_df.loc[i, "Live Price"] = price

        if trade_side == "BUY":
            trades_df.loc[i, "P/L"] = (price - entry) * qty
        else:
            trades_df.loc[i, "P/L"] = (entry - price) * qty

    st.dataframe(
        trades_df,
        use_container_width=True,
        hide_index=True
    )

    if st.button("❌ Clear Paper Trades"):
        st.session_state.paper_trades = []
        st.rerun()

# =========================================================
# MARKET ANALYSIS
# =========================================================
st.subheader("🌐 Market Analysis")

m1, m2, m3, m4 = st.columns(4)

# These are reference instruments available through Yahoo Finance.
try:
    usd = get_data("INR=X", "5d", "1d")
    crude = get_data("CL=F", "5d", "1d")
    nifty = get_data("^NSEI", "5d", "1d")
    banknifty = get_data("^NSEBANK", "5d", "1d")

    usd_price = float(usd["Close"].iloc[-1]) if not usd.empty else 0
    crude_price = float(crude["Close"].iloc[-1]) if not crude.empty else 0
    nifty_price = float(nifty["Close"].iloc[-1]) if not nifty.empty else 0
    banknifty_price = float(banknifty["Close"].iloc[-1]) if not banknifty.empty else 0

except Exception:
    usd_price = crude_price = nifty_price = banknifty_price = 0

m1.metric("USD / INR", f"₹{usd_price:,.2f}" if usd_price else "N/A")
m2.metric("Crude Oil", f"${crude_price:,.2f}" if crude_price else "N/A")
m3.metric("NIFTY 50", f"{nifty_price:,.2f}" if nifty_price else "N/A")
m4.metric("BANK NIFTY", f"{banknifty_price:,.2f}" if banknifty_price else "N/A")

# =========================================================
# FII / DII
# =========================================================
st.subheader("🏦 FII / DII")

st.info(
    "FII/DII data source-dependent hai. "
    "Is dashboard mein live market-price data Yahoo Finance se aata hai. "
    "Official NSE FII/DII figures ko final trading decision se pehle verify karein."
)

fii_col1, fii_col2 = st.columns(2)

with fii_col1:
    st.metric("FII", "Data source required")

with fii_col2:
    st.metric("DII", "Data source required")

# =========================================================
# NEWS
# =========================================================
st.subheader("📰 Market & Geopolitical News")

news = get_news()

if news:
    for title, link in news:
        st.markdown(f"• [{title}]({link})")
else:
    st.info("News temporarily unavailable.")

# =========================================================
# DATA TABLE
# =========================================================
with st.expander("📑 Latest Market Data"):
    display_cols = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "EMA5",
        "EMA21",
        "EMA50",
        "EMA200",
        "VWAP",
        "RSI",
        "MACD",
        "ATR"
    ]

    available_cols = [
        col for col in display_cols
        if col in df.columns
    ]

    st.dataframe(
        df[available_cols].tail(20),
        use_container_width=True
    )

# =========================================================
# FOOTER
# =========================================================
st.divider()

st.caption(
    "Trading Dashboard | Live data may be delayed or unavailable. "
    "This application is for informational and paper-trading purposes."
)
