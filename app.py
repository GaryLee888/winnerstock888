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
# 1. 核心配置區
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
# 2. 核心功能函式
# ==========================================

def get_font(size):
    try:
        f_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        if platform.system() == "Windows":
            f_path = "msjhbd.ttc"
        return ImageFont.truetype(f_path, size)
    except:
        return ImageFont.load_default()

def send_winner_alert(item):
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 50], fill=(255, 215, 0))
    draw.text((40, 10), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=get_font(24))
    draw.text((40, 75), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 140), f"{item['price']}", fill=accent, font=get_font(75))
    draw.text((320, 170), f"{item['chg']}%", fill=accent, font=get_font(35))
    draw.text((40, 250), f"目標停利：{item['tp']:.2f}", fill=(255, 60, 60), font=get_font(28))
    draw.text((310, 250), f"建議停損：{item['sl']:.2f}", fill=(0, 200, 0), font=get_font(28))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    content = f"🚀 **發財電報！** 💰 **{item['code']} {item['name']}** 爆發中！"
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 3. UI 介面
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5, step=0.1)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    if not st.session_state.state['running']:
        if st.button("▶ 啟動監控", type="primary", use_container_width=True):
            try:
                st.session_state.api.login(API_KEY, SECRET_KEY)
                raw = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] 
                       for c in m if len(c.code) == 4]
                st.session_state.ref_map = {c.code: float(c.reference) for c in raw if c.reference}
                st.session_state.name_map = {c.code: c.name for c in raw}
                st.session_state.cat_map = {c.code: c.category for c in raw}
                st.session_state.contracts = [c for c in raw if c.code in st.session_state.ref_map]
                try:
                    st.session_state.mkt_contracts = [st.session_state.api.Contracts.Indices.TSE["001"], st.session_state.api.Contracts.Indices.OTC["OTC"]]
                except:
                    st.session_state.mkt_contracts = [st.session_state.api.Contracts.Stocks.TSE["001"], st.session_state.api.Contracts.Stocks.OTC["OTC"]]
                st.session_state.state['running'] = True
                st.rerun()
            except Exception as e:
                st.error(f"登入失敗: {e}")
    else:
        if st.button("■ 停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()

    if st.session_state.state['history']:
        df_exp = pd.DataFrame(st.session_state.state['history'])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_exp.to_excel(writer, index=False)
        st.download_button("📥 下載 Excel", output.getvalue(), f"Trade_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)

# ==========================================
# 4. 核心監控邏輯 (修復 NameError)
# ==========================================
if st.session_state.state['running']:
    # --- 關鍵修正：將 now 定義移出 try 區塊 ---
    now = datetime.now() 
    
    # A. 大盤檢查
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
                if diff < -0.15: 
                    danger = True
                    m_msgs.append(f"{name}急殺({diff:.2f}%)")
                else: m_msgs.append(f"{name}穩定")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs) if m_msgs else "大盤數據收集中..."
    except:
        st.session_state.state['market_safe'] = True # 失敗時預設安全，避免卡死

    # B. 動態閥值
    hm = now.hour * 100 + now.minute
    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)
    vol_threshold = vol_base * vol_weight

    st.info(f"{'🔴' if not st.session_state.state['market_safe'] else '🟢'} 大盤: {st.session_state.state['market_msg']}")

    # C. 標的掃描 (取前 500 檔活躍標的)
    targets = st.session_state.contracts[:500] 
    cat_hits = {}
    all_snaps = []
    for i in range(0, len(targets), 100):
        all_snaps.extend(st.session_state.api.snapshots(targets[i:i+100]))
    
    for s in all_snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if price <= 0 or ref <= 0 or s.total_volume < vol_total_min: continue
        chg = round(((price - ref) / ref * 100), 2)
        if not (chg_min <= chg <= 9.8): continue
        
        # 動能計算
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        
        # 條件篩選
        if ((min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)) and (ratio >= vol_threshold):
            daily_high = s.high if s.high > 0 else price
            vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
            vwap_dist = round(((price - vwap) / vwap * 100), 2)
            
            if ((daily_high - price) / daily_high * 100) <= drawdown_limit and vwap_dist <= vwap_gap_limit:
                st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
                hits = len(st.session_state.state['trigger_history'][code])
                cat = st.session_state.cat_map.get(code, "未知")
                cat_hits[cat] = cat_hits.get(cat, 0) + 1
                
                if hits >= hit_thr and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
                    cond_msg = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
                    item = {
                        "通報時間": now.strftime("%H:%M:%S"), "代碼": code, "名稱": st.session_state.name_map.get(code),
                        "產業": cat, "price": price, "chg": chg, "vwap_dist": vwap_dist,
                        "sl": round(price * 0.985, 2), "tp": round(price * 1.025, 2), "cond": cond_msg
                    }
                    st.session_state.state['history'].append(item)
                    st.session_state.state['reported_codes'].add(code)
                    send_winner_alert(item)

    if st.session_state.state['history']:
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(15), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
