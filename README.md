# Trading Intelligence Terminal

A Python and Streamlit starter application for market analysis,
technical indicators, signal scoring, risk/reward calculations,
basic backtesting, and trade journaling.

## Features

- Application login gate
- Instrument selection
- NIFTY, BANK NIFTY, SENSEX and stock symbol input
- Manual Angel One API login form
- Historical candle retrieval through SmartAPI
- Demo data mode for testing the interface
- EMA 20 and EMA 50
- RSI
- ATR
- VWAP
- Rule-based BUY, SELL and WAIT signals
- Risk/reward calculator
- Basic EMA crossover backtest
- Trade journal and CSV export

## Requirements

- Python 3.10 or newer
- Angel One SmartAPI account for broker-data testing
- Valid API key and broker login credentials
- Correct exchange and symbol token

## Installation

Open a terminal in the project directory.

Install dependencies:

    pip install -r requirements.txt

## Configure Application Login

Set the environment variable APP_USERNAME.

Generate a SHA-256 password hash by running:

    python -c "import hashlib; print(hashlib.sha256(b'YOUR-STRONG-PASSWORD').hexdigest())"

Set the resulting hash as APP_PASSWORD_SHA256.

Example for macOS/Linux:

    export APP_USERNAME="admin"
    export APP_PASSWORD_SHA256="PASTE_HASH_HERE"

On Windows PowerShell:

    $env:APP_USERNAME="admin"
    $env:APP_PASSWORD_SHA256="PASTE_HASH_HERE"

Use your own username and password. Do not commit credentials to source control.

## Start the Application

Run:

    streamlit run app.py

Streamlit will print a local address in the terminal. Open that address
in your browser.

## Demo Mode

Choose Demo Data in the sidebar and click Fetch / Refresh Market Data.

Demo prices are synthetic. They are not actual exchange prices.

## Manual Angel One Connection

1. Choose Angel One API in the Data Source selector.
2. Enter your API key, client code, trading PIN, and TOTP secret.
3. Enter the correct exchange and symbol token.
4. Click Connect to Angel One.
5. Click Fetch / Refresh Market Data.

The broker's API and SDK behavior may change. Verify the integration against
the current official Angel One SmartAPI documentation.

Never share API keys, PINs, OTPs, TOTP secrets, or session tokens.
Do not expose a credential-entry application publicly.

## Important Limitations

This is a starter project, not a production-ready trading system.

The current implementation does not include:

- WebSocket real-time tick streaming
- Guaranteed zero-delay market data
- Option-chain analysis
- PCR and open-interest analysis
- FII/DII data integration
- Global market feeds
- News and sentiment analysis
- Production-grade authentication
- Live order placement or automated trading
- Calibrated signal win probabilities

The backtest is illustrative and excludes brokerage, taxes, slippage,
partial fills, latency, and other real execution costs.

A signal score is not a probability of winning. Evaluate strategies on
historical data and out-of-sample periods, include all costs, and test
using paper trading before considering any live trading workflow.

## Security

- Keep credentials out of source files and Git repositories.
- Prefer environment variables or a secure secrets manager.
- Use a trusted local environment while testing.
- Rotate credentials if they may have been exposed.
- Do not rely on this starter's login gate as production security.
