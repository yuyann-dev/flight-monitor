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
from datetime import datetime, timedelta
from app.models import PriceSample, Monitor
from app.config import settings


# ── 费用 ──
def compute_fees(distance_km: int | None = None) -> dict:
    """计算机建燃油附加费。

    没有可靠航线距离时按长途（800km 以上）保守计算，
    避免低估用户实际支付的总价。
    """
    fuel = (
        settings.fuel_surcharge_long
        if (distance_km or 0) > settings.short_haul_threshold_km
        else settings.fuel_surcharge_short
    )
    return {
        "airport_fee": settings.airport_construction_fee,
        "fuel_fee": fuel,
        "total_fee": settings.airport_construction_fee + fuel,
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
        return 85.0, "临期航班，价格通常只会上涨，越早买越划算"
    if days_left <= 7:
        return 75.0, "临近出发，价格已进入上升通道，建议尽快确定"
    if days_left <= 14:
        return 70.0, "现在通常是国内航线票价较低的阶段，适合出手"
    if days_left <= 30:
        return 40.0, "距离出发还有一段时间，价格后续可能还有下降空间"
    return 30.0, "距离出发较远，通常票价还会继续调整"


def _evaluate(monitor: Monitor, samples: list[PriceSample]) -> dict:
    """核心评估：综合时间阶段、百分位、趋势、心理价位。

    generate_advice 与 should_notify 共用，保证前后端判断一致。
    """
    now = datetime.utcnow()
    history_prices = [s.best_price for s in samples if s.best_price]
    current_price = history_prices[-1] if history_prices else None

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
            reasons.append("当前价格处于你监控周期内的较低水平")
            buy_score += 80 * 0.25
        elif percentile >= 75:
            reasons.append("当前价格处于你监控周期内的较高水平")
            buy_score += 20 * 0.25
        else:
            reasons.append("当前价格处于你监控周期内的中等水平")
            buy_score += 50 * 0.25

    # 3. 趋势预测
    forecast = ewma_forecast(history_prices[-14:]) if len(history_prices) >= 5 else None
    if forecast is not None:
        if current_price < forecast * 0.97:
            reasons.append("当前价格低于近期预测均价，短期内性价比较高")
            buy_score += 70 * 0.15
        elif current_price > forecast * 1.03:
            reasons.append("当前价格高于近期预测均价，后续可能有回调")
            buy_score += 30 * 0.15
        else:
            buy_score += 50 * 0.15

    # 4. 心理价位
    hit_target = bool(monitor.max_price and current_price <= monitor.max_price)
    if hit_target:
        reasons.append(
            f"当前价格¥{current_price}，已达到你设定的心理价位¥{monitor.max_price}"
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

    fees = compute_fees()
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
    """是否发邮件提醒：核心评估 + 冷却期规则。"""
    result = _evaluate(monitor, samples)
    now = datetime.utcnow()

    # 只有倾向“买”的建议才考虑提醒
    is_buy_advice = result["advice"] in ("建议考虑购票", "建议尽快购票")
    score = result["buy_score"] if is_buy_advice else 0.0
    reasons = list(result["reasons"])

    # 冷却期：同航线一段时间内不重复打扰，除非再降一定比例
    if monitor.last_notified_at:
        hours_since = (now - monitor.last_notified_at).total_seconds() / 3600
        if hours_since < settings.notify_cooldown_hours:
            if (
                monitor.last_notified_price
                and current_price
                <= monitor.last_notified_price
                * (1 - settings.notify_price_drop_pct / 100)
            ):
                drop_pct = round(
                    (1 - current_price / monitor.last_notified_price) * 100, 1
                )
                reasons.append(f"比上次提醒又降了{drop_pct}%")
            else:
                score = 0.0  # 冷却期内不重复提醒

    # 价格不应明显高于历史最低（防止高位误报）
    if monitor.best_price_ever and current_price > monitor.best_price_ever * 1.1:
        score = 0.0

    should_alert = score >= 60.0

    fees = compute_fees()
    analysis = {
        "best_price": current_price,
        "total_price": result["total_price"],
        "airport_fee": fees["airport_fee"],
        "fuel_fee": fees["fuel_fee"],
        "percentile": result["percentile"],
        "forecast_price": result["forecast_price"],
        "score": score,
        "reason": "；".join(reasons[:4]),
        "days_left": result["days_left"],
    }
    return should_alert, analysis["reason"], analysis
