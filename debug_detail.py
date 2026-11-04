import sys
sys.path.insert(0, "/opt/flight-monitor")
from app.database import SessionLocal
from app.models import PriceSample

db = SessionLocal()
sample = db.query(PriceSample).filter(PriceSample.monitor_id == 1).order_by(PriceSample.checked_at.desc()).first()
print("sample exists:", sample is not None)
if sample:
    print("flight_detail type:", type(sample.flight_detail))
    print("flight_detail length:", len(sample.flight_detail) if sample.flight_detail else 0)
    if sample.flight_detail:
        print("first flight:", sample.flight_detail[0] if isinstance(sample.flight_detail, list) else "not list")
db.close()
