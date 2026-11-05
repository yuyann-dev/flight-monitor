"""航司分类与行李额度规则（全系统唯一权威来源）。

分类依据：各航司公开的国内运输总条件与经济舱行李规则。

两层匹配，避免名称包含关系误判：
1) 简称用“精确相等”（如 “南航” 只匹配名称本身，不误伤“湖南航空”里的连续字）；
2) 全称用“子串包含”（如 “西部航空” 命中 “新海航|西部航空” 这种拼接名）。
类别顺序从严到宽：廉航 → 差异化 → 全服务，最后 unknown 兜底且保留（不误杀）。
"""


# ── 廉航：经济舱基本无免费托运行李，需单独购买 ──
ULTRA_LOW_COST_ALIAS = {"春秋", "九元"}
ULTRA_LOW_COST_NAMES = ["春秋航空", "九元航空"]

# ── 差异化航司：全价经济舱通常有20kg，折扣/特价经济舱常无免费托运 ──
# 仅收录高置信名单；拿不准的航司留 unknown（保留并显示“以购买页为准”），避免误杀
DIFFERENTIATED_ALIAS = {
    "西部", "祥鹏", "首都", "天津", "乌鲁木齐", "华夏", "多彩贵州",
    "中联航", "联航", "桂林", "江西", "长安", "大新华", "福州", "北部湾",
}
DIFFERENTIATED_NAMES = [
    "西部航空", "祥鹏航空", "首都航空", "天津航空",
    "乌鲁木齐航空", "华夏航空", "多彩贵州航空", "中国联航", "中联航",
    "桂林航空", "江西航空", "长安航空", "大新华航空",
    "福州航空", "北部湾航空",
]

# ── 全服务航司：经济舱标准票价含20kg免费托运 ──
# 简称（精确匹配）覆盖上游可能只返回简称的情况，如“西藏航”“成都航”
FULL_SERVICE_ALIAS = {
    "国航", "川航", "南航", "深航", "东航", "厦航", "上航",
    "吉祥", "长龙", "成都航", "河北航", "西藏航", "昆明航",
    "大连航", "重庆航", "青岛航", "山东航",
}
FULL_SERVICE_NAMES = [
    "中国国航", "中国东方航空", "东方航空",
    "中国南方航空", "南方航空", "海南航空",
    "四川航空", "深圳航空", "厦门航空",
    "山东航空", "上海航空", "青岛航空", "吉祥航空",
    "成都航空", "长龙航空", "河北航空", "西藏航空",
    "昆明航空", "大连航空", "重庆航空",
]

# (类别, 简称集合, 全称列表)，顺序即从严到宽
_CATEGORY_RULES = [
    ("ultra_low_cost", ULTRA_LOW_COST_ALIAS, ULTRA_LOW_COST_NAMES),
    ("differentiated", DIFFERENTIATED_ALIAS, DIFFERENTIATED_NAMES),
    ("full_service", FULL_SERVICE_ALIAS, FULL_SERVICE_NAMES),
]


def classify(airline_name: str) -> str:
    """返回航司类别：ultra_low_cost / differentiated / full_service / unknown。

    先精确匹配简称，再用全称子串包含；类别顺序从严到宽。
    """
    if not airline_name or not airline_name.strip():
        return "unknown"
    name = airline_name.strip()
    for category, aliases, names in _CATEGORY_RULES:
        if name in aliases or any(alias in name for alias in names):
            return category
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

    勾选“需要免费托运”时使用：只剔除明确的廉航和差异化航司；
    full_service 保留，unknown（含空值、证据不足的航司）一律保留，
    绝不因为“识别不出”就默认没有行李额度而误过滤。
    """
    return classify(airline_name) in ("full_service", "unknown")
