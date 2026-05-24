import os
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# -------------------------------------------------------------------------
# SETUP & CONFIGURATION
# -------------------------------------------------------------------------
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

WATCHLIST = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "EIGEN-USD", "DOGE-USD"]

def get_realtime_thb_rate():
    """ ดึงอัตราแลกเปลี่ยน USD/THB ปัจจุบันจาก Yahoo Finance """
    try:
        ticker = yf.Ticker("THB=X")
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            return df["Close"].iloc[-1]
        return 35.5
    except Exception as e:
        print(f"Warning: Cannot fetch THB rate ({e}). Using default 35.5")
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

def get_historical_data_yf(symbol, interval="1h"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="60d", interval=interval)
        if df.empty:
            return None
        # ใช้ .copy() ป้องกัน SettingWithCopyWarning
        df = df.reset_index().copy()
        df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
        return df
    except Exception as e:
        print(f"Exception fetching {symbol}: {e}")
        return None

def check_bullish_divergence(df):
    """ [แก้ไขบั๊ก] ตรวจสอบ Bullish Divergence โดยเทียบราคาต่ำสุดกับ RSI ณ แท่งเดียวกัน """
    if len(df) < 20:
        return False
        
    current_close = df["close"].iloc[-1]
    current_rsi = df["RSI"].iloc[-1]
    
    # มองหาย้อนกลับไปในอดีต (แท่งที่ -15 ถึง -3)
    lookback_df = df.iloc[-15:-3]
    
    # หาตำแหน่ง (Index) ของราคาที่ต่ำที่สุดในช่วงนั้น
    lowest_price_idx = lookback_df["close"].idxmin()
    
    older_close = df["close"].loc[lowest_price_idx]
    older_rsi = df["RSI"].loc[lowest_price_idx] # ดึงค่า RSI จากแท่งเทียนเดียวกับที่ราคาต่ำสุด
    
    # ราคาทำ New Low แต่ RSI ยก Low สูงขึ้น และ RSI ปัจจุบันยังไม่หลุดโซนตึงตัวมากเกินไป
    if current_close < older_close and current_rsi > older_rsi and current_rsi < 45:
        return True
    return False

def check_bearish_divergence(df):
    """ [แก้ไขบั๊ก] ตรวจสอบ Bearish Divergence โดยเทียบราคาสูงสุดกับ RSI ณ แท่งเดียวกัน """
    if len(df) < 20:
        return False
        
    current_close = df["close"].iloc[-1]
    current_rsi = df["RSI"].iloc[-1]
    
    lookback_df = df.iloc[-15:-3]
    
    # หาตำแหน่ง (Index) ของราคาที่สูงที่สุดในช่วงนั้น
    highest_price_idx = lookback_df["close"].idxmax()
    
    older_close = df["close"].loc[highest_price_idx]
    older_rsi = df["RSI"].loc[highest_price_idx] # ดึงค่า RSI จากแท่งเทียนเดียวกับที่ราคาสูงสุด
    
    # ราคาทำ New High แต่ RSI กลับลดลง
    if current_close > older_close and current_rsi < older_rsi and current_rsi > 55:
        return True
    return False

def screen_crypto():
    print("🚀 Starting Binance Thailand Crypto Screener [Engine: Yahoo Finance Global-to-THB]...")
    thb_rate = get_realtime_thb_rate()
    print(f"Current FX Rate from Yahoo: 1 USD = {thb_rate:.2f} THB")
    
    signals = []
    
    for symbol in WATCHLIST:
        display_name = symbol.replace("-USD", "_THB")
        print(f"Scanning {display_name}...")
        
        df = get_historical_data_yf(symbol, interval="1h")
        if df is None or df.empty:
            continue
            
        df["EMA_50"] = ta.ema(df["close"], length=50)
        df["EMA_200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)
        
        # คัดกรองข้อมูลแถวล่าสุด และแถวก่อนหน้า (เช็คการตัดเข้าโซน)
        if len(df) < 2:
            continue
            
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        last_close_usd = last_row["close"]
        last_rsi = last_row["RSI"]
        prev_rsi = prev_row["RSI"]
        
        last_ema50_usd = last_row["EMA_50"]
        last_ema200_usd = last_row["EMA_200"]
        
        last_close_thb = last_close_usd * thb_rate
        last_ema50_thb = last_ema50_usd * thb_rate
        last_ema200_thb = last_ema200_usd * thb_rate
        
        # 🟢 เงื่อนไขเข้าซื้อ: เพิ่งตัดลงมาต่ำกว่าหรือเท่ากับ 32 ในแท่งนี้ (ลดการส่งสแปมซ้ำๆ)
        if last_rsi <= 32 and prev_rsi > 32:
            is_bull_div = check_bullish_divergence(df)
            buy_zone = f"{last_close_thb:,.2f} - {(last_close_thb * 0.98):,.2f}"
            take_profit = f"{(last_close_thb * 1.05):,.2f} (หรือแนวต้าน EMA50: {last_ema50_thb:,.2f})"
            stop_loss = f"{(last_close_thb * 0.95):,.2f}"
            
            status_context = "📉 RSI Oversold"
            if last_close_usd > last_ema200_usd:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (ภาพใหญ่ยังเป็นแนวโน้มขาขึ้น)"
            else:
                status_context += "\n- อยู่ใต้เส้น EMA200 (ภาพใหญ่ขาลง ระวังเน้นเล่นรอบสั้น)"
                
            if is_bull_div:
                status_context += "\n🔥 พบบูลลิชไดเวอร์เจนท์ (Bullish Divergence) มีโอกาสกลับตัวสูง!"
                
            msg = (
                f"\n🟢 [SIGNAL BUY] {display_name}\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (1h): {last_rsi:.2f}\n"
                f"สถานะกราฟ: {status_context}\n"
                f"📍 ช่วงราคาเข้าซื้อ: {buy_zone} THB\n"
                f"🎯 เป้าขายทำกำไร: {take_profit} THB\n"
                f"❌ จุดตัดขาดทุน: {stop_loss} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)
            
        # 🔴 เงื่อนไขเตือนขาย: เพิ่งตัดขึ้นไปสูงกว่าหรือเท่ากับ 70 ในแท่งนี้
        elif last_rsi >= 70 and prev_rsi < 70:
            is_bear_div = check_bearish_divergence(df)
            sell_zone = f"{last_close_thb:,.2f} - {(last_close_thb * 1.02):,.2f}"
            re_entry_zone = f"{(last_close_thb * 0.95):,.2f} (หรือแนวรับ EMA50: {last_ema50_thb:,.2f})"
            trailing_stop = f"{(last_close_thb * 0.97):,.2f}"
            
            status_context = "⚠️ RSI Overbought (ซื้อมากเกินไป)"
            if last_close_usd > last_ema200_usd:
                status_context += "\n+ ยืนเหนือเส้น EMA200 (โครงสร้างแข็งแกร่ง แต่อาจย่อตัวระยะสั้น)"
            else:
                status_context += "\n- อยู่ใต้เส้น EMA200 (เด้งเพื่อลงต่อในภาพใหญ่ ระวังแรงเทขาย)"
                
            if is_bear_div:
                status_context += "\n🚨 พบแบร์ริชไดเวอร์เจนท์ (Bearish Divergence) สัญญาณกลับตัวลงรุนแรง!"
                
            msg = (
                f"\n🔴 [SIGNAL SELL] {display_name}\n"
                f"ราคาปัจจุบัน: {last_close_thb:,.2f} THB\n"
                f"RSI (1h): {last_rsi:.2f}\n"
                f"สถานะกราฟ: {status_context}\n"
                f"📍 โซนแบ่งขายทำกำไร: {sell_zone} THB\n"
                f"🎯 รอรับกลับเมื่อย่อตัว: {re_entry_zone} THB\n"
                f"❌ หลุดจุดนี้ควรหนี (Trailing Stop): {trailing_stop} THB\n"
                f"--------------------------------"
            )
            signals.append(msg)

    if signals:
        alert_header = "📊 [Thai Crypto Screener Report]"
        full_message = alert_header + "".join(signals)
        send_line_messaging_api(full_message)
        print("Success! Notification sent to LINE.")
    else:
        print("Process complete: No assets matched the criteria at this hour.")

if __name__ == "__main__":
    screen_crypto()
