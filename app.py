import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 強制台灣時區校正 (關鍵修正)
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

st.set_page_config(page_title="當沖雷達-終極修復版", layout="wide")

# ==========================================
# 2. Session State 初始化 (加入備援機制)
# ==========================================
if 'state' not in st.session_state:
    history_df = load_local_history()
    st.session_state.state = {
        'running': False,
        'history': history_df.to_dict('records'),
        'reported_codes': set(history_df['code'].tolist()) if not history_df.empty else set(),
        'last_total_vol': {}, 
        'market_safe': True,
        'market_msg': "穩定",
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 自動啟動與合約預載
# ==========================================
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(st.secrets["SHIOAJI_API_KEY"], st.secrets["SHIOAJI_SECRET_KEY"])
        # 抓取所有合約並預存必要數值
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.y_vol_map = {c.code: (c.yesterday_volume if c.yesterday_volume else 1) for c in raw}
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.state['running'] = True
        st.rerun()
    except Exception as e:
        st.error(f"登入失敗: {e}"); time.sleep(10); st.rerun()

# ==========================================
# 4. 監控主邏輯 (修正時區與門檻)
# ==========================================
if st.session_state.state['running']:
    now_tw = get_now()
    hm = now_tw.hour * 100 + now_tw.minute # 這是正確的台灣時間
    
    # 側邊欄參數
    with st.sidebar:
        st.header("🎯 門檻微調")
        chg_min = st.number_input("漲幅下限%", value=2.5)
        vol_total_min = st.number_input("基準總量>", value=3000)
        # 如果還是篩不到，增加一個「測試模式」按鈕來放寬門檻
        test_mode = st.checkbox("放寬模式 (測試用)", value=False)
        if test_mode:
            chg_min = 0.5
            vol_total_min = 500

    # 時間動態門檻 (強制校正)
    if hm < 1000: vol_base, hit_thr = 0.55, 15
    elif hm < 1100: vol_base, hit_thr = 0.40, 12
    elif hm < 1230: vol_base, hit_thr = 0.25, 8
    else: vol_base, hit_thr = 0.20, 6 # 目前 12:50 走這條

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
        
        # --- 篩選閘門 ---
        if chg < chg_min: continue
        if s.total_volume < vol_total_min: continue
        
        # 計算動能
        last_vol = st.session_state.state['last_total_vol'].get(code)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        if last_vol is None: continue 
        
        vol_diff = s.total_volume - last_vol
        ratio = s.total_volume / y_vol
        
        # 判定是否符合動能 (桌面版核心)
        momentum_ok = (vol_diff >= 50) or ((vol_diff / s.total_volume * 100) >= 1.5)
        if not momentum_ok: continue
        if ratio < vol_base: continue

        # 紀錄觸發
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now_tw - timedelta(minutes=10)] + [now_tw]
        hits = len(st.session_state.state['trigger_history'][code])
        
        current_detecting.append({
            "代碼": code, "股名": st.session_state.name_map.get(code), 
            "現價": s.close, "次數": hits, "漲幅%": chg, "量差": vol_diff
        })

    # 顯示結果
    st.subheader(f"📊 即時偵測看板 (台灣時間 {now_tw.strftime('%H:%M:%S')})")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)
    else:
        st.warning("⚠️ 目前無標的符合門檻。請確認：1. 漲幅 > 2.5%  2. 總張數 > 3000  3. 每 10 秒有爆發 50 張。")
        st.write(f"當前系統時間判斷: {hm} (應對應台灣時分)")

    time.sleep(10)
    st.rerun()
