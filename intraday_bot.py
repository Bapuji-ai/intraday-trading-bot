import os, glob, csv, time, re
import pandas as pd
import requests, yfinance as yf
from datetime import datetime
from textblob import TextBlob
import talib
from dotenv import load_dotenv
import logging, schedule
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from bs4 import BeautifulSoup
import backtrader as bt

# ===== CONFIG =====
load_dotenv()
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
CAPITAL = 100000
RISK_PER_TRADE = 0.01
POSITIVE_WORDS = ["rally","highs","bullish","gains","optimism","rate cut"]
NEGATIVE_WORDS = ["drop","crash","bearish","losses","concerns","sell-off"]

logging.basicConfig(level=logging.INFO, filename="trading_bot.log",
                    format="%(asctime)s - %(message)s")
vader_analyzer = SentimentIntensityAnalyzer()
active_positions = {}
trade_history = {}

# ===== ROBUST NSE CSV LOADER =====
def load_symbols_from_nse_files(folder_path="nse_sector_files"):
    """Load symbols from NSE Market Watch CSV files with robust format handling."""
    tickers = {}
    files = glob.glob(os.path.join(folder_path, "*.csv"))
    if not files:
        print(f"⚠ No files found in {folder_path}")
        return tickers

    for file in files:
        try:
            # Read with UTF-8-BOM handling for NSE files
            with open(file, encoding='utf-8-sig', errors='ignore') as f:
                lines = f.readlines()

            # Find header row - look for SYMBOL anywhere in the line
            header_row_idx = None
            for i, line in enumerate(lines):
                # Clean the line and check for SYMBOL
                clean_line = re.sub(r'["\s\n\r]', '', line.upper())
                if 'SYMBOL' in clean_line:
                    header_row_idx = i
                    break
            
            if header_row_idx is None:
                print(f"Skipping {file}: No SYMBOL header found")
                continue

            # Read CSV from header row
            df = pd.read_csv(file, skiprows=header_row_idx, dtype=str, 
                           engine='python', encoding='utf-8-sig')
            
            # Clean column names - remove quotes, spaces, newlines
            df.columns = [re.sub(r'["\s\n\r]', '', str(c)).upper() for c in df.columns]
            
            if 'SYMBOL' not in df.columns:
                print(f"Skipping {file}: No SYMBOL column found")
                continue

            # Clean symbol column
            symbols = (df['SYMBOL']
                      .astype(str)
                      .str.replace('"', '')
                      .str.replace('&amp;', '&')
                      .str.strip()
                      .str.upper())
            
            # Filter valid symbols
            mask = (
                symbols.notna() &
                (symbols != '') &
                (symbols != '-') &
                (~symbols.str.startswith('NIFTY')) &
                (~symbols.str.startswith('BANKNIFTY')) &
                (~symbols.str.startswith('FINNIFTY'))
            )
            
            valid_symbols = symbols[mask].unique()
            count = 0
            for sym in valid_symbols:
                # Keep alphabetic symbols or those with & character (like M&MFIN)
                if sym.replace('&', '').replace('M', '').isalpha() or '&' in sym:
                    tickers[sym + ".NS"] = sym.lower()
                    count += 1
            
            print(f"Loaded {count} symbols from {os.path.basename(file)}")
            
        except Exception as e:
            print(f"Skipping {file}: {e}")

    print(f"✅ Total unique symbols loaded: {len(tickers)}")
    return tickers

# ===== NEWS SOURCES =====
def fetch_news(query):
    if not NEWS_API_KEY: return []
    try:
        r = requests.get(f"https://newsapi.org/v2/everything?q={query}&language=en&apiKey={NEWS_API_KEY}")
        return r.json().get("articles", []) if r.status_code==200 else []
    except: return []

def fetch_finnhub_news():
    if not FINNHUB_API_KEY: return []
    try:
        r = requests.get(f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}")
        if r.status_code == 200:
            return [{"description": a.get("headline","")} for a in r.json()]
    except: return []
    return []

def fetch_rss_news(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code==200:
            soup = BeautifulSoup(r.content, "xml")
            return [{"description": item.title.text} for item in soup.find_all("item")]
    except: return []
    return []

def get_all_news():
    today = datetime.now().strftime("%Y-%m-%d")
    articles=[]
    feeds = [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.moneycontrol.com/rss/marketreports.xml",
        "https://www.livemint.com/rss/markets"
    ]
    articles += fetch_news(f"global stock market {today}")
    articles += fetch_news(f"Indian stock market {today}")
    articles += fetch_finnhub_news()
    for f in feeds: articles += fetch_rss_news(f)
    return articles

# ===== SENTIMENT ANALYSIS =====
def analyze_sentiment(text):
    if not text.strip(): return 0.0
    try:
        return (TextBlob(text).sentiment.polarity +
                vader_analyzer.polarity_scores(text)['compound'])/2
    except:
        pos = sum(text.lower().count(w) for w in POSITIVE_WORDS)
        neg = sum(text.lower().count(w) for w in NEGATIVE_WORDS)
        return (pos-neg)/(pos+neg+1)

def fetch_and_analyze_sentiment():
    scores=[]; mentioned=[]
    for _ in range(3):
        text = " ".join([(a.get("description") or "") for a in get_all_news()])
        sc = analyze_sentiment(text)
        scores.append(sc)
        mentioned += [s for s,n in ALL_STOCKS.items()
                      if n in text.lower() or s.split('.')[0].lower() in text.lower()]
    avg = sum(scores)/len(scores)
    sent = "Bullish" if avg>0.2 else "Bearish" if avg<-0.2 else "Neutral"
    uniq = list(set(mentioned)) or list(ALL_STOCKS.keys())[:5]
    logging.info(f"Sentiment: {sent} ({avg:.2f}) | {uniq}")
    return sent, avg, uniq

# ===== BACKTEST STRATEGY =====
class VWAPRSIStrategy(bt.Strategy):
    params = (("rsi_period",14),("vwap_period",20),)
    def __init__(self):
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.vwap = bt.ind.WMA(self.data.close*self.data.volume, period=self.p.vwap_period)/ \
                     bt.ind.WMA(self.data.volume, period=self.p.vwap_period)
    def next(self):
        if not self.position:
            if self.data.close>self.vwap and self.rsi<70: self.buy()
            elif self.data.close<self.vwap and self.rsi>30: self.sell()
        else:
            if self.position.size>0 and (self.data.close<self.vwap or self.rsi>70): self.close()
            elif self.position.size<0 and (self.data.close>self.vwap or self.rsi<30): self.close()

def backtest_vwap_rsi(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="5m")
        if df.empty: return None, None
        df.dropna(inplace=True)
        cb = bt.Cerebro()
        cb.addstrategy(VWAPRSIStrategy)
        cb.adddata(bt.feeds.PandasData(dataname=df))
        cb.broker.set_cash(100000)
        cb.addsizer(bt.sizers.FixedSize, stake=10)
        start=cb.broker.getvalue(); cb.run(); end=cb.broker.getvalue()
        return end-start, df
    except: return None, None

# ===== ATR & TRADING =====
def calculate_atr(df, period=14):
    try: return talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=period).dropna().iloc[-1]
    except: return None

def place_trade(symbol, signal, sentiment, df):
    atr = calculate_atr(df)
    if not atr or atr==0: return
    qty = int((CAPITAL*RISK_PER_TRADE)/(atr*1.5))
    if qty<1: return
    if (sentiment=="Bearish" and signal=="BUY") or (sentiment=="Bullish" and signal=="SELL"): return
    entry = df['Close'].iloc[-1]
    sl = entry - atr*1.5 if signal=="BUY" else entry + atr*1.5
    active_positions[symbol] = {"signal":signal,"qty":qty,"entry_price":entry,"stop_loss":sl}
    print(f"{datetime.now()} | {symbol} | {signal} | Qty:{qty} | SL:{sl:.2f}")
    logging.info(f"Placed {signal} {symbol} | Entry:{entry:.2f} | SL:{sl:.2f}")

def exit_trade(symbol, price):
    pos = active_positions.pop(symbol, None)
    if pos:
        pnl = pos['qty']*(price-pos['entry_price']) if pos['signal']=="BUY" else pos['qty']*(pos['entry_price']-price)
        trade_history.setdefault(datetime.now().date(), []).append(
            {'symbol':symbol,'signal':pos['signal'],'qty':pos['qty'],
             'entry_price':pos['entry_price'],'exit_price':price,
             'pnl':pnl,'exit_time':datetime.now().strftime('%H:%M:%S')})
        print(f"{datetime.now()} | Closed {symbol} | {pos['signal']} | PnL:{pnl:.2f}")
        logging.info(f"Closed {symbol} | {pos['signal']} | PnL:{pnl:.2f}")

def get_current_price(symbol):
    try:
        df = yf.download(symbol, period="1d", interval="1m")
        if not df.empty: return df['Close'][-1]
    except: return None
    return None

def monitor_positions():
    for sym,pos in list(active_positions.items()):
        price = get_current_price(sym)
        if price is None: continue
        if pos['signal']=="BUY" and price<=pos['stop_loss']: exit_trade(sym, price)
        elif pos['signal']=="SELL" and price>=pos['stop_loss']: exit_trade(sym, price)

def save_daily_report():
    if not trade_history: return
    filename = f"daily_trade_report_{datetime.now().strftime('%Y-%m-%d')}.csv"
    all_trades = []
    for _,t in trade_history.items(): all_trades.extend(t)
    if all_trades:
        keys = all_trades[0].keys()
        with open(filename,'w',newline='') as f:
            w = csv.DictWriter(f,keys); w.writeheader(); w.writerows(all_trades)
        print(f"📊 Daily report saved to {filename}")
        logging.info(f"Daily report saved: {filename}")

# ===== MAIN TRADING LOGIC =====
def daily_trading():
    print(f"🔍 Running daily trading at {datetime.now()}")
    sentiment,score,stocks = fetch_and_analyze_sentiment()
    for sym in stocks:
        pnl,df = backtest_vwap_rsi(sym)
        if pnl is None: continue
        sig = "BUY" if pnl>0 and sentiment=="Bullish" else "SELL" if pnl<0 and sentiment=="Bearish" else None
        if sig: place_trade(sym,sig,sentiment,df)

# ===== MAIN PROGRAM =====
if __name__=="__main__":
    print("🚀 Starting Intraday Trading Bot...")
    
    # Load stock symbols from NSE files
    ALL_STOCKS = load_symbols_from_nse_files(folder_path="nse_sector_files")
    if not ALL_STOCKS: 
        print("❌ No stock data loaded. Exiting...")
        exit()

    # Schedule tasks
    schedule.every(1).minutes.do(daily_trading)
    schedule.every(1).minutes.do(monitor_positions)
    schedule.every().day.at("15:30").do(save_daily_report)

    print("✅ Trading bot started. Waiting for schedule...")
    print(f"📊 Loaded symbols: {list(ALL_STOCKS.keys())[:10]}...")  # Show first 10
    
    while True:
        schedule.run_pending()
        time.sleep(1)
