"""飞猪机票爬虫 - flyai CLI（飞猪官方工具）"""
import json
import shutil
import subprocess
from app.airlines import classify, baggage_text


# 城市三字码 → 飞猪城市名映射
CITY_CODE_TO_NAME = {
    "PEK": "Beijing", "PKX": "Beijing", "BJS": "Beijing",
    "SHA": "Shanghai", "PVG": "Shanghai",
    "CAN": "Guangzhou", "SZX": "Shenzhen",
    "CTU": "Chengdu", "TFU": "Chengdu",
    "HGH": "Hangzhou", "NKG": "Nanjing", "WUH": "Wuhan",
    "XIY": "Xian", "CSX": "Changsha", "XMN": "Xiamen",
    "KMG": "Kunming", "SYX": "Sanya", "HAK": "Haikou",
    "TAO": "Qingdao", "DLC": "Dalian", "TSN": "Tianjin",
    "CGO": "Zhengzhou", "TNA": "Jinan", "FOC": "Fuzhou",
    "HFE": "Hefei", "KHN": "Nanchang", "KWE": "Guiyang",
    "NNG": "Nanning", "LHW": "Lanzhou", "URC": "Urumqi",
    "HRB": "Harbin", "SHE": "Shenyang", "CGQ": "Changchun",
    "HET": "Hohhot", "SJW": "Shijiazhuang", "TYN": "Taiyuan",
    "INC": "Yinchuan", "XNN": "Xining", "LXA": "Lhasa",
    "NGB": "Ningbo", "WNZ": "Wenzhou", "KWL": "Guilin",
    "LJG": "Lijiang", "CKG": "Chongqing",
}


def _code_to_name(code: str) -> str:
    """三字码 → 城市名"""
    return CITY_CODE_TO_NAME.get(code.upper(), code)


class FliggyCrawler:
    name = "fliggy"

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        """用 flyai CLI 搜索机票"""
        if not shutil.which("flyai"):
            print("[fliggy] flyai CLI not installed")
            return []

        cmd = [
            "flyai", "search-flight",
            "--origin", _code_to_name(dep_code),
            "--destination", _code_to_name(arr_code),
            "--dep-date", dep_date,
            "--seat-class-name", "economy",
        ]

        if ret_date:
            cmd += ["--back-date", ret_date]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                print(f"[fliggy] flyai error: {proc.stderr[:200]}")
                return []

            data = json.loads(proc.stdout)
            if data.get("status") != 0:
                print(f"[fliggy] flyai status not ok: {data.get('message')}")
                return []

            item_list = data.get("data", {}).get("itemList", [])
            flights = []

            for item in item_list:
                price = float(item.get("ticketPrice", 0))
                if price <= 0:
                    continue

                journeys = item.get("journeys", [])
                if not journeys:
                    continue

                journey = journeys[0]
                segments = journey.get("segments", [])
                if not segments:
                    continue

                first_seg = segments[0]
                last_seg = segments[-1]

                airline_name = first_seg.get("marketingTransportName", "")
                is_direct = journey.get("journeyType") == "直达"
                # 经停/中转次数：段数减一
                stops = max(0, len(segments) - 1)

                flights.append({
                    "platform": "飞猪",
                    "price": int(price),
                    "flight_no": first_seg.get("marketingTransportNo", ""),
                    "airline": airline_name,
                    "airline_category": classify(airline_name),
                    "baggage_text": baggage_text(airline_name),
                    "dep_time": first_seg.get("depDateTime", ""),
                    "arr_time": last_seg.get("arrDateTime", ""),
                    "dep_airport": first_seg.get("depStationName", ""),
                    "arr_airport": last_seg.get("arrStationName", ""),
                    "is_direct": is_direct,
                    "stops": stops,
                    "link": item.get("jumpUrl", ""),
                })

            flights.sort(key=lambda x: x["price"])
            print(f"[fliggy] got {len(flights)} flights, cheapest: ¥{flights[0]['price'] if flights else 'N/A'}")
            return flights

        except subprocess.TimeoutExpired:
            print("[fliggy] flyai timeout")
            return []
        except Exception as e:
            print(f"[fliggy] error: {e}")
            return []
