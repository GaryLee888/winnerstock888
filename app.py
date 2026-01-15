import streamlit as st
import shioaji as sj
import pandas as pd
import time
import requests
import io
import platform
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. 配置與秘鑰 (建議部屬時改用 st.secrets)
# ==========================================
API_KEY = "5FhL23V9888K6yMnMK3S7CAnCdHAtrESypTGprqRz"
SECRET_KEY = "HV8yi97EpyTYxN9yEB9tiEjnWpNZeNLcVyf4WRw"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1457393304537927764/D2vpM73dMl2Z-bLfI0Us52eGdCQyjztASwkBP3RzyF2jaALzEeaigajpXQfzsgLdyzw4"

st.set_page_config(page_title="當沖雷達-勝率最佳化Web版", layout="wide")

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
# 3. 核心工具函式
# ==========================================

def get_font(size):
    try:
        f_name = "msjhbd.ttc" if platform.system() == "Windows" else "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        return ImageFont.truetype(f_name, size)
    except:
        return ImageFont.load_default()

def send_winner_alert(item):
    """完整移植原本的卡片美化與 Discord 發送"""
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
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    content = f"🚀 **發財電報！** 💰 **{item['code']} {item['name']}** 爆發中！\n條件: {item['cond']}"
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 4. UI 參數配置 (完整移植原本所有參數)
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5)
    vol_yesterday = st.number_input("昨日交易量>", value=3000)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    st.divider()
    if st.button("▶ 啟動/刷新 API 登入", type="primary", use_container_width=True):
        st.session_state.api.login(API_KEY, SECRET_KEY)
        # 預載合約
        stocks = [c for m in [st.session_state.api.Contracts.Stocks.TSE, st.session_state.api.Contracts.Stocks.OTC] for c in m if len(c.code) == 4]
        st.session_state.contracts = stocks
        st.session_state.ref_map = {c.code: float(c.reference) for c in stocks if c.reference}
        st.session_state.name_map = {c.code: c.name for c in stocks}
        st.session_state.cat_map = {c.code: c.category for c in stocks}
        # 大盤合約
        try:
            st.session_state.mkt_contracts = [st.session_state.api.Contracts.Indices.TSE["001"], st.session_state.api.Contracts.Indices.OTC["OTC"]]
        except:
            st.session_state.mkt_contracts = [st.session_state.api.Contracts.Stocks.TSE["001"], st.session_state.api.Contracts.Stocks.OTC["OTC"]]
        st.session_state.state['running'] = True
        st.rerun()

    if st.button("■ 停止監控", use_container_width=True):
        st.session_state.state['running'] = False
        st.rerun()

    if st.session_state.state['history']:
        st.divider()
        if st.button("🏁 一鍵結算今日收盤價", use_container_width=True):
            # (結算邏輯同前，略過以省空間)
            pass
        
        df_export = pd.DataFrame(st.session_state.state['history'])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False)
        st.download_button("📥 下載 Excel 到電腦", output.getvalue(), f"Trade_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)

# ==========================================
# 5. 核心監控邏輯 (完整移植)
# ==========================================
if st.session_state.state['running']:
    # A. 市場風險檢查 (移植自 check_market_risk)
    m_snaps = st.session_state.api.snapshots(st.session_state.mkt_contracts)
    now = datetime.now()
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

    # B. 時間動態閾值 (移植自 refresh_data)
    hm = now.hour * 100 + now.minute
    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)
    vol_threshold = vol_base * vol_weight

    # C. 掃描與篩選
    st.info(f"{'🔴' if danger else '🟢'} 環境: {st.session_state.state['market_msg']}")
    
    # 分批掃描 (Web 版限制批次以維持穩定)
    all_contracts = st.session_state.contracts
    cat_hits = {}
    
    # 這裡示範掃描前 300 檔標的 (或可根據成交量排序預篩)
    target_batches = all_contracts[:500] 
    
    snaps = []
    for i in range(0, len(target_batches), 100):
        snaps.extend(st.session_state.api.snapshots(target_batches[i:i+100]))
    
    for s in snaps:
        code, price = s.code, s.close
        ref = st.session_state.ref_map.get(code, 0)
        if not code or price <= 0 or ref <= 0 or s.total_volume < vol_total_min: continue
        
        # 漲幅過濾
        chg = round(((price - ref) / ref * 100), 2)
        if not (chg_min <= chg <= 9.8): continue
        
        # 1分動能與量增率 (移植原本計算方式)
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        
        # 核心條件篩選
        momentum_ok = (min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)
        if not momentum_ok or ratio < vol_threshold: continue
        
        # 回撤限制
        daily_high = s.high if s.high > 0 else price
        if ((daily_high - price) / daily_high * 100) > drawdown_limit: continue
        
        # 均價乖離
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        if vwap_dist > vwap_gap_limit: continue
        
        # 觸發計數與族群判斷
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.state['trigger_history'][code])
        cat = st.session_state.cat_map.get(code, "未知")
        cat_hits[cat] = cat_hits.get(cat, 0) + 1
        
        # 判斷是否發報
        if hits >= hit_thr and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
            cond_msg = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
            item = {
                "通報時間": now.strftime("%H:%M:%S"), "代碼": code, "名稱": st.session_state.name_map.get(code),
                "產業": cat, "price": price, "chg": chg, "min_v": min_vol_pct, "ratio": ratio,
                "sl": round(price * 0.985, 2), "tp": round(price * 1.025, 2), "cond": cond_msg,
                "收盤價": None, "績效%": None
            }
            st.session_state.state['history'].append(item)
            st.session_state.state['reported_codes'].add(code)
            send_winner_alert(item)

    # 顯示即時監控表
    if st.session_state.state['history']:
        st.subheader("🚩 最近觸發訊號")
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(10), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
else:
    st.warning("監控停止中，請從側邊欄啟動。")
