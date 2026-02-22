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
    main()