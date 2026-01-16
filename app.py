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
# 1. 核心配置與時區
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請檢查 Settings -> Secrets。")
    st.stop()

st.set_page_config(page_title="當沖雷達-診斷修復版", layout="wide")
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
        'trigger_history': {},
        'debug_info': {
            'last_scan_count': 0,
            'max_vol_diff': 0,
            'filtered_by_chg': 0,
            'filtered_by_vol': 0,
            'error_log': "系統初始化..."
        }
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 工具函式
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
    draw.text((40, 8), "🚀 財神降臨！發報成功", fill=(0, 0, 0), font=get_font(22))
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 4. UI 介面
# ==========================================
st.title("🚀 當沖雷達 - 系統診斷中心")

# 診斷看板
diag_col1, diag_col2, diag_col3, diag_col4 = st.columns(4)
diag_col1.metric("API 狀態", "🟢 在線" if st.session_state.state['running'] else "🔴 斷線")
diag_col2.metric("每輪掃描標的", f"{st.session_state.state['debug_info']['last_scan_count']} 檔")
diag_col3.metric("本輪最大量差", f"{st.session_state.state['debug_info']['max_vol_diff']} 張")
diag_col4.metric("系統狀態碼", st.session_state.state['debug_info']['error_log'])

with st.sidebar:
    st.header("🎯 參數設定")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)

# 自動啟動邏輯 (修正屬性錯誤)
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(API_KEY, SECRET_KEY)
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        
        # ✨ 重要修正：yesterday_vol 而非 yesterday_volume
        st.session_state.y_vol_map = {c.code: (c.yesterday_vol if hasattr(c, 'yesterday_vol') else 0) for c in raw}
        
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.state['debug_info']['last_scan_count'] = len(st.session_state.contracts)
        st.session_state.state['debug_info']['error_log'] = "合約預載完成"
        st.session_state.state['running'] = True
        st.rerun()
    except Exception as e:
        st.session_state.state['debug_info']['error_log'] = f"錯誤: {str(e)}"
        time.sleep(10); st.rerun()

# ==========================================
# 5. 核心監控循環
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    current_max_diff = 0

    # 批次掃描
    all_snaps = []
    with st.spinner("正在同步行情..."):
        for i in range(0, len(st.session_state.contracts), 100):
            batch = st.session_state.contracts[i:i+100]
            all_snaps.extend(st.session_state.api.snapshots(batch))
    
    current_detecting = []
    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        y_vol = st.session_state.y_vol_map.get(code, 0)
        
        if price <= 0 or ref <= 0: continue
        chg = round(((price - ref) / ref * 100), 2)
        
        # 篩選
        if chg < chg_min or s.total_volume < vol_total_min or y_vol < vol_yesterday_min:
            continue
        
        # 動能計算
        last_vol = st.session_state.state['last_total_vol'].get(code)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        if last_vol is None: continue 
        
        vol_diff = s.total_volume - last_vol
        if vol_diff > current_max_diff: current_max_diff = vol_diff
        
        # 觸發看板內容
        if vol_diff >= 50 or chg >= 2.5:
            current_detecting.append({
                "代碼": code, "名稱": st.session_state.name_map.get(code),
                "現價": price, "漲幅%": chg, "量差": vol_diff
            })

    # 更新診斷數據
    st.session_state.state['debug_info']['max_vol_diff'] = current_max_diff
    st.session_state.state['debug_info']['error_log'] = "正常監控中"

    st.subheader("🔍 即時偵測看板")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("量差", ascending=False), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
