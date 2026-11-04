"""APScheduler job: periodically crawl all active monitors."""
import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.models import SessionLocal, Monitor, PriceSample
from app.crawlers.fliggy_api import search_fliggy, CITY_CODES
from app.analyzer import should_notify
from app.email_service import send_price_alert
from app.config import settings


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def run_monitor_job():
    """Crawl all active monitors and analyze prices."""
    db = SessionLocal()
    try:
        monitors = db.query(Monitor).filter(Monitor.is_active == True).all()
        print(f"[scheduler] {datetime.now()} checking {len(monitors)} monitors")

        for m in monitors:
            try:
                # 城市名转三字码
                dep_code = CITY_CODES.get(m.departure_city, m.departure_city[:3].upper())
                arr_code = CITY_CODES.get(m.arrival_city, m.arrival_city[:3].upper())
                
                flights = await search_fliggy(dep_code, arr_code, m.departure_date_start)
                if not flights:
                    continue
                
                best_price = min(f["price"] for f in flights)
                best_platform = flights[0]["platform"]

                # 保存样本
                sample = PriceSample(
                    monitor_id=m.id,
                    best_price=best_price,
                    best_platform=best_platform,
                    raw_data=str(flights[:5]),
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
        next_run_time=datetime.now(),  # 启动后立即跑一次
        id="monitor_job",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[scheduler] started, interval={settings.crawl_interval_minutes}min")
