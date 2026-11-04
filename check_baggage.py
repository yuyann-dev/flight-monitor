import subprocess
import json

cmd = [
    "flyai", "search-flight",
    "--origin", "Hefei",
    "--destination", "Chengdu",
    "--dep-date", "2026-12-13",
    "--seat-class-name", "economy",
]

proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
data = json.loads(proc.stdout)
item = data["data"]["itemList"][0]

print("=== item 顶层字段 ===")
print(list(item.keys()))

print("\n=== journeys[0] 字段 ===")
journey = item["journeys"][0]
print(list(journey.keys()))

print("\n=== segments[0] 字段 ===")
seg = journey["segments"][0]
print(list(seg.keys()))

print("\n=== 完整 item JSON (前2000字符) ===")
print(json.dumps(item, ensure_ascii=False, indent=2)[:2000])
