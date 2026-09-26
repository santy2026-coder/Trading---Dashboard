Screenshot_2026-09-27-01-01-31-80_6012fa4d4ddec268fc5c7112cbb265e7.jpg
Requirement wali file mein bas itna hi tha

17904532144405631738300983013957.jpg

Haan bhai, ab ye features bhi add kar diye hain. Updated app.py ready hai. ✅

Ab dashboard mein ye extra cheezein hain
Backtest dropdown: 1, 2, 3, 4, 5, 6 months, 1 year, 5 years

Har period ka:

Historical win rate

Total signals

BUY win rate

SELL win rate

Strong BUY win rate

Strong SELL win rate

Average next-bar return

BUY + Volume confirmation win rate

SELL + Volume confirmation win rate

Current signal ka historical win rate

Confidence score

STRONG BUY / BUY BIAS / NO SIGNAL / SELL BIAS / STRONG SELL

Volume confirmation + volume ratio

PCR / OI-PCR, where the selected symbol's Yahoo option chain is available

Current trend:

Strong Uptrend

Bullish Trend

Sideways

Bearish Trend

Strong Downtrend

Previous-day trend

Previous-day:

High/Low

Close

Pivot

S1/S2

R1/R2

Current/live support & resistance

Pre-open analysis:

Previous close

Possible opening price

Possible opening range

Bullish/Bearish/Neutral bias

Multifactor score

NSE pre-open data attempt भी रखा है; NSE के अनुसार pre-open session 9:00–9:15 IST है और discovered equilibrium price day का opening price बनता है. 

Option-chain section में OI/PCR उपलब्ध होने पर display होगा; NSE option-chain data में OI और volume जैसे fields उपलब्ध होते हैं. 

सबसे जरूरी बात
Win rate को future guarantee नहीं दिखाया है.
Dashboard में जो “win rate” दिखेगा वह selected historical backtest period में इस rule-based setup की actual historical hit rate होगी। Current live confirmation के लिए वही historical evidence अलग से दिखेगा—इसे future probability नहीं माना जाएगा।

Updated file
Download updated app.py

अब GitHub में
app.py खोलो

पूरा पुराना code हटाओ

इस नए app.py का पूरा code paste करो

Commit changes

requirements.txt को मत बदलना

इसके बाद Streamlit अपने-आप नया version redeploy करेगा.

और हाँ—पुराने app को अभी final मत मानना; यह वाला update हमारी requirements के काफी ज्यादा करीब है.


app.py
Code


Ishme entry point ke sate entry price Target 1,target2 and aage kuch banat hai to and trolling stoploss bhi signal ke niche  suggest price atm or otm point and price ke sath dikhaye

Haan bhai 👍 ye bhi add kar diya hai.

Ab signal ke niche dashboard mein ye sab dikhega:

Suggested Entry Price

Initial Stop Loss

Target 1

Target 2

Target 3

Trailing Stop Loss

Break-even Stop — T1 hit hone ke baad

Risk/Reward to Target 2

Live Support / Resistance / Pivot ke basis par levels

Signal BUY/SELL na ho to entry/targets automatically activate nahi honge.

Options bhi add kiye
Signal ke hisaab se:

ATM Call/Put

OTM +1 / -1

OTM +2 / -2

Strike price

Latest available option premium

Expiry

Agar option-chain data available nahi hua, dashboard clearly N/A/unavailable dikhayega—price invent nahi karega.

Paper Trading
Paper trade mein bhi suggested Entry + SL + Target 1 automatically pre-fill honge.

Trailing SL live price ke saath recalculate hoga jab dashboard refresh hoga.

Updated file:

Download updated app.py

GitHub mein purane app.py ko isse replace karke Commit changes kar dena. requirements.txt mein koi change nahi chahiye.


app.py
Code


App mein auto refresh enable ya disable ka bhi option hona chahiye,live news detect karke live market mein analysis aur sare data ke sath critical analysis karke signal ke sath add karke suggestions call ya out ka bhi de

Haan bhai 👍 ye features bhi updated app mein add kar diye hain.

⚡ Auto Refresh
Sidebar mein:

Enable Auto Refresh ON/OFF

Refresh interval: 15 / 30 / 60 / 120 / 300 sec

OFF hone par manual refresh

ON hone par live market data automatically refresh

🧠 Critical Live Analysis
Ab signal sirf indicators par depend nahi karega. Analysis mein combine hoga:

Live price & price change

EMA / RSI / MACD / VWAP / ADX

Trend

Volume confirmation

PCR, jab available ho

Recent market/company news

Geopolitical/news risk themes

Crude/RBI/Fed related headlines

Existing BUY/SELL signal

Aur dashboard mein Critical Bias + Analysis Score + News Risk dikhega.

📞 CALL / PUT
Signal ke saath:

CALL candidate — bullish setup

PUT candidate — bearish setup

NO OPTION SIGNAL — mixed/unclear setup

ATM

OTM +1

OTM +2

Strike

Premium

Expiry

Option data unavailable hone par app N/A dikhayega, fabricated price nahi.

🎯 Existing Trade Setup bhi rahega
Entry → SL → Target 1 → Target 2 → Target 3 → Trailing SL → Break-even

Aur critical analysis ko clearly rule-based analysis label kiya hai, guarantee/prediction ke roop mein nahi.

Files
Download updated app.py

Download updated requirements.txt

GitHub mein dono files replace/commit karna hai. requirements.txt mein streamlit-autorefresh bhi add ho gaya hai.


app.py
Code


requirements.txt
Document


No file chosen
Library
/
app.py



1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime
from urllib.parse import quote
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title='Advanced Trading Dashboard', page_icon='📈', layout='wide')

# ---------------- STATE ----------------
for k, v in {'logged_in':False,'paper_trades':[],'realized_pnl':0.0,'balance':100000.0}.items():
    if k not in st.session_state: st.session_state[k] = v

# ---------------- LOGIN ----------------
def login_page():
    st.title('📈 Advanced Trading Dashboard')
    st.caption('Login')
    with st.form('login'):
        u = st.text_input('Username')
        p = st.text_input('Password', type='password')
        ok = st.form_submit_button('Login', use_container_width=True)
