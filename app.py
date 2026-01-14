import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import twstock
import warnings

# --- 基礎設定 ---
st.set_page_config(page_title="台股智慧策略決策系統", layout="wide")
warnings.filterwarnings("ignore")

class ProStockAnalyzer:
    def __init__(self):
        self.special_mapping = {"貝爾威勒": "7861", "能率亞洲": "7777", "力旺": "3529", "朋程": "8255"}
        self.twii_df = self.fetch_market_data()

    def fetch_market_data(self):
        try:
            df = yf.download("^TWII", period="2y", progress=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return df
        except: return None
        return None

    def fetch_data_robust(self, sid, period="1y", interval="1d"):
        for suffix in [".TW", ".TWO"]:
            try:
                ticker = f"{sid}{suffix}"
                df = yf.download(ticker, period=period, interval=interval, progress=False)
                if df is not None and not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    return df, ticker
            except: continue
        return None, None

    def calculate_advanced_strategy(self, df_d, df_w):
        df = df_d.copy()
        # 1. 基礎指標
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        std = df['Close'].rolling(20).std()
        df['BB_up'] = df['MA20'] + (std * 2)
        df['BB_low'] = df['MA20'] - (std * 2)
        df['BB_width'] = (df['BB_up'] - df['BB_low']) / df['MA20']
        
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        df['K'] = ((df['Close'] - low_9) / (high_9 - low_9).replace(0, np.nan) * 100).ewm(com=2).mean()
        df['D'] = df['K'].ewm(com=2).mean()
        
        ema12, ema26 = df['Close'].ewm(span=12).mean(), df['Close'].ewm(span=26).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9).mean()
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss).replace(0, np.nan)))
        
        tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        
        # 2. 籌碼與位階
        df['VMA20'] = df['Volume'].rolling(20).mean()
        df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
        df['MFI'] = 50 + (df['Close'].diff().rolling(14).mean() * 10)
        df['BIAS5'] = (df['Close'] - df['MA5']) / df['MA5'] * 100
        df['BIAS20'] = (df['Close'] - df['MA20']) / df['MA20'] * 100
        df['Bias_P90'] = df['BIAS20'].rolling(250).quantile(0.9)
        df['ROC'] = df['Close'].pct_change(12) * 100
        df['SR_Rank'] = (df['Close'] - df['Close'].rolling(60).min()) / (df['Close'].rolling(60).max() - df['Close'].rolling(60).min()).replace(0, 1)
        
        # 3. 進階策略項 (Pro 版核心)
        df['Range_Ratio'] = (df['High'] - df['Low']) / df['Close']
        df['VCP_Score'] = df['Range_Ratio'].rolling(10).mean() < df['Range_Ratio'].rolling(30).mean()
        df['Squeeze_Release'] = (df['BB_width'] > df['BB_width'].shift(1)) & (df['BB_width'].shift(1) < 0.08)
        
        if df_w is not None and not df_w.empty:
            w_ma20 = df_w['Close'].rolling(20).mean()
            df['Weekly_Trend'] = float(df_w['Close'].iloc[-1]) > float(w_ma20.iloc[-1])
        else: df['Weekly_Trend'] = False

        if self.twii_df is not None:
            s_ret = df['Close'].pct_change(20)
            m_ret = self.twii_df['Close'].pct_change(20).reindex(s_ret.index, method='ffill')
            df['RS'] = s_ret - m_ret
        else: df['RS'] = 0

        up_v = df['Volume'].where(df['Close'] > df['Close'].shift(1), 0).rolling(10).sum()
        dn_v = df['Volume'].where(df['Close'] < df['Close'].shift(1), 0).rolling(10).sum()
        df['Vol_Ratio'] = up_v / dn_v.replace(0, 1)

        return df.dropna()

    def calculate_total_score(self, curr, prev, df_p):
        # 基礎 20 項 (各 5分，共 100分)
        base_conds = [
            curr['Close'] > curr['MA20'], curr['Close'] > curr['BB_up'],
            curr['K'] > curr['D'], curr['MACD_hist'] > 0, curr['RSI'] > 50,
            curr['MA5'] > curr['MA10'], curr['K'] > 50, abs(curr['BIAS20']) < 10,
            curr['BB_width'] < 0.1, curr['Close'] > prev['Close'],
            curr['RS'] > 0, curr['OBV'] > df_p['OBV'].mean(), curr['MFI'] > 50,
            curr['Volume'] > curr['VMA20'], curr['Close'] > curr['MA5'],
            curr['BIAS5'] > curr['BIAS20'], curr['Close'] > curr['MA20'], # KC Mid 簡化為 MA20
            curr['Vol_Ratio'] > 1, curr['ROC'] > 0, curr['SR_Rank'] > 0.5
        ]
        # 進階 5 項 (各 10分，共 50分)
        adv_conds = [
            curr['VCP_Score'], curr['Volume'] > curr['VMA20'] * 1.5,
            curr['Squeeze_Release'], curr['BIAS20'] < curr['Bias_P90'],
            curr['Weekly_Trend']
        ]
        total = sum(base_conds) * 5 + sum(adv_conds) * 10
        return int((total / 150) * 100), base_conds, adv_conds

# --- UI 介面 ---
analyzer = ProStockAnalyzer()

with st.sidebar:
    st.title("🛡️ Pro 智慧策略設定")
    atr_sl_mult = st.slider("動態止損倍數 (ATR)", 1.5, 3.5, 2.5)
    st.divider()
    default_stocks = ["2330", "2317", "2454", "能率亞洲", "2603", "2881", "3035", "6235", "", ""]
    queries = [st.text_input(f"股票 {i+1}", v, key=f"q{i}") for i, v in enumerate(default_stocks)]
    queries = [q for q in queries if q]

st.title("🚀 台股 Pro 智慧全方位決策系統")

if queries:
    tabs = st.tabs([f"📊 {q}" for q in queries])
    for tab, query in zip(tabs, queries):
        with tab:
            sid = analyzer.special_mapping.get(query, query)
            if not sid.isdigit():
                for code, info in twstock.codes.items():
                    if query in info.name: sid = code; break
            
            df_d, _ = analyzer.fetch_data_robust(sid, "1y", "1d")
            df_w, _ = analyzer.fetch_data_robust(sid, "2y", "1wk")
            
            if df_d is not None and not df_d.empty:
                df_p = analyzer.calculate_advanced_strategy(df_d, df_w)
                curr = df_p.iloc[-1]
                prev = df_p.iloc[-2]
                curr_p = float(curr['Close'])
                
                # 智慧點位計算
                smart_entry = float(curr['MA20']) if curr_p > curr['MA20'] else float(curr['MA10'])
                chandelier_exit = df_p['High'].tail(20).max() - (curr['ATR'] * atr_sl_mult)
                smart_sl = max(chandelier_exit, curr_p * 0.93)
                smart_tp = curr_p + (curr_p - smart_sl) * 2.5
                
                # 評分
                score, b_list, a_list = analyzer.calculate_total_score(curr, prev, df_p)
                
                # 1. 最上方交易決策
                if score <= 20: advice, color = "🚫 不能碰", "#7f8c8d"
                elif score <= 40: advice, color = "👀 看就好", "#95a5a6"
                elif score <= 60: advice, color = "⚖️ 中立觀望", "#3498db"
                elif score <= 80: advice, color = "💸 小量試單", "#f39c12"
                else: advice, color = "🔥 強烈買進", "#e74c3c"

                st.markdown(f"<h2 style='color:{color}; text-align:center;'>{advice} (得分: {score})</h2>", unsafe_allow_html=True)
                st.progress(score / 100)

                # 2. 智慧價位卡片
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("💰 目前現價", f"{curr_p:.2f}")
                c2.metric("🎯 智慧買點", f"{smart_entry:.2f}")
                c3.metric("🚫 動態止損", f"{smart_sl:.2f}")
                c4.metric("🏆 目標獲利", f"{smart_tp:.2f}")

                # 3. 策略分析報告 (Expander)
                with st.expander("📝 智慧策略診斷報告", expanded=True):
                    msg = "🚩 **策略提示：** "
                    if curr['VCP_Score']: msg += "偵測到 VCP 收斂狀態，波動縮減中。 "
                    if curr['Squeeze_Release']: msg += "布林噴發啟動(Squeeze Release)！ "
                    if curr_p > curr['Bias_P90']: msg += "⚠️ 注意：乖離率進入過熱區(P90)。 "
                    if not curr['Weekly_Trend']: msg += "⚠️ 警告：週線趨勢向下，長線偏空。"
                    st.write(msg)
                    
                    st.divider()
                    col_l, col_r = st.columns(2)
                    col_l.write("**基礎指標符合數:** " + str(sum(b_list)) + "/20")
                    col_r.write("**進階策略加分項:** " + str(sum(a_list)) + "/5")

                # 4. 互動圖表
                st.subheader("📈 技術走勢與智慧買賣點")
                chart_df = df_p.tail(80).copy()
                # 為了顯示買賣點，將點位加入圖表數據
                chart_df['智慧買點'] = smart_entry
                chart_df['動態止損'] = smart_sl
                st.line_chart(chart_df[['Close', 'MA20', '智慧買點', '動態止損']])

            else:
                st.error(f"無法讀取股票 {query} 的數據")
