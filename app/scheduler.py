"""APScheduler job: periodically crawl all active monitors."""
from datetime import datetime
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


async def run_monitor_job():
    """Crawl all active monitors and analyze prices."""
    db = SessionLocal()
    try:
        monitors = db.query(Monitor).filter(Monitor.is_active == True).all()
        print(f"[scheduler] {datetime.now()} checking {len(monitors)} monitors")

        fliggy_crawler = FliggyCrawler()
        ctrip_crawler = CtripCrawler()

        for m in monitors:
            try:
                # 城市名转三字码
                dep_code = CITY_NAME_TO_CODE.get(m.departure_city, m.departure_city[:3].upper())
                arr_code = CITY_NAME_TO_CODE.get(m.arrival_city, m.arrival_city[:3].upper())

                # 多平台比价：飞猪（主，flyai CLI 官方数据）
                all_flights = []

                # 飞猪（主源，flyai CLI）
                try:
                    fliggy_flights = fliggy_crawler.search(dep_code, arr_code, m.departure_date_start)
                    all_flights.extend(fliggy_flights)
                except Exception as e:
                    print(f"[scheduler] fliggy error: {e}")

                # 携程（备用，反爬严，暂不启用）
                # try:
                #     ctrip_flights = ctrip_crawler.search(dep_code, arr_code, m.departure_date_start)
                #     all_flights.extend(ctrip_flights)
                # except Exception as e:
                #     print(f"[scheduler] ctrip error: {e}")

                flights = all_flights
                if not flights:
                    print(f"[scheduler] no flights for monitor {m.id}")
                    continue

                best_price = min(f["price"] for f in flights)
                best_platform = next(f["platform"] for f in flights if f["price"] == best_price)

                # 保存样本
                sample = PriceSample(
                    monitor_id=m.id,
                    departure_date=m.departure_date_start,
                    best_price=best_price,
                    best_platform=best_platform,
                    flight_detail=flights[:5],
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
            except Exception as e:
                print(f"[scheduler] monitor {m.id} error: {e}")
                db.rollback()
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
