import os
import time
import logging
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# -------------------------------------------------------------------------
# LOGGING SETUP
# -------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# CONFIGURATION (แก้ค่าได้ที่นี่ที่เดียว)
# -------------------------------------------------------------------------
CONFIG = {
    # --- RSI Thresholds ---
    "rsi_oversold":            35,   # RSI ต่ำกว่านี้ = Oversold zone
    "rsi_overbought":          65,   # RSI สูงกว่านี้ = Overbought zone
    "rsi_recovery_threshold":  45,   # RSI ดีดกลับขึ้นมาถึงแนวนี้ = Recovery signal (buy)
    "rsi_pullback_threshold":  55,   # RSI ย่อตัวลงมาถึงแนวนี้ = Pullback signal (sell)
    "rsi_recovery_lookback":    5,   # ดู bar ย้อนหลังกี่ bar เพื่อหาว่าเคยแตะ oversold ไหม

    # --- Divergence ---
    "rsi_bull_div_max":        45,   # RSI สูงสุดที่ยังนับว่า Bullish Divergence
    "rsi_bear_div_min":        55,   # RSI ต่ำสุดที่ยังนับว่า Bearish Divergence
    "lookback_bars":           15,   # จำนวน bar ย้อนหลังสำหรับ Divergence
    "lookback_skip_bars":       3,   # ตัดกี่ bar ล่าสุดออก

    # --- TP / SL ---
    "atr_tp_multiplier":      2.0,   # ATR × ค่านี้ = Take Profit
    "atr_sl_multiplier":      1.5,   # ATR × ค่านี้ = Stop Loss

    # --- Volume Filter ---
    "vol_filter_ratio":       0.5,   # Volume ต้องไม่ต่ำกว่า VOL_MA × ค่านี้

    # --- Indicators ---
    "ema_short":               50,
    "ema_long":               200,
    "rsi_length":              14,
    "atr_length":              14,

    # --- Data Fetching ---
    "interval":             "1h",
    "period":               "90d",   # 90d เพื่อให้ EMA200 warmup ครบ
    "request_delay":         0.5,    # วินาที ระหว่างเหรียญ
    "max_retries":             3,
    "retry_delay":             2,
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "EIGEN-USD", "FLOKI-USD", "NEAR-USD", "OP-USD", "ADA-USD",
    "SHIB-USD", "DOGE-USD",
]


# -------------------------------------------------------------------------
# TELEGRAM
# -------------------------------------------------------------------------
def send_telegram_message(text_msg: str) -> None:
    """ส่งข้อความไปยัง Telegram ด้วยรูปแบบ HTML"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text_msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    # Telegram จำกัดข้อความ 4096 ตัวอักษร — แบ่งส่งอัตโนมัติ
    MAX_LEN = 4096
    chunks = [text_msg[i:i + MAX_LEN] for i in range(0, len(text_msg), MAX_LEN)]

    for chunk in chunks:
        payload["text"] = chunk
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram message sent successfully.")
            else:
                logger.warning(f"Telegram error {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Exception while sending Telegram message: {e}")


# -------------------------------------------------------------------------
# DATA FETCHING (พร้อม retry)
# -------------------------------------------------------------------------
def get_historical_data_yf(symbol: str) -> pd.DataFrame | None:
    """ดึงข้อมูล OHLCV จาก Yahoo Finance พร้อม retry"""
    interval = CONFIG["interval"]
    period   = CONFIG["period"]

    for attempt in range(1, CONFIG["max_retries"] + 1):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)

            if df is None or df.empty:
                logger.warning(f"[{symbol}] No data returned (attempt {attempt}).")
            else:
                df = df.reset_index().copy()
                df.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low",  "Close": "close",
                    "Volume": "volume"
                }, inplace=True)
                return df

        except Exception as e:
            logger.error(f"[{symbol}] Fetch error (attempt {attempt}): {e}")

        if attempt < CONFIG["max_retries"]:
            time.sleep(CONFIG["retry_delay"])

    return None


# -------------------------------------------------------------------------
# INDICATOR CALCULATION
# -------------------------------------------------------------------------
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """คำนวณ EMA, RSI, ATR, Volume MA ทั้งหมดในที่เดียว"""
    df = df.reset_index(drop=True)  # ทำให้ index เป็น sequential integer เสมอ
    df[f"EMA_{CONFIG['ema_short']}"]  = ta.ema(df["close"], length=CONFIG["ema_short"])
    df[f"EMA_{CONFIG['ema_long']}"]   = ta.ema(df["close"], length=CONFIG["ema_long"])
    df["RSI"]    = ta.rsi(df["close"], length=CONFIG["rsi_length"])
    df["ATR"]    = ta.atr(df["high"], df["low"], df["close"], length=CONFIG["atr_length"])
    df["VOL_MA"] = df["volume"].rolling(20).mean()
    return df


def has_valid_indicators(row: pd.Series, cols: list[str]) -> bool:
    """ตรวจว่า indicator ที่ต้องการไม่มีค่า NaN"""
    return all(not pd.isna(row[col]) for col in cols)


# -------------------------------------------------------------------------
# DIVERGENCE DETECTION (แก้ index bug + ใช้ reset_index)
# -------------------------------------------------------------------------
def _find_swing_low(lookback: pd.DataFrame) -> pd.Series | None:
    """หา swing low จริงๆ (bar ที่ต่ำกว่า bar รอบข้าง)"""
    closes = lookback["close"].values
    for i in range(1, len(closes) - 1):
        if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
            return lookback.iloc[i]
    # ถ้าหา swing low ไม่ได้ ใช้จุดต่ำสุดแทน
    return lookback.iloc[lookback["close"].idxmin()]


def _find_swing_high(lookback: pd.DataFrame) -> pd.Series | None:
    """หา swing high จริงๆ (bar ที่สูงกว่า bar รอบข้าง)"""
    closes = lookback["close"].values
    for i in range(1, len(closes) - 1):
        if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
            return lookback.iloc[i]
    return lookback.iloc[lookback["close"].idxmax()]


def check_bullish_divergence(df: pd.DataFrame) -> bool:
    """
    Bullish Divergence: ราคาทำ lower low แต่ RSI ทำ higher low
    (ราคาต่ำกว่าเดิม แต่ RSI สูงกว่าเดิม → momentum กำลังดีขึ้น)
    """
    min_bars = CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"] + 2
    if len(df) < min_bars:
        return False

    current = df.iloc[-1]
    if current["RSI"] >= CONFIG["rsi_bull_div_max"]:
        return False

    lookback = df.iloc[-(CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"]):-CONFIG["lookback_skip_bars"]].reset_index(drop=True)
    swing = _find_swing_low(lookback)
    if swing is None:
        return False

    # ราคาปัจจุบัน < swing low เดิม + RSI ปัจจุบัน > RSI ณ swing low → divergence
    return (current["close"] < swing["close"]) and (current["RSI"] > swing["RSI"])


def check_bearish_divergence(df: pd.DataFrame) -> bool:
    """
    Bearish Divergence: ราคาทำ higher high แต่ RSI ทำ lower high
    (ราคาสูงกว่าเดิม แต่ RSI ต่ำกว่าเดิม → momentum กำลังอ่อนแรง)
    """
    min_bars = CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"] + 2
    if len(df) < min_bars:
        return False

    current = df.iloc[-1]
    if current["RSI"] <= CONFIG["rsi_bear_div_min"]:
        return False

    lookback = df.iloc[-(CONFIG["lookback_bars"] + CONFIG["lookback_skip_bars"]):-CONFIG["lookback_skip_bars"]].reset_index(drop=True)
    swing = _find_swing_high(lookback)
    if swing is None:
        return False

    return (current["close"] > swing["close"]) and (current["RSI"] < swing["RSI"])


# -------------------------------------------------------------------------
# RSI SIGNAL MODE DETECTION  (3 โหมด)
# -------------------------------------------------------------------------
def detect_buy_mode(rsi_series: pd.Series) -> str | None:
    """
    คืนค่า mode ของสัญญาณซื้อ หรือ None ถ้าไม่มีสัญญาณ

    โหมด 1 — "crossunder" : RSI เพิ่งข้ามลงใต้เส้น Oversold (bar นี้พอดี)
    โหมด 2 — "in_zone"    : RSI อยู่ใน Oversold zone ต่อเนื่อง (ยังไม่ข้ามขึ้น)
    โหมด 3 — "recovery"   : RSI ดีดกลับขึ้นจาก Oversold (จุดที่ดีที่สุดในการเข้า)
    """
    if len(rsi_series) < CONFIG["rsi_recovery_lookback"] + 1:
        return None

    last_rsi = rsi_series.iloc[-1]
    prev_rsi = rsi_series.iloc[-2]
    ov       = CONFIG["rsi_oversold"]
    rec      = CONFIG["rsi_recovery_threshold"]
    lookback = CONFIG["rsi_recovery_lookback"]

    # โหมด 3: Recovery — RSI กำลังดีดขึ้น, เคยแตะ oversold ใน lookback bars
    recent_min = rsi_series.iloc[-lookback:].min()
    if last_rsi <= rec and last_rsi > prev_rsi and recent_min <= ov:
        return "recovery"

    # โหมด 1: Crossunder — RSI เพิ่งข้ามลงใต้ oversold
    if last_rsi <= ov and prev_rsi > ov:
        return "crossunder"

    # โหมด 2: In Zone — RSI อยู่ใน oversold ต่อเนื่องแต่ยังไม่ดีดกลับ
    if last_rsi <= ov and prev_rsi <= ov:
        return "in_zone"

    return None


def detect_sell_mode(rsi_series: pd.Series) -> str | None:
    """
    คืนค่า mode ของสัญญาณขาย หรือ None ถ้าไม่มีสัญญาณ

    โหมด 1 — "crossover"  : RSI เพิ่งข้ามขึ้นเหนือเส้น Overbought
    โหมด 2 — "in_zone"    : RSI อยู่ใน Overbought zone ต่อเนื่อง
    โหมด 3 — "pullback"   : RSI ย่อตัวลงจาก Overbought (จุดที่ดีที่สุดในการขาย)
    """
    if len(rsi_series) < CONFIG["rsi_recovery_lookback"] + 1:
        return None

    last_rsi = rsi_series.iloc[-1]
    prev_rsi = rsi_series.iloc[-2]
    ob       = CONFIG["rsi_overbought"]
    pb       = CONFIG["rsi_pullback_threshold"]
    lookback = CONFIG["rsi_recovery_lookback"]

    # โหมด 3: Pullback — RSI กำลังย่อลง, เคยแตะ overbought ใน lookback bars
    recent_max = rsi_series.iloc[-lookback:].max()
    if last_rsi >= pb and last_rsi < prev_rsi and recent_max >= ob:
        return "pullback"

    # โหมด 1: Crossover — RSI เพิ่งข้ามขึ้นเหนือ overbought
    if last_rsi >= ob and prev_rsi < ob:
        return "crossover"

    # โหมด 2: In Zone — RSI อยู่ใน overbought ต่อเนื่อง
    if last_rsi >= ob and prev_rsi >= ob:
        return "in_zone"

    return None


# -------------------------------------------------------------------------
# SIGNAL BUILDER
# -------------------------------------------------------------------------
_MODE_LABEL_BUY = {
    "crossunder": "🔔 RSI เพิ่งลงใต้ Oversold",
    "in_zone":    "📉 RSI อยู่ใน Oversold ต่อเนื่อง",
    "recovery":   "🚀 RSI กำลังดีดกลับจาก Oversold ← จังหวะเข้าซื้อที่ดีที่สุด",
}
_MODE_LABEL_SELL = {
    "crossover": "🔔 RSI เพิ่งขึ้นเหนือ Overbought",
    "in_zone":   "⚠️ RSI อยู่ใน Overbought ต่อเนื่อง",
    "pullback":  "🎯 RSI กำลังย่อจาก Overbought ← จังหวะขายที่ดีที่สุด",
}


def build_buy_signal(
    display_name: str,
    last: pd.Series,
    coin_trend: str,
    mode: str,
    has_div: bool,
) -> str:
    atr       = last["ATR"]
    tp_price  = last["close"] + (atr * CONFIG["atr_tp_multiplier"])
    sl_price  = last["close"] - (atr * CONFIG["atr_sl_multiplier"])
    ema_short = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long  = last[f"EMA_{CONFIG['ema_long']}"]

    context_lines = [_MODE_LABEL_BUY[mode]]
    if last["close"] > ema_long:
        context_lines.append("+ ยืนเหนือ EMA200 (ภาพใหญ่ยังเป็นขาขึ้น)")
    else:
        context_lines.append("- อยู่ใต้ EMA200 (ภาพใหญ่ขาลง — เล่นรอบสั้นเท่านั้น)")
    if has_div:
        context_lines.append("🔥 พบ Bullish Divergence — โอกาสกลับตัวสูง!")
    context = "\n".join(context_lines)

    return (
        f"\n🟢 <b>[BUY] {display_name}</b>\n"
        f"ราคา: <b>${last['close']:,.4f}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.4f}\n"
        f"EMA50: {ema_short:,.4f} | EMA200: {ema_long:,.4f}\n"
        f"สถานะ: {context}\n"
        f"📍 ช่วงเข้าซื้อ: ${last['close'] * 0.99:,.4f} – ${last['close']:,.4f}\n"
        f"🎯 Take Profit (ATR×{CONFIG['atr_tp_multiplier']}): ${tp_price:,.4f}\n"
        f"❌ Stop Loss (ATR×{CONFIG['atr_sl_multiplier']}): ${sl_price:,.4f}\n"
        f"{'─'*32}"
    )


def build_sell_signal(
    display_name: str,
    last: pd.Series,
    coin_trend: str,
    mode: str,
    has_div: bool,
) -> str:
    atr       = last["ATR"]
    tp_price  = last["close"] - (atr * CONFIG["atr_tp_multiplier"])
    sl_price  = last["close"] + (atr * CONFIG["atr_sl_multiplier"])
    ema_short = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long  = last[f"EMA_{CONFIG['ema_long']}"]

    context_lines = [_MODE_LABEL_SELL[mode]]
    if last["close"] > ema_long:
        context_lines.append("+ ยืนเหนือ EMA200 (แข็งแกร่ง แต่อาจย่อระยะสั้น)")
    else:
        context_lines.append("- อยู่ใต้ EMA200 (เด้งขึ้นมาเพื่อลงต่อ — ระวังแรงเทขาย)")
    if has_div:
        context_lines.append("🚨 พบ Bearish Divergence — สัญญาณกลับตัวลงรุนแรง!")
    context = "\n".join(context_lines)

    return (
        f"\n🔴 <b>[SELL] {display_name}</b>\n"
        f"ราคา: <b>${last['close']:,.4f}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.4f}\n"
        f"EMA50: {ema_short:,.4f} | EMA200: {ema_long:,.4f}\n"
        f"สถานะ: {context}\n"
        f"📍 โซนแบ่งขาย: ${last['close']:,.4f} – ${last['close'] * 1.01:,.4f}\n"
        f"🎯 รอรับกลับ (ATR×{CONFIG['atr_tp_multiplier']}): ${tp_price:,.4f}\n"
        f"❌ Trailing Stop (ATR×{CONFIG['atr_sl_multiplier']}): ${sl_price:,.4f}\n"
        f"{'─'*32}"
    )


# -------------------------------------------------------------------------
# MAIN SCREENER
# -------------------------------------------------------------------------
def screen_crypto() -> None:
    logger.info(
        "🚀 Starting Crypto Screener [Engine: Yahoo Finance | Interval: %s]",
        CONFIG["interval"],
    )

    buy_signals:  list[str] = []
    sell_signals: list[str] = []
    coin_summaries: list[str] = []
    bullish_count = 0
    total_coins   = 0

    required_cols = [
        "RSI", "ATR", "VOL_MA",
        f"EMA_{CONFIG['ema_short']}",
        f"EMA_{CONFIG['ema_long']}",
    ]

    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_USD")
        logger.info(f"Scanning {display_name}...")
        time.sleep(CONFIG["request_delay"])

        df = get_historical_data_yf(symbol)
        if df is None or df.empty:
            logger.warning(f"[{display_name}] Skipped — no data.")
            continue

        df = calculate_indicators(df)

        if len(df) < CONFIG["rsi_recovery_lookback"] + 2:
            continue

        last = df.iloc[-1]

        # ตรวจ NaN ก่อนใช้ indicator ทุกตัว
        if not has_valid_indicators(last, required_cols):
            logger.warning(
                f"[{display_name}] Skipped — NaN detected "
                f"(ข้อมูลไม่พอสำหรับ EMA{CONFIG['ema_long']})."
            )
            continue

        # Volume filter
        low_volume = last["volume"] < last["VOL_MA"] * CONFIG["vol_filter_ratio"]
        if low_volume:
            logger.info(
                f"[{display_name}] Low volume — signal suppressed "
                f"(volume < VOL_MA × {CONFIG['vol_filter_ratio']})."
            )

        total_coins   += 1
        ema_long_val   = last[f"EMA_{CONFIG['ema_long']}"]

        # แนวโน้มรายเหรียญ
        if last["close"] > ema_long_val:
            coin_trend = "🟢 ขาขึ้น"
            bullish_count += 1
        else:
            coin_trend = "🔴 ขาลง"

        coin_summaries.append(
            f"• <b>{display_name}</b>: ${last['close']:,.4f} "
            f"({coin_trend} | RSI: {last['RSI']:.1f} | ATR: {last['ATR']:,.4f})"
        )

        if low_volume:
            continue  # ข้ามการสร้าง signal แต่ยังแสดงในสรุปรายเหรียญ

        rsi_series = df["RSI"]

        # ตรวจสัญญาณซื้อ (3 โหมด)
        buy_mode = detect_buy_mode(rsi_series)
        if buy_mode:
            is_div = check_bullish_divergence(df)
            buy_signals.append(
                build_buy_signal(display_name, last, coin_trend, buy_mode, is_div)
            )
            logger.info(f"[{display_name}] BUY signal [{buy_mode}] | RSI={last['RSI']:.1f}")
            continue  # ถ้ามีสัญญาณซื้อแล้ว ไม่ต้องเช็คขาย

        # ตรวจสัญญาณขาย (3 โหมด)
        sell_mode = detect_sell_mode(rsi_series)
        if sell_mode:
            is_div = check_bearish_divergence(df)
            sell_signals.append(
                build_sell_signal(display_name, last, coin_trend, sell_mode, is_div)
            )
            logger.info(f"[{display_name}] SELL signal [{sell_mode}] | RSI={last['RSI']:.1f}")

    # -------------------------------------------------------------------------
    # ประกอบ Report
    # -------------------------------------------------------------------------
    if total_coins == 0:
        logger.warning("No coins analyzed. Check WATCHLIST or network connection.")
        return

    bullish_ratio = bullish_count / total_coins
    if bullish_ratio >= 0.6:
        market_overview = "📈 ขาขึ้นชัดเจน (Bullish)"
    elif bullish_ratio <= 0.4:
        market_overview = "📉 ขาลงรุนแรง (Bearish)"
    else:
        market_overview = "↔️ ไซด์เวย์เลือกทาง (Sideways)"

    report = (
        f"📊 <b>[Crypto Screener] ภาพรวมตลาด: {market_overview}</b>\n"
        f"เหรียญขาขึ้น: {bullish_count}/{total_coins} "
        f"({bullish_ratio * 100:.0f}%)\n"
        f"{'='*33}\n\n"
        f"<b>🧐 สรุปรายเหรียญ:</b>\n"
        + "\n".join(coin_summaries)
        + f"\n\n{'='*33}\n"
    )

    total_signals = len(buy_signals) + len(sell_signals)
    if total_signals > 0:
        report += f"⚡ <b>สัญญาณเทรดชั่วโมงนี้ ({total_signals} สัญญาณ):</b>\n"
        if buy_signals:
            report += "".join(buy_signals)
        if sell_signals:
            report += "".join(sell_signals)
    else:
        report += "\nℹ️ <i>ไม่มีเหรียญใดเข้าเงื่อนไขสัญญาณซื้อ/ขายในชั่วโมงนี้</i>"

    send_telegram_message(report)
    logger.info(
        "✅ Report sent | BUY: %d | SELL: %d",
        len(buy_signals), len(sell_signals),
    )


# -------------------------------------------------------------------------
# ENTRY POINT
# -------------------------------------------------------------------------
if __name__ == "__main__":
    screen_crypto()
            
