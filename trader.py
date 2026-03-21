#!/usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import time
import hmac
import hashlib
import json
import numpy as np
from collections import deque
from datetime import datetime
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
API_KEY = "1KPVEQfk2NJ6AdxP4tPb36MhKNbOFhNLhVjAjKpVq9TDPcusQONWODpe2iVjEOca"         
SECRET_KEY = "ss3x6EvpsIsunKx1takYy99rW1Mifiy6h7edKWePc2JdW1zUE1zn9x70KNMtT4zq"       

BASE_URL = "https://mock-api.roostoo.com"

MAX_POSITIONS = 2
ATR_PERIOD = 14
STOP_LOSS_ATR = 1.5
TAKE_PROFIT_ATR = 3.0
TICK_INTERVAL = 60          # seconds
MIN_VOLUME_USD = 1000000
MIN_ATR_PERCENT = 0.005


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
    return signature

def get_signed_headers(payload=None):
    if payload is None:
        payload = {}
    payload['timestamp'] = get_timestamp_ms()
    signature = generate_signature(payload)
    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature,
    }
    return headers, payload

def check_api_keys():
    if not API_KEY.strip() or not SECRET_KEY.strip():
        logger.error("API_KEY and/or SECRET_KEY are empty. Edit the script.")
        return False
    return True

def get_server_time():
    url = f"{BASE_URL}/v3/serverTime"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        return int(data.get('ServerTime', 0))
    except Exception as e:
        logger.error(f"get_server_time failed: {e}")
        return None

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
    # Ticker does NOT require authentication signature — only timestamp in query
    url = f"{BASE_URL}/v3/ticker"
    params = {'timestamp': get_timestamp_ms()}
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data.get('Data', {})
        else:
            logger.error(f"Ticker API returned error: {data.get('ErrMsg', 'Unknown')}")
            return {}
    except Exception as e:
        logger.error(f"get_all_tickers failed: {e}")
        return {}

def get_balance():
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/balance"
    headers, payload = get_signed_headers()
    try:
        res = requests.get(url, headers=headers, params=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data
        else:
            logger.error(f"Balance error: {data.get('ErrMsg', 'Unknown')}")
            return None
    except Exception as e:
        logger.error(f"get_balance failed: {e}")
        return None

def get_pending_count():
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/pending_count"
    headers, payload = get_signed_headers()
    try:
        res = requests.get(url, headers=headers, params=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data.get('PendingCount', 0)
        else:
            logger.warning(f"Pending count error: {data.get('ErrMsg')}")
            return None
    except Exception as e:
        logger.warning(f"get_pending_count failed: {e}")
        return None

def place_order(pair, side, quantity, price=None):
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/place_order"
    order_type = "LIMIT" if price else "MARKET"
    payload = {
        'pair': pair,
        'side': side.upper(),
        'type': order_type,
        'quantity': str(quantity),
    }
    if price is not None:
        payload['price'] = str(price)

    headers, payload = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            logger.info(f"Order placed successfully: {data.get('OrderDetail', {})}")
            return data
        else:
            logger.error(f"Place order failed: {data.get('ErrMsg', 'Unknown error')}")
            return None
    except Exception as e:
        logger.error(f"place_order failed: {e}")
        return None

def cancel_order(order_id=None, pair=None):
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/cancel_order"
    payload = {}
    if order_id:
        payload['order_id'] = str(order_id)
    if pair:
        payload['pair'] = pair

    headers, payload = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        logger.info(f"Cancel result: {data}")
        return data
    except Exception as e:
        logger.error(f"cancel_order failed: {e}")
        return None

# ────────────────────────────────────────────────
#                 TRADING LOGIC
# ────────────────────────────────────────────────

class VolatilityAnalyzer:
    def __init__(self, period=14):
        self.period = period
        self.price_history = {}

    def update_price(self, pair, price):
        if pair not in self.price_history:
            self.price_history[pair] = deque(maxlen=self.period + 1)
        self.price_history[pair].append(price)

    def calculate_atr(self, pair):
        hist = self.price_history.get(pair)
        if not hist or len(hist) < self.period + 1:
            return None
        prices = list(hist)
        trs = [max(prices[i], prices[i-1]) - min(prices[i], prices[i-1]) for i in range(1, len(prices))]
        if len(trs) < self.period:
            return None
        return sum(trs[-self.period:]) / self.period


class AssetSelector:
    def __init__(self):
        self.available_pairs = []

    def fetch_available_pairs(self):
        ex = get_exchange_info()
        if ex and 'TradePairs' in ex:
            self.available_pairs = list(ex['TradePairs'].keys())
            logger.info(f"Loaded {len(self.available_pairs)} tradable pairs")
            return self.available_pairs
        logger.error("Failed to load trade pairs")
        return []

    def rank_by_volume_score(self, tickers):
        candidates = []
        for pair, d in tickers.items():
            vol = float(d.get('CoinTradeValue', 0))
            price = float(d.get('LastPrice', 0))
            chg = abs(float(d.get('Change', 0)))
            if vol > MIN_VOLUME_USD and price > 0:
                score = vol * (1 + chg)
                candidates.append((pair, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_pairs = [p for p, s in candidates][:MAX_POSITIONS * 3]
        logger.info(f"Top volume+momentum pairs: {top_pairs}")
        return top_pairs


class TradingBot:
    def __init__(self):
        self.volatility = VolatilityAnalyzer(ATR_PERIOD)
        self.selector = AssetSelector()
        self.positions = {}
        self.running = False
        self.active_pairs = []

    def get_total_equity(self):
        bal = get_balance()
        if not bal or not bal.get('Success'):
            return 0.0
        total = 0.0
        wallet = bal.get('Wallet', {})
        tickers = get_all_tickers()
        for asset, amounts in wallet.items():
            qty = float(amounts.get('Free', 0)) + float(amounts.get('Lock', 0))
            if asset == 'USD':
                total += qty
            else:
                pair = f"{asset}/USD"
                if pair in tickers:
                    price = float(tickers[pair].get('LastPrice', 0))
                    total += qty * price
        return total

    def update_active_pairs(self):
        tickers = get_all_tickers()
        if tickers:
            self.active_pairs = self.selector.rank_by_volume_score(tickers)

    def get_market_data(self, pair):
        tickers = get_all_tickers()
        if pair in tickers:
            d = tickers[pair]
            return {
                'price': float(d.get('LastPrice', 0)),
                'change_24h': float(d.get('Change', 0)),
                'volume_24h': float(d.get('CoinTradeValue', 0))
            }
        return None

    def calculate_position_size(self, equity, price, atr):
        if not atr or atr <= 0 or equity <= 0:
            return 0.0
        risk_amount = equity * 0.02
        stop_distance = STOP_LOSS_ATR * atr
        size = risk_amount / stop_distance
        max_value = equity * 0.25
        max_size = max_value / price
        return min(size, max_size)

    def generate_signal(self, pair, md, atr):
        if not md or not atr:
            return 'HOLD', 0
        price = md['price']
        if price <= 0:
            return 'HOLD', 0
        change = md['change_24h']
        score = change * 10
        if (atr / price) > MIN_ATR_PERCENT:
            score += 0.2
        conf = min(100, abs(score) * 50)
        if score > 0.3 and conf >= 65:
            return 'BUY', conf
        if score < -0.3 and conf >= 65:
            return 'SELL', conf
        return 'HOLD', conf

    def execute_trade(self, pair, signal, conf, md, atr):
        price = md['price']
        equity = self.get_total_equity()
        if equity <= 0:
            logger.warning("No equity → cannot trade")
            return False
        qty = self.calculate_position_size(equity, price, atr)
        if qty <= 0:
            return False
        result = place_order(pair, signal, qty)
        if result and result.get('Success'):
            sl = price - (STOP_LOSS_ATR * atr) if signal == 'BUY' else price + (STOP_LOSS_ATR * atr)
            tp = price + (TAKE_PROFIT_ATR * atr) if signal == 'BUY' else price - (TAKE_PROFIT_ATR * atr)
            self.positions[pair] = {
                'side': signal,
                'entry_price': price,
                'size': qty,
                'stop_loss': sl,
                'take_profit': tp,
                'entry_time': datetime.now()
            }
            logger.info(f"OPEN {signal} {pair} | size={qty:.6f} | entry≈${price:.4f}")
            return True
        return False

    def check_exits(self, pair, pos, current_price, atr):
        side = pos['side']
        sl = pos['stop_loss']
        tp = pos['take_profit']
        should_exit = False
        reason = ""
        if side == 'BUY':
            if current_price <= sl:
                should_exit = True
                reason = "STOP LOSS"
            elif current_price >= tp:
                should_exit = True
                reason = "TAKE PROFIT"
        else:
            if current_price >= sl:
                should_exit = True
                reason = "STOP LOSS"
            elif current_price <= tp:
                should_exit = True
                reason = "TAKE PROFIT"

        if should_exit:
            close_side = 'SELL' if side == 'BUY' else 'BUY'
            result = place_order(pair, close_side, pos['size'])
            if result and result.get('Success'):
                logger.info(f"CLOSE {pair} | {reason} | exit≈${current_price:.4f}")
                del self.positions[pair]
            return True
        return False

    def run(self):
        self.running = True
        logger.info("Roostoo Mock Trading Bot started")

        if not check_api_keys():
            logger.critical("API keys missing → exiting")
            return

        self.selector.fetch_available_pairs()
        if not self.selector.available_pairs:
            logger.critical("No tradable pairs → exiting")
            return

        # Quick time sync check
        srv_time = get_server_time()
        if srv_time:
            diff = abs(srv_time - int(time.time() * 1000))
            if diff > 60000:
                logger.warning(f"Local clock drift detected: {diff//1000} seconds")

        while self.running:
            try:
                self.update_active_pairs()
                equity = self.get_total_equity()
                pending = get_pending_count()
                if pending is not None and pending > 0 and len(self.positions) == 0:
                    logger.warning(f"API shows {pending} pending orders but local state empty → possible desync")

                logger.info(f"Equity: ${equity:,.2f} | Local positions: {len(self.positions)}")

                for pair in list(self.active_pairs):
                    md = self.get_market_data(pair)
                    if not md or md['price'] <= 0:
                        continue

                    price = md['price']
                    self.volatility.update_price(pair, price)
                    atr = self.volatility.calculate_atr(pair)

                    if pair in self.positions:
                        self.check_exits(pair, self.positions[pair], price, atr)
                        continue

                    if len(self.positions) >= MAX_POSITIONS:
                        continue

                    signal, conf = self.generate_signal(pair, md, atr)
                    if signal != 'HOLD' and conf >= 65:
                        logger.info(f"Signal → {signal} {pair}  (conf {conf:.1f}%)")
                        self.execute_trade(pair, signal, conf, md, atr)

                time.sleep(TICK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt → shutting down")
                self.running = False
            except Exception as e:
                logger.error(f"Main loop exception: {type(e).__name__} - {e}")
                time.sleep(10)


if __name__ == "__main__":
    srv = get_server_time()
    if srv:
        logger.info(f"API reachable | server time offset ok")
    else:
        logger.error("Cannot reach Roostoo mock API → check network / BASE_URL")
        sys.exit(1)

    bot = TradingBot()
    bot.run()
