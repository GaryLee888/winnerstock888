import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 持久化檔案配置 (解決重整歸零)
# ==========================================
REPORT_FILE = "report_history.csv"

def load_local_history():
    if os.path.exists(REPORT_FILE):
        try:
            return pd.read_csv(REPORT_FILE)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def save_to_local(df):
    df.to_csv(REPORT_FILE, index=False)

# ==========================================
# 2. 核心配置與初始化
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請檢查 Settings -> Secrets。")
    st.stop()

st.set_page_config(page_title="24H 雲端當沖雷達", layout="wide")
TZ_TW = timezone(timedelta(hours=8))

# 初始化 Session State 並加載舊紀錄
if 'state' not in st.session_state:
    # 從 CSV 加載歷史通報
    history_df = load_local_history()
    st.session_state.state = {
        'running': False,
        'history': history_df.to_dict('records'),
        'reported_codes': set(history_df['code'].astype(str)) if not history_df.empty else set(),
        'last_total_vol': {},
        'market_safe': True,
        'market_msg': "等待數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# --- 工具函式 (Discord 發報與卡片) ---
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
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 3. 介面與自動啟動
# ==========================================
st.title("🚀 24H 雲端自動雷達")

# 手動存紀錄功能 (從 CSV 讀取確保完整)
current_history = pd.DataFrame(st.session_state.state['history'])
if not current_history.empty:
    excel_data = io.BytesIO()
    with pd.ExcelWriter(excel_data, engine='xlsxwriter') as writer:
        current_history.to_excel(writer, index=False)
    st.download_button("📥 下載完整通報紀錄 (Excel)", excel_data.getvalue(), 
                       file_name=f"Trade_Log_{datetime.now(TZ_TW).strftime('%Y%m%d')}.xlsx")

status_container = st.empty()
progress_container = st.empty()

with st.sidebar:
    st.header("🎯 參數設定")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_total_min = st.number_input("基準總量>", value=3000)

# 自動啟動邏輯
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(API_KEY, SECRET_KEY)
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.state['running'] = True
        st.rerun()
    except: time.sleep(10); st.rerun()

# ==========================================
# 4. 核心監控循環 (修復 NameError)
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    
    # 依照時間設定門檻 (核心邏輯不變)
    if hm < 1000: hit_thr = 15
    elif hm < 1100: hit_thr = 12
    elif hm < 1230: hit_thr = 8
    else: hit_thr = 6

    # 模擬進度掃描
    all_snaps = []
    with progress_container:
        bar = st.progress(0, text="🔎 掃描中...")
        for i in range(0, len(st.session_state.contracts), 100):
            batch = st.session_state.contracts[i:i+100]
            all_snaps.extend(st.session_state.api.snapshots(batch))
            bar.progress(min((i+100)/len(st.session_state.contracts), 1.0))
        bar.empty()

    current_detecting = []

    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0: continue
        
        chg = round(((price - ref) / ref * 100), 2)
        
        if chg >= chg_min and s.total_volume >= vol_total_min:
            # 計算動能與次數
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'].get(code, s.total_volume)
            st.session_state.state['last_total_vol'][code] = s.total_volume
            
            if vol_diff >= 50: # 動能觸發
                st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
            
            hits = len(st.session_state.state['trigger_history'].get(code, []))
            
            # 顯示看板數據
            current_detecting.append({"代碼": code, "股名": st.session_state.name_map.get(code), "現價": price, "次數": hits, "漲幅": chg})

            # 發報邏輯
            if hits >= hit_thr and str(code) not in st.session_state.state['reported_codes']:
                item = {
                    "通報時間": now.strftime("%H:%M:%S"), "code": str(code), 
                    "name": st.session_state.name_map.get(code), "price": price, "chg": chg
                }
                # 更新狀態
                st.session_state.state['history'].append(item)
                st.session_state.state['reported_codes'].add(str(code))
                # 寫入 CSV 持久化
                save_to_local(pd.DataFrame(st.session_state.state['history']))
                # 發送 Discord
                send_winner_alert(item)

    # 顯示即時看板
    st.subheader("🔍 即時偵測紀錄 (F5 重整不會消失)")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
