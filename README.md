# Diversified Trading Bot

A Python-based cryptocurrency trading bot that maintains a diversified portfolio of up to 15 cryptocurrencies, randomly selecting from top coins and automatically selling at profit/loss targets.

## Features

- **Diversified Portfolio**: Holds up to 15 different cryptocurrencies simultaneously
- **Equal Position Sizing**: Divides available cash equally among all positions
- **Random Selection**: Randomly picks from 36 cryptocurrencies, chosen manually
- **Automated Trading**: 
  - Sells when price increases by +2% (Take Profit)
  - Sells when price decreases by -1% (Stop Loss)
  - Risk/Reward Ratio 1:2
- **Strategy Switching**: Can change trading behavior via simple text file
- **Market Orders Only**: Uses market orders for reliable execution
- **Minimal Logging**: Only logs trades (BUY/SELL)

## Files

| File | Purpose |
|------|---------|
| `trader4.py` | Main trading bot |
| `api.py` | API keys configuration |
| `strategy.txt` | Strategy selection file |
| `trade.log` | Trade history log |
| `mon.py` | Status monitor (optional) |
