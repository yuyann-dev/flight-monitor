import subprocess, json

for date in ["2026-12-13", "2026-12-14", "2026-12-15"]:
    print(f"\n=== {date} ===")
    result = subprocess.run(
        ["flyai", "search-flight", "--origin", "Hefei", "--destination", "Chengdu", "--dep-date", date, "--seat-class-name", "economy"],
        capture_output=True, text=True, timeout=30
    )
    try:
        data = json.loads(result.stdout)
        if data["status"] == 0:
            print(f"成功，{len(data['data']['itemList'])} 个航班")
        else:
            print(f"失败: {data['message']}")
    except Exception as e:
        print(f"解析错误: {e}")
        print(result.stdout[:200])
