"""Simple HTTP-based crawler using httpx instead of Playwright."""
import httpx
import re
import random
import asyncio
from app.config import settings


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


async def crawl_ctrip(dep: str, arr: str, date: str) -> list[dict]:
    """Ctrip flight search via web API."""
    url = f"https://flights.ctrip.com/online/list/oneway-{dep}-{arr}?depdate={date}"
    headers = {"User-Agent": UA, "Referer": "https://flights.ctrip.com/"}
    flights = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            # 从页面中提取价格
            prices = re.findall(r'"price":(\d{3,5})', r.text)
            for p in prices[:10]:
                flights.append({"platform": "携程", "price": int(p), "link": url})
    except Exception as e:
        print(f"[ctrip] error: {e}")
    return flights


async def crawl_qunar(dep: str, arr: str, date: str) -> list[dict]:
    """Qunar flight search."""
    url = f"https://flight.qunar.com/site/oneway_list.htm?searchDepartureAirport={dep}&searchArrivalAirport={arr}&searchDepartureDate={date}"
    headers = {"User-Agent": UA}
    flights = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            prices = re.findall(r'"lowestPrice":(\d{3,5})', r.text)
            if not prices:
                prices = re.findall(r">¥(\d{3,5})<", r.text)
            for p in prices[:10]:
                flights.append({"platform": "去哪儿", "price": int(p), "link": url})
    except Exception as e:
        print(f"[qunar] error: {e}")
    return flights


async def crawl_fliggy(dep: str, arr: str, date: str) -> list[dict]:
    """Fliggy (支付宝/飞猪) flight search."""
    url = f"https://sj-h5.fliggy.com/flight/search?depCity={dep}&arrCity={arr}&depDate={date}"
    headers = {"User-Agent": UA}
    flights = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            prices = re.findall(r'"price":(\d{3,5})', r.text)
            for p in prices[:10]:
                flights.append({"platform": "飞猪", "price": int(p), "link": url})
    except Exception as e:
        print(f"[fliggy] error: {e}")
    return flights


async def crawl_all(dep: str, arr: str, date: str, ret_date: str | None = None,
                    cabin: str = "economy", direct_only: bool = False) -> dict:
    """Run all crawlers and aggregate."""
    results = {}
    all_flights = []
    tasks = [crawl_ctrip(dep, arr, date), crawl_qunar(dep, arr, date), crawl_fliggy(dep, arr, date)]
    for t in asyncio.as_completed(tasks):
        try:
            flights = await t
            if flights:
                all_flights.extend(flights)
                results[flights[0]["platform"]] = flights[0]["price"]
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2, 5))

    all_flights.sort(key=lambda x: x["price"])
    return {
        "platform_prices": results,
        "best_price": all_flights[0]["price"] if all_flights else None,
        "best_platform": all_flights[0]["platform"] if all_flights else None,
        "flights": all_flights[:10],
    }
