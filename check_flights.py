import sqlite3, json

conn = sqlite3.connect("/opt/flight-monitor/data/flight_monitor.db")
c = conn.cursor()
c.execute("SELECT flight_detail FROM price_samples ORDER BY checked_at DESC LIMIT 1")
row = c.fetchone()
d = json.loads(row[0])
print("航班数:", len(d))
print("日期列表:", [f.get("date", "无") for f in d])
print("前3个航班:")
for f in d[:3]:
    print(f"  {f.get('date')} {f.get('airline')} {f.get('flight_no')} ¥{f.get('price')}")
