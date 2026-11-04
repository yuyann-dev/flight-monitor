#!/bin/bash
for d in 2026-12-15 2026-12-20 2026-12-25 2027-01-01; do
  echo -n "$d: "
  result=$(flyai search-flight --origin Hefei --destination Chengdu --dep-date $d --seat-class-name economy 2>&1)
  status=$(echo "$result" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("成功" if d["status"]==0 else "失败")')
  echo "$status"
done
