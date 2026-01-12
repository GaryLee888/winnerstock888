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
# 1. 核心設定
# ==========================================
st.set_page_config(page_title="當沖雷達 - 終極修復版", layout="wide")

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
if "market_msg" not in st.session_state:
    st.session_state.market_msg = "等待數據..."

# ==========================================
# 2. 終極修復版：字體載入邏輯
# ==========================================
def get_fonts():
    base_path = os.path.dirname(__file__)
    # 建議改用 .ttf 檔案，避免 .ttc 在 Linux 下的 unknown format 報錯
    f_path = os.path.join(base_path, "font.ttf") 
    
    try:
        if os.path.exists(f_path):
            # 檢查檔案大小，若小於 100KB 可能是 Git LFS 指標檔，會導致報錯
            if os.path.getsize(f_path) < 102400:
                st.error(f"❌ 檔案大小異常 ({os.path.getsize(f_path)} bytes)，請確認是否為 LFS 連結而非真實字體檔。")
            
            return {
                'title': ImageFont.truetype(f_path, 44),
                'price': ImageFont.truetype(f_path, 70),
                'info': ImageFont.truetype(f_path, 26),
                'small': ImageFont.truetype(f_path, 18),
                'alert': ImageFont.truetype(f_path, 22)
            }
        else:
            st.error(f"❌ 找不到字體檔：{f_path}")
            default = ImageFont.load_default()
            return {k: default for k in ['title', 'price', 'info', 'small', 'alert']}
    except Exception as e:
        st.error(f"❌ 字體載入失敗: {e}")
        default = ImageFont.load_default()
        return {k: default for k in ['title', 'price', 'info', 'small', 'alert']}

# ==========================================
# 3. 核心功能 (原版邏輯完全不動)
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
    draw.text((40, 8), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=fonts['alert'])
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
# 4. UI 與 監控循環
# ==========================================
with st.sidebar:
    st.header("🎮 參數設定")
    scan_interval = st.slider("掃頻速度(秒)", 5, 60, 10)
    min_chg = st.number_input("漲幅下限%", value=2.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    vwap_dist_thr = st.number_input("均價乖離% <", value=3.5)
    st.divider()
    if st.button("🚀 測試發報 (檢查中文圖片)", use_container_width=True):
        test_item = {"code": "8888", "name": "測試成功", "price": 100.0, "chg": 5.0, "sl": 98.5, "tp": 102.5, "vwap_dist": 1.2, "cond": "🚀 系統測試", "hit": 3}
        send_winner_alert(test_item, is_test=True)
        st.toast("已送出測試訊息")
    if not st.session_state.running:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            st.session_state.running = True
            st.rerun()
    else:
        if st.button("■ 停止監控", type="secondary", use_container_width=True):
            st.session_state.running = False
            st.rerun()

if st.session_state.running:
    if "api" not in st.session_state:
        with st.spinner("API 初始化中..."):
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

    check_market_risk(st.session_state.api, st.session_state.m_contracts)
    m_color = "🔴" if not st.session_state.market_safe else "🟢"
    st.info(f"{m_color} 環境: {st.session_state.market_msg} | 正在監控 {len(st.session_state.all_contracts)} 檔")

    now = datetime.now()
    hm = now.hour * 100 + now.minute
    vol_base = 0.25 if hm < 930 else 0.55 if hm < 1130 else 0.85
    vol_threshold = vol_base * vol_weight
    
    data_list, cat_hits = [], {}
    snaps = st.session_state.api.snapshots(st.session_state.all_contracts)
    
    for s in snaps:
        code = s.code
        ref = st.session_state.ref_map.get(code, 0)
        if not code or s.close <= 0 or ref <= 0: continue
        
        chg = round(((s.close - ref) / ref * 100), 2)
        if not (min_chg <= chg <= 9.8): continue
        
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else s.close
        vwap_dist = round(((s.close - vwap) / vwap * 100), 2)
        vol_diff = s.total_volume - st.session_state.last_total_vol_map.get(code, s.total_volume)
        st.session_state.last_total_vol_map[code] = s.total_volume
        min_vol_pct = round((vol_diff / s.total_volume) * 100, 2) if s.total_volume > 0 else 0
        
        if not ((min_vol_pct >= 1.5) or (vol_diff >= 50)): continue
        
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        if ratio < vol_threshold: continue
        
        st.session_state.trigger_history[code] = [t for t in st.session_state.trigger_history.get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.trigger_history[code])
        cat = st.session_state.cat_map.get(code, "其他")
        cat_hits[cat] = cat_hits.get(cat, 0) + 1
        
        item = {"代碼": code, "名稱": st.session_state.name_map.get(code, ""), "產業": cat, "現價": s.close, "漲幅%": chg, "觸發": hits, "vwap_dist": vwap_dist, "sl": round(s.close * 0.985, 2), "tp": round(s.close * 1.025, 2)}
        data_list.append(item)
        
        if hits >= 10 and code not in st.session_state.reported_codes:
            if st.session_state.market_safe and vwap_dist <= vwap_dist_thr:
                item['cond'] = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
                send_winner_alert(item)
                st.session_state.reported_codes.add(code)
                st.toast(f"✅ 已發送：{code}")

    if data_list:
        st.dataframe(pd.DataFrame(data_list).sort_values("觸發", ascending=False), use_container_width=True)
    
    time.sleep(scan_interval)
    st.rerun()
