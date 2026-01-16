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
# 1. 核心配置 (Secrets 讀取)
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入金鑰。")
    st.stop()

st.set_page_config(page_title="當沖雷達-診斷版", layout="wide")
TZ_TW = timezone(timedelta(hours=8)) 

# ==========================================
# 2. 初始化 Session State (新增診斷欄位)
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
        'debug_info': {  # 新增診斷資訊
            'last_scan_count': 0,
            'max_vol_diff': 0,
            'avg_response_time': 0,
            'filtered_by_chg': 0,
            'filtered_by_vol': 0
        }
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 工具函式 (保持 100% 原始邏輯)
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
    draw.text((40, 240), f"目標：{item['tp']:.2f} | 停損：{item['sl']:.2f}", fill=(255, 60, 60), font=get_font(26))
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
st.title("🚀 當沖雷達 - 系統診斷中心")

# --- ✨ 新增：系統診斷儀表板 ---
diag_col1, diag_col2, diag_col3, diag_col4 = st.columns(4)
diag_col1.metric("API 狀態", "🟢 在線" if st.session_state.state['running'] else "🔴 斷線")
diag_col2.metric("每輪掃描標的", f"{st.session_state.state['debug_info']['last_scan_count']} 檔")
diag_col3.metric("本輪最大量差", f"{st.session_state.state['debug_info']['max_vol_diff']} 張")
diag_col4.metric("過濾統計 (漲幅/總量)", f"{st.session_state.state['debug_info']['filtered_by_chg']} / {st.session_state.state['debug_info']['filtered_by_vol']}")

# 下載存檔按鈕
if st.session_state.state['history']:
    df_save = pd.DataFrame(st.session_state.state['history'])
    excel_data = io.BytesIO()
    with pd.ExcelWriter(excel_data, engine='xlsxwriter') as writer:
        df_save.to_excel(writer, index=False)
    st.download_button("📥 下載通報紀錄", excel_data.getvalue(), file_name="Trade_Log.xlsx")

status_container = st.empty()
progress_container = st.empty()

with st.sidebar:
    st.header("🎯 參數設定")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    # 診斷專用：手動重啟
    if st.button("🔴 手動重啟連線"):
        st.session_state.state['running'] = False
        st.rerun()

# 自動啟動邏輯
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(API_KEY, SECRET_KEY)
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.cat_map = {c.code: c.category for c in raw}
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.mkt_codes = ["001", "OTC"]
        st.session_state.state['running'] = True
        st.rerun()
    except: time.sleep(10); st.rerun()

# ==========================================
# 5. 核心監控循環 (偵測紀錄顯示)
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    
    # 重置本輪診斷計數
    st.session_state.state['debug_info']['filtered_by_chg'] = 0
    st.session_state.state['debug_info']['filtered_by_vol'] = 0
    current_max_diff = 0

    try:
        m_snaps = st.session_state.api.snapshots(st.session_state.mkt_codes)
        danger = False
        m_msgs = []
        for ms in m_snaps:
            if ms.close <= 0: continue
            name = "加權" if ms.code == "001" else "櫃買"
            st.session_state.state['market_history'][ms.code] = [(t, p) for t, p in st.session_state.state['market_history'][ms.code] if t > now - timedelta(minutes=5)]
            st.session_state.state['market_history'][ms.code].append((now, ms.close))
            past = [p for t, p in st.session_state.state['market_history'][ms.code] if t < now - timedelta(minutes=2)]
            if past:
                diff = (ms.close - past[-1]) / past[-1] * 100
                if diff < -0.15: danger = True; m_msgs.append(f"{name}急殺")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs) if m_msgs else "大盤穩定"
    except: st.session_state.state['market_safe'] = True

    status_container.info(f"🕒 {now.strftime('%H:%M:%S')} | 大盤: {st.session_state.state['market_msg']}")

    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)
    vol_threshold = vol_base * vol_weight

    all_snaps = []
    with progress_container:
        bar = st.progress(0, text="🔎 同步行情中...")
        for i in range(0, len(st.session_state.contracts), 100):
            batch = st.session_state.contracts[i:i+100]
            all_snaps.extend(st.session_state.api.snapshots(batch))
            bar.progress(min((i+100)/len(st.session_state.contracts), 1.0))
        bar.empty()
    
    st.session_state.state['debug_info']['last_scan_count'] = len(all_snaps)

    # [C] 篩選
    current_detecting = [] 
    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0: continue
        
        chg = round(((price - ref) / ref * 100), 2)
        
        # 診斷統計：因漲幅被濾掉
        if chg < chg_min:
            st.session_state.state['debug_info']['filtered_by_chg'] += 1
            continue
            
        # 診斷統計：因總成交量被濾掉
        if s.total_volume < vol_total_min:
            st.session_state.state['debug_info']['filtered_by_vol'] += 1
            continue
        
        # 計算動能
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: 
                min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
                if vol_diff > current_max_diff: current_max_diff = vol_diff
        st.session_state.state['last_total_vol'][code] = s.total_volume

        # 紀錄觸發次數
        if (min_vol_pct >= adj_mom_thr or vol_diff >= 50):
            st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        
        hits = len(st.session_state.state['trigger_history'].get(code, []))
        current_detecting.append({"代碼": code, "名稱": st.session_state.name_map.get(code), "現價": price, "次數": hits, "漲幅%": chg, "量差": vol_diff})

        # 發報門檻
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        if (min_vol_pct >= adj_mom_thr or vol_diff >= 50) and ratio >= vol_threshold:
            daily_high = s.high if s.high > 0 else price
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
            vwap_dist = round(((price - vwap) / vwap * 100), 2)
            if ((daily_high - price) / daily_high * 100) <= drawdown_limit and vwap_dist <= vwap_gap_limit:
                if hits >= hit_thr and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
                    item = {"通報時間": now.strftime("%H:%M:%S"), "code": code, "name": st.session_state.name_map.get(code), "price": price, "chg": chg, "tp": round(price * 1.025, 2), "sl": round(price * 0.985, 2)}
                    st.session_state.state['history'].append(item)
                    st.session_state.state['reported_codes'].add(code)
                    send_winner_alert(item)

    st.session_state.state['debug_info']['max_vol_diff'] = current_max_diff

    # [D] 顯示看板
    st.subheader("🔍 即時偵測看板 (依觸發次數排序)")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)
    
    time.sleep(scan_sec)
    st.rerun()
