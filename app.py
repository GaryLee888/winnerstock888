import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import os
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 持久化與時區
# ==========================================
REPORT_FILE = "report_history.csv"
TZ_TW = timezone(timedelta(hours=8))

def load_local_history():
    if os.path.exists(REPORT_FILE):
        try:
            df = pd.read_csv(REPORT_FILE)
            df['code'] = df['code'].astype(str)
            return df
        except: return pd.DataFrame()
    return pd.DataFrame()

def save_to_local(df):
    df.to_csv(REPORT_FILE, index=False)

st.set_page_config(page_title="24H 穩定監控雷達", layout="wide")

# ==========================================
# 2. Session State 初始化
# ==========================================
if 'state' not in st.session_state:
    history_df = load_local_history()
    st.session_state.state = {
        'running': False,
        'history': history_df.to_dict('records'),
        'reported_codes': set(history_df['code'].tolist()) if not history_df.empty else set(),
        'last_total_vol': {}, 
        'market_safe': True,
        'market_msg': "等待數據...",
        'market_history': {"001": [], "OTC": []},
        'trigger_history': {},
        'diag_msg': "初始化中..." # 新增診斷訊息
    }

if 'api' not in st.session_state:
    st.session_state.api = sj.Shioaji()

# --- 工具函式 (保持 100% 原始邏輯) ---
def get_font(size):
    try: return ImageFont.truetype("msjhbd.ttc", size)
    except: return ImageFont.load_default()

def send_winner_alert(item):
    try:
        DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"]
        img = Image.new('RGB', (600, 400), color=(18, 19, 23))
        draw = ImageDraw.Draw(img)
        accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
        draw.rectangle([0, 0, 15, 400], fill=accent)
        draw.rectangle([15, 0, 600, 45], fill=(255, 215, 0))
        draw.text((40, 8), "🚀 財神降臨！發財電報", fill=(0, 0, 0), font=get_font(22))
        draw.text((40, 65), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
        draw.text((40, 130), f"{item['price']}", fill=accent, font=get_font(70))
        draw.text((320, 160), f"{item['chg']}%", fill=accent, font=get_font(30))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        requests.post(DISCORD_WEBHOOK_URL, data={"content": f"🚀 **{item['code']} {item['name']}** 爆發！"}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 3. UI 介面
# ==========================================
st.title("🚀 當沖雷達 - 系統診斷版")

# 系統診斷看板 (幫助檢查為什麼篩不到)
diag_col1, diag_col2, diag_col3 = st.columns(3)
diag_col1.metric("API 狀態", "連線中" if st.session_state.state['running'] else "斷線")
diag_col2.metric("已監控標的", len(st.session_state.state.get('last_total_vol', {})))
diag_col3.write(f"📢 診斷狀態: {st.session_state.state['diag_msg']}")

with st.sidebar:
    st.header("🎯 參數設定")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday_min = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("基準總量>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)

status_container = st.empty()
progress_container = st.empty()

# 自動啟動
if not st.session_state.state['running']:
    try:
        st.session_state.api.login(st.secrets["SHIOAJI_API_KEY"], st.secrets["SHIOAJI_SECRET_KEY"])
        raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
        st.session_state.name_map = {c.code: c.name for c in raw}
        st.session_state.cat_map = {c.code: c.category for c in raw}
        st.session_state.y_vol_map = {c.code: getattr(c, 'yesterday_volume', 0) for c in raw}
        st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
        st.session_state.mkt_codes = ["001", "OTC"]
        st.session_state.state['running'] = True
        st.session_state.state['diag_msg'] = "合約抓取成功"
        st.rerun()
    except Exception as e:
        st.error(f"登入失敗: {e}"); time.sleep(10); st.rerun()

# ==========================================
# 4. 核心監控循環
# ==========================================
if st.session_state.state['running']:
    now = datetime.now(TZ_TW)
    hm = now.hour * 100 + now.minute
    
    # [A] 大盤檢查
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
            if past and (ms.close - past[-1]) / past[-1] * 100 < -0.15: danger = True; m_msgs.append(f"{name}急殺")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs) if m_msgs else "穩定"
    except: st.session_state.state['market_safe'] = True

    status_container.info(f"🕒 {now.strftime('%H:%M:%S')} | 大盤: {st.session_state.state['market_msg']}")

    # [B] 時間閾值
    if hm < 1000: vol_base, hit_thr = 0.55, 15
    elif hm < 1100: vol_base, hit_thr = 0.40, 12
    elif hm < 1230: vol_base, hit_thr = 0.25, 8
    else: vol_base, hit_thr = 0.20, 6
    adj_mom_thr = (mom_min_pct * (1.6 if hm < 1000 else 1.0)) * (scan_sec / 60.0)

    # [C] 掃描
    all_snaps = []
    with progress_container:
        bar = st.progress(0, text="🔎 行情同步中...")
        for i in range(0, len(st.session_state.contracts), 100):
            all_snaps.extend(st.session_state.api.snapshots(st.session_state.contracts[i:i+100]))
            bar.progress(min((i+100)/len(st.session_state.contracts), 1.0))
        bar.empty()

    current_detecting = []
    max_vol_diff = 0 # 診斷用

    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        y_vol = st.session_state.y_vol_map.get(code, 0)
        
        if price <= 0 or ref <= 0 or s.total_volume < vol_total_min: continue
        
        chg = round(((price - ref) / ref * 100), 2)
        if chg < chg_min or y_vol < vol_yesterday_min: continue
        
        # 動能計算
        last_vol = st.session_state.state['last_total_vol'].get(code)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        if last_vol is None: continue 
        
        vol_diff = s.total_volume - last_vol
        if vol_diff > max_vol_diff: max_vol_diff = vol_diff
        
        min_vol_pct = round((vol_diff / s.total_volume * 100), 2) if s.total_volume > 0 else 0
        
        # 核心篩選門檻
        momentum_ok = (min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)
        ratio = round(s.total_volume / (y_vol if y_vol > 0 else 1), 2)
        
        if not momentum_ok or ratio < (vol_base * vol_weight): continue
        
        daily_high = s.high if s.high > 0 else price
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        
        if ((daily_high - price) / daily_high * 100) > drawdown_limit: continue
        
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.state['trigger_history'][code])
        
        current_detecting.append({"代碼": code, "股名": st.session_state.name_map.get(code), "現價": price, "次數": hits, "漲幅%": chg, "量差": vol_diff})

        if hits >= hit_thr and str(code) not in st.session_state.state['reported_codes']:
            if st.session_state.state['market_safe'] and vwap_dist <= vwap_gap_limit:
                item = {
                    "通報時間": now.strftime("%H:%M:%S"), "code": str(code), "name": st.session_state.name_map.get(code),
                    "price": price, "chg": chg, "tp": round(price * 1.025, 2), "sl": round(price * 0.985, 2), "vwap_dist": vwap_dist, "hit": hits, "cond": "🚀 爆發"
                }
                st.session_state.state['history'].append(item)
                st.session_state.state['reported_codes'].add(str(code))
                save_to_local(pd.DataFrame(st.session_state.state['history']))
                send_winner_alert(item)

    st.session_state.state['diag_msg'] = f"掃描完成 | 本輪最大量差: {max_vol_diff}"
    
    st.subheader("🔍 即時偵測看板")
    if current_detecting:
        st.dataframe(pd.DataFrame(current_detecting).sort_values("次數", ascending=False), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
