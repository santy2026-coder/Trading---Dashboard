import os
import hashlib
import time
import requests
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Trading Intelligence Terminal",
    page_icon="📈",
    layout="wide"
)


# ============================================================
# 2. LOGIN SYSTEM
# ============================================================

def sha256(value):
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def login():

    if st.session_state.get("authenticated"):
        return True

    st.title("🔐 Trading Intelligence Terminal")
    st.subheader("Application Login")

    expected_username = os.getenv(
        "APP_USERNAME",
        "admin"
    )

    expected_password_hash = os.getenv(
        "APP_PASSWORD_SHA256",
        sha256("admin123")
    )

    with st.form("login_form"):

        username = st.text_input("Username")

        password = st.text_input(
            "Password",
            type="password"
        )

        submitted = st.form_submit_button("Login")

    if submitted:

        if (
            username == expected_username
            and sha256(password) == expected_password_hash
        ):

            st.session_state.authenticated = True
            st.rerun()

        else:

            st.error("Incorrect username or password.")

    st.caption(
        "Testing login: admin / admin123. "
        "Change these credentials before public deployment."
    )

    return False


if not login():
    st.stop()


# ============================================================
# 3. DASHBOARD HEADER
# ============================================================

st.title("📈 Trading Intelligence Terminal")

st.caption(
    "Technical Analysis | Indian Markets | Global Markets | "
    "Angel One | Backtesting | Paper Trading"
)

st.warning(
    "This application is for market analysis and paper trading. "
    "Market data may be delayed or unavailable. "
    "Signals are not guaranteed to be profitable. "
    "This application does not place real broker orders."
)


# ============================================================
# 4. SESSION INITIALIZATION
# ============================================================

default_session_values = {

    "paper_trades": [],

    "paper_position": None,

    "angel_candles": None,

    "angel_candle_key": None,

    "angel_connected": False,

    "angel_api": None,

    "angel_client_code": "",

    "angel_resolved_instrument": None,

    "authenticated": True
}


for key, value in default_session_values.items():

    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 5. INDIAN MARKET WATCHLIST
# ============================================================

SYMBOLS = {

    # Major indices

    "NIFTY 50": "^NSEI",

    "BANK NIFTY": "^NSEBANK",

    "SENSEX": "^BSESN",

    "NIFTY IT": "^CNXIT",

    "NIFTY AUTO": "^CNXAUTO",


    # Banking

    "HDFC BANK": "HDFCBANK.NS",

    "ICICI BANK": "ICICIBANK.NS",

    "SBI": "SBIN.NS",

    "AXIS BANK": "AXISBANK.NS",

    "KOTAK BANK": "KOTAKBANK.NS",


    # IT

    "TCS": "TCS.NS",

    "INFOSYS": "INFY.NS",

    "HCLTECH": "HCLTECH.NS",

    "WIPRO": "WIPRO.NS",

    "TECH MAHINDRA": "TECHM.NS",


    # Automobile

    "MARUTI": "MARUTI.NS",

    "M&M": "M&M.NS",

    "TATA MOTORS": "TMPV.NS",

    "BAJAJ AUTO": "BAJAJ-AUTO.NS",

    "EICHER MOTORS": "EICHERMOT.NS",


    # Other major stocks

    "RELIANCE": "RELIANCE.NS",

    "L&T": "LT.NS",

    "BHARTI AIRTEL": "BHARTIARTL.NS",

    "ITC": "ITC.NS"
}


# ============================================================
# 6. GLOBAL MARKET WATCHLIST
# ============================================================

GLOBAL = {

    "US S&P 500": "^GSPC",

    "US Nasdaq": "^IXIC",

    "US Dow Jones": "^DJI",

    "Japan Nikkei": "^N225",

    "Hong Kong Hang Seng": "^HSI",

    "China Shanghai": "000001.SS",

    "South Korea KOSPI": "^KS11",

    "Brent Crude": "BZ=F",

    "USD/INR": "INR=X",

    "India VIX": "^INDIAVIX"
}


# ============================================================
# 7. ANGEL ONE INSTRUMENT MASTER
# ============================================================

ANGEL_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)


@st.cache_data(ttl=3600, show_spinner=False)
def load_angel_master():

    """
    Download the Angel One instrument master.
    Cache it for one hour to reduce repeated downloads.
    """

    response = requests.get(
        ANGEL_MASTER_URL,
        timeout=25
    )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list) or not payload:

        raise RuntimeError(
            "Angel One instrument master is unavailable."
        )

    master = pd.DataFrame(payload)

    required_columns = {
        "symbol",
        "token",
        "exch_seg"
    }

    if not required_columns.issubset(master.columns):

        raise RuntimeError(
            "Required symbol, token or exchange fields are missing."
        )

    for column in [
        "symbol",
        "name",
        "exch_seg",
        "instrumenttype",
        "token"
    ]:

        if column not in master.columns:
            master[column] = ""

        master[column] = (
            master[column]
            .fillna("")
            .astype(str)
        )

    master["_sym_norm"] = (
        master["symbol"]
        .str.upper()
        .str.replace(
            r"[^A-Z0-9]",
            "",
            regex=True
        )
    )

    master["_name_norm"] = (
        master["name"]
        .str.upper()
        .str.replace(
            r"[^A-Z0-9]",
            "",
            regex=True
        )
    )

    master["_exchange_norm"] = (
        master["exch_seg"].str.upper()
    )

    return master


# ============================================================
# 8. AUTOMATIC ANGEL ONE SYMBOL MAPPING
# ============================================================

ANGEL_ALIASES = {

    "NIFTY 50": [
        "NIFTY 50",
        "NIFTY50"
    ],

    "BANK NIFTY": [
        "NIFTY BANK",
        "BANKNIFTY",
        "NIFTYBANK"
    ],

    "SENSEX": [
        "SENSEX"
    ],

    "NIFTY IT": [
        "NIFTY IT",
        "NIFTYIT"
    ],

    "NIFTY AUTO": [
        "NIFTY AUTO",
        "NIFTYAUTO"
    ],

    "RELIANCE": [
        "RELIANCE-EQ",
        "RELIANCE"
    ],

    "HDFC BANK": [
        "HDFCBANK-EQ",
        "HDFCBANK"
    ],

    "ICICI BANK": [
        "ICICIBANK-EQ",
        "ICICIBANK"
    ],

    "SBI": [
        "SBIN-EQ",
        "SBIN"
    ],

    "AXIS BANK": [
        "AXISBANK-EQ",
        "AXISBANK"
    ],

    "KOTAK BANK": [
        "KOTAKBANK-EQ",
        "KOTAKBANK"
    ],

    "TCS": [
        "TCS-EQ",
        "TCS"
    ],

    "INFOSYS": [
        "INFY-EQ",
        "INFY"
    ],

    "HCLTECH": [
        "HCLTECH-EQ",
        "HCLTECH"
    ],

    "WIPRO": [
        "WIPRO-EQ",
        "WIPRO"
    ],

    "TECH MAHINDRA": [
        "TECHM-EQ",
        "TECHM"
    ],

    "MARUTI": [
        "MARUTI-EQ",
        "MARUTI"
    ],

    "M&M": [
        "M&M-EQ",
        "MM-EQ",
        "MM"
    ],

    "TATA MOTORS": [
        "TMPV-EQ",
        "TATAMOTORS-EQ",
        "TMPV",
        "TATAMOTORS"
    ],

    "BAJAJ AUTO": [
        "BAJAJ-AUTO-EQ",
        "BAJAJAUTO-EQ",
        "BAJAJ-AUTO"
    ],

    "EICHER MOTORS": [
        "EICHERMOT-EQ",
        "EICHERMOT"
    ]
}


def resolve_angel_instrument(
    master,
    display_name,
    custom_query=""
):

    """
    Resolve exchange, trading symbol and token automatically.
    The user does not need to enter the token manually.
    """

    if master is None or master.empty:

        raise RuntimeError(
            "Angel One instrument master is empty."
        )

    if display_name == "Custom instrument":

        query = custom_query.strip().upper()

        if not query:

            raise RuntimeError(
                "Enter an instrument name or symbol."
            )

        candidates = [
            query,
            query + "-EQ"
        ]

        preferred_exchange = "NSE"

    else:

        candidates = ANGEL_ALIASES.get(
            display_name,
            [display_name]
        )

        preferred_exchange = (
            "BSE"
            if display_name == "SENSEX"
            else "NSE"
        )

    normalized_candidates = [

        "".join(
            character
            for character in candidate.upper()
            if character.isalnum()
        )

        for candidate in candidates
    ]

    allowed_exchanges = [
        preferred_exchange,
        "BSE" if preferred_exchange == "NSE" else "NSE"
    ]

    subset = master[
        master["_exchange_norm"].isin(
            allowed_exchanges
        )
    ].copy()

    for candidate in normalized_candidates:

        hits = subset[
            subset["_sym_norm"] == candidate
        ].copy()

        if hits.empty:
            continue

        hits["_exchange_rank"] = (
            hits["_exchange_norm"] != preferred_exchange
        ).astype(int)

        hits["_derivative_rank"] = (
            hits["_sym_norm"]
            .str.contains(
                r"FUT|CE|PE",
                regex=True
            )
        ).astype(int)

        hits = hits.sort_values(
            [
                "_exchange_rank",
                "_derivative_rank"
            ]
        )

        row = hits.iloc[0]

        return {

            "exchange": row["exch_seg"].upper(),

            "symbol": row["symbol"],

            "token": row["token"],

            "name": row["name"] or row["symbol"]
        }

    for candidate in normalized_candidates:

        hits = subset[
            (
                subset["_name_norm"] == candidate
            )
            |
            (
                subset["_sym_norm"] == candidate
            )
        ].copy()

        if not hits.empty:

            hits["_exchange_rank"] = (
                hits["_exchange_norm"] != preferred_exchange
            ).astype(int)

            row = hits.sort_values(
                "_exchange_rank"
            ).iloc[0]

            return {

                "exchange": row["exch_seg"].upper(),

                "symbol": row["symbol"],

                "token": row["token"],

                "name": row["name"] or row["symbol"]
            }

    raise RuntimeError(
        f"'{display_name}' was not found in the Angel One "
        "instrument master. Refresh the instrument master "
        "or select another instrument."
    )


# ============================================================
# PART 1 ENDS HERE
# PART 2 WILL CONTINUE IN THE SAME app.py FILE
# ============================================================
