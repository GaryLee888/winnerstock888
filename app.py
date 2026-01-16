import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
import platform
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 核心配置 (Secrets 讀取)
# ==========================================
try:
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Settings -> Secrets 填入正確金鑰。")
    st.stop()

st.set_page_config(page_title="當沖雷達", layout="wide")
TZ_TW = timezone(timedelta(hours=8)) # 強制台灣時區

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'state' not in st.session_state:
    st.session_state.state = {
        'running': False,
        'history': [],
        'reported_codes': set(),
        'last_total_vol': {},
        'market_safe': True,
        'market_msg': "等待數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {}
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 工具函式
# ==========================================
def get_font(size):
    try:
        f_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        if platform.system() == "Windows": f_path = "msjhbd.ttc"
        return ImageFont.truetype(f_path, size)
    except: return ImageFont.load_default()

def send_winner_alert(item):
    """依照桌面版 create_winner_card 邏輯還原"""
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 45], fill=(255, 215, 0))
    draw.text((40, 8), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=get_font(22))
    
    draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
    draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
    
    draw.text((40, 240), f"目標停利：{item['tp']:.2f}", fill=(255, 60, 60), font=get_font(26))
    draw.text((310, 240), f"建議停損：{item['sl']:.2f}", fill=(0, 200, 0), font=get_font(26))
    draw.text((40, 290), f"均價乖離：{item['vwap_dist']}%", fill=(0, 255, 255), font=get_font(18))
    
    draw.rectangle([0, 350, 600, 400], fill=(30, 31, 35))
    draw.text((40, 362), f"訊號: {item['cond']} | 偵測: {item['hit']}次", fill=(255, 215, 0), font=get_font(18))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **發財電報！** 💰💰💰\n🔥 **{item['code']} {item['name']}** 爆發中！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
        return True
    except: return False

# ==========================================
# 4. UI 介面
# ==========================================
st.title("🚀 當沖雷達監控系統")

# --- ✨ 新增：手動存檔功能區 ---
if st.session_state.state['history']:
    st.subheader("💾 數據手動存檔")
    # 將通報紀錄轉換為 DataFrame
    df_save = pd.DataFrame(st.session_state.state['history'])
    
    # 建立記憶體內的 Excel 檔案
    excel_data = io.BytesIO()
    with pd.ExcelWriter(excel_data, engine='xlsxwriter') as writer:
        df_save.to_excel(writer, index=False, sheet_name='今日通報紀錄')
    
    st.download_button(
        label="📥 儲存目前通報紀錄為 Excel",
        data=excel_data.getvalue(),
        file_name=f"Trade_Log_{datetime.now(TZ_TW).strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    st.divider()

with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vol_trade_min = st.number_input("成交張數>", value=3000)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    st.divider()
    if st.button("🧪 測試發報", use_container_width=True):
        send_winner_alert({"code":"8888","name":"測試股","price":100,"chg":5,"tp":102.5,"sl":98.5,"vwap_dist":1.2,"cond":"🚀 測試","hit":3})

    if not st.session_state.state['running']:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            st.session_state.api.login(API_KEY, SECRET_KEY)
            raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
            st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
            st.session_state.name_map = {c.code: c.name for c in raw}
            st.session_state.cat_map = {c.code: c.category for c in raw}
            st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
            st.session_state.mkt_codes = ["001", "OTC"]
            st.session_state.state['running'] = True
            st.rerun()
    else:
        if st.button("■ 停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()

status_container = st.empty()
progress_container = st.empty()

# ==========================================
# 5. 核心邏輯 (保持不動)
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    
    try:
        m_snaps = st.session_state.api.snapshots(st.session_state.mkt_codes)
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

    status_container.info(f"🕒 {now.strftime('%H:%M:%S')} | 大盤: {st.session_state.state['market_msg']}")

    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    
    vol_threshold = vol_base * vol_weight
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)

    targets = [c for c in st.session_state.contracts] 
    all_snaps = []
    with progress_container:
        bar = st.progress(0, text="🔎 掃描中...")
        for i in range(0, len(targets), 100):
            snaps = st.session_state.api.snapshots(targets[i:i+100])
            all_snaps.extend(snaps)
            bar.progress(min((i+100)/len(targets), 1.0))
        bar.empty()

    current_hits_cat = {}
    current_detecting = []

    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0 or s.total_volume < vol_trade_min: continue
        
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        chg = round(((price - ref) / ref * 100), 2)
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        
        if not (chg_min <= chg <= 9.8): continue
        if s.yesterday_volume < vol_yesterday_min: continue 
        
        momentum_ok = (min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)
        if not momentum_ok: continue
        if ratio < vol_threshold: continue
        
        daily_high = s.high if s.high > 0 else price
        if ((daily_high - price) / daily_high * 100) > drawdown_limit: continue
        
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.state['trigger_history'][code])
        cat = st.session_state.cat_map.get(code, "未知")
        current_hits_cat[cat] = current_hits_cat.get(cat, 0) + 1
        
        current_detecting.append({
            "代碼": code, "名稱": st.session_state.name_map.get(code),
            "價格": price, "漲幅": chg, "動能%": min_vol_pct, "次數": hits, "狀態": "觀察中"
        })

        if hits >= hit_thr and code not in st.session_state.state['reported_codes']:
            if st.session_state.state['market_safe'] and vwap_dist <= vwap_gap_limit:
                cond = f"🔥 {cat}族群強勢" if current_hits_cat.get(cat, 0) >= 2 else "🚀 短線爆發"
                item = {
                    "通報時間": now.strftime("%H:%M:%S"), "code": code, "name": st.session_state.name_map.get(code),
                    "cat": cat, "price": price, "chg": chg, "vwap_dist": vwap_dist, "hit": hits,
                    "sl": round(price * 0.985, 2), "tp": round(price * 1.025, 2), "cond": cond
                }
                st.session_state.state['history'].append(item)
                st.session_state.state['reported_codes'].add(code)
                send_winner_alert(item)

    st.subheader("📊 今日通報紀錄 (發財電報)")
    if st.session_state.state['history']:
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(15), use_container_width=True)

    st.divider()
    st.subheader("🔍 即時偵測清單")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
