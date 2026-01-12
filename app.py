import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# 1. 核心初始化
# ==========================================
st.set_page_config(page_title="當沖雷達 - 進度監控版", layout="wide")

API_KEY = st.secrets.get("API_KEY", "")
SECRET_KEY = st.secrets.get("SECRET_KEY", "")
DISCORD_WEBHOOK_URL = st.secrets.get("DISCORD_WEBHOOK_URL", "")

if "running" not in st.session_state:
    st.session_state.running = False
if "reported_codes" not in st.session_state:
    st.session_state.reported_codes = set()
if "last_total_vol_map" not in st.session_state:
    st.session_state.last_total_vol_map = {}
if "trigger_history" not in st.session_state:
    st.session_state.trigger_history = {}
if "market_history" not in st.session_state:
    st.session_state.market_history = {"001": [], "OTC": []}
if "market_safe" not in st.session_state:
    st.session_state.market_safe = True

# ==========================================
# 2. Discord 發送邏輯 (內容對齊、基準分析數據)
# ==========================================
def send_winner_alert(item, is_test=False):
    header = "🧪 測試通報" if is_test else "🚀 財神降臨！發財電報"
    content = f"### {header}\n"
    content += f"🔥 **{item['code']} {item['name']}**\n"
    content += f"```yaml\n"
    content += f"{'現價':<6}: {item['price']}\n"
    content += f"{'漲幅':<6}: {item['chg']}%\n"
    content += f"{'量增倍率':<4}: {item['ratio']}x (基準:{item['v_base']})\n"
    content += f"{'停利價':<5}: {item['tp']}\n"
    content += f"{'停損價':<5}: {item['sl']}\n"
    content += f"{'偵測次數':<4}: {item['hit']} 次\n"
    content += f"```"
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
    except:
        pass

# ==========================================
# 3. 大盤風險檢查
# ==========================================
def check_market_risk(api, market_contracts):
    try:
        snaps = api.snapshots(market_contracts)
        now = datetime.now()
        danger_detected = False
        for s in snaps:
            if s.close <= 0: continue
            st.session_state.market_history[s.code] = [(t, p) for t, p in st.session_state.market_history[s.code] if t > now - timedelta(minutes=5)]
            st.session_state.market_history[s.code].append((now, s.close))
            past_data = [p for t, p in st.session_state.market_history[s.code] if t < now - timedelta(minutes=2)]
            if past_data:
                ref_p = past_data[-1]
                if (s.close - ref_p) / ref_p * 100 < -0.15: danger_detected = True
        st.session_state.market_safe = not danger_detected
    except: pass

# ==========================================
# 4. Streamlit UI (加回測試按鈕)
# ==========================================
with st.sidebar:
    st.header("⚙️ 核心監控參數")
    scan_interval = st.slider("掃頻速度(秒)", 5, 60, 10)
    min_chg = st.number_input("1. 漲幅下限%", value=2.5)
    prev_vol_min = st.number_input("2. 昨日交易量 >", value=3000)
    vol_now_min = st.number_input("3. 盤中總張數 >", value=1000)
    momentum_thr = st.number_input("4. 1分動能% >", value=1.5)
    vol_weight = st.number_input("5. 動態量權重", value=1.0)
    back_limit = st.number_input("6. 回撤限制%", value=1.2)
    vwap_dist_thr = st.number_input("7. 均價乖離% <", value=3.5)

    st.divider()
    # 【加回測試發報按鈕】
    if st.button("🚀 測試 Discord 發報", use_container_width=True):
        test_item = {
            "code": "8888", "name": "測試股", "price": 100.0, "chg": 5.0, 
            "tp": 105.0, "sl": 98.5, "hit": 10, "ratio": 1.5, "v_base": 0.55
        }
        send_winner_alert(test_item, is_test=True)
        st.toast("測試發報已送出！")

    if not st.session_state.running:
        if st.button("▶ 啟動雷達", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止雷達", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 5. 主循環 (加入進度條與基準總量判斷)
# ==========================================
if st.session_state.running:
    if "api" not in st.session_state:
        api = sj.Shioaji()
        api.login(API_KEY, SECRET_KEY)
        raw = [c for m in [api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
        try: st.session_state.m_contracts = [api.Contracts.Indices.TSE["001"], api.Contracts.Indices.OTC["OTC"]]
        except: st.session_state.m_contracts = [api.Contracts.Stocks.TSE["001"], api.Contracts.Stocks.OTC["OTC"]]
        st.session_state.api = api

    check_market_risk(st.session_state.api, st.session_state.m_contracts)
    
    now = datetime.now()
    hm = now.hour * 100 + now.minute
    vol_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
    target_threshold = round(vol_base * vol_weight, 2)
    
    # 【顯示進度條】
    progress_bar = st.progress(0, text="準備掃描全台股市場...")
    
    data_list = []
    # 這裡將標的分組掃描，模擬進度感
    batch_size = 500
    contracts = st.session_state.all_contracts
    
    for i in range(0, len(contracts), batch_size):
        batch = contracts[i : i + batch_size]
        # 更新進度條百分比
        progress = (i + batch_size) / len(contracts)
        progress_bar.progress(min(progress, 1.0), text=f"正在分析標的 {i} ~ {min(i+batch_size, len(contracts))} ...")
        
        snaps = st.session_state.api.snapshots(batch)
        
        for s in snaps:
            code = s.code; ref = st.session_state.ref_map.get(code, 0)
            if not code or s.close <= 0 or ref <= 0 or s.yesterday_volume <= 0: continue
            
            # 【移植基準總量分析判斷】
            # 1. 昨日量過濾
            if s.yesterday_volume < prev_vol_min: continue
            # 2. 盤中總張數過濾
            if s.total_volume < vol_now_min: continue
            # 3. 基準總量比值判斷 (動態門檻)
            ratio = round(s.total_volume / s.yesterday_volume, 2)
            if ratio < target_threshold: continue
            
            # 漲幅與動能
            chg = round(((s.close - ref) / ref * 100), 2)
            if not (min_chg <= chg <= 9.8): continue
            
            vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
            st.session_state.last_total_vol_map[code] = s.total_volume
            min_vol_pct = round((vol_diff / s.total_volume) * 100, 2) if s.total_volume > 0 else 0
            
            # 1分爆量強度判斷
            if not ((min_vol_pct >= momentum_thr) or (vol_diff >= 50)): continue
            
            # 回撤與乖離
            daily_high = s.high if s.high > 0 else s.close
            if ((daily_high - s.close) / daily_high * 100) > back_limit: continue
            
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
            vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
            
            st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
            hits = len(st.session_state.trigger_history[code])
            
            item = {
                "code": code, "name": st.session_state.name_map.get(code, ""), 
                "price": s.close, "chg": chg, "hit": hits, "ratio": ratio, 
                "v_base": target_threshold, "sl": round(s.close * 0.985, 2), 
                "tp": round(s.close * 1.025, 2), "vwap_dist": vwap_dist
            }
            data_list.append(item)
            
            if hits >= 10 and code not in st.session_state.reported_codes:
                if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                    send_winner_alert(item)
                    st.session_state.reported_codes.add(code)
    
    progress_bar.empty() # 掃描完後清除進度條
    
    if data_list:
        st.dataframe(pd.DataFrame(data_list).sort_values("hit", ascending=False), use_container_width=True)
    
    time.sleep(scan_interval)
    st.rerun()
