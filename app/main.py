"""FastAPI main app."""
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
from app.config import settings, BASE_DIR
from app.models import init_db, SessionLocal, Monitor, User, PriceSample
from app.auth import create_login_code, send_code_email, login_with_code, verify_token
from app.scheduler import start_scheduler

app = FastAPI(title="Flight Monitor")
init_db()


# ── Auth middleware ──
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


# ── Pydantic schemas ──
class LoginRequest(BaseModel):
    email: str

class VerifyRequest(BaseModel):
    email: str
    code: str

class MonitorCreate(BaseModel):
    departure_city: str
    arrival_city: str
    departure_code: str = ""
    arrival_code: str = ""
    departure_date_start: str
    departure_date_end: str
    return_date_start: str | None = None
    return_date_end: str | None = None
    cabin_class: str = "economy"
    direct_only: bool = False
    baggage_required: bool = False
    max_price: int | None = None
    departure_time_windows: list = []
    arrival_time_windows: list = []


# ── Auth routes ──
@app.post("/api/auth/send-code")
async def send_code(req: LoginRequest, bg: BackgroundTasks):
    code = create_login_code(req.email)
    bg.add_task(send_code_email, req.email, code)
    return {"ok": True, "msg": "验证码已发送"}


@app.post("/api/auth/verify")
async def verify(req: VerifyRequest):
    result = login_with_code(req.email, req.code)
    if not result:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    token, user = result
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"ok": True, "email": user.email})
    resp.set_cookie("token", token, max_age=30*24*3600, httponly=True, samesite="lax")
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


# ── Query price on demand ──
class PriceQueryReq(BaseModel):
    dep: str
    arr: str
    date: str

@app.post("/api/query-price")
async def query_price(req: PriceQueryReq, user: User = Depends(get_current_user)):
    from app.crawlers import crawl_all
    from app.analyzer import industry_rule_score
    result = await crawl_all(req.dep, req.arr, req.date)
    if not result["best_price"]:
        return {"best_price": None}

    # 推荐价位算法：当前价 × 折扣系数，结合距出发天数
    rule = industry_rule_score(req.date)
    days_left = rule["days_left"]

    # 距出发越远，通常还会降，建议设更低
    if days_left > 60:
        discount = 0.80   # 两个月以上，建议等降20%
    elif days_left > 30:
        discount = 0.88   # 1-2个月，建议等降12%
    elif days_left > 14:
        discount = 0.93   # 2周-1月，建议等降7%
    elif days_left > 7:
        discount = 0.97   # 1-2周，建议等降3%
    else:
        discount = 1.0    # 一周内，别等了

    suggested = int(result["best_price"] * discount)
    return {
        "best_price": result["best_price"],
        "best_platform": result["best_platform"],
        "suggested_price": suggested,
        "days_left": days_left,
        "tip": rule["reasons"][0] if rule["reasons"] else "",
    }


# ── Monitor CRUD ──
@app.get("/api/monitors")
async def list_monitors(user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        monitors = db.query(Monitor).filter(Monitor.user_id == user.id, Monitor.is_active == True).all()
        return [
            {
                "id": m.id,
                "departure_city": m.departure_city,
                "arrival_city": m.arrival_city,
                "departure_date": m.departure_date_start,
                "return_date": m.return_date_start,
                "best_price": m.best_price_ever,
                "last_checked": m.last_checked_at.isoformat() + "Z" if m.last_checked_at else None,
                "is_active": m.is_active,
                "max_price": m.max_price,
                "cabin_class": m.cabin_class,
            }
            for m in monitors
        ]
    finally:
        db.close()


@app.post("/api/monitors")
async def create_monitor(req: MonitorCreate, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        m = Monitor(
            user_id=user.id,
            departure_city=req.departure_city,
            arrival_city=req.arrival_city,
            departure_code=req.departure_code,
            arrival_code=req.arrival_code,
            departure_date_start=req.departure_date_start,
            departure_date_end=req.departure_date_end,
            return_date_start=req.return_date_start,
            return_date_end=req.return_date_end,
            cabin_class=req.cabin_class,
            direct_only=req.direct_only,
            baggage_required=req.baggage_required,
            max_price=req.max_price,
            departure_time_windows=req.departure_time_windows,
            arrival_time_windows=req.arrival_time_windows,
        )
        db.add(m)
        db.commit()
        # 后台异步跑一次爬虫
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


@app.get("/api/monitors/{mid}/history")
async def monitor_history(mid: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        samples = db.query(PriceSample).filter(
            PriceSample.monitor_id == mid
        ).order_by(PriceSample.checked_at).all()
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


@app.get("/api/monitors/{mid}/latest-detail")
async def monitor_latest_detail(mid: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        sample = db.query(PriceSample).filter(
            PriceSample.monitor_id == mid
        ).order_by(PriceSample.checked_at.desc()).first()
        if not sample:
            return {"flights": []}
        return {"flights": sample.flight_detail or []}
    finally:
        db.close()


@app.post("/api/monitors/{mid}/refresh")
async def monitor_refresh(mid: int, user: User = Depends(get_current_user)):
    import asyncio
    from app.scheduler import run_single_monitor
    await run_single_monitor(mid)
    return {"ok": True}


# ── Crawler callback API (for GitHub Actions) ──
class CrawlerResult(BaseModel):
    dep: str
    arr: str
    date: str
    platform: str
    prices: list[int]


@app.post("/api/crawler-results")
async def receive_crawler_results(results: list[CrawlerResult], request: Request):
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer ") or token[7:] != settings.crawler_token:
        raise HTTPException(status_code=403, detail="非法调用")
    
    db = SessionLocal()
    try:
        for r in results:
            best = min(r.prices) if r.prices else None
            # 找到对应的监控任务
            monitors = db.query(Monitor).filter(
                Monitor.dep_code == r.dep,
                Monitor.arr_code == r.arr,
                Monitor.departure_date_start <= r.date,
                Monitor.departure_date_end >= r.date,
                Monitor.is_active == True,
            ).all()
            for m in monitors:
                sample = PriceSample(
                    monitor_id=m.id,
                    best_price=best,
                    best_platform=r.platform,
                    raw_data=str(r.prices),
                )
                db.add(sample)
        db.commit()
        return {"ok": True, "count": len(results)}
    finally:
        db.close()


# ── Static frontend ──
static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend():
    return FileResponse(static_dir / "index.html")


# ── Startup ──
@app.on_event("startup")
async def startup():
    start_scheduler()
