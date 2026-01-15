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
# 1. 核心配置區 (自動從 Secrets 讀取並清洗數據)
# ==========================================
try:
    # 使用 .strip() 防止 nacl 格式錯誤 (這就是你遇到的 Bug 修復關鍵)
    API_KEY = st.secrets["SHIOAJI_API_KEY"].strip()
    SECRET_KEY = st.secrets["SHIOAJI_SECRET_KEY"].strip()
    DISCORD_WEBHOOK_URL = st.secrets["DISCORD_WEBHOOK_URL"].strip()
except Exception as e:
    st.error("❌ 找不到 Secrets 設定！請在 Streamlit Cloud 的 Settings -> Secrets 填入 API 金鑰。")
    st.stop()

st.set_page_config(page_title="當沖雷達-雲端終極版", layout="wide")

# ==========================================
# 2. 初始化 Session State (跨頁面與循環保存資料)
# ==========================================
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
    # 建立一個持久的 API 物件
    st.session_state.api = sj.Shioaji()

# ==========================================
# 3. 核心工具函式
# ==========================================

def get_font(size):
    """雲端環境字體適配"""
    try:
        # Streamlit Cloud (Linux) 路徑
        f_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        if platform.system() == "Windows":
            f_path = "msjhbd.ttc"
        return ImageFont.truetype(f_path, size)
    except:
        return ImageFont.load_default()

def send_winner_alert(item):
    """生成卡片並發送至 Discord (完整美化版)"""
    img = Image.new('RGB', (600, 400), color=(18, 19, 23))
    draw = ImageDraw.Draw(img)
    accent = (255, 60, 60) if item['chg'] > 8 else (255, 165, 0)
    
    # 繪製邊框與標題區
    draw.rectangle([0, 0, 15, 400], fill=accent)
    draw.rectangle([15, 0, 600, 50], fill=(255, 215, 0))
    draw.text((40, 10), "🚀 財神降臨！發財電報 💰💰💰", fill=(0, 0, 0), font=get_font(24))
    
    # 內容資訊
    draw.text((40, 75), f"{item['code']} {item['name']}", fill=(255, 255, 255), font=get_font(44))
    draw.text((40, 140), f"{item['price']}", fill=accent, font=get_font(75))
    draw.text((320, 170), f"{item['chg']}%", fill=accent, font=get_font(35))
    draw.text((40, 250), f"目標停利：{item['tp']:.2f}", fill=(255, 60, 60), font=get_font(28))
    draw.text((310, 250), f"建議停損：{item['sl']:.2f}", fill=(0, 200, 0), font=get_font(28))
    draw.text((40, 310), f"均價乖離：{item['vwap_dist']}%", fill=(0, 255, 255), font=get_font(20))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    content = f"🚀 **發財電報！** 💰 **{item['code']} {item['name']}** 爆發中！\n條件: {item['cond']}"
    try:
        requests.post(DISCORD_WEBHOOK_URL, data={"content": content}, 
                      files={"file": (f"{item['code']}.png", buf, "image/png")}, timeout=10)
    except: pass

# ==========================================
# 4. UI 介面與參數 (完整移植篩選邏輯)
# ==========================================
st.title("🚀 當沖雷達 - 雲端監控終極版")

with st.sidebar:
    st.header("🎯 核心監控參數")
    scan_sec = st.slider("掃頻(秒)", 5, 60, 10)
    chg_min = st.number_input("漲幅下限%", value=2.5, step=0.1)
    vol_total_min = st.number_input("今日成交張數>", value=3000)
    mom_min_pct = st.number_input("1分動能% >", value=1.5)
    vol_weight = st.number_input("動態量權重", value=1.0)
    drawdown_limit = st.number_input("回撤限制%", value=1.2)
    vwap_gap_limit = st.number_input("均價乖離% <", value=3.5)
    
    st.divider()
    
    if not st.session_state.state['running']:
        if st.button("▶ 啟動 API 監控", type="primary", use_container_width=True):
            try:
                # 登入前檢查 Key 是否有效
                if len(API_KEY) < 10 or len(SECRET_KEY) < 10:
                    st.error("Key 長度異常，請檢查 Secrets 設定。")
                else:
                    st.session_state.api.login(API_KEY, SECRET_KEY)
                    st.toast("✅ API 登入成功")
                    
                    # 預載合約庫
                    with st.spinner("初始化全市場標的..."):
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
                st.error(f"登入失敗 (可能是金鑰問題): {e}")
    else:
        if st.button("■ 停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()

    if st.session_state.state['history']:
        st.divider()
        if st.button("🏁 一鍵結算收盤價", use_container_width=True):
            # 結算邏輯
            target_codes = list(set([str(i['code']) for i in st.session_state.state['history']]))
            target_contracts = [c for c in st.session_state.contracts if c.code in target_codes]
            snap_map = {}
            for i in range(0, len(target_contracts), 100):
                snaps = st.session_state.api.snapshots(target_contracts[i:i+100])
                for s in snaps: snap_map[str(s.code)] = s.close
            
            for item in st.session_state.state['history']:
                code_str = str(item['code'])
                if code_str in snap_map and snap_map[code_str] > 0:
                    cp = snap_map[code_str]
                    item['收盤價'] = cp
                    item['績效%'] = round((cp - item['price']) / item['price'] * 100, 2)
            st.success("結算更新完畢！")

        # 導出 Excel (方案一核心)
        df_exp = pd.DataFrame(st.session_state.state['history'])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_exp.to_excel(writer, index=False)
        st.download_button("📥 下載 Excel 到電腦", output.getvalue(), f"Trade_{datetime.now().strftime('%Y%m%d')}.xlsx", use_container_width=True)

# ==========================================
# 5. 監控邏輯主循環 (完整移植)
# ==========================================
if st.session_state.state['running']:
    # A. 大盤檢查
    try:
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
                if diff < -0.15: 
                    danger = True
                    m_msgs.append(f"{name}急殺({diff:.2f}%)")
                else: m_msgs.append(f"{name}穩定")
        st.session_state.state['market_safe'] = not danger
        st.session_state.state['market_msg'] = " | ".join(m_msgs) if m_msgs else "大盤數據收集中..."
    except: pass

    # B. 動態閥值計算
    hm = now.hour * 100 + now.minute
    if hm < 1000: vol_base, mom_adj, hit_thr = 0.55, 1.6, 15
    elif hm < 1100: vol_base, mom_adj, hit_thr = 0.40, 1.2, 12
    elif hm < 1230: vol_base, mom_adj, hit_thr = 0.25, 0.9, 8
    else: vol_base, mom_adj, hit_thr = 0.20, 0.7, 6
    
    adj_mom_thr = (mom_min_pct * mom_adj) * (scan_sec / 60.0)
    vol_threshold = vol_base * vol_weight

    st.info(f"{'🔴' if not st.session_state.state['market_safe'] else '🟢'} 市場狀態: {st.session_state.state['market_msg']}")

    # C. 標的掃描 (分批處理提升穩定度)
    # 取前 500 檔 (可根據權重或量能排序以增加效率)
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
        
        # 動能與量增計算
        vol_diff = 0
        min_vol_pct = 0.0
        if code in st.session_state.state['last_total_vol']:
            vol_diff = s.total_volume - st.session_state.state['last_total_vol'][code]
            if vol_diff > 0: min_vol_pct = round((vol_diff / s.total_volume) * 100, 2)
        st.session_state.state['last_total_vol'][code] = s.total_volume
        
        ratio = round(s.total_volume / (s.yesterday_volume if s.yesterday_volume > 0 else 1), 2)
        
        # 條件篩選
        momentum_ok = (min_vol_pct >= adj_mom_thr) or (vol_diff >= 50)
        if not momentum_ok or ratio < vol_threshold: continue
        
        daily_high = s.high if s.high > 0 else price
        if ((daily_high - price) / daily_high * 100) > drawdown_limit: continue
        
        vwap = (s.amount / s.total_volume) if s.total_volume > 0 else price
        vwap_dist = round(((price - vwap) / vwap * 100), 2)
        if vwap_dist > vwap_gap_limit: continue
        
        # 觸發與族群
        st.session_state.state['trigger_history'][code] = [t for t in st.session_state.state['trigger_history'].get(code, []) if t > now - timedelta(minutes=10)] + [now]
        hits = len(st.session_state.state['trigger_history'][code])
        cat = st.session_state.cat_map.get(code, "未知")
        cat_hits[cat] = cat_hits.get(cat, 0) + 1
        
        if hits >= hit_thr and code not in st.session_state.state['reported_codes'] and st.session_state.state['market_safe']:
            cond_msg = f"🔥 {cat}族群強勢" if cat_hits.get(cat, 0) >= 2 else "🚀 短線爆發"
            item = {
                "通報時間": now.strftime("%H:%M:%S"), "代碼": code, "名稱": st.session_state.name_map.get(code),
                "產業": cat, "price": price, "chg": chg, "vwap_dist": vwap_dist, "min_v": min_vol_pct,
                "sl": round(price * 0.985, 2), "tp": round(price * 1.025, 2), "cond": cond_msg,
                "收盤價": None, "績效%": None
            }
            st.session_state.state['history'].append(item)
            st.session_state.state['reported_codes'].add(code)
            send_winner_alert(item)

    # D. 介面表格更新
    if st.session_state.state['history']:
        st.subheader("📊 即時觸發清單")
        st.dataframe(pd.DataFrame(st.session_state.state['history']).tail(15), use_container_width=True)

    time.sleep(scan_sec)
    st.rerun()
else:
    st.info("💡 準備就緒，請點擊啟動監控。")
    if st.session_state.state['history']:
        st.subheader("📅 今日歷史紀錄")
        st.dataframe(pd.DataFrame(st.session_state.state['history']), use_container_width=True)
