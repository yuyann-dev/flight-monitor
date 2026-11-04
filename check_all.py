import sqlite3, json

print("=" * 50)
print("1. 数据库表结构检查")
print("=" * 50)
conn = sqlite3.connect("/opt/flight-monitor/data/flight_monitor.db")
c = conn.cursor()
c.execute("PRAGMA table_info(monitors)")
columns = [row[1] for row in c.fetchall()]
print("monitors表字段:", columns)

print("\n" + "=" * 50)
print("2. 监控任务检查")
print("=" * 50)
c.execute("SELECT id, departure_city, arrival_city, departure_date_start, departure_date_end, direct_only, baggage_required, is_active FROM monitors")
for row in c.fetchall():
    print(f"  任务{row[0]}: {row[1]}->{row[2]} | {row[3]}~{row[4]} | 直飞:{row[5]} | 行李:{row[6]} | 活跃:{row[7]}")

print("\n" + "=" * 50)
print("3. 最新样本检查")
print("=" * 50)
c.execute("SELECT checked_at, best_price, departure_date, flight_detail FROM price_samples ORDER BY checked_at DESC LIMIT 1")
row = c.fetchone()
print(f"时间: {row[0]}")
print(f"最低价: ¥{row[1]}")
print(f"最便宜日期: {row[2]}")
d = json.loads(row[3])
print(f"航班总数: {len(d)}")

from collections import defaultdict
by_date = defaultdict(list)
for f in d:
    by_date[f.get("date", "无")].append(f)
for date, flights in by_date.items():
    prices = sorted([f["price"] for f in flights])
    direct_count = sum(1 for f in flights if f.get("is_direct"))
    print(f"  {date}: {len(flights)}个航班, 直飞{direct_count}个, 价格{prices[:5]}...")

print("\n" + "=" * 50)
print("4. 按价格排序前5个航班")
print("=" * 50)
sorted_flights = sorted(d, key=lambda x: x["price"])
for f in sorted_flights[:5]:
    print(f"  ¥{f['price']} | {f['date']} | {f['airline']} {f['flight_no']} | {'直飞' if f.get('is_direct') else '中转'} | 行李:{f.get('baggage', '未知')}")

print("\n" + "=" * 50)
print("5. 被剔除航班检查")
print("=" * 50)
c.execute("SELECT excluded_flights FROM monitors WHERE id=1")
row = c.fetchone()
print(f"任务1被剔除航班: {row[0]}")
