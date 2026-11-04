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
