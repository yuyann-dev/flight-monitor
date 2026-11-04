import subprocess, json

print("=== 12-13 航班价格 ===")
result = subprocess.run(["flyai", "search-flight", "--origin", "Hefei", "--destination", "Chengdu", "--dep-date", "2026-12-13", "--seat-class-name", "economy"], capture_output=True, text=True, timeout=30)
data = json.loads(result.stdout)
flights = data["data"]["itemList"]
prices = [float(f["ticketPrice"]) for f in flights]
prices.sort()
print(f"共{len(flights)}个航班")
print(f"最低价: ¥{prices[0]}")
print(f"前5个价格: {prices[:5]}")

print("\n=== 12-14 航班价格 ===")
result = subprocess.run(["flyai", "search-flight", "--origin", "Hefei", "--destination", "Chengdu", "--dep-date", "2026-12-14", "--seat-class-name", "economy"], capture_output=True, text=True, timeout=30)
data = json.loads(result.stdout)
flights = data["data"]["itemList"]
prices = [float(f["ticketPrice"]) for f in flights]
prices.sort()
print(f"共{len(flights)}个航班")
print(f"最低价: ¥{prices[0]}")
print(f"前5个价格: {prices[:5]}")
