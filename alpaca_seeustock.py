import time
import schedule
import requests
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

def send_telegram(message):
    print(f"[알림] {message}")
    # 여러 방법으로 텔레그램 전송 시도
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
                print(f"[텔레그램 전송 성공] endpoint {i+1}")
                return
        except Exception as e:
            print(f"[텔레그램 시도 {i+1} 실패] {e}")
    print("[텔레그램 전송 실패]")

try:
    trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING)
    data_client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    print('[연결] Alpaca 연결 성공')
except Exception as e:
    print(f'[연결 오류] Alpaca 연결 실패: {e}')
    import sys
    sys.exit(1)

US_HOLIDAYS = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25"
]

# 섹터 맵 - 같은 섹터 종목 교체 방지용
SECTOR_MAP = {
    "NVDA": "semiconductor", "AMD": "semiconductor", "INTC": "semiconductor", "QCOM": "semiconductor",
    "AAPL": "bigtech", "MSFT": "bigtech", "GOOGL": "bigtech", "META": "bigtech", "AMZN": "bigtech",
    "TSLA": "ev", "RIVN": "ev", "LCID": "ev", "F": "auto", "GM": "auto", "NKLA": "ev",
    "LMT": "defense", "RTX": "defense", "NOC": "defense", "GD": "defense", "KTOS": "defense", "BBAI": "defense",
    "MARA": "crypto", "RIOT": "crypto", "CLSK": "crypto", "COIN": "crypto", "MSTR": "crypto", "BITF": "crypto",
    "XOM": "energy", "CVX": "energy", "COP": "energy", "TELL": "energy", "SWN": "energy",
    "PLUG": "cleanenergy", "FCEL": "cleanenergy", "BLNK": "cleanenergy", "ENPH": "cleanenergy",
    "JPM": "finance", "BAC": "finance", "GS": "finance", "MS": "finance", "SOFI": "finance",
    "JNJ": "pharma", "PFE": "pharma", "MRK": "pharma", "LLY": "pharma", "MRNA": "biotech", "NVAX": "biotech",
    "VALE": "mining", "FCX": "mining", "CLF": "steel", "NEM": "gold", "GLD": "gold", "GORO": "gold",
    "DAL": "airline", "UAL": "airline", "AAL": "airline",
    "RKLB": "space", "SPCE": "space", "ASTR": "space",
    "RGTI": "quantum", "QUBT": "quantum", "IONQ": "quantum",
    "SOUN": "aivoice", "AITX": "aivoice",
    "WMT": "retail", "AMZN": "retail", "COST": "retail",
    "NOK": "telecom",
}

def is_market_open_today():
    # 미국 시간 기준으로 요일과 공휴일 체크
    us_tz = pytz.timezone('America/New_York')
    us_now = datetime.now(us_tz)
    # 미국 기준 주말 체크
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
                    ratings.append({'symbol': symbol, 'score': score, 'action': action, 'rating': rating})
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
            if '/news/' in a.get('href','') and len(a.get_text().strip()) > 20:
                href = a['href']
                if not href.startswith('http'):
                    href = 'https://finance.yahoo.com' + href
                news_items.append({'title': a.get_text().strip(), 'url': href, 'source': 'yahoo'})
    except Exception as e:
        print(f"[Yahoo 오류] {e}")
    try:
        url = "https://finviz.com/news.ashx"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', class_='nn-tab-link'):
            if a.get_text().strip():
                news_items.append({'title': a.get_text().strip(), 'url': a.get('href',''), 'source': 'finviz'})
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
                    news_items.append({'title': t.strip(), 'url': '', 'source': 'reddit'})
    except Exception as e:
        print(f"[Reddit 오류] {e}")
    return news_items[:50]

def score_article(title, content):
    combined = (title + ' ' + content).lower()
    score = 0
    positive = [('strong buy',3),('upgraded to buy',3),('outperform',2),('overweight',2),
                ('price target raised',2),('target raised',2),('beat estimates',2),
                ('earnings beat',2),('revenue beat',2),('raised guidance',2),
                ('record revenue',2),('fda approval',3),('new contract',1),
                ('bullish',1),('strong demand',1),('market share gain',2)]
    negative = [('strong sell',-3),('downgraded',-3),('underperform',-2),('underweight',-2),
                ('price target cut',-2),('missed estimates',-2),('earnings miss',-2),
                ('lowered guidance',-2),('investigation',-3),('lawsuit',-3),
                ('fraud',-3),('recall',-2),('bankruptcy',-3),('layoffs',-1),
                ('disappointing',-1),('guidance cut',-2)]
    for kw, pts in positive:
        if kw in combined: score += pts
    for kw, pts in negative:
        if kw in combined: score += pts
    return score

def get_market_news_list():
    """기존 코드 호환용 - 텍스트 리스트 반환"""
    items = get_market_news()
    return [item['title'] for item in items]

def analyze_news_sentiment(news_list, symbol):
    """개별 종목 뉴스 감성 분석 - 긍정/부정/중립"""
    positive_words = ["surge", "jump", "rise", "gain", "beat", "exceed", "record", "high",
                     "growth", "profit", "revenue", "upgrade", "buy", "bullish", "strong",
                     "win", "contract", "deal", "approval", "approved", "partnership"]
    negative_words = ["crash", "fall", "drop", "loss", "bankrupt", "lawsuit", "fraud",
                     "recall", "cut", "miss", "decline", "investigation", "sec",
                     "delisted", "halt", "warning", "downgrade", "sell", "bearish",
                     "weak", "disappoint", "fail", "reject", "layoff", "fine"]

    positive_score = 0
    negative_score = 0

    for news in news_list:
        if symbol.lower() in news.lower():
            news_lower = news.lower()
            for word in positive_words:
                if word in news_lower:
                    positive_score += 1
            for word in negative_words:
                if word in news_lower:
                    negative_score += 1

    if positive_score > negative_score:
        return "positive", positive_score - negative_score
    elif negative_score > positive_score:
        return "negative", negative_score - positive_score
    else:
        return "neutral", 0

def analyze_news_and_pick_stocks(news_list):
    stock_map = {
        # 반도체/AI
        "nvidia": ["NVDA"], "semiconductor": ["NVDA", "AMD", "INTC", "QCOM"],
        "chip": ["NVDA", "AMD", "INTC", "QCOM"], "artificial intelligence": ["NVDA", "MSFT", "GOOGL", "META", "AMD"],
        "AI": ["NVDA", "MSFT", "GOOGL", "META", "AMD", "BBAI", "SOUN"],
        "chatgpt": ["MSFT", "NVDA", "GOOGL"], "openai": ["MSFT", "NVDA"],
        "quantum": ["RGTI", "QUBT", "IONQ"], "quantum computing": ["RGTI", "QUBT", "IONQ"],
        # 빅테크
        "apple": ["AAPL"], "microsoft": ["MSFT"], "google": ["GOOGL"],
        "amazon": ["AMZN"], "meta": ["META"], "tesla": ["TSLA"],
        "tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA"],
        # 트럼프/정치
        "trump": ["BBAI", "KTOS", "LMT", "RTX", "TELL", "VALE"],
        "tariff": ["AAPL", "NVDA", "TSLA", "VALE", "CLF", "AAL"],
        "tariff exemption": ["NVDA", "AAPL", "TSLA", "AMD"],
        "trade deal": ["NVDA", "AAPL", "TSLA", "AMZN"],
        "trade war": ["AAPL", "NVDA", "VALE", "CLF"],
        "sanction": ["VALE", "CLF", "TELL", "LMT"],
        "white house": ["BBAI", "KTOS", "LMT", "RTX"],
        # 방산 - 전쟁 반대편 수혜
        "war": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "conflict": ["LMT", "RTX", "NOC", "BBAI", "KTOS"],
        "ukraine": ["LMT", "RTX", "VALE", "CLF", "NOK"],
        "russia": ["LMT", "RTX", "VALE", "TELL"],
        "middle east": ["LMT", "RTX", "TELL", "SWN"],
        "israel": ["LMT", "RTX", "BBAI", "KTOS"],
        "iran": ["LMT", "RTX", "TELL", "XOM"],
        "military": ["LMT", "RTX", "NOC", "GD"],
        "defense": ["LMT", "RTX", "NOC", "GD", "BBAI", "KTOS"],
        "pentagon": ["LMT", "RTX", "NOC", "GD"],
        # 금융
        "bank": ["JPM", "BAC", "GS", "MS", "SOFI"],
        "federal reserve": ["JPM", "BAC", "GS", "SOFI"],
        "interest rate": ["JPM", "BAC", "SOFI"],
        "rate cut": ["JPM", "BAC", "SOFI", "BLNK"],
        "inflation": ["JPM", "XOM", "CVX", "VALE", "GLD"],
        "recession": ["GLD", "JPM", "WMT"],
        # 에너지
        "oil": ["XOM", "CVX", "COP", "TELL"],
        "energy": ["XOM", "CVX", "PLUG", "FCEL", "TELL"],
        "natural gas": ["TELL", "SWN", "XOM"],
        "opec": ["XOM", "CVX", "COP"],
        "solar": ["PLUG", "FCEL", "BLNK", "ENPH"],
        "clean energy": ["PLUG", "FCEL", "BLNK"],
        "hydrogen": ["PLUG", "FCEL"],
        # EV
        "electric vehicle": ["TSLA", "RIVN", "LCID", "F", "GM"],
        "EV": ["TSLA", "RIVN", "LCID", "F"],
        "rivian": ["RIVN"], "lucid": ["LCID"],
        "charging": ["BLNK", "EVGO", "CHPT"],
        # 바이오
        "biotech": ["MRNA", "BNTX", "NVAX", "AGEN"],
        "drug": ["JNJ", "PFE", "MRK", "ABBV", "LLY"],
        "pharma": ["JNJ", "PFE", "MRK", "LLY"],
        "FDA": ["MRNA", "NVAX", "AGEN"],
        "vaccine": ["MRNA", "BNTX", "NVAX"],
        "weight loss": ["LLY", "NVO"],
        "ozempic": ["LLY", "NVO"],
        # 원자재
        "gold": ["GLD", "GORO", "NEM"],
        "silver": ["SLV", "HYMC"],
        "mining": ["VALE", "FCX", "NEM"],
        "copper": ["FCX", "VALE"],
        "lithium": ["ALB", "LTHM", "LAC"],
        "steel": ["CLF", "X", "NUE"],
        # 우주/항공
        "space": ["RKLB", "SPCE", "ASTR"],
        "rocket": ["RKLB", "ASTR"],
        "nasa": ["RKLB", "LMT"],
        "airline": ["DAL", "UAL", "AAL"],
        "aviation": ["DAL", "UAL", "JOBY"],
        "travel": ["DAL", "UAL", "ABNB"],
        # 암호화폐
        "bitcoin": ["MARA", "RIOT", "CLSK", "COIN", "MSTR"],
        "crypto": ["MARA", "RIOT", "COIN", "MSTR"],
        "ethereum": ["MARA", "RIOT", "COIN"],
        "coinbase": ["COIN"],
        # 시장 하락 수혜
        "crash": ["GLD", "SLV", "GORO"],
        "bear market": ["GLD", "SLV"],
        "sell off": ["GLD", "LMT", "RTX"],
        # 개별
        "sofi": ["SOFI"], "marathon": ["MARA"], "riot": ["RIOT"],
        "sound hound": ["SOUN"], "soundhound": ["SOUN"],
        "bigbear": ["BBAI"], "rigetti": ["RGTI"],
        "nokia": ["NOK"], "ford": ["F"],
        "5G": ["NOK", "QCOM"],
    }

    # 1단계: 키워드 기반 후보 종목 선정 (30개)
    keyword_scores = {}
    for item in news_list:
        news_lower = item['title'].lower() if isinstance(item, dict) else item.lower()
        for keyword, stocks in stock_map.items():
            if keyword.lower() in news_lower:
                for stock in stocks:
                    keyword_scores[stock] = keyword_scores.get(stock, 0) + 1

    sorted_candidates = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)
    candidates = [s for s, _ in sorted_candidates[:30]]

    default_stocks = ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA", "JPM", "AMD", "SOFI"]
    for s in default_stocks:
        if s not in candidates:
            candidates.append(s)
        if len(candidates) == 30:
            break

    # 2단계: 기사 본문 + 애널리스트 등급으로 정밀 점수 계산
    print(f"[분석] 후보 {len(candidates)}개 종목 정밀 분석 시작...")
    analyst_ratings = get_analyst_ratings()
    
    final_scores = {}
    for symbol in candidates:
        total = 0
        
        # 뉴스 본문 분석
        for item in news_list:
            if isinstance(item, dict):
                title = item.get('title','')
                url = item.get('url','')
            else:
                title = item
                url = ''
            
            if symbol.lower() in title.lower():
                content = get_full_article_text(url) if url else ''
                total += score_article(title, content)
                time.sleep(0.2)
        
        # 애널리스트 등급 점수 (가중치 1.5배)
        analyst_score = sum(r['score'] for r in analyst_ratings if r['symbol'] == symbol)
        total += analyst_score * 1.5
        
        final_scores[symbol] = total

    # 3단계: 섹터 다양성 확보하면서 상위 30개 선정
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

    # 부족하면 채우기
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
buy_amounts = {}  # 매수 금액 기록
sell_records = []  # 매도 기록
trading_done_today = False
blacklist_today = []  # 당일 악재 종목 블랙리스트

def get_price(symbol):
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = data_client.get_stock_latest_quote(req)
        price = float(quote[symbol].ask_price or quote[symbol].bid_price)
        return price
    except Exception as e:
        print(f"[가격조회 오류] {symbol}: {e}")
    return None

def get_equity():
    try:
        account = trading_client.get_account()
        return float(account.equity)
    except:
        return 0

def get_cash():
    try:
        account = trading_client.get_account()
        return float(account.cash)
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
                market_val = sell_amt

                order = MarketOrderRequest(
                    symbol=symbol,
                    qty=float(pos.qty),
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY
                )
                trading_client.submit_order(order)

                # 매도 기록 저장
                sell_records.append({
                    "symbol": symbol,
                    "buy_price": buy_prices.get(symbol, 0),
                    "sell_price": sell_price,
                    "buy_amount": buy_amt,
                    "sell_amount": sell_amt,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "reason": reason
                })

                if symbol in held_stocks:
                    held_stocks.remove(symbol)
                send_telegram(f"📤 매도: {symbol}\n매수가: ${buy_prices.get(symbol,0):.2f} → 매도가: ${sell_price:.2f}\n손익: {'+'if pnl>=0 else ''}{pnl:.2f} ({pnl_pct:.1f}%)\n사유: {reason}")
                return True
    except Exception as e:
        send_telegram(f"❌ {symbol} 매도 실패: {e}")
    return False

def sell_all_stocks(reason="장 마감"):
    global held_stocks, trading_done_today
    send_telegram(f"🔔 전량 매도 시작 ({reason})...")
    try:
        positions = trading_client.get_all_positions()
        for pos in positions:
            sell_stock(pos.symbol, reason)
            time.sleep(1)
    except Exception as e:
        send_telegram(f"❌ 포지션 조회 실패: {e}")

    held_stocks = []
    trading_done_today = True
    time.sleep(3)
    send_daily_report()

def send_daily_report():
    """마감 보고 - 종목별 상세 내역 포함"""
    try:
        end_equity = get_equity()
        pnl = end_equity - start_equity
        pnl_pct = (pnl / start_equity * 100) if start_equity > 0 else 0
        emoji = "📈" if pnl >= 0 else "📉"

        report = f"{emoji} 오늘의 매매 결과\n"
        report += f"{'='*25}\n"
        report += f"시작 잔액: ${start_equity:,.2f}\n"
        report += f"마감 잔액: ${end_equity:,.2f}\n"
        report += f"총 손익: {'+'if pnl>=0 else ''}{pnl:,.2f} USD\n"
        report += f"수익률: {'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%\n"
        report += f"{'='*25}\n"
        report += f"📋 종목별 내역\n\n"

        for record in sell_records:
            r_emoji = "📈" if record['pnl'] >= 0 else "📉"
            report += f"{r_emoji} {record['symbol']}\n"
            report += f"  매수: ${record['buy_amount']:.2f} @ ${record['buy_price']:.2f}\n"
            report += f"  매도: ${record['sell_amount']:.2f} @ ${record['sell_price']:.2f}\n"
            report += f"  손익: {'+'if record['pnl']>=0 else ''}{record['pnl']:.2f} ({record['pnl_pct']:.1f}%)\n"
            report += f"  사유: {record['reason']}\n\n"

        send_telegram(report)
    except Exception as e:
        send_telegram(f"✅ 전량 매도 완료 (보고서 오류: {e})")

def find_next_stock(current_symbol):
    """교체 매수 종목 찾기 - 같은 섹터 제외, 뉴스 더블체크"""
    global watchlist, blacklist_today

    current_sector = SECTOR_MAP.get(current_symbol, "other")
    news_list = get_market_news_list()

    for candidate in watchlist:
        if candidate in blacklist_today:
            continue
        if candidate in held_stocks:
            continue

        candidate_sector = SECTOR_MAP.get(candidate, "other")

        # 같은 섹터면 건너뛰기
        if candidate_sector == current_sector:
            send_telegram(f"⏭️ {candidate} 건너뜀 (같은 섹터: {current_sector})")
            continue

        # 뉴스 감성 체크
        sentiment, score = analyze_news_sentiment(news_list, candidate)
        if sentiment == "negative":
            send_telegram(f"⏭️ {candidate} 건너뜀 (악재 뉴스 감지)")
            blacklist_today.append(candidate)
            continue

        # 통과 → 이 종목으로 교체
        watchlist.remove(candidate)
        return candidate

    return None

def monitor_positions():
    global held_stocks, watchlist, trading_done_today, blacklist_today

    if trading_done_today or not held_stocks:
        return

    try:
        current_equity = get_equity()
        if start_equity > 0:
            total_pnl_pct = ((current_equity - start_equity) / start_equity) * 100

            # 전체 +10% → 전량 매도 종료
            if total_pnl_pct >= config.PROFIT_TARGET:
                send_telegram(f"🎯 목표 달성! 전체 +{total_pnl_pct:.1f}% → 전량 매도!")
                sell_all_stocks("목표 수익 달성")
                return

            # 전체 -8% → 손절
            if total_pnl_pct <= -5.0:
                send_telegram(f"🛑 전체 손절! {total_pnl_pct:.1f}%")
                sell_all_stocks("전체 손절")
                return

        news_list = get_market_news_list()

        for symbol in held_stocks.copy():
            try:
                price = get_price(symbol)
                if not price:
                    continue

                buy_price = buy_prices.get(symbol, price)
                profit_pct = ((price - buy_price) / buy_price) * 100

                # 개별 +10% → 수익 확정 매도 + 교체
                if profit_pct >= 10:
                    send_telegram(f"🎯 {symbol} 개별 목표 달성! +{profit_pct:.1f}%")
                    sell_stock(symbol, "개별 목표 달성 +10%")
                    next_stock = find_next_stock(symbol)
                    if next_stock:
                        send_telegram(f"🔄 교체 매수: {next_stock}")
                        buy_stocks([next_stock])
                    continue

                # 개별 -5% → 손절 + 교체 (뉴스 상관없이)
                if profit_pct <= config.DROP_THRESHOLD:
                    sentiment, score = analyze_news_sentiment(news_list, symbol)
                    blacklist_today.append(symbol)
                    send_telegram(f"🚨 {symbol} 손절! {profit_pct:.1f}% (뉴스: {sentiment})")
                    sell_stock(symbol, f"손절 {profit_pct:.1f}%")
                    next_stock = find_next_stock(symbol)
                    if next_stock:
                        send_telegram(f"🔄 교체 매수: {next_stock}")
                        buy_stocks([next_stock])
                    continue

                # 상승 알림
                if profit_pct >= 5:
                    send_telegram(f"📈 {symbol} +{profit_pct:.1f}% 상승 중!")
                elif profit_pct <= -3:
                    send_telegram(f"📉 {symbol} {profit_pct:.1f}% 하락 중...")

            except Exception as e:
                print(f"[모니터링 오류] {symbol}: {e}")

    except Exception as e:
        print(f"[모니터링 오류] {e}")

def pre_market_job():
    global watchlist, trading_done_today, blacklist_today, sell_records
    trading_done_today = False
    blacklist_today = []
    sell_records = []

    if not is_market_open_today():
        send_telegram("📅 오늘은 미국 장이 열리지 않아요")
        return

    send_telegram("🔍 뉴스 수집 시작...")
    news_list = get_market_news_list()
    top_30 = analyze_news_and_pick_stocks(news_list)
    watchlist = top_30.copy()

    message = "📊 오늘의 추천 종목 (상위 10개)\n\n"
    for i, stock in enumerate(top_30[:10], 1):
        sector = SECTOR_MAP.get(stock, "기타")
        message += f"{i}위: {stock} ({sector})\n"
    message += f"\n✅ 매수 예정: 1~2위"
    message += f"\n⏳ 대기: 3~10위 (교체용)"
    send_telegram(message)

def market_open_job():
    global watchlist, start_equity, buy_prices, buy_amounts, trading_done_today
    if not is_market_open_today() or trading_done_today:
        return

    start_equity = get_equity()
    buy_prices = {}
    buy_amounts = {}

    if not watchlist:
        send_telegram("❌ 추천 종목 없음.")
        return

    buy_list = watchlist[:2]
    watchlist = watchlist[2:]
    buy_stocks(buy_list)

def market_close_job():
    if not is_market_open_today():
        return
    if not trading_done_today:
        sell_all_stocks("장 마감")
    # 매도 완료 후 프로그램 종료
    send_telegram("🔒 오늘 거래 완료. 프로그램 종료합니다.")
    import sys
    sys.exit(0)

schedule.every().day.at("01:00").do(pre_market_job)
schedule.every().day.at("01:30").do(market_open_job)
schedule.every(config.CHECK_INTERVAL).minutes.do(monitor_positions)
schedule.every().day.at("07:30").do(market_close_job)

if __name__ == "__main__":
    send_telegram("🚀 SeeuStock 자동매매 시작!")
    try:
        existing_positions = trading_client.get_all_positions()
        if existing_positions:
            for pos in existing_positions:
                symbol = pos.symbol
                if symbol not in held_stocks:
                    held_stocks.append(symbol)
                    buy_prices[symbol] = float(pos.avg_entry_price)
                    buy_amounts[symbol] = float(pos.market_value)
            start_equity = get_equity()
            pos_list = ", ".join([pos.symbol for pos in existing_positions])
            send_telegram("📋 기존 포지션: " + pos_list)
    except Exception as e:
        print(f"포지션 로드 오류: {e}")
    print("SeeuStock 자동매매 실행 중...")
    print("종료하려면 Ctrl+C 를 누르세요.")
    while True:
        schedule.run_pending()
        time.sleep(30)
