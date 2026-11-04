"""飞猪机票爬虫 - flyai CLI（飞猪官方工具）"""
import json
import shutil
import subprocess
import time
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
    return CITY_CODE_TO_NAME.get(code.upper(), code)


class FliggyCrawler:
    name = "fliggy"

    def __init__(self):
        # None=未尝试；"limited"=额度或限流；"transient"=临时失败/无数据
        self.last_error_kind = None
        # True=接口正常响应（status=0，即使无航班也算链路通）；False=链路报错；None=未调用
        self.last_ok = None

    def search(self, dep_code: str, arr_code: str, dep_date: str,
               ret_date: str | None = None) -> list[dict]:
        self.last_error_kind = None
        self.last_ok = False
        if not shutil.which("flyai"):
            print("[fliggy] flyai CLI not installed")
            self.last_error_kind = "transient"
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

        # 网络抖动（超时/非0退出码/解析异常）重试一次；
        # 业务确定性失败（如“搜索机票低价行业失败”=该日低价聚合无覆盖）不重复消耗额度，直接返回。
        data = None
        for attempt in range(2):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if proc.returncode != 0:
                    print(f"[fliggy] flyai exit {proc.returncode}: {proc.stderr[:200]}")
                    self.last_error_kind = self._classify(proc.stderr)
                    self.last_ok = False
                    if attempt == 0:
                        time.sleep(2); continue
                    return []
                data = json.loads(proc.stdout)
                if data.get("status") == 0:
                    self.last_ok = True
                    break
                msg = data.get("message", "")
                print(f"[fliggy] {dep_date} business fail: {msg}")
                self.last_error_kind = self._classify(msg)
                self.last_ok = False
                return []
            except subprocess.TimeoutExpired:
                print("[fliggy] flyai timeout")
                self.last_error_kind = "transient"
                self.last_ok = False
                if attempt == 0:
                    time.sleep(2); continue
                return []
            except Exception as e:
                print(f"[fliggy] error: {e}")
                self.last_error_kind = "transient"
                self.last_ok = False
                if attempt == 0:
                    time.sleep(2); continue
                return []

        if not data or data.get("status") != 0:
            self.last_ok = False
            return []

    @staticmethod
    def _classify(text: str) -> str:
        """区分额度/限流（limited）与临时失败（transient）。"""
        if not text:
            return "transient"
        limited_keys = ("429", "Trial limit", "额度", "次数已", "次数用完", "限流", "超出限额", "已用完", "quota", "rate limit")
        if any(k.lower() in text.lower() for k in limited_keys):
            return "limited"
        return "transient"

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
        cheapest = flights[0]["price"] if flights else "N/A"
        print(f"[fliggy] got {len(flights)} flights, cheapest: ¥{cheapest}")
        return flights
