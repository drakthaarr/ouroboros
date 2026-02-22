import config
from exchange import BinanceClient

def main():
    """
    Main entry point of the trading bot.
    """
    print("Starting the trading bot initialization...")

    # Initialize the exchange client using credentials from config
    client = BinanceClient(
        api_key=config.BINANCE_API_KEY,
        api_secret=config.BINANCE_API_SECRET
    )

    # Test API connection by fetching USDT balance
    balance = client.get_usdt_balance()
    
    if balance > 0:
        print(f"Connection successful! You have ${balance} ready for trading.")
    else:
        print("Balance is 0 or connection failed. Check your API keys and network.")

if __name__ == "__main__":
    main()import logging
import time
import os
from exchange import BinanceClient
from strategy import PairsTradingStrategy

logging.basicConfig(filename='logs/trading.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def main():
    client = BinanceClient()
    strategy = PairsTradingStrategy()

    while True:
        try:
            df_btc = client.fetch_ohlcv_data('BTC/USDT', '1m', 100)
            df_eth = client.fetch_ohlcv_data('ETH/USDT', '1m', 100)

            z_score = strategy.calculate_z_score(df_btc, df_eth, 20)
            signal = strategy.generate_signal(z_score)

            logger.info(f"Z-score: {z_score.iloc[-1]}")
            logger.info(f"Signal: {signal}")

            if signal == 'SHORT_SPREAD' and not os.getenv('MOCK_TRADING', 'True') == 'True':
                # place short order
                client.place_market_order('BTC/USDT', 'sell', 0.01)
            elif signal == 'LONG_SPREAD' and not os.getenv('MOCK_TRADING', 'True') == 'True':
                # place long order
                client.place_market_order('BTC/USDT', 'buy', 0.01)

            time.sleep(60)
        except Exception as e:
            logger.error(f"Error: {e}")

if __name__ == '__main__':
    main()
