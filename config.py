import os

BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

WATCHED_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv(
        "WATCHED_SYMBOLS",
        "BTCUSDT,ETHUSDT,HYPEUSDT,FARTCOINUSDT,ZAMAUSDT,SOLUSDT"
    ).split(",")
    if s.strip()
]

SCAN_INTERVAL_SECONDS = 60
MIN_SIGNAL_SCORE = int(os.getenv("MIN_SIGNAL_SCORE", "70"))
SHORT_MIN_SIGNAL_SCORE = int(os.getenv("SHORT_MIN_SIGNAL_SCORE", "88"))

# Liquidity Tracker defaults from the Pine script
WT_CHANNEL = 9
WT_AVG = 12
WT_SIGNAL = 3
WT_EXTREME = 60.0

MF_SMOOTH_1 = 21
MF_SMOOTH_2 = 9
MF_WEIGHT = 0.50
MF_SCALE = 150.0

ATR_LENGTH = 14
SWING_LEFT = 30
SWING_RIGHT = 3
