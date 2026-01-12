import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# 1. 資源與連線緩存
# ==========================================
@st.cache_resource
def get_shioaji_api(api_key, secret_key):
    """建立 API 連線並登入"""
    api = sj.Shioaji()
    api.login(api_key, secret_key)
    return api

# ==========================================
# 2. 初始化 Session State
# ==========================================
def init_states():
    defaults = {
        "running": False,
        "reported_codes": set(),
        "last_total_vol_map": {},
        "trigger_history": {},
        "market_history": {"001": [], "OTC": []},
        "market_safe": True,
        "ref_map": {},
        "name_map": {},
        "all_contracts": [],
        "m_contracts": []
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

# ==========================================
# 3. 通報功能 (文字對齊)
# ==========================================
def send_winner_alert(item, webhook_url, is_test=False):
    header = "🧪 測試發報" if is_test else "🚀 財神降臨！發財電報"
    content = f"### {header}\n"
    content += f"```yaml\n"
    content += f"{'股票代號':<4}: {item['code']}\n"
    content += f"{'股票名稱':<4}: {item['name']}\n"
    content += f"{'現價':<6}: {item['price']}\n"
    content += f"{'漲幅':<6}: {item['chg']}%\n"
    content += f"{'停利價':<5}: {item['tp']}\n"
    content += f"{'停損價':<5}: {item['sl']}\n"
    content += f"{'偵測次數':<4}: {item['hit']} 次\n"
    content += "```"
    try:
        requests.post(webhook_url, json={"content": content}, timeout=5)
    except: pass

# ==========================================
# 4. 主 UI 與 側邊欄
# ==========================================
st.set_page_config(page_title="當沖雷達 - 終極修復版", layout="wide")
init_states()

with st.sidebar:
    st.header("⚙️ 核心監控參數")
    S_API_KEY = st.text_input("API KEY", value=st.secrets.get("API_KEY", ""), type="password")
    S_SECRET_KEY = st.text_input("SECRET KEY", value=st.secrets.get("SECRET_KEY", ""), type="password")
    S_WEBHOOK = st.text_input("WEBHOOK URL", value=st.secrets.get("DISCORD_WEBHOOK_URL", ""))

    scan_interval = st.slider("掃頻速度(秒)", 5, 60, 10)
    min_chg = st.number_input("1. 漲幅下限%", value=2.5)
    prev_vol_min = st.number_input("2. 昨日交易量 >", value=3000)
    vol_now_min = st.number_input("3. 盤中總張數 >", value=1000)
    momentum_thr = st.number_input("4. 1分動能% >", value=1.5)
    vol_weight = st.number_input("5. 動態量權重", value=1.0)
    back_limit = st.number_input("6. 回撤限制%", value=1.2)
    vwap_dist_thr = st.number_input("7. 均價乖離% <", value=3.5)

    if st.button("🚀 測試 Discord 通報"):
        send_winner_alert({"code":"2330","name":"台積電","price":1000,"chg":5.0,"tp":1025,"sl":985,"hit":10}, S_WEBHOOK, True)

    if not st.session_state.running:
        if st.button("▶ 啟動雷達", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止雷達", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 5. 主循環邏輯 (防止 NoneType 錯誤)
# ==========================================
if st.session_state.running:
    try:
        api = get_shioaji_api(S_API_KEY, S_SECRET_KEY)
        
        # --- 安全抓取合約邏輯 ---
        if not st.session_state.all_contracts:
            with st.spinner("正在安全下載全市場股票合約 (需時約 15-30 秒)..."):
                # 檢查 api.Contracts 是否為 None
                if api.Contracts.Stocks is None:
                    st.error("❌ API 合約載入失敗，請檢查 API Key 或網路連線。")
                    st.stop()
                
                # 合併上市與上櫃合約，並加入 None 檢查
                tse = list(api.Contracts.Stocks.TSE) if api.Contracts.Stocks.TSE else []
                otc = list(api.Contracts.Stocks.OTC) if api.Contracts.Stocks.OTC else []
                
                if not tse and not otc:
                    st.warning("⚠️ 尚未抓取到合約，系統將在 5 秒後重試...")
                    time.sleep(5)
                    st.rerun()

                raw = [c for c in (tse + otc) if len(c.code) == 4]
                st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
                st.session_state.name_map = {c.code: c.name for c in raw}
                st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
                
                # 指數合約初始化
                try:
                    st.session_state.m_contracts = [api.Contracts.Indices.TSE["001"], api.Contracts.Indices.OTC["OTC"]]
                except:
                    # 備案：若抓不到指數，抓取大型權值股作為大盤參考
                    st.session_state.m_contracts = [api.Contracts.Stocks.TSE["2330"], api.Contracts.Stocks.OTC["6488"]]

        # 大盤檢查
        snaps_m = api.snapshots(st.session_state.m_contracts)
        now = datetime.now()
        danger_flag = False
        for s in snaps_m:
            if s.close <= 0: continue
            st.session_state.market_history[s.code] = [(t, p) for t, p in st.session_state.market_history[s.code] if t > now - timedelta(minutes=5)]
            st.session_state.market_history[s.code].append((now, s.close))
            past = [p for t, p in st.session_state.market_history[s.code] if t < now - timedelta(minutes=2)]
            if past and (s.close - past[-1]) / past[-1] * 100 < -0.15: danger_flag = True
        st.session_state.market_safe = not danger_flag

        # 基準量判斷
        hm = now.hour * 100 + now.minute
        vol_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
        target_threshold = vol_base * vol_weight

        # 市場掃描進度
        data_list = []
        contracts = st.session_state.all_contracts
        progress_bar = st.progress(0, text=f"掃描中... {len(contracts)} 檔標的分析中")
        
        batch_size = 500
        for i in range(0, len(contracts), batch_size):
            batch = contracts[i : i + batch_size]
            progress_bar.progress(min((i + batch_size) / len(contracts), 1.0))
            
            snaps = api.snapshots(batch)
            for s in snaps:
                code = s.code; ref = st.session_state.ref_map.get(code, 0)
                if not code or s.close <= 0 or ref <= 0 or s.yesterday_volume <= 0: continue
                
                # 篩選 1 & 2: 昨量與盤中量
                if s.yesterday_volume < prev_vol_min or s.total_volume < vol_now_min: continue
                
                # 篩選 3: 基準總量比例
                ratio = s.total_volume / s.yesterday_volume
                if ratio < target_threshold: continue
                
                # 篩選 4: 漲幅
                chg = round(((s.close - ref) / ref * 100), 2)
                if not (min_chg <= chg <= 9.8): continue
                
                # 篩選 5: 1分動能
                vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
                st.session_state.last_total_vol_map[code] = s.total_volume
                
                if vol_diff <= 0: continue # 避開初始值
                
                min_vol_pct = (vol_diff / s.total_volume) * 100
                if not (min_vol_pct >= momentum_thr or vol_diff >= 50): continue
                
                # 篩選 6: 回撤
                if s.high > 0 and ((s.high - s.close) / s.high * 100) > back_limit: continue
                
                # 篩選 7: 均價乖離
                vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
                vwap_dist = ((s.close - vwap) / vwap * 100)
                
                # Hits 累計
                st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
                hits = len(st.session_state.trigger_history[code])
                
                item = {"code": code, "name": st.session_state.name_map.get(code, ""), "price": s.close, "chg": chg, "hit": hits, "tp": round(s.close * 1.025, 2), "sl": round(s.close * 0.985, 2), "vwap_dist": vwap_dist}
                data_list.append(item)
                
                if hits >= 10 and code not in st.session_state.reported_codes:
                    if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                        send_winner_alert(item, S_WEBHOOK)
                        st.session_state.reported_codes.add(code)

        progress_bar.empty()
        if data_list:
            st.dataframe(pd.DataFrame(data_list).sort_values("hit", ascending=False), use_container_width=True)
        
        time.sleep(scan_interval)
        st.rerun()

    except Exception as e:
        st.error(f"系統運行異常: {e}")
        # 若發生錯誤，暫停 5 秒後重試，避免無效循環
        time.sleep(5)
        st.rerun()
