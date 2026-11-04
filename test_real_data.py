"""用数据库里的真实数据测试预测算法"""
import sys
sys.path.insert(0, "/opt/flight-monitor")
import sqlite3

conn = sqlite3.connect("/opt/flight-monitor/data/flight_monitor.db")
c = conn.cursor()

# 取所有历史价格
c.execute("SELECT checked_at, best_price FROM price_samples WHERE monitor_id=1 ORDER BY checked_at")
rows = c.fetchall()
conn.close()

print("=" * 60)
print(f"真实历史价格数据（共{len(rows)}条）")
print("=" * 60)

prices = [r[1] for r in rows]
times = [r[0] for r in rows]

for i, (t, p) in enumerate(zip(times, prices)):
    print(f"  {i+1}. {t} → ¥{p}")

print(f"\n最低: ¥{min(prices)}")
print(f"最高: ¥{max(prices)}")
print(f"当前: ¥{prices[-1]}")

# 测试 EWMA 预测
def ewma_forecast(history, days_ahead=7, alpha=0.3):
    if len(history) < 3:
        return None
    smoothed = history[0]
    for p in history[1:]:
        smoothed = alpha * p + (1 - alpha) * smoothed
    if len(history) >= 5:
        recent_slope = (history[-1] - history[-3]) / 3
        return smoothed + recent_slope * days_ahead
    return smoothed

def percentile_rank(value, history):
    if not history:
        return 50.0
    below = sum(1 for p in history if p <= value)
    return round(below / len(history) * 100, 1)

print("\n" + "=" * 60)
print("算法测试结果")
print("=" * 60)

pct = percentile_rank(prices[-1], prices)
print(f"当前价格百分位: {pct}%")

forecast = ewma_forecast(prices)
print(f"EWMA预测未来7天均价: ¥{int(forecast) if forecast else 'N/A'}")

# 模拟不同时间点的判断
print("\n--- 模拟判断 ---")
print(f"当前价格 ¥{prices[-1]}")
print(f"历史最低 ¥{min(prices)}")
print(f"百分位 {pct}%")

if pct <= 25:
    print("→ 便宜（低于历史75%时段）")
elif pct <= 50:
    print("→ 中等偏低")
else:
    print("→ 偏贵")

print("\n" + "=" * 60)
