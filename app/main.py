"""FastAPI main app."""
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime
from app.config import BASE_DIR, settings
from app.models import init_db, SessionLocal, Monitor, User, PriceSample
from app.auth import create_login_code, send_code_email, login_with_code, verify_token
from app.scheduler import start_scheduler

app = FastAPI(title="Flight Monitor")
init_db()


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
    resp.set_cookie("token", token, max_age=30 * 24 * 3600, httponly=True, samesite="lax")
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
            result.append(
                {
                    "id": m.id,
                    "departure_city": m.departure_city,
                    "arrival_city": m.arrival_city,
                    "departure_date_start": m.departure_date_start,
                    "departure_date_end": m.departure_date_end,
                    "best_price": m.best_price_ever,
                    "latest_price": latest.best_price if latest else None,
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
        import asyncio
        from app.scheduler import run_single_monitor
        asyncio.create_task(run_single_monitor(m.id))
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
        import asyncio
        from app.scheduler import run_single_monitor
        asyncio.create_task(run_single_monitor(m.id))
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
        monitor = db.query(Monitor).filter(Monitor.id == mid).first()
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
        if not sample:
            return {"flights": [], "fees": fees, "stale": not bool(monitor and monitor.last_checked_at)}
        return {"flights": sample.flight_detail or [], "fees": fees}
    finally:
        db.close()


@app.post("/api/monitors/{mid}/refresh")
async def monitor_refresh(mid: int, user: User = Depends(get_current_user)):
    from app.scheduler import run_single_monitor
    from pathlib import Path
    import time as _time
    db = SessionLocal()
    try:
        monitor = db.query(Monitor).filter(Monitor.id == mid).first()
        owner = db.query(User).filter(User.email == settings.owner_email).first()
        is_owner = owner and user.id == owner.id
    finally:
        db.close()

    today = _time.strftime("%Y-%m-%d")
    quota_file = Path(settings.database_url.replace("sqlite:///", "")).parent / ".variflight_daily_quota"

    if is_owner:
        # 主人：飞猪不限额，飞常准每天3次
        used_today = 0
        if quota_file.exists():
            try:
                d, c = quota_file.read_text().strip().split(":")
                if d == today: used_today = int(c)
            except Exception: pass
        if used_today >= 3:
            return {"ok": False, "msg": "飞常准今日手动刷新次数已用完（3次），明天自动恢复"}
        await run_single_monitor(mid, manual_refresh=True)
        quota_file.write_text(f"{today}:{used_today + 1}")
        return {"ok": True, "variflight_left": 3 - used_today - 1}
    else:
        # 其他用户：手动刷新每天限1次
        other_quota = Path(settings.database_url.replace("sqlite:///", "")).parent / ".other_refresh_quota"
        used = 0
        if other_quota.exists():
            try:
                d, c = other_quota.read_text().strip().split(":")
                if d == today: used = int(c)
            except Exception: pass
        if used >= 10:
            return {"ok": False, "msg": "今日手动刷新次数已用完（10次），明天自动恢复"}
        await run_single_monitor(mid, manual_refresh=True)
        other_quota.write_text(f"{today}:{used + 1}")
        return {"ok": True, "refresh_left": 10 - used - 1}


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
