import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# 1. 強力緩存連線 (含自動修復功能)
# ==========================================
@st.cache_resource
def get_shioaji_api(api_key, secret_key):
    api = sj.Shioaji()
    api.login(api_key, secret_key)
    return api

def init_states():
    defaults = {
        "running": False, "reported_codes": set(), "last_total_vol_map": {},
        "trigger_history": {}, "market_history": {"001": [], "OTC": []},
        "market_safe": True, "all_contracts": [], "ref_map": {}, "name_map": {}
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

# ==========================================
# 2. Discord 通報 (極簡對齊)
# ==========================================
def send_winner_alert(item, url):
    msg = f"### 🚀 財神降臨！發財電報\n🔥 **{item['code']} {item['name']}**\n"
    msg += f"```yaml\n"
    msg += f"{'現價':<6}: {item['price']}\n"
    msg += f"{'漲幅':<6}: {item['chg']}%\n"
    msg += f"{'停利價':<5}: {item['tp']}\n"
    msg += f"{'停損價':<5}: {item['sl']}\n"
    msg += f"{'偵測次數':<4}: {item['hit']} 次\n"
    msg += "```"
    try: requests.post(url, json={"content": msg}, timeout=5)
    except: pass

# ==========================================
# 3. 主介面
# ==========================================
st.set_page_config(page_title="當沖雷達-終極版", layout="wide")
init_states()

with st.sidebar:
    st.header("⚙️ 核心監控參數")
    K1 = st.text_input("API KEY", value=st.secrets.get("API_KEY", ""), type="password")
    K2 = st.text_input("SECRET KEY", value=st.secrets.get("SECRET_KEY", ""), type="password")
    URL = st.text_input("WEBHOOK", value=st.secrets.get("DISCORD_WEBHOOK_URL", ""))
    
    scan_int = st.slider("秒數", 5, 60, 10)
    min_c = st.number_input("漲幅下限%", 2.5)
    v_prev = st.number_input("昨日量 >", 3000)
    v_now = st.number_input("盤中量 >", 1000)
    m_thr = st.number_input("1分動能% >", 1.5)
    w_vol = st.number_input("動態量權重", 1.0)
    b_lim = st.number_input("回撤限制%", 1.2)
    dist_thr = st.number_input("乖離限制%", 3.5)

    if not st.session_state.running:
        if st.button("▶ 啟動", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 4. 監控邏輯
# ==========================================
if st.session_state.running:
    try:
        api = get_shioaji_api(K1, K2)
        
        # A. 合約下載保護
        if not st.session_state.all_contracts:
            with st.spinner("同步市場資訊..."):
                if not api.Contracts.Stocks:
                    st.error("API 尚未就緒，請檢查連線。")
                    st.stop()
                raw = [c for c in (list(api.Contracts.Stocks.TSE) + list(api.Contracts.Stocks.OTC)) if len(c.code) == 4]
                st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
                st.session_state.name_map = {c.code: c.name for c in raw}
                st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
                st.session_state.m_contracts = [api.Contracts.Indices.TSE["001"], api.Contracts.Indices.OTC["OTC"]]

        # B. 大盤監控 (加入數據有效性判斷)
        try:
            m_snaps = api.snapshots(st.session_state.m_contracts)
            now = datetime.now()
            danger = False
            for s in m_snaps:
                if s.close <= 100: continue # 避開無效數據
                hist = st.session_state.market_history[s.code]
                st.session_state.market_history[s.code] = [(t, p) for t, p in hist if t > now - timedelta(minutes=5)]
                st.session_state.market_history[s.code].append((now, s.close))
                past = [p for t, p in st.session_state.market_history[s.code] if t < now - timedelta(minutes=2)]
                if past and (s.close - past[-1]) / past[-1] * 100 < -0.15: danger = True
            st.session_state.market_safe = not danger
        except: st.session_state.market_safe = True # API指數抖動時保守對待

        # C. 量能基準分析
        hm = now.hour * 100 + now.minute
        v_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
        thr = v_base * w_vol

        # D. 市場掃描
        res_list = []
        conts = st.session_state.all_contracts
        p_bar = st.progress(0, text="動能掃描中...")
        
        batch = 500
        for i in range(0, len(conts), batch):
            p_bar.progress(min((i+batch)/len(conts), 1.0))
            snaps = api.snapshots(conts[i:i+batch])
            
            for s in snaps:
                code = s.code; ref = st.session_state.ref_map.get(code, 0)
                if not code or s.close <= 0 or ref <= 0: continue
                
                # --- 核心過濾 (基準總量分析) ---
                if s.yesterday_volume < v_prev or s.total_volume < v_now: continue
                ratio = s.total_volume / s.yesterday_volume
                if ratio < thr: continue
                
                chg = round(((s.close - ref) / ref * 100), 2)
                if not (min_c <= chg <= 9.8): continue
                
                # --- 1分動能 ---
                last_v = st.session_state.last_total_vol_map.get(code, s.total_volume)
                v_diff = s.total_volume - last_v
                st.session_state.last_total_vol_map[code] = s.total_volume
                
                if v_diff <= 0: continue 
                v_pct = (v_diff / s.total_volume) * 100
                if not (v_pct >= m_thr or v_diff >= 50): continue
                
                # --- 回撤與乖離 ---
                if s.high > 0 and ((s.high - s.close) / s.high * 100) > b_lim: continue
                vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
                dist = ((s.close - vwap) / vwap * 100)
                
                # --- Hits 紀錄 ---
                st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
                h = len(st.session_state.trigger_history[code])
                
                item = {"code":code, "name":st.session_state.name_map.get(code,""), "price":s.close, "chg":chg, "hit":h, "tp":round(s.close*1.025,2), "sl":round(s.close*0.985,2), "dist":dist}
                res_list.append(item)
                
                if h >= 10 and code not in st.session_state.reported_codes:
                    if st.session_state.market_safe and dist <= dist_thr:
                        send_winner_alert(item, URL)
                        st.session_state.reported_codes.add(code)

        p_bar.empty()
        if res_list:
            st.dataframe(pd.DataFrame(res_list).sort_values("hit", ascending=False), use_container_width=True)
        
        time.sleep(scan_int)
        st.rerun()

    except Exception as e:
        # 如果是連線問題，清除緩存強制下一次重登
        if "Disconnected" in str(e) or "NoneType" in str(e):
            st.cache_resource.clear()
        st.error(f"⚠️ 運行抖動，5秒後自動嘗試恢復: {e}")
        time.sleep(5)
        st.rerun()
