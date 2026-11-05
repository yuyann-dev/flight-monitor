"""Price analysis & prediction engine.

判断依据（按可信度分层）：
1. 行业先验：距出发天数对应的价格阶段（U 型规律）
2. 自有数据：监控历史价格的百分位
3. 趋势外推：EWMA 指数平滑预测
4. 用户设置：心理价位

说明：周几出发、日内时段、航司会员日等说法在大样本研究中信号很弱
（Google/Hopper 等统计差异仅约 2% 且不具统计显著性），或规则会随时间变化，
为保证客观性与可解释性，本引擎不采用这些规则。
"""
import math
from datetime import datetime, timedelta
from app.models import PriceSample, Monitor
from app.config import settings


# ── 费用（成人国内航线：民航发展基金 + 燃油附加费参考值）──
# 常用城市经纬度（近似值，仅用于 800km 里程二分，不用于票价预测）。
# 官方燃油标准按航段里程：>800km 为长途档，<=800km 为短途档；标准由航司按月公布，
# 常量值需随官方调价更新（见 app/config.py 的 fuel_surcharge_short/long）。
CITY_COORDS: dict[str, tuple[float, float]] = {
    "合肥": (31.784, 117.302), "成都": (30.573, 104.066), "北京": (39.904, 116.407),
    "上海": (31.230, 121.473), "广州": (23.129, 113.264), "深圳": (22.543, 114.058),
    "重庆": (29.563, 106.551), "西安": (34.341, 108.939), "杭州": (30.274, 120.155),
    "昆明": (25.039, 102.718), "三亚": (18.252, 109.512), "海口": (20.044, 110.199),
    "乌鲁木齐": (43.825, 87.617), "拉萨": (29.652, 91.172), "南京": (32.060, 118.797),
    "武汉": (30.593, 114.305), "长沙": (28.228, 112.939), "青岛": (36.067, 120.383),
    "大连": (38.914, 121.614), "厦门": (24.479, 118.089), "福州": (26.075, 119.296),
    "天津": (39.343, 117.362), "郑州": (34.747, 113.625), "贵阳": (26.647, 106.630),
    "兰州": (36.061, 103.834), "西宁": (36.617, 101.778), "银川": (38.487, 106.230),
    "呼和浩特": (40.842, 111.749), "沈阳": (41.805, 123.431), "长春": (43.817, 125.323),
    "哈尔滨": (45.803, 126.534), "南昌": (28.682, 115.858), "南宁": (22.817, 108.366),
    "珠海": (22.271, 113.576), "宁波": (29.868, 121.544), "温州": (27.994, 120.699),
    "桂林": (25.274, 110.290), "丽江": (26.872, 100.229), "太原": (37.871, 112.549),
    "石家庄": (38.042, 114.514), "济南": (36.651, 117.120), "泸州": (28.870, 105.443),
    "南充": (30.837, 106.082), "西昌": (27.892, 102.265), "景洪": (22.009, 100.797),
}


def _norm_city(name: str | None) -> str:
    if not name:
        return ""
    return str(name).strip().replace("市", "").replace(" ", "")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lam / 2) ** 2
    )
    return 2 * earth_r * math.asin(math.sqrt(a))


def _route_distance_km(departure: str | None, arrival: str | None) -> float | None:
    """用城市坐标估算大圆里程；任一城市未收录则返回 None，交由调用方保守处理。"""
    dep, arr = _norm_city(departure), _norm_city(arrival)
    if dep not in CITY_COORDS or arr not in CITY_COORDS:
        return None
    lat1, lon1 = CITY_COORDS[dep]
    lat2, lon2 = CITY_COORDS[arr]
    return _haversine_km(lat1, lon1, lat2, lon2)


def compute_fees(
    distance_km: float | None = None,
    departure: str | None = None,
    arrival: str | None = None,
) -> dict:
    """计算机建（民航发展基金）与燃油附加费参考值。

    燃油按航段里程二分：>800km 用长途标准，否则用短途标准。
    距离来源优先级：显式传入 distance_km > 出发/到达城市坐标估算；
    都无法确定时按长途保守取值，避免低估用户实际支付的含税总价。
    机建费成人国内干线统一按配置值（默认 50 元），支线减免不在本模型内细分。
    """
    dist = distance_km
    if dist is None and departure and arrival:
        dist = _route_distance_km(departure, arrival)

    long_haul = True if dist is None else dist > settings.short_haul_threshold_km
    fuel = settings.fuel_surcharge_long if long_haul else settings.fuel_surcharge_short
    airport = settings.airport_construction_fee
    return {
        "airport_fee": airport,
        "fuel_fee": fuel,
        "total_fee": airport + fuel,
        "long_haul": long_haul,
        "distance_km": round(dist) if dist is not None else None,
    }


# ── 统计工具 ──
def percentile_rank(value: float, history: list[float]) -> float:
    """value 在历史中的百分位（0-100，越低越便宜）。"""
    if not history:
        return 50.0
    below = sum(1 for p in history if p <= value)
    return round(below / len(history) * 100, 1)


def ewma_forecast(history: list[float], days_ahead: int = 7, alpha: float = 0.35):
    """指数平滑 + 近期斜率，预测未来价格。数据不足返回 None。"""
    if len(history) < 3:
        return None
    smoothed = history[0]
    for p in history[1:]:
        smoothed = alpha * p + (1 - alpha) * smoothed
    if len(history) >= 5:
        recent_slope = (history[-1] - history[-3]) / 3
        return smoothed + recent_slope * days_ahead
    return smoothed


# ── 时间阶段（行业先验）──
def _time_stage(days_left: int) -> tuple[float, str]:
    """根据距出发天数返回 (买入倾向分 0-100, 大白话说明)。

    黄金窗口取 7-14 天（结合用户实际经验与多份购票攻略）。
    """
    if days_left < 0:
        return 0.0, "航班日期已过"
    if days_left <= 3:
        return 85.0, "临期航班票价通常趋紧，建议尽早对比安排"
    if days_left <= 7:
        return 75.0, "临近出行，票价存在上行可能，可尽早安排"
    if days_left <= 14:
        return 70.0, "该阶段国内航线票价相对友好，可多加关注"
    if days_left <= 30:
        return 40.0, "距离出发还有一段时间，价格后续可能还有下降空间"
    return 30.0, "距离出发较远，通常票价还会继续调整"


def _evaluate(monitor: Monitor, samples: list[PriceSample],
              current_price_override: float | None = None) -> dict:
    """核心评估：综合时间阶段、百分位、趋势、心理价位。

    generate_advice 与 should_notify 共用，保证前后端判断一致。
    current_price_override 用于首次爬取：新样本尚未提交、历史样本为空时，
    由调用方传入当前真实最低价，避免误判为“无数据”。
    """
    now = datetime.utcnow()
    history_prices = [s.best_price for s in samples if s.best_price]
    current_price = current_price_override or (
        history_prices[-1] if history_prices else None
    )

    # 距出发天数
    try:
        dep_dt = datetime.strptime(monitor.departure_date_start, "%Y-%m-%d")
        days_left = (dep_dt - now).days
    except Exception:
        days_left = 30

    # 无数据场景
    if current_price is None:
        return {
            "advice": "系统监测中",
            "reasons": [
                "太远的日期暂时还搜不到航班数据",
                "系统会持续监控，直到能获取到航班信息",
            ],
            "days_left": days_left,
            "current_price": None,
            "percentile": None,
            "forecast_price": None,
            "buy_score": 0.0,
        }

    reasons = []
    buy_score = 0.0

    # 1. 时间阶段
    stage_score, stage_text = _time_stage(days_left)
    buy_score += stage_score * 0.5
    reasons.append(stage_text)

    # 2. 百分位（有足够数据才用）
    percentile = None
    if len(history_prices) >= 3:
        percentile = percentile_rank(current_price, history_prices[-30:])
        if percentile <= 25:
            reasons.append("当前裸票价处于你监控周期内的较低水平")
            buy_score += 80 * 0.25
        elif percentile >= 75:
            reasons.append("当前裸票价处于你监控周期内的较高水平")
            buy_score += 20 * 0.25
        else:
            reasons.append("当前裸票价处于你监控周期内的中等水平")
            buy_score += 50 * 0.25

    # 3. 趋势预测
    forecast = ewma_forecast(history_prices[-14:]) if len(history_prices) >= 5 else None
    if forecast is not None:
        if current_price < forecast * 0.97:
            reasons.append("当前裸票价低于近期预测均价，短期走势相对偏好")
            buy_score += 70 * 0.15
        elif current_price > forecast * 1.03:
            reasons.append("当前裸票价高于近期预测均价，后续可能有回调")
            buy_score += 30 * 0.15
        else:
            buy_score += 50 * 0.15

    # 4. 心理价位
    hit_target = bool(monitor.max_price and current_price <= monitor.max_price)
    if hit_target:
        reasons.append(
            f"当前裸票价¥{current_price}，已达到你设定的心理价位¥{monitor.max_price}"
        )
        buy_score += 85 * 0.10

    buy_score = max(0.0, min(100.0, round(buy_score, 1)))

    # 最终建议（明确的分场景规则，避免只靠一个分数）
    if days_left <= 7:
        advice = "建议尽快购票"
    elif days_left <= 14 and (hit_target or (percentile is not None and percentile <= 30)):
        advice = "建议考虑购票"
    elif days_left > 30:
        advice = "建议继续观察"
    elif percentile is not None and percentile <= 25:
        advice = "建议考虑购票"
    else:
        advice = "建议继续观察"

    fees = compute_fees(departure=monitor.departure_city, arrival=monitor.arrival_city)
    return {
        "advice": advice,
        "reasons": reasons,
        "days_left": days_left,
        "current_price": current_price,
        "percentile": percentile,
        "forecast_price": int(forecast) if forecast else None,
        "buy_score": buy_score,
        "total_price": current_price + fees["total_fee"],
        "airport_fee": fees["airport_fee"],
        "fuel_fee": fees["fuel_fee"],
        "hit_target": hit_target,
    }


def generate_advice(monitor: Monitor, samples: list[PriceSample]) -> dict:
    """前端展示用的完整购买建议。"""
    result = _evaluate(monitor, samples)
    # 前端不需要内部字段，保留有用的部分
    return {
        "advice": result["advice"],
        "reasons": result["reasons"],
        "days_left": result["days_left"],
        "current_price": result["current_price"],
        "percentile": result["percentile"],
        "forecast_price": result["forecast_price"],
    }


def should_notify(
    monitor: Monitor, samples: list[PriceSample], current_price: float
) -> tuple[bool, str, dict]:
    """是否发邮件提醒，三支 OR 触发、命中优先、无冷却：

    ① 裸票价命中心理价；② 常规买入倾向综合分 >=60；
    ③ 未命中且常规分不足，但临近出行并达到“越临近门槛越低”的梯度（中档阈值）。
    命中/常规达标时不进入临期档；双不满足且不临期则不发。
    """
    result = _evaluate(monitor, samples, current_price_override=current_price)
    if current_price is None:
        return False, "", {}

    hit_target = bool(result.get("hit_target"))
    is_buy_advice = result["advice"] in ("建议考虑购票", "建议尽快购票")
    score = result["buy_score"] if is_buy_advice else 0.0
    raw_score = result["buy_score"]
    days_left = int(result.get("days_left") if result.get("days_left") is not None else 30)
    reasons = list(result["reasons"])

    # 临期低门槛档：仅在“未命中且常规分不足”时启用；命中走①②，不进临期；
    # 当天出发因时分取整可能为 -1，纳入兜底；明显已过期（<=-2）不发
    near_deadline = False
    if not hit_target and score < 60.0 and days_left >= -1:
        if days_left <= 1:
            near_deadline = True  # 当天兜底：本轮拿到真实最低价即提醒
        elif days_left <= 2 and raw_score >= 20:
            near_deadline = True
        elif days_left <= 3 and raw_score >= 40:
            near_deadline = True
        elif days_left <= 5 and raw_score >= 55:
            near_deadline = True

    # 三支 OR；不做冷却/高位硬拦截，避免错过提醒
    should_alert = hit_target or score >= 60.0 or near_deadline

    fees = compute_fees(departure=monitor.departure_city, arrival=monitor.arrival_city)
    analysis = {
        "best_price": current_price,
        "total_price": result["total_price"],
        "airport_fee": fees["airport_fee"],
        "fuel_fee": fees["fuel_fee"],
        "percentile": result["percentile"],
        "forecast_price": result["forecast_price"],
        "score": score,
        "hit_target": hit_target,
        "near_deadline": near_deadline,
        "days_left": days_left,
        "target_price": monitor.max_price,
        "reason": "；".join(reasons[:4]),
        "advice": result["advice"],
    }
    return should_alert, analysis["reason"], analysis
