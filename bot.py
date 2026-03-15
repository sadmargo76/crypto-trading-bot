import os
import time
import hmac
import hashlib
import requests
import pandas as pd
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

AUTO_TRADE = True
RISK_PER_TRADE = 0.005
MAX_OPEN_POSITIONS = 3
LEVERAGE = 5

BINANCE_FUTURES_BASE_URL = "https://demo-fapi.binance.com"
SYMBOLS = [
"BTCUSDT",
"ETHUSDT",
"SOLUSDT",
"BNBUSDT",
"XRPUSDT",
"LINKUSDT",
"AVAXUSDT",
"DOGEUSDT"
]

CHECK_INTERVAL_SECONDS = 300
SUMMARY_HOUR_UTC = 7

SEND_ONLY_STRONG = False
SEND_NORMAL_AND_STRONG = False
SEND_ONLY_INSTITUTIONAL = True
SEND_STARTUP_MESSAGE = False
SEND_MORNING_SUMMARY = False
SEND_BREAKOUT_ALERTS = False

MIN_RR = 2.0
ATR_MULTIPLIER_MAX = 1.5
VOLUME_MULTIPLIER_MIN = 1.0
FLAT_THRESHOLD_PCT = 0.15
BREAKOUT_LOOKBACK = 20
BREAKOUT_ATR_MULTIPLIER = 1.2
REQUEST_TIMEOUT = 20
FUNDING_POS_EXTREME = 0.03
FUNDING_NEG_EXTREME = -0.03

ORDERFLOW_TAKER_STRONG_LONG = 0.55
ORDERFLOW_TAKER_STRONG_SHORT = 0.45

AI_SCORE_INSTITUTIONAL = 8.0
AI_SCORE_STRONG = 6.5
AI_SCORE_NORMAL = 5.0

last_signal_keys = set()
last_breakout_keys = set()
last_summary_date = None


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN or CHAT_ID is missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print("Telegram error:", e)


def sign_params(params: dict) -> str:
    query = "&".join([f"{k}={params[k]}" for k in params])
    signature = hmac.new(
        BINANCE_SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature



def signed_request(method: str, path: str, params: dict):
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        print("Binance API keys are missing")
        return None

    params = params.copy()
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign_params(params)

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    url = f"{BINANCE_FUTURES_BASE_URL}{path}"

    try:
        if method == "GET":
            r = requests.get(url, params=params, headers=headers, timeout=20)
        else:
            r = requests.post(url, params=params, headrs=headers, timeout=20)

        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Signed request error:", e)
        return None


def get_open_positions():
    data = signed_request("GET", "/fapi/v2/positionRisk", {})
    if not data:
        return []

    positions = []
    for p in data:
        try:
            amt = float(p["positionAmt"])
            if amt != 0:
                positions.append(p)
        except Exception:
            pass
    return positions


def has_open_position(symbol: str) -> bool:
    positions = get_open_positions()
    for p in positions:
        if p.get("symbol") == symbol:
            try:
                if float(p["positionAmt"]) != 0:
                    return True
            except Exception:
                pass
    return False


def set_leverage(symbol: str, leverage: int):
    return signed_request("POST", "/fapi/v1/leverage", {
        "symbol": symbol,
        "leverage": leverage
    })


def get_account_balance():
    data = signed_request("GET", "/fapi/v2/balance", {})
    if not data:
        return None

    for item in data:
        if item.get("asset") == "USDT":
            try:
                return float(item["balance"])
            except Exception:
                return None
    return None


def calculate_quantity(entry: float, stop: float, balance: float) -> float:
    risk_amount = balance * RISK_PER_TRADE
    stop_distance = abs(entry - stop)

    if stop_distance <= 0:
        return 0.0

    qty = (risk_amount / stop_distance) * LEVERAGE
    return round(qty, 3)


def place_market_order(symbol: str, side: str, quantity: float):
    return signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": quantity
    })


def place_exit_orders(symbol: str, side: str, quantity: float, stop_price: float, take_price: float):
    exit_side = "SELL" if side == "BUY" else "BUY"

    stop_order = signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": exit_side,
        "type": "STOP_MARKET",
        "stopPrice": round(stop_price, 4),
        "closePosition": "true",
        "workingType": "MARK_PRICE"
    })

    take_order = signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": exit_side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": round(take_price, 4),
        "closePosition": "true",
        "workingType": "MARK_PRICE"
    })

    return stop_order, take_order


def execute_auto_trade(symbol: str, trend: str, trade: dict, strength: str):
    if not AUTO_TRADE:
        return

    if strength != "INSTITUTIONAL":
        print(symbol, "- autotrade skipped, not institutional")
        return

    positions = get_open_positions()
    if len(positions) >= MAX_OPEN_POSITIONS:
        print(symbol, "- autotrade skipped, max positions reached")
        return

    if has_open_position(symbol):
        print(symbol, "- autotrade skipped, position already exists")
        return

    balance = get_account_balance()
    if balance is None:
        print(symbol, "- autotrade skipped, no balance")
        return

    entry = trade["entry"]
    stop = trade["stop"]
    take = trade["take"]

    qty = calculate_quantity(entry, stop, balance)
    if qty <= 0:
        print(symbol, "- autotrade skipped, invalid quantity")
        return

    set_leverage(symbol, LEVERAGE)

    side = "BUY" if trend == "LONG" else "SELL"
    order = place_market_order(symbol, side, qty)

    if not order:
        print(symbol, "- market order failed")
        return

    stop_order, take_order = place_exit_orders(symbol, side, qty, stop, take)

    send_telegram(
        f"🤖 DEMO AUTO-TRADE OPENED\n\n"
        f"{symbol} {trend}\n"
        f"Сила: {strength}\n"
        f"Объём: {qty}\n"
        f"Вход: {entry:.2f}\n"
        f"Стоп: {stop:.2f}\n"
        f"Тейк: {take:.2f}\n"
        f"Плечо: {LEVERAGE}x\n"
        f"Риск: {RISK_PER_TRADE * 100:.1f}%"
    )

    print(symbol, "- demo auto-trade opened")
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN or CHAT_ID is missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print("Telegram error:", e)


def get_futures_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col])

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def get_open_interest(symbol: str):
    try:
        url = "https://fapi.binance.com/fapi/v1/openInterest"
        params = {"symbol": symbol}

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()

        data = r.json()

        oi = float(data["openInterest"])

        if oi <= 0:
            return None

        return oi

    except Exception as e:
        print(f"Open interest error {symbol}: {e}")
        return None


def get_funding_rate(symbol: str) -> float:
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    params = {"symbol": symbol}
    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return float(data["lastFundingRate"])


def get_long_short_ratio(symbol: str, period: str = "5m", limit: int = 30):
    try:
        url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
        params = {
            "symbol": symbol,
            "period": period,
            "limit": limit
        }
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        if not data:
            return None

        return float(data[0]["longShortRatio"])
    except Exception:
        return None


def funding_bias_label(funding: float, trend: str) -> str:
    if funding >= FUNDING_POS_EXTREME:
        return "толпа перегрета в LONG"
    if funding <= FUNDING_NEG_EXTREME:
        return "толпа перегрета в SHORT"

    if trend == "LONG":
        return "funding нейтральный / умеренный"
    if trend == "SHORT":
        return "funding нейтральный / умеренный"

    return "funding нейтральный"


def orderflow_score(trend: str, taker_ratio: float, oi_pct: float, volume_ratio: float) -> tuple[float, str]:
    score = 0.0
    label = "нейтральный"

    if trend == "LONG":
        if taker_ratio >= ORDERFLOW_TAKER_STRONG_LONG:
            score += 2.0
        elif taker_ratio >= 0.52:
            score += 1.0

        if oi_pct > 1.2:
            score += 2.0
        elif oi_pct > 0.5:
            score += 1.0

        if volume_ratio > 1.4:
            score += 1.5
        elif volume_ratio > 1.1:
            score += 0.5

    elif trend == "SHORT":
        if taker_ratio <= ORDERFLOW_TAKER_STRONG_SHORT:
            score += 2.0
        elif taker_ratio <= 0.48:
            score += 1.0

        if oi_pct < -1.2:
            score += 2.0
        elif oi_pct < -0.5:
            score += 1.0

        if volume_ratio > 1.4:
            score += 1.5
        elif volume_ratio > 1.1:
            score += 0.5

    if score >= 4.5:
        label = "сильный"
    elif score >= 2.5:
        label = "умеренный"

    return score, label


def ai_setup_score(
    trend: str,
    strength: str,
    funding: float,
    long_short_ratio: float | None,
    taker_ratio: float,
    oi_pct: float,
    volume_ratio: float,
    rr: float
) -> tuple[float, str]:
    score = 0.0

    if trend in ["LONG", "SHORT"]:
        score += 1.0

    if strength == "INSTITUTIONAL":
        score += 3.0
    elif strength == "Сильный":
        score += 2.0
    elif strength == "Нормальный":
        score += 1.0

    if rr >= 2.5:
        score += 1.5
    elif rr >= 2.0:
        score += 1.0

    if trend == "LONG":
        if taker_ratio >= 0.55:
            score += 1.0
        if oi_pct > 1.2:
            score += 1.0
        if long_short_ratio is not None and long_short_ratio < 1.8:
            score += 0.5
        if funding >= FUNDING_POS_EXTREME:
            score -= 0.5

    elif trend == "SHORT":
        if taker_ratio <= 0.45:
            score += 1.0
        if oi_pct < -1.2:
            score += 1.0
        if long_short_ratio is not None and long_short_ratio > 0.6:
            score += 0.5
        if funding <= FUNDING_NEG_EXTREME:
            score -= 0.5

    if volume_ratio > 1.4:
        score += 1.0
    elif volume_ratio > 1.1:
        score += 0.5

    if score >= AI_SCORE_INSTITUTIONAL:
        label = "INSTITUTIONAL"
    elif score >= AI_SCORE_STRONG:
        label = "Сильный"
    elif score >= AI_SCORE_NORMAL:
        label = "Нормальный"
    else:
        label = "Слабый"

    return round(score, 1), label


def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()


def atr(df, length=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def add_indicators(df):
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["atr14"] = atr(df, 14)
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["range"] = df["high"] - df["low"]
    return df


def detect_trend(df_1h):
    last = df_1h.iloc[-1]

    bullish = last["close"] > last["ema200"] and last["ema50"] > last["ema200"]
    bearish = last["close"] < last["ema200"] and last["ema50"] < last["ema200"]

    distance_pct = abs(last["ema50"] - last["ema200"]) / last["close"] * 100

    if distance_pct < FLAT_THRESHOLD_PCT:
        return "FLAT"

    if bullish:
        return "LONG"
    if bearish:
        return "SHORT"
    return "NONE"


def check_pullback(df_15m, trend):
    last = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]

    ema_zone_low = min(last["ema20"], last["ema50"])
    ema_zone_high = max(last["ema20"], last["ema50"])

    near_ema_zone = (
        ema_zone_low <= last["close"] <= ema_zone_high
        or ema_zone_low <= last["low"] <= ema_zone_high
        or ema_zone_low <= last["high"] <= ema_zone_high
    )

    if trend == "LONG":
        return near_ema_zone and last["close"] >= last["ema50"] and prev["close"] >= prev["ema50"]

    if trend == "SHORT":
        return near_ema_zone and last["close"] <= last["ema50"] and prev["close"] <= prev["ema50"]

    return False


def check_confirmation(df_5m, trend):
    last = df_5m.iloc[-1]
    prev = df_5m.iloc[-2]

    volume_ok = pd.notna(last["vol_ma20"]) and last["volume"] >= last["vol_ma20"] * VOLUME_MULTIPLIER_MIN

    body = abs(last["close"] - last["open"])
    candle_range = max(last["high"] - last["low"], 1e-9)
    body_ratio = body / candle_range
    candle_quality_ok = body_ratio >= 0.45

    taker_ratio = last["taker_buy_base"] / max(last["volume"], 1e-9)

    if trend == "LONG":
        direction_ok = last["close"] > last["open"] and last["close"] > prev["high"]
        flow_ok = taker_ratio >= 0.52
    elif trend == "SHORT":
        direction_ok = last["close"] < last["open"] and last["close"] < prev["low"]
        flow_ok = taker_ratio <= 0.48
    else:
        direction_ok = False
        flow_ok = False

    confirm_ok = direction_ok and volume_ok and candle_quality_ok and flow_ok
    return confirm_ok, taker_ratio


def check_impulse_filter(df_5m):
    last = df_5m.iloc[-1]
    if pd.isna(last["atr14"]):
        return False
    last_range = last["high"] - last["low"]
    return last_range <= ATR_MULTIPLIER_MAX * last["atr14"]


def build_trade(df_5m, trend):
    last = df_5m.iloc[-1]

    if trend == "LONG":
        entry = float(last["close"])
        stop = float(df_5m["low"].tail(5).min())
        if stop >= entry:
            return None
        risk = entry - stop
        take = entry + risk * MIN_RR

    elif trend == "SHORT":
        entry = float(last["close"])
        stop = float(df_5m["high"].tail(5).max())
        if stop <= entry:
            return None
        risk = stop - entry
        take = entry - risk * MIN_RR

    else:
        return None

    rr = abs(take - entry) / abs(entry - stop)
    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "stop": stop,
        "take": take,
        "rr": rr,
        "time": last["open_time"]
    }


def detect_breakout_setup(df_15m, symbol, trend):
    last = df_15m.iloc[-1]
    recent = df_15m.tail(BREAKOUT_LOOKBACK)

    range_high = recent["high"].max()
    range_low = recent["low"].min()
    range_size = range_high - range_low

    if pd.isna(last["atr14"]) or last["atr14"] == 0:
        return None

    compressed = range_size <= last["atr14"] * BREAKOUT_ATR_MULTIPLIER * 3
    if not compressed:
        return None

    if trend == "LONG" and last["close"] >= range_high * 0.995:
        return {
            "symbol": symbol,
            "trend": "LONG",
            "price": float(last["close"]),
            "range_high": float(range_high),
            "range_low": float(range_low),
            "time": last["open_time"]
        }

    if trend == "SHORT" and last["close"] <= range_low * 1.005:
        return {
            "symbol": symbol,
            "trend": "SHORT",
            "price": float(last["close"]),
            "range_high": float(range_high),
            "range_low": float(range_low),
            "time": last["open_time"]
        }

    return None


def breakout_message(setup):
    return (
        f"⚠️ BREAKOUT HEADS-UP\n\n"
        f"{setup['symbol']} {setup['trend']}\n"
        f"Цена: {setup['price']:.2f}\n"
        f"Диапазон high: {setup['range_high']:.2f}\n"
        f"Диапазон low: {setup['range_low']:.2f}\n"
        f"Время UTC: {setup['time']}\n\n"
        f"Рынок сжат. Возможен импульс."
    )


def signal_strength(df_1h, df_15m, df_5m, trend, funding, long_short_ratio, taker_ratio):
    score = 0
    last1h = df_1h.iloc[-1]
    last15 = df_15m.iloc[-1]
    last5 = df_5m.iloc[-1]

    if trend == "LONG":
        if last1h["close"] > last1h["ema50"]:
            score += 1
        if last15["close"] > last15["ema20"]:
            score += 1
        if last5["volume"] > last5["vol_ma20"]:
            score += 1
        if taker_ratio >= 0.53:
            score += 1
        if funding < 0.03:
            score += 1
        if long_short_ratio is not None and long_short_ratio < 2.0:
            score += 1

    elif trend == "SHORT":
        if last1h["close"] < last1h["ema50"]:
            score += 1
        if last15["close"] < last15["ema20"]:
            score += 1
        if last5["volume"] > last5["vol_ma20"]:
            score += 1
        if taker_ratio <= 0.47:
            score += 1
        if funding > -0.03:
            score += 1
        if long_short_ratio is not None and long_short_ratio > 0.5:
            score += 1

    if score <= 2:
        return "Слабый"
    elif score <= 4:
        return "Нормальный"
    return "Сильный"


def should_send_strength(strength):

    if SEND_ONLY_INSTITUTIONAL:
        return strength == "INSTITUTIONAL"

    if SEND_ONLY_STRONG:
        return strength == "Сильный"

    if SEND_NORMAL_AND_STRONG:
        return strength in ["Нормальный", "Сильный", "INSTITUTIONAL"]

    return True


def format_signal_message(symbol, trend, trade, strength, funding, oi, long_short_ratio, taker_ratio, oi_pct):
    reasons = []

    if oi_pct > 1.2:
        reasons.append("растёт open interest")
    elif oi_pct < -1.2:
        reasons.append("сильный сдвиг open interest")

    if trend == "LONG" and taker_ratio >= 0.53:
        reasons.append("агрессивные покупки")
    elif trend == "SHORT" and taker_ratio <= 0.47:
        reasons.append("агрессивные продажи")

    if long_short_ratio is not None:
        if trend == "LONG" and long_short_ratio < 2.0:
            reasons.append("толпа не перегрета в лонг")
        elif trend == "SHORT" and long_short_ratio > 0.5:
            reasons.append("толпа не перегрета в шорт")
            
    return (
    f"{symbol} {trend}\n\n"
    f"Сила: {strength}\n"
    f"Вероятность: {probability}%\n"
    f"Вход: {trade['entry']:.2f}\n"
    f"Стоп: {trade['stop']:.2f}\n"
    f"Тейк: {trade['take']:.2f}\n"
    f"R:R = {trade['rr']:.2f}\n\n"
    f"Почему сигнал:\n"
    f"{reasons_text}"
)

def market_summary_for_symbol(symbol):
    df_1h = add_indicators(get_futures_klines(symbol, "1h", 300))
    trend = detect_trend(df_1h)
    funding = get_funding_rate(symbol)
    oi = get_open_interest(symbol)
    long_short_ratio = get_long_short_ratio(symbol)

    if trend == "FLAT":
        trend_text = "FLAT"
    elif trend == "LONG":
        trend_text = "BULLISH"
    elif trend == "SHORT":
        trend_text = "BEARISH"
    else:
        trend_text = "NEUTRAL"

    ratio_text = "n/a" if long_short_ratio is None else f"{long_short_ratio:.2f}"

    return (
        f"{symbol}\n"
        f"- Trend: {trend_text}\n"
        f"- Funding: {funding:.5f}\n"
        f"- Open Interest: {oi:.2f}\n"
        f"- Long/Short ratio: {ratio_text}\n"
    )


def send_morning_summary():
    if not SEND_MORNING_SUMMARY:
        return
    global last_summary_date

    now = datetime.now(timezone.utc)
    current_date = now.date()

    if last_summary_date == current_date:
        return
    if now.hour < SUMMARY_HOUR_UTC:
        return

    parts = ["🌅 Утренний обзор рынка\n"]
    for symbol in SYMBOLS:
        try:
            parts.append(market_summary_for_symbol(symbol))
        except Exception as e:
            parts.append(f"{symbol}\n- Ошибка обзора: {e}\n")

    send_telegram("\n".join(parts))
    last_summary_date = current_date
    print("Morning summary sent")


def check_symbol(symbol):
    df_1h = add_indicators(get_futures_klines(symbol, "1h", 300))
    df_15m = add_indicators(get_futures_klines(symbol, "15m", 300))
    df_5m = add_indicators(get_futures_klines(symbol, "5m", 300))

    trend = detect_trend(df_1h)

    if trend == "FLAT":
        print(symbol, "- flat market")
        return

    if trend == "NONE":
        print(symbol, "- no clear trend")
        return

    breakout = detect_breakout_setup(df_15m, symbol, trend)
    if breakout and SEND_BREAKOUT_ALERTS:
        breakout_key = f"{symbol}_{trend}_{breakout['time']}_breakout"
        if breakout_key not in last_breakout_keys:
            send_telegram(breakout_message(breakout))
            last_breakout_keys.add(breakout_key)
            print(symbol, "- breakout sent")

    if not check_pullback(df_15m, trend):
        print(symbol, "- no quality pullback")
        return

    confirm_ok, taker_ratio = check_confirmation(df_5m, trend)
    if not confirm_ok:
        print(symbol, "- no valid confirmation")
        return

    if not check_impulse_filter(df_5m):
        print(symbol, "- impulse too extended")
        return

    trade = build_trade(df_5m, trend)
    if trade is None:
        print(symbol, "- failed RR")
        return

    funding = get_funding_rate(symbol)
    oi = get_open_interest(symbol)
    long_short_ratio = get_long_short_ratio(symbol)

    strength = signal_strength(df_1h, df_15m, df_5m, trend, funding, long_short_ratio, taker_ratio)
    if not should_send_strength(strength):
        print(symbol, f"- strength {strength}, skipped")
        return

    key = f"{symbol}_{trend}_{round(trade['entry'], 2)}_{trade['time']}"
    if key in last_signal_keys:
        print(symbol, "- duplicate skipped")
        return

    message = format_signal_message(
        symbol,
        trend,
        trade,
        strength,
        funding,
        oi,
        long_short_ratio,
        taker_ratio,
        oi_pct
    )

    send_telegram(message)
    execute_auto_trade(symbol, trend, trade, strength)
    last_signal_keys.add(key)
    print(symbol, "- signal sent")


def startup_message():
    return (
        "🚀 Бот запущен 24/7\n"
        "Монеты: BTCUSDT, ETHUSDT, SOLUSDT\n"
        "Режим: intraday futures\n"
        "Фильтры: trend / pullback / ATR / funding / OI / long-short / breakout"
    )


def run_bot():
    if SEND_STARTUP_MESSAGE:
        send_telegram(startup_message())

    while True:
        try:
            send_morning_summary()
            for symbol in SYMBOLS:
                try:
                    check_symbol(symbol)
                except Exception as e:
                    print(symbol, "error:", e)
        except Exception as e:
            print("Main loop error:", e)

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
