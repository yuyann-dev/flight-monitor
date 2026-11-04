"""APScheduler job: periodically crawl all active monitors."""
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.models import SessionLocal, Monitor, PriceSample
from app.crawlers.fliggy_api import FliggyCrawler
from app.crawlers.variflight_api import VariflightCrawler
from app.analyzer import should_notify
from app.airlines import has_free_baggage
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


def _parse_date_range(m: Monitor) -> list[str]:
    """从监控任务解析出要爬取的日期列表，对空值/异常值做容错。"""
    start = (m.departure_date_start or "").strip()
    end = (m.departure_date_end or "").strip()

    if not start:
        raise ValueError("departure_date_start 为空")

    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    # 结束日期为空或异常时，退化为单日
    if not end:
        end_date = start_date
    else:
        end_date = datetime.strptime(end, "%Y-%m-%d").date()

    # 防止结束早于开始
    if end_date < start_date:
        end_date = start_date

    date_list = []
    current = start_date
    while current <= end_date:
        date_list.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return date_list


def _get_hours(time_str: str):
    """从时间字符串提取小时数。"""
    if not time_str:
        return None
    try:
        if "T" in time_str:
            time_part = time_str.split("T")[1]
        elif " " in time_str:
            time_part = time_str.split(" ")[1]
        else:
            time_part = time_str
        return int(time_part.split(":")[0])
    except Exception:
        return None


def _in_windows(hour, windows) -> bool:
    """检查小时是否落在任意一个时段窗口内。"""
    if hour is None:
        return False
    for w in windows:
        if isinstance(w, list) and len(w) == 2 and w[0] <= hour < w[1]:
            return True
    return False


async def run_single_monitor(mid: int):
    """Crawl a single monitor and analyze prices."""
    db = SessionLocal()
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.is_active == True).first()
        if not m:
            print(f"[scheduler] monitor {mid} not found or inactive")
            return

        print(f"[scheduler] running single monitor {mid}: {m.departure_city}->{m.arrival_city}")

        dep_code = CITY_NAME_TO_CODE.get(m.departure_city, m.departure_city[:3].upper())
        arr_code = CITY_NAME_TO_CODE.get(m.arrival_city, m.arrival_city[:3].upper())

        date_list = _parse_date_range(m)
        print(f"[scheduler] crawling {len(date_list)} dates: {date_list}")

        # 多数据源逐天爬取并合并：飞猪（flyai CLI）+ 飞常准（MCP，未配置 Key 时自动跳过）
        all_flights = []
        crawlers = [
            FliggyCrawler(),
            VariflightCrawler(
                api_key=settings.variflight_mcp_key,
                min_interval_hours=settings.variflight_min_interval_hours,
            ),
        ]
        for date_str in date_list:
            day_flights = []
            for crawler in crawlers:
                try:
                    found = crawler.search(dep_code, arr_code, date_str)
                    day_flights.extend(found)
                except Exception as e:
                    print(f"[scheduler] {date_str} {crawler.name} error: {e}")
            for f in day_flights:
                f["date"] = date_str
            all_flights.extend(day_flights)
            print(f"[scheduler] {date_str}: got {len(day_flights)} flights")

        flights = all_flights

        # 只看直飞
        if m.direct_only:
            before = len(flights)
            flights = [f for f in flights if f.get("is_direct")]
            print(f"[scheduler] direct_only: {before} -> {len(flights)} flights")

        # 时段筛选（出发、到达都填了取并集）
        dep_windows = m.departure_time_windows or []
        arr_windows = m.arrival_time_windows or []
        if dep_windows or arr_windows:
            before = len(flights)
            filtered = []
            for f in flights:
                dep_hour = _get_hours(f.get("dep_time", ""))
                arr_hour = _get_hours(f.get("arr_time", ""))
                dep_match = _in_windows(dep_hour, dep_windows) if dep_windows else False
                arr_match = _in_windows(arr_hour, arr_windows) if arr_windows else False

                if dep_windows and arr_windows:
                    ok = dep_match or arr_match  # 并集
                elif dep_windows:
                    ok = dep_match
                else:
                    ok = arr_match
                if ok:
                    filtered.append(f)
            flights = filtered
            print(f"[scheduler] time_windows: {before} -> {len(flights)} flights")

        # 需要免费托运：剔除廉航和差异化航司
        if m.baggage_required:
            before = len(flights)
            flights = [f for f in flights if has_free_baggage(f.get("airline", ""))]
            print(f"[scheduler] baggage_required: {before} -> {len(flights)} flights")

        # 过滤用户手动剔除的航班
        excluded = m.excluded_flights or []
        if excluded:
            before = len(flights)
            flights = [
                f for f in flights
                if not any(
                    e["flight_no"] == f.get("flight_no") and e["date"] == f.get("date")
                    for e in excluded
                )
            ]
            print(f"[scheduler] excluded {before - len(flights)} flights")

        if not flights:
            print(f"[scheduler] no flights for monitor {m.id}")
            m.last_checked_at = datetime.utcnow()
            db.commit()
            return

        # 所有日期航班统一按价格排序
        flights.sort(key=lambda x: x["price"])
        best_price = flights[0]["price"]
        best_platform = flights[0]["platform"]
        cheapest_date = flights[0]["date"]

        # 样本去重：距上一条样本不足10分钟且价格相同，则不重复落库
        last_sample = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == m.id)
            .order_by(PriceSample.checked_at.desc())
            .first()
        )
        now = datetime.utcnow()
        save_new = True
        if last_sample and last_sample.best_price == best_price:
            gap_min = (now - last_sample.checked_at).total_seconds() / 60
            if gap_min < 10:
                save_new = False

        if save_new:
            sample = PriceSample(
                monitor_id=m.id,
                departure_date=cheapest_date,
                best_price=best_price,
                best_platform=best_platform,
                flight_detail=flights[:50],
            )
            db.add(sample)

        m.last_checked_at = now
        if not m.best_price_ever or best_price < m.best_price_ever:
            m.best_price_ever = best_price

        # 邮件提醒判断
        all_samples = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == m.id)
            .order_by(PriceSample.checked_at)
            .all()
        )
        should, reason, analysis = should_notify(m, all_samples, best_price)

        if should:
            user_email = m.owner.email
            send_price_alert(
                email=user_email,
                monitor_info={
                    "departure_city": m.departure_city,
                    "arrival_city": m.arrival_city,
                    "departure_date": m.departure_date_start,
                    "best_price_ever": m.best_price_ever,
                },
                flights=flights[:5],
                analysis=analysis,
            )
            m.last_notified_at = now
            m.last_notified_price = best_price
            print(f"[scheduler] ALERT sent to {user_email}: {m.departure_city}->{m.arrival_city} ¥{best_price}")

        db.commit()
        print(f"[scheduler] monitor {mid} done, cheapest: ¥{best_price}, saved_new={save_new}")
    except Exception as e:
        print(f"[scheduler] monitor {mid} error: {e}")
        db.rollback()
    finally:
        db.close()


async def run_monitor_job():
    """Crawl all active monitors."""
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
