"""Crawler running on GitHub Actions, posts results to our server."""
import asyncio
import os
import json
import httpx
from playwright.async_api import async_playwright

SERVER_URL = os.environ.get("SERVER_URL", "https://yystudy.top/flight")
SERVER_TOKEN = os.environ.get("SERVER_TOKEN", "")

# 这里先写死几个测试航线，后续从服务器拉取监控列表
TEST_ROUTES = [
    {"dep": "CTU", "arr": "PVG", "date": "2026-12-15"},
    {"dep": "CTU", "arr": "PEK", "date": "2026-12-20"},
]


async def crawl_ctrip(page, dep, arr, date):
    url = f"https://flights.ctrip.com/online/list/oneway-{dep}-{arr}?depdate={date}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(8000)
        prices = await page.evaluate("""
            () => {
                const els = document.querySelectorAll('.base-od-list-box--price, [class*="price--"], .price');
                return Array.from(els).map(e => e.innerText.replace(/[^0-9]/g, '')).filter(Boolean).slice(0, 10);
            }
        """)
        return [int(p) for p in prices if len(p) >= 3]
    except Exception as e:
        print(f"ctrip error: {e}")
        return []


async def crawl_qunar(page, dep, arr, date):
    url = f"https://flight.qunar.com/site/oneway_list.htm?searchDepartureAirport={dep}&searchArrivalAirport={arr}&searchDepartureDate={date}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(10000)
        prices = await page.evaluate("""
            () => {
                const els = document.querySelectorAll('.m--layer--price, [class*="price"]');
                return Array.from(els).map(e => e.innerText.replace(/[^0-9]/g, '')).filter(Boolean).slice(0, 10);
            }
        """)
        return [int(p) for p in prices if len(p) >= 3]
    except Exception as e:
        print(f"qunar error: {e}")
        return []


async def main():
    results = []
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0")
    page = await ctx.new_page()

    for route in TEST_ROUTES:
        ctrip_prices = await crawl_ctrip(page, route["dep"], route["arr"], route["date"])
        qunar_prices = await crawl_qunar(page, route["dep"], route["arr"], route["date"])
        
        if ctrip_prices:
            results.append({**route, "platform": "携程", "prices": ctrip_prices})
        if qunar_prices:
            results.append({**route, "platform": "去哪儿", "prices": qunar_prices})
        
        await asyncio.sleep(3)

    await browser.close()
    await pw.stop()

    # POST 到服务器
    if results:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SERVER_URL}/api/crawler-results",
                json=results,
                headers={"Authorization": f"Bearer {SERVER_TOKEN}"},
                timeout=10,
            )
            print(f"posted {len(results)} results, status={r.status_code}")
    else:
        print("no results found")


if __name__ == "__main__":
    asyncio.run(main())
