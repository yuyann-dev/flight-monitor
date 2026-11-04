import sys
sys.path.insert(0, "/opt/flight-monitor")
from app.crawlers.fliggy_api import FliggyCrawler

crawler = FliggyCrawler()
print("=== 爬12-13 ===")
f1 = crawler.search("HFE", "TFU", "2026-12-13")
print(f"拿到 {len(f1)} 个航班")
for f in f1[:3]:
    print(f"  {f['price']}元 | {f['airline']} {f['flight_no']}")

print("\n=== 爬12-14 ===")
f2 = crawler.search("HFE", "TFU", "2026-12-14")
print(f"拿到 {len(f2)} 个航班")
for f in f2[:3]:
    print(f"  {f['price']}元 | {f['airline']} {f['flight_no']}")

print(f"\n总共: {len(f1) + len(f2)} 个航班")
