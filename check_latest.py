import sqlite3, json

conn = sqlite3.connect("/opt/flight-monitor/data/flight_monitor.db")
c = conn.cursor()
c.execute("SELECT checked_at, departure_date, best_price, flight_detail FROM price_samples ORDER BY checked_at DESC LIMIT 1")
row = c.fetchone()
print(f"时间: {row[0]}")
print(f"最便宜日期: {row[1]}")
print(f"最低价: ¥{row[2]}")
d = json.loads(row[3])
print(f"总航班数: {len(d)}")
print("\n按日期分组:")
from collections import defaultdict
by_date = defaultdict(list)
for f in d:
    by_date[f.get("date", "无")].append(f)
for date, flights in by_date.items():
    prices = sorted([f["price"] for f in flights])
    print(f"  {date}: {len(flights)}个航班, 价格 {prices}")

print("\n按价格排序前10个:")
sorted_flights = sorted(d, key=lambda x: x["price"])
for f in sorted_flights[:10]:
    print(f"  ¥{f['price']} | {f['date']} | {f['airline']} {f['flight_no']}")
