import sqlite3

conn = sqlite3.connect("/opt/flight-monitor/data/flight_monitor.db")
c = conn.cursor()
c.execute("SELECT id, departure_city, arrival_city, departure_date_start, departure_date_end FROM monitors WHERE id=1")
row = c.fetchone()
print(f"ID: {row[0]}")
print(f"航线: {row[1]} -> {row[2]}")
print(f"开始日期: {row[3]}")
print(f"结束日期: {row[4]}")
