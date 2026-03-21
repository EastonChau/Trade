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
    # Sort keys alphabetically → REQUIRED by Roostoo docs
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        total_params.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def get_signed_headers(payload):
    payload['timestamp'] = get_timestamp_ms()
    signature = generate_signature(payload)
    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature,
    }
    return headers, payload

def check_api_keys():
    if not API_KEY or not SECRET_KEY:
        logger.error("API_KEY and/or SECRET_KEY not set. Edit lines ~21-22.")
        return False
    return True

def get_server_time():
    url = f"{BASE_URL}/v3/serverTime"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        return res.json().get('ServerTime')
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
    payload = {'timestamp': get_timestamp_ms()}
    url = f"{BASE_URL}/v3/ticker"
    try:
        res = requests.get(url, params=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data.get('Data', {})
        else:
            logger.error(f"Ticker error: {data.get('ErrMsg', 'Unknown')}")
            return {}
    except Exception as e:
        logger.error(f"get_all_tickers failed: {e}")
        return {}

def get_balance():
    if not check_api_keys():
        return None
    url = f"{BASE_URL}/v3/balance"
    payload = {}  # timestamp added in get_signed_headers
    headers, payload = get_signed_headers(payload)
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
    # timestamp added below
    headers, payload = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('Success'):
            return data
        else:
            logger.error(f"Place order failed: {data.get('ErrMsg', 'Unknown')}")
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
        return res.json()
    except Exception as e:
        logger.error(f"cancel_order failed: {e}")
        return None

class VolatilityAnalyzer:
    def __init__(self, period=14):
        self.period = period
        self.price_history = {}
        self.atr_values = {}

    def update_price(self, pair, price):
        if pair not in self.price_history:
            self.price_history[pair] = deque(maxlen=self.period + 1)
        self.price_history[pair].append(price)

    def calculate_atr(self, pair):
        if pair not in self.price_history or len(self.price_history[pair]) < self.period + 1:
            return None
        prices = list(self.price_history[pair])
        true_ranges = []
        for i in range(1, len(prices)):
            high = max(prices[i], prices[i-1])
            low = min(prices[i], prices[i-1])
            true_ranges.append(high - low)
        if len(true_ranges) >= self.period:
            atr = sum(true_ranges[-self.period:]) / self.period
            self.atr_values[pair] = atr
            return atr
        return None

class AssetSelector:
    def __init__(self):
        self.available_pairs = []
        self.ranked_pairs = []

    def fetch_available_pairs(self):
        ex_info = get_exchange_info()
        if ex_info and 'TradePairs' in ex_info:
            self.available_pairs = list(ex_info['TradePairs'].keys())
            logger.info(f"Found {len(self.available_pairs)} tradable pairs")
            return self.available_pairs
        logger.error("Failed to get trade pairs")
        return []

    def rank_assets_by_volume(self, tickers):
        assets = []
        for pair, data in tickers.items():
            vol = data.get('CoinTradeValue', 0)
            price = data.get('LastPrice', 0)
            change = abs(data.get('Change', 0))
            if vol > MIN_VOLUME_USD and price > 0:
                score = vol * (1 + change)
                assets.append({
                    'pair': pair,
                    'volume': vol,
                    'score': score
                })
        assets.sort(key=lambda x: x['score'], reverse=True)
        self.ranked_pairs = [a['pair'] for a in assets]
        if self.ranked_pairs:
            logger.info(f"Top ranked pairs: {self.ranked_pairs[:10]}")
        return self.ranked_pairs[:MAX_POSITIONS * 2]

class TradingBot:
    def __init__(self):
        self.volatility = VolatilityAnalyzer(ATR_PERIOD)
        self.asset_selector = AssetSelector()
        self.positions = {}  # pair → dict
        self.running = False
        self.active_trading_pairs = []

    def get_total_equity(self):
        bal = get_balance()
        if not bal or not bal.get('Success'):
            return 0
        total = 0.0
        wallet = bal.get('Wallet', {})
        tickers = get_all_tickers()
        for asset, info in wallet.items():
            free = info.get('Free', 0)
            locked = info.get('Lock', 0)
            if asset == 'USD':
                total += free + locked
            else:
                pair = f"{asset}/USD"
                if pair in tickers:
                    price = tickers[pair].get('LastPrice', 0)
                    total += (free + locked) * price
        return total

    def update_trading_pairs(self):
        tickers = get_all_tickers()
        if tickers:
            self.active_trading_pairs = self.asset_selector.rank_assets_by_volume(tickers)
            logger.info(f"Active pairs ({len(self.active_trading_pairs)}): {self.active_trading_pairs}")

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
            return 0
        risk = equity * 0.02
        stop_dist = STOP_LOSS_ATR * atr
        size = risk / stop_dist
        max_value = equity * 0.25
        max_size = max_value / price
        return min(size, max_size)

    def generate_signal(self, pair, market_data, atr):
        if not market_data or not atr:
            return 'HOLD', 0
        price = market_data['price']
        if price <= 0:
            return 'HOLD', 0
        change = market_data['change_24h']
        score = change * 10  # simple momentum
        atr_pct = atr / price
        if atr_pct > MIN_ATR_PERCENT:
            score += 0.2
        confidence = min(100, abs(score) * 50)
        if score > 0.3 and confidence > 65:
            return 'BUY', confidence
        if score < -0.3 and confidence > 65:
            return 'SELL', confidence
        return 'HOLD', confidence

    def execute_trade(self, pair, signal, confidence, market_data, atr):
        price = market_data['price']
        equity = self.get_total_equity()
        if equity <= 0:
            logger.warning("No equity available")
            return False
        qty = self.calculate_position_size(equity, price, atr)
        if qty <= 0:
            return False
        result = place_order(pair, signal, qty)
        if result and result.get('Success'):
            logger.info(f"EXECUTED {signal} {pair} | Qty: {qty:.6f} | Price ~${price:.2f}")
            sl = price - (STOP_LOSS_ATR * atr) if signal == 'BUY' else price + (STOP_LOSS_ATR * atr)
            tp = price + (TAKE_PROFIT_ATR * atr) if signal == 'BUY' else price - (TAKE_PROFIT_ATR * atr)
            self.positions[pair] = {
                'side': signal,
                'entry_price': price,
                'size': qty,
                'stop_loss': sl,
                'take_profit': tp
            }
            return True
        return False

    def check_exit_conditions(self, pair, pos, current_price, atr):
        side = pos['side']
        sl = pos['stop_loss']
        tp = pos['take_profit']
        if (side == 'BUY' and (current_price <= sl or current_price >= tp)) or \
           (side == 'SELL' and (current_price >= sl or current_price <= tp)):
            close_side = 'SELL' if side == 'BUY' else 'BUY'
            result = place_order(pair, close_side, pos['size'])
            if result and result.get('Success'):
                logger.info(f"EXIT {pair} | Price: ${current_price:.2f}")
                del self.positions[pair]
            return True
        return False

    def run(self):
        self.running = True
        logger.info("Roostoo Trading Bot started (mock mode)")
        if not check_api_keys():
            logger.error("API keys missing → stopping")
            return

        self.asset_selector.fetch_available_pairs()
        if not self.asset_selector.available_pairs:
            logger.error("No pairs available → stopping")
            return

        while self.running:
            try:
                self.update_trading_pairs()
                equity = self.get_total_equity()
                logger.info(f"Equity: ${equity:.2f} | Positions: {len(self.positions)}")

                for pair in self.active_trading_pairs[:]:
                    data = self.get_market_data(pair)
                    if not data or data['price'] <= 0:
                        continue
                    price = data['price']
                    self.volatility.update_price(pair, price)
                    atr = self.volatility.calculate_atr(pair)

                    if pair in self.positions:
                        self.check_exit_conditions(pair, self.positions[pair], price, atr)
                        continue

                    if len(self.positions) >= MAX_POSITIONS:
                        continue

                    signal, conf = self.generate_signal(pair, data, atr)
                    if signal != 'HOLD' and conf >= 65:
                        logger.info(f"Signal {signal} on {pair} ({conf:.1f}%)")
                        self.execute_trade(pair, signal, conf, data, atr)

                time.sleep(TICK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self.running = False
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(10)

if __name__ == "__main__":
    server_time = get_server_time()
    if server_time:
        logger.info(f"API connected | Server time: {server_time}")
    else:
        logger.error("Cannot reach Roostoo API → check network / BASE_URL")
        sys.exit(1)

    bot = TradingBot()
    bot.run()
