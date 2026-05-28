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
    "rsi_oversold":       35,       # RSI ต่ำกว่านี้ = Oversold
    "rsi_overbought":     65,       # RSI สูงกว่านี้ = Overbought
    "rsi_bull_div_max":   45,       # RSI สูงสุดที่ยังนับว่า Bullish Divergence
    "rsi_bear_div_min":   55,       # RSI ต่ำสุดที่ยังนับว่า Bearish Divergence
    "lookback_bars":      15,       # จำนวน bar ย้อนหลังสำหรับ Divergence
    "lookback_skip_bars": 3,        # ตัดกี่ bar ล่าสุดออก (หลีกเลี่ยง bar ปัจจุบัน)
    "atr_tp_multiplier":  2.0,      # ATR × ค่านี้ = Take Profit
    "atr_sl_multiplier":  1.5,      # ATR × ค่านี้ = Stop Loss
    "vol_filter_ratio":   0.5,      # Volume ต้องไม่ต่ำกว่า MA20 × ค่านี้
    "ema_short":          50,       # EMA เส้นสั้น
    "ema_long":           200,      # EMA เส้นยาว
    "rsi_length":         14,       # RSI period
    "atr_length":         14,       # ATR period
    "interval":           "1h",     # Timeframe
    "period":             "90d",    # ดึงข้อมูลย้อนหลัง (เพิ่มเป็น 90d เพื่อให้ EMA200 warmup ครบ)
    "request_delay":      0.5,      # หน่วง (วินาที) ระหว่างแต่ละเหรียญ ป้องกัน rate limit
    "max_retries":        3,        # จำนวนครั้งที่ retry ถ้าดึงข้อมูลล้มเหลว
    "retry_delay":        2,        # หน่วง (วินาที) ก่อน retry
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
# SIGNAL BUILDER
# -------------------------------------------------------------------------
def build_buy_signal(display_name: str, last: pd.Series, coin_trend: str, has_div: bool) -> str:
    atr       = last["ATR"]
    tp_price  = last["close"] + (atr * CONFIG["atr_tp_multiplier"])
    sl_price  = last["close"] - (atr * CONFIG["atr_sl_multiplier"])
    buy_low   = last["close"] * 0.99
    ema_short = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long  = last[f"EMA_{CONFIG['ema_long']}"]

    context = "📉 RSI Oversold"
    if last["close"] > ema_long:
        context += "\n+ ยืนเหนือ EMA200 (ภาพใหญ่ยังเป็นขาขึ้น)"
    else:
        context += "\n- อยู่ใต้ EMA200 (ภาพใหญ่ขาลง — เล่นรอบสั้นเท่านั้น)"
    if has_div:
        context += "\n🔥 พบ Bullish Divergence — โอกาสกลับตัวสูง!"

    return (
        f"\n🟢 <b>[SIGNAL BUY] {display_name}</b>\n"
        f"ราคา: <b>${last['close']:,.4f}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.4f}\n"
        f"EMA50: {ema_short:,.4f} | EMA200: {ema_long:,.4f}\n"
        f"สถานะ: {context}\n"
        f"📍 ช่วงเข้าซื้อ: ${buy_low:,.4f} – ${last['close']:,.4f}\n"
        f"🎯 Take Profit (ATR×{CONFIG['atr_tp_multiplier']}): ${tp_price:,.4f}\n"
        f"❌ Stop Loss (ATR×{CONFIG['atr_sl_multiplier']}): ${sl_price:,.4f}\n"
        f"{'─'*32}"
    )


def build_sell_signal(display_name: str, last: pd.Series, coin_trend: str, has_div: bool) -> str:
    atr          = last["ATR"]
    tp_price     = last["close"] - (atr * CONFIG["atr_tp_multiplier"])
    sl_price     = last["close"] + (atr * CONFIG["atr_sl_multiplier"])
    sell_high    = last["close"] * 1.01
    ema_short    = last[f"EMA_{CONFIG['ema_short']}"]
    ema_long     = last[f"EMA_{CONFIG['ema_long']}"]

    context = "⚠️ RSI Overbought"
    if last["close"] > ema_long:
        context += "\n+ ยืนเหนือ EMA200 (แข็งแกร่ง แต่อาจย่อระยะสั้น)"
    else:
        context += "\n- อยู่ใต้ EMA200 (เด้งขึ้นมาเพื่อลงต่อ — ระวังแรงเทขาย)"
    if has_div:
        context += "\n🚨 พบ Bearish Divergence — สัญญาณกลับตัวลงรุนแรง!"

    return (
        f"\n🔴 <b>[SIGNAL SELL] {display_name}</b>\n"
        f"ราคา: <b>${last['close']:,.4f}</b> ({coin_trend})\n"
        f"RSI: {last['RSI']:.2f} | ATR: {atr:,.4f}\n"
        f"EMA50: {ema_short:,.4f} | EMA200: {ema_long:,.4f}\n"
        f"สถานะ: {context}\n"
        f"📍 โซนแบ่งขาย: ${last['close']:,.4f} – ${sell_high:,.4f}\n"
        f"🎯 รอรับกลับ (ATR×{CONFIG['atr_tp_multiplier']}): ${tp_price:,.4f}\n"
        f"❌ Trailing Stop (ATR×{CONFIG['atr_sl_multiplier']}): ${sl_price:,.4f}\n"
        f"{'─'*32}"
    )


# -------------------------------------------------------------------------
# MAIN SCREENER
# -------------------------------------------------------------------------
def screen_crypto() -> None:
    logger.info("🚀 Starting Crypto Screener [Engine: Yahoo Finance | Interval: %s]", CONFIG["interval"])

    signals       = []
    coin_summaries = []
    bullish_count = 0
    total_coins   = 0

    required_cols = ["RSI", "ATR", "VOL_MA", f"EMA_{CONFIG['ema_short']}", f"EMA_{CONFIG['ema_long']}"]

    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_USD")
        logger.info(f"Scanning {display_name}...")

        # --- หน่วงเพื่อป้องกัน rate limit ---
        time.sleep(CONFIG["request_delay"])

        df = get_historical_data_yf(symbol)
        if df is None or df.empty:
            logger.warning(f"[{display_name}] Skipped — no data.")
            continue

        df = calculate_indicators(df)

        if len(df) < 2:
            continue

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # --- ตรวจ NaN ก่อนใช้ทุก indicator ---
        if not has_valid_indicators(last, required_cols):
            logger.warning(f"[{display_name}] Skipped — indicator NaN (ข้อมูลไม่พอสำหรับ EMA{CONFIG['ema_long']}).")
            continue

        # --- Volume Filter: ข้ามถ้า volume ต่ำผิดปกติ (อาจเป็น fake signal) ---
        low_volume = last["volume"] < last["VOL_MA"] * CONFIG["vol_filter_ratio"]
        if low_volume:
            logger.info(f"[{display_name}] Skipped signal check — volume ต่ำกว่า MA20 × {CONFIG['vol_filter_ratio']}")

        total_coins += 1
        ema_long_val  = last[f"EMA_{CONFIG['ema_long']}"]

        # --- แนวโน้มรายเหรียญ ---
        if last["close"] > ema_long_val:
            coin_trend = "🟢 ขาขึ้น"
            bullish_count += 1
        else:
            coin_trend = "🔴 ขาลง"

        coin_summaries.append(
            f"• <b>{display_name}</b>: ${last['close']:,.4f} "
            f"({coin_trend} | RSI: {last['RSI']:.1f} | ATR: {last['ATR']:,.4f})"
        )

        # --- ตรวจสัญญาณ RSI Crossunder/Crossover (และกรอง volume) ---
        rsi_cross_oversold   = last["RSI"] <= CONFIG["rsi_oversold"]   and prev["RSI"] > CONFIG["rsi_oversold"]
        rsi_cross_overbought = last["RSI"] >= CONFIG["rsi_overbought"] and prev["RSI"] < CONFIG["rsi_overbought"]

        if rsi_cross_oversold and not low_volume:
            is_bull_div = check_bullish_divergence(df)
            signals.append(build_buy_signal(display_name, last, coin_trend, is_bull_div))

        elif rsi_cross_overbought and not low_volume:
            is_bear_div = check_bearish_divergence(df)
            signals.append(build_sell_signal(display_name, last, coin_trend, is_bear_div))

    # -------------------------------------------------------------------------
    # ประกอบ Report
    # -------------------------------------------------------------------------
    if total_coins == 0:
        logger.warning("No coins analyzed. Check your WATCHLIST or network connection.")
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
        f"เหรียญขาขึ้น: {bullish_count} / {total_coins} ตัว "
        f"({bullish_ratio*100:.0f}%)\n"
        f"{'='*33}\n\n"
        f"<b>🧐 สรุปรายเหรียญ:</b>\n"
        + "\n".join(coin_summaries)
        + f"\n\n{'='*33}\n"
    )

    if signals:
        report += "⚡ <b>สัญญาณเทรดชั่วโมงนี้:</b>\n" + "".join(signals)
    else:
        report += "\nℹ️ <i>ไม่มีเหรียญใดเข้าเงื่อนไขสัญญาณซื้อ/ขายในชั่วโมงนี้</i>"

    send_telegram_message(report)
    logger.info("✅ Report sent to Telegram.")


# -------------------------------------------------------------------------
# ENTRY POINT
# -------------------------------------------------------------------------
if __name__ == "__main__":
    screen_crypto()
