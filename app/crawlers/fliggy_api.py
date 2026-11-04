"""飞猪开放平台 API 客户端 - 官方免费机票搜索接口"""
import time
import hashlib
import httpx
from app.config import settings


FLIGGY_APP_KEY = ""  # 申请后填这里
FLIGGY_APP_SECRET = ""  # 申请后填这里


def _sign(params: dict, secret: str) -> str:
    """淘宝开放平台签名算法"""
    items = sorted(params.items())
    raw = secret + "".join(f"{k}{v}" for k, v in items) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


async def search_fliggy(dep_code: str, arr_code: str, dep_date: str, cabin_class: int = 1) -> list[dict]:
    """
    飞猪国内机票搜索
    返回: [{"flight_no": "MU5102", "price": 12300, "dep_time": "...", ...}] 单位: 分
    """
    if not FLIGGY_APP_KEY or not FLIGGY_APP_SECRET:
        # 未配置时返回 mock 数据，确保流程能跑通
        import random
        base_price = 500 + random.randint(-100, 300)
        print(f"[fliggy] 未配置app_key，返回mock数据: ¥{base_price}")
        return [
            {"flight_no": "CA1234", "price": base_price, "dep_airport": dep_code, "arr_airport": arr_code, "platform": "飞猪(mock)"},
            {"flight_no": "MU5678", "price": base_price + 50, "dep_airport": dep_code, "arr_airport": arr_code, "platform": "飞猪(mock)"},
        ]

    params = {
        "method": "taobao.trip.jipiao.nsearch.ow.search",
        "app_key": FLIGGY_APP_KEY,
        "sign_method": "md5",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "dep_city_code": dep_code,
        "arr_city_code": arr_code,
        "dep_date": dep_date,
        "search_type": "outbound",
        "cabin_class": cabin_class,
    }
    params["sign"] = _sign(params, FLIGGY_APP_SECRET)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://eco.taobao.com/router/rest", params=params)
            data = r.json()
            result = data.get("trip_jipiao_nsearch_ow_search_response", {}).get("result", {})
            items = result.get("items", [])
            
            flights = []
            for item in items[:20]:
                price_fen = item.get("price", {}).get("value", 0)
                price_yuan = price_fen // 100
                segments = item.get("segments", [])
                if segments:
                    seg = segments[0]
                    flights.append({
                        "flight_no": seg.get("flight_no", ""),
                        "price": price_yuan,
                        "dep_airport": seg.get("dep_city", ""),
                        "arr_airport": seg.get("arr_city", ""),
                        "platform": "飞猪",
                    })
            print(f"[fliggy] got {len(flights)} flights")
            return flights
    except Exception as e:
        print(f"[fliggy] error: {e}")
        return []


# 城市三字码映射表（常用国内城市）
CITY_CODES = {
    "北京": "BJS", "上海": "SHA", "广州": "CAN", "深圳": "SZX",
    "成都": "CTU", "重庆": "CKG", "杭州": "HGH", "南京": "NKG",
    "武汉": "WUH", "西安": "XIY", "长沙": "CSX", "厦门": "XMN",
    "昆明": "KMG", "三亚": "SYX", "海口": "HAK", "青岛": "TAO",
    "大连": "DLC", "天津": "TSN", "郑州": "CGO", "济南": "TNA",
    "福州": "FOC", "合肥": "HFE", "南昌": "KHN", "贵阳": "KWE",
    "南宁": "NNG", "兰州": "LHW", "乌鲁木齐": "URC", "哈尔滨": "HRB",
    "沈阳": "SHE", "长春": "CGQ", "呼和浩特": "HET", "石家庄": "SJW",
    "太原": "TYN", "银川": "INC", "西宁": "XNN", "拉萨": "LXA",
    "宁波": "NGB", "温州": "WNZ", "桂林": "KWL", "丽江": "LJG",
    "张家界": "DYG", "九寨沟": "JZH", "稻城": "DCY",
}
