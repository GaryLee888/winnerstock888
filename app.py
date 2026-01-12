import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import os
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

# ==========================================
# 1. 初始化與 Secrets 讀取
# ==========================================
st.set_page_config(page_title="當沖雷達 - 終極修復移植版", layout="wide")

# 建議在 Streamlit Cloud 的 Settings -> Secrets 填寫以下資訊
API_KEY = st.secrets.get("API_KEY", "")
SECRET_KEY = st.secrets.get("SECRET_KEY", "")
DISCORD_WEBHOOK_URL = st.secrets.get("DISCORD_WEBHOOK_URL", "")

# 初始化 Session State (對應原 Tkinter 的成員變數)
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
# 2. 安全字體載入函式 (整合雙重檢查邏輯)
# ==========================================
def get_fonts():
    base_path = os.path.dirname(__file__)
    f_path = os.path.join(base_path, "msjhbd.ttc") 
    
    try:
        if os.path.exists(f_path):
            # 這是針對 Linux 環境讀取 TTC 的最安全寫法
            return {
                'title': ImageFont.truetype(f_path, 44, index=0),
                'price': ImageFont.truetype(f_path, 70, index=0),
                'info': ImageFont.truetype(f_path, 26, index=0),
                'small': ImageFont.truetype(f_path, 18, index=0),
                'alert': ImageFont.truetype(f_path, 22, index=0)
            }
        else:
            st.error(f"❌ 找不到字體檔：{f_path}")
            return {k: ImageFont.load_default() for k in ['title', 'price', 'info', 'small', 'alert']}
    except Exception as e:
        # 如果 index=0 還是報錯，可能是 Pillow 版本或 FreeType 限制
        # 我們嘗試不帶 index 的寫法作為最後掙扎
        try:
            return {
                'title': ImageFont.truetype(f_path, 44),
                'price': ImageFont.truetype(f_path, 70),
                'info': ImageFont.truetype(f_path, 26),
                'small': ImageFont.truetype(f_path, 18),
                'alert': ImageFont.truetype(f_path, 22)
            }
        except:
            st.error(f"❌ 字體完全不相容: {e}。Discord 圖片將無中文。")
            return {k: ImageFont.load_default() for k in ['title', 'price', 'info', 'small', 'alert']}

# ==========================================
# 3. 核心運算函式 (原版邏輯完整移植)
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

def send_winner_alert(item, is_test=False):
    fonts = get_fonts()
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 45], fill=(255, 215, 0))
    
    draw.text((40, 8), "🚀 財神降臨！發揮電報 💰💰💰", fill=(0, 0, 0), font=fonts['alert'])
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=fonts['title'])
    draw.text((40, 130), f"{item['price']}", fill=accent, font=fonts['price'])
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=fonts['info'])
    draw.text((40, 240), f"目標停利：{item['tp']:.2f}", fill=(255, 60, 60), font=fonts['info'])
    draw.text((310, 240), f"建議停損：{item['sl']:.2f}", fill=(0, 200, 0), font=fonts['info'])
    draw.text((40, 290), f"均價乖離：{item['vwap_dist']}%", fill=(0, 255, 255), font=fonts['small'])
    
    draw.rectangle([0, 350, 600, 400], fill=(30, 31, 35))
    draw.text((40, 362), f"訊號: {item['cond']} | 偵測: {item['hit']}次", fill=(255, 215, 0), font=fonts['small'])
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    header = "🧪 測試" if is_test else "🚀 發財電報"
    content = f"{header}！🔥 **{item['code']} {item['name']}**"
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass
    finally: buf.close()

# ==========================================
# 4. Streamlit UI 介面
# ==========================================
with st.sidebar:
    st.header("⚙️ 核心監控參數")
    scan_interval = st.slider("掃頻速度(秒)", 5, 60, 10)
    min_chg = st.number_input("漲幅下限%", value=2.5)
    prev_vol_min = st.number_input("昨日交易量 >", value=3000)
    momentum_thr = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    back_limit = st.number_input("回撤限制%", value=1.2)
    vol_now_min = st.number_input("成交張數 >", value=1000)
    vwap_dist_thr = st.number_input("均價乖離% <", value=3.5)

    st.divider()
    if st.button("🚀 測試發報 (檢查中文圖片)", use_container_width=True):
        test_item = {"code": "8888", "name": "字體測試成功", "price": 100.0, "chg": 5.0, "sl": 98.5, "tp": 102.5, "vwap_dist": 1.2, "cond": "🚀 系統測試", "hit": 3}
        send_winner_alert(test_item, is_test=True)
        st.toast("測試訊號已送出，請檢查 Discord")

    if not st.session_state.running:
        if st.button("▶ 啟動雷達監控", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止監控", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

# ==========================================
# 5. 主執行邏輯 (與原 Tkinter 掃描流程完全一致)
# ==========================================
if st.session_state.running:
    # API 初始化
    if "api" not in st.session_state:
        with st.spinner("Shioaji API 登入中..."):
            api = sj.Shioaji()
            api.login(API_KEY, SECRET_KEY)
            # 完整載入股票合約
            raw = [c for m in [api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
            st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
            st.session_state.name_map = {c.code: c.name for c in raw}
            st.session_state.cat_map = {c.code: c.category for c in raw}
            st.session_state.all_contracts = [c for c in raw if c.code in st.session_state.ref_map]
            try:
                st.session_state.m_contracts = [api.Contracts.Indices.TSE["001"], api.Contracts.Indices.OTC["OTC"]]
            except:
                st.session_state.m_contracts = [api.Contracts.Stocks.TSE["001"], api.Contracts.Stocks.OTC["OTC"]]
            st.session_state.api = api

    # 大盤風險評估
    check_market_risk(st.session_state.api, st.session_state.m_contracts)
    m_color = "🔴" if not st.session_state.market_safe else "🟢"
    st.info(f"{m_color} 環境: {st.session_state.market_msg} | 正在掃描精選 {len(st.session_state.all_contracts)} 檔...")

    now = datetime.now()
    hm = now.hour * 100 + now.minute
    # 原版動態量能邏輯
    vol_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
    vol_threshold = vol_base * vol_weight
    
    data_list, cat_hits = [], {}
    # 執行 Snapshot 獲取現價數據
    snaps = st.session_state.api.snapshots(st.session_state.all_contracts)
    
    for s in snaps:
        code = s.code
        ref = st.session_state.ref_map.get(code, 0)
        # 基礎過濾條件 (成交張數、價格、參考價)
        if not code or s.close <= 0 or ref <= 0 or s.total_volume < vol_now_min: continue
        
        # 篩選條件 1: 漲幅限制
        chg = round(((s.close - ref) / ref * 100), 2)
        if not (min_chg <= chg <= 9.8): continue
        
        # 篩選條件 2: 1分動能與瞬間爆量
        vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
        st.session_state.last_total_vol_map[code] = s.total_volume
        min_vol_pct = round((vol_diff / s.total_volume) * 100, 2) if s.total_volume > 0 else 0
        
        # 動能條件 (百分比達標 OR 瞬間 50 張)
        momentum_ok = (min_vol_pct >= momentum_thr) or (vol_diff >= 50)
        if not momentum_ok: continue
        
        # 篩選條件 3: 量增倍率 (對比昨日)
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        if ratio < vol_threshold: continue
        
        # 篩選條件 4: 回撤限制 (避免追高在高點下殺)
        daily_high = s.high if s.high > 0 else s.close
        if ((daily_high - s.close) / daily_high * 100) > back_limit: continue
        
        # 統計資訊 (均價距離、觸發次數、族群)
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
        vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
        
        st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.trigger_history[code])
        cat = st.session_state.cat_map.get(code, "其他")
        cat_hits[cat] = cat_hits.get(cat, 0) + 1
        
        item = {
            "代碼": code, "名稱": st.session_state.name_map.get(code, ""), "產業": cat,
            "現價": s.close, "漲幅%": chg, "觸發": hits, "均價距離": vwap_dist,
            "sl": round(s.close * 0.985, 2), "tp": round(s.close * 1.025, 2),
            "1分動能": min_vol_pct, "量增": ratio
        }
        data_list.append(item)
        
        # 發報判斷 (次數 >= 10 且符合大盤安全與均價距離)
        if hits >= 10 and code not in st.session_state.reported_codes:
            if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                item['cond'] = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
                item['vwap_dist'] = vwap_dist # 傳給卡片
                send_winner_alert(item)
                st.session_state.reported_codes.add(code)
                st.toast(f"✅ 通報成功: {code} {item['名稱']}")

    # 顯示掃描結果表格
    if data_list:
        df_display = pd.DataFrame(data_list).sort_values("觸發", ascending=False)
        st.dataframe(df_display, use_container_width=True, height=600)
    
    # 控制掃描頻率並自動重新運行
    time.sleep(scan_interval)
    st.rerun()

else:
    st.warning("👈 雷達監控已停止，請在左側側邊欄點擊「啟動雷達監控」開始工作。")
