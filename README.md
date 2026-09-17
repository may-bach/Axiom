# Axiom

Intraday breakout trading system for the National Stock Exchange (NSE), integrated with Flattrade API. Written in Go for low latency and minimal resource usage, with Python micro-services for morning token synchronization and pre-market regime detection.

## Architecture

- Core Engine (`cmd/main.go`): Native Go binary polling real-time quotes, tracking price levels, and managing trade entries and exits.
- Market Sentinel (`sentinel.py`): Transient pre-market scanner combining financial news sentiment and India VIX volatility into a risk regime.
- Morning Auto Sync (`auto_sync.py`): Headless token acquisition service that refreshes session keys daily at 08:00 AM IST.
- Audit Ledger (`ledger.py`): Performance analytics engine that records trade executions to CSV and builds summary reports.

## Strategy and Risk Management

### Entry Conditions
- Breakout Long: Triggered when LTP crosses above established High by stock-specific threshold.
- Breakdown Short: Triggered when LTP drops below established Low by stock-specific threshold.
- Mean Reversion: Secondary buy on rebound off support and quick-drop short logic.

### Exit Conditions
- Target: Stock-specific profit target (typically 2.0% to 3.5%).
- Trailing Stop-Loss: Dynamic stop-loss trailing highest/lowest price (default 1.0%).
- Fixed Stop-Loss: Hard floor stop (1.0% to 1.5%).
- Stagnation Timeout: Automatically exits trades that remain flat within +/- 0.4% after 45 minutes to avoid capital lockup during chop.
- EOD Square-Off: All open positions automatically squared off at 15:10 IST.

### Macro and Volatility Sentinel
Before market opens, `sentinel.py` evaluates market regime using two inputs:
1. News Sentiment: Scans RSS feeds (Economic Times, Livemint, Moneycontrol) with weighted financial lexicons for geopolitical conflict, rate hikes, budget/election events, and chop.
2. Market Implied Volatility: Fetches real-time India VIX and Nifty 50 levels.

Regimes:
- NORMAL (Risk score < 25, VIX < 16): Up to 3 concurrent positions, 15,000 INR position sizing.
- CAUTION (Risk score 25-69, VIX 17.5-25): Max 1 position, 10,000 INR conservative position sizing.
- CRISIS (Risk score >= 70, direct war/crash keywords, or VIX >= 25): Trading halted for the day (0 positions, 100% cash).

### Account Circuit Breaker
- Daily Loss Limit: -750 INR. If cumulative daily PnL drops to or below -750 INR, new entries are disabled for the remainder of the session.

## Project Structure

```
Axiom/
├── cmd/
│   └── main.go              # Main trading engine
├── internal/
│   ├── auth/                # Flattrade API authentication
│   ├── client/              # REST client for PiConnectAPI
│   ├── config/              # Environment and strategy config loaders
│   ├── session/             # Global session token management
│   └── stocks/              # Stock list loader
├── data/
│   ├── config.json          # Per-stock strategy parameters (thresholds, targets, SL)
│   ├── stocks.json          # Watchlist symbols
│   ├── token_map.json       # Symbol to exchange token cache
│   ├── regime.json          # Daily sentinel risk verdict
│   └── trades_ledger.csv    # Persistent trade execution history
├── sentinel.py              # Macro and volatility scanner
├── auto_sync.py             # Headless morning token refresher
├── ledger.py                # Audit and statistics generator
├── pnl.sh                   # Server-side intraday PnL report
├── start.sh / stop.sh       # Server process control scripts
├── check_pnl.bat            # Windows one-click PnL viewer
└── view_ledger.bat          # Windows one-click audit ledger viewer
```

## Configuration

Create a `.env` file in the project root:

```env
FLAT_USER_ID=your_client_id
FLAT_PASSWORD=your_password
FLAT_PIN=your_totp_or_pin
FLAT_API_KEY=your_api_key
FLAT_SECRET_KEY=your_secret_key
FLAT_REQUEST_CODE=your_initial_request_code
```

Strategy parameters for individual symbols can be tuned in `data/config.json`.

## Usage

### Local Build (Cross-compile for Linux)

```powershell
$env:GOOS="linux"; $env:GOARCH="amd64"; go build -o axiom cmd/main.go
```

### Running on Server

```bash
# Start the bot in the background
./start.sh

# Check current status and running process
./status.sh

# Stop the bot
./stop.sh

# View today's PnL and trade log
./pnl.sh
```

### Automation via Cron

Recommended server crontab (Asia/Kolkata timezone):

```cron
CRON_TZ=Asia/Kolkata
0 8 * * 1-5 /usr/bin/python3 /home/opc/Axiom/auto_sync.py >> /home/opc/Axiom/logs/auto_sync.log 2>&1
15 8 * * 1-5 /usr/bin/python3 /home/opc/Axiom/sentinel.py >> /home/opc/Axiom/logs/sentinel.log 2>&1
45 8 * * 1-5 /usr/bin/python3 /home/opc/Axiom/auto_sync.py >> /home/opc/Axiom/logs/auto_sync.log 2>&1
0 9 * * 1-5 /usr/bin/python3 /home/opc/Axiom/sentinel.py >> /home/opc/Axiom/logs/sentinel.log 2>&1
@reboot sleep 10 && /home/opc/Axiom/start.sh >> /home/opc/Axiom/logs/reboot.log 2>&1
```

### Viewing Reports from Windows

- `check_pnl.bat`: Connects to the server and displays today's live PnL and sentinel regime.
- `view_ledger.bat`: Runs the audit engine, syncs `trades_ledger.csv` and `ledger.html` locally, and opens the performance dashboard.
