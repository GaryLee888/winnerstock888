import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 強制台灣時區校正
# ==========================================
TZ_TW = timezone(timedelta(hours=8))
def get_now():
    return datetime.now(TZ_TW)

REPORT_FILE = "report_history.csv"

def load_local_history():
    if os.path.exists(REPORT_FILE):
        try:
            df = pd.read_csv(REPORT_FILE)
            df['code'] = df['code'].astype(str)
            return df
        except: return pd.DataFrame()
    return pd.DataFrame()

st.set_page_config(page_title="當沖雷達-穩定版", layout="wide")

# ==========================================
# 2. Session State 初始化
# ==========================================
if 'state' not in st.session_state:
    history_df = load_local_history()
    st.session_state.state = {
        'running': False,
        'history': history_df.to_dict('records'),
        'reported_codes': set(history_df['code'].tolist()) if not history_df.empty else set(),
        'last_total_vol': {}, 
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 自動啟動與合約預載 (修復 yesterday_vol)
# ==========================================
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(st.secrets["SHIOAJI_API_KEY"], st.secrets["SHIOAJI_SECRET_KEY"])
        # 抓取合約
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        
        # 修正處：屬性名稱應為 yesterday_vol 而非 yesterday_volume
        st.session_state.y_vol_map = {c.code: (c.yesterday_vol if hasattr(c, 'yesterday_vol') else 1) for c in raw}
        
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.state['running'] = True
        st.rerun()
    except Exception as e:
        st.error(f"登入失敗: {e}"); time.sleep(10); st.rerun()

# ==========================================
# 4. 監控主邏輯
# ==========================================
if st.session_state.state['running']:
    now_tw = get_now()
    hm = now_tw.hour * 100 + now_tw.minute
    
    with st.sidebar:
        st.header("🎯 門檻微調")
        chg_min = st.number_input("漲幅下限%", value=2.5)
        vol_total_min = st.number_input("基準總量>", value=3000)

    # 門檻設定
    if hm < 1000: vol_base, hit_thr = 0.55, 15
    elif hm < 1100: vol_base, hit_thr = 0.40, 12
    elif hm < 1230: vol_base, hit_thr = 0.25, 8
    else: vol_base, hit_thr = 0.20, 6

    # 批次抓取快照
    all_snaps = []
    targets = st.session_state.contracts
    for i in range(0, len(targets), 100):
        all_snaps.extend(st.session_state.api.snapshots(targets[i:i+100]))
    
    current_detecting = []
    for s in all_snaps:
        code = s.code
        ref = st.session_state.ref_map.get(code, 0)
        y_vol = st.session_state.y_vol_map.get(code, 1)
        
        if s.close <= 0 or ref <= 0: continue
        chg = round(((s.close - ref) / ref * 100), 2)
        
        # 門檻篩選
        if chg < chg_min or s.total_volume < vol_total_min: continue
        
        # 動能計算
        last_vol = st.session_state.state['last_total_vol'].get(code)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        if last_vol is None: continue 
        
        vol_diff = s.total_volume - last_vol
        momentum_ok = (vol_diff >= 50) or ((vol_diff / s.total_volume * 100) >= 1.5)
        
        if momentum_ok and (s.total_volume / y_vol) >= vol_base:
            st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now_tw - timedelta(minutes=10)] + [now_tw]
            hits = len(st.session_state.state['trigger_history'][code])
            
            current_detecting.append({
                "代碼": code, "股名": st.session_state.name_map.get(code), 
                "現價": s.close, "次數": hits, "漲幅%": chg, "量差": vol_diff
            })

    st.subheader(f"📊 即時看板 ({now_tw.strftime('%H:%M:%S')})")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)

    time.sleep(10)
    st.rerun()
