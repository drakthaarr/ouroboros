import pandas as pd

class PairsTradingStrategy:
    def __init__(self):
        pass

    def calculate_z_score(self, df_asset1, df_asset2, window_size):
        spread = df_asset1['close'] / df_asset2['close']
        rolling_mean = spread.rolling(window=window_size).mean()
        rolling_std = spread.rolling(window=window_size).std()
        z_score = (spread - rolling_mean) / rolling_std
        return z_score

    def generate_signal(self, z_score, upper_threshold=2.0, lower_threshold=-2.0):
        if z_score > upper_threshold:
            return 'SHORT_SPREAD'
        elif z_score < lower_threshold:
            return 'LONG_SPREAD'
        else:
            return 'FLAT'
