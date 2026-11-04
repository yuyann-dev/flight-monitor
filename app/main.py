"""FastAPI main app."""
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime
from app.config import BASE_DIR
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
                    "baggage_required": m.baggage_required,
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
        sample = (
            db.query(PriceSample)
            .filter(PriceSample.monitor_id == mid)
            .order_by(PriceSample.checked_at.desc())
            .first()
        )
        # 无可靠距离时按长途保守计算费用
        fees = compute_fees()
        if not sample:
            return {"flights": [], "fees": fees}
        return {"flights": sample.flight_detail or [], "fees": fees}
    finally:
        db.close()


@app.post("/api/monitors/{mid}/refresh")
async def monitor_refresh(mid: int, user: User = Depends(get_current_user)):
    from app.scheduler import run_single_monitor
    await run_single_monitor(mid)
    return {"ok": True}


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
