"""本地测试价格预测算法"""
from datetime import datetime, timedelta

def percentile_rank(value: float, history: list[float]) -> float:
    if not history:
        return 50.0
    below = sum(1 for p in history if p <= value)
    return round(below / len(history) * 100, 1)

def ewma_forecast(history: list[float], days_ahead: int = 7, alpha: float = 0.3) -> float | None:
    if len(history) < 3:
        return None
    smoothed = history[0]
    for p in history[1:]:
        smoothed = alpha * p + (1 - alpha) * smoothed
    if len(history) >= 5:
        recent_slope = (history[-1] - history[-3]) / 3
        return smoothed + recent_slope * days_ahead
    return smoothed

def industry_rule_score(dep_date: str, checked_at: datetime | None = None) -> dict:
    if checked_at is None:
        checked_at = datetime.utcnow()

    score = 50.0
    reasons = []

    try:
        dep_dt = datetime.strptime(dep_date, "%Y-%m-%d")
        days_left = (dep_dt - checked_at).days
    except Exception:
        days_left = 30

    if days_left < 7:
        score += 25
        reasons.append(f"距出发仅{days_left}天，临期价格通常只会上涨，建议尽快入手")
    elif days_left < 14:
        score += 15
        reasons.append(f"距出发{days_left}天，低价窗口正在关闭，价格将逐步上涨")
    elif 20 <= days_left <= 30:
        score += 20
        reasons.append(f"距出发{days_left}天，正处于国内机票黄金购票窗口")
    elif 14 <= days_left < 20:
        score += 10
        reasons.append(f"距出发{days_left}天，接近黄金购票窗口")
    elif 30 < days_left <= 45:
        score += 5
        reasons.append(f"距出发{days_left}天，旺季可考虑锁票")
    elif days_left > 90:
        score -= 15
        reasons.append(f"距出发{days_left}天过早，价格尚未稳定，可再观望")
    elif 45 < days_left <= 90:
        score -= 5
        reasons.append(f"距出发{days_left}天偏早，价格可能还有下降空间")

    try:
        weekday = dep_dt.weekday()
        if weekday in [1, 2]:
            score += 10
            reasons.append("周二/周三出发为周中价格洼地，比周末便宜15-30%")
        elif weekday == 0:
            score -= 5
            reasons.append("周一出发商务客流多，价格偏高")
        elif weekday in [4, 5]:
            score -= 10
            reasons.append("周五/周六出发为商旅+旅游高峰，溢价较高")
        elif weekday == 6:
            score -= 8
            reasons.append("周日返程高峰，价格偏高")
    except Exception:
        pass

    hour = checked_at.hour
    if 13 <= hour < 15:
        score += 5
        reasons.append("当前是下午退票回流高峰，可能捡漏低价")
    elif hour >= 22 or hour < 3:
        score += 5
        reasons.append("当前是深夜系统清仓时段，可能有尾单低价")

    score = max(0, min(100, score))
    return {"score": score, "reasons": reasons, "days_left": days_left}


print("=" * 60)
print("价格预测算法测试")
print("=" * 60)

# 测试1：正常价格走势（先高后低再涨）
print("\n【测试1】价格走势：第1天500 → 第7天400 → 第14天450")
history1 = [500, 490, 480, 460, 440, 420, 400, 410, 420, 430, 440, 445, 450, 450]
forecast1 = ewma_forecast(history1)
pct1 = percentile_rank(400, history1)
rule1 = industry_rule_score("2026-11-18")
print(f"  历史数据: {history1}")
print(f"  当前价格: 400 (最低点)")
print(f"  百分位: {pct1}%")
print(f"  EWMA预测7天后: ¥{int(forecast1) if forecast1 else 'N/A'}")
print(f"  行业规则评分: {rule1['score']}")
print(f"  理由: {rule1['reasons']}")

# 测试2：价格持续上涨（临期）
print("\n【测试2】临期价格持续上涨：第1天300 → 第7天500")
history2 = [300, 320, 350, 380, 420, 460, 500]
forecast2 = ewma_forecast(history2)
pct2 = percentile_rank(500, history2)
rule2 = industry_rule_score("2026-11-08")
print(f"  历史数据: {history2}")
print(f"  当前价格: 500")
print(f"  百分位: {pct2}%")
print(f"  EWMA预测: ¥{int(forecast2) if forecast2 else 'N/A'}")
print(f"  行业规则评分: {rule2['score']}")
print(f"  理由: {rule2['reasons']}")

# 测试3：太早出发，价格还没降
print("\n【测试3】太早出发（还有90天），价格偏高")
history3 = [600, 590, 580, 570, 560]
forecast3 = ewma_forecast(history3)
pct3 = percentile_rank(560, history3)
rule3 = industry_rule_score("2027-02-02")
print(f"  历史数据: {history3}")
print(f"  当前价格: 560")
print(f"  百分位: {pct3}%")
print(f"  EWMA预测: ¥{int(forecast3) if forecast3 else 'N/A'}")
print(f"  行业规则评分: {rule3['score']}")
print(f"  理由: {rule3['reasons']}")

# 测试4：黄金窗口（提前25天）+ 历史低位
print("\n【测试4】黄金窗口 + 历史低位（最佳买入时机）")
history4 = [500, 480, 460, 440, 420, 400, 380]
forecast4 = ewma_forecast(history4)
pct4 = percentile_rank(380, history4)
rule4 = industry_rule_score("2026-11-29")
print(f"  历史数据: {history4}")
print(f"  当前价格: 380 (历史最低)")
print(f"  百分位: {pct4}%")
print(f"  EWMA预测7天后: ¥{int(forecast4) if forecast4 else 'N/A'}")
print(f"  行业规则评分: {rule4['score']}")
print(f"  理由: {rule4['reasons']}")

# 测试5：周二出发 + 黄金窗口
print("\n【测试5】周二出发 + 黄金窗口（叠加效应）")
rule5 = industry_rule_score("2026-11-25")
print(f"  行业规则评分: {rule5['score']}")
print(f"  理由: {rule5['reasons']}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
