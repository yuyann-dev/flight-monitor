"""Configuration loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # App
    app_name: str = "Flight Monitor"
    host: str = "127.0.0.1"
    port: int = 8787
    secret_key: str = "change-me-in-production"
    frontend_base_path: str = "/flight"

    # Database
    database_url: str = f"sqlite:///{BASE_DIR}/data/flight_monitor.db"

    # SMTP（发件邮箱）
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""  # 授权码
    smtp_from: str = ""

    # 抓取配置
    crawl_interval_minutes: int = 30  # 每轮间隔（分钟）

    # 多数据源：飞常准 MCP（留空则仅使用飞猪）
    variflight_mcp_key: str = ""
    # 飞常准最小调用间隔（小时）：0=不节流；>0 时用于把每月调用控制在免费额度内。
    # 每次触发产生 2 次工具调用，15 小时间隔约 96 次/月，低于 100 次免费额度
    variflight_min_interval_hours: float = 6.0
    # 系统主人邮箱：主人有活跃任务时，飞常准额度仅服务于主人
    owner_email: str = ""

    # 费用（含税计算）
    airport_construction_fee: int = 50       # 机建费
    fuel_surcharge_short: int = 80           # 800km 以下燃油
    fuel_surcharge_long: int = 140           # 800km 以上燃油
    short_haul_threshold_km: int = 800

    # 通知防打扰
    notify_cooldown_hours: int = 6           # 同航线冷却
    notify_price_drop_pct: float = 5.0       # 再降一定比例才二次提醒

    # 忽略 .env 中残留的废弃字段，避免环境变量清理不彻底导致启动失败
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
