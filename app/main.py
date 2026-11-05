"""FastAPI main app."""
import asyncio
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from app.config import BASE_DIR, settings
from app.models import init_db, SessionLocal, Monitor, User, PriceSample
from app.auth import create_login_code, send_code_email, login_with_code, verify_token, code_cooldown_seconds
from app.rbac import role_of, is_owner, is_high_tier
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


class RoleUpdate(BaseModel):
    role: str


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
    r = role_of(user)
    return {"email": user.email, "role": r, "is_owner": r == "owner", "is_vip": r == "vip"}


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
        fees = compute_fees(departure=monitor.departure_city, arrival=monitor.arrival_city)
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
    finally:
        db.close()

    role = role_of(user)
    today = _time.strftime("%Y-%m-%d")
    data_dir = Path(settings.database_url.replace("sqlite:///", "")).parent

    # 主人：飞猪手动不限次；飞常准每日最多3次，仅飞猪失败时才消耗
    if role == "owner":
        quota_file = data_dir / ".variflight_daily_quota"
        used_today = 0
        if quota_file.exists():
            try:
                d, c = quota_file.read_text().strip().split(":")
                if d == today:
                    used_today = int(c)
            except Exception:
                pass
        allow_variflight = used_today < 3
        variflight_used = await run_single_monitor(mid, manual_refresh=True, allow_variflight=allow_variflight)
        if variflight_used:
            quota_file.write_text(f"{today}:{used_today + 1}")
            return {"ok": True, "used_variflight": True, "variflight_left": 3 - used_today - 1}
        if not allow_variflight:
            return {"ok": True, "note": "飞常准今日3次已用完，本次仅用飞猪查询"}
        return {"ok": True}

    # VIP（轻量档）：飞猪手动刷新不限次；不启用飞常准（免费备用源仅留给主人）
    if role == "vip":
        await run_single_monitor(mid, manual_refresh=True, allow_variflight=False)
        return {"ok": True, "tier": "vip"}

    # 普通用户：飞猪手动刷新每日10次；不启用飞常准
    other_quota = data_dir / ".other_refresh_quota"
    used = 0
    if other_quota.exists():
        try:
            d, c = other_quota.read_text().strip().split(":")
            if d == today:
                used = int(c)
        except Exception:
            pass
    if used >= 10:
        return {"ok": False, "msg": "今日手动刷新次数已用完（10次），明天自动恢复"}
    await run_single_monitor(mid, manual_refresh=True, allow_variflight=False)
    other_quota.write_text(f"{today}:{used + 1}")
    return {"ok": True, "refresh_left": 10 - used - 1}


_CST = timezone(timedelta(hours=8))


def _fmt_cst(dt):
    """数据库按 UTC naive 存储，统一格式化为北京时间展示，避免前端直接截字符串错位。"""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M")


# ── 管理员：用户与角色（仅系统主人）──
@app.get("/api/admin/users")
async def admin_list_users(user: User = Depends(get_current_user)):
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="无管理员权限")
    db = SessionLocal()
    try:
        out = []
        for u in db.query(User).order_by(User.id).all():
            total = db.query(Monitor).filter(Monitor.user_id == u.id).count()
            active = db.query(Monitor).filter(
                Monitor.user_id == u.id, Monitor.is_active == True
            ).count()
            out.append({
                "id": u.id,
                "email": u.email,
                "role": role_of(u),
                "created_at": _fmt_cst(u.created_at),
                "monitor_count": total,
                "active_count": active,
            })
        return out
    finally:
        db.close()


@app.post("/api/admin/users/{uid}/role")
async def admin_set_role(uid: int, body: RoleUpdate, user: User = Depends(get_current_user)):
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="无管理员权限")
    if body.role not in ("vip", "normal"):
        raise HTTPException(status_code=400, detail="角色只能设为 vip 或 normal")
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == uid).first()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target.email == settings.owner_email:
            raise HTTPException(status_code=400, detail="系统主人账号角色不可修改")
        target.role = body.role
        db.commit()
        return {"ok": True, "id": target.id, "role": role_of(target)}
    finally:
        db.close()


@app.get("/api/admin/users/{uid}/monitors")
async def admin_user_monitors(uid: int, user: User = Depends(get_current_user)):
    """主人查看指定用户名下的监控任务明细（只读，不修改角色）。"""
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="无管理员权限")
    db = SessionLocal()
    try:
        target = db.query(User).filter(User.id == uid).first()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        rows = (
            db.query(Monitor)
            .filter(Monitor.user_id == uid)
            .order_by(Monitor.is_active.desc(), Monitor.id.asc())
            .all()
        )
        out = []
        for m in rows:
            last = (
                db.query(PriceSample)
                .filter(PriceSample.monitor_id == m.id)
                .order_by(PriceSample.checked_at.desc())
                .first()
            )
            date_range = (
                f"{m.departure_date_start} ~ {m.departure_date_end}"
                if m.departure_date_end and m.departure_date_end != m.departure_date_start
                else m.departure_date_start
            )
            out.append({
                "id": m.id,
                "route": f"{m.departure_city}→{m.arrival_city}",
                "date_range": date_range,
                "target_price": m.max_price,
                "active": bool(m.is_active),
                "direct_only": bool(m.direct_only),
                "baggage_required": bool(m.baggage_required),
                "latest_price": last.best_price if last else None,
                "platform": last.best_platform if last else None,
                "best_price_ever": m.best_price_ever,
                "last_success_at": _fmt_cst(m.last_success_at),
                "created_at": _fmt_cst(m.created_at),
            })
        return out
    finally:
        db.close()


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
    # 幂等锚定主人账号角色（权限以邮箱权威判定，DB 角色字段供列表展示与 VIP 标记用）
    if settings.owner_email:
        db = SessionLocal()
        try:
            owner = db.query(User).filter(User.email == settings.owner_email).first()
            if owner and owner.role != "owner":
                owner.role = "owner"
                db.commit()
        finally:
            db.close()
    start_scheduler()
