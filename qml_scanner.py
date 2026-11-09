import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("QMLScanner")

class QMLScanner:
    def __init__(self, zigzag_len=9, min_struct_atr=1.0, min_break_atr=0.15):
        self.zigzag_len = zigzag_len
        self.min_struct_atr = min_struct_atr
        self.min_break_atr = min_break_atr

    def calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        return ranges.max(axis=1).rolling(period).mean()

    def find_pivots(self, df):
        """Поиск точек разворота (Swing High / Swing Low) аналогично ZigZag"""
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)
        l = self.zigzag_len
        swings = []
        
        for i in range(l, n - l):
            if highs[i] == np.max(highs[i - l : i + l + 1]):
                swings.append({'index': i, 'price': highs[i], 'dir': 1})
            if lows[i] == np.min(lows[i - l : i + l + 1]):
                swings.append({'index': i, 'price': lows[i], 'dir': -1})
                
        return sorted(swings, key=lambda x: x['index'])

    def scan_dataframe(self, df, timeframe, symbol):
        """Сканирование DataFrame с учетом настроек: Loose, Min Score = 0, Both режимы"""
        if len(df) < self.zigzag_len * 6:
            return []

        df = df.copy()
        df['atr'] = self.calculate_atr(df)
        swings = self.find_pivots(df)
        signals = []

        if len(swings) < 6:
            return signals

        # Проходим по последним свингам для поиска паттернов QML
        for i in range(len(swings) - 5):
            s5, s4, s3, s2, s1, s0 = swings[i:i+6]
            atr_val = df['atr'].iloc[s3['index']] if not np.isnan(df['atr'].iloc[s3['index']]) else df['atr'].iloc[-1]

            # --- 1. БЫЧИЙ QML (Bullish Quasimodo) ---
            bull_form = (s5['dir'] == -1 and s4['dir'] == 1 and 
                         s3['dir'] == -1 and s2['dir'] == 1 and 
                         s1['dir'] == -1 and s0['dir'] == 1)
            
            if bull_form:
                l2, h1, l1, h0, l0 = s5['price'], s4['price'], s3['price'], s2['price'], s1['price']
                struct_ok = (h0 > h1) and (l0 < l1) and (l0 > l2)
                struct_height = h1 - l1

                if struct_ok and struct_height >= atr_val * self.min_struct_atr:
                    qml_level = l1  # Уровень зоны (Demand)
                    head_level = l2 # Уровень головы для инвалидации
                    
                    # Проверяем Break of Structure (BOS) выше h1
                    bos_found = False
                    for idx in range(s1['index'], len(df)):
                        if df['close'].iloc[idx] > h1:
                            bos_found = True
                            break

                    # Проверяем условие Invalidation: закрытие свечи ниже head_level (l2)
                    invalidated = False
                    for idx in range(s1['index'], len(df)):
                        if df['close'].iloc[idx] < head_level:
                            invalidated = True
                            break

                    if bos_found and not invalidated:
                        current_price = df['close'].iloc[-1]
                        current_low = df['low'].iloc[-1]
                        
                        # Режим Both (BOS + FTB): фиксируем сигнал либо при пробое, либо при касании зоны QML
                        is_near_zone = current_low <= qml_level + (atr_val * 0.2)
                        
                        signals.append({
                            'symbol': symbol,
                            'timeframe': timeframe,
                            'type': 'BULLISH QML FTB',
                            'score': 50,
                            'level': qml_level,
                            'price': current_price,
                            'is_ftb_touch': is_near_zone
                        })

            # --- 2. МЕДВЕЖИЙ QML (Bearish Quasimodo) ---
            bear_form = (s5['dir'] == 1 and s4['dir'] == -1 and 
                         s3['dir'] == 1 and s2['dir'] == -1 and 
                         s1['dir'] == 1 and s0['dir'] == -1)
            
            if bear_form:
                h2, l1, h1, l0, h0 = s5['price'], s4['price'], s3['price'], s2['price'], s1['price']
                struct_ok = (l0 < l1) and (h0 > h1) and (h0 < h2)
                struct_height = h1 - l1

                if struct_ok and struct_height >= atr_val * self.min_struct_atr:
                    qml_level = h1  # Уровень зоны (Supply)
                    head_level = h2 # Уровень головы для инвалидации

                    # Проверяем Break of Structure (BOS) ниже l1
                    bos_found = False
                    for idx in range(s1['index'], len(df)):
                        if df['close'].iloc[idx] < l1:
                            bos_found = True
                            break

                    # Проверяем Invalidation: закрытие свечи выше head_level (h2)
                    invalidated = False
                    for idx in range(s1['index'], len(df)):
                        if df['close'].iloc[idx] > head_level:
                            invalidated = True
                            break

                    if bos_found and not invalidated:
                        current_price = df['close'].iloc[-1]
                        current_high = df['high'].iloc[-1]
                        
                        is_near_zone = current_high >= qml_level - (atr_val * 0.2)

                        signals.append({
                            'symbol': symbol,
                            'timeframe': timeframe,
                            'type': 'BEARISH QML FTB',
                            'score': 50,
                            'level': qml_level,
                            'price': current_price,
                            'is_ftb_touch': is_near_zone
                        })

        return signals
