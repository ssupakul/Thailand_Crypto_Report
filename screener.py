import os
import requests
import pandas as pd
import pandas_ta as ta

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
# เปลี่ยนมาใช้ Web Data API ของ Binance TH สำหรับดึงกราฟแท่งเทียนโดยตรง ป้องกัน 404 
BINANCE_TH_PUBLIC_KLINE = "https://www.binance.th/bapi/composite/v1/public/market/kline"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# รายชื่อคู่เหรียญที่เปิดซื้อขายจริงด้วยเงินบาทบนกระดาน Binance TH
WATCHLIST = ["BTC_THB", "ETH_THB", "BNB_THB", "SOL_THB", "XRP_THB", "ADA_THB", "DOGE_THB", "FLOKI_THB"]

def send_line_messaging_api(text_msg):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("Error: Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_USER_ID.")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text_msg}]
    }
    
    try:
        response = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("Successfully sent message via LINE Messaging API.")
        else:
            print(f"Failed to send LINE message: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception while sending LINE message: {e}")

def get_historical_data_public(symbol, interval="4h", limit=100):
    """
    ดึงข้อมูลแท่งเทียนจาก Public Web API ของ Binance TH ปลอดภัย ไร้ปัญหาเรื่อง Endpoint บล็อก
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(BINANCE_TH_PUBLIC_KLINE, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Error fetching {symbol}: {response.status_code}")
            return None
            
        res_data = response.json()
        raw_klines = res_data.get("data", [])
        
        if not raw_klines:
            print(f"No data returned for {symbol}")
            return None
            
        # แปลงข้อมูลโครงสร้างกราฟแท่งเทียน
        # โครงสร้าง: [Open time, Open, High, Low, Close, Volume, ...]
        df = pd.DataFrame(raw_klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        
        df["close"] = pd.to_numeric(df["close"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        return df
    except Exception as e:
        print(f"Exception fetching data for {symbol}: {e}")
        return None

def check_bullish_divergence(df, rsi):
    if len(df) < 10:
        return False
    current_close = df["close"].iloc[-1]
    older_close = df["close"].iloc[-5:-2].min()
    current_rsi = rsi.iloc[-1]
    older_rsi = rsi.iloc[-5:-2].min()
    
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def screen_crypto():
    signals = []
    print("🚀 Starting Binance Thailand Crypto Screener (Public Data Gateway)...")
    
    for symbol in WATCHLIST:
        print(f"Scanning {symbol}...")
        df = get_historical_data_public(symbol)
        
        if df is None or df.empty:
            continue
            
        # คำนวณสัญญาณเทคนิคัลอิงตามราคาเงินบาทจริง
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        
        last_close = df["close"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]
        last_ema50 = df["EMA_50"].iloc[-1]
        last_ema200 = df["EMA_200"].iloc[-1]
        
        is_bull_div = check_bullish_divergence(df, df["RSI"])
        
        # 🟢 เงื่อนไขเข้าซื้อ: RSI Oversold (<= 32)
        if last_rsi <= 32:
            buy_zone = f"{last_close:,.2f} - {(last_close * 0.98):,.2f}"
            take_profit = f"{(last_close * 1.05):,.2f} (หรือแนวต้าน EMA50: {last_ema50:,.2f})"
            stop_loss = f"{(last_close * 0.95):,.2f}"
            
            status_context = "📉 RSI Oversold"
            if last_close > last_ema200:
                status_context += "\n+ แท่งเทียนยืนเหนือเส้น EMA200 (โครงสร้างหลักยังเป็นขาขึ้น)"
            else:
                status_context += "\n- แท่งเทียนอยู่ใต้เส้น EMA200 (แนวโน้มหลักเป็นขาลง เน้นเล่นเด้งระยะสั้น)"
                
            if is_bull_div:
                status_context += "\n🔥 พบบูลลิชไดเวอร์เจนท์ (Bullish Divergence) สัญญาณกลับตัวแรง!"
                
            msg = (
                f"\n🟢 [SIGNAL BUY] {symbol}\n"
                f"ราคาปัจจุบัน: {last_close:,.2f} THB\n"
                f"RSI (4h): {last_rsi:.2f}\n"
                f"สถานะกราฟ: {status_context}\n"
                f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} THB\n"
                f"🎯 เป้าขายทำกำไร: {take_profit} THB\n"
                f"❌ จุดตัดขาดทุน: {stop_loss} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)
            
        # 🔴 เงื่อนไขเตือนขาย: RSI Overbought (>= 70)
        elif last_rsi >= 70:
            msg = (
                f"\n🔴 [SIGNAL SELL] {symbol}\n"
                f"ราคาปัจจุบัน: {last_close:,.2f} THB\n"
                f"RSI (4h): {last_rsi:.2f} (Overbought ⚠️)\n"
                f"คำแนะนำ: ราคาโซนเงินบาทเข้าเขตซื้อมากเกินไปแล้ว พิจารณาแบ่งขายทำกำไรบางส่วน\n"
                f"--------------------------------"
            )
            signals.append(msg)

    # ส่งสัญญาณสรุปผลเข้าสู่ LINE
    if signals:
        alert_header = "📊 [Binance TH Crypto Screener Report]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
        print("Done! Technical signal notification sent to LINE.")
    else:
        print("Process complete: No assets matched the RSI 32/70 criteria at this hour.")

if __name__ == "__main__":
    screen_crypto()
