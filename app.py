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
# 1. 核心配置 (從 Secrets 讀取)
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入金鑰。")
    st.stop()

st.set_page_config(page_title="24H 自動當沖雷達", layout="wide")
TZ_TW = timezone(timedelta(hours=8)) # 台灣時區校正

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'state' not in st.session_state:
    st.session_state.state = {
        'running': False, # 預設 False，下方會自動觸發改為 True
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
    draw.text((40, 8), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=get_font(22))
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    draw.text((40, 240), f"目標停利：{item['tp']:.2f} | 停損：{item['sl']:.2f}", fill=(255, 60, 60), font=get_font(24))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
        return True
    except: return False

# ==========================================
# 4. UI 參數區
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻週期(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    # 這裡保留啟動/停止狀態顯示，但會自動執行
    if st.session_state.state['running']:
        if st.button("■ 手動停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()
    else:
        st.warning("⚡ 系統目前正在嘗試自動連線...")

# ==========================================
# 5. 自動啟動與核心邏輯
# ==========================================
st.title("🚀 當沖雷達 - 24H 雲端自動版")

# 下載按鈕
if st.session_state.state['history']:
    df_save = pd.DataFrame(st.session_state.state['history'])
    output_excel = io.BytesIO()
    with pd.ExcelWriter(output_excel, engine='xlsxwriter') as writer:
        df_save.to_excel(writer, index=False)
    st.download_button("📥 下載目前戰果 (Excel)", output_excel.getvalue(), 
                       file_name=f"Trade_Log_{datetime.now(TZ_TW).strftime('%m%d_%H%M')}.xlsx")

status_container = st.empty()
progress_container = st.empty()

# --- 自動啟動邏輯 ---
if not st.session_state.state['running']:
    try:
        with st.spinner("正在自動登入並抓取合約資料..."):
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
        st.error(f"自動啟動失敗: {e}")
        time.sleep(30) # 失敗則等 30 秒後自動重試
        st.rerun()

# --- 核心循環 ---
if st.session_state.state['running']:
    # 檢查 API 存活
    try:
        st.session_state.api.list_accounts()
    except:
        st.session_state.api.login(API_KEY, SECRET_KEY)

    now = datetime.now(TZ_TW)
    
    # [A] 大盤風險
    try:
        m_snaps = st.session_state.api.snapshots(st.session_state.mkt_codes)
        danger = False
        m_msgs = []
        for ms in m_snaps:
            if ms.close <= 0: continue
            name = "加權" if ms.code == "001" else "櫃買"
            st.session_state.state['market_history'][ms.code] = [(t, p) for t, p in st.session_state.state['market_history'][ms.code] if t > now - timedelta(minutes=10)]
            st.session_state.state['market_history'][ms.code].append((now, ms.close))
            past = [p for t, p in st.session_state.state['market_history'][ms.code] if t < now - timedelta(minutes=2)]
            if past:
                diff = (ms.close - past[-1]) / past[-1] * 100
                if diff < -0.15: danger = True; m_msgs.append(f"{name}急殺({diff:.2f}%)")
                else: m_msgs.append(f"{name}穩定")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs)
    except: st.session_state.state['market_safe'] = True

    status_container.info(f"🕒 更新時間: {now.strftime('%H:%M:%S')} | 大盤: {st.session_state.state['market_msg']}")

    # [B] 分批掃描
    targets = [c for c in st.session_state.contracts if st.session_state.y_vol_map.get(c.code, 0) >= vol_yesterday_min]
    targets = targets[:600]
    
    with progress_container:
        bar = st.progress(0, text="🔎 雷達偵測中...")
        all_snaps = []
        batch_size = 100
        for i in range(0, len(targets), batch_size):
            batch = targets[i : i+batch_size]
            all_snaps.extend(st.session_state.api.snapshots(batch))
            percent = min((i + batch_size) / len(targets), 1.0)
            bar.progress(percent, text=f"🔎 掃描進度 ({int(percent*100)}%)")
            time.sleep(0.05)
        bar.empty()

    # [C] 原始過濾邏輯 100% 鎖定
    hm = now.hour * 100 + now.minute
    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)
    vol_threshold = vol_base * vol_weight
    
    cat_hits = {}
    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0 or s.total_volume < vol_total_min: continue
        chg = round(((price - ref) / ref * 100), 2)
        if not (chg_min <= chg <= 9.8): continue
        
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        ratio = round(s.total_volume / (st.session_state.y_vol_map.get(code, 1) or 1), 2)
        if ratio < vol_threshold: continue
        
        if (min_vol_pct >= adj_mom_thr or vol_diff >= 50):
            daily_high = s.high if s.high > 0 else price
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
            vwap_dist = round(((price - vwap) / vwap * 100), 2)
            
            if ((daily_high - price) / daily_high * 100) <= drawdown_limit and vwap_dist <= vwap_gap_limit:
                st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
                cat = st.session_state.cat_map.get(code, "未知")
                cat_hits[cat] = cat_hits.get(cat, 0) + 1
                
                if len(st.session_state.state['trigger_history'][code]) >= hit_thr and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
                    item = {
                        "通報時間": now.strftime("%H:%M:%S"), "code": code, "name": st.session_state.name_map.get(code),
                        "產業": cat, "price": price, "chg": chg, "tp": round(price * 1.025, 2), "sl": round(price * 0.985, 2)
                    }
                    st.session_state.state['history'].append(item)
                    st.session_state.state['reported_codes'].add(code)
                    send_winner_alert(item)

    if st.session_state.state['history']:
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(15), use_container_width=True)
    
    time.sleep(scan_sec)
    st.rerun()
