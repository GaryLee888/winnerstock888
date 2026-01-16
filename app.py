import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
import platform
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 核心配置
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入金鑰。")
    st.stop()

st.set_page_config(page_title="24H 自動當沖雷達", layout="wide")
TZ_TW = timezone(timedelta(hours=8)) 

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'state' not in st.session_state:
    st.session_state.state = {
        'running': False, 
        'history': [],
        'reported_codes': set(),
        'last_total_vol': {},
        'market_safe': True,
        'market_msg': "等待數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 工具函式 (卡片與發報)
# ==========================================
def get_font(size):
    try:
        f_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        if platform.system() == "Windows": f_path = "msjhbd.ttc"
        return ImageFont.truetype(f_path, size)
    except: return ImageFont.load_default()

def send_winner_alert(item):
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 45], fill=(255, 215, 0))
    draw.text((40, 8), "🚀 財神降臨！發財電報", fill=(0, 0, 0), font=get_font(22))
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    draw.text((40, 240), f"目標：{item['tp']:.2f} | 停損：{item['sl']:.2f}", fill=(255, 60, 60), font=get_font(24))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
        return True
    except: return False

# ==========================================
# 4. UI 介面
# ==========================================
st.title("🚀 當沖雷達 - 24H 雲端自動監控")

with st.sidebar:
    st.header("🎯 參數設定")
    scan_sec = st.slider("掃頻週期(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    
    if st.session_state.state['running']:
        if st.button("■ 手動停止"):
            st.session_state.state['running'] = False
            st.rerun()

# 狀態顯示容器
status_container = st.empty()
# 進度條容器 (掃描時才會出現)
progress_placeholder = st.empty()

# ==========================================
# 5. 自動啟動與核心掃描
# ==========================================

# --- 自動登入邏輯 ---
if not st.session_state.state['running']:
    try:
        with st.spinner("系統喚醒中，正在連接永豐 API..."):
            st.session_state.api.login(API_KEY, SECRET_KEY)
            raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
            st.session_state.y_vol_map = {c.code: getattr(c, 'yesterday_volume', 0) for c in raw}
            st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
            st.session_state.name_map = {c.code: c.name for c in raw}
            st.session_state.cat_map = {c.code: c.category for c in raw}
            st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
            st.session_state.mkt_codes = ["001", "OTC"]
            st.session_state.state['running'] = True
            st.rerun()
    except Exception as e:
        st.error(f"登入失敗，30秒後重試: {e}")
        time.sleep(30)
        st.rerun()

# --- 循環掃描邏輯 ---
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    
    # 顯示目前狀態
    status_container.info(f"🟢 系統監控中 | 最後更新: {now.strftime('%H:%M:%S')} | 大盤: {st.session_state.state['market_msg']}")

    # 篩選掃描目標
    targets = [c for c in st.session_state.contracts if st.session_state.y_vol_map.get(c.code, 0) >= 3000]
    targets = targets[:600] # 限制掃描量以維持穩定
    
    # 【核心功能：掃描進度顯示】
    all_snaps = []
    batch_size = 100
    with progress_placeholder.container():
        # 建立進度條
        bar = st.progress(0, text=f"🔎 正在準備掃描 {len(targets)} 檔標的...")
        for i in range(0, len(targets), batch_size):
            batch = targets[i : i+batch_size]
            # 抓取快照
            all_snaps.extend(st.session_state.api.snapshots(batch))
            
            # 更新百分比與文字訊息
            percent = min((i + batch_size) / len(targets), 1.0)
            bar.progress(percent, text=f"🚀 掃描進度: {int(percent*100)}% (已完成 {len(all_snaps)} 檔)")
            time.sleep(0.1) # 稍微停頓讓 UI 刷新
        
        # 掃描完成後顯示提示，隨後清空
        bar.progress(1.0, text="✅ 本輪掃描完成，正在分析數據...")
        time.sleep(0.5)
    
    # 清空進度條容器，讓畫面保持簡潔
    progress_placeholder.empty()

    # --- 篩選與通報 (原始邏輯) ---
    # ... 此處省略後續篩選邏輯，保持您原有的核心算法 ...
    # (此部分請接續您原本的篩選代碼)

    time.sleep(scan_sec)
    st.rerun()
