#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import time
import hmac
import hashlib
import logging
import sys
from collections import deque
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('heavy_spot_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== CONFIG / API KEYS ====================
API_KEY = ""
SECRET_KEY = ""

BASE_URL = "https://mock-api.roostoo.com"

# ==================== STRATEGY PARAMETERS ====================
RISK_PERCENT = 0.05               # 1. Math of Heavy Spot Sizing: Risk 5% of equity
MIN_VOLUME_USD = 150_000_000      # 2. Liquidity Filtering: $150M minimum 24h volume
TIME_STOP_MINUTES = 20            # 3. Capital Velocity: 15-20 mins to breakout or exit
TICK_INTERVAL = 60                # 1-minute candles (seconds)
VWAP_PERIOD = 60                  # Rolling window for VWAP calculation (60 mins)
SL_DISTANCE_PCT = 0.05            # Technical Stop Loss distance (e.g., 5% below VWAP)

# ==================== API HELPER FUNCTIONS ====================
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
        logger.error("API_KEY and/or SECRET_KEY are empty. Please add your keys.")
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
    if not check_api_keys(): return None
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

def place_order(pair, side, quantity, price=None):
    if not check_api_keys(): return None
    url = f"{BASE_URL}/v3/place_order"
    order_type = "LIMIT" if price else "MARKET"
    payload = {
        'pair': pair,
        'side': side.upper(),
        'type': order_type,
        'quantity': str(quantity),
        'timestamp': get_timestamp_ms()
    }
    if price is not None:
        payload['price'] = str(price)

    headers, payload, params_string = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=params_string, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data
        else:
            logger.error(f"Place order failed: {data.get('ErrMsg', 'Unknown error')}")
            return None
    except Exception as e:
        logger.error(f"place_order failed: {e}")
        return None

def cancel_order(order_id=None, pair=None):
    if not check_api_keys(): return None
    url = f"{BASE_URL}/v3/cancel_order"
    payload = {'timestamp': get_timestamp_ms()}
    if order_id: payload['order_id'] = str(order_id)
    if pair: payload['pair'] = pair

    headers, payload, params_string = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=params_string, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        logger.error(f"cancel_order failed: {e}")
        return None

# ==================== STRATEGY LOGIC ====================

class MarketData:
    """Tracks 1-minute rolling candles, VWAP, and 9 EMA per pair."""
    def __init__(self):
        self.prices = deque(maxlen=VWAP_PERIOD)
        self.volumes = deque(maxlen=VWAP_PERIOD) # 1m volumes
        self.ema_9 = None
        self.prev_24h_vol = 0
        self.last_price = 0

    def update(self, price, current_24h_vol):
        # Calculate 1m volume from 24h accumulated volume
        vol_1m = max(0, current_24h_vol - self.prev_24h_vol) if self.prev_24h_vol else 0
        self.prev_24h_vol = current_24h_vol

        self.prices.append(price)
        self.volumes.append(vol_1m)
        self.last_price = price

        # Update 9 EMA
        if self.ema_9 is None:
            self.ema_9 = price
        else:
            k = 2 / (9 + 1)
            self.ema_9 = (price * k) + (self.ema_9 * (1 - k))

    @property
    def vwap(self):
        """Calculate Rolling VWAP."""
        rolling_v = sum(self.volumes)
        if rolling_v == 0:
            return self.prices[-1] if self.prices else 0
        rolling_pv = sum(p * v for p, v in zip(self.prices, self.volumes))
        return rolling_pv / rolling_v

class HeavySpotTrader:
    def __init__(self):
        self.market_data = {}
        self.active_setup = None # Since we go heavy/all-in, handle one setup at a time.
        self.precisions = {}
        self.running = False

    def get_pair_precision(self, pair):
        return self.precisions.get(pair, 8)

    def load_precisions(self):
        ex = get_exchange_info()
        if ex and 'TradePairs' in ex:
            for pair, info in ex['TradePairs'].items():
                self.precisions[pair] = info.get('AmountPrecision', 8)

    def get_usd_balance(self):
        bal = get_balance()
        if not bal or not bal.get('Success'): return 0.0
        wallet = bal.get('SpotWallet') or bal.get('Wallet', {})
        return float(wallet.get('USD', {}).get('Free', 0))

    def calculate_heavy_spot_size(self, pair, current_price, vwap_price):
        """Rule 1: The Math of Heavy Spot Sizing"""
        capital = self.get_usd_balance()
        if capital <= 0: return 0
        
        # Risk Amount = Capital * 5%
        risk_amount = capital * RISK_PERCENT
        
        # Stop-Loss Distance (Technical stop below VWAP)
        sl_price = vwap_price * (1 - SL_DISTANCE_PCT)
        sl_distance = (current_price - sl_price) / current_price
        
        # Position Size = (Total Capital * Risk %) / Stop-Loss Distance %
        target_position_usd = risk_amount / max(sl_distance, 0.001) 
        
        # Cap at 99% of cash balance to strictly avoid leverage/margin usage
        target_position_usd = min(target_position_usd, capital * 0.99)
        
        target_qty = target_position_usd / current_price
        return round(target_qty, self.get_pair_precision(pair))

    def execute_iceberg_entry(self, current_time, pair, md):
        """Rule 4: Smart Execution (Iceberg Entry)"""
        s = self.active_setup
        price = md.last_price

        # Phase 2: Wait 1 minute for pullback
        if s['phase'] == 1 and (current_time - s['entry_time']) >= TICK_INTERVAL:
            if price < s['initial_price']:  # Pullback condition
                logger.info(f"[{pair}] Phase 2: Pullback detected. Executing 2nd 25%.")
                qty = round(s['target_qty'] * 0.25, self.get_pair_precision(pair))
                res = place_order(pair, 'BUY', qty)
                if res and res.get('Success'):
                    s['filled_qty'] += qty
                    s['phase'] = 2
                else:
                    logger.warning(f"Failed Phase 2 execution.")

        # Phase 3: Resting limit order exactly on 9 EMA
        if s['phase'] == 2:
            logger.info(f"[{pair}] Phase 3: Placing remaining 50% limit order at 9 EMA (${md.ema_9:.4f}).")
            qty = round(s['target_qty'] * 0.50, self.get_pair_precision(pair))
            
            res = place_order(pair, 'BUY', qty, price=round(md.ema_9, 4))
            if res and res.get('Success'):
                s['limit_order_id'] = res['OrderDetail'].get('OrderId')
                s['phase'] = 3
                logger.info(f"[{pair}] Setup Iceberg Entry completed and resting.")

    def manage_positions_and_exits(self, current_time, pair, md):
        """Rule 3: Capital Velocity (Time Stops & Breakeven)"""
        if not self.active_setup or self.active_setup['pair'] != pair:
            return

        s = self.active_setup
        price = md.last_price
        time_in_trade = (current_time - s['entry_time']) / 60.0 # in minutes

        # Check technical Stop Loss (below VWAP)
        sl_price = s['vwap_at_entry'] * (1 - SL_DISTANCE_PCT)
        
        time_stop_triggered = time_in_trade >= TIME_STOP_MINUTES and price <= (s['initial_price'] * 1.002)
        hard_stop_triggered = price <= sl_price

        if time_stop_triggered or hard_stop_triggered:
            reason = "TIME STOP (No Breakout)" if time_stop_triggered else "HARD STOP LOSS"
            logger.warning(f"[{pair}] EXIT TRIGGERED: {reason}. Liquidating position to free capital.")
            
            # 1. Cancel resting 9 EMA limit order if it exists
            if s.get('limit_order_id'):
                cancel_order(order_id=s['limit_order_id'], pair=pair)
            
            # 2. Market sell all filled quantity
            if s['filled_qty'] > 0:
                qty_to_sell = round(s['filled_qty'], self.get_pair_precision(pair))
                place_order(pair, 'SELL', qty_to_sell)
            
            # 3. Clear setup to free bot for next chart
            self.active_setup = None

    def run(self):
        if not check_api_keys(): return
        self.load_precisions()
        self.running = True
        logger.info("Heavy Spot Trading Bot started. Waiting to build initial 1m candles...")

        while self.running:
            try:
                start_time = time.time()
                tickers = get_all_tickers()
                
                valid_pairs = []
                # Rule 2: Liquidity Filtering (Avoiding Slippage Trap)
                for pair, d in tickers.items():
                    vol_24h = float(d.get('CoinTradeValue', 0))
                    price = float(d.get('LastPrice', 0))
                    
                    if vol_24h >= MIN_VOLUME_USD and price > 0:
                        valid_pairs.append((pair, price, vol_24h))

                for pair, price, vol in valid_pairs:
                    if pair not in self.market_data:
                        self.market_data[pair] = MarketData()
                    
                    md = self.market_data[pair]
                    prev_vwap = md.vwap
                    md.update(price, vol)

                    # Manage existing setups (Exits / Iceberg progression)
                    if self.active_setup and self.active_setup['pair'] == pair:
                        self.execute_iceberg_entry(start_time, pair, md)
                        self.manage_positions_and_exits(start_time, pair, md)
                        continue

                    # Signal Generation (VWAP Crossover)
                    if not self.active_setup and len(md.prices) > 2:
                        vwap = md.vwap
                        # Bullish cross: Previous price below VWAP, Current price above VWAP
                        if md.prices[-2] < prev_vwap and price > vwap:
                            
                            logger.info(f"[{pair}] VWAP Crossover Detected! Calculating Heavy Spot Size.")
                            target_qty = self.calculate_heavy_spot_size(pair, price, vwap)
                            
                            if target_qty > 0:
                                logger.info(f"[{pair}] Executing Phase 1: Market Buy 25% on crossover.")
                                phase_1_qty = round(target_qty * 0.25, self.get_pair_precision(pair))
                                
                                res = place_order(pair, 'BUY', phase_1_qty)
                                if res and res.get('Success'):
                                    self.active_setup = {
                                        'pair': pair,
                                        'target_qty': target_qty,
                                        'filled_qty': phase_1_qty,
                                        'initial_price': price,
                                        'vwap_at_entry': vwap,
                                        'entry_time': start_time,
                                        'phase': 1,
                                        'limit_order_id': None
                                    }

                # Ensure exact 1-minute tick intervals
                elapsed = time.time() - start_time
                sleep_time = max(0, TICK_INTERVAL - elapsed)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("Bot stopped by user.")
                self.running = False
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(10)

if __name__ == "__main__":
    bot = HeavySpotTrader()
    bot.run()
