import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
import platform
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 核心配置與初始化
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入金鑰。")
    st.stop()

st.set_page_config(page_title="當沖雷達-雲端終極版", layout="wide")

if 'state' not in st.session_state:
    st.session_state.state = {
        'running': False,
        'history': [],
        'reported_codes': set(),
        'last_total_vol': {},
        'market_safe': True,
        'market_msg': "等待大盤數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 2. 工具函式
# ==========================================
def get_font(size):
    try:
        f_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        if platform.system() == "Windows": f_path = "msjhbd.ttc"
        return ImageFont.truetype(f_path, size)
    except: return ImageFont.load_default()

def send_winner_alert(item):
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 50], fill=(255, 215, 0))
    draw.text((40, 10), "🚀 財神降臨！發揮電報 💰💰💰", fill=(0, 0, 0), font=get_font(24))
    draw.text((40, 75), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 140), f"{item['price']}", fill=accent, font=get_font(75))
    draw.text((320, 170), f"{item['chg']}%", fill=accent, font=get_font(35))
    draw.text((40, 250), f"目標停利：{item['tp']:.2f} | 停損：{item['sl']:.2f}", fill=(255, 60, 60), font=get_font(24))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 觸發條件！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 3. UI 介面
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻週期(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5, step=0.1)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    if not st.session_state.state['running']:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            try:
                st.session_state.api.login(API_KEY, SECRET_KEY)
                raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
                st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
                st.session_state.name_map = {c.code: c.name for c in raw}
                st.session_state.cat_map = {c.code: c.category for c in raw}
                st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
                st.session_state.mkt_contracts = [st.session_state.api.Contracts.Indices.TSE["001"], st.session_state.api.Contracts.Indices.OTC["OTC"]]
                st.session_state.state['running'] = True
                st.rerun()
            except Exception as e: st.error(f"登入失敗: {e}")
    else:
        if st.button("■ 停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()

# ==========================================
# 4. 監控邏輯 (含進度條)
# ==========================================
if st.session_state.state['running']:
    now = datetime.now()
    
    # 大盤檢查
    try:
        m_snaps = st.session_state.api.snapshots(st.session_state.mkt_contracts)
        danger = False
        m_msgs = []
        for ms in m_snaps:
            if ms.close <= 0: continue
            name = "加權" if ms.code == "001" else "櫃買"
            st.session_state.state['market_history'][ms.code] = [(t, p) for t, p in st.session_state.state['market_history'][ms.code] if t > now - timedelta(minutes=5)]
            st.session_state.state['market_history'][ms.code].append((now, ms.close))
            past = [p for t, p in st.session_state.state['market_history'][ms.code] if t < now - timedelta(minutes=2)]
            if past:
                diff = (ms.close - past[-1]) / past[-1] * 100
                if diff < -0.15: danger = True; m_msgs.append(f"{name}急殺({diff:.2f}%)")
                else: m_msgs.append(f"{name}穩定")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs)
    except: st.session_state.state['market_safe'] = True

    # 狀態顯示
    st.info(f"🕒 最後更新: {now.strftime('%H:%M:%S')} | {'🔴 市場風險' if not st.session_state.state['market_safe'] else '🟢 市場安全'}: {st.session_state.state['market_msg']}")
    
    # --- 加入掃描進度條 ---
    targets = st.session_state.contracts[:500] 
    batch_size = 100
    total_batches = (len(targets) // batch_size) + (1 if len(targets) % batch_size > 0 else 0)
    
    progress_text = "🔎 正在掃描全市場標的數據..."
    my_bar = st.progress(0, text=progress_text)
    
    all_snaps = []
    for i in range(total_batches):
        batch = targets[i*batch_size : (i+1) * batch_size]
        all_snaps.extend(st.session_state.api.snapshots(batch))
        # 更新進度條
        percent_complete = (i + 1) / total_batches
        my_bar.progress(percent_complete, text=f"{progress_text} ({int(percent_complete*100)}%)")
        time.sleep(0.05) # 避免 API 請求過快
    
    # 清除進度條 (掃描完畢)
    my_bar.empty()

    # 篩選邏輯
    cat_hits = {}
    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0 or s.total_volume < vol_total_min: continue
        chg = round(((price - ref) / ref * 100), 2)
        if not (chg_min <= chg <= 9.8): continue
        
        # 動能計算
        vol_diff = 0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        
        if vol_diff >= 50 and vwap_dist <= vwap_gap_limit:
            st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
            hits = len(st.session_state.state['trigger_history'][code])
            cat = st.session_state.cat_map.get(code, "其他")
            cat_hits[cat] = cat_hits.get(cat, 0) + 1
            
            if hits >= 10 and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
                item = {
                    "通報時間": now.strftime("%H:%M:%S"), "代碼": code, "名稱": st.session_state.name_map.get(code),
                    "產業": cat, "price": price, "chg": chg, "vwap_dist": vwap_dist,
                    "sl": round(price * 0.985, 2), "tp": round(price * 1.025, 2), "cond": "量能激增"
                }
                st.session_state.state['history'].append(item)
                st.session_state.state['reported_codes'].add(code)
                send_winner_alert(item)

    # 顯示結果
    if st.session_state.state['history']:
        st.subheader("📊 今日通報紀錄")
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(20), use_container_width=True)

    # 倒數計時進入下次掃描
    st.write(f"⌛ 預計 {scan_sec} 秒後進行下次掃頻...")
    time.sleep(scan_sec)
    st.rerun()
