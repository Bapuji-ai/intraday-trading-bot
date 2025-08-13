import streamlit as st
import pandas as pd
import glob, os
from datetime import datetime, date
import plotly.express as px
import plotly.graph_objects as go
import json

# Page config for mobile-friendly design
st.set_page_config(
    page_title="📊 Intraday Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better mobile experience
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #1f77b4;
        text-align: center;
        padding: 1rem 0;
    }
    .metric-card {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 10px;
        margin: 0.5rem 0;
        color: white;
    }
    .positive { color: #00ff00; font-weight: bold; }
    .negative { color: #ff4444; font-weight: bold; }
    .neutral { color: #ffa500; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 class="main-header">📊 Intraday Trading Bot Dashboard</h1>', unsafe_allow_html=True)

# Sidebar for controls
with st.sidebar:
    st.header("🎛️ Controls")
    auto_refresh = st.checkbox("Auto Refresh (30s)", value=True)
    if auto_refresh:
        st.rerun()
    
    st.header("📋 Quick Stats")
    
# Load latest data function
@st.cache_data(ttl=30)  # Cache for 30 seconds
def load_bot_data():
    """Load trading bot data from files"""
    data = {
        'daily_reports': [],
        'log_entries': [],
        'sentiment': 'Neutral',
        'active_positions': 0
    }
    
    # Load daily trade reports
    try:
        report_files = glob.glob('daily_trade_report_*.csv')
        if report_files:
            latest_report = max(report_files, key=os.path.getctime)
            df = pd.read_csv(latest_report)
            data['daily_reports'] = df.to_dict('records')
    except:
        pass
    
    # Load log entries (last 50 lines)
    try:
        with open('trading_bot.log', 'r') as f:
            lines = f.readlines()
            data['log_entries'] = lines[-50:] if lines else []
    except:
        pass
    
    return data

# Load data
bot_data = load_bot_data()

# Main dashboard layout
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="🎯 Total Trades Today",
        value=len(bot_data['daily_reports']),
        delta=f"+{len(bot_data['daily_reports'])}"
    )

with col2:
    total_pnl = sum(trade.get('pnl', 0) for trade in bot_data['daily_reports'])
    st.metric(
        label="💰 Today's P&L (₹)",
        value=f"₹{total_pnl:.2f}",
        delta=f"{'📈' if total_pnl > 0 else '📉'} {total_pnl:.2f}"
    )

with col3:
    winning_trades = len([t for t in bot_data['daily_reports'] if t.get('pnl', 0) > 0])
    win_rate = (winning_trades / len(bot_data['daily_reports']) * 100) if bot_data['daily_reports'] else 0
    st.metric(
        label="🏆 Win Rate",
        value=f"{win_rate:.1f}%",
        delta=f"{winning_trades}/{len(bot_data['daily_reports'])}"
    )

with col4:
    st.metric(
        label="📊 Market Sentiment",
        value="🟢 Bullish" if total_pnl > 100 else "🔴 Bearish" if total_pnl < -100 else "🟡 Neutral"
    )

# Charts section
if bot_data['daily_reports']:
    st.header("📈 Trading Performance")
    
    # Create DataFrame for charts
    trades_df = pd.DataFrame(bot_data['daily_reports'])
    
    col1, col2 = st.columns(2)
    
    with col1:
        # P&L by Stock
        if 'symbol' in trades_df.columns and 'pnl' in trades_df.columns:
            pnl_by_stock = trades_df.groupby('symbol')['pnl'].sum().reset_index()
            pnl_by_stock = pnl_by_stock.sort_values('pnl', ascending=False).head(10)
            
            fig = px.bar(
                pnl_by_stock,
                x='symbol',
                y='pnl',
                title="📊 Top 10 Stocks by P&L",
                color='pnl',
                color_continuous_scale=['red', 'yellow', 'green']
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # Trade Distribution
        if 'signal' in trades_df.columns:
            signal_counts = trades_df['signal'].value_counts()
            
            fig = px.pie(
                values=signal_counts.values,
                names=signal_counts.index,
                title="🎯 Trade Distribution",
                color_discrete_map={'BUY': '#00ff00', 'SELL': '#ff4444'}
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

# Recent trades table
if bot_data['daily_reports']:
    st.header("📋 Recent Trades")
    
    recent_trades = pd.DataFrame(bot_data['daily_reports']).tail(10)
    
    # Style the dataframe
    def color_pnl(val):
        if isinstance(val, (int, float)):
            return 'color: green' if val > 0 else 'color: red' if val < 0 else 'color: orange'
        return ''
    
    if 'pnl' in recent_trades.columns:
        styled_trades = recent_trades.style.applymap(color_pnl, subset=['pnl'])
        st.dataframe(styled_trades, use_container_width=True)
    else:
        st.dataframe(recent_trades, use_container_width=True)

# Live activity log
st.header("🔴 Live Activity Log")
if bot_data['log_entries']:
    log_container = st.container()
    with log_container:
        for line in bot_data['log_entries'][-10:]:  # Show last 10 entries
            if 'Sentiment:' in line:
                st.success(f"📊 {line.strip()}")
            elif 'Placed' in line:
                st.info(f"🎯 {line.strip()}")
            elif 'Closed' in line:
                st.warning(f"💼 {line.strip()}")
            else:
                st.text(line.strip())
else:
    st.info("🤖 Waiting for bot activity...")

# Download section
st.header("💾 Download Reports")
col1, col2 = st.columns(2)

with col1:
    if st.button("📥 Download Today's Report"):
        try:
            report_files = glob.glob('daily_trade_report_*.csv')
            if report_files:
                latest_report = max(report_files, key=os.path.getctime)
                with open(latest_report, 'rb') as f:
                    st.download_button(
                        label="💾 Download CSV",
                        data=f.read(),
                        file_name=f"trading_report_{date.today()}.csv",
                        mime="text/csv"
                    )
        except:
            st.error("No reports available")

with col2:
    if st.button("📥 Download Full Log"):
        try:
            with open('trading_bot.log', 'rb') as f:
                st.download_button(
                    label="💾 Download Log",
                    data=f.read(),
                    file_name=f"trading_log_{date.today()}.txt",
                    mime="text/plain"
                )
        except:
            st.error("Log file not available")

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666;'>
    🤖 Intraday Trading Bot Dashboard | Built with Streamlit<br>
    📱 Mobile Friendly | 🔄 Auto-refreshing every 30 seconds
</div>
""", unsafe_allow_html=True)

# Auto refresh
if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()
