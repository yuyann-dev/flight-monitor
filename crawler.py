"""Crawler running on GitHub Actions - 携程两步法 + XHR拦截"""
import asyncio
import os
import json
import httpx
from playwright.async_api import async_playwright

SERVER_URL = os.environ.get("SERVER_URL", "https://yystudy.top/flight")
SERVER_TOKEN = os.environ.get("SERVER_TOKEN", "")

# 测试航线
TEST_ROUTES = [
    {"dep": "CTU", "arr": "PVG", "date": "2026-12-15"},
    {"dep": "CTU", "arr": "PEK", "date": "2026-12-20"},
]


async def crawl_ctrip(page, dep, arr, date):
    """携程两步法：先种cookie，再XHR拦截batchSearch"""
    captured = []

    def on_response(response):
        if "batchSearch" not in response.url:
            return
        try:
            body = response.json()
            flight_list = body.get("data", {}).get("flightItineraryList", [])
            if flight_list:
                captured.append(body)
        except Exception:
            pass

    page.on("response", on_response)

    # 第一步：访问首页种 cookie
    print(f"[ctrip] step1: visit homepage...")
    await page.goto("https://www.ctrip.com/", timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # 第二步：访问搜索页
    url = f"https://flights.ctrip.com/online/list/oneway-{dep}-{arr}?_=1&depdate={date}&cabin=Y_S_C_F&adult=1"
    print(f"[ctrip] step2: goto search {url}")
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")

    # 等待 batchSearch XHR
    for i in range(90):
        if captured:
            break
        await page.wait_for_timeout(1000)

    if not captured:
        print(f"[ctrip] no batchSearch captured")
        return []

    # 解析结果
    raw = max(captured, key=lambda d: len(d.get("data", {}).get("flightItineraryList", [])))
    flights = []
    for it in raw.get("data", {}).get("flightItineraryList", [])[:20]:
        seg0 = it.get("flightSegments", [{}])[0]
        fl0 = seg0.get("flightList", [{}])[0]
        if not fl0.get("flightNo"):
            continue
        prices = it.get("priceList", [])
        price = next((float(p["adultPrice"]) for p in prices if p.get("adultPrice")), None)
        if not price:
            continue
        flights.append({
            "flight_no": fl0.get("flightNo"),
            "price": int(price),
            "airline": fl0.get("marketAirlineName"),
            "dep_time": fl0.get("departureDateTime"),
            "arr_time": fl0.get("arrivalDateTime"),
            "platform": "携程",
        })

    print(f"[ctrip] got {len(flights)} flights")
    return flights


async def main():
    results = []
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
        locale="zh-CN",
        viewport={"width": 1440, "height": 900},
    )
    page = await ctx.new_page()

    for route in TEST_ROUTES:
        try:
            flights = await crawl_ctrip(page, route["dep"], route["arr"], route["date"])
            if flights:
                results.append({**route, "platform": "携程", "flights": flights})
        except Exception as e:
            print(f"[main] route error: {e}")
        await asyncio.sleep(3)

    await browser.close()
    await pw.stop()

    # POST 到服务器
    if results:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SERVER_URL}/api/crawler-results",
                json=[{"dep": r["dep"], "arr": r["arr"], "date": r["date"], "platform": r["platform"], "prices": [f["price"] for f in r["flights"]]} for r in results],
                headers={"Authorization": f"Bearer {SERVER_TOKEN}"},
                timeout=10,
            )
            print(f"posted {len(results)} routes, status={r.status_code}")
    else:
        print("no results found")


if __name__ == "__main__":
    asyncio.run(main())
