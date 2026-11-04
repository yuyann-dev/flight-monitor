"""FastAPI main app."""
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
from app.config import settings, BASE_DIR
from app.models import init_db, SessionLocal, Monitor, User, PriceSample
from app.auth import request_login_code, login_with_code, verify_token
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
async def send_code(req: LoginRequest):
    request_login_code(req.email)
    return {"ok": True, "msg": "验证码已发送"}


@app.post("/api/auth/verify")
async def verify(req: VerifyRequest):
    result = login_with_code(req.email, req.code)
    if not result:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    token, user = result
    from fastapi.responses import Response
    resp = Response(content={"ok": True, "email": user.email}, media_type="application/json")
    resp.set_cookie("token", token, max_age=30*24*3600, httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/logout")
async def logout():
    from fastapi.responses import Response
    resp = Response(content={"ok": True}, media_type="application/json")
    resp.delete_cookie("token")
    return resp


@app.get("/api/auth/me")
async def me(user: User = Depends(get_current_user)):
    return {"email": user.email}


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
                "last_checked": m.last_checked_at.isoformat() if m.last_checked_at else None,
                "is_active": m.is_active,
                "max_price": m.max_price,
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
                "time": s.checked_at.isoformat(),
                "price": s.best_price,
                "platform": s.best_platform,
            }
            for s in samples[-60:]
        ]
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
