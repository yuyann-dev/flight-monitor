"""Ctrip (携程) flight crawler - Playwright with cookie."""
import asyncio
import json
import base64
from pathlib import Path
from playwright.async_api import async_playwright
from app.email_service import send_email


class CtripCrawler:
    name = "ctrip"
    COOKIE_FILE = Path("/opt/flight-monitor/ctrip_cookie.txt")
    STATE_FILE = Path("/opt/flight-monitor/ctrip_state.json")
    EXECUTABLE = "/usr/bin/chromium-browser"
    ADMIN_EMAIL = "2719752623@qq.com"

    def _load_cookie_str(self) -> str:
        if not self.COOKIE_FILE.exists():
            return ""
        return self.COOKIE_FILE.read_text().strip()

    def _cookie_str_to_playwright_cookies(self, cookie_str: str) -> list[dict]:
        cookies = []
        for item in cookie_str.split("; "):
            if "=" in item:
                name, value = item.split("=", 1)
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": ".ctrip.com",
                    "path": "/",
                })
        return cookies

    async def search_async(self, dep_code: str, arr_code: str, dep_date: str,
                           ret_date: str | None = None) -> list[dict]:
        cookie_str = self._load_cookie_str()
        if not cookie_str:
            print("[ctrip] no cookie, skip")
            return []

        trip_type = "oneway" if not ret_date else "round"
        url = f"https://flights.ctrip.com/online/list/{trip_type}-{dep_code}-{arr_code}?depdate={dep_date}"

        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=self.EXECUTABLE,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 反检测：隐藏 webdriver 特征
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)

        await context.add_cookies(self._cookie_str_to_playwright_cookies(cookie_str))

        page = await context.new_page()
        flights = []

        try:
            # 先打开首页，模拟真实用户行为
            await page.goto("https://flights.ctrip.com/", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # 再导航到搜索页
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(8000)

            # 检测是否触发验证码
            page_content = await page.content()
            if "验证码" in page_content or "captcha" in page_content.lower() or "whaleguard" in page_content.lower():
                print("[ctrip] 触发验证码，截图发邮件")
                screenshot = await page.screenshot()
                screenshot_b64 = base64.b64encode(screenshot).decode()
                send_email(
                    self.ADMIN_EMAIL,
                    "【机票监控】携程触发验证码，请手动处理",
                    f"<p>携程爬虫触发了验证码，请手动登录服务器处理。</p><p>验证码截图：</p><img src='data:image/png;base64,{screenshot_b64}' style='max-width:600px;'>"
                )
                return flights

            # 提取所有价格
            price_elements = await page.query_selector_all("span, div")
            prices = set()
            for el in price_elements:
                try:
                    text = await el.inner_text()
                    text = text.strip()
                    if text.startswith("¥") and len(text) <= 8:
                        price_str = text[1:]
                        if price_str.isdigit():
                            p = int(price_str)
                            if 100 < p < 20000:
                                prices.add(p)
                except Exception:
                    continue

            for p in sorted(prices):
                flights.append({
                    "platform": "携程",
                    "price": p,
                    "raw_text": f"携程机票 ¥{p}",
                    "link": url,
                })

        except Exception as e:
            print(f"[ctrip] error: {e}")
        finally:
            await context.close()
            await browser.close()
            await pw.stop()

        return flights

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        return asyncio.run(self.search_async(dep_code, arr_code, dep_date, ret_date))
