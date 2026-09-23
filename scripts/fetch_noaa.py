"""Download only required GFS GRIB messages and extract archived point forecasts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timedelta, timezone
from email.parser import Parser
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

BASE = 'https://noaa-gfs-bdp-pds.s3.amazonaws.com'
ROOT = Path(__file__).resolve().parents[1]
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
SITES = [(1, 43.645150, 78.535604), (2, 43.643198, 78.538828)]
DECODE_LOCK = threading.Lock()


def select_ranges(index_text):
    records = [line.split(':') for line in index_text.splitlines() if line.strip()]
    found = {}
    for field, parameter, level in [('temperature', 'TMP', '2 m above ground'),
                                     ('u', 'UGRD', '100 m above ground'),
                                     ('v', 'VGRD', '100 m above ground')]:
        matches = [i for i, r in enumerate(records) if len(r) >= 6 and r[3:5] == [parameter, level]]
        if len(matches) != 1:
            raise ValueError(f'Expected one {parameter}:{level}, found {len(matches)}')
        i = matches[0]
        if i + 1 == len(records):
            raise ValueError(f'Cannot determine final byte of {parameter}')
        found[field] = (int(records[i][1]), int(records[i + 1][1]) - 1)
    if found['u'][1] + 1 != found['v'][0]:
        raise ValueError('100m wind components must be adjacent to download together')
    return {'temperature': found['temperature'], 'wind': (found['u'][0], found['v'][1])}


def check_availability(grib_modified, index_modified, origin):
    times = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in (grib_modified, index_modified)]
    latest = max(times)
    if latest > origin:
        raise ValueError(f'Weather published after forecast origin: {latest} > {origin}')
    return latest.isoformat()


def bilinear(points, latitude, longitude):
    ys = sorted({float(p['lat']) for p in points})
    xs = sorted({float(p['lon']) for p in points})
    if len(xs) != 2 or len(ys) != 2 or len(points) != 4:
        raise ValueError('Expected four corners of a regular grid cell')
    if not (ys[0] <= latitude <= ys[1] and xs[0] <= longitude <= xs[1]):
        raise ValueError('Coordinate outside surrounding grid cell')
    tx, ty = (longitude - xs[0]) / (xs[1] - xs[0]), (latitude - ys[0]) / (ys[1] - ys[0])
    values = {(float(p['lat']), float(p['lon'])): float(p['value']) for p in points}
    if len(values) != 4 or not all(math.isfinite(v) for v in values.values()):
        raise ValueError('Invalid or duplicated grid corners')
    return ((1-tx)*(1-ty)*values[(ys[0], xs[0])] + tx*(1-ty)*values[(ys[0], xs[1])]
            + (1-tx)*ty*values[(ys[1], xs[0])] + tx*ty*values[(ys[1], xs[1])])


def validate_grib_messages(payload):
    pos = 0
    while pos < len(payload):
        if payload[pos:pos+4] != b'GRIB' or len(payload)-pos < 20 or payload[pos+7] != 2:
            raise ValueError('Invalid GRIB2 message header')
        size = int.from_bytes(payload[pos+8:pos+16], 'big')
        if size < 20 or pos+size > len(payload) or payload[pos+size-4:pos+size] != b'7777':
            raise ValueError('Truncated or invalid GRIB2 message')
        pos += size
    if not payload:
        raise ValueError('Empty GRIB response')


def download(url, path, byte_range=None, expected_metadata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    headers = path.with_suffix(path.suffix + '.headers')
    command = ['curl', '-sS', '--fail-with-body', '--max-time', '60', '--retry', '2',
               '--retry-delay', '1', '-D', str(headers), '-o', str(temporary), '-w', '%{http_code}']
    if byte_range:
        command += ['--range', f'{byte_range[0]}-{byte_range[1]}']
    if expected_metadata is not None:
        command += ['--header', 'If-Match: ' + expected_metadata['ETag']]
    result = subprocess.run(command + [url], capture_output=True, text=True, check=False)
    expected = '206' if byte_range else '200'
    if result.returncode or result.stdout.strip() != expected:
        raise RuntimeError(f'HTTP download failed ({result.stdout}): {url}: {result.stderr[-400:]}')
    content = temporary.read_bytes()
    if byte_range or expected_metadata is not None:
        # curl can record CONNECT, retry, and final response headers in one file.
        # Only the final response describes the bytes that will be committed.
        blocks = [block for block in headers.read_text().split('\n\n') if block.startswith('HTTP/')]
        if not blocks:
            raise ValueError('Missing HTTP response headers')
        response = Parser().parsestr(blocks[-1].partition('\n')[2])
    if expected_metadata is not None:
        if response.get_all('ETag', []) != [expected_metadata['ETag']]:
            raise ValueError('Downloaded ETag differs from the listed source identity')
        modified = response.get_all('Last-Modified', [])
        if len(modified) != 1:
            raise ValueError('Missing or duplicate Last-Modified publication header')
        try:
            actual_modified = parsedate_to_datetime(modified[0])
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid Last-Modified publication header') from error
        listed_modified = datetime.fromisoformat(expected_metadata['LastModified'].replace('Z', '+00:00'))
        # HTTP dates have second precision; S3 listings may include fractions.
        # ETag equality binds the downloaded bytes to that exact listed object.
        if (actual_modified.tzinfo is None
                or actual_modified != listed_modified.replace(microsecond=0)):
            raise ValueError('Downloaded Last-Modified differs from the listed publication time')
    if byte_range:
        start, end = byte_range
        if (len(content) != end-start+1
                or not response.get('Content-Range', '').lower().startswith(f'bytes {start}-{end}/')):
            raise ValueError('Range response length or Content-Range mismatch')
    temporary.replace(path)
    return content


def write_point_cache(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_listing(run, cache, refresh=False):
    date = run.strftime('%Y%m%d')
    path = cache / 'listings' / f'{date}.xml'
    if refresh or not path.exists():
        existing = ROOT / 'research/weather-check/raw' / f'noaa-gfs-{date}T12-list.xml'
        if date == '20260101':
            existing = ROOT / 'research/weather-check/raw/noaa-gfs-validation-20260101T12-list.xml'
        if existing.exists() and not refresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(existing.read_bytes())
        else:
            url = f'{BASE}/?list-type=2&prefix=gfs.{date}%2F12%2Fatmos%2Fgfs.t12z.pgrb2.0p25.f0&max-keys=200'
            download(url, path)
    root = ET.fromstring(path.read_text())
    return {e.findtext('s:Key', namespaces=NS): {c.tag.rsplit('}', 1)[-1]: c.text for c in e}
            for e in root.findall('s:Contents', NS)}


def decode_fields(payload, run, lead):
    from eccodes import codes_get, codes_grib_find_nearest, codes_release
    fields = {}
    # The Python binding requires a real file descriptor, so codes_new_from_message
    # is used on each already validated GRIB message instead.
    from eccodes import codes_new_from_message
    pos = 0
    while pos < len(payload):
        size = int.from_bytes(payload[pos+8:pos+16], 'big')
        handle = codes_new_from_message(payload[pos:pos+size])
        try:
            actual_run = datetime.strptime(f'{codes_get(handle, "dataDate")}{int(codes_get(handle, "dataTime")):04d}',
                                           '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
            valid = datetime.strptime(f'{codes_get(handle, "validityDate")}{int(codes_get(handle, "validityTime")):04d}',
                                      '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
            if actual_run != run or valid != run + timedelta(hours=lead):
                raise ValueError('GRIB run or valid time does not match requested forecast')
            if codes_get(handle, 'stepType') != 'instant':
                raise ValueError('Expected instantaneous forecast field')
            short_name = codes_get(handle, 'shortName')
            level = int(codes_get(handle, 'level'))
            level_type = codes_get(handle, 'typeOfLevel')
            if level_type != 'heightAboveGround':
                raise ValueError(f'Unexpected level type {level_type}')
            if short_name in ('100u', 'u') and level == 100:
                variable = 'u'
            elif short_name in ('100v', 'v') and level == 100:
                variable = 'v'
            elif short_name in ('2t', 't') and level == 2:
                variable = 'temperature'
            else:
                raise ValueError(f'Unexpected forecast field {short_name} at {level}m')
            points_by_site = {}
            for turbine_id, latitude, longitude in SITES:
                points = codes_grib_find_nearest(handle, latitude, longitude, npoints=4)
                corners = [{'lat': float(p['lat']), 'lon': float(p['lon']), 'value': float(p['value'])} for p in points]
                value = bilinear(corners, latitude, longitude)
                points_by_site[turbine_id] = {'value': value, 'corners': corners}
            fields[variable] = points_by_site
        finally:
            codes_release(handle)
        pos += size
    return fields


def fetch_hour(run, lead, entries, cache, keep_grib):
    date = run.strftime('%Y%m%d')
    origin = run + timedelta(hours=6)
    key = f'gfs.{date}/12/atmos/gfs.t12z.pgrb2.0p25.f{lead:03d}'
    meta, index_meta = entries[key], entries[key + '.idx']
    available_at = check_availability(meta['LastModified'], index_meta['LastModified'], origin)
    cached = cache / 'points' / date / f'f{lead:03d}.json'
    if cached.exists():
        try:
            obj = json.loads(cached.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            obj = None
        if (isinstance(obj, dict) and obj.get('schema_version') == 1 and obj.get('etag') == meta['ETag']
                and obj.get('available_at_utc') == available_at):
            return obj
    directory = cache / 'messages' / date
    index_path = directory / f'f{lead:03d}.idx'
    # Only a complete point cache carries source identity. Intermediates may
    # belong to an interrupted download of an older source revision.
    for path in (index_path, directory / f'f{lead:03d}-temperature.grib2',
                 directory / f'f{lead:03d}-wind.grib2'):
        path.unlink(missing_ok=True)
    index_text = download(BASE + '/' + key + '.idx', index_path, expected_metadata=index_meta).decode()
    ranges = select_ranges(index_text)
    fields, message_evidence, generated_paths = {}, {}, []
    for name, byte_range in ranges.items():
        path = directory / f'f{lead:03d}-{name}.grib2'
        payload = download(BASE + '/' + key, path, byte_range, expected_metadata=meta)
        validate_grib_messages(payload)
        with DECODE_LOCK:
            fields.update(decode_fields(payload, run, lead))
        message_evidence[name] = {'bytes': list(byte_range), 'sha256': hashlib.sha256(payload).hexdigest()}
        generated_paths.append(path)
    if set(fields) != {'u', 'v', 'temperature'}:
        raise ValueError('Missing decoded meteorological field')
    rows = []
    for turbine_id, latitude, longitude in SITES:
        u, v = fields['u'][turbine_id]['value'], fields['v'][turbine_id]['value']
        rows.append({
            'turbine_id': turbine_id, 'forecast_origin_utc': origin.isoformat(),
            'run_time_utc': run.isoformat(), 'valid_time_utc': (run + timedelta(hours=lead)).isoformat(),
            'lead_hours': lead-6, 'wind_speed_100m_ms': math.hypot(u, v),
            'temperature_2m_c': fields['temperature'][turbine_id]['value'] - 273.15,
            'wind_direction_100m_deg': math.degrees(math.atan2(-u, -v)) % 360,
            'available_at_utc': available_at, 'gfs_forecast_hour': lead,
            'source_key': key, 'source_etag': meta['ETag'],
            'interpolation': 'bilinear_uv_temperature', 'latitude': latitude, 'longitude': longitude,
        })
    obj = {'schema_version': 1, 'etag': meta['ETag'], 'available_at_utc': available_at,
           'source_key': key, 'message_evidence': message_evidence, 'grid_fields': fields, 'rows': rows}
    write_point_cache(cached, obj)
    if not keep_grib:
        for path in generated_paths:
            path.unlink()
    return obj


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date', required=True, help='First GFS run date, YYYY-MM-DD')
    parser.add_argument('--end-date', required=True, help='Last GFS run date, inclusive')
    parser.add_argument('--lead-start', type=int, default=7)
    parser.add_argument('--lead-end', type=int, default=54)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--cache', type=Path, default=ROOT / 'data/cache/noaa')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/weather/noaa_forecasts.csv')
    parser.add_argument('--keep-grib', action='store_true')
    parser.add_argument('--refresh-listings', action='store_true', help='Read current NOAA object listings instead of saved listing snapshots')
    args = parser.parse_args()
    if not 7 <= args.lead_start <= args.lead_end <= 54:
        parser.error('Forecast hours must be within f007..f054 (lead 1..48 from issue time)')
    start = datetime.strptime(args.start_date, '%Y-%m-%d').replace(hour=12, tzinfo=timezone.utc)
    end = datetime.strptime(args.end_date, '%Y-%m-%d').replace(hour=12, tzinfo=timezone.utc)
    if end < start or not 1 <= args.workers <= 16:
        parser.error('Invalid date interval or worker count')
    runs = [start + timedelta(days=i) for i in range((end-start).days+1)]
    started = time.monotonic()
    listings = {}
    with ThreadPoolExecutor(max_workers=min(args.workers, 8)) as pool:
        futures = {pool.submit(get_listing, run, args.cache, args.refresh_listings): run for run in runs}
        for future in as_completed(futures):
            run = futures[future]
            listings[run] = future.result()
    jobs = [(run, lead) for run in runs for lead in range(args.lead_start, args.lead_end+1)]
    print(f'Listed {len(runs)} runs; extracting {len(jobs)} forecast hours for two turbines.', flush=True)
    outputs, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_hour, run, lead, listings[run], args.cache, args.keep_grib): (run, lead)
                   for run, lead in jobs}
        for completed, future in enumerate(as_completed(futures), 1):
            run, lead = futures[future]
            try:
                outputs.extend(future.result()['rows'])
            except Exception as exc:
                errors.append({'run': run.isoformat(), 'gfs_forecast_hour': lead, 'error': str(exc)})
                print(f'ERROR {run.date()} f{lead:03d}: {exc}', flush=True)
            if completed % 48 == 0 or completed == len(jobs):
                print(f'{completed}/{len(jobs)} completed; {len(errors)} errors; {time.monotonic()-started:.1f}s', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if errors:
        args.output.with_suffix('.errors.json').write_text(json.dumps(errors, indent=2))
        raise RuntimeError(f'{len(errors)} forecast hours failed. Valid cached results can be resumed.')
    outputs.sort(key=lambda r: (r['forecast_origin_utc'], r['valid_time_utc'], r['turbine_id']))
    with args.output.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(outputs[0]))
        writer.writeheader()
        writer.writerows(outputs)
    summary = {'runs': len(runs), 'forecast_hours': len(jobs), 'rows': len(outputs),
               'start_run': start.isoformat(), 'end_run': end.isoformat(),
               'interpolation': 'bilinear on U,V and temperature; wind magnitude after interpolation',
               'retrieval_seconds': round(time.monotonic()-started, 1),
               'source': BASE, 'origin': 'run time + 6 hours', 'timezone': 'UTC'}
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
