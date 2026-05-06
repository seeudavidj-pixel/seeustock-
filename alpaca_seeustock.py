import time
import requests
import sys
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime
from bs4 import BeautifulSoup
import pytz
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
import os

class config:
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    ALPACA_API_KEY = os.environ.get('ALPACA_API_KEY', '')
    ALPACA_SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', '')
    PAPER_TRADING = os.environ.get('PAPER_TRADING', 'True') == 'True'
    TOP_N_BUY = 2
    CHECK_INTERVAL = 5
    DROP_THRESHOLD = -5.0
    PROFIT_TARGET = 10.0
    STOP_LOSS = -5.0

NZ_TZ = pytz.timezone('Pacific/Auckland')
US_TZ = pytz.timezone('America/New_York')

def nz_now():
    return datetime.now(NZ_TZ)

def send_telegram(message):
    print(f"[알림] {message}")
    urls = [
        f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
        f"https://api1.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
        f"https://api2.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
    ]
    for i, url in enumerate(urls):
        try:
            response = requests.post(
                url,
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message},
                timeout=15,
                verify=False
            )
            if response.status_code == 200:
                print(f"[텔레그램 전송 성공]")
                return
        except Exception as e:
            print(f"[텔레그램 시도 {i+1} 실패] {e}")

try:
    trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING)
    data_client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    print('[연결] Alpaca 연결 성공')
except Exception as e:
    print(f'[연결 오류] {e}')
    sys.exit(1)

US_HOLIDAYS = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25"
]

SECTOR_MAP = {
    "NVDA": "semiconductor", "AMD": "semiconductor", "INTC": "semiconductor", "QCOM": "semiconductor",
    "AAPL": "bigtech", "MSFT": "bigtech", "GOOGL": "bigtech", "META": "bigtech", "AMZN": "bigtech",
    "TSLA": "ev", "RIVN": "ev", "LCID": "ev", "F": "auto", "GM": "auto", "NKLA": "ev",
    "LMT": "defense", "RTX": "defense", "NOC": "defense", "GD": "defense", "KTOS": "defense", "BBAI": "defense",
    "MARA": "crypto", "RIOT": "crypto", "CLSK": "crypto", "COIN": "crypto", "MSTR": "crypto",
    "XOM": "energy", "CVX": "energy", "COP": "energy", "TELL": "energy", "SWN": "energy",
    "PLUG": "cleanenergy", "FCEL": "cleanenergy", "BLNK": "cleanenergy", "ENPH": "cleanenergy",
    "JPM": "finance", "BAC": "finance", "GS": "finance", "MS": "finance", "SOFI": "finance",
    "JNJ": "pharma", "PFE": "pharma", "MRK": "pharma", "LLY": "pharma", "MRNA": "biotech", "NVAX": "biotech",
    "VALE": "mining", "FCX": "mining", "CLF": "steel", "NEM": "gold", "GLD": "gold",
    "DAL": "airline", "UAL": "airline", "AAL": "airline",
    "RKLB": "space", "SPCE": "space",
    "RGTI": "quantum", "QUBT": "quantum", "IONQ": "quantum",
    "SOUN": "aivoice", "NOK": "telecom",
}

def is_market_open_today():
    us_now = datetime.now(US_TZ)
    if us_now.weekday() >= 5:
        return False
    us_today = us_now.strftime("%Y-%m-%d")
    if us_today in US_HOLIDAYS:
        return False
    return True

def get_full_article_text(url, timeout=8):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=timeout)
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text() for p in paragraphs if len(p.get_text()) > 50])
        return text[:3000]
    except:
        return ""

def get_analyst_ratings():
    ratings = []
    try:
        url = "https://finviz.com/analyst_ratings_latest.ashx"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.find_all('tr')[1:30]
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 5:
                symbol = cols[1].get_text().strip()
                action = cols[2].get_text().strip().lower()
                rating = cols[3].get_text().strip().lower()
                score = 0
                if 'upgrade' in action: score += 3
                elif 'downgrade' in action: score -= 3
                elif 'initiat' in action: score += 1
                if any(w in rating for w in ['buy', 'outperform', 'overweight']): score += 2
                elif any(w in rating for w in ['sell', 'underperform', 'underweight']): score -= 2
                if symbol:
                    ratings.append({'symbol': symbol, 'score': score})
    except Exception as e:
        print(f"[애널리스트 오류] {e}")
    return ratings

def get_market_news():
    news_items = []
    try:
        url = "https://finance.yahoo.com/news/"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            if '/news/' in a.get('href', '') and len(a.get_text().strip()) > 20:
                href = a['href']
                if not href.startswith('http'):
                    href = 'https://finance.yahoo.com' + href
                news_items.append({'title': a.get_text().strip(), 'url': href})
    except Exception as e:
        print(f"[Yahoo 오류] {e}")
    try:
        url = "https://finviz.com/news.ashx"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', class_='nn-tab-link'):
            if a.get_text().strip():
                news_items.append({'title': a.get_text().strip(), 'url': a.get('href', '')})
    except Exception as e:
        print(f"[Finviz 오류] {e}")
    try:
        for sub in ["investing", "stocks", "wallstreetbets"]:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit=10"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            for post in r.json()['data']['children']:
                t = post['data']['title']
                if t.strip():
                    news_items.append({'title': t.strip(), 'url': ''})
    except Exception as e:
        print(f"[Reddit 오류] {e}")
    return news_items[:50]

def score_article(title, content):
    combined = (title + ' ' + content).lower()
    score = 0
    positive = [('strong buy',3),('upgraded to buy',3),('outperform',2),('overweight',2),
                ('price target raised',2),('beat estimates',2),('earnings beat',2),
                ('revenue beat',2),('raised guidance',2),('fda approval',3),('new contract',1),
                ('bullish',1),('strong demand',1)]
    negative = [('strong sell',-3),('downgraded',-3),('underperform',-2),('underweight',-2),
                ('price target cut',-2),('missed estimates',-2),('earnings miss',-2),
                ('lowered guidance',-2),('investigation',-3),('lawsuit',-3),
                ('fraud',-3),('recall',-2),('bankruptcy',-3),('disappointing',-1)]
    for kw, pts in positive:
        if kw in combined: score += pts
    for kw, pts in negative:
        if kw in combined: score += pts
    return score


def analyze_news_quick(news_items):
    """시간 촉박할때 키워드만 빠르게 분석"""
    stock_map = {
        "nvidia": ["NVDA"], "semiconductor": ["NVDA", "AMD", "INTC", "QCOM"],
        "chip": ["NVDA", "AMD", "INTC"], "artificial intelligence": ["NVDA", "MSFT", "GOOGL", "META", "AMD"],
        "AI": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "BBAI", "SOUN"],
        "apple": ["AAPL"], "microsoft": ["MSFT"], "google": ["GOOGL"],
        "amazon": ["AMZN"], "meta": ["META"], "tesla": ["TSLA"],
        "tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA"],
        "trump": ["BBAI", "KTOS", "LMT", "RTX", "TELL", "VALE"],
        "tariff": ["AAPL", "NVDA", "TSLA", "VALE", "CLF", "AAL"],
        "war": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "defense": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "bank": ["JPM", "BAC", "GS", "MS", "SOFI"],
        "federal reserve": ["JPM", "BAC", "GS", "SOFI"],
        "oil": ["XOM", "CVX", "COP", "TELL"],
        "energy": ["XOM", "CVX", "PLUG", "FCEL", "TELL"],
        "electric vehicle": ["TSLA", "RIVN", "LCID", "F", "GM"],
        "EV": ["TSLA", "RIVN", "LCID", "F"],
        "drug": ["JNJ", "PFE", "MRK", "ABBV", "LLY"],
        "FDA": ["MRNA", "NVAX", "AGEN"],
        "gold": ["GLD", "NEM"],
        "bitcoin": ["MARA", "RIOT", "CLSK", "COIN", "MSTR"],
        "crypto": ["MARA", "RIOT", "COIN", "MSTR"],
        "ukraine": ["LMT", "RTX", "VALE", "NOK"],
        "middle east": ["LMT", "RTX", "TELL", "XOM"],
        "sofi": ["SOFI"], "marathon": ["MARA"], "soundhound": ["SOUN"],
        "bigbear": ["BBAI"], "rigetti": ["RGTI"], "nokia": ["NOK"],
    }
    keyword_scores = {}
    for item in news_items:
        news_lower = item["title"].lower()
        for keyword, stocks in stock_map.items():
            if keyword.lower() in news_lower:
                for stock in stocks:
                    keyword_scores[stock] = keyword_scores.get(stock, 0) + 1

    sorted_stocks = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)
    top_30 = []
    used_sectors = {}
    for stock, score in sorted_stocks:
        sector = SECTOR_MAP.get(stock, "other")
        if used_sectors.get(sector, 0) < 2:
            top_30.append(stock)
            used_sectors[sector] = used_sectors.get(sector, 0) + 1
        if len(top_30) == 30:
            break

    default_stocks = ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "JPM", "AMD", "SOFI"]
    for s in default_stocks:
        if s not in top_30:
            top_30.append(s)
        if len(top_30) == 30:
            break
    return top_30

def analyze_news_and_pick_stocks(news_items):
    stock_map = {
        "nvidia": ["NVDA"], "semiconductor": ["NVDA", "AMD", "INTC", "QCOM"],
        "chip": ["NVDA", "AMD", "INTC"], "artificial intelligence": ["NVDA", "MSFT", "GOOGL", "META", "AMD"],
        "AI": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "BBAI", "SOUN"],
        "chatgpt": ["MSFT", "NVDA", "GOOGL"], "quantum": ["RGTI", "QUBT", "IONQ"],
        "apple": ["AAPL"], "microsoft": ["MSFT"], "google": ["GOOGL"],
        "amazon": ["AMZN"], "meta": ["META"], "tesla": ["TSLA"],
        "tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA"],
        "trump": ["BBAI", "KTOS", "LMT", "RTX", "TELL", "VALE"],
        "tariff": ["AAPL", "NVDA", "TSLA", "VALE", "CLF", "AAL"],
        "tariff exemption": ["NVDA", "AAPL", "TSLA", "AMD"],
        "war": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "defense": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "bank": ["JPM", "BAC", "GS", "MS", "SOFI"],
        "federal reserve": ["JPM", "BAC", "GS", "SOFI"],
        "interest rate": ["JPM", "BAC", "SOFI"],
        "rate cut": ["JPM", "BAC", "SOFI"],
        "inflation": ["JPM", "XOM", "CVX", "VALE", "GLD"],
        "oil": ["XOM", "CVX", "COP", "TELL"],
        "energy": ["XOM", "CVX", "PLUG", "FCEL", "TELL"],
        "solar": ["PLUG", "FCEL", "BLNK", "ENPH"],
        "electric vehicle": ["TSLA", "RIVN", "LCID", "F", "GM"],
        "EV": ["TSLA", "RIVN", "LCID", "F"],
        "biotech": ["MRNA", "BNTX", "NVAX", "AGEN"],
        "drug": ["JNJ", "PFE", "MRK", "ABBV", "LLY"],
        "FDA": ["MRNA", "NVAX", "AGEN"],
        "gold": ["GLD", "NEM"], "mining": ["VALE", "FCX", "NEM"],
        "space": ["RKLB", "SPCE"], "airline": ["DAL", "UAL", "AAL"],
        "bitcoin": ["MARA", "RIOT", "CLSK", "COIN", "MSTR"],
        "crypto": ["MARA", "RIOT", "COIN", "MSTR"],
        "crash": ["GLD", "SLV"], "recession": ["GLD", "JPM", "WMT"],
        "sofi": ["SOFI"], "marathon": ["MARA"], "soundhound": ["SOUN"],
        "bigbear": ["BBAI"], "rigetti": ["RGTI"], "nokia": ["NOK"],
        "ukraine": ["LMT", "RTX", "VALE", "NOK"],
        "middle east": ["LMT", "RTX", "TELL", "XOM"],
    }

    keyword_scores = {}
    for item in news_items:
        news_lower = item['title'].lower()
        for keyword, stocks in stock_map.items():
            if keyword.lower() in news_lower:
                for stock in stocks:
                    keyword_scores[stock] = keyword_scores.get(stock, 0) + 1

    analyst_ratings = get_analyst_ratings()
    final_scores = {}
    candidates = [s for s, _ in sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)[:30]]

    default_stocks = ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "JPM", "AMD", "SOFI"]
    for s in default_stocks:
        if s not in candidates:
            candidates.append(s)
        if len(candidates) == 30:
            break

    for symbol in candidates:
        total = keyword_scores.get(symbol, 0)
        for item in news_items:
            if symbol.lower() in item['title'].lower():
                content = get_full_article_text(item['url']) if item['url'] else ''
                total += score_article(item['title'], content)
                time.sleep(0.2)
        analyst_score = sum(r['score'] for r in analyst_ratings if r['symbol'] == symbol)
        total += analyst_score * 1.5
        final_scores[symbol] = total

    sorted_final = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    top_30 = []
    used_sectors = {}
    for stock, score in sorted_final:
        sector = SECTOR_MAP.get(stock, "other")
        if used_sectors.get(sector, 0) < 2:
            top_30.append(stock)
            used_sectors[sector] = used_sectors.get(sector, 0) + 1
        if len(top_30) == 30:
            break

    for s in default_stocks:
        if s not in top_30:
            top_30.append(s)
        if len(top_30) == 30:
            break

    return top_30

held_stocks = []
watchlist = []
start_equity = 0
buy_prices = {}
buy_amounts = {}
sell_records = []
blacklist_today = []

def get_price(symbol):
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = data_client.get_stock_latest_quote(req)
        return float(quote[symbol].ask_price or quote[symbol].bid_price)
    except Exception as e:
        print(f"[가격조회 오류] {symbol}: {e}")
    return None

def get_equity():
    for attempt in range(3):
        try:
            account = trading_client.get_account()
            equity = float(account.equity)
            if equity > 0:
                return equity
            time.sleep(2)
        except Exception as e:
            print(f"[잔액조회 오류 {attempt+1}] {e}")
            time.sleep(3)
    return 0

def get_cash():
    try:
        return float(trading_client.get_account().cash)
    except:
        return 0

def buy_stocks(symbols):
    global held_stocks, buy_prices, buy_amounts
    cash = get_cash()
    per_stock = cash / len(symbols)
    send_telegram(f"💰 매수 시작\n현금: ${cash:.2f}\n종목당: ${per_stock:.2f}")
    for symbol in symbols:
        try:
            price = get_price(symbol)
            if not price or price <= 0:
                send_telegram(f"⚠️ {symbol} 가격 조회 실패")
                continue
            order = MarketOrderRequest(
                symbol=symbol,
                notional=round(per_stock, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            )
            trading_client.submit_order(order)
            held_stocks.append(symbol)
            buy_prices[symbol] = price
            buy_amounts[symbol] = per_stock
            send_telegram(f"✅ 매수: {symbol} ${per_stock:.2f}어치 @ ${price:.2f}")
            time.sleep(1)
        except Exception as e:
            send_telegram(f"❌ {symbol} 매수 실패: {e}")

def sell_stock(symbol, reason=""):
    global held_stocks, sell_records
    try:
        positions = trading_client.get_all_positions()
        for pos in positions:
            if pos.symbol == symbol:
                sell_price = get_price(symbol) or float(pos.avg_entry_price)
                qty = float(pos.qty)
                buy_price_per_share = buy_prices.get(symbol, float(pos.avg_entry_price))
                buy_amt = buy_amounts.get(symbol, buy_price_per_share * qty)
                sell_amt = sell_price * qty
                pnl = sell_amt - buy_amt
                pnl_pct = (pnl / buy_amt * 100) if buy_amt > 0 else 0
                order = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY
                )
                trading_client.submit_order(order)
                sell_records.append({
                    "symbol": symbol,
                    "buy_price": buy_price_per_share,
                    "sell_price": sell_price,
                    "buy_amount": buy_amt,
                    "sell_amount": sell_amt,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "reason": reason
                })
                if symbol in held_stocks:
                    held_stocks.remove(symbol)
                send_telegram(f"📤 매도: {symbol}\n매수가: ${buy_price_per_share:.2f} → 매도가: ${sell_price:.2f}\n손익: {'+'if pnl>=0 else ''}{pnl:.2f} ({pnl_pct:.1f}%)\n사유: {reason}")
                return True
    except Exception as e:
        send_telegram(f"❌ {symbol} 매도 실패: {e}")
    return False

def sell_all_stocks(reason="장 마감"):
    global held_stocks
    send_telegram(f"🔔 전량 매도 시작 ({reason})...")
    # 최대 3번 재시도
    for attempt in range(3):
        try:
            positions = trading_client.get_all_positions()
            if not positions:
                send_telegram("✅ 보유 포지션 없음")
                break
            for pos in positions:
                sell_stock(pos.symbol, reason)
                time.sleep(1)
            break
        except Exception as e:
            send_telegram(f"⚠️ 포지션 조회 실패 ({attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(10)
            else:
                send_telegram("❌ 매도 실패 - 수동으로 확인 필요!")
    held_stocks = []
    time.sleep(3)
    send_daily_report()

def send_daily_report():
    try:
        end_equity = get_equity()
        # API 오류로 $0 반환시 포지션 가치로 대체
        if end_equity <= 0:
            try:
                positions = trading_client.get_all_positions()
                if positions:
                    end_equity = sum(float(p.market_value) for p in positions)
                else:
                    end_equity = start_equity  # 포지션도 없으면 시작 잔액 유지
            except:
                end_equity = start_equity
        pnl = end_equity - start_equity
        pnl_pct = (pnl / start_equity * 100) if start_equity > 0 else 0
        emoji = "📈" if pnl >= 0 else "📉"
        report = f"대표님 오늘 매매 결과 보고드리겠습니다! {emoji}\n{'='*25}\n"
        report += f"시작 잔액: ${start_equity:,.2f}\n"
        report += f"마감 잔액: ${end_equity:,.2f}\n"
        report += f"총 손익: {'+'if pnl>=0 else ''}{pnl:,.2f} USD\n"
        report += f"수익률: {'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%\n"
        report += f"{'='*25}\n📋 종목별 내역\n\n"
        for record in sell_records:
            r_emoji = "📈" if record['pnl'] >= 0 else "📉"
            report += f"{r_emoji} {record['symbol']}\n"
            report += f"  매수: ${record['buy_amount']:.2f} @ ${record['buy_price']:.2f}\n"
            report += f"  매도: ${record['sell_amount']:.2f} @ ${record['sell_price']:.2f}\n"
            report += f"  손익: {'+'if record['pnl']>=0 else ''}{record['pnl']:.2f} ({record['pnl_pct']:.1f}%)\n"
            report += f"  사유: {record['reason']}\n\n"
        send_telegram(report)
    except Exception as e:
        send_telegram(f"대표님 오늘 매매 결과 보고드리겠습니다!\n(보고서 오류: {e})")

def find_next_stock(current_symbol):
    global watchlist, blacklist_today
    current_sector = SECTOR_MAP.get(current_symbol, "other")
    news_items = get_market_news()
    for candidate in watchlist[:]:
        if candidate in blacklist_today or candidate in held_stocks:
            continue
        if SECTOR_MAP.get(candidate, "other") == current_sector:
            send_telegram(f"⏭️ {candidate} 건너뜀 (같은 섹터)")
            continue
        sentiment_score = 0
        for item in news_items:
            if candidate.lower() in item['title'].lower():
                sentiment_score += score_article(item['title'], '')
        if sentiment_score < -3:
            send_telegram(f"⏭️ {candidate} 건너뜀 (악재)")
            blacklist_today.append(candidate)
            continue
        watchlist.remove(candidate)
        return candidate
    return None

# 종목별 뉴스 감시 상태 (악재 감지 후 주가 하락 확인용)
news_watch = {}  # {symbol: {'bad_news': True, 'watch_count': 0}}

def monitor_once():
    global held_stocks, watchlist, blacklist_today, start_equity, news_watch
    if not held_stocks:
        return
    try:
        current_equity = get_equity()
        # API 오류로 0 반환시 손절 금지 - 재시도
        if current_equity <= 0:
            print("[모니터링] 잔액 조회 실패 - 손절 판단 건너뜀")
            return "continue"
        if start_equity > 0:
            total_pnl_pct = ((current_equity - start_equity) / start_equity) * 100
            if total_pnl_pct >= config.PROFIT_TARGET:
                send_telegram(f"🎯 목표 달성! +{total_pnl_pct:.1f}% → 전량 매도!")
                sell_all_stocks("목표 수익 달성")
                return "done"
            if total_pnl_pct <= config.STOP_LOSS:
                send_telegram(f"🛑 전체 손절! {total_pnl_pct:.1f}%")
                sell_all_stocks("전체 손절")
                return "done"

        # 5분마다 뉴스 수집
        news_items = get_market_news()

        for symbol in held_stocks.copy():
            try:
                price = get_price(symbol)
                if not price:
                    continue
                buy_price = buy_prices.get(symbol, price)
                profit_pct = ((price - buy_price) / buy_price) * 100

                # 뉴스 감성 분석 - score_article 활용
                news_score = 0
                for item in news_items:
                    if isinstance(item, dict):
                        title = item.get('title', '')
                    else:
                        title = item
                    if symbol.lower() in title.lower():
                        news_score += score_article(title, '')

                if news_score >= 2:
                    sentiment = "positive"
                elif news_score <= -2:
                    sentiment = "negative"
                else:
                    sentiment = "neutral"

                # 악재 뉴스 감지
                if sentiment == "negative":
                    if symbol not in news_watch:
                        news_watch[symbol] = {'bad_news': True, 'watch_count': 0, 'price_at_news': price}
                        send_telegram(f"⚠️ {symbol} 악재 뉴스 감지! 주가 주시 시작 (현재: ${price:.2f})")
                    else:
                        news_watch[symbol]['watch_count'] += 1
                        price_at_news = news_watch[symbol]['price_at_news']
                        price_change = ((price - price_at_news) / price_at_news) * 100

                        if price_change <= -1.5:
                            # 악재 뉴스 후 주가 하락 확인 → 즉시 매도
                            send_telegram(f"🚨 {symbol} 악재 뉴스 후 하락 확인! ({price_change:.1f}%) → 즉시 매도!")
                            blacklist_today.append(symbol)
                            if symbol in news_watch:
                                del news_watch[symbol]
                            sell_stock(symbol, f"악재 뉴스 후 하락 확인")
                            next_stock = find_next_stock(symbol)
                            if next_stock:
                                send_telegram(f"🔄 교체 매수: {next_stock}")
                                buy_stocks([next_stock])
                            continue
                        elif price_change >= 0:
                            # 악재 뉴스인데 주가 버팀 → 계속 주시
                            send_telegram(f"👀 {symbol} 악재 뉴스이나 주가 버팀 (${price:.2f}) → 계속 주시")
                        else:
                            # 소폭 하락 중 → 주시 계속
                            send_telegram(f"👀 {symbol} 악재 뉴스 후 소폭 하락 중 ({price_change:.1f}%) → 주시 중")
                else:
                    # 악재 해소되면 주시 해제
                    if symbol in news_watch:
                        del news_watch[symbol]
                        send_telegram(f"✅ {symbol} 악재 해소 → 정상 모니터링")

                # 수익/손절 기준 체크
                if profit_pct >= 10:
                    send_telegram(f"🎯 {symbol} +{profit_pct:.1f}% → 수익 확정!")
                    if symbol in news_watch:
                        del news_watch[symbol]
                    sell_stock(symbol, f"개별 목표 달성 +{profit_pct:.1f}%")
                    next_stock = find_next_stock(symbol)
                    if next_stock:
                        send_telegram(f"🔄 교체 매수: {next_stock}")
                        buy_stocks([next_stock])
                    continue
                if profit_pct <= config.DROP_THRESHOLD:
                    blacklist_today.append(symbol)
                    send_telegram(f"🚨 {symbol} 손절! {profit_pct:.1f}%")
                    if symbol in news_watch:
                        del news_watch[symbol]
                    sell_stock(symbol, f"손절 {profit_pct:.1f}%")
                    next_stock = find_next_stock(symbol)
                    if next_stock:
                        send_telegram(f"🔄 교체 매수: {next_stock}")
                        buy_stocks([next_stock])
                    continue

                # 현재 상태 보고
                news_status = "악재주시중" if symbol in news_watch else sentiment
                if profit_pct >= 5:
                    send_telegram(f"📈 {symbol} +{profit_pct:.1f}% 상승 중! (뉴스: {news_status})")
                elif profit_pct <= -3:
                    send_telegram(f"📉 {symbol} {profit_pct:.1f}% 하락 중... (뉴스: {news_status})")
                else:
                    send_telegram(f"📊 {symbol} {profit_pct:+.1f}% (뉴스: {news_status})")

            except Exception as e:
                print(f"[모니터링 오류] {symbol}: {e}")
    except Exception as e:
        print(f"[모니터링 오류] {e}")
    return "continue"

# ============================================
# 메인 실행 - 시간 기반 순차 처리
# ============================================
if __name__ == "__main__":
    # 시간 체크 - 00:50 이전이면 대기, 07:30 이후면 종료
    now = nz_now()
    current_time_min = now.hour * 60 + now.minute
    START_TIME = 1 * 60 + 30   # 01:30
    MARKET_CLOSE_CHECK = 7 * 60 + 0   # 07:00

    # 07:30 이후면 오늘은 종료
    if current_time_min >= MARKET_CLOSE_CHECK:
        print("[종료] 이미 07:30 이후 - 오늘 매매 종료")
        sys.exit(0)

    # 00:50 이전이면 대기
    if current_time_min < START_TIME:
        wait_sec = (START_TIME - current_time_min) * 60
        print(f"[대기] 00:50까지 {wait_sec//60}분 {wait_sec%60}초 대기...")
        time.sleep(wait_sec)

    send_telegram("🚀 대표님 오늘 매매 시작하겠습니다!")

    if not is_market_open_today():
        send_telegram("📅 오늘은 미국 장이 열리지 않아요")
        sys.exit(0)

    # 잔액 확인 - 재시도 포함
    current_equity = get_equity()
    if current_equity <= 0:
        # 포지션 가치로 대체 계산
        try:
            positions = trading_client.get_all_positions()
            if positions:
                current_equity = sum(float(p.market_value) for p in positions)
                send_telegram(f"⚠️ 잔액 조회 실패 - 포지션 가치로 대체: ${current_equity:,.2f}")
            else:
                send_telegram("⚠️ 잔액 조회 실패 + 포지션 없음 - 종료")
                sys.exit(0)
        except:
            send_telegram("⚠️ 잔액 및 포지션 조회 모두 실패 - 종료")
            sys.exit(0)
    start_equity = current_equity

    # 기존 포지션 로드
    try:
        existing_positions = trading_client.get_all_positions()
        if existing_positions:
            for pos in existing_positions:
                symbol = pos.symbol
                if symbol not in held_stocks:
                    held_stocks.append(symbol)
                    buy_prices[symbol] = float(pos.avg_entry_price)
                    buy_amounts[symbol] = float(pos.market_value)
            pos_list = ", ".join([pos.symbol for pos in existing_positions])
            send_telegram(f"📋 기존 포지션 로드: {pos_list}")
    except Exception as e:
        print(f"포지션 로드 오류: {e}")

    now = nz_now()
    current_hour = now.hour
    current_minute = now.minute
    current_time = current_hour * 60 + current_minute  # 분으로 변환

    MARKET_CLOSE = 7 * 60 + 0    # 07:00
    NEWS_TIME = 1 * 60 + 30      # 01:30
    BUY_TIME = 2 * 60 + 0        # 02:00 = 90분

    # 07:30 이후면 종료
    if current_time >= MARKET_CLOSE:
        send_telegram("⏰ 이미 장 마감 시간이에요.")
        if held_stocks:
            sell_all_stocks("장 마감")
        sys.exit(0)

    # 기존 포지션 없으면 뉴스 수집 + 매수 처리
    if not held_stocks:
        now = nz_now()
        current_time = now.hour * 60 + now.minute

        # 01:30 이전이면 → 01:00~01:30 사이에 분석 완료 목표
        # 01:30 이후면 → 바로 분석 후 즉시 매수
        time_until_buy = BUY_TIME - current_time  # 매수까지 남은 분

        if time_until_buy > 0:
            send_telegram(f"🔍 뉴스 수집 시작 (매수까지 {time_until_buy}분 남음)")
            # 30분 이상 남으면 상세 분석, 10분 이하면 빠른 분석
            if time_until_buy <= 10:
                send_telegram("⚡ 시간 촉박 - 빠른 분석 모드")
        else:
            send_telegram(f"🔍 뉴스 수집 시작 (01:30 지남 - 즉시 매수 예정)")

        # 뉴스 수집
        news_collection_start = time.time()
        news_items = get_market_news()

        # 남은 시간 계산해서 기사 본문 읽을지 결정
        now = nz_now()
        current_time = now.hour * 60 + now.minute
        time_until_buy = BUY_TIME - current_time
        # 10분 이상 남으면 기사 본문 분석, 아니면 키워드만
        do_full_analysis = time_until_buy >= 10

        if do_full_analysis:
            send_telegram("📰 기사 본문 + 애널리스트 등급 분석 중...")
            top_30 = analyze_news_and_pick_stocks(news_items)
        else:
            send_telegram("⚡ 키워드 빠른 분석 중...")
            top_30 = analyze_news_quick(news_items)

        watchlist = top_30.copy()

        message = "📊 오늘의 추천 종목 (상위 10개)\n\n"
        for i, stock in enumerate(top_30[:10], 1):
            sector = SECTOR_MAP.get(stock, "기타")
            message += f"{i}위: {stock} ({sector})\n"
        message += f"\n✅ 매수 예정: 1~{config.TOP_N_BUY}위"
        send_telegram(message)

        # 01:30 이전이면 대기
        now = nz_now()
        current_time = now.hour * 60 + now.minute
        if current_time < BUY_TIME:
            wait_seconds = (BUY_TIME - current_time) * 60
            send_telegram(f"⏳ 01:30 장 시작까지 {wait_seconds//60}분 {wait_seconds%60}초 대기...")
            time.sleep(wait_seconds)

        # 매수 전 보유 주식 확인 및 처리
        send_telegram("🔔 01:30 장 시작 → 보유 주식 확인 중...")
        start_equity = get_equity()
        new_top2 = watchlist[:config.TOP_N_BUY]

        try:
            existing_positions = trading_client.get_all_positions()
            if existing_positions:
                existing_symbols = [pos.symbol for pos in existing_positions]
                to_sell = []
                to_keep = []

                for sym in existing_symbols:
                    if sym in new_top2:
                        to_keep.append(sym)
                        send_telegram(f"✅ {sym} 순위 유지 → 보유 계속")
                    else:
                        to_sell.append(sym)

                # 순위 밖 종목 매도
                for sym in to_sell:
                    send_telegram(f"🔄 {sym} 순위 변동 → 매도 후 교체")
                    sell_stock(sym, "순위 변동 교체")
                    time.sleep(1)

                # 새로 매수할 종목 (보유 중인 건 제외)
                buy_list = [s for s in new_top2 if s not in to_keep]
                watchlist = [s for s in watchlist[config.TOP_N_BUY:] if s not in to_keep]
            else:
                buy_list = new_top2
                watchlist = watchlist[config.TOP_N_BUY:]
        except Exception as e:
            print(f"보유 주식 확인 오류: {e}")
            buy_list = new_top2
            watchlist = watchlist[config.TOP_N_BUY:]

        if buy_list:
            buy_stocks(buy_list)
        else:
            send_telegram("✅ 기존 보유 종목과 순위 동일 → 매수 없음")

    # 07:30까지 모니터링 루프
    send_telegram(f"👀 모니터링 시작 (5분 간격 체크, 30분마다 보고, 07:00 자동 매도)")
    last_monitor = time.time()
    last_report = time.time()

    while True:
        now = nz_now()
        current_time = now.hour * 60 + now.minute

        # 07:30 도달 → 전량 매도 후 종료 (5번 재시도)
        if current_time >= MARKET_CLOSE:
            send_telegram("⏰ 07:00 장 마감 → 전량 매도 시작")
            success = False
            for attempt in range(5):
                try:
                    sell_all_stocks("장 마감")
                    success = True
                    break
                except Exception as e:
                    send_telegram(f"⚠️ 매도 실패 ({attempt+1}/5): {e}\n10초 후 재시도...")
                    time.sleep(10)
            if success:
                send_telegram("🔒 모든 포지션 정리 완료. 프로그램 종료합니다.")
            else:
                send_telegram("🚨 [긴급] 5회 시도에도 매도 실패! 수동 확인 필요!")
            sys.exit(0)

        # 5분마다 모니터링
        if time.time() - last_monitor >= config.CHECK_INTERVAL * 60:
            result = monitor_once()
            last_monitor = time.time()
            if result == "done":
                send_telegram("🔒 오늘 거래 완료. 프로그램 종료합니다.")
                sys.exit(0)

        # 30분마다 상태 보고
        if time.time() - last_report >= 30 * 60:
            if held_stocks:
                status = "📊 30분 현황 보고\n"
                for sym in held_stocks:
                    price = get_price(sym)
                    if price:
                        bp = buy_prices.get(sym, price)
                        pct = ((price - bp) / bp * 100)
                        news_status = "악재주시중" if sym in news_watch else "정상"
                        status += f"  {sym}: ${price:.2f} ({'+'if pct>=0 else ''}{pct:.1f}%) [{news_status}]\n"
                send_telegram(status)
            last_report = time.time()

        time.sleep(30)
