import os
import time
import logging
from datetime import datetime, timezone

log = logging.getLogger("liquidity-hunter")

# ==========================================
# НАСТРОЙКИ (можно менять через Railway Variables)
# ==========================================
CRT_TF = os.getenv("CRT_TF", "1h").strip().lower()                # свеча-диапазон: "1h" | "4h"
SL_ATR_MULT = float(os.getenv("SL_ATR_MULT", "0.5"))              # запас стопа за проколом = 0.5 * ATR(M5)
ENTRY_RETRACE = float(os.getenv("ENTRY_RETRACE", "0.25"))         # вход: доля отката от уровня слома к экстремуму прокола (0 = ровно уровень слома)
MIN_RR = float(os.getenv("MIN_RR", "1.5"))                        # RR до границы диапазона, ниже — сигнал не шлём
MIN_RANGE_ATR = float(os.getenv("MIN_RANGE_ATR", "1.0"))          # свеча-диапазон >= k * ATR(CRT TF)
MAX_SWEEP_ATR = float(os.getenv("MAX_SWEEP_ATR", "1.0"))          # прокол не глубже k * ATR(CRT TF), иначе это пробой, а не закол
MIN_RISK_ATR = float(os.getenv("MIN_RISK_ATR", "1.5"))            # риск >= k * ATR(M5), иначе стоп слишком мал
MAX_SIGNAL_AGE_BARS = int(os.getenv("MAX_SIGNAL_AGE_BARS", "6"))  # сигнал не старше N закрытых M5-свечей после слома

TF_SECONDS = {"1h": 3600, "4h": 14400}
M5 = 300


# ==========================================
# 1. ИНДИКАТОРЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def calculate_atr(bars, period=14):
    """Average True Range."""
    if len(bars) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr_list.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(tr_list[-period:]) / period


def fmt_price(p):
    p = float(p)
    if abs(p) >= 1000:
        return f"{p:.2f}"
    if abs(p) >= 10:
        return f"{p:.3f}"
    return f"{p:.5f}"


# ==========================================
# 2. DOL / ДИАПАЗОН / BIAS / QT  (для утренних отчётов)
# ==========================================

def calculate_dol_levels(d1_bars, w1_bars=None):
    levels = {}
    if d1_bars and len(d1_bars) >= 2:
        prev_day = d1_bars[-2]
        levels["PDH"] = prev_day["high"]
        levels["PDL"] = prev_day["low"]
    if w1_bars and len(w1_bars) >= 2:
        prev_week = w1_bars[-2]
        levels["PWH"] = prev_week["high"]
        levels["PWL"] = prev_week["low"]
    return levels


def calculate_range_dynamics(bars, window=20):
    if not bars or len(bars) < window:
        return None
    recent = bars[-window:]
    range_high = max(b["high"] for b in recent)
    range_low = min(b["low"] for b in recent)
    return {"range_high": range_high, "range_low": range_low,
            "eq_level": (range_high + range_low) / 2.0}


def get_quarterly_phase(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    if 0 <= hour < 6:
        return "Q1 (Accumulation)"
    elif 6 <= hour < 12:
        return "Q2 (Manipulation)"
    elif 12 <= hour < 18:
        return "Q3 (Distribution)"
    return "Q4 (Reversal/Continuation)"


def calculate_daily_bias(d1_bars, h4_bars):
    if not h4_bars or len(h4_bars) < 20:
        return "NEUTRAL"
    if h4_bars[-1]["close"] < h4_bars[-10]["close"]:
        return "BEARISH"
    return "BULLISH"


# ==========================================
# 3. CRT + TURTLE SOUP
#    Свеча-диапазон (CRT_TF) -> прокол её хая/лоя -> возврат внутрь ->
#    слом структуры на M5 -> вход лимиткой на откате
# ==========================================

def _flip(bars):
    """Зеркалим цену (p -> -p), чтобы LONG считать той же логикой, что SHORT."""
    return [{"timestamp": b["timestamp"], "open": -b["open"], "high": -b["low"],
             "low": -b["high"], "close": -b["close"]} for b in bars]


def _last_swing_low(m5, p, left=2, right=2, max_back=36):
    """Последний фрактальный лой M5 перед экстремумом прокола p. Нет фрактала — минимум 6 свечей перед p."""
    for i in range(p - right, max(left, p - max_back) - 1, -1):
        lo = m5[i]["low"]
        if all(lo < m5[j]["low"] for j in range(i - left, i)) and \
           all(lo <= m5[j]["low"] for j in range(i + 1, i + right + 1)):
            return i, lo
    start = max(0, p - 6)
    if p - start < 2:
        return None
    seg = m5[start:p]
    k = min(range(len(seg)), key=lambda j: seg[j]["low"])
    return start + k, seg[k]["low"]


def _short_setup(rh, rl, m5, cur_start, now, atr5, atr_crt, symbol):
    """
    SHORT-логика в «прямых» координатах.
    rh/rl — хай/лой свечи-диапазона, m5 — M5-свечи (последняя может быть ещё не закрыта).
    Возвращает dict или None.
    """
    first = next((i for i, b in enumerate(m5) if b["timestamp"] >= cur_start), None)
    if first is None or len(m5) - first < 3:
        return None

    win = m5[first:]
    peak = max(b["high"] for b in win)
    if peak <= rh:
        return None
    if peak - rh > MAX_SWEEP_ATR * atr_crt:
        log.info(f"[{symbol}] прокол {peak - rh:.2f} глубже {MAX_SWEEP_ATR} ATR — это пробой, а не закол")
        return None
    p = first + max(i for i, b in enumerate(win) if b["high"] == peak)  # последний экстремум

    sw = _last_swing_low(m5, p)
    if sw is None:
        return None
    _, sw_lvl = sw

    # слом структуры: ЗАКРЫТАЯ M5-свеча закрылась ниже последнего лоя перед проколом
    mss = None
    for i in range(p + 1, len(m5)):
        b = m5[i]
        if b["timestamp"] + M5 > now:   # свеча ещё формируется
            break
        if b["close"] < sw_lvl:
            mss = i
            break
    if mss is None:
        return None

    age_bars = (now - (m5[mss]["timestamp"] + M5)) / M5
    if age_bars > MAX_SIGNAL_AGE_BARS:
        return None

    leg = peak - sw_lvl
    entry = sw_lvl + ENTRY_RETRACE * leg
    sl = peak + SL_ATR_MULT * atr5
    risk = sl - entry
    tp_eq = (rh + rl) / 2.0
    tp_main = rl
    if risk <= 0 or not (tp_main < tp_eq < entry):
        return None
    if risk < MIN_RISK_ATR * atr5:
        log.info(f"[{symbol}] стоп слишком мал ({risk:.2f} < {MIN_RISK_ATR} ATR M5) — пропуск")
        return None
    rr = (entry - tp_main) / risk
    if rr < MIN_RR:
        log.info(f"[{symbol}] RR {rr:.2f} < {MIN_RR} — пропуск")
        return None

    after = m5[mss + 1:]
    if any(b["high"] >= entry for b in after):
        log.info(f"[{symbol}] откат в зону входа уже был — сигнал упущен")
        return None
    if any(b["low"] <= tp_eq for b in after):
        log.info(f"[{symbol}] цена уже дошла до EQ — сигнал упущен")
        return None

    return {
        "entry": entry, "stop_loss": sl, "tp_eq": tp_eq, "target": tp_main,
        "rr": rr, "rr_eq": (entry - tp_eq) / risk, "sweep_extreme": peak,
        "mss_level": sw_lvl, "mss_ts": m5[mss]["timestamp"],
    }


def detect_crt_setup(crt_bars, m5_bars, symbol="UNKNOWN", now=None):
    """
    1) Свеча-диапазон = последняя ЗАКРЫТАЯ свеча CRT_TF, мы находимся внутри следующей.
    2) В текущей свече цена проколола хай ИЛИ лой диапазона (не оба).
    3) На M5 закрытием сломан последний свинг перед проколом (возврат внутрь диапазона).
    4) Лимитный вход на откате, SL за экстремумом прокола, цели: EQ и противоположная граница.
    """
    now = time.time() if now is None else now
    period = TF_SECONDS.get(CRT_TF, 3600)
    if not crt_bars or not m5_bars or len(m5_bars) < 60:
        return None

    closed = [b for b in crt_bars if b["timestamp"] + period <= now]
    if len(closed) < 16:
        return None
    rng = closed[-1]
    cur_start = rng["timestamp"] + period
    if not (cur_start <= now < cur_start + period):
        return None   # рынок закрыт / нет текущей свечи

    atr_crt = calculate_atr(closed, 14)
    m5 = sorted(m5_bars, key=lambda b: b["timestamp"])
    m5_closed = [b for b in m5 if b["timestamp"] + M5 <= now]
    atr5 = calculate_atr(m5_closed, 14)
    if atr_crt <= 0 or atr5 <= 0:
        return None

    rh, rl = rng["high"], rng["low"]
    if (rh - rl) < MIN_RANGE_ATR * atr_crt:
        return None

    cur = [b for b in m5 if b["timestamp"] >= cur_start]
    if not cur:
        return None
    swept_hi = max(b["high"] for b in cur) > rh
    swept_lo = min(b["low"] for b in cur) < rl
    if swept_hi == swept_lo:   # ничего не проколото либо проколоты обе стороны
        return None

    if swept_hi:
        direction = "SHORT"
        res = _short_setup(rh, rl, m5, cur_start, now, atr5, atr_crt, symbol)
    else:
        direction = "LONG"
        r = _short_setup(-rl, -rh, _flip(m5), cur_start, now, atr5, atr_crt, symbol)
        res = None
        if r:
            res = {"entry": -r["entry"], "stop_loss": -r["stop_loss"], "tp_eq": -r["tp_eq"],
                   "target": -r["target"], "rr": r["rr"], "rr_eq": r["rr_eq"],
                   "sweep_extreme": -r["sweep_extreme"], "mss_level": -r["mss_level"],
                   "mss_ts": r["mss_ts"]}
    if not res:
        return None

    zone_a, zone_b = sorted((res["mss_level"], res["entry"]))
    log.info(f"🚨 [{symbol}] CRT {direction}: вход {fmt_price(res['entry'])} SL {fmt_price(res['stop_loss'])} RR {res['rr']:.2f}")

    return {
        "setup_type": "CRT_TURTLE_SOUP",
        "direction": direction,
        "tf": f"CRT {CRT_TF.upper()} / M5",
        "crt_high": rh,
        "crt_low": rl,
        "eq_level": res["tp_eq"],
        "sweep_extreme": res["sweep_extreme"],
        "mss_level": res["mss_level"],
        "zone": (zone_a, zone_b),
        "entry": res["entry"],
        "stop_loss": res["stop_loss"],
        "tp_eq": res["tp_eq"],
        "target_1": res["target"],      # главная цель = противоположная граница диапазона (по ней считается результат)
        "rr_ratio": round(res["rr"], 2),
        "rr_eq": round(res["rr_eq"], 2),
        "atr_m5": atr5,
        "daily_bias": "—",
        "qt_phase": get_quarterly_phase(),
        "bar_time": rng["timestamp"],   # одна свеча-диапазон + одно направление = один сигнал
    }


# ==========================================
# 4. ФОРМАТ СООБЩЕНИЯ
# ==========================================

def format_signal(symbol, event, time_str=""):
    arrow = "📉" if event["direction"] == "SHORT" else "📈"
    side = "SELL" if event["direction"] == "SHORT" else "BUY"
    z1, z2 = event["zone"]
    msg = (
        f"{arrow} **{symbol} {side} LIMIT** (CRT + Turtle Soup)\n\n"
        f"🕯 Свеча-диапазон {CRT_TF.upper()}: `{fmt_price(event['crt_low'])}` – `{fmt_price(event['crt_high'])}`\n"
        f"🪤 Прокол (Ts): `{fmt_price(event['sweep_extreme'])}`\n"
        f"🔄 Слом структуры M5 (M'): `{fmt_price(event['mss_level'])}`\n\n"
        f"📍 Вход лимиткой: `{fmt_price(event['entry'])}` (зона `{fmt_price(z1)}`–`{fmt_price(z2)}`)\n"
        f"🛡 SL: `{fmt_price(event['stop_loss'])}` (за проколом + {SL_ATR_MULT} ATR M5)\n"
        f"✔️ TP1 (EQ): `{fmt_price(event['tp_eq'])}` (1:{event['rr_eq']})\n"
        f"🎯 TP2 (граница диапазона): `{fmt_price(event['target_1'])}` (1:{event['rr_ratio']})\n\n"
        f"⚠️ Ордер отложенный: если цена не вернётся в зону входа, сетап отменяется.\n"
    )
    if time_str:
        msg += f"⏱ Время: {time_str}"
    return msg
