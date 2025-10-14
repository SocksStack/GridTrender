# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GridTrender (GridBNB) is a multi-currency automated grid trading bot for Binance spot trading. It uses adaptive grid strategies with dynamic volatility analysis, position control (S1 strategy), and multi-layer risk management to capture market fluctuations.

## Development Commands

### Environment Setup
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Application
```bash
# Direct Python execution
python main.py

# Docker deployment (recommended for production)
docker-compose up -d --build

# View logs
docker-compose logs -f gridbnb-bot

# Stop services
docker-compose down
```

### Testing
```bash
# Run all tests
python run_tests.py

# Run specific test module
pytest tests/test_trader.py -v
pytest tests/test_risk_manager.py -v
```

### Configuration
- Copy `.env.example` to `.env` and configure:
  - `BINANCE_API_KEY` and `BINANCE_API_SECRET` (required)
  - `SYMBOLS`: Comma-separated list of trading pairs (e.g., "BNB/USDT,ETH/USDT")
  - `INITIAL_PARAMS_JSON`: Per-pair initial settings in JSON format
  - `ENABLE_SAVINGS_FUNCTION`: Enable/disable Binance Earn integration

## Architecture

### Core Trading Flow

1. **Main Entry Point** (`main.py`):
   - Creates shared `ExchangeClient` instance to avoid API rate limiting
   - Spawns concurrent `GridTrader` instances per trading pair
   - Runs periodic global asset monitoring task
   - Starts Web monitoring server

2. **Grid Trader** (`trader.py`):
   - Each instance manages one trading pair independently
   - Main loop separates maintenance tasks from trading decisions:
     - **Maintenance Phase**: Updates S1 levels, adjusts grid size based on volatility
     - **Trading Phase**: Checks risk state, executes buy/sell signals, runs S1 position control
   - State persistence: Saves/loads base_price, grid_size, EWMA volatility, monitoring flags to `data/trader_state_{symbol}.json`

3. **Exchange Client** (`exchange_client.py`):
   - Wraps ccxt.binance with shared connection pooling
   - Implements caching for balance/funding queries (30s TTL)
   - Handles time synchronization with periodic background task
   - Supports Binance Earn (savings) integration with pagination
   - Provides dual asset calculation methods:
     - `calculate_total_account_value()`: Global report (all assets across all pairs)
     - Used by trader's `_get_pair_specific_assets_value()`: Per-pair risk isolation

### Strategy Components

**Dynamic Grid Adjustment** (`trader.py:adjust_grid_size`):
- Calculates hybrid volatility: 70% EWMA + 30% traditional (7-day, 4h candles)
- Smooths volatility over 3 measurements to prevent overreaction
- Uses continuous linear function: `new_grid = base_grid + k * (volatility - center_volatility)`
- Grid range: 1.0% - 4.0%

**Position Control S1** (`position_controller_s1.py`):
- Independent strategy that adjusts positions based on 52-day high/low breakouts
- Updates daily levels from 1d candles
- Executes market orders when:
  - Price > 52-day high AND position > 50%: Sell to 50%
  - Price < 52-day low AND position < 70%: Buy to 70%
- Does NOT modify grid's base_price (unlike main grid trades)
- Respects global risk state from RiskManager

**Risk Management** (`risk_manager.py`):
- Returns `RiskState` enum: `ALLOW_ALL`, `ALLOW_SELL_ONLY`, `ALLOW_BUY_ONLY`
- Position limits: 10% (min) to 90% (max) of pair-specific assets
- Single balance snapshot passed to all checks to ensure consistency

**Order Tracking** (`order_tracker.py`):
- Maintains trade history in `data/trade_history_{symbol}.json`
- Syncs recent trades from exchange on startup (aggregates by order ID)
- Calculates profit for grid trades

### Web Monitoring (`web_server.py`)

- Runs on port 58181 (or via Nginx on port 80)
- Multi-pair dashboard with dropdown selector
- Real-time data: prices, positions, S1 levels, grid bands, trade history
- Optional HTTP Basic Auth (WEB_USER/WEB_PASSWORD)
- API endpoints:
  - `/`: Main dashboard
  - `/api/status?symbol=BNB/USDT`: Trading pair status
  - `/api/symbols`: List all active pairs
  - `/api/logs`: Recent logs

## Key Implementation Details

### Multi-Currency Design
- Each trading pair runs in its own `GridTrader` instance
- Shared `ExchangeClient` prevents API rate limit issues
- Per-pair state files and order tracking for isolation
- Dynamic asset names (`self.base_asset`, `self.quote_asset`) throughout code

### Savings (Binance Earn) Integration
- Controlled by `ENABLE_SAVINGS_FUNCTION` flag
- Target: Keep 16% of pair assets in spot, rest in Binance Earn
- Auto-transfer logic:
  - `_check_and_transfer_initial_funds()`: On startup
  - `_transfer_excess_funds()`: After trades
  - `_ensure_balance_for_trade()`: Before trades (redeems if needed)
- Minimum transfer amounts enforced to avoid API errors
- Pagination support for large portfolios

### State Persistence
- Atomic file writes (temp file + rename) to prevent corruption
- Saves: base_price, grid_size, EWMA state, monitoring flags, volatility history
- Per-pair files in `data/` directory

### Error Handling
- Consecutive error counter: Stops trader after 5 consecutive failures
- Signal checking with retry (3 attempts, 2s delay)
- Order execution retries (10 attempts with 3s check interval)
- PushPlus notifications for critical errors

### Time Synchronization
- Periodic sync task (every 1 hour by default)
- Adjusts all API requests with `time_diff` offset
- Critical for Binance's strict timestamp validation

## Important Configuration Patterns

### Per-Pair Initial Parameters
```json
{
  "BNB/USDT": {
    "initial_base_price": 683.0,
    "initial_grid": 2.0
  },
  "ETH/USDT": {
    "initial_base_price": 3000.0,
    "initial_grid": 2.5
  }
}
```

### Grid Continuous Parameters (config.py)
```python
GRID_CONTINUOUS_PARAMS = {
    'base_grid': 2.5,          # Grid at center volatility
    'center_volatility': 0.25,  # "Normal" market volatility
    'sensitivity_k': 10.0       # Grid change per 1% volatility change
}
```

### Dynamic Interval (config.py)
```python
'volatility_to_interval_hours': [
    {'range': [0, 0.10], 'interval_hours': 1.0},
    {'range': [0.10, 0.20], 'interval_hours': 0.5},
    {'range': [0.20, 0.30], 'interval_hours': 0.25},
    {'range': [0.30, 999], 'interval_hours': 0.125}
]
```

## Common Pitfalls

1. **Don't modify grid's `base_price` from S1 controller** - Only main grid trades should update it
2. **Always pass balance snapshots to risk checks** - Prevents race conditions from refetching
3. **Use `_get_pair_specific_assets_value()` for trading decisions** - Not global account value
4. **Check `ENABLE_SAVINGS_FUNCTION` before savings operations** - Sub-accounts often lack permissions
5. **Respect minimum transfer amounts** - 1.0 USDT, 0.01 BNB (configurable)
6. **State file corruption** - Atomic writes are implemented, but always validate JSON on load
7. **API rate limits** - Use shared ExchangeClient, respect caching TTLs
8. **Precision handling** - Use exchange's `amount_to_precision()` and `price_to_precision()` methods

## File Structure

```
GridTrender/
├── main.py                     # Entry point, multi-pair orchestration
├── trader.py                   # GridTrader class (core strategy)
├── exchange_client.py          # CCXT wrapper with caching
├── position_controller_s1.py   # 52-day breakout strategy
├── risk_manager.py             # Position limits and risk state
├── order_tracker.py            # Trade history persistence
├── config.py                   # Settings and TradingConfig
├── helpers.py                  # Logging, notifications
├── web_server.py               # Monitoring dashboard
├── monitor.py                  # Real-time monitoring utilities
├── data/                       # State files, trade history
│   ├── trader_state_{symbol}.json
│   └── trade_history_{symbol}.json
├── tests/                      # Unit tests
├── nginx/                      # Nginx config for reverse proxy
├── docker-compose.yml          # Multi-container deployment
└── requirements.txt            # Python dependencies
```

## Testing Strategy

- Test files follow pattern: `tests/test_*.py`
- Key areas to test:
  - Risk manager position limit calculations
  - Grid size adjustment logic
  - Order amount calculations
  - State persistence (save/load cycles)
  - Balance checking before trades
  - S1 strategy trigger conditions

## Deployment

**Docker (Production)**:
- Uses `docker-compose.yml` with 3 services: gridbnb-bot, nginx, certbot
- Nginx reverse proxies port 80 → bot's 58181
- Healthchecks ensure container restart on failure
- Persistent volumes for data/ directory

**HTTPS Setup** (Optional):
- Follow `README-https.md` for Certbot integration
- Requires domain name and DNS configuration

## Security Notes

- API keys must have SPOT trading permissions only
- Withdraw permission should be disabled
- Never commit `.env` file (in .gitignore)
- Web auth is optional but recommended for production
- Logs may contain sensitive balance info - secure access

## Multi-Pair Best Practices

- Use same quote currency across all pairs (e.g., all */USDT)
- Monitor global USDT balance to ensure sufficient liquidity
- Each pair's grid/position is independent, but shares spot account
- S1 and main grid trades are sequential per pair (no conflicts)
