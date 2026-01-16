# ==========================================
# 4. UI 介面與自動啟動邏輯
# ==========================================
with st.sidebar:
    st.header("🎯 核心監控參數")
    # ... (保留原本的 slider 和 number_input) ...

    # 關鍵修改：移除按鈕判斷，改為「只要在交易時段就嘗試啟動」
    if not st.session_state.state['running']:
        # 自動執行登入邏輯
        try:
            st.session_state.api.login(API_KEY, SECRET_KEY)
            # ... (保留原本的合約抓取邏輯) ...
            st.session_state.state['running'] = True
            st.success("✅ 系統已自動啟動監控")
            st.rerun()
        except Exception as e:
            st.error(f"自動登入失敗: {e}")
    else:
        if st.button("■ 手動停止監控", use_container_width=True):
            st.session_state.state['running'] = False
            st.rerun()
