import os
import hashlib
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Trading Intelligence Terminal",
    page_icon="📈",
    layout="wide"
)

st.title("📈 Trading Intelligence Terminal")
st.caption("Market Analysis | Technical Indicators | Risk Management")


# ============================================================
# 2. APPLICATION LOGIN
# ============================================================

def app_login():
    if st.session_state.get("authenticated", False):
        return True

    st.subheader("Application Login")

    expected_user = "admin"
    expected_hash = hashlib.sha256(
        "admin123".encode("utf-8")
    ).hexdigest()

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")

    if submit:
        password_hash = hashlib.sha256(
            password.encode("utf-8")
        ).hexdigest()

        if (
            username == expected_user
            and expected_hash
            and password_hash == expected_hash
        ):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error(
                "Login failed. Configure your username "
                "and password hash before using the application."
            )

    return False


if not app_login():
    st.info(
        "Set APP_USERNAME and APP_PASSWORD_SHA256 "
        "as environment variables before running."
    )
    st.stop()


# ============================================================
# 3. SESSION INITIALIZATION
# ============================================================

if "journal" not in st.session_state:
    st.session_state["journal"] = []

if "candles" not in st.session_state:
    st.session_state["candles"] = None

if "angel_credentials" not in st.session_state:
    st.session_state["angel_credentials"] = None


# ============================================================
# 4. SIDEBAR CONTROLS
# ============================================================

st.sidebar.header("Market Controls")

market = st.sidebar.selectbox(
    "Market",
    ["NIFTY 50", "BANK NIFTY", "SENSEX", "Indian Stocks"]
)

if market == "NIFTY 50":
    default_symbol = "NIFTY"
elif market == "BANK NIFTY":
    default_symbol = "BANKNIFTY"
elif market == "SENSEX":
    default_symbol = "SENSEX"
else:
    default_symbol = "RELIANCE-EQ"

symbol = st.sidebar.text_input(
    "Trading Symbol",
    value=default_symbol
)

exchange = st.sidebar.selectbox(
    "Exchange",
    ["NSE", "BSE", "NFO"]
)

symbol_token = st.sidebar.text_input(
    "Angel One Symbol Token",
    help="Enter the exact token from the broker's instrument master."
)

interval = st.sidebar.selectbox(
    "Candle Interval",
    [
        "ONE_MINUTE",
        "THREE_MINUTE",
        "FIVE_MINUTE",
        "FIFTEEN_MINUTE",
        "THIRTY_MINUTE",
        "ONE_HOUR",
        "ONE_DAY"
    ]
)

lookback_days = st.sidebar.slider(
    "Historical Data Days",
    min_value=1,
    max_value=30,
    value=5
)

data_mode = st.sidebar.radio(
    "Data Source",
    ["Demo Data", "Angel One API"]
)


# ============================================================
# 5. ANGEL ONE MANUAL CONNECTION
# ============================================================

def connect_angel_one(api_key, client_code, pin, totp_secret):
    try:
        import pyotp
        from SmartApi import SmartConnect

        smart_api = SmartConnect(api_key=api_key)

        otp = pyotp.TOTP(totp_secret).now()

        response = smart_api.generateSession(
            client_code,
            pin,
            otp
        )

        if not response or response.get("status") is False:
            raise RuntimeError("Angel One authentication failed.")

        return smart_api

    except Exception as exc:
        raise RuntimeError(
            f"Could not connect to Angel One: {exc}"
        ) from exc


def fetch_angel_candles(
    smart_api,
    exchange,
    symbol_token,
    interval,
    lookback_days
):
    from datetime import timedelta

    now = datetime.now()
    start = now - timedelta(days=lookback_days)

    params = {
        "exchange": exchange,
        "symboltoken": str(symbol_token).strip(),
        "interval": interval,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": now.strftime("%Y-%m-%d %H:%M")
    }

    response = smart_api.getCandleData(params)

    if not response or response.get("status") is False:
        raise RuntimeError(
            f"Angel One candle request failed: {response}"
        )

    rows = response.get("data") or []

    if not rows:
        raise RuntimeError(
            "No candles returned. Verify exchange, token and interval."
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        errors="coerce"
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )

    return df.set_index("timestamp").sort_index()


if data_mode == "Angel One API":
    st.subheader("Connect Angel One")

    st.warning(
        "Credentials are sensitive. Use this form only in a trusted "
        "local environment. Never share API keys, PINs, OTPs or TOTP secrets."
    )

    with st.form("angel_one_login"):
        api_key = st.text_input("API Key", type="password")
        client_code = st.text_input("Client Code")
        pin = st.text_input("Trading PIN", type="password")
        totp_secret = st.text_input(
            "TOTP Secret",
            type="password"
        )

        connect_button = st.form_submit_button(
            "Connect to Angel One"
        )

    if connect_button:
        try:
            smart_api = connect_angel_one(
                api_key,
                client_code,
                pin,
                totp_secret
            )

            st.session_state["angel_credentials"] = {
                "api_key": api_key,
                "client_code": client_code,
                "pin": pin,
                "totp_secret": totp_secret
            }

            st.session_state["angel_connected"] = True

            st.success("Angel One authentication succeeded.")

        except Exception as exc:
            st.session_state["angel_connected"] = False
            st.error(str(exc))


# ============================================================
# 6. DEMO MARKET DATA
# ============================================================

def generate_demo_candles(rows=240):
    """
    Synthetic data for testing the interface only.
    This is NOT real market data.
    """

    rng = np.random.default_rng(42)

    close = 1000 * np.exp(
        np.cumsum(rng.normal(0.0001, 0.004, rows))
    )

    open_price = np.r_[close[0], close[:-1]]

    high = np.maximum(open_price, close) * (
        1 + rng.uniform(0.0001, 0.002, rows)
    )

    low = np.minimum(open_price, close) * (
        1 - rng.uniform(0.0001, 0.002, rows)
    )

    volume = rng.integers(1000, 50000, rows)

    index = pd.date_range(
        end=pd.Timestamp.now(),
        periods=rows,
        freq="5min"
    )

    return pd.DataFrame(
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume
        },
        index=index
    )


# ============================================================
# 7. DATA FETCH BUTTON
# ============================================================

if st.button("Fetch / Refresh Market Data", type="primary"):

    if data_mode == "Demo Data":
        st.session_state["candles"] = generate_demo_candles()
        st.success("Demo data generated.")

    else:
        credentials = st.session_state.get("angel_credentials")

        if not credentials:
            st.error("Connect to Angel One first.")

        elif not symbol_token:
            st.error("Enter the correct Angel One symbol token.")

        else:
            try:
                smart_api = connect_angel_one(
                    credentials["api_key"],
                    credentials["client_code"],
                    credentials["pin"],
                    credentials["totp_secret"]
                )

                df = fetch_angel_candles(
                    smart_api,
                    exchange,
                    symbol_token,
                    interval,
                    lookback_days
                )

                st.session_state["candles"] = df

                st.success("Historical candles fetched.")

            except Exception as exc:
                st.error(str(exc))


# ============================================================
# 8. LOAD DATA
# ============================================================

df = st.session_state.get("candles")

if df is None:
    if data_mode == "Demo Data":
        df = generate_demo_candles()
        st.warning("Demo mode: prices are simulated.")
    else:
        st.info(
            "Connect to Angel One and click Fetch / Refresh Market Data."
        )
        st.stop()

if df.empty:
    st.error("No market data is available.")
    st.stop()


# ============================================================
# 9. TECHNICAL INDICATORS
# ============================================================

def calculate_indicators(data):
    data = data.copy()

    data["ema20"] = data["close"].ewm(
        span=20,
        adjust=False
    ).mean()

    data["ema50"] = data["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    delta = data["close"].diff()

    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()

    rs = gain / loss.replace(0, np.nan)

    data["rsi"] = 100 - (100 / (1 + rs))

    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - data["close"].shift()).abs(),
            (data["low"] - data["close"].shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    data["atr"] = true_range.rolling(14).mean()

    typical_price = (
        data["high"] + data["low"] + data["close"]
    ) / 3

    cumulative_volume = data["volume"].cumsum()

    data["vwap"] = (
        typical_price * data["volume"]
    ).cumsum() / cumulative_volume.replace(0, np.nan)

    return data


df = calculate_indicators(df)
last = df.iloc[-1]


# ============================================================
# 10. SIGNAL ENGINE
# ============================================================

def generate_signal(row):
    score = 0

    if row["close"] > row["ema20"]:
        score += 1
    else:
        score -= 1

    if row["ema20"] > row["ema50"]:
        score += 1
    else:
        score -= 1

    if pd.notna(row["vwap"]):
        score += 1 if row["close"] > row["vwap"] else -1

    if pd.notna(row["rsi"]):
        if 50 < row["rsi"] < 70:
            score += 1
        elif row["rsi"] < 40:
            score -= 1

    if score >= 2:
        signal = "BUY"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "WAIT"

    return signal, score


signal, score = generate_signal(last)


# ============================================================
# 11. DASHBOARD
# ============================================================

st.subheader("Market Overview")

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Selected Symbol",
    symbol
)

c2.metric(
    "Latest Close",
    f"{last['close']:.2f}"
)

c3.metric(
    "Signal",
    signal
)

c4.metric(
    "Rule Score",
    f"{score:+d}"
)

left, right = st.columns([2, 1])

with left:
    st.subheader("Price and Indicators")

    chart_cols = [
        "close",
        "ema20",
        "ema50",
        "vwap"
    ]

    st.line_chart(df[chart_cols].tail(200))

with right:
    st.subheader("Technical Analysis")

    st.metric(
        "RSI",
        f"{last['rsi']:.2f}"
        if pd.notna(last["rsi"])
        else "N/A"
    )

    st.metric(
        "ATR",
        f"{last['atr']:.2f}"
        if pd.notna(last["atr"])
        else "N/A"
    )

    st.metric(
        "VWAP",
        f"{last['vwap']:.2f}"
        if pd.notna(last["vwap"])
        else "N/A"
    )

    st.metric(
        "EMA Trend",
        "Bullish"
        if last["ema20"] > last["ema50"]
        else "Bearish"
    )


# ============================================================
# 12. RISK / REWARD CALCULATOR
# ============================================================

st.subheader("Risk / Reward Calculator")

col1, col2, col3 = st.columns(3)

atr_value = (
    float(last["atr"])
    if pd.notna(last["atr"])
    else 1.0
)

atr_value = max(atr_value, 0.01)

with col1:
    entry = st.number_input(
        "Entry Price",
        min_value=0.0,
        value=float(last["close"])
    )

with col2:
    stop_loss = st.number_input(
        "Stop Loss",
        min_value=0.0,
        value=float(max(0.01, last["close"] - atr_value))
    )

with col3:
    target = st.number_input(
        "Target",
        min_value=0.0,
        value=float(last["close"] + 2 * atr_value)
    )

risk = abs(entry - stop_loss)
reward = abs(target - entry)

if risk > 0:
    st.metric(
        "Risk : Reward",
        f"1 : {reward / risk:.2f}"
    )
else:
    st.warning("Entry aur stop-loss alag rakho.")


# ============================================================
# 13. BASIC EMA BACKTEST
# ============================================================

st.subheader("Basic Historical Backtest")

st.caption(
    "Illustrative EMA strategy. Brokerage, taxes, slippage and execution "
    "effects are not included."
)

if len(df) >= 55:
    bt = df.copy()

    bt["fast"] = bt["close"].ewm(
        span=20,
        adjust=False
    ).mean()

    bt["slow"] = bt["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    bt["position"] = (
        bt["fast"] > bt["slow"]
    ).astype(float).shift(1).fillna(0)

    bt["returns"] = bt["close"].pct_change().fillna(0)

    bt["strategy_returns"] = (
        bt["position"] * bt["returns"]
    )

    bt["equity"] = (
        1 + bt["strategy_returns"]
    ).cumprod()

    buy_hold = (
        1 + bt["returns"]
    ).cumprod()

    b1, b2 = st.columns(2)

    b1.metric(
        "Strategy Return",
        f"{(bt['equity'].iloc[-1] - 1) * 100:.2f}%"
    )

    b2.metric(
        "Buy-and-Hold Return",
        f"{(buy_hold.iloc[-1] - 1) * 100:.2f}%"
    )

    st.line_chart(bt[["equity"]])

else:
    st.info("Backtest ke liye aur historical candles chahiye.")


# ============================================================
# 14. TRADE JOURNAL
# ============================================================

st.subheader("Trade Journal")

j1, j2 = st.columns(2)

with j1:
    outcome = st.selectbox(
        "Signal Outcome",
        ["Pending", "Win", "Loss"]
    )

with j2:
    if st.button("Record Signal"):
        st.session_state["journal"].append(
            {
                "Time": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "Symbol": symbol,
                "Signal": signal,
                "Price": round(float(last["close"]), 2),
                "Score": score,
                "Outcome": outcome
            }
        )

if st.session_state["journal"]:
    journal_df = pd.DataFrame(
        st.session_state["journal"]
    )

    st.dataframe(
        journal_df,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download Journal CSV",
        journal_df.to_csv(index=False),
        file_name="trade_journal.csv",
        mime="text/csv"
    )

    completed = journal_df[
        journal_df["Outcome"].isin(["Win", "Loss"])
    ]

    if not completed.empty:
        wins = (completed["Outcome"] == "Win").sum()
        losses = (completed["Outcome"] == "Loss").sum()
        total = len(completed)

        win_rate = (wins / total) * 100

        c1, c2, c3 = st.columns(3)

        c1.metric("Completed Trades", total)
        c2.metric("Wins", int(wins))
        c3.metric("Win Rate", f"{win_rate:.2f}%")

    else:
        st.info("Win rate dekhne ke liye pehle trades ka outcome record karo.")
