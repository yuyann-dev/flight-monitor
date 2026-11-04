"""Tongcheng (同程) flight crawler."""
import re
from app.crawlers.base import BaseCrawler


class TongchengCrawler(BaseCrawler):
    name = "tongcheng"
    BASE = "https://www.ly.com/flights/itinerary/oneway"

    async def search(self, dep_code: str, arr_code: str, dep_date: str,
                     ret_date: str | None = None, cabin: str = "economy",
                     direct_only: bool = False) -> list[dict]:
        url = f"{self.BASE}/{dep_code}-{arr_code}?date={dep_date}"

        context = await self.new_context()
        page = await context.new_page()
        flights = []
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)

            cards = await page.query_selector_all("[class*='flight-item'], [class*='FlightItem'], .flight-list-item")
            for card in cards[:15]:
                try:
                    text = await card.inner_text()
                    price_match = re.search(r"¥?(\d{3,5})", text)
                    if not price_match:
                        continue
                    price = int(price_match.group(1))
                    flights.append({
                        "platform": "同程",
                        "price": price,
                        "raw_text": text[:300],
                        "link": url,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[tongcheng] crawl error: {e}")
        finally:
            await context.close()

        flights.sort(key=lambda x: x["price"])
        return flights
