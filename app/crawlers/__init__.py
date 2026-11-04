"""Crawler registry - run all platforms and aggregate results."""
import asyncio
from app.crawlers.ctrip import CtripCrawler
from app.crawlers.qunar import QunarCrawler
from app.crawlers.fliggy import FliggyCrawler
from app.crawlers.tongcheng import TongchengCrawler
from app.config import settings


CRAWLERS = [CtripCrawler, QunarCrawler, FliggyCrawler, TongchengCrawler]


async def crawl_all(dep_code: str, arr_code: str, dep_date: str,
                    ret_date: str | None = None, cabin: str = "economy",
                    direct_only: bool = False) -> dict:
    """Run all crawlers sequentially with random delays between them.
    Returns {platform: lowest_price} and aggregated flight list.
    """
    results = {}
    all_flights = []
    crawler_instances = [cls() for cls in CRAWLERS]

    try:
        for inst in crawler_instances:
            try:
                await inst.start()
                flights = await inst.search(
                    dep_code, arr_code, dep_date, ret_date, cabin, direct_only
                )
                if flights:
                    results[inst.name] = flights[0]["price"]
                    all_flights.extend(flights)
            except Exception as e:
                print(f"[{inst.name}] failed: {e}")
            finally:
                await inst.stop()
                await asyncio.sleep(
                    __import__("random").uniform(settings.request_delay_min, settings.request_delay_max)
                )
    finally:
        pass

    all_flights.sort(key=lambda x: x["price"])
    return {
        "platform_prices": results,
        "best_price": all_flights[0]["price"] if all_flights else None,
        "best_platform": all_flights[0]["platform"] if all_flights else None,
        "flights": all_flights[:10],
    }
