"""测试价格预测算法的准确性"""
import sys
sys.path.insert(0, "/opt/flight-monitor")
from app.analyzer import should_notify, industry_rule_score, ewma_forecast, percentile_rank
from datetime import datetime, timedelta

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
rule2 = industry_rule_score("2026-11-08")  # 还有4天出发
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
rule3 = industry_rule_score("2027-02-02")  # 还有90天
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
rule4 = industry_rule_score("2026-11-29")  # 还有25天
print(f"  历史数据: {history4}")
print(f"  当前价格: 380 (历史最低)")
print(f"  百分位: {pct4}%")
print(f"  EWMA预测7天后: ¥{int(forecast4) if forecast4 else 'N/A'}")
print(f"  行业规则评分: {rule4['score']}")
print(f"  理由: {rule4['reasons']}")

# 测试5：周二出发 + 黄金窗口
print("\n【测试5】周二出发 + 黄金窗口（叠加效应）")
rule5 = industry_rule_score("2026-11-25")  # 周二，还有21天
print(f"  行业规则评分: {rule5['score']}")
print(f"  理由: {rule5['reasons']}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
