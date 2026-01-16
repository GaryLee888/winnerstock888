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
# 1. 檔案持久化設定 (解決重整歸零問題)
# ==========================================
LOG_FILE = "detection_log.csv"
REPORT_FILE = "report_history.csv"

def load_data(file):
    if os.path.exists(file):
        return pd.read_csv(file)
    return pd.DataFrame()

def save_data(df, file):
    df.to_csv(file, index=False)

# ==========================================
# 2. 核心配置與初始化
# ==========================================
st.set_page_config(page_title="24H 雲端當沖雷達", layout="wide")
TZ_TW = timezone(timedelta(hours=8))

# 從檔案恢復紀錄，若無檔案則建立空結構
if 'history_df' not in st.session_state:
    st.session_state.history_df = load_data(REPORT_FILE)
if 'reported_codes' not in st.session_state:
    st.session_state.reported_codes = set(st.session_state.history_df['code'].astype(str)) if not st.session_state.history_df.empty else set()

# ==========================================
# 3. 介面與下載功能 (手動存檔)
# ==========================================
st.title("🚀 雲端自動雷達 (持久化版)")

# 提供下載按鈕：直接讀取儲存在伺服器上的檔案
if not st.session_state.history_df.empty:
    csv_data = st.session_state.history_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 下載完整通報紀錄 (CSV)",
        data=csv_data,
        file_name=f"Trade_Log_{datetime.now(TZ_TW).strftime('%Y%m%d')}.csv",
        mime="text/csv",
        type="primary"
    )

# ==========================================
# 4. 核心監控邏輯 (節錄關鍵修改處)
# ==========================================

# ... (API 登入與大盤檢查邏輯保持不變) ...

if st.session_state.state['running']:
    # --- 篩選符合條件標的後 ---
    # 當符合發報門檻時：
    if hits >= hit_thr and code not in st.session_state.reported_codes:
        new_item = {
            "通報時間": now.strftime("%H:%M:%S"),
            "code": code,
            "name": st.session_state.name_map.get(code),
            "price": price,
            "chg": chg,
            "cond": "🚀 短線爆發"
        }
        
        # 1. 更新 Session State (即時顯示)
        st.session_state.history_df = pd.concat([st.session_state.history_df, pd.DataFrame([new_item])], ignore_index=True)
        st.session_state.reported_codes.add(code)
        
        # 2. 立即寫入實體檔案 (持久化)
        save_data(st.session_state.history_df, REPORT_FILE)
        
        # 3. 發送 Discord
        send_winner_alert(new_item)

# ... (顯示看板邏輯) ...
