"""APScheduler job: periodically crawl all active monitors."""
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.models import SessionLocal, Monitor, PriceSample, User
from app.crawlers.fliggy_api import FliggyCrawler
from app.crawlers.variflight_api import VariflightCrawler
from app.analyzer import should_notify
from app.airlines import has_free_baggage
from app.email_service import send_price_alert
from app.config import settings
from app.rbac import is_owner, is_high_tier


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

_round_counter = 0  # 全局轮次计数，用于分级调度
_batch_lock = asyncio.Lock()  # 防止同一轮采集被重复/并发触发，避免重复消耗飞猪额度


def _set_fliggy_status(limited: bool):
    """记录飞猪限流状态，供前端提示。"""
    p = settings.database_url.replace("sqlite:///", "")
    flag_path = p.rsplit("/", 1)[0] + "/.fliggy_rate_limited"
    try:
        from pathlib import Path
        f = Path(flag_path)
        if limited:
            if not f.exists():
                f.write_text(datetime.utcnow().isoformat())
        else:
            if f.exists():
                f.unlink()
    except Exception:
        pass

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


def _merge_multi_source(flights: list[dict]) -> list[dict]:
    """多源合并去重。

    飞猪与飞常准返回的航班统一按价格排序；若为同一航班（航班号 + 日期相同），
    只保留报价最低的一条，并在 platform 标注其来源渠道，同时用 alt_platforms
    记录该航班在其他渠道的报价，保证信息不冲突、不重复。
    无航班号的条目无法判断是否同一航班，原样保留。
    """
    grouped: dict[tuple, dict] = {}
    standalone = []
    for f in flights:
        flight_no = f.get("flight_no", "")
        date = f.get("date", "")
        if not flight_no:
            standalone.append(f)
            continue
        key = (flight_no, date)
        if key not in grouped:
            entry = dict(f)
            entry["alt_platforms"] = []
            grouped[key] = entry
            continue

        current = grouped[key]
        # 把当前主条目与新报价比较，便宜的作为主条目，另一个记入备选渠道
        if f["price"] < current["price"]:
            cheaper, costlier = f, current
        else:
            cheaper, costlier = current, f
        entry = dict(cheaper)
        entry["alt_platforms"] = current["alt_platforms"] + [
            {"platform": costlier["platform"], "price": costlier["price"]}
        ]
        grouped[key] = entry

    merged = list(grouped.values()) + standalone
    merged.sort(key=lambda x: x["price"])
    return merged


def _variflight_allowed_for(db, monitor) -> bool:
    """飞常准每月免费额度仅服务系统主人本人。

    按需求：VIP/普通用户手动刷新都不调用飞常准，飞常准不参与自动轮次；
    避免多用户分摊少量免费工具调用次数。
    """
    owner = db.query(User).filter(User.email == settings.owner_email).first()
    return bool(owner and monitor.user_id == owner.id)


async def run_single_monitor(mid: int, manual_refresh: bool = False, allow_variflight: bool = True) -> bool:
    """Crawl a single monitor. Returns True if variflight was actually used.

    allow_variflight=False 时即便手动刷新也不启用飞常准（用于每日额度用尽后仍可用飞猪刷新）。
    """
    db = SessionLocal()
    variflight_used = False
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.is_active == True).first()
        if not m:
            print(f"[scheduler] monitor {mid} not found or inactive")
            return False

        print(f"[scheduler] running single monitor {mid}: {m.departure_city}->{m.arrival_city}")

        dep_code = CITY_NAME_TO_CODE.get(m.departure_city, m.departure_city[:3].upper())
        arr_code = CITY_NAME_TO_CODE.get(m.arrival_city, m.arrival_city[:3].upper())

        date_list = _parse_date_range(m)
        print(f"[scheduler] crawling {len(date_list)} dates: {date_list}")

        # 数据源策略：自动轮次只用飞猪，飞常准额度留给手动刷新
        fliggy = FliggyCrawler()
        variflight = None
        if manual_refresh and allow_variflight and _variflight_allowed_for(db, m):
            variflight = VariflightCrawler(
                api_key=settings.variflight_mcp_key,
                min_interval_hours=0,  # 手动刷新不节流，由每日次数限制控制
            )

        all_flights = []
        fliggy_hit_limit = False
        source_ok = False  # 是否至少有一个数据源成功响应（即使筛选后无航班也算链路正常）
        for date_str in date_list:
            day_flights = []
            # 飞猪
            try:
                found = fliggy.search(dep_code, arr_code, date_str)
                day_flights.extend(found)
            except Exception as e:
                print(f"[scheduler] {date_str} 飞猪 error: {e}")
            # 以爬虫自报的错误类型为准：明确额度/限流才点亮限流标记，临时失败不误报
            if getattr(fliggy, "last_error_kind", None) == "limited":
                fliggy_hit_limit = True
            if getattr(fliggy, "last_ok", False):
                source_ok = True

            # 手动刷新且飞猪没数据时才用飞常准
            if not day_flights and variflight:
                try:
                    found = variflight.search(dep_code, arr_code, date_str)
                    day_flights.extend(found)
                    if found:
                        variflight_used = True
                        print(f"[scheduler] {date_str} 飞常准补上 {len(found)} 班")
                except Exception as e:
                    print(f"[scheduler] {date_str} 飞常准 error: {e}")
                if getattr(variflight, "last_ok", False):
                    source_ok = True

            for f in day_flights:
                f["date"] = date_str
            all_flights.extend(day_flights)
            print(f"[scheduler] {date_str}: got {len(day_flights)} flights")
        _set_fliggy_status(fliggy_hit_limit)

        # 同一航班跨渠道取最低价，全部航班统一按价格排序
        flights = _merge_multi_source(all_flights)

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
            now0 = datetime.utcnow()
            m.last_checked_at = now0
            if source_ok:
                # 数据源链路正常响应，只是没有航班符合筛选条件/暂无票：
                # 视为最近一次采集成功，避免误报“数据源不可用”
                m.last_success_at = now0
                print(f"[scheduler] monitor {m.id}: 数据源正常但无符合条件航班")
            else:
                # 所有数据源都报错：只更新检查时间，保留旧成功时间 → 前端如实显示不可用
                print(f"[scheduler] monitor {m.id}: 数据源异常且无航班")
            db.commit()
            return variflight_used

        # 合并函数已按价格排序，这里取本次真实最低价/平台/日期，保证价格曲线真实
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
        elif last_sample:
            # 价格没变不新建sample，但更新航班列表（筛选条件可能变了）
            last_sample.flight_detail = flights[:50]
            last_sample.departure_date = cheapest_date
            last_sample.best_platform = best_platform
            last_sample.checked_at = now

        m.last_checked_at = now
        m.last_success_at = now
        # 用“更新前”历史最低判断是否新低，避免更新后恒等导致横幅误显
        prev_best_price = m.best_price_ever
        is_new_low = bool(prev_best_price is None or best_price < prev_best_price)
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
                    "best_price_ever": prev_best_price,
                },
                flights=flights[:5],
                analysis=analysis,
                advice=analysis.get("advice", "建议考虑购票"),
                is_new_low=is_new_low,
            )
            m.last_notified_at = now
            m.last_notified_price = best_price
            print(f"[scheduler] ALERT sent to {user_email}: {m.departure_city}->{m.arrival_city} ¥{best_price}")

        db.commit()
        print(f"[scheduler] monitor {mid} done, cheapest: ¥{best_price}, saved_new={save_new}")
        return variflight_used
    except Exception as e:
        print(f"[scheduler] monitor {mid} error: {e}")
        db.rollback()
        return False
    finally:
        db.close()


async def run_monitor_job():
    """Crawl all active monitors. Owner every 30min, others every 60min (every other round)."""
    # 防重入：上一批还没跑完就跳过，避免重复触发导致飞猪被自己打限流
    if _batch_lock.locked():
        print("[scheduler] skip: previous batch still running")
        return
    global _round_counter
    async with _batch_lock:
        _round_counter += 1
        print(f"[scheduler] batch #{_round_counter} triggered at {datetime.now().isoformat()}")
        db = SessionLocal()
        try:
            monitors = db.query(Monitor).filter(Monitor.is_active == True).all()
            for m in monitors:
                # 主人/VIP 每轮采集（30分钟）；普通用户隔轮（约60分钟）
                if not is_high_tier(m.owner) and _round_counter % 2 != 0:
                    continue
                await run_single_monitor(m.id)
        finally:
            db.close()


def start_scheduler():
    from apscheduler.triggers.cron import CronTrigger
    # 白天 7:00-23:59 每30分钟一轮（用户monitor每轮都跑，别人的隔轮=每60分钟）
    scheduler.add_job(
        run_monitor_job,
        trigger=CronTrigger(minute="*/30", hour="7-23"),
        id="monitor_day",
        replace_existing=True,
        max_instances=1,       # 同一时刻只允许一个批次实例
        coalesce=True,         # 错过的多次触发合并成一次，不补跑
        misfire_grace_time=60, # 启动/卡顿后60秒内仍可补一轮，过期丢弃
    )
    # 夜间不跑；不设置 next_run_time，纯按 cron 整点/半点触发，避免重启立即抓
    scheduler.start()
    jobs = scheduler.get_jobs()
    print(f"[scheduler] started: {len(jobs)} job(s); 白天7-23点每30分钟(用户)/每60分钟(别人), 夜间不跑")
