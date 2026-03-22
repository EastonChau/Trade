cat > mon.py << 'EOF'

#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import time
import hmac
import hashlib
import os
import sys
from datetime import datetime

# ==================== CONFIG ====================
try:
    from api import API_KEY, SECRET_KEY, BASE_URL
except ImportError:
    print("ERROR: api.py not found")
    sys.exit(1)

LOG_FILE = "trade.log"

# ==================== API FUNCTIONS ====================
def get_timestamp_ms():
    return str(int(time.time() * 1000))

def generate_signature(payload):
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        total_params.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, total_params

def get_signed_headers(payload=None):
    if payload is None:
        payload = {}
    if 'timestamp' not in payload:
        payload['timestamp'] = get_timestamp_ms()
    signature, params_string = generate_signature(payload)
    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature,
    }
    return headers, payload, params_string

def get_balance():
    """Get wallet balance"""
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data
        return None
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None

def get_all_tickers():
    """Get all tickers"""
    url = f"{BASE_URL}/v3/ticker"
    params = {'timestamp': get_timestamp_ms()}
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data.get('Data', {})
        return {}
    except Exception as e:
        print(f"Error getting tickers: {e}")
        return {}

# ==================== MONITOR FUNCTIONS ====================
def get_balance_info():
    """Get current wallet balance and holdings"""
    bal = get_balance()
    if not bal or not bal.get('Success'):
        return None

    wallet = bal.get('SpotWallet', {})
    tickers = get_all_tickers()

    info = {
        'usd_free': 0,
        'usd_locked': 0,
        'holdings': [],
        'total_value': 0
    }

    for asset, amounts in wallet.items():
        free = float(amounts.get('Free', 0))
        locked = float(amounts.get('Lock', 0))
        total = free + locked

        if asset == 'USD':
            info['usd_free'] = free
            info['usd_locked'] = locked
            info['total_value'] += free + locked
        else:
            pair = f"{asset}/USD"
            price = 0
            if pair in tickers:
                price = float(tickers[pair].get('LastPrice', 0))
            value = total * price
            info['total_value'] += value
            if total > 0:
                info['holdings'].append({
                    'asset': asset,
                    'quantity': total,
                    'price': price,
                    'value': value
                })

    return info

def get_trade_history(limit=20):
    """Get recent trades from log file"""
    trades = []

    if not os.path.exists(LOG_FILE):
        return trades

    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()

    for line in lines[-500:]:
        # Look for BUY/SELL trades (🟢 or 🔴)
        if '🟢 BUY' in line or '🔴 SELL' in line:
            try:
                timestamp = line.split(',')[0]
                
                if '🟢 BUY' in line:
                    # Format: 🟢 BUY BTC/USD | 0.05 units @ $85000.00 | $4250.00
                    parts = line.split('|')
                    if len(parts) >= 2:
                        # Extract pair
                        pair_part = parts[0].split('🟢 BUY')[1].strip()
                        pair = pair_part.split()[0] if pair_part else 'Unknown'
                        
                        # Extract size and price
                        size = 'N/A'
                        price = 'N/A'
                        for part in parts:
                            if 'units @ $' in part:
                                size_price = part.strip().split('units @ $')
                                if len(size_price) >= 2:
                                    size = size_price[0].strip()
                                    price = size_price[1].split()[0].strip()
                        
                        trades.append({
                            'timestamp': timestamp,
                            'type': 'BUY',
                            'pair': pair,
                            'size': size,
                            'price': price
                        })
                        
                elif '🔴 SELL' in line:
                    # Format: 🔴 SELL BTC/USD | 0.05 units @ $86700.00 | TAKE PROFIT (+2.00%)
                    parts = line.split('|')
                    if len(parts) >= 2:
                        # Extract pair
                        pair_part = parts[0].split('🔴 SELL')[1].strip()
                        pair = pair_part.split()[0] if pair_part else 'Unknown'
                        
                        # Extract size, price, and reason
                        size = 'N/A'
                        price = 'N/A'
                        reason = 'Unknown'
                        
                        for part in parts:
                            if 'units @ $' in part:
                                size_price = part.strip().split('units @ $')
                                if len(size_price) >= 2:
                                    size = size_price[0].strip()
                                    price = size_price[1].split()[0].strip()
                            if 'TAKE PROFIT' in part or 'STOP LOSS' in part:
                                reason = part.strip()
                        
                        trades.append({
                            'timestamp': timestamp,
                            'type': 'SELL',
                            'pair': pair,
                            'size': size,
                            'price': price,
                            'reason': reason
                        })
            except Exception as e:
                continue

    return trades[-limit:]

def get_strategy_status():
    """Read current strategy from strategy.txt"""
    try:
        with open('strategy.txt', 'r') as f:
            strategy = f.read().strip().lower()
            return strategy
    except FileNotFoundError:
        return 'random'

def calculate_statistics(trades):
    """Calculate win/loss statistics"""
    if not trades:
        return {'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'breakeven': 0}

    wins = 0
    losses = 0
    
    for trade in trades:
        if trade['type'] == 'SELL':
            if 'TAKE PROFIT' in trade.get('reason', ''):
                wins += 1
            elif 'STOP LOSS' in trade.get('reason', ''):
                losses += 1

    total = wins + losses

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_rate': (wins / total * 100) if total > 0 else 0
    }

def get_current_holdings_count():
    """Get number of current holdings from wallet"""
    bal = get_balance()
    if not bal or not bal.get('Success'):
        return 0
    
    wallet = bal.get('SpotWallet', {})
    count = 0
    for asset, amounts in wallet.items():
        if asset != 'USD':
            qty = float(amounts.get('Free', 0))
            if qty > 0:
                count += 1
    return count

def display():
    """Display all information"""
    print("\n" + "☀️"*150)
    print("\n" + "☀️"*120)
    print("\n" + "☀️"*100)
    print("  Trader Model ver. 4.0 (Random Strategy) - MONITOR")
    print("="*70)
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    # Strategy Status
    strategy = get_strategy_status()
    strategy_emoji = {
        'random': '🔄',
        'stop': '⏸️',
        'sell': '🔴'
    }.get(strategy, '❓')
    print(f"\n🎮 STRATEGY: {strategy_emoji} {strategy.upper()}")
    print("-"*50)

    # 1. Balance Information
    print("\n💰 BALANCE & HOLDINGS")
    print("-"*50)
    balance = get_balance_info()
    if balance:
        print(f"  USD Free:    ${balance['usd_free']:,.2f}")
        print(f"  USD Locked:  ${balance['usd_locked']:,.2f}")
        print(f"  Total Cash:  ${balance['usd_free'] + balance['usd_locked']:,.2f}")

        if balance['holdings']:
            print(f"\n  🪙 Crypto Holdings ({len(balance['holdings'])} positions):")
            for h in balance['holdings']:
                print(f"    {h['asset']:8} {h['quantity']:>20,.0f} units @ ${h['price']:.8f} = ${h['value']:,.2f}")
        else:
            print("\n  🪙 Crypto Holdings: None")

        print(f"\n  💰 Total Portfolio Value: ${balance['total_value']:,.2f}")
    else:
        print("  ❌ Failed to fetch balance - Check API keys")

    # 2. Holdings Count
    current_count = get_current_holdings_count()
    print(f"\n📊 HOLDINGS: {current_count} / 15")
    print("-"*50)

    # 3. Recent Trades
    print("\n🔄 RECENT TRADES (last 5)")
    print("-"*50)
    recent = get_trade_history(5)
    if recent:
        for trade in recent:
            if trade['type'] == 'BUY':
                print(f"  {trade['timestamp']} | 🟢 BUY {trade['pair']} | {trade['size']} units @ ${trade['price']}")
            else:
                emoji = '🔴' if 'STOP LOSS' in trade.get('reason', '') else '🟢'
                print(f"  {trade['timestamp']} | {emoji} SELL {trade['pair']} | {trade['size']} units @ ${trade['price']} | {trade.get('reason', '')}")
    else:
        print("  No recent trades")

    # 4. Trade Statistics
    all_trades = get_trade_history(100)
    stats = calculate_statistics(all_trades)
    print("\n📈 TRADE STATISTICS")
    print("-"*50)
    print(f"  Total Trades:   {stats['total']}")
    print(f"  Wins:           {stats['wins']} 🟢")
    print(f"  Losses:         {stats['losses']} 🔴")
    print(f"  Win Rate:       {stats['win_rate']:.1f}%")

    # 5. System Status
    print("\n🖥️ SYSTEM STATUS")
    print("-"*50)
    if os.path.exists(LOG_FILE):
        log_size = os.path.getsize(LOG_FILE) / 1024
        print(f"  Log File:       {LOG_FILE} ({log_size:.1f} KB)")
        print(f"  Last Updated:   {datetime.fromtimestamp(os.path.getmtime(LOG_FILE)).strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print(f"  Log File:       {LOG_FILE} (NOT FOUND)")

    # 6. Strategy Tips
    print("\n💡 STRATEGY TIPS")
    print("-"*50)
    print("  To change strategy, edit strategy.txt:")
    print("    echo 'random' > strategy.txt   # Normal trading")
    print("    echo 'stop' > strategy.txt     # Stop trading")
    print("    echo 'sell' > strategy.txt     # Sell all positions")
    print("  Then restart the bot to apply changes.")

    print("\n" + "="*70)
    print("  Monitor by Easton | Run: python3 monitor.py")
    print("☀️"*100 + "\n")
    print("☀️"*120 + "\n")
    print("☀️"*150 + "\n")

if __name__ == "__main__":
    if not API_KEY or not SECRET_KEY:
        print("\n⚠️ WARNING: API keys not set in api.py!")
        print("   Edit api.py and add your keys.\n")
    
    display()

EOF
