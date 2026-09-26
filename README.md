# Trading---Dashboard
Trading Anlysis
# Advanced Trading Dashboard

Streamlit dashboard based on the supplied `app.py`, expanded to cover the consolidated trading-dashboard requirements.

## Included
- Login: `admin` / `admin123` (demo/local credentials)
- Demo password recovery with recovery PIN `1234` (override with `TRADING_RECOVERY_PIN`)
- Live/paper-analysis workflow
- Auto refresh enable/disable and interval
- Market status / IST clock
- NIFTY, BANK NIFTY, SENSEX, sector indices, NSE stocks, crypto and custom Yahoo symbols
- EMA 5/21/50/200, RSI, MACD, VWAP, ATR, ADX, Bollinger Bands, Stochastic, CCI, pivot/support/resistance and Fibonacci
- Candlestick and approximate chart-pattern detection
- Multi-factor BUY/SELL/WAIT signal and critical analysis
- Entry, Target 1/2/3, stop loss, break-even and trailing stop
- ATM/nearby OTM option suggestions when a Yahoo option chain is available
- PCR/OI/volume option-chain analysis when available
- Paper positions with live P/L and optional automatic target/trailing-SL exit
- Risk-based position sizing and max position value
- Historical backtest with signal stats and drawdown/return metrics
- Advance/Decline dashboard
- USD/INR, crude, gold and global/market context
- Live market/news/geopolitical headline context with transparent keyword biasing
- NSE FII/DII feed when the official endpoint is reachable
- Pre-open snapshot attempt and clearly labelled fallback estimate
- Broker integration placeholders; no real order is placed by default

## Install
```bash
pip install -r requirements.txt
streamlit run app.py
```

### Android / Termux
```bash
pip install -r requirements.txt
python run_android.py
```
Then open the Streamlit URL shown by the terminal, normally port `8501`.

## Important
Yahoo/NSE endpoints can be delayed, rate-limited, blocked, or incomplete. FII/DII, options, news and live prices are displayed only when the source responds. The app does not fabricate missing market data.

Paper trading is simulation only. Real broker execution requires the broker's current official API, secure credentials, explicit order controls and additional production safeguards.
