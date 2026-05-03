import time
import requests
import sys
from datetime import datetime
from bs4 import BeautifulSoup
import pytz
import os

# Tiger Brokers API
from tigeropen.common.consts import Language, Market, BarPeriod, QuoteRight
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.trade.trade_client import TradeClient
from tigeropen.quote.quote_client import QuoteClient
from tigeropen.common.consts import OrderType, OrderStatus

# ============================================
# 설정
# ============================================
TIGER_TIGER_ID = os.environ.get('TIGER_ID', '')
TIGER_PRIVATE_KEY = os.environ.get('TIGER_PRIVATE_KEY', '')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

PROFIT_TARGET = 10.0    # 수수료 포함 수익 목표 +10%
STOP_LOSS = -3.0        # 수수료 포함 손절 -3%
CHECK_INTERVAL = 5      # 모니터링 간격 (분)

NZ_TZ = pytz.timezone('Pacific/Auckland')
HK_TZ = pytz.timezone('Asia/Hong_Kong')

# 섹터 맵
SECTOR_MAP = {
    "00700": "tech", "09988": "tech", "03690": "tech", "09618": "tech",
    "00005": "finance", "02388": "finance", "00939": "finance", "01398": "finance", "03968": "finance",
    "00941": "telecom", "00762": "telecom",
    "02318": "insurance", "02628": "insurance", "01336": "insurance",
    "00883": "energy", "00386": "energy", "00857": "energy",
    "01211": "ev", "02015": "ev", "09863": "ev",
    "01810": "hardware", "00992": "hardware",
    "00016": "property", "00101": "property", "02007": "property",
    "00388": "exchange",
}

# 상태 변수
held_stocks = {}       # {symbol: {'qty': qty, 'buy_price': price, 'buy_amount': amount}}
watchlist = []
start_equity = 0
sell_records = []
blacklist_today = []
afternoon_news_done = False
trading_done_today = False

# ============================================
# 텔레그램
# ============================================
def send_telegram(message):
    print(f"[알림] {message}")
    urls = [
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        f"https://api1.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        f"https://api2.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    ]
    for i, url in enumerate(urls):
        try:
            response = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=15,
                verify=False
            )
            if response.status_code == 200:
                return
        except Exception as e:
            print(f"[텔레그램 시도 {i+1} 실패] {e}")

# ============================================
# Tiger Brokers 연결
# ============================================
def get_tiger_clients():
    try:
        client_config = TigerOpenClientConfig(sandbox_debug=False)
        client_config.tiger_id = TIGER_TIGER_ID
        # Private Key 형식 정규화
        pk = TIGER_PRIVATE_KEY.strip()
        if not pk.startswith('-----BEGIN'):
            # 헤더/푸터 없는 경우 추가
            pk = "-----BEGIN RSA PRIVATE KEY-----\n" + pk + "\n-----END RSA PRIVATE KEY-----"
        client_config.private_key = pk
        client_config.language = Language.en_US
        trade_client = TradeClient(client_config)
        quote_client = QuoteClient(client_config)
        print("[연결] Tiger Brokers 연결 성공")
        return trade_client, quote_client
    except Exception as e:
        print(f"[연결 오류] {e}")
        sys.exit(1)

trade_client, quote_client = get_tiger_clients()

# ============================================
# 시장 오픈 확인
# ============================================
def is_hk_market_open():
    hk_now = datetime.now(HK_TZ)
    if hk_now.weekday() >= 5:
        return False
    return True

def nz_now():
    return datetime.now(NZ_TZ)

def nz_time_minutes():
    now = nz_now()
    return now.hour * 60 + now.minute

# ============================================
# 뉴스 수집
# ============================================
def get_hk_news():
    news_items = []
    # Yahoo Finance HK
    try:
        url = "https://finance.yahoo.com/topic/stock-market-news/"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            title = a.get_text().strip()
            if len(title) > 20:
                news_items.append({'title': title, 'url': a.get('href', ''), 'source': 'yahoo'})
    except Exception as e:
        print(f"[Yahoo 오류] {e}")

    # Reuters Asia
    try:
        url = "https://www.reuters.com/markets/asia/"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            title = a.get_text().strip()
            if len(title) > 20:
                news_items.append({'title': title, 'url': '', 'source': 'reuters'})
    except Exception as e:
        print(f"[Reuters 오류] {e}")

    # SCMP
    try:
        url = "https://www.scmp.com/business/markets"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            title = a.get_text().strip()
            if len(title) > 20:
                news_items.append({'title': title, 'url': '', 'source': 'scmp'})
    except Exception as e:
        print(f"[SCMP 오류] {e}")

    return news_items[:60]

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

def score_article(title, content):
    combined = (title + ' ' + content).lower()
    score = 0
    positive = [
        ('strong buy', 3), ('upgraded', 3), ('outperform', 2), ('overweight', 2),
        ('price target raised', 2), ('beat estimates', 2), ('earnings beat', 2),
        ('revenue beat', 2), ('raised guidance', 2), ('record revenue', 2),
        ('new contract', 2), ('partnership', 1), ('approval', 2), ('bullish', 1),
        ('stimulus', 2), ('rate cut', 2), ('growth', 1), ('profit', 1),
        ('china recovery', 2), ('trade deal', 2), ('upgrade', 2),
    ]
    negative = [
        ('strong sell', -3), ('downgraded', -3), ('underperform', -2), ('underweight', -2),
        ('price target cut', -2), ('missed estimates', -2), ('earnings miss', -2),
        ('lowered guidance', -2), ('investigation', -3), ('lawsuit', -3),
        ('fraud', -3), ('recall', -2), ('bankruptcy', -3), ('disappointing', -1),
        ('trade war', -2), ('sanctions', -2), ('tariff', -1), ('delisted', -3),
        ('sell off', -2), ('bearish', -1),
    ]
    for kw, pts in positive:
        if kw in combined:
            score += pts
    for kw, pts in negative:
        if kw in combined:
            score += pts
    return score

# ============================================
# 홍콩 저가종목 분석
# ============================================
def get_hk_penny_stocks():
    """홍콩 저가종목 후보 - 뉴스 호재 기반"""
    # 섹터별 저가 종목 맵
    stock_map = {
        # 중국 경제/부양책
        "china stimulus": ["01800", "03323", "00688", "02600"],
        "china recovery": ["01800", "03323", "00688", "02600", "01088"],
        "china property": ["03333", "02007", "00688", "01109"],
        # 기술
        "artificial intelligence": ["00020", "00268", "01024", "09888"],
        "AI": ["00020", "00268", "01024", "09888"],
        "semiconductor": ["00981", "02382", "06869"],
        "tech": ["00020", "00268", "01024", "09888", "00981"],
        # 에너지
        "oil": ["00883", "00386", "01088"],
        "energy": ["00883", "00386", "01088", "03800"],
        "solar": ["03800", "00968", "02380"],
        "renewable": ["03800", "00968", "02380"],
        # EV/전기차
        "electric vehicle": ["01211", "02015", "09863", "01958"],
        "EV": ["01211", "02015", "09863", "01958"],
        "battery": ["01211", "02015", "06797"],
        # 금융
        "rate cut": ["00005", "02388", "00939", "01398"],
        "federal reserve": ["00005", "02388", "00939"],
        "bank": ["00005", "02388", "00939", "01398", "03968"],
        # 광업/원자재
        "gold": ["02899", "01818", "03330"],
        "copper": ["02899", "01208"],
        "mining": ["02899", "01208", "01818"],
        "iron ore": ["02388", "01088"],
        # 헬스케어
        "biotech": ["01177", "02269", "06160", "09926"],
        "drug": ["01177", "02269", "06160"],
        "vaccine": ["02269", "06160"],
        # 미중 관계
        "trade war": ["00941", "09988", "00700"],
        "tariff": ["09988", "00700", "01211"],
        "trade deal": ["09988", "00700", "01211", "00005"],
        "trump": ["00883", "00386", "01088", "00941"],
        # 부동산
        "property": ["03333", "02007", "00688", "01109"],
        "real estate": ["03333", "02007", "00688"],
        # 여행/소비
        "tourism": ["00293", "00670", "01308"],
        "consumer": ["00291", "00992", "01929"],
        # 홍콩 특화
        "hong kong": ["00005", "00016", "00388", "02388"],
        "hkex": ["00388"],
        "hang seng": ["00005", "00016", "00388"],
    }
    return stock_map

def analyze_hk_stocks(news_items, time_limit_minutes=25):
    """홍콩 저가종목 분석 및 순위 산출"""
    stock_map = get_hk_penny_stocks()
    keyword_scores = {}

    for item in news_items:
        news_lower = item['title'].lower()
        for keyword, stocks in stock_map.items():
            if keyword.lower() in news_lower:
                for stock in stocks:
                    keyword_scores[stock] = keyword_scores.get(stock, 0) + 1

    # 시간 여유 있으면 기사 본문 분석
    final_scores = {}
    start_time = time.time()

    for symbol, base_score in keyword_scores.items():
        total = base_score
        elapsed = (time.time() - start_time) / 60
        if elapsed < time_limit_minutes - 5:
            for item in news_items[:20]:
                if item.get('url'):
                    content = get_full_article_text(item['url'])
                    total += score_article(item['title'], content) * 0.5
                    time.sleep(0.2)
        final_scores[symbol] = total

    # 기본 저가 우량 종목 추가
    defaults = ["00020", "00268", "03800", "00968", "02899", "01177", "00293", "01800", "03323"]
    for s in defaults:
        if s not in final_scores:
            final_scores[s] = 0

    # 섹터 다양성 확보하며 상위 30개
    sorted_stocks = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    top_30 = []
    used_sectors = {}
    for stock, score in sorted_stocks:
        sector = SECTOR_MAP.get(stock, "other")
        if used_sectors.get(sector, 0) < 2:
            top_30.append(stock)
            used_sectors[sector] = used_sectors.get(sector, 0) + 1
        if len(top_30) == 30:
            break

    return top_30

# ============================================
# 가격 조회
# ============================================
def get_hk_price(symbol):
    try:
        # Tiger Brokers API로 HK 주식 가격 조회
        symbols = [f"{symbol}.HK"]
        briefs = quote_client.get_stock_briefs(symbols=symbols)
        if briefs and len(briefs) > 0:
            return float(briefs[0].latest_price)
    except Exception as e:
        print(f"[가격조회 오류] {symbol}: {e}")
    return None

def get_hkd_balance():
    try:
        assets = trade_client.get_assets()
        for asset in assets:
            if hasattr(asset, 'cash') and asset.currency == 'HKD':
                return float(asset.cash)
        # 전체 잔액에서 HKD 찾기
        return float(assets[0].cash) if assets else 0
    except Exception as e:
        print(f"[잔액조회 오류] {e}")
        return 0

def get_total_equity():
    try:
        assets = trade_client.get_assets()
        return float(assets[0].net_liquidation) if assets else 0
    except:
        return 0

# ============================================
# 매수 / 매도
# ============================================
def buy_hk_stock(symbol, amount_hkd):
    """HKD 금액 기준 매수"""
    try:
        price = get_hk_price(symbol)
        if not price or price <= 0:
            send_telegram(f"⚠️ {symbol} 가격 조회 실패")
            return False

        # 홍콩 최소 거래 단위 계산
        # lot size는 종목마다 다름 - 일단 100주 기준
        lot_size = 1000  # 저가종목은 lot size가 크게 설정됨
        if price >= 0.5:
            lot_size = 500
        if price >= 2:
            lot_size = 100

        qty_raw = amount_hkd / price
        lots = max(1, int(qty_raw / lot_size))
        qty = lots * lot_size
        actual_amount = qty * price

        order = trade_client.place_order(
            symbol=f"{symbol}.HK",
            order_type=OrderType.MKT,
            side='BUY',
            quantity=qty
        )

        held_stocks[symbol] = {
            'qty': qty,
            'buy_price': price,
            'buy_amount': actual_amount,
            'lot_size': lot_size
        }
        send_telegram(f"✅ 매수: {symbol}\n수량: {qty}주 @ HK${price:.3f}\n금액: HK${actual_amount:.0f}")
        return True
    except Exception as e:
        send_telegram(f"❌ {symbol} 매수 실패: {e}")
        return False

def sell_hk_stock(symbol, reason=""):
    """홍콩 주식 매도"""
    try:
        if symbol not in held_stocks:
            return False

        info = held_stocks[symbol]
        price = get_hk_price(symbol)
        if not price:
            price = info['buy_price']

        qty = info['qty']
        sell_amount = price * qty
        buy_amount = info['buy_amount']
        pnl = sell_amount - buy_amount
        pnl_pct = (pnl / buy_amount * 100) if buy_amount > 0 else 0

        # 수수료 차감 (매도 HKD $15)
        pnl_after_fee = pnl - 15
        pnl_pct_after_fee = (pnl_after_fee / buy_amount * 100) if buy_amount > 0 else 0

        order = trade_client.place_order(
            symbol=f"{symbol}.HK",
            order_type=OrderType.MKT,
            side='SELL',
            quantity=qty
        )

        sell_records.append({
            "symbol": symbol,
            "buy_price": info['buy_price'],
            "sell_price": price,
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "pnl": pnl_after_fee,
            "pnl_pct": pnl_pct_after_fee,
            "reason": reason
        })

        del held_stocks[symbol]
        send_telegram(f"📤 매도: {symbol}\n매수가: HK${info['buy_price']:.3f} → 매도가: HK${price:.3f}\n손익(수수료후): {'+'if pnl_after_fee>=0 else ''}{pnl_after_fee:.0f} ({pnl_pct_after_fee:.1f}%)\n사유: {reason}")
        return True
    except Exception as e:
        send_telegram(f"❌ {symbol} 매도 실패: {e}")
        return False

def sell_all_hk_stocks(reason="장 마감"):
    send_telegram(f"🔔 전량 매도 시작 ({reason})...")
    for symbol in list(held_stocks.keys()):
        sell_hk_stock(symbol, reason)
        time.sleep(1)
    time.sleep(3)
    send_daily_report()

def send_daily_report():
    try:
        end_equity = get_total_equity()
        pnl = end_equity - start_equity
        pnl_pct = (pnl / start_equity * 100) if start_equity > 0 else 0
        emoji = "📈" if pnl >= 0 else "📉"

        report = f"{emoji} 오늘의 홍콩 매매 결과\n{'='*25}\n"
        report += f"시작 잔액: HK${start_equity:,.0f}\n"
        report += f"마감 잔액: HK${end_equity:,.0f}\n"
        report += f"총 손익: {'+'if pnl>=0 else ''}{pnl:,.0f} HKD\n"
        report += f"수익률: {'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%\n"
        report += f"{'='*25}\n📋 종목별 내역\n\n"

        for record in sell_records:
            r_emoji = "📈" if record['pnl'] >= 0 else "📉"
            report += f"{r_emoji} {record['symbol']}\n"
            report += f"  매수: HK${record['buy_amount']:.0f} @ ${record['buy_price']:.3f}\n"
            report += f"  매도: HK${record['sell_amount']:.0f} @ ${record['sell_price']:.3f}\n"
            report += f"  손익(수수료후): {'+'if record['pnl']>=0 else ''}{record['pnl']:.0f} ({record['pnl_pct']:.1f}%)\n"
            report += f"  사유: {record['reason']}\n\n"

        send_telegram(report)
    except Exception as e:
        send_telegram(f"✅ 매도 완료 (보고서 오류: {e})")

# ============================================
# 교체 매수
# ============================================
def find_next_hk_stock(current_symbol):
    global watchlist, blacklist_today
    current_sector = SECTOR_MAP.get(current_symbol, "other")
    news_items = get_hk_news()

    for candidate in watchlist[:]:
        if candidate in blacklist_today:
            continue
        if candidate in held_stocks:
            continue
        if SECTOR_MAP.get(candidate, "other") == current_sector:
            send_telegram(f"⏭️ {candidate} 건너뜀 (같은 섹터)")
            continue
        sentiment = score_article(' '.join([n['title'] for n in news_items[:20] if candidate in n['title'].lower()]), '')
        if sentiment < -3:
            send_telegram(f"⏭️ {candidate} 건너뜀 (악재)")
            blacklist_today.append(candidate)
            continue
        watchlist.remove(candidate)
        return candidate
    return None

# ============================================
# 모니터링
# ============================================
def monitor_once():
    global held_stocks, trading_done_today

    if not held_stocks:
        return "continue"

    try:
        current_equity = get_total_equity()
        if start_equity > 0:
            total_pnl_pct = ((current_equity - start_equity) / start_equity) * 100
            if total_pnl_pct >= PROFIT_TARGET:
                send_telegram(f"🎯 전체 목표 달성! +{total_pnl_pct:.1f}% → 전량 매도!")
                sell_all_hk_stocks("전체 목표 달성")
                return "done"
            if total_pnl_pct <= STOP_LOSS:
                send_telegram(f"🛑 전체 손절! {total_pnl_pct:.1f}%")
                sell_all_hk_stocks("전체 손절")
                return "done"

        for symbol in list(held_stocks.keys()):
            price = get_hk_price(symbol)
            if not price:
                continue

            info = held_stocks[symbol]
            buy_price = info['buy_price']

            # 수수료 포함 손익 계산
            sell_amount = price * info['qty']
            buy_amount = info['buy_amount']
            pnl_after_fee = (sell_amount - buy_amount) - 15  # 매도 수수료 HKD $15
            pnl_pct = (pnl_after_fee / buy_amount * 100) if buy_amount > 0 else 0

            if pnl_pct >= PROFIT_TARGET:
                send_telegram(f"🎯 {symbol} 개별 목표! +{pnl_pct:.1f}%(수수료후)")
                sell_hk_stock(symbol, f"개별 목표 +{pnl_pct:.1f}%")
                blacklist_today.append(symbol)
                next_stock = find_next_hk_stock(symbol)
                if next_stock:
                    balance = get_hkd_balance()
                    send_telegram(f"🔄 교체 매수: {next_stock}")
                    buy_hk_stock(next_stock, balance / max(len(held_stocks) + 1, 1))

            elif pnl_pct <= STOP_LOSS:
                send_telegram(f"🚨 {symbol} 손절! {pnl_pct:.1f}%(수수료후)")
                sell_hk_stock(symbol, f"손절 {pnl_pct:.1f}%")
                blacklist_today.append(symbol)
                next_stock = find_next_hk_stock(symbol)
                if next_stock:
                    balance = get_hkd_balance()
                    send_telegram(f"🔄 교체 매수: {next_stock}")
                    buy_hk_stock(next_stock, balance / max(len(held_stocks) + 1, 1))

    except Exception as e:
        print(f"[모니터링 오류] {e}")

    return "continue"

# ============================================
# 오후장 순위 재산출 및 비교
# ============================================
def afternoon_rerank(morning_top2):
    """점심 휴장 중 뉴스 수집 후 오후 순위 재산출"""
    send_telegram("🔍 오후장 순위 재산출 중...")
    news_items = get_hk_news()
    new_top30 = analyze_hk_stocks(news_items, time_limit_minutes=50)
    new_top2 = new_top30[:2]

    send_telegram(f"📊 오후 순위\n1위: {new_top2[0]}\n2위: {new_top2[1] if len(new_top2) > 1 else '-'}")

    # 변경된 종목만 교체
    to_sell = []
    to_buy = []

    for i, symbol in enumerate(morning_top2):
        if i < len(new_top2) and symbol != new_top2[i]:
            to_sell.append(symbol)
            to_buy.append(new_top2[i])

    if to_sell:
        send_telegram(f"🔄 순위 변동 감지!\n매도: {', '.join(to_sell)}\n매수: {', '.join(to_buy)}")
    else:
        send_telegram("✅ 순위 변동 없음 → 모니터링 계속")

    return to_sell, to_buy, new_top30

# ============================================
# 메인 실행
# ============================================
if __name__ == "__main__":
    send_telegram("🚀 홍콩 HKEx 자동매매 시작!")

    if not is_hk_market_open():
        send_telegram("📅 오늘은 홍콩 장이 열리지 않아요")
        sys.exit(0)

    # NZ 시간 기준
    # 09:30 뉴스수집, 10:00 매수, 12:00~13:00 휴장, 16:45 마감
    OPEN_TIME = 9 * 60 + 30    # 09:30
    BUY_TIME = 10 * 60         # 10:00
    LUNCH_START = 12 * 60      # 12:00
    LUNCH_END = 13 * 60        # 13:00
    CLOSE_TIME = 16 * 60 + 45  # 16:45

    current_time = nz_time_minutes()

    if current_time >= CLOSE_TIME:
        send_telegram("⏰ 이미 장 마감 시간이에요.")
        sys.exit(0)

    start_equity = get_total_equity()
    morning_top2 = []

    # 뉴스 수집 및 분석
    send_telegram("🔍 홍콩/아시아 뉴스 수집 시작...")
    news_items = get_hk_news()

    now = nz_now()
    current_time = nz_time_minutes()
    time_until_buy = BUY_TIME - current_time

    if time_until_buy >= 10:
        send_telegram(f"📰 풀 분석 시작 (매수까지 {time_until_buy}분)")
        top_30 = analyze_hk_stocks(news_items, time_limit_minutes=max(time_until_buy - 3, 5))
    else:
        send_telegram("⚡ 빠른 분석 모드")
        top_30 = analyze_hk_stocks(news_items, time_limit_minutes=3)

    watchlist = top_30.copy()

    message = "📊 오늘 홍콩 추천 종목 (상위 10개)\n\n"
    for i, stock in enumerate(top_30[:10], 1):
        sector = SECTOR_MAP.get(stock, "기타")
        message += f"{i}위: {stock} ({sector})\n"
    message += f"\n✅ 매수 예정: 1~2위"
    send_telegram(message)

    # 10:00까지 대기
    current_time = nz_time_minutes()
    if current_time < BUY_TIME:
        wait_sec = (BUY_TIME - current_time) * 60
        send_telegram(f"⏳ 10:00 장 시작까지 {wait_sec//60}분 대기...")
        time.sleep(wait_sec)

    # 매수
    send_telegram("🔔 10:00 홍콩 장 시작 → 매수!")
    balance = get_hkd_balance()
    per_stock = balance / 2

    buy_list = watchlist[:2]
    watchlist = watchlist[2:]

    for symbol in buy_list:
        # 매수 수수료 HKD $15 차감 고려
        buy_hk_stock(symbol, per_stock - 15)
        morning_top2.append(symbol)
        time.sleep(1)

    # 모니터링 루프
    send_telegram("👀 모니터링 시작 (5분 간격)")
    last_monitor = time.time()
    afternoon_done = False

    while True:
        current_time = nz_time_minutes()

        # 16:45 마감
        if current_time >= CLOSE_TIME:
            send_telegram("⏰ 16:45 장 마감 → 전량 매도!")
            sell_all_hk_stocks("장 마감")
            send_telegram("🔒 오늘 홍콩 거래 완료. 프로그램 종료.")
            sys.exit(0)

        # 점심 휴장 (12:00~13:00)
        if LUNCH_START <= current_time < LUNCH_END:
            if not afternoon_done:
                send_telegram("🍽️ 점심 휴장 - 뉴스 모니터링 계속...")
                # 휴장 중 뉴스 수집 + 순위 재산출
                time.sleep(55 * 60)  # 55분 대기 후 재산출
                to_sell, to_buy, new_top30 = afternoon_rerank(morning_top2)
                watchlist = new_top30[2:]
                afternoon_done = True

                # 오후장 시작 대기
                current_time = nz_time_minutes()
                if current_time < LUNCH_END:
                    wait_sec = (LUNCH_END - current_time) * 60
                    time.sleep(wait_sec)

                # 바뀐 종목만 교체
                if to_sell:
                    for sym in to_sell:
                        sell_hk_stock(sym, "오후 순위 변동 교체")
                        time.sleep(1)
                    balance = get_hkd_balance()
                    per_new = balance / max(len(to_buy), 1)
                    for sym in to_buy:
                        buy_hk_stock(sym, per_new - 15)
                        time.sleep(1)

            time.sleep(30)
            continue

        # 5분마다 모니터링
        if time.time() - last_monitor >= CHECK_INTERVAL * 60:
            result = monitor_once()
            last_monitor = time.time()
            if result == "done":
                send_telegram("🔒 오늘 홍콩 거래 완료. 프로그램 종료.")
                sys.exit(0)

        time.sleep(30)
