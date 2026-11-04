"""Price analysis & prediction engine.

冷启动阶段：基于行业先验规则 + 实时百分位
积累数据后：EWMA 指数平滑趋势外推
"""
import math
from datetime import datetime, timedelta
from statistics import mean, median, quantiles
from app.models import PriceSample, Monitor
from app.config import settings


def compute_fees(distance_km: int | None = None) -> dict:
    """计算机建燃油附加费。"""
    fuel = settings.fuel_surcharge_long if (distance_km or 0) > settings.short_haul_threshold_km else settings.fuel_surcharge_short
    return {"airport_fee": settings.airport_construction_fee, "fuel_fee": fuel, "total_fee": settings.airport_construction_fee + fuel}


def percentile_rank(value: float, history: list[float]) -> float:
    """返回 value 在历史中的百分位（0-100，越低越便宜）。"""
    if not history:
        return 50.0
    below = sum(1 for p in history if p <= value)
    return round(below / len(history) * 100, 1)


def ewma_forecast(history: list[float], days_ahead: int = 7, alpha: float = 0.3) -> float | None:
    """指数平滑预测未来 days_ahead 天的平均价格。"""
    if len(history) < 3:
        return None
    # 逆向时间：history[-1] 是最新
    smoothed = history[0]
    for p in history[1:]:
        smoothed = alpha * p + (1 - alpha) * smoothed
    # 简单趋势外推：最近斜率
    if len(history) >= 5:
        recent_slope = (history[-1] - history[-3]) / 3
        return smoothed + recent_slope * days_ahead
    return smoothed


def industry_rule_score(dep_date: str, checked_at: datetime | None = None) -> dict:
    """基于行业先验规则的评分。返回 (推荐买入度 0-100, 理由列表)。

    参考来源：Travel Arbitrage 2026年1.2亿条国内订单数据、飞猪大数据、航空旅游网报告
    核心结论：价格呈U型曲线，31-45天最便宜，太早太晚都贵
    """
    if checked_at is None:
        checked_at = datetime.utcnow()

    score = 50.0
    reasons = []

    # 1. 距出发天数（基于1.2亿条真实订单数据的U型曲线）
    try:
        dep_dt = datetime.strptime(dep_date, "%Y-%m-%d")
        days_left = (dep_dt - checked_at).days
    except Exception:
        days_left = 30

    if days_left < 0:
        score -= 30
        reasons.append("航班已过日期")
    elif days_left < 3:
        score += 35
        reasons.append(f"离出发只剩{days_left}天，价格只会涨不会降，赶紧买")
    elif 3 <= days_left < 7:
        score += 25
        reasons.append(f"离出发{days_left}天，临期价格持续上涨，建议尽快入手")
    elif 7 <= days_left < 14:
        score += 10
        reasons.append(f"离出发{days_left}天，低价舱快卖完了，价格逐步上涨")
    elif 14 <= days_left < 22:
        score += 15
        reasons.append(f"离出发{days_left}天，价格处于低位，可以考虑入手")
    elif 22 <= days_left <= 30:
        score += 25
        reasons.append(f"离出发{days_left}天，价格已经很划算了")
    elif 31 <= days_left <= 45:
        score += 30
        reasons.append(f"离出发{days_left}天，正是全年最便宜的黄金窗口，现在买最划算")
    elif 46 <= days_left <= 60:
        score += 5
        reasons.append(f"离出发{days_left}天，价格还偏贵一点，再等等可能更便宜")
    elif days_left > 60:
        score -= 15
        reasons.append(f"离出发{days_left}天太早了，现在买贵20%-30%，再等等")

    # 2. 周几出发（基于真实数据：周二最便宜，周五最贵）
    try:
        weekday = dep_dt.weekday()  # 0=周一, 1=周二, ..., 6=周日
        if weekday == 1:  # 周二
            score += 10
            reasons.append("周二出发，是全周最便宜的日子")
        elif weekday == 2:  # 周三
            score += 5
            reasons.append("周三出发，价格也比较低")
        elif weekday == 0:  # 周一
            score -= 3
            reasons.append("周一出发商务多，价格略高")
        elif weekday in [5, 6]:  # 周六、周日
            score -= 8
            reasons.append("周末出发，旅游+返程高峰，价格偏高")
        elif weekday == 4:  # 周五
            score -= 12
            reasons.append("周五出发，全周最贵，比周二贵20%左右")
    except Exception:
        pass

    # 3. 一天中的捡漏时段
    hour = checked_at.hour
    if 14 <= hour < 16:
        score += 3
        reasons.append("下午2-4点航司放折扣，可能捡到便宜")
    elif hour >= 22 or hour < 2:
        score += 3
        reasons.append("深夜系统清仓，可能有尾单低价")

    score = max(0, min(100, score))
    return {"score": score, "reasons": reasons, "days_left": days_left}


def should_notify(monitor: Monitor, samples: list[PriceSample], current_price: float) -> tuple[bool, str, dict]:
    """判断是否应该发送低价提醒。

    返回 (should_alert, reason, analysis_dict)
    """
    now = datetime.utcnow()
    history_prices = [s.best_price for s in samples if s.best_price]
    fees = compute_fees()
    total_price = current_price + fees["total_fee"]

    # 基础分析
    pct = percentile_rank(current_price, history_prices[-30:]) if len(history_prices) >= 3 else 50.0
    forecast = ewma_forecast(history_prices[-14:]) if len(history_prices) >= 5 else None
    rule = industry_rule_score(monitor.departure_date_start, now)

    # 判断逻辑
    reasons = []
    score = 0

    # 1. 低于历史百分位
    if pct <= 25:
        score += 40
        reasons.append(f"当前价格处于历史{pct}%分位（低于75%的时段）")
    elif pct <= 40:
        score += 20
        reasons.append(f"当前价格处于历史{pct}%分位，偏低")

    # 2. 预测不会更低（结合行业规则修正）
    days_left = rule["days_left"]
    is_low_season_window = 20 <= days_left <= 30  # 黄金窗口
    is_near_departure = days_left < 14  # 临期

    if forecast:
        # 如果在黄金窗口或临期，即使 EWMA 预测还会降，也认为价格到底了
        if is_low_season_window or is_near_departure:
            # 黄金窗口后价格只会涨，不会再降
            score += 25
            reasons.append(f"已到黄金购票窗口（距出发{days_left}天），后续价格大概率上涨")
        elif current_price <= forecast * 0.95:
            score += 25
            reasons.append(f"预测未来7天均价¥{int(forecast)}，当前已低于预测值5%以上")
        # else: forecast > current_price，预测还会降，不加分
    else:
        # 数据不足，用行业规则判断
        if is_low_season_window:
            score += 15
            reasons.append(f"已到黄金购票窗口（距出发{days_left}天），建议关注")

    # 3. 行业规则加分
    score += rule["score"] * 0.3
    reasons.extend(rule["reasons"])

    # 4. 到用户设定的价格上限
    if monitor.max_price and current_price <= monitor.max_price:
        score += 20
        reasons.append(f"已达到你设定的心理价位¥{monitor.max_price}")

    # 5. 冷却期检查
    if monitor.last_notified_at:
        hours_since = (now - monitor.last_notified_at).total_seconds() / 3600
        if hours_since < settings.notify_cooldown_hours:
            # 冷却期内，只有再降5%才提醒
            if monitor.last_notified_price and current_price <= monitor.last_notified_price * (1 - settings.notify_price_drop_pct / 100):
                reasons.append(f"比上次提醒又降了{round((1 - current_price/monitor.last_notified_price)*100,1)}%")
                score += 10
            else:
                score -= 50  # 冷却期内不重复提醒

    should_alert = score >= 55 and current_price <= (monitor.best_price_ever or float('inf')) * 1.1

    analysis = {
        "best_price": current_price,
        "total_price": total_price,
        "airport_fee": fees["airport_fee"],
        "fuel_fee": fees["fuel_fee"],
        "percentile": pct,
        "forecast_price": int(forecast) if forecast else None,
        "score": round(score, 1),
        "reason": "；".join(reasons[:4]),
        "days_left": rule["days_left"],
    }

    return should_alert, analysis["reason"], analysis
