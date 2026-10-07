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

try:
    from signal_upgrade import improved_entry_signal, dynamic_exit_levels, smart_exit_condition, confidence_with_win_rate
except Exception:
    improved_entry_signal = None
    dynamic_exit_levels = None
    smart_exit_condition = None
    confidence_with_win_rate = None

# ---------------- ANGEL ONE SMARTAPI (OPTIONAL) ----------------
try:
    from SmartApi import SmartConnect
    import pyotp
    ANGEL_SDK_AVAILABLE = True
except Exception:
    SmartConnect = None
    pyotp = None
    ANGEL_SDK_AVAILABLE = False
