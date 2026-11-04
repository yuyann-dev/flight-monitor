"""Ctrip (携程) flight crawler - httpx + cookie."""
import re
import httpx
from pathlib import Path


class CtripCrawler:
    name = "ctrip"
    COOKIE_FILE = Path("/opt/flight-monitor/ctrip_cookie.txt")
    BASE = "https://flights.ctrip.com/online/list"

    def _load_cookie_str(self) -> str:
        if not self.COOKIE_FILE.exists():
            return ""
        return self.COOKIE_FILE.read_text().strip()

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        cookie_str = self._load_cookie_str()
        if not cookie_str:
            print("[ctrip] no cookie, skip")
            return []

        trip_type = "oneway" if not ret_date else "round"
        url = f"{self.BASE}/{trip_type}-{dep_code}-{arr_code}?depdate={dep_date}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Cookie": cookie_str,
            "Referer": "https://flights.ctrip.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        try:
            r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                print(f"[ctrip] status {r.status_code}")
                return []
            html = r.text
        except Exception as e:
            print(f"[ctrip] request error: {e}")
            return []

        # 检测是否被封
        if "whaleguard" in html.lower() or "验证码" in html:
            print("[ctrip] blocked or captcha")
            return []

        # 从 HTML 里提取价格
        flights = self._parse_prices(html, url)
        flights.sort(key=lambda x: x["price"])
        return flights

    def _parse_prices(self, html: str, source_url: str) -> list[dict]:
        flights = []

        # 正则找所有 ¥数字
        price_matches = re.findall(r'[¥￥]\s*(\d{3,5})', html)
        seen = set()
        for pm in price_matches:
            p = int(pm)
            if 100 < p < 20000 and p not in seen:
                seen.add(p)
                flights.append({
                    "platform": "携程",
                    "price": p,
                    "raw_text": f"携程机票 ¥{p}",
                    "link": source_url,
                })

        return flights[:30]
