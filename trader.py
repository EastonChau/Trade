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
from textblob import TextBlob
import feedparser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============= INSERT YOUR API KEYS HERE =============
API_KEY = "M4jgniiiVKr9hEFK1ebfDJRdLWMkUMU0Xrja2UTfzzkZ2KbKwNaLc7DHMFu36yJD"
SECRET_KEY = "K1Jk6lCsSEpBAktHua73mi4kowWSE9huy1JHS0XbiTT3ilDUTsrayWswniuB6pYF"
# ====================================================

BASE_URL = "https://mock-api.roostoo.com"

MAX_POSITIONS = 2
ATR_PERIOD = 14
STOP_LOSS_ATR = 1.5
TAKE_PROFIT_ATR = 3.0
TICK_INTERVAL = 60
MIN_VOLUME_USD = 1000000
MIN_ATR_PERCENT = 0.005

def get_timestamp():
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
    payload['timestamp'] = get_timestamp()
    signature, total_params = generate_signature(payload)
    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature
    }
    return headers, payload, total_params

def get_server_time():
    url = f"{BASE_URL}/v3/serverTime"
    try:
        res = requests.get(url, timeout=10)
        return res.json()
    except Exception as e:
        logger.error(f"Server time error: {e}")
        return None

def get_exchange_info():
    url = f"{BASE_URL}/v3/exchangeInfo"
    try:
        res = requests.get(url, timeout=10)
        return res.json()
    except Exception as e:
        logger.error(f"Exchange info error: {e}")
        return None

def get_all_tickers():
    url = f"{BASE_URL}/v3/ticker"
    params = {'timestamp': get_timestamp()}
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        if data.get('Success'):
            return data.get('Data', {})
    except Exception as e:
        logger.error(f"Ticker error: {e}")
    return {}

def get_balance():
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload, timeout=10)
        return res.json()
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return None

def place_order(pair, side, quantity, price=None):
    url = f"{BASE_URL}/v3/place_order"
    order_type = "LIMIT" if price else "MARKET"
    payload = {
        'pair': pair,
        'side': side.upper(),
        'type': order_type,
        'quantity': str(quantity)
    }
    if price:
        payload['price'] = str(price)
    
    headers, _, total_params = get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    
    try:
        res = requests.post(url, headers=headers, data=total_params, timeout=10)
        return res.json()
    except Exception as e:
        logger.error(f"Order error: {e}")
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
            true_range = high - low
            true_ranges.append(true_range)
        
        if len(true_ranges) >= self.period:
            atr = sum(true_ranges[-self.period:]) / self.period
            self.atr_values[pair] = atr
            return atr
        
        return None

class SentimentAnalyzer:
    def __init__(self):
        self.sentiment_history = {}
    
    def get_crypto_panic_sentiment(self, symbol):
        try:
            api_key = "YOUR_CRYPTOPANIC_API_KEY"
            url = f"https://cryptopanic.com/api/v1/posts/"
            params = {
                'auth_token': api_key,
                'currencies': symbol,
                'kind': 'news'
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                sentiments = []
                for post in data.get('results', [])[:20]:
                    blob = TextBlob(post.get('title', ''))
                    sentiments.append(blob.sentiment.polarity)
                if sentiments:
                    return np.mean(sentiments)
        except Exception as e:
            logger.debug(f"CryptoPanic error for {symbol}: {e}")
        return 0
    
    def get_reddit_sentiment(self, symbol):
        try:
            url = f"https://api.pushshift.io/reddit/search/submission/"
            params = {
                'subreddit': 'CryptoCurrency',
                'q': symbol,
                'sort': 'desc',
                'size': 25,
                'after': int(time.time()) - 86400
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                sentiments = []
                for post in data.get('data', []):
                    blob = TextBlob(post.get('title', ''))
                    sentiments.append(blob.sentiment.polarity)
                if sentiments:
                    return np.mean(sentiments) * 0.8
        except Exception as e:
            logger.debug(f"Reddit error for {symbol}: {e}")
        return 0
    
    def get_fear_greed_index(self):
        try:
            url = "https://api.alternative.me/fomo/"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                fg_value = int(data['fear_and_greed']['value'])
                return (fg_value / 50) - 1
        except Exception as e:
            logger.debug(f"Fear & Greed error: {e}")
        return 0
    
    def calculate_sentiment(self, symbol):
        crypto_panic = self.get_crypto_panic_sentiment(symbol)
        reddit = self.get_reddit_sentiment(symbol)
        fear_greed = self.get_fear_greed_index()
        
        sentiment = (crypto_panic * 0.4) + (reddit * 0.3) + (fear_greed * 0.3)
        
        if symbol not in self.sentiment_history:
            self.sentiment_history[symbol] = deque(maxlen=20)
        self.sentiment_history[symbol].append(sentiment)
        
        return sentiment
    
    def get_sentiment_trend(self, symbol):
        if symbol not in self.sentiment_history or len(self.sentiment_history[symbol]) < 10:
            return 0
        recent = list(self.sentiment_history[symbol])[-10:]
        trend = np.polyfit(range(len(recent)), recent, 1)[0]
        return trend

class AssetSelector:
    def __init__(self):
        self.available_pairs = []
        self.ranked_pairs = []
    
    def fetch_available_pairs(self):
        exchange_info = get_exchange_info()
        if exchange_info and exchange_info.get('TradePairs'):
            self.available_pairs = list(exchange_info['TradePairs'].keys())
            logger.info(f"Found {len(self.available_pairs)} tradable pairs")
            return self.available_pairs
        return []
    
    def rank_assets_by_volume(self, tickers):
        assets_with_metrics = []
        
        for pair, data in tickers.items():
            coin_trade_value = data.get('CoinTradeValue', 0)
            unit_trade_value = data.get('UnitTradeValue', 0)
            last_price = data.get('LastPrice', 0)
            change_24h = abs(data.get('Change', 0))
            
            if coin_trade_value > MIN_VOLUME_USD and last_price > 0:
                score = coin_trade_value * (1 + change_24h)
                assets_with_metrics.append({
                    'pair': pair,
                    'volume': coin_trade_value,
                    'price': last_price,
                    'volatility_24h': change_24h,
                    'score': score
                })
        
        assets_with_metrics.sort(key=lambda x: x['score'], reverse=True)
        self.ranked_pairs = [a['pair'] for a in assets_with_metrics]
        
        logger.info(f"Top 10 assets by volume: {self.ranked_pairs[:10]}")
        return self.ranked_pairs[:MAX_POSITIONS * 2]

class TradingBot:
    def __init__(self):
        self.volatility = VolatilityAnalyzer(ATR_PERIOD)
        self.sentiment = SentimentAnalyzer()
        self.asset_selector = AssetSelector()
        self.positions = {}
        self.running = False
        self.balance_history = deque(maxlen=100)
        self.active_trading_pairs = []
    
    def get_total_equity(self):
        balance_data = get_balance()
        if not balance_data or not balance_data.get('Success'):
            return 10000
        
        total_usd = 0
        wallet = balance_data.get('Wallet', {})
        
        for asset, data in wallet.items():
            if asset == 'USD':
                total_usd += data.get('Free', 0) + data.get('Lock', 0)
            else:
                ticker_data = get_all_tickers()
                pair = f"{asset}/USD"
                if pair in ticker_data:
                    price = ticker_data[pair].get('LastPrice', 0)
                    if price > 0:
                        total_usd += (data.get('Free', 0) + data.get('Lock', 0)) * price
        
        self.balance_history.append(total_usd)
        return total_usd
    
    def update_trading_pairs(self):
        tickers = get_all_tickers()
        if not tickers:
            return
        
        self.asset_selector.rank_assets_by_volume(tickers)
        
        if not self.active_trading_pairs:
            self.active_trading_pairs = self.asset_selector.ranked_pairs[:MAX_POSITIONS * 2]
            logger.info(f"Active trading pairs: {self.active_trading_pairs}")
    
    def get_market_data(self, pair):
        tickers = get_all_tickers()
        if pair not in tickers:
            return None
        
        data = tickers[pair]
        return {
            'price': float(data.get('LastPrice', 0)),
            'bid': float(data.get('MaxBid', 0)),
            'ask': float(data.get('MinAsk', 0)),
            'change_24h': float(data.get('Change', 0)),
            'volume_24h': float(data.get('CoinTradeValue', 0))
        }
    
    def calculate_position_size(self, equity, price, atr):
        if atr is None or atr == 0:
            return 0
        
        risk_per_trade = equity * 0.02
        stop_distance = STOP_LOSS_ATR * atr
        position_size = risk_per_trade / stop_distance
        
        max_position_value = equity * 0.25
        max_size_by_value = max_position_value / price
        
        return min(position_size, max_size_by_value)
    
    def generate_signal(self, pair, market_data, atr, sentiment_score, sentiment_trend):
        if atr is None or atr == 0:
            return 'HOLD', 0
        
        price = market_data['price']
        change_24h = market_data['change_24h']
        volume = market_data['volume_24h']
        
        technical_score = 0
        if change_24h > 0.02:
            technical_score += 0.3
        elif change_24h < -0.02:
            technical_score -= 0.3
        
        atr_pct = atr / price
        if atr_pct > MIN_ATR_PERCENT:
            technical_score += 0.2
        elif atr_pct < 0.003:
            technical_score -= 0.2
        
        volume_score = min(volume / 10000000, 0.3)
        if change_24h > 0:
            technical_score += volume_score
        else:
            technical_score -= volume_score
        
        combined_score = (sentiment_score * 0.6) + (technical_score * 0.4)
        
        if sentiment_trend > 0 and combined_score > 0:
            combined_score *= 1.2
        elif sentiment_trend < 0 and combined_score < 0:
            combined_score *= 1.2
        
        confidence = abs(combined_score) * 100
        confidence = min(100, confidence)
        
        if combined_score > 0.3 and confidence > 65:
            return 'BUY', confidence
        elif combined_score < -0.3 and confidence > 65:
            return 'SELL', confidence
        
        return 'HOLD', confidence
    
    def execute_trade(self, pair, signal, confidence, market_data, atr):
        price = market_data['price']
        equity = self.get_total_equity()
        
        position_size = self.calculate_position_size(equity, price, atr)
        
        if position_size <= 0:
            logger.warning(f"Position size zero for {pair}")
            return False
        
        side = signal
        order_result = place_order(pair, side, position_size)
        
        if order_result and order_result.get('Success'):
            order_detail = order_result.get('OrderDetail', {})
            if order_detail.get('Status') in ['FILLED', 'PENDING']:
                stop_loss = price - (STOP_LOSS_ATR * atr) if side == 'BUY' else price + (STOP_LOSS_ATR * atr)
                take_profit = price + (TAKE_PROFIT_ATR * atr) if side == 'BUY' else price - (TAKE_PROFIT_ATR * atr)
                
                self.positions[pair] = {
                    'side': side,
                    'entry_price': price,
                    'size': position_size,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'entry_time': datetime.now(),
                    'confidence': confidence,
                    'atr_entry': atr
                }
                
                logger.info(f"ENTER {pair} {side} | Size: {position_size:.6f} | Price: ${price:.2f}")
                logger.info(f"Stop Loss: ${stop_loss:.2f} | Take Profit: ${take_profit:.2f}")
                return True
        
        logger.error(f"Order failed for {pair}: {order_result}")
        return False
    
    def check_exit_conditions(self, pair, position, current_price, atr):
        exit_reason = None
        
        if position['side'] == 'BUY':
            if current_price <= position['stop_loss']:
                exit_reason = 'STOP_LOSS'
            elif current_price >= position['take_profit']:
                exit_reason = 'TAKE_PROFIT'
        else:
            if current_price >= position['stop_loss']:
                exit_reason = 'STOP_LOSS'
            elif current_price <= position['take_profit']:
                exit_reason = 'TAKE_PROFIT'
        
        if exit_reason:
            self.close_position(pair, position, current_price, exit_reason)
            return True
        
        if atr and position.get('atr_entry'):
            atr_change = atr / position['atr_entry']
            if atr_change > 1.5:
                if position['side'] == 'BUY':
                    new_stop = current_price - (STOP_LOSS_ATR * atr)
                    if new_stop > position['stop_loss']:
                        position['stop_loss'] = new_stop
                        logger.info(f"Trailing stop updated for {pair}: ${new_stop:.2f}")
                else:
                    new_stop = current_price + (STOP_LOSS_ATR * atr)
                    if new_stop < position['stop_loss']:
                        position['stop_loss'] = new_stop
                        logger.info(f"Trailing stop updated for {pair}: ${new_stop:.2f}")
        
        return False
    
    def close_position(self, pair, position, exit_price, reason):
        side = 'SELL' if position['side'] == 'BUY' else 'BUY'
        order_result = place_order(pair, side, position['size'])
        
        if order_result and order_result.get('Success'):
            if position['side'] == 'BUY':
                pnl = (exit_price - position['entry_price']) * position['size']
            else:
                pnl = (position['entry_price'] - exit_price) * position['size']
            
            logger.info(f"EXIT {pair} | Reason: {reason} | Price: ${exit_price:.2f} | P&L: ${pnl:.2f}")
            del self.positions[pair]
    
    def check_portfolio_limits(self):
        return len(self.positions) < MAX_POSITIONS
    
    def run(self):
        self.running = True
        
        available_pairs = self.asset_selector.fetch_available_pairs()
        if not available_pairs:
            logger.error("No tradable pairs found")
            return
        
        logger.info("=" * 60)
        logger.info("TRADING BOT STARTED")
        logger.info(f"Total Tradable Assets: {len(available_pairs)}")
        logger.info(f"Max Positions: {MAX_POSITIONS}")
        logger.info(f"Stop Loss: {STOP_LOSS_ATR}x ATR | Take Profit: {TAKE_PROFIT_ATR}x ATR")
        logger.info(f"Min Volume Filter: ${MIN_VOLUME_USD:,.0f}")
        logger.info("=" * 60)
        
        scan_counter = 0
        
        while self.running:
            try:
                if scan_counter % 5 == 0:
                    self.update_trading_pairs()
                
                equity = self.get_total_equity()
                logger.info(f"Total Equity: ${equity:.2f} | Active Positions: {len(self.positions)}")
                
                if not self.active_trading_pairs:
                    time.sleep(TICK_INTERVAL)
                    scan_counter += 1
                    continue
                
                for pair in self.active_trading_pairs:
                    market_data = self.get_market_data(pair)
                    if not market_data or market_data['price'] == 0:
                        continue
                    
                    price = market_data['price']
                    self.volatility.update_price(pair, price)
                    atr = self.volatility.calculate_atr(pair)
                    
                    if pair in self.positions:
                        self.check_exit_conditions(pair, self.positions[pair], price, atr)
                        continue
                    
                    if not self.check_portfolio_limits():
                        break
                    
                    symbol = pair.split('/')[0]
                    sentiment_score = self.sentiment.calculate_sentiment(symbol)
                    sentiment_trend = self.sentiment.get_sentiment_trend(symbol)
                    
                    signal, confidence = self.generate_signal(
                        pair, market_data, atr, sentiment_score, sentiment_trend
                    )
                    
                    if signal != 'HOLD' and confidence >= 65:
                        logger.info(f"{pair} | Signal: {signal} | Confidence: {confidence:.1f}%")
                        logger.info(f"Volume: ${market_data['volume_24h']:,.0f} | 24h Change: {market_data['change_24h']*100:.2f}%")
                        if atr:
                            logger.info(f"ATR: ${atr:.2f} ({atr/price*100:.2f}%)")
                        
                        self.execute_trade(pair, signal, confidence, market_data, atr)
                    
                    time.sleep(0.5)
                
                time.sleep(TICK_INTERVAL)
                scan_counter += 1
                
            except KeyboardInterrupt:
                logger.info("Shutdown signal received")
                self.running = False
                
                for pair, position in list(self.positions.items()):
                    market_data = self.get_market_data(pair)
                    if market_data:
                        self.close_position(pair, position, market_data['price'], 'MANUAL_EXIT')
                break
                
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(TICK_INTERVAL)

if __name__ == "__main__":
    try:
        server_time = get_server_time()
        if server_time:
            logger.info(f"Connected to Roostoo API | Server Time: {server_time}")
        
        bot = TradingBot()
        bot.run()
        
    except Exception as e:
        logger.error(f"Startup error: {e}")