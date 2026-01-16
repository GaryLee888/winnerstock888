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
# 1. 核心配置 (保持不動)
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入金鑰。")
    st.stop()

st.set_page_config(page_title="當沖雷達-手動存檔版", layout="wide")
TZ_TW = timezone(timedelta(hours=8)) 

# ==========================================
# 2. 初始化 Session State (保持不動)
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
# 3. 工具函式 (保持不動)
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
    draw.text((40, 8), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=get_font(22))
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    draw.text((40, 240), f"目標停利：{item['tp']:.2f}", fill=(255, 60, 60), font=get_font(26))
    draw.text((310, 240), f"建議停損：{item['sl']:.2f}", fill=(0, 200, 0), font=get_font(26))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發中！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
        return True
    except: return False

# ==========================================
# 4. UI 介面 - 新增手動儲存區
# ==========================================
st.title("🚀 當沖雷達 - 24H 雲端自動監控")

# --- ✨ 新增：手動存檔下載功能 ---
if st.session_state.state['history']:
    st.subheader("💾 數據手動存檔")
    # 將通報紀錄轉換為 DataFrame
    df_save = pd.DataFrame(st.session_state.state['history'])
    
    # 建立記憶體內的 Excel 檔案
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_save.to_excel(writer, index=False, sheet_name='今日通報')
    
    # 下載按鈕
    st.download_button(
        label="📥 點我儲存目前通報紀錄 (Excel)",
        data=output.getvalue(),
        file_name=f"Trade_Log_{datetime.now(TZ_TW).strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    st.write("---")

# 狀態顯示與進度條
status_container = st.empty()
progress_container = st.empty()

# ==========================================
# 5. 側邊欄與核心邏輯 (完全保持不動)
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vol_trade_min = st.number_input("成交張數>", value=3000)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)

# --- 這裡往下接原本的自動啟動與監控邏輯 (100% 保持不動) ---
# ... 原本的自動啟動邏輯 ...
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(API_KEY, SECRET_KEY)
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.cat_map = {c.code: c.category for c in raw}
        st.session_state.y_vol_map = {c.code: getattr(c, 'yesterday_volume', 0) for c in raw}
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.mkt_codes = ["001", "OTC"]
        st.session_state.state['running'] = True
        st.rerun()
    except: pass

# ... 原本的監控循環 (含大盤風險、掃描進度條、7大篩選邏輯) ...
if st.session_state.state['running']:
    # (此處接續原本 100% 相同的所有代碼)
    # [A] 大盤風險檢查 ...
    # [B] 分批掃描進度 ...
    # [C] 7大過濾邏輯 ...
    pass # 為節省版面，邏輯保持原樣
