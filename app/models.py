"""Database setup and models."""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from app.config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    monitors = relationship("Monitor", back_populates="owner")


class Monitor(Base):
    __tablename__ = "monitors"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    owner = relationship("User", back_populates="monitors")

    # 航线（仅用城市名，三字码在 scheduler 内转换）
    departure_city = Column(String(100), nullable=False)
    arrival_city = Column(String(100), nullable=False)

    # 出发日期范围（单程；单日时 end == start）
    departure_date_start = Column(String(20), nullable=False)
    departure_date_end = Column(String(20), nullable=False)

    # 偏好
    cabin_class = Column(String(20), default="economy")
    direct_only = Column(Boolean, default=False)
    baggage_required = Column(Boolean, default=False)
    max_price = Column(Integer, nullable=True)
    departure_time_windows = Column(JSON, default=list)
    arrival_time_windows = Column(JSON, default=list)
    excluded_flights = Column(JSON, default=list)

    # 状态
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime, nullable=True)
    best_price_ever = Column(Float, nullable=True)
    last_notified_at = Column(DateTime, nullable=True)
    last_notified_price = Column(Float, nullable=True)

    price_samples = relationship("PriceSample", back_populates="monitor")


class PriceSample(Base):
    __tablename__ = "price_samples"
    id = Column(Integer, primary_key=True, index=True)
    monitor_id = Column(Integer, ForeignKey("monitors.id"), nullable=False)
    monitor = relationship("Monitor", back_populates="price_samples")

    checked_at = Column(DateTime, default=datetime.utcnow, index=True)
    departure_date = Column(String(20))

    best_price = Column(Float, nullable=False)
    best_platform = Column(String(50))

    # 完整航班详情（后端已过滤、排序）
    flight_detail = Column(JSON)


class EmailCode(Base):
    __tablename__ = "email_codes"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), index=True, nullable=False)
    code = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used = Column(Boolean, default=False)


def init_db():
    Base.metadata.create_all(bind=engine)
