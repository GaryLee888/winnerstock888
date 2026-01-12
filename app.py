import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import os
import platform
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

# ==========================================
# 1. 基礎設定與 Session State 初始化
# ==========================================
st.set_page_config(page_title="當沖雷達 - 終極版", layout="wide")

if "running" not in st.session_state:
    st.session_state.running = False
if "reported_codes" not in st.session_state:
    st.session_state.reported_codes = set()
if "last_total_vol_map" not in st.session_state:
    st.session_state.last_total_vol_map = {}
if "trigger_history" not in st.session_state:
    st.session_state.trigger_history = {}

# API 資訊 (建議從 Streamlit Secrets 讀取)
API_KEY = st.secrets.get("API_KEY", "你的預設KEY")
SECRET_KEY = st.secrets.get("SECRET_KEY", "你的預設SECRET")
DISCORD_URL = st.secrets.get("DISCORD_WEBHOOK_URL", "")

# ==========================================
# 2. 輔助函式
# ==========================================
def get_daily_filename():
    return f"DayTrade_Winner_{datetime.now().strftime('%Y-%m-%d')}.csv" # GitHub環境建議用csv

def create_winner_card(item):
    # 簡化字體處理，適應雲端環境
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.text((40, 60), f"{item['code']} {item['name']}", fill=(255, 255, 255))
    draw.text((40, 120), f"Price: {item['price']}", fill=accent)
    draw.text((40, 180), f"Change: {item['chg']}%", fill=accent)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

def send_discord(item):
    buf = create_winner_card(item)
    content = f"🚀 **發財電報！**\n🔥 **{item['code']} {item['name']}** 爆發中！"
    try:
        requests.post(DISCORD_URL, data={"content": content}, files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except:
        pass

# ==========================================
# 3. Streamlit UI 介面
# ==========================================
st.title("📈 當沖雷達 - 勝率最佳化終極版")

with st.sidebar:
    st.header("⚙️ 核心監控參數")
    scan_interval = st.slider("掃頻(秒)", 5, 60, 10)
    min_chg = st.number_input("漲幅下限%", value=2.5)
    min_vol = st.number_input("昨日交易量>", value=3000)
    momentum_limit = st.number_input("1分動能% >", value=1.5)
    dist_limit = st.number_input("均價乖離% <", value=3.5)
    
    if not st.session_state.running:
        if st.button("▶ 啟動監控", type="primary"):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止監控", type="secondary"):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 4. 核心邏輯
# ==========================================
if st.session_state.running:
    # 初始化 API
    if "api" not in st.session_state:
        with st.spinner("API 登入中..."):
            api = sj.Shioaji()
            api.login(API_KEY, SECRET_KEY)
            
            # 獲取合約
            raw = [c for m in [api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
            st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
            st.session_state.name_map = {c.code: c.name for c in raw}
            st.session_state.cat_map = {c.code: c.category for c in raw}
            st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
            st.session_state.api = api

    # 顯示狀態
    status_placeholder = st.empty()
    table_placeholder = st.empty()
    
    # 模擬循環 (Streamlit 透過自動重新運行來達成更新)
    now = datetime.now()
    status_placeholder.info(f"🔄 正在掃描中... 最後更新: {now.strftime('%H:%M:%S')}")
    
    data_list = []
    # 這裡只取前200檔範例，實際可依效能調整
    contracts_to_check = st.session_state.all_contracts[:500] 
    
    snaps = st.session_state.api.snapshots(contracts_to_check)
    
    for s in snaps:
        code = s.code
        ref = st.session_state.ref_map.get(code, 0)
        if ref <= 0 or s.close <= 0: continue
        
        chg = round(((s.close - ref) / ref * 100), 2)
        
        # 簡易篩選邏輯
        if min_chg <= chg <= 9.8:
            # 計算動能 (與原本邏輯相同)
            vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
            st.session_state.last_total_vol_map[code] = s.total_volume
            
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
            vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
            
            # 觸發次數紀錄
            st.session_state.trigger_history[code] = st.session_state.trigger_history.get(code, 0) + 1
            
            item = {
                "代碼": code,
                "名稱": st.session_state.name_map.get(code, ""),
                "現價": s.close,
                "漲幅%": chg,
                "均價乖離": vwap_dist,
                "觸發次數": st.session_state.trigger_history[code]
            }
            data_list.append(item)
            
            # 通報邏輯
            if st.session_state.trigger_history[code] >= 10 and code not in st.session_state.reported_codes:
                if vwap_dist <= dist_limit:
                    send_discord(item)
                    st.session_state.reported_codes.add(code)
                    st.toast(f"🚀 已通報: {code} {item['名稱']}")

    # 更新表格
    if data_list:
        df_display = pd.DataFrame(data_list).sort_values("觸發次數", ascending=False)
        table_placeholder.table(df_display.head(20))
    
    # 等待並刷新
    time.sleep(scan_interval)
    st.rerun()

else:
    st.warning("👈 請點擊左側「啟動監控」開始運行。")
    if os.path.exists(get_daily_filename()):
        st.download_button("下載今日交易紀錄", open(get_daily_filename(), "rb"), file_name=get_daily_filename())
