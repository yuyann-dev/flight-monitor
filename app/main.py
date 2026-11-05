"""FastAPI main app."""
import asyncio
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime
from app.config import BASE_DIR, settings
from app.models import init_db, SessionLocal, Monitor, User, PriceSample
from app.auth import create_login_code, send_code_email, login_with_code, verify_token, code_cooldown_seconds
from app.scheduler import start_scheduler

app = FastAPI(title="Flight Monitor")
init_db()

# 持有后台采集任务引用，避免事件循环弱引用导致任务被提前 GC 取消
_bg_tasks: set = set()


def _spawn_crawl(monitor_id: int):
    """后台触发单条采集，任务完成后自动从集合移除。"""
    from app.scheduler import run_single_monitor
    task = asyncio.create_task(run_single_monitor(monitor_id))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ── 鉴权依赖 ──
async def get_current_user(request: Request) -> User:
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    uid = verify_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="登录已过期")
    db = SessionLocal()
    try:
        user = db.query(User).get(uid)
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user
    finally:
        db.close()


# ── 请求模型 ──
class LoginRequest(BaseModel):
    email: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class MonitorCreate(BaseModel):
    departure_city: str
    arrival_city: str
    departure_date_start: str
    departure_date_end: str | None = None
    cabin_class: str = "economy"
    direct_only: bool = False
    baggage_required: bool = False
    max_price: int | None = None
    departure_time_windows: list = []
    arrival_time_windows: list = []


class MonitorUpdate(BaseModel):
    departure_date_start: str | None = None
    departure_date_end: str | None = None
    direct_only: bool | None = None
    baggage_required: bool | None = None
    max_price: int | None = None
    departure_time_windows: list | None = None
    arrival_time_windows: list | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── 认证路由 ──
@app.post("/api/auth/send-code")
async def send_code(req: LoginRequest, background: BackgroundTasks):
    wait = code_cooldown_seconds(req.email, 60)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"验证码发送过于频繁，请 {wait} 秒后再试")
    code = create_login_code(req.email)
    # 后台发邮件，避免阻塞响应
    background.add_task(send_code_email, req.email, code)
    return {"ok": True, "msg": "验证码已发送"}


@app.post("/api/auth/verify")
async def verify(req: VerifyRequest):
    result = login_with_code(req.email, req.code)
    if not result:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    from fastapi.responses import JSONResponse
    token, user = result
    resp = JSONResponse(content={"ok": True, "email": user.email})
    resp.set_cookie("token", token, max_age=30 * 24 * 3600, httponly=True, secure=True, samesite="lax")
    return resp


@app.post("/api/auth/logout")
async def logout():
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie("token")
    return resp


@app.get("/api/auth/me")
async def me(user: User = Depends(get_current_user)):
    return {"email": user.email}


# ── 监控 CRUD ──
@app.get("/api/system/status")
async def system_status(user: User = Depends(get_current_user)):
    """数据源状态：飞猪是否被限流。"""
    from pathlib import Path
    p = settings.database_url.replace("sqlite:///", "")
    flag = Path(p.rsplit("/", 1)[0]) / ".fliggy_rate_limited"
    if flag.exists():
        return {"fliggy_limited": True, "since": flag.read_text()}
    return {"fliggy_limited": False}


@app.get("/api/monitors")
async def list_monitors(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        monitors = (
            db.query(Monitor)
            .filter(Monitor.user_id == user.id, Monitor.is_active == True)
            .all()
        )
        result = []
        for m in monitors:
            latest = (
                db.query(PriceSample)
                .filter(PriceSample.monitor_id == m.id)
                .order_by(PriceSample.checked_at.desc())
                .first()
            )
            # stale判断：最近一次采集失败了就不显示最新价格
            is_stale = bool(
                m.last_checked_at and m.last_success_at
                and m.last_checked_at > m.last_success_at
            )
            # no_match：链路正常但本次无符合筛选条件的航班，也不沿用旧当前价
            no_match = bool(
                not is_stale and m.last_checked_at
                and (latest is None or m.last_checked_at > latest.checked_at)
            )
            result.append(
                {
                    "id": m.id,
                    "departure_city": m.departure_city,
                    "arrival_city": m.arrival_city,
                    "departure_date_start": m.departure_date_start,
                    "departure_date_end": m.departure_date_end,
                    "best_price": m.best_price_ever,
                    "latest_price": None if (is_stale or no_match) else (latest.best_price if latest else None),
                    "stale": is_stale,
                    "no_match": no_match,
                    "last_checked": (
                        m.last_checked_at.isoformat() + "Z"
                        if m.last_checked_at
                        else None
                    ),
                    "is_active": m.is_active,
                    "max_price": m.max_price,
                    "cabin_class": m.cabin_class,
                    "direct_only": m.direct_only,
                    "baggage_required": m.baggage_required,
                    "departure_time_windows": m.departure_time_windows or [],
                    "arrival_time_windows": m.arrival_time_windows or [],
                }
            )
        return result
    finally:
        db.close()


@app.post("/api/monitors")
async def create_monitor(req: MonitorCreate, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        # 结束日期为空时，默认等于开始日期（单日）
        start = req.departure_date_start
        end = req.departure_date_end or start

        m = Monitor(
            user_id=user.id,
            departure_city=req.departure_city,
            arrival_city=req.arrival_city,
            departure_date_start=start,
            departure_date_end=end,
            cabin_class=req.cabin_class,
            direct_only=req.direct_only,
            baggage_required=req.baggage_required,
            max_price=req.max_price,
            departure_time_windows=req.departure_time_windows,
            arrival_time_windows=req.arrival_time_windows,
        )
        db.add(m)
        db.commit()
        # 后台异步先爬一次，不阻塞响应
        _spawn_crawl(m.id)
        return {"ok": True, "id": m.id}
    finally:
        db.close()


@app.delete("/api/monitors/{mid}")
async def stop_monitor(mid: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not m:
            raise HTTPException(status_code=404)
        m.is_active = False
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.put("/api/monitors/{mid}")
async def update_monitor(mid: int, req: MonitorUpdate, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not m:
            raise HTTPException(status_code=404)
        data = req.model_dump(exclude_unset=True)
        if "departure_date_start" in data:
            m.departure_date_start = data["departure_date_start"]
        if "departure_date_end" in data:
            m.departure_date_end = data["departure_date_end"] or m.departure_date_start
        if "direct_only" in data:
            m.direct_only = data["direct_only"]
        if "baggage_required" in data:
            m.baggage_required = data["baggage_required"]
        if "max_price" in data:
            m.max_price = data["max_price"]
        if "departure_time_windows" in data:
            m.departure_time_windows = data["departure_time_windows"] or []
        if "arrival_time_windows" in data:
            m.arrival_time_windows = data["arrival_time_windows"] or []
        db.commit()
        # 后台立即重新采集一次，让新条件立刻生效
        _spawn_crawl(m.id)
        return {"ok": True}
    finally:
        db.close()


@app.post("/api/monitors/{mid}/exclude-flight")
async def exclude_flight(mid: int, body: dict, user: User = Depends(get_current_user)):
    """剔除某个航班，后续不再展示。带去重。"""
    db = SessionLocal()
    try:
        m = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not m:
            raise HTTPException(status_code=404)
        flight_no = body.get("flight_no")
        date = body.get("date")
        if not (flight_no and date):
            raise HTTPException(status_code=400, detail="缺少航班号或日期")

        excluded = m.excluded_flights or []
        # 去重：已存在则不重复添加
        exists = any(
            e.get("flight_no") == flight_no and e.get("date") == date
            for e in excluded
        )
        if not exists:
            excluded.append({"flight_no": flight_no, "date": date})
            m.excluded_flights = excluded
            db.commit()
        return {"ok": True, "excluded": excluded}
    finally:
        db.close()


@app.get("/api/monitors/{mid}/history")
async def monitor_history(mid: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        monitor = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not monitor:
            raise HTTPException(status_code=404)
        samples = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == mid)
            .order_by(PriceSample.checked_at)
            .all()
        )
        return [
            {
                "time": s.checked_at.isoformat() + "Z",
                "price": s.best_price,
                "platform": s.best_platform,
            }
            for s in samples[-60:]
        ]
    finally:
        db.close()


@app.get("/api/monitors/{mid}/advice")
async def monitor_advice(mid: int, user: User = Depends(get_current_user)):
    """后端完整购买建议，前端直接展示。"""
    from app.analyzer import generate_advice
    db = SessionLocal()
    try:
        monitor = db.query(Monitor).filter(
            Monitor.id == mid, Monitor.user_id == user.id
        ).first()
        if not monitor:
            return {"advice": "系统监测中", "reasons": ["监控任务不存在"]}
        samples = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == mid)
            .order_by(PriceSample.checked_at)
            .all()
        )
        return generate_advice(monitor, samples)
    finally:
        db.close()


@app.get("/api/monitors/{mid}/latest-detail")
async def monitor_latest_detail(mid: int, user: User = Depends(get_current_user)):
    from app.analyzer import compute_fees
    db = SessionLocal()
    try:
        monitor = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not monitor:
            raise HTTPException(status_code=404)
        sample = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == mid)
            .order_by(PriceSample.checked_at.desc())
            .first()
        )
        fees = compute_fees()
        # 诚实显示：最近一次采集失败了（last_checked_at > last_success_at）就显示不可用
        stale = bool(
            monitor and monitor.last_checked_at and monitor.last_success_at
            and monitor.last_checked_at > monitor.last_success_at
        )
        if stale:
            return {"flights": [], "fees": fees, "stale": True}
        # 最近一次采集没有产生“符合条件”的新样本（无票或被筛选条件过滤）：
        # 不回传旧航班冒充最新，返回 no_match 由前端提示“暂无符合条件航班”
        latest_sample_at = sample.checked_at if sample else None
        no_match = bool(
            monitor.last_checked_at
            and (latest_sample_at is None or monitor.last_checked_at > latest_sample_at)
        )
        if no_match:
            return {"flights": [], "fees": fees, "stale": False, "no_match": True}
        # 样本快照是采集时落库的，这里按“不关注”列表实时过滤，使用户剔除即时生效，
        # 不必等下一轮采集才从页面消失
        excluded = {
            (e.get("flight_no"), e.get("date"))
            for e in (monitor.excluded_flights or [])
        }
        flights = [
            f for f in (sample.flight_detail or [])
            if (f.get("flight_no"), f.get("date")) not in excluded
        ]
        # 行李文案/类别在采集时固化过；展示层按最新航司规则实时重算，
        # 避免历史样本沿用旧的“以购买页为准”中性文案（只改展示文本，不改变入选与价格）
        from app.airlines import classify, baggage_text
        for f in flights:
            airline = f.get("airline", "") or ""
            f["airline_category"] = classify(airline)
            f["baggage_text"] = baggage_text(airline)
        return {"flights": flights, "fees": fees}
    finally:
        db.close()


@app.post("/api/monitors/{mid}/refresh")
async def monitor_refresh(mid: int, user: User = Depends(get_current_user)):
    from app.scheduler import run_single_monitor
    from pathlib import Path
    import time as _time
    db = SessionLocal()
    try:
        monitor = db.query(Monitor).filter(Monitor.id == mid, Monitor.user_id == user.id).first()
        if not monitor:
            raise HTTPException(status_code=404)
        owner = db.query(User).filter(User.email == settings.owner_email).first()
        is_owner = owner and user.id == owner.id
    finally:
        db.close()

    today = _time.strftime("%Y-%m-%d")
    quota_file = Path(settings.database_url.replace("sqlite:///", "")).parent / ".variflight_daily_quota"

    if is_owner:
        # 主人：飞猪手动刷新不限额；飞常准每天最多3次，且只有飞猪失败时才消耗
        used_today = 0
        if quota_file.exists():
            try:
                d, c = quota_file.read_text().strip().split(":")
                if d == today: used_today = int(c)
            except Exception: pass
        allow_variflight = used_today < 3
        variflight_used = await run_single_monitor(mid, manual_refresh=True, allow_variflight=allow_variflight)
        if variflight_used:
            quota_file.write_text(f"{today}:{used_today + 1}")
            left = 3 - used_today - 1
            return {"ok": True, "used_variflight": True, "variflight_left": left}
        # 飞猪直接成功：不强调飞常准剩余额度，避免误导用户以为依赖备用源
        if not allow_variflight:
            return {"ok": True, "note": "飞常准今日3次已用完，本次仅用飞猪查询"}
        return {"ok": True}
    else:
        # 其他用户：手动刷新（含飞猪）每天限10次；飞常准另计每天3次，
        # 且仅当主人无活跃任务时才允许使用（归属由 run_single_monitor 内 _variflight_allowed_for 兜底）
        data_dir = Path(settings.database_url.replace("sqlite:///", "")).parent
        other_quota = data_dir / ".other_refresh_quota"
        used = 0
        if other_quota.exists():
            try:
                d, c = other_quota.read_text().strip().split(":")
                if d == today: used = int(c)
            except Exception: pass
        if used >= 10:
            return {"ok": False, "msg": "今日手动刷新次数已用完（10次），明天自动恢复"}

        vfile = data_dir / ".variflight_other_quota"
        v_used = 0
        if vfile.exists():
            try:
                d, c = vfile.read_text().strip().split(":")
                if d == today: v_used = int(c)
            except Exception: pass
        allow_variflight = v_used < 3

        used_v = await run_single_monitor(mid, manual_refresh=True, allow_variflight=allow_variflight)
        other_quota.write_text(f"{today}:{used + 1}")
        if used_v:
            vfile.write_text(f"{today}:{v_used + 1}")
            return {"ok": True, "used_variflight": True,
                    "refresh_left": 10 - used - 1, "variflight_left": 3 - v_used - 1}
        note = "飞常准今日3次已用完，本次仅用飞猪查询" if not allow_variflight else None
        result = {"ok": True, "refresh_left": 10 - used - 1}
        if note:
            result["note"] = note
        return result


# ── 静态资源与前端 ──
static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


# 前端单页路由兜底（仅对非 /api 路径返回首页）
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse(static_dir / "index.html")


@app.on_event("startup")
async def startup():
    start_scheduler()
