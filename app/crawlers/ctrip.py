"""Ctrip (携程) flight crawler."""
import re
from app.crawlers.base import BaseCrawler


class CtripCrawler(BaseCrawler):
    name = "ctrip"
    BASE = "https://flights.ctrip.com/online/list"

    async def search(self, dep_code: str, arr_code: str, dep_date: str,
                     ret_date: str | None = None, cabin: str = "economy",
                     direct_only: bool = False) -> list[dict]:
        cabin_map = {"economy": "y", "business": "c", "first": "f"}
        cabin_char = cabin_map.get(cabin, "y")
        trip_type = "oneway" if not ret_date else "round"

        url = f"{self.BASE}/{trip_type}-{dep_code}-{arr_code}?depdate={dep_date}&cabin={cabin_char}_s_c_f"
        if ret_date:
            url += f"&rtdate={ret_date}"

        context = await self.new_context()
        page = await context.new_page()
        flights = []
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)  # 等待动态加载

            # 提取航班卡片
            cards = await page.query_selector_all(".flight-list, .list-item, [class*='flight-item']")
            if not cards:
                # 备用选择器
                cards = await page.query_selector_all("div[class*='FlightItem'], div[class*='list-item']")

            for card in cards[:15]:
                try:
                    text = await card.inner_text()
                    price_match = re.search(r"¥?(\d{3,5})", text)
                    if not price_match:
                        continue
                    price = int(price_match.group(1))

                    flights.append({
                        "platform": "携程",
                        "price": price,
                        "raw_text": text[:300],
                        "link": url,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[ctrip] crawl error: {e}")
        finally:
            await context.close()

        flights.sort(key=lambda x: x["price"])
        return flights
