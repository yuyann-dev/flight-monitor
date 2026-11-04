"""APScheduler job: periodically crawl all active monitors."""
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.models import SessionLocal, Monitor, PriceSample
from app.crawlers.fliggy_api import FliggyCrawler
from app.crawlers.ctrip import CtripCrawler
from app.analyzer import should_notify
from app.email_service import send_price_alert
from app.config import settings


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# 城市名 → 三字码映射表
CITY_NAME_TO_CODE = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "成都": "CTU", "重庆": "CKG", "杭州": "HGH", "南京": "NKG",
    "武汉": "WUH", "西安": "XIY", "长沙": "CSX", "厦门": "XMN",
    "昆明": "KMG", "三亚": "SYX", "海口": "HAK", "青岛": "TAO",
    "大连": "DLC", "天津": "TSN", "郑州": "CGO", "济南": "TNA",
    "福州": "FOC", "合肥": "HFE", "南昌": "KHN", "贵阳": "KWE",
    "南宁": "NNG", "兰州": "LHW", "乌鲁木齐": "URC", "哈尔滨": "HRB",
    "沈阳": "SHE", "长春": "CGQ", "呼和浩特": "HET", "石家庄": "SJZ",
    "太原": "TYN", "银川": "INC", "西宁": "XNN", "拉萨": "LXA",
    "宁波": "NGB", "温州": "WNZ", "桂林": "KWL", "丽江": "LJG",
}


async def run_single_monitor(mid: int):
    """Crawl a single monitor and analyze prices."""
    db = SessionLocal()
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.is_active == True).first()
        if not m:
            print(f"[scheduler] monitor {mid} not found or inactive")
            return

        print(f"[scheduler] running single monitor {mid}: {m.departure_city}->{m.arrival_city}")

        # 城市名转三字码
        dep_code = CITY_NAME_TO_CODE.get(m.departure_city, m.departure_city[:3].upper())
        arr_code = CITY_NAME_TO_CODE.get(m.arrival_city, m.arrival_city[:3].upper())

        # 遍历日期范围，爬每一天的价格
        all_flights = []
        cheapest_date = m.departure_date_start

        # 生成日期范围列表
        start_date = datetime.strptime(m.departure_date_start, "%Y-%m-%d").date()
        end_date = datetime.strptime(m.departure_date_end, "%Y-%m-%d").date()
        date_list = []
        current = start_date
        while current <= end_date:
            date_list.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        print(f"[scheduler] crawling {len(date_list)} dates: {date_list}")

        # 飞猪（主源，flyai CLI）- 遍历每一天
        try:
            fliggy_crawler = FliggyCrawler()
            for date_str in date_list:
                try:
                    day_flights = fliggy_crawler.search(dep_code, arr_code, date_str)
                    # 给每个航班加上日期信息
                    for f in day_flights:
                        f["date"] = date_str
                    all_flights.extend(day_flights)
                    print(f"[scheduler] {date_str}: got {len(day_flights)} flights")
                except Exception as e:
                    print(f"[scheduler] {date_str} error: {e}")
        except Exception as e:
            print(f"[scheduler] fliggy error: {e}")

        flights = all_flights

        # 过滤掉用户手动剔除的航班
        excluded = m.excluded_flights or []
        if excluded:
            before = len(flights)
            flights = [f for f in flights if not any(
                e["flight_no"] == f.get("flight_no") and e["date"] == f.get("date")
                for e in excluded
            )]
            print(f"[scheduler] excluded {before - len(flights)} flights")

        if not flights:
            print(f"[scheduler] no flights for monitor {m.id}")
            return

        # 找最低价和最便宜的日期
        best_price = min(f["price"] for f in flights)
        best_platform = next(f["platform"] for f in flights if f["price"] == best_price)
        cheapest_date = next(f["date"] for f in flights if f["price"] == best_price)

        # 保存样本
        sample = PriceSample(
            monitor_id=m.id,
            departure_date=cheapest_date,
            best_price=best_price,
            best_platform=best_platform,
            flight_detail=flights[:10],  # 存更多航班
        )
        db.add(sample)

        # 更新监控状态
        m.last_checked_at = datetime.utcnow()
        if not m.best_price_ever or best_price < m.best_price_ever:
            m.best_price_ever = best_price

        # 分析是否需要提醒
        samples = db.query(PriceSample).filter(PriceSample.monitor_id == m.id).order_by(PriceSample.checked_at).all()
        should, reason, analysis = should_notify(m, samples, best_price)

        if should:
            user_email = m.owner.email
            send_price_alert(
                email=user_email,
                monitor_info={
                    "departure_city": m.departure_city,
                    "arrival_city": m.arrival_city,
                    "departure_date": m.departure_date_start,
                },
                flights=flights[:5],
                analysis=analysis,
            )
            m.last_notified_at = datetime.utcnow()
            m.last_notified_price = best_price
            print(f"[scheduler] ALERT sent to {user_email}: {m.departure_city}->{m.arrival_city} ¥{best_price}")

        db.commit()
        print(f"[scheduler] monitor {mid} done, cheapest: ¥{best_price}")
    except Exception as e:
        print(f"[scheduler] monitor {mid} error: {e}")
        db.rollback()
    finally:
        db.close()


async def run_monitor_job():
    """Crawl all active monitors and analyze prices."""
    db = SessionLocal()
    try:
        monitors = db.query(Monitor).filter(Monitor.is_active == True).all()
        print(f"[scheduler] {datetime.now()} checking {len(monitors)} monitors")

        for m in monitors:
            await run_single_monitor(m.id)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(
        run_monitor_job,
        trigger=IntervalTrigger(minutes=settings.crawl_interval_minutes),
        next_run_time=datetime.now(),
        id="monitor_job",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[scheduler] started, interval={settings.crawl_interval_minutes}min")
