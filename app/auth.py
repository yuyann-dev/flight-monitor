"""Auth: email verification code login + JWT session."""
import random
import string
from datetime import datetime, timedelta
from jose import jwt
from app.config import settings
from app.models import User, EmailCode, SessionLocal
from app.email_service import send_verification_code

ALGO = "HS256"


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(days=30)}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGO)


def verify_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGO])
        return int(payload["sub"])
    except Exception:
        return None


def create_login_code(email: str) -> str:
    """Generate and store a 6-digit code. Caller sends email separately."""
    code = "".join(random.choices(string.digits, k=6))
    db = SessionLocal()
    try:
        db.query(EmailCode).filter(EmailCode.email == email, EmailCode.used == False).update({"used": True})
        db.add(EmailCode(email=email, code=code))
        db.commit()
        return code
    finally:
        db.close()


def send_code_email(email: str, code: str):
    send_verification_code(email, code)


def code_cooldown_seconds(email: str, window: int = 60) -> int:
    """距离最近一条验证码还剩多少秒处于发送冷却期；返回 0 表示允许立即发送。

    无论旧码是否已使用，短时间内对同一邮箱重复下发都应限流，避免被刷发信。
    """
    db = SessionLocal()
    try:
        last = (
            db.query(EmailCode)
            .filter(EmailCode.email == email)
            .order_by(EmailCode.created_at.desc())
            .first()
        )
        if not last or not last.created_at:
            return 0
        elapsed = (datetime.utcnow() - last.created_at).total_seconds()
        return max(0, int(window - elapsed))
    finally:
        db.close()


def login_with_code(email: str, code: str) -> tuple[str, User] | None:
    db = SessionLocal()
    try:
        record = (
            db.query(EmailCode)
            .filter(EmailCode.email == email, EmailCode.code == code, EmailCode.used == False)
            .order_by(EmailCode.created_at.desc())
            .first()
        )
        if not record:
            return None
        # Expire after 10 min
        if datetime.utcnow() - record.created_at > timedelta(minutes=10):
            return None
        record.used = True

        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email)
            db.add(user)
            db.commit()
            db.refresh(user)
        db.commit()

        token = create_token(user.id)
        return token, user
    finally:
        db.close()
