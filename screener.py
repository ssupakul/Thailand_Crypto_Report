import os
import requests
import pandas as pd
import pandas_ta as ta

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
# ใช้ API สากลที่ไม่มีการบล็อกและเสถียรที่สุดในการคำนวณกราฟเทคนิคัล
BINANCE_GLOBAL_URL = "https://api.binance.com/api/v3"
EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/USD"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# รายชื่อเหรียญที่เราจะสแกนทรงกราฟตลาดโลก แล้วแปลงราคาเป็น THB ให้คุณ
WATCHLIST = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "EIGENUSDT"]
, "FLOKIUSDT"]

def get_usd_to_thb():
    """ ดึงอัตราแลกเปลี่ยน USD เป็น THB ปัจจุบันแบบเรียลไทม์ """
    try:
        res = requests.get(EXCHANGE_RATE_URL, timeout=10)
        data = res.json()
        return data["rates"]["THB"]
    except Exception as e:
        print(f"Warning: Cannot fetch real-time USD/THB rate ({e}). Using default 35.5")
        return 35.5

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

def get_historical_data(symbol, interval="4h", limit=150):
    url = f"{BINANCE_GLOBAL_URL}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Error fetching {symbol}: {response.status_code}")
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
        print(f"Exception fetching data for {symbol}: {e}")
        return None

def check_bullish_divergence(df, rsi):
    """ ตรวจสอบสัญญาณ Bullish Divergence (ราคาสร้างจุดต่ำสุดใหม่ แต่ RSI ยกฐานสูงขึ้น) """
    if len(df) < 15:
        return False
    
    # ดูย้อนหลังสั้นๆ เพื่อหาจังหวะขัดแย้งของราคาและ RSI
    current_close = df["close"].iloc[-1]
    older_close = df["close"].iloc[-6:-2].min()
    
    current_rsi = rsi.iloc[-1]
    older_rsi = rsi.iloc[-6:-2].min()
    
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def screen_crypto():
    print("🚀 Starting Binance Thailand Crypto Screener [Engine: Hybrid Global-to-THB]...")
    thb_rate = get_usd_to_thb()
    print(f"Current Exchange Rate: 1 USD = {thb_rate:.2f} THB")
    
    signals = []
    
    for symbol in WATCHLIST:
        coin_name = symbol.replace("USDT", "")
        print(f"Scanning {coin_name}_THB (via Global Feed)...")
        
        df = get_historical_data(symbol)
        if df is None or df.empty:
            continue
            
        # คำนวณเทคนิคัลตามสูตรสากล
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        
        last_close_usd = df["close"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]
        last_ema50_usd = df["EMA_50"].iloc[-1]
        last_ema200_usd = df["EMA_200"].iloc[-1]
        
        # แปลงเป็นค่าเงินบาท THB เพื่อความสะดวกในการตั้งออเดอร์ในไทย
        last_close_thb = last_close_usd * thb_rate
        last_ema50_thb = last_ema50_usd * thb_rate
        last_ema200_thb = last_ema200_usd * thb_rate
        
        is_bull_div = check_bullish_divergence(df, df["RSI"])
        
        # 🟢 เงื่อนไขเข้าซื้อ: RSI Oversold (<= 32)
        if last_rsi <= 32:
            buy_zone = f"{last_close_thb:,.2f} - {(last_close_thb * 0.98):,.2f}"
            take_profit = f"{(last_close_thb * 1.05):,.2f} (หรือแนวต้าน EMA50: {last_ema50_thb:,.2f})"
            stop_loss = f"{(last_close_thb * 0.95):,.2f}"
            
            status_context = "📉 RSI Oversold"
            if last_close_usd > last_ema200_usd:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (โครงสร้างหลักยังเป็นขาขึ้น)"
            else:
                status_context += "\n- อยู่ใต้เส้น EMA200 (โครงสร้างหลักเป็นขาลง เน้นเล่นเด้งสั้น)"
                
            if is_bull_div:
                status_context += "\n🔥 พบสัญญาณ Bullish Divergence จุดกลับตัวแรง!"
                
            msg = (
                f"\n🟢 [SIGNAL BUY] {coin_name}_THB\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (4h): {last_rsi:.2f}\n"
                f"สถานะหลัก: {status_context}\n"
                f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} THB\n"
                f"🎯 เป้าขายทำกำไร: {take_profit} THB\n"
                f"❌ จุดตัดขาดทุน: {stop_loss} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)
            
        # 🔴 เงื่อนไขเตือนขาย: RSI Overbought (>= 70)
        elif last_rsi >= 70:
            msg = (
                f"\n🔴 [SIGNAL SELL] {coin_name}_THB\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (4h): {last_rsi:.2f} (Overbought ⚠️)\n"
                f"คำแนะนำ: ราคาเงินบาทเข้าเขตซื้อมากเกินไปแล้ว พิจารณาแบ่งขายทำกำไร\n"
                f"--------------------------------"
            )
            signals.append(msg)

    if signals:
        alert_header = "📊 [Binance TH Crypto Screener Report]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
        print("Success! Signals sent to LINE.")
    else:
        print("Process complete: No assets matched the criteria at this hour.")

if __name__ == "__main__":
    screen_crypto()
