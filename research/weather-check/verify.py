"""Verify cached, read-only weather archive probes. Does not train a model."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
SITES = [
    {"turbine": 1, "latitude": 43.645150, "longitude": 78.535604,
     "source": "https://maps.app.goo.gl/iN6svMt69D5qRpFU9"},
    {"turbine": 2, "latitude": 43.643198, "longitude": 78.538828,
     "source": "https://maps.app.goo.gl/8UQMwsYavY6nLvFY8"},
]


def read_listing(path):
    root = ET.fromstring(path.read_text())
    return {
        e.findtext("s:Key", namespaces=NS):
        {c.tag.rsplit("}", 1)[-1]: c.text for c in e}
        for e in root.findall("s:Contents", NS)
    }


coverage = []
for day in range(29):
    run = datetime(2026, 1, 31, 12, tzinfo=timezone.utc) + timedelta(days=day)
    date = run.strftime("%Y%m%d")
    cutoff = run + timedelta(hours=6)
    entries = read_listing(RAW / f"noaa-gfs-{date}T12-list.xml")
    selected = []
    for lead in range(7, 55):
        for suffix in ("", ".idx"):
            key = f"gfs.{date}/12/atmos/gfs.t12z.pgrb2.0p25.f{lead:03d}{suffix}"
            assert key in entries, f"Missing object: {key}"
            entry = entries[key]
            modified = datetime.fromisoformat(entry["LastModified"].replace("Z", "+00:00"))
            assert modified <= cutoff, f"Object unavailable by cutoff: {key}"
            assert int(entry["Size"]) > 0
            selected.append({"key": key, "last_modified": entry["LastModified"],
                             "size": int(entry["Size"]), "etag": entry["ETag"]})
    coverage.append({
        "run_utc": run.isoformat(), "example_cutoff_utc": cutoff.isoformat(),
        "first_valid_time_utc": (run + timedelta(hours=7)).isoformat(),
        "last_valid_time_utc": (run + timedelta(hours=54)).isoformat(),
        "present_objects": len(selected),
        "latest_publication_utc": max(x["last_modified"] for x in selected),
        "objects": selected,
    })

weather = json.loads((RAW / "openmeteo-gfs-20260101-20260302.json").read_text())
weather_checks = []
for site, obj in zip(SITES, weather, strict=True):
    hours = obj["hourly"]["time"]
    expected_start = datetime(2026, 1, 1)
    assert len(hours) == 1464
    assert hours == [(expected_start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                     for i in range(1464)]
    variables = {k: v for k, v in obj["hourly"].items() if k != "time"}
    assert len(variables) == 9
    for key, values in variables.items():
        assert len(values) == len(hours)
        assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
        if key.startswith("wind_speed"):
            assert obj["hourly_units"][key] == "m/s"
    weather_checks.append({
        "turbine": site["turbine"], "hours": len(hours), "variables": len(variables),
        "null_or_nonfinite": 0, "grid_latitude": obj["latitude"],
        "grid_longitude": obj["longitude"], "timezone": obj["timezone"],
    })

tz_responses = json.loads((RAW / "openmeteo-timezone-auto.json").read_text())
assert len(tz_responses) == 2
assert all(o["timezone"] == "Asia/Almaty" and o["utc_offset_seconds"] == 18000
           for o in tz_responses)
zone = ZoneInfo("Asia/Almaty")
before = datetime(2024, 2, 29, 17, 59, 59, tzinfo=timezone.utc).astimezone(zone)
after = datetime(2024, 2, 29, 18, 0, 0, tzinfo=timezone.utc).astimezone(zone)
assert before.utcoffset() == timedelta(hours=6)
assert after.utcoffset() == timedelta(hours=5)

index_lines = (RAW / "noaa-gfs-20260131T12-f024.idx").read_text().splitlines()
fields = [line for line in index_lines
          if ":UGRD:100 m above ground:" in line
          or ":VGRD:100 m above ground:" in line
          or ":TMP:2 m above ground:" in line]
assert len(fields) == 3
binary_header = (RAW / "noaa-gfs-20260131T12-f024-ugrd-header.bin").read_bytes()
assert len(binary_header) == 16 and binary_header[:4] == b"GRIB" and binary_header[7] == 2
assert int.from_bytes(binary_header[8:16], "big") == 970660
headers = (RAW / "noaa-gfs-20260131T12-f024-ugrd-header.txt").read_text().lower()
assert "206" in headers and "content-range: bytes 497823535-497823550/539031306" in headers

validation_listing = read_listing(RAW / "noaa-gfs-validation-20260101T12-list.xml")
validation_missing = [lead for lead in range(7, 55)
                      if f"gfs.20260101/12/atmos/gfs.t12z.pgrb2.0p25.f{lead:03d}"
                      not in validation_listing]
assert not validation_missing

a, b = SITES
lat1, lat2 = math.radians(a["latitude"]), math.radians(b["latitude"])
dlon = math.radians(b["longitude"] - a["longitude"])
angle = math.sin((lat2 - lat1) / 2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon / 2)**2
distance_m = 6371008.8 * 2 * math.atan2(math.sqrt(angle), math.sqrt(1-angle))

summary = {
    "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    "coordinates": SITES, "distance_m": round(distance_m, 1),
    "noaa": {"cycles": len(coverage), "forecast_hours_per_cycle": 48,
             "forecast_hour_records": len(coverage)*48, "objects_including_indices": len(coverage)*96,
             "missing_objects": 0, "objects_published_after_example_cutoff": 0,
             "example_cutoff_utc_hour": 18, "run_utc_hour": 12,
             "all_test_cycles_verified": True, "jan1_validation_run_spot_checked": True,
             "fields_spot_checked": fields, "partial_grib_request_http_status": 206,
             "grib_edition": 2, "grib_fields_decoded": False},
    "open_meteo": {"model": "gfs_global", "locations": weather_checks,
                   "same_returned_grid_and_values": weather[0]["hourly"] == weather[1]["hourly"],
                   "exact_run_or_available_at_returned": False},
    "timezone": {"civil_zone": "Asia/Almaty", "feb2026_utc_offset_hours": 5,
                 "transition_before_local": before.isoformat(),
                 "transition_after_local": after.isoformat(),
                 "csv_timezone_confirmed": False,
                 "csv_timestamp_interval_start_or_end_confirmed": False},
    "raw_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(RAW.iterdir()) if p.is_file()},
}
(ROOT / "noaa-coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2))
(ROOT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k != "raw_sha256"}, ensure_ascii=False, indent=2))
