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
# 1. 持久化設定
# ==========================================
REPORT_FILE = "report_history.csv"
TZ_TW = timezone(timedelta(hours=8))

def load_local_history():
    if os.path.exists(REPORT_FILE):
        try:
            df = pd.read_csv(REPORT_FILE)
            df['code'] = df['code'].astype(str)
            return df
        except: return pd.DataFrame()
    return pd.DataFrame()

def save_to_local(df):
    df.to_csv(REPORT_FILE, index=False)

st.set_page_config(page_title="當沖雷達-雲端穩定修復版", layout="wide")

# ==========================================
# 2. Session State 初始化 (修正數據遺失問題)
# ==========================================
if 'state' not in st.session_state:
    history_df = load_local_history()
    st.session_state.state = {
        'running': False,
        'history': history_df.to_dict('records'),
        'reported_codes': set(history_df['code'].tolist()) if not history_df.empty else set(),
        'last_total_vol': {}, 
        'market_safe': True,
        'market_msg': "等待數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {},
        'diag_msg': "系統就緒"
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 核心自動啟動 (確保 y_vol 正確抓取)
# ==========================================
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(st.secrets["SHIOAJI_API_KEY"], st.secrets["SHIOAJI_SECRET_KEY"])
        # 抓取所有合約
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        
        # 關鍵修復：確保昨日量與參考價被強制讀取
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.cat_map = {c.code: c.category for c in raw}
        
        # 強制抓取昨日成交量，避免 snapshots 漏掉
        st.session_state.y_vol_map = {}
        for c in raw:
            v = getattr(c, 'yesterday_volume', 0)
            if v is None: v = 0
            st.session_state.y_vol_map[c.code] = v
            
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.mkt_codes = ["001", "OTC"]
        st.session_state.state['running'] = True
        st.rerun()
    except Exception as e:
        st.error(f"連線失敗: {e}"); time.sleep(10); st.rerun()

# ==========================================
# 4. UI 介面
# ==========================================
with st.sidebar:
    st.header("🎯 核心參數 (對齊桌面版)")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)

# ==========================================
# 5. 監控循環 (修正資料比對邏輯)
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    
    # [A] 大盤風險
    try:
        m_snaps = st.session_state.api.snapshots(st.session_state.mkt_codes)
        danger = False
        for ms in m_snaps:
            if ms.close <= 0: continue
            st.session_state.state['market_history'][ms.code] = [(t, p) for t, p in st.session_state.state['market_history'][ms.code] if t > now - timedelta(minutes=5)]
            st.session_state.state['market_history'][ms.code].append((now, ms.close))
            past = [p for t, p in st.session_state.state['market_history'][ms.code] if t < now - timedelta(minutes=2)]
            if past and (ms.close - past[-1]) / past[-1] * 100 < -0.15: danger = True
        st.session_state.state['market_safe'] = not danger
    except: st.session_state.state['market_safe'] = True

    # [B] 分段閾值
    if hm < 1000: vol_base, hit_thr = 0.55, 15
    elif hm < 1100: vol_base, hit_thr = 0.40, 12
    elif hm < 1230: vol_base, hit_thr = 0.25, 8
    else: vol_base, hit_thr = 0.20, 6
    adj_mom_thr = (mom_min_pct * (1.6 if hm < 1000 else 1.0)) * (scan_sec / 60.0)

    # [C] 批次掃描 (解決 Linux 環境回傳不全問題)
    all_snaps = []
    targets = st.session_state.contracts
    batch_size = 50 # 縮小批次提高穩定度
    for i in range(0, len(targets), batch_size):
        snaps = st.session_state.api.snapshots(targets[i:i+batch_size])
        all_snaps.extend(snaps)
        time.sleep(0.02) # 輕微延遲避免 API 擠塞

    current_detecting = []
    for s in all_snaps:
        code = s.code
        price = s.close
        ref = st.session_state.ref_map.get(code, 0)
        y_vol = st.session_state.y_vol_map.get(code, 0)
        
        # --- 核心邏輯檢查 ---
        if price <= 0 or ref <= 0: continue
        chg = round(((price - ref) / ref * 100), 2)
        
        # 1. 漲幅與昨日量門檻
        if chg < chg_min or y_vol < vol_yesterday_min: continue
        
        # 2. 今日總量門檻 (基準總量)
        if s.total_volume < vol_total_min: continue

        # 3. 量增率計算 (ratio)
        safe_y_vol = y_vol if y_vol > 0 else 1
        ratio = round(s.total_volume / safe_y_vol, 2)
        if ratio < (vol_base * vol_weight): continue

        # 4. 動能計算 (vol_diff)
        last_vol = st.session_state.state['last_total_vol'].get(code)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        if last_vol is None: continue 
        
        vol_diff = s.total_volume - last_vol
        min_vol_pct = round((vol_diff / s.total_volume * 100), 2) if s.total_volume > 0 else 0
        
        momentum_ok = (min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)
        if not momentum_ok: continue
        
        # 5. 回撤與均價乖離
        daily_high = s.high if s.high > 0 else price
        if ((daily_high - price) / daily_high * 100) > drawdown_limit: continue
        
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        if vwap_dist > vwap_gap_limit: continue
        
        # 6. 通報
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.state['trigger_history'][code])
        
        current_detecting.append({"代碼": code, "股名": st.session_state.name_map.get(code), "現價": price, "次數": hits, "漲幅%": chg})

        if hits >= hit_thr and str(code) not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
            # ... (發送 Discord 邏輯保持相同) ...
            pass

    # 顯示
    st.info(f"🕒 更新完成 | 偵測標的: {len(current_detecting)} 檔")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
