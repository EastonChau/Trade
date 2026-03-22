#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import time
import hmac
import hashlib
import logging
import random
import sys
import os
from datetime import datetime, timedelta

# ==================== IMPORT CONFIG ====================
# Import API keys
try:
    from api import API_KEY, SECRET_KEY, BASE_URL
except ImportError:
    print("ERROR: api.py not found. Please create api.py with API_KEY, SECRET_KEY, BASE_URL")
    sys.exit(1)

# ==================== READ STRATEGY ====================
def read_strategy():
    """Read strategy from strategy.txt file"""
    try:
        with open('strategy.txt', 'r') as f:
            strategy = f.read().strip().lower()
            if strategy in ['random', 'stop', 'sell', 'sto']:
                return strategy
            else:
                print(f"Invalid strategy: {strategy}. Using 'random'")
                return 'random'
    except FileNotFoundError:
        print("strategy.txt not found. Creating with default 'random'")
        with open('strategy.txt', 'w') as f:
            f.write('random')
        return 'random'

# ==================== CONFIGURATION ====================
# Trading Parameters
MAX_HOLDINGS = 15
SELL_PROFIT_PCT = 0.02
SELL_LOSS_PCT = -0.01
MIN_VOLUME_USD = 5000000
TICK_INTERVAL = 120
STO_TARGET_PRICE = 0.13  # Target price for STO strategy
STO_DAILY_SELL_USD = 1.0  # Sell $1 worth of STO per day

# Top cryptos list
TOP_CRYPTOS = [
    'BTC', 'ETH', 'SOL', 'XRP', 'PAXG', 'TAO', 'TRX', 'BNB', 'ZEC', 'DOGE',
    'FET', 'ADA', 'LINK', 'AVAX', 'FIL', 'LTC', 'SUI', 'APT', 'NEAR', 'PENGU',
    'XPL', 'WLFI', 'ENA', 'ASTER', 'TON', 'UNI', 'DOT', 'PUMP', 'VIRTUAL', 'WIF',
    'ICP', 'ZEN', 'EIGEN', 'STO', 'AAVE', 'XLM'
]

# Logging - ONLY log trades (BUY/SELL)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler('trade.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

def check_api_keys():
    if not API_KEY.strip() or not SECRET_KEY.strip():
        logger.error("API_KEY and/or SECRET_KEY are empty.")
        return False
    return True

def get_exchange_info():
    url = f"{BASE_URL}/v3/exchangeInfo"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logger.error(f"get_exchange_info failed: {e}")
        return None

def get_all_tickers():
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
        logger.error(f"get_all_tickers failed: {e}")
        return {}

def get_balance():
    if not check_api_keys():
        return None
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
        logger.error(f"get_balance failed: {e}")
        return None

def place_order(pair, side, quantity):
    """Market order only"""
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/place_order"
    payload = {
        'pair': pair,
        'side': side.upper(),
        'type': 'MARKET',
        'quantity': str(quantity),
        'timestamp': get_timestamp_ms()
    }

    headers, payload, params_string = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=params_string, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data
        else:
            err_msg = data.get('ErrMsg', 'Unknown error')
            if "pending" not in err_msg.lower():
                logger.error(f"Place order failed: {err_msg}")
            return None
    except Exception as e:
        logger.error(f"place_order failed: {e}")
        return None

def get_pair_precision(pair):
    """Get amount precision for a trading pair"""
    ex_info = get_exchange_info()
    if ex_info and 'TradePairs' in ex_info:
        pair_info = ex_info['TradePairs'].get(pair)
        if pair_info:
            return pair_info.get('AmountPrecision', 8)
    return 8

# ==================== TRADING BOT ====================
class DiversifiedBot:
    def __init__(self, strategy):
        self.positions = {}
        self.precisions = {}
        self.running = False
        self.strategy = strategy
        self.sto_bought = False
        self.last_daily_sell_date = None  # Track last daily sell date
        
    def load_precisions(self):
        """Load precision settings for all pairs"""
        ex = get_exchange_info()
        if ex and 'TradePairs' in ex:
            for pair, info in ex['TradePairs'].items():
                self.precisions[pair] = info.get('AmountPrecision', 8)
    
    def get_usd_balance(self):
        """Get current USD balance"""
        bal = get_balance()
        if not bal or not bal.get('Success'):
            return 0.0
        wallet = bal.get('SpotWallet') or bal.get('Wallet', {})
        return float(wallet.get('USD', {}).get('Free', 0))
    
    def get_current_holdings(self):
        """Get current crypto holdings from wallet"""
        bal = get_balance()
        if not bal or not bal.get('Success'):
            return {}
        
        wallet = bal.get('SpotWallet') or bal.get('Wallet', {})
        holdings = {}
        
        for asset, amounts in wallet.items():
            if asset != 'USD':
                qty = float(amounts.get('Free', 0))
                if qty > 0:
                    holdings[asset] = qty
        return holdings
    
    def get_eligible_pairs(self, tickers):
        """Get tradable pairs from top cryptos list with sufficient volume"""
        eligible = []
        
        for base in TOP_CRYPTOS:
            pair = f"{base}/USD"
            if pair in tickers:
                data = tickers[pair]
                vol = float(data.get('CoinTradeValue', 0))
                price = float(data.get('LastPrice', 0))
                
                if vol >= MIN_VOLUME_USD and price > 0:
                    eligible.append({
                        'pair': pair,
                        'base': base,
                        'price': price,
                        'volume': vol
                    })
        
        return eligible
    
    def calculate_equal_amount(self, num_positions_to_fill):
        """Calculate equal USD amount for each new position"""
        usd_balance = self.get_usd_balance()
        
        if num_positions_to_fill <= 0:
            return 0
        
        return usd_balance / num_positions_to_fill
    
    def should_sell(self, pair, entry_price, current_price):
        """Check if position should be sold based on profit/loss"""
        # Special handling for STO strategy
        if self.strategy == 'sto' and pair == 'STO/USD':
            if current_price >= STO_TARGET_PRICE:
                return True, f"TARGET REACHED (${STO_TARGET_PRICE})"
            return False, None
        
        # Normal strategy
        pct_change = (current_price - entry_price) / entry_price
        
        if pct_change >= SELL_PROFIT_PCT:
            return True, f"TAKE PROFIT (+{pct_change*100:.2f}%)"
        elif pct_change <= SELL_LOSS_PCT:
            return True, f"STOP LOSS ({pct_change*100:.2f}%)"
        
        return False, None
    
    def sell_daily_sto(self):
        """Sell $1 worth of STO per day"""
        if 'STO/USD' not in self.positions:
            return
        
        # Check if we've already sold today
        today = datetime.now().date()
        if self.last_daily_sell_date == today:
            return
        
        pos = self.positions['STO/USD']
        total_quantity = pos['quantity']
        
        # Get current price
        tickers = get_all_tickers()
        if 'STO/USD' not in tickers:
            return
        
        current_price = float(tickers['STO/USD'].get('LastPrice', 0))
        if current_price <= 0:
            return
        
        # Calculate quantity worth $1 (minimum order requirement)
        min_order_usd = STO_DAILY_SELL_USD
        sell_quantity = min_order_usd / current_price
        
        # Round to precision
        precision = self.precisions.get('STO/USD', 8)
        sell_quantity = round(sell_quantity, precision)
        
        # Cap at available quantity
        if sell_quantity > total_quantity:
            sell_quantity = total_quantity
        
        if sell_quantity <= 0:
            return
        
        logger.info(f"📅 DAILY SELL: Selling ${STO_DAILY_SELL_USD:.2f} worth of STO ({sell_quantity:.2f} units @ ${current_price:.8f})")
        
        result = place_order('STO/USD', 'SELL', sell_quantity)
        
        if result and result.get('Success'):
            order_detail = result.get('OrderDetail', {})
            filled_price = float(order_detail.get('FilledAverPrice', current_price))
            filled_qty = float(order_detail.get('FilledQuantity', sell_quantity))
            
            # Update position quantity
            remaining_qty = total_quantity - filled_qty
            if remaining_qty > 0:
                self.positions['STO/USD']['quantity'] = remaining_qty
            else:
                del self.positions['STO/USD']
            
            logger.info(f"🔴 DAILY SELL STO/USD | {filled_qty:.2f} units @ ${filled_price:.8f} | ${filled_qty * filled_price:.2f}")
            self.last_daily_sell_date = today
            
            # Log remaining holdings
            if remaining_qty > 0:
                logger.info(f"📊 STO remaining: {remaining_qty:.2f} units")
            else:
                logger.info(f"📊 All STO sold out!")
        else:
            # If failed, log error but don't mark as sold
            err_msg = result.get('ErrMsg', 'Unknown error') if result else 'Unknown error'
            logger.warning(f"Failed to sell STO: {err_msg}")
    
    def select_pairs_to_buy(self, eligible_pairs, num_needed):
        """Randomly select pairs that are NOT currently held"""
        current_bases = set(p.split('/')[0] for p in self.positions.keys())
        
        wallet_holdings = self.get_current_holdings()
        for asset in wallet_holdings.keys():
            current_bases.add(asset)
        
        available = [p for p in eligible_pairs if p['base'] not in current_bases]
        
        if len(available) < num_needed:
            num_needed = len(available)
        
        if num_needed <= 0:
            return []
        
        return random.sample(available, num_needed)
    
    def execute_buy(self, pair_info, amount_usd):
        """Execute market buy order for a pair"""
        pair = pair_info['pair']
        price = pair_info['price']
        precision = self.precisions.get(pair, 8)
        
        quantity = amount_usd / price
        quantity = round(quantity, precision)
        
        if quantity <= 0:
            return False
        
        result = place_order(pair, 'BUY', quantity)
        
        if result and result.get('Success'):
            order_detail = result.get('OrderDetail', {})
            filled_price = float(order_detail.get('FilledAverPrice', price))
            filled_qty = float(order_detail.get('FilledQuantity', quantity))
            
            self.positions[pair] = {
                'entry_price': filled_price,
                'quantity': filled_qty
            }
            
            logger.info(f"🟢 BUY {pair} | {filled_qty:.2f} units @ ${filled_price:.8f} | ${filled_qty * filled_price:.2f}")
            return True
        
        return False
    
    def execute_sell(self, pair, reason):
        """Sell entire position for a pair"""
        if pair not in self.positions:
            return False
        
        pos = self.positions[pair]
        precision = self.precisions.get(pair, 8)
        quantity = round(pos['quantity'], precision)
        
        result = place_order(pair, 'SELL', quantity)
        
        if result and result.get('Success'):
            order_detail = result.get('OrderDetail', {})
            filled_price = float(order_detail.get('FilledAverPrice', 0))
            
            logger.info(f"🔴 SELL {pair} | {quantity:.2f} units @ ${filled_price:.8f} | {reason}")
            del self.positions[pair]
            return True
        
        return False
    
    def update_positions(self):
        """Update position tracking based on actual wallet holdings (no logging)"""
        holdings = self.get_current_holdings()
        
        for asset, qty in holdings.items():
            pair = f"{asset}/USD"
            if pair not in self.positions and qty > 0:
                tickers = get_all_tickers()
                if pair in tickers:
                    price = float(tickers[pair].get('LastPrice', 0))
                    self.positions[pair] = {
                        'entry_price': price,
                        'quantity': qty
                    }
        
        for pair in list(self.positions.keys()):
            asset = pair.split('/')[0]
            if asset not in holdings or holdings[asset] == 0:
                del self.positions[pair]
    
    def check_and_sell(self):
        """Check all positions for sell conditions"""
        tickers = get_all_tickers()
        
        for pair, pos in list(self.positions.items()):
            if pair not in tickers:
                continue
            
            current_price = float(tickers[pair].get('LastPrice', 0))
            if current_price <= 0:
                continue
            
            should_sell, reason = self.should_sell(pair, pos['entry_price'], current_price)
            
            if should_sell:
                self.execute_sell(pair, reason)
    
    def sell_all_except_sto(self):
        """Sell all positions except STO"""
        if not self.positions:
            logger.info("No positions to sell")
            return
        
        sold_count = 0
        for pair in list(self.positions.keys()):
            if pair != 'STO/USD':
                logger.info(f"Selling {pair} (part of STO strategy preparation)")
                self.execute_sell(pair, "STO STRATEGY - SELLING ALL EXCEPT STO")
                sold_count += 1
        
        if sold_count > 0:
            logger.info(f"Sold {sold_count} positions. Ready to buy STO.")
        else:
            logger.info("No positions to sell except STO")
    
    def buy_sto_with_all_cash(self):
        """Use all available USD to buy STO"""
        usd_balance = self.get_usd_balance()
        
        if usd_balance <= 0:
            logger.info("No USD balance to buy STO")
            return False
        
        # Get STO price
        tickers = get_all_tickers()
        if 'STO/USD' not in tickers:
            logger.error("STO/USD not found in tickers")
            return False
        
        price = float(tickers['STO/USD'].get('LastPrice', 0))
        if price <= 0:
            logger.error("Invalid STO price")
            return False
        
        precision = self.precisions.get('STO/USD', 8)
        quantity = usd_balance / price
        quantity = round(quantity, precision)
        
        if quantity <= 0:
            return False
        
        logger.info(f"🚀 STO STRATEGY: Buying STO with all ${usd_balance:,.2f}")
        result = place_order('STO/USD', 'BUY', quantity)
        
        if result and result.get('Success'):
            order_detail = result.get('OrderDetail', {})
            filled_price = float(order_detail.get('FilledAverPrice', price))
            filled_qty = float(order_detail.get('FilledQuantity', quantity))
            
            self.positions['STO/USD'] = {
                'entry_price': filled_price,
                'quantity': filled_qty
            }
            self.sto_bought = True
            
            logger.info(f"🟢 BUY STO/USD | {filled_qty:.2f} units @ ${filled_price:.8f} | ${filled_qty * filled_price:.2f}")
            return True
        
        return False
    
    def sell_all_positions(self):
        """Sell all current positions (for 'sell' strategy)"""
        if not self.positions:
            logger.info("No positions to sell")
            return
        
        logger.info(f"SELL ALL strategy: Selling {len(self.positions)} positions")
        for pair in list(self.positions.keys()):
            self.execute_sell(pair, "SELL ALL STRATEGY")
    
    def run(self):
        """Main bot loop"""
        if not check_api_keys():
            logger.error("API keys missing")
            return
        
        self.load_precisions()
        self.running = True
        
        # First, sync with existing wallet holdings
        self.update_positions()
        
        # Handle 'sell' strategy
        if self.strategy == 'sell':
            logger.info("=" * 60)
            logger.info("SELL STRATEGY ACTIVE - Selling all positions")
            logger.info("=" * 60)
            self.sell_all_positions()
            logger.info("Sell strategy completed. Bot will now idle.")
            while self.running:
                time.sleep(TICK_INTERVAL)
            return
        
        # Handle 'sto' strategy
        if self.strategy == 'sto':
            logger.info("=" * 60)
            logger.info("STO STRATEGY ACTIVE")
            logger.info(f"Target Price: ${STO_TARGET_PRICE:.2f}")
            logger.info(f"Daily Sell: ${STO_DAILY_SELL_USD:.2f} worth of STO")
            logger.info("=" * 60)
            
            # Check if we already have STO position
            if 'STO/USD' in self.positions:
                logger.info(f"Existing STO position detected: {self.positions['STO/USD']['quantity']:.2f} units")
            else:
                # Step 1: Sell all crypto except STO
                logger.info("Step 1: Selling all positions except STO...")
                self.sell_all_except_sto()
                
                # Step 2: Buy STO with all cash
                logger.info("Step 2: Buying STO with all available cash...")
                self.buy_sto_with_all_cash()
            
            # Step 3: Monitor STO price and sell daily
            if 'STO/USD' in self.positions:
                logger.info(f"Step 3: Monitoring STO price. Target: ${STO_TARGET_PRICE:.2f}")
                logger.info(f"Daily sell: ${STO_DAILY_SELL_USD:.2f} worth of STO")
                
                while self.running:
                    try:
                        self.update_positions()
                        
                        # Sell $1 worth of STO per day
                        self.sell_daily_sto()
                        
                        # Check for target price sell
                        self.check_and_sell()
                        
                        # Check if STO is still held
                        if 'STO/USD' not in self.positions:
                            logger.info("STO position fully sold. STO strategy complete.")
                            break
                        
                        time.sleep(TICK_INTERVAL)
                        
                    except KeyboardInterrupt:
                        logger.info("Bot stopped by user")
                        self.running = False
                    except Exception as e:
                        logger.error(f"Error: {e}")
                        time.sleep(10)
            else:
                logger.error("Failed to buy STO. Strategy aborted.")
            return
        
        # Handle 'stop' strategy
        if self.strategy == 'stop':
            logger.info("=" * 60)
            logger.info("STOP STRATEGY ACTIVE - No trading will occur")
            logger.info("=" * 60)
            while self.running:
                time.sleep(TICK_INTERVAL)
            return
        
        # Normal 'random' strategy
        logger.info("=" * 60)
        logger.info("DIVERSIFIED TRADING BOT STARTED")
        logger.info(f"Strategy: {self.strategy.upper()}")
        logger.info(f"Max Holdings: {MAX_HOLDINGS}")
        logger.info(f"Sell at +{SELL_PROFIT_PCT*100}% or -{abs(SELL_LOSS_PCT)*100}%")
        logger.info(f"Check interval: {TICK_INTERVAL} seconds")
        logger.info("=" * 60)
        
        while self.running:
            try:
                self.update_positions()
                self.check_and_sell()
                
                current_holdings = len(self.positions)
                
                if current_holdings < MAX_HOLDINGS:
                    needed = MAX_HOLDINGS - current_holdings
                    
                    tickers = get_all_tickers()
                    eligible = self.get_eligible_pairs(tickers)
                    
                    if eligible:
                        equal_amount = self.calculate_equal_amount(needed)
                        
                        if equal_amount > 0:
                            to_buy = self.select_pairs_to_buy(eligible, needed)
                            
                            if to_buy:
                                logger.info(f"📊 Buying {len(to_buy)} new positions (current: {current_holdings}/{MAX_HOLDINGS})")
                                
                                for pair_info in to_buy:
                                    self.execute_buy(pair_info, equal_amount)
                                    time.sleep(0.5)
                
                time.sleep(TICK_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                self.running = False
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(10)

if __name__ == "__main__":
    # Read strategy first
    strategy = read_strategy()
    print(f"Strategy loaded: {strategy}")
    
    # Create and run bot
    bot = DiversifiedBot(strategy)
    bot.run()
