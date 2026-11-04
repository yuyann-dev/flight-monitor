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

    # SMTP (发件邮箱)
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""  # 授权码
    smtp_from: str = ""
    admin_email: str = ""  # 管理员邮箱，用于cookie过期提醒

    # 抓取配置
    crawl_interval_minutes: int = 180  # 默认3小时一轮
    request_delay_min: float = 3.0     # 平台间最小延迟(秒)
    request_delay_max: float = 8.0     # 平台间最大延迟(秒)
    max_retries: int = 2
    headless: bool = True
    proxy_url: str = ""  # 代理地址，如 "http://user:pass@proxy:port"，留空直连
    crawler_token: str = "change-me-crawler-token"  # GitHub Actions 爬虫回调 token

    # 费用（含税计算）
    airport_construction_fee: int = 50       # 机建费
    fuel_surcharge_short: int = 80           # 800km以下燃油
    fuel_surcharge_long: int = 140           # 800km以上燃油
    short_haul_threshold_km: int = 800

    # 通知防打扰
    notify_cooldown_hours: int = 6            # 同航线冷却
    notify_price_drop_pct: float = 5.0       # 再降5%才二次提醒

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
