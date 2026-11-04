"""Database setup and models."""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, JSON
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

    # 航线
    departure_city = Column(String(100), nullable=False)   # e.g. 成都
    arrival_city = Column(String(100), nullable=False)      # e.g. 上海
    departure_code = Column(String(10))                     # CTU/TFU
    arrival_code = Column(String(10))                        # PVG/SHA

    # 日期范围
    departure_date_start = Column(String(20), nullable=False)  # 2026-01-15
    departure_date_end = Column(String(20), nullable=False)
    return_date_start = Column(String(20), nullable=True)      # 往返
    return_date_end = Column(String(20), nullable=True)

    # 偏好
    cabin_class = Column(String(20), default="economy")  # economy/business/first
    direct_only = Column(Boolean, default=False)
    baggage_required = Column(Boolean, default=False)    # 需要免费托运
    max_price = Column(Integer, nullable=True)            # 价格上限
    departure_time_windows = Column(JSON, default=list)  # [[6,12],[18,23]] 出发时段
    arrival_time_windows = Column(JSON, default=list)    # 到达时段
    preferred_airlines = Column(JSON, default=list)
    excluded_flights = Column(JSON, default=list)        # 被用户剔除的航班 [{flight_no, date}]

    # 状态
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime, nullable=True)
    best_price_ever = Column(Float, nullable=True)        # 历史最低价
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
    return_date = Column(String(20), nullable=True)

    # 各平台最低价
    ctrip_price = Column(Float, nullable=True)
    qunar_price = Column(Float, nullable=True)
    fliggy_price = Column(Float, nullable=True)
    tongcheng_price = Column(Float, nullable=True)
    best_price = Column(Float, nullable=False)
    best_platform = Column(String(50))

    # 推荐航班详情 JSON
    flight_detail = Column(JSON)  # {flight_no, airline, dep_time, arr_time, duration, stops, baggage, link}


class EmailCode(Base):
    __tablename__ = "email_codes"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), index=True, nullable=False)
    code = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used = Column(Boolean, default=False)


def init_db():
    Base.metadata.create_all(bind=engine)
