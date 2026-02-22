import ccxt
import logging

# Configure basic logging to output to console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BinanceClient:
    """
    A wrapper class for Binance exchange operations using ccxt.
    """

    def __init__(self, api_key: str, api_secret: str):
        """
        Initialize the Binance client with API credentials.
        """
        try:
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True, # Crucial: prevents IP bans by respecting API rate limits
                'options': {
                    'defaultType': 'spot' # We are trading on the spot market, not futures
                }
            })
            logger.info("Binance client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Binance client: {e}")
            raise

    def get_usdt_balance(self) -> float:
        """
        Fetches the free (available) USDT balance from the spot account.
        
        Returns:
            float: The available USDT balance. Returns 0.0 if an error occurs.
        """
        try:
            # Fetch all balances
            balance = self.exchange.fetch_balance()
            
            # Extract the 'free' (not locked in orders) USDT amount
            usdt_free = balance.get('USDT', {}).get('free', 0.0)
            
            logger.info(f"Current available USDT balance: {usdt_free}")
            return float(usdt_free)
            
        except ccxt.NetworkError as e:
            logger.warning(f"Network error while fetching balance: {e}")
            return 0.0
        except ccxt.ExchangeError as e:
            logger.warning(f"Exchange error while fetching balance: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"Unexpected error while fetching balance: {e}")
            return 0.0import pandas as pd
import ccxt

class BinanceClient:
    def __init__(self):
        self.exchange = ccxt.binance({
            'apiKey': os.getenv('BINANCE_API_KEY'),
            'apiSecret': os.getenv('BINANCE_API_SECRET'),
        })

    def get_usdt_balance(self):
        # existing code...

    def fetch_ohlcv_data(self, symbol, timeframe, limit):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except ccxt.NetworkError as e:
            print(f"Network error: {e}")
            return None

    def place_market_order(self, symbol, side, amount):
        try:
            order = self.exchange.place_market_order(symbol, side, amount)
            return order
        except ccxt.InsufficientFunds as e:
            print(f"Insufficient funds: {e}")
            return None
        except ccxt.NetworkError as e:
            print(f"Network error: {e}")
            return None
