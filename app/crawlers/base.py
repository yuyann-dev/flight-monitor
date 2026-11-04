"""Base crawler with anti-detection measures."""
import asyncio
import random
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from app.config import settings


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


class BaseCrawler:
    name = "base"

    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=settings.headless,
            executable_path="/usr/bin/chromium-browser",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def new_context(self) -> BrowserContext:
        ua = random.choice(USER_AGENTS)
        context = await self.browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        # 注入反检测脚本
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = { runtime: {} };
        """)
        return context

    async def random_delay(self):
        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))

    async def search(self, dep: str, arr: str, dep_date: str, ret_date: str | None = None,
                     cabin: str = "economy", direct_only: bool = False) -> list[dict]:
        """Override in subclass. Returns list of flight dicts."""
        raise NotImplementedError
