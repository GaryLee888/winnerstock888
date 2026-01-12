import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import os
from datetime import datetime, timedelta

# ==========================================
# 1. 核心初始化
# ==========================================
st.set_page_config(page_title="當沖雷達 - 完整通報版", layout="wide")

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
# 2. Discord 發送邏輯 (代號、名稱與數值完全對齊)
# ==========================================
def send_winner_alert(item, is_test=False):
    header = "🧪 測試發報" if is_test else "🚀 財神降臨！發財電報"
    
    # 使用 yaml 格式與固定長度對齊
    content = f"### {header}\n"
    content += f"```yaml\n"
    content += f"{'股票代號':<4}: {item['code']}\n"
    content += f"{'股票名稱':<4}: {item['name']}\n"
    content += f"{'現價':<6}: {item['price']}\n"
    content += f"{'漲幅':<6}: {item['chg']}%\n"
    content += f"{'停利價':<5}: {item['tp']}\n"
    content += f"{'停損價':<5}: {item['sl']}\n"
    content += f"{'偵測次數':<4}: {item['hit']} 次\n"
    content += f"```"
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
    except:
        pass

# ==========================================
# 3. 大盤風險檢查邏輯
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
# 4. Streamlit UI 與測試按鈕
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
    if st.button("🚀 測試 Discord 通報內容", use_container_width=True):
        test_item = {
            "code": "2330", "name": "台積電", "price": 1000.0, "chg": 5.2, 
            "tp": 1050.0, "sl": 985.0, "hit": 12
        }
        send_winner_alert(test_item, is_test=True)
        st.toast("測試訊息已送出，請檢查 Discord 排版")

    if not st.session_state.running:
        if st.button("▶ 啟動雷達監控", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止雷達監控", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 5. 主循環 (包含進度條與量能基準分析)
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
    
    # 掃描進度條
    progress_bar = st.progress(0, text="同步市場 Snapshot 中...")
    
    data_list = []
    batch_size = 400
    contracts = st.session_state.all_contracts
    
    for i in range(0, len(contracts), batch_size):
        batch = contracts[i : i + batch_size]
        progress_bar.progress((i + batch_size) / len(contracts) if (i + batch_size) < len(contracts) else 1.0, 
                              text=f"正在分析全市場量價動能 ({i}/{len(contracts)})...")
        
        snaps = st.session_state.api.snapshots(batch)
        
        for s in snaps:
            code = s.code; ref = st.session_state.ref_map.get(code, 0)
            if not code or s.close <= 0 or ref <= 0 or s.yesterday_volume <= 0: continue
            
            # 基準總量分析
            if s.yesterday_volume < prev_vol_min: continue
            if s.total_volume < vol_now_min: continue
            ratio = round(s.total_volume / s.yesterday_volume, 2)
            if ratio < target_threshold: continue
            
            # 漲幅初選
            chg = round(((s.close - ref) / ref * 100), 2)
            if not (min_chg <= chg <= 9.8): continue
            
            # 1分動能
            vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
            st.session_state.last_total_vol_map[code] = s.total_volume
            min_vol_pct = round((vol_diff / s.total_volume) * 100, 2) if s.total_volume > 0 else 0
            if not ((min_vol_pct >= momentum_thr) or (vol_diff >= 50)): continue
            
            # 回撤限制
            daily_high = s.high if s.high > 0 else s.close
            if ((daily_high - s.close) / daily_high * 100) > back_limit: continue
            
            # 均價乖離
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
            vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
            
            # 觸發次數統計
            st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
            hits = len(st.session_state.trigger_history[code])
            
            item = {
                "code": code, "name": st.session_state.name_map.get(code, ""), 
                "price": s.close, "chg": chg, "hit": hits,
                "sl": round(s.close * 0.985, 2), "tp": round(s.close * 1.025, 2),
                "vwap_dist": vwap_dist
            }
            data_list.append(item)
            
            if hits >= 10 and code not in st.session_state.reported_codes:
                if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                    send_winner_alert(item)
                    st.session_state.reported_codes.add(code)
    
    progress_bar.empty()
    
    if data_list:
        st.dataframe(pd.DataFrame(data_list).sort_values("hit", ascending=False), use_container_width=True)
    
    time.sleep(scan_interval)
    st.rerun()
