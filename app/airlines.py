"""航司分类与行李额度规则（全系统唯一权威来源）。

分类依据：各航司公开的国内运输总条件与经济舱行李规则。
匹配顺序从严到宽：廉航 → 差异化航司 → 全服务航司，避免名称包含关系误判。
"""

# 廉航：经济舱基本无免费托运行李，需单独购买
ULTRA_LOW_COST = ["春秋航空", "九元航空", "春秋", "九元"]

# 差异化航司：全价经济舱通常有20kg，折扣/特价经济舱常无免费托运
DIFFERENTIATED = [
    "西部航空", "祥鹏航空", "首都航空", "天津航空",
    "乌鲁木齐航空", "华夏航空", "多彩贵州航空", "中国联航", "中联航",
    "桂林航空", "江西航空", "长安航空", "大新华航空",
]

# 全服务航司：经济舱标准票价含20kg免费托运
FULL_SERVICE = [
    "中国国航", "国航", "中国东方航空", "东方航空", "东航",
    "中国南方航空", "南方航空", "南航", "海南航空", "海航",
    "四川航空", "川航", "深圳航空", "深航", "厦门航空", "厦航",
    "山东航空", "山东航", "上海航空", "上航", "青岛航空", "青岛航",
    "吉祥航空", "吉祥", "成都航空", "长龙航空", "长龙",
]


def classify(airline_name: str) -> str:
    """返回航司类别：ultra_low_cost / differentiated / full_service / unknown。

    匹配顺序：廉航 → 差异化 → 全服务，从严到宽。
    """
    if not airline_name:
        return "unknown"
    name = airline_name.strip()

    for kw in ULTRA_LOW_COST:
        if kw in name:
            return "ultra_low_cost"
    for kw in DIFFERENTIATED:
        if kw in name:
            return "differentiated"
    for kw in FULL_SERVICE:
        if kw in name:
            return "full_service"
    return "unknown"


def baggage_text(airline_name: str, cabin_class: str = "economy") -> str:
    """生成给用户看的行李说明（大白话，保守、不瞎编）。"""
    category = classify(airline_name)
    is_economy = cabin_class in ("economy", "", None)

    if category == "ultra_low_cost":
        return "廉航：经济舱通常无免费托运行李，需付费购买（具体以购买页为准）"
    if category == "differentiated":
        if is_economy:
            return "差异化航司：折扣经济舱通常无免费托运，全价舱一般含20kg（具体以购买页为准）"
        return "差异化航司：行李额度请以购买页为准"
    if category == "full_service":
        return "全服务航司：经济舱免费托运20kg（具体以购买页为准）"
    return "行李额度请以购买页为准"


def has_free_baggage(airline_name: str) -> bool:
    """判断该航司是否可视为有免费托运。

    勾选“需要免费托运”时使用：保守处理，剔除廉航和差异化航司；
    无法确认的航司保留（避免误杀）。
    """
    category = classify(airline_name)
    # 只有明确是全服务航司才保留；unknown 也保留（不确定不误杀）
    return category in ("full_service", "unknown")
