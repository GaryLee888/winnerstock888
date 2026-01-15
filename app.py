import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
import platform
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 核心設定區
# ==========================================
API_KEY = "5FhL23V9888K6yMnMK3S7CAnCdHAtrESypTGprqRz"
SECRET_KEY = "HV8yi97EpyTYxN9yEB9tiEjnWpNZeNLcVyf4WRw"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1457393304537927764/D2vpM73dMl2Z-bLfI0Us52eGdCQyjztASwkBP3RzyF2jaALzEeaigajpXQfzsgLdyzw4"

# 介面設定
st.set_page_config(page_title="當沖雷達 Web版", layout="wide")

# ==========================================
# 2. 初始化 Session State (關鍵 Bug 修復)
# ==========================================
if 'history' not in st.session_state:
    st.session_state.history = []
if 'reported_codes' not in st.session_state:
    st.session_state.reported_codes = set()
if 'running' not in st.session_state:
    st.session_state.running = False
if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()
if 'contracts' not in st.session_state:
    st.session_state.contracts = []
    st.session_state.ref_map = {}
    st.session_state.name_map = {}

# ==========================================
# 3. 核心功能函式
# ==========================================

def get_font(size):
    """根據環境取得字體，避免 Linux 報錯"""
    try:
        if platform.system() == "Windows":
            return ImageFont.truetype("msjhbd.ttc", size)
        else:
            # Linux (Cloud) 常用路徑
            return ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", size)
    except:
        return ImageFont.load_default()

def send_discord_alert(item):
    """生成卡片並發送至 Discord"""
    # 建立圖片
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 135), f"現價: {item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    content = f"🚀 **發財電報！** {item['code']} {item['name']} 觸發條件！"
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except Exception as e:
        print(f"Discord 發送失敗: {e}")

# ==========================================
# 4. 網頁 UI 佈局
# ==========================================
st.title("🚀 當沖雷達 - Web 終極版")

with st.sidebar:
    st.header("⚙️ 參數設定")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    vol_min = st.number_input("成交張數 >", value=3000)
    chg_min = st.number_input("漲幅下限 %", value=2.5)
    vwap_max = st.number_input("均價乖離 % <", value=3.5)
    
    st.divider()
    
    if not st.session_state.running:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            with st.spinner("API 登入與合約初始化中..."):
                try:
                    st.session_state.api.login(API_KEY, SECRET_KEY)
                    # 初始化合約
                    raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] 
                           for c in m if len(c.code) == 4]
                    st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
                    st.session_state.name_map = {c.code: c.name for c in raw}
                    st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
                    st.session_state.running = True
                    st.rerun()
                except Exception as e:
                    st.error(f"登入失敗: {e}")
    else:
        if st.button("■ 停止監控", use_container_width=True):
            st.session_state.running = False
            st.rerun()

    st.divider()
    st.header("📊 盤後處理")
    if st.button("🏁 一鍵結算收盤價", use_container_width=True):
        if st.session_state.history:
            # 結算邏輯
            target_codes = list(set([str(i['code']) for i in st.session_state.history]))
            target_contracts = [c for c in st.session_state.contracts if c.code in target_codes]
            
            snap_map = {}
            for i in range(0, len(target_contracts), 100):
                snaps = st.session_state.api.snapshots(target_contracts[i:i+100])
                for s in snaps: snap_map[s.code] = s.close
            
            for item in st.session_state.history:
                if item['code'] in snap_map:
                    cp = snap_map[item['code']]
                    item['收盤價'] = cp
                    item['績效%'] = round((cp - item['price']) / item['price'] * 100, 2)
            st.success("結算完成！")
        else:
            st.warning("尚無資料可結算")

    # 下載 Excel 按鈕 (方案一核心)
    if st.session_state.history:
        df_exp = pd.DataFrame(st.session_state.history)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_exp.to_excel(writer, index=False)
        st.download_button(
            label="📥 下載 Excel 到電腦",
            data=output.getvalue(),
            file_name=f"Trade_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ==========================================
# 5. 監控主循環
# ==========================================
if st.session_state.running:
    status_area = st.empty()
    table_area = st.empty()
    
    status_area.info(f"正在監控 {len(st.session_state.contracts)} 檔標的... 最後更新: {datetime.now().strftime('%H:%M:%S')}")
    
    # 分批抓取 Snapshot 防止逾時
    all_snaps = []
    for i in range(0, len(st.session_state.contracts), 100):
        batch = st.session_state.api.snapshots(st.session_state.contracts[i:i+100])
        all_snaps.extend(batch)
        time.sleep(0.05) # 稍微緩衝

    # 邏輯判斷
    for s in all_snaps:
        if s.close <= 0: continue
        ref = st.session_state.ref_map.get(s.code, 0)
        chg = round((s.close - ref) / ref * 100, 2)
        
        # 基本過濾
        if chg >= chg_min and s.total_volume >= vol_min and s.code not in st.session_state.reported_codes:
            # 均價乖離判斷
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
            vwap_dist = round((s.close - vwap) / vwap * 100, 2)
            
            if vwap_dist <= vwap_max:
                new_item = {
                    "通報時間": datetime.now().strftime("%H:%M:%S"),
                    "代碼": s.code,
                    "名稱": st.session_state.name_map.get(s.code, ""),
                    "price": s.close,
                    "chg": chg,
                    "vwap_dist": vwap_dist,
                    "收盤價": None,
                    "績效%": None
                }
                st.session_state.history.append(new_item)
                st.session_state.reported_codes.add(s.code)
                send_discord_alert(new_item) # 發送 Discord

    # 顯示歷史清單
    if st.session_state.history:
        table_area.dataframe(pd.DataFrame(st.session_state.history).tail(15), use_container_width=True)
    
    time.sleep(scan_sec)
    st.rerun() # 觸發 Streamlit 刷新

else:
    st.write("👋 請點擊側邊欄「啟動監控」開始運行。")
    if st.session_state.history:
        st.subheader("今日累積訊號")
        st.dataframe(pd.DataFrame(st.session_state.history), use_container_width=True)
