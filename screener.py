import os
import requests
import pandas as pd
import pandas_ta as ta

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
BINANCE_TH_URL = "https://api.binance.th/api/v3"

# LINE Messaging API Endpoint สำหรับส่งข้อความเดี่ยว (Push Message)
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# ดึงค่า Secrets จาก GitHub Environment
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# รายชื่อเหรียญยอดนิยมจับคู่กับ USDT บน Binance Thailand
WATCHLIST = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]

def send_line_messaging_api(text_msg):
    """
    ฟังก์ชันส่งข้อความด้วย LINE Messaging API แบบ Push Message (v2)
    """
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("Error: Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_USER_ID.")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    # โครงสร้าง JSON ตามมาตรฐานของ LINE Messaging API
    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": text_msg
            }
        ]
    }
    
    try:
        response = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("Successfully sent message via LINE Messaging API.")
        else:
            print(f"Failed to send LINE message: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception while sending LINE message: {e}")

def get_historical_data(symbol, interval="4h", limit=100):
    url = f"{BINANCE_TH_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Error fetching {symbol} from Binance TH: {response.status_code}")
            return None
        data = response.json()
        
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["close"] = pd.to_numeric(df["close"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def check_bullish_divergence(df, rsi):
    """
    เช็ค Bullish Divergence ข้อมูลย้อนหลัง 5 แท่งล่าสุดเทียบกับอดีต
    """
    if len(df) < 10:
        return False
        
    current_close = df["close"].iloc[-1]
    older_close = df["close"].iloc[-5:-2].min()
    
    current_rsi = rsi.iloc[-1]
    older_rsi = rsi.iloc[-5:-2].min()
    
    # ราคาทำ Lower Low แต่ RSI ยกฐานทำ Higher Low
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def screen_crypto():
    signals = []
    
    for symbol in WATCHLIST:
        print(f"Scanning {symbol}...")
        df = get_historical_data(symbol)
        if df is None or df.empty:
            continue
            
        # คำนวณ EMA 50, EMA 200 และ RSI 14
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        
        last_close = df["close"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]
        last_ema50 = df["EMA_50"].iloc[-1]
        last_ema200 = df["EMA_200"].iloc[-1]
        
        is_bull_div = check_bullish_divergence(df, df["RSI"])
        
        # -----------------------------------------------------------------
        # 🟢 เงื่อนไขเข้าซื้อ (RSI <= 32)
        # -----------------------------------------------------------------
        if last_rsi <= 32:
            buy_zone = f"{last_close:.4f} - {(last_close * 0.98):.4f}"
            take_profit = f"{(last_close * 1.05):.4f} (หรือแนวต้าน EMA50: {last_ema50:.4f})"
            stop_loss = f"{(last_close * 0.95):.4f}"
            
            status_context = "📉 RSI Oversold"
            if last_close > last_ema200:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (เทรนด์ใหญ่ยังเป็นขาขึ้น)"
            if is_bull_div:
                status_context += "\n🔥 พบสัญญาณ Bullish Divergence!"
                
            msg = (
                f"\n🟢 [SIGNAL BUY] {symbol}\n"
                f"ราคาปัจจุบัน: {last_close:,.4f} USDT\n"
                f"RSI (4h): {last_rsi:.2f}\n"
                f"สถานะหลัก: {status_context}\n"
                f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} USDT\n"
                f"🎯 เป้าขายทำกำไร: {take_profit} USDT\n"
                f"❌ จุดตัดขาดทุน: {stop_loss} USDT\n"
                f"--------------------------------"
            )
            signals.append(msg)
            
        # -----------------------------------------------------------------
        # 🔴 เงื่อนไขเตือนขาย (RSI >= 70)
        # -----------------------------------------------------------------
        elif last_rsi >= 70:
            msg = (
                f"\n🔴 [SIGNAL SELL] {symbol}\n"
                f"ราคาปัจจุบัน: {last_close:,.4f} USDT\n"
                f"RSI (4h): {last_rsi:.2f} (Overbought ⚠️)\n"
                f"คำแนะนำ: กราฟตึงมากแล้ว พิจารณาแบ่งขายทำกำไร หรือระวังแรงเทขาย\n"
                f"--------------------------------"
            )
            signals.append(msg)

    # รวบรวมข้อมูลแล้วยิงเข้า LINE Messaging API ครั้งเดียว
    if signals:
        alert_header = "📊 [Binance TH Crypto Screener Report]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
    else:
        print("No assets matched the criteria this time.")

if __name__ == "__main__":
    screen_crypto()
