"""Qunar (去哪儿) flight crawler."""
import re
from app.crawlers.base import BaseCrawler


class QunarCrawler(BaseCrawler):
    name = "qunar"
    BASE = "https://flight.qunar.com/site/oneway_list.htm"

    async def search(self, dep_code: str, arr_code: str, dep_date: str,
                     ret_date: str | None = None, cabin: str = "economy",
                     direct_only: bool = False) -> list[dict]:
        url = f"{self.BASE}?searchDepartureAirport={dep_code}&searchArrivalAirport={arr_code}&searchDepartureDate={dep_date}"

        context = await self.new_context()
        page = await context.new_page()
        flights = []
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)

            cards = await page.query_selector_all(".m-airfly-list, [class*='aircraft-item'], .b-airfly")
            for card in cards[:15]:
                try:
                    text = await card.inner_text()
                    price_match = re.search(r"¥?(\d{3,5})", text)
                    if not price_match:
                        continue
                    price = int(price_match.group(1))
                    flights.append({
                        "platform": "去哪儿",
                        "price": price,
                        "raw_text": text[:300],
                        "link": url,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[qunar] crawl error: {e}")
        finally:
            await context.close()

        flights.sort(key=lambda x: x["price"])
        return flights
