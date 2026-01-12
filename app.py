import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# 1. 核心初始化 (完全移植)
# ==========================================
st.set_page_config(page_title="當沖雷達 - 終極移植版", layout="wide")

API_KEY = st.secrets.get("API_KEY", "")
SECRET_KEY = st.secrets.get("SECRET_KEY", "")
DISCORD_WEBHOOK_URL = st.secrets.get("DISCORD_WEBHOOK_URL", "")

# 初始化 Session State
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
if "market_msg" not in st.session_state:
    st.session_state.market_msg = "等待數據..."

# ==========================================
# 2. Discord 發送邏輯 (改用純文字表格，避開字體報錯)
# ==========================================
def send_winner_alert(item, is_test=False):
    header = "🧪 測試發報" if is_test else "🚀 財神降臨！發財電報"
    
    # 建立 Discord 純文字 Embed 格式
    content = f"### {header} 💰💰💰\n"
    content += f"🔥 **{item['code']} {item['name']}** ({item['cat']}) 爆發中！\n"
    content += f"```py\n"
    content += f"現價: {item['price']:<10} 漲幅: {item['chg']}%\n"
    content += f"目標停利: {item['tp']:<10} 建議停損: {item['sl']}\n"
    content += f"均價乖離: {item['vwap_dist']}%      1分動能: {item['min_v']}%\n"
    content += f"量增倍率: {item['ratio']}x       偵測次數: {item['hit']}次\n"
    content += f"訊號條件: {item['cond']}\n"
    content += "```"

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
    except:
        pass

# ==========================================
# 3. 大盤風險檢查 (移植原版 check_market_risk)
# ==========================================
def check_market_risk(api, market_contracts):
    try:
        snaps = api.snapshots(market_contracts)
        now = datetime.now()
        danger_detected = False
        status_text = []
        for s in snaps:
            if s.close <= 0: continue
            code_name = "加權" if s.code == "001" else "櫃買"
            st.session_state.market_history[s.code] = [(t, p) for t, p in st.session_state.market_history[s.code] if t > now - timedelta(minutes=5)]
            st.session_state.market_history[s.code].append((now, s.close))
            past_data = [p for t, p in st.session_state.market_history[s.code] if t < now - timedelta(minutes=2)]
            if past_data:
                ref_p = past_data[-1]
                diff_pct = (s.close - ref_p) / ref_p * 100
                if diff_pct < -0.15: 
                    danger_detected = True
                    status_text.append(f"{code_name}急殺({diff_pct:.2f}%)")
                else: status_text.append(f"{code_name}穩定")
        st.session_state.market_safe = not danger_detected
        if status_text: st.session_state.market_msg = " | ".join(status_text)
    except: pass

# ==========================================
# 4. Streamlit UI
# ==========================================
with st.sidebar:
    st.header("⚙️ 核心監控參數")
    scan_interval = st.slider("掃頻速度(秒)", 5, 60, 10)
    min_chg = st.number_input("漲幅下限%", value=2.5)
    momentum_thr = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    back_limit = st.number_input("回撤限制%", value=1.2)
    vwap_dist_thr = st.number_input("均價乖離% <", value=3.5)

    st.divider()
    if st.button("🚀 測試 Discord 通報", use_container_width=True):
        test_item = {"code": "8888", "name": "終極測試", "cat": "系統測試", "price": 100.0, "chg": 5.0, "sl": 98.5, "tp": 105.0, "vwap_dist": 1.2, "cond": "✅ 移植成功", "hit": 3, "min_v": 2.5, "ratio": 1.8}
        send_winner_alert(test_item, is_test=True)
        st.toast("測試訊息已送出")

    if not st.session_state.running:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 5. 主執行循環 (完整移植核心邏輯)
# ==========================================
if st.session_state.running:
    if "api" not in st.session_state:
        with st.spinner("API 登入中..."):
            api = sj.Shioaji()
            api.login(API_KEY, SECRET_KEY)
            raw = [c for m in [api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
            st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
            st.session_state.name_map = {c.code: c.name for c in raw}
            st.session_state.cat_map = {c.code: c.category for c in raw}
            st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
            try: st.session_state.m_contracts = [api.Contracts.Indices.TSE["001"], api.Contracts.Indices.OTC["OTC"]]
            except: st.session_state.m_contracts = [api.Contracts.Stocks.TSE["001"], api.Contracts.Stocks.OTC["OTC"]]
            st.session_state.api = api

    # 執行大盤風險檢查
    check_market_risk(st.session_state.api, st.session_state.m_contracts)
    m_color = "🔴" if not st.session_state.market_safe else "🟢"
    st.info(f"{m_color} 環境: {st.session_state.market_msg} | 正在掃描 {len(st.session_state.all_contracts)} 檔標的")

    now = datetime.now()
    hm = now.hour * 100 + now.minute
    # 移植原版動態量能基準
    vol_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
    vol_threshold = vol_base * vol_weight
    
    data_list, cat_hits = [], {}
    snaps = st.session_state.api.snapshots(st.session_state.all_contracts[:1500]) # 限制前1500檔確保效能
    
    for s in snaps:
        code = s.code; ref = st.session_state.ref_map.get(code, 0)
        if not code or s.close <= 0 or ref <= 0: continue
        
        # 漲幅過濾
        chg = round(((s.close - ref) / ref * 100), 2)
        if not (min_chg <= chg <= 9.8): continue
        
        # 動能計算
        vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
        st.session_state.last_total_vol_map[code] = s.total_volume
        min_vol_pct = round((vol_diff / s.total_volume) * 100, 2) if s.total_volume > 0 else 0
        
        # 1分動能門檻 (移植: 門檻達標 或 瞬間50張)
        if not ((min_vol_pct >= momentum_thr) or (vol_diff >= 50)): continue
        
        # 量增倍率過濾
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        if ratio < vol_threshold: continue
        
        # 回撤限制
        daily_high = s.high if s.high > 0 else s.close
        if ((daily_high - s.close) / daily_high * 100) > back_limit: continue
        
        # 統計與 Hits 次數
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
        vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
        
        st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.trigger_history[code])
        cat = st.session_state.cat_map.get(code, "其他")
        cat_hits[cat] = cat_hits.get(cat, 0) + 1
        
        item = {"code": code, "name": st.session_state.name_map.get(code, ""), "cat": cat, "price": s.close, "chg": chg, "hit": hits, "vwap_dist": vwap_dist, "sl": round(s.close * 0.985, 2), "tp": round(s.close * 1.025, 2), "min_v": min_vol_pct, "ratio": ratio}
        data_list.append(item)
        
        # 發報判斷 (Hits >= 10 且符合安全環境)
        if hits >= 10 and code not in st.session_state.reported_codes:
            if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                item['cond'] = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
                send_winner_alert(item)
                st.session_state.reported_codes.add(code)
                st.toast(f"✅ 通報：{code}")

    if data_list:
        st.dataframe(pd.DataFrame(data_list).sort_values("hit", ascending=False), use_container_width=True)
    
    time.sleep(scan_interval)
    st.rerun()
