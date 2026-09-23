import importlib.util
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime, timezone

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch_noaa.py'


class NOAAContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('fetch_noaa', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
        else:
            cls.module = None

    def implementation(self):
        self.assertIsNotNone(self.module, 'NOAA extraction implementation is missing')
        return self.module

    def test_index_byte_ranges_end_before_the_next_message(self):
        m = self.implementation()
        text = ('1:0:d=2026010112:TMP:2 m above ground:24 hour fcst:\n'
                '2:100:d=2026010112:UGRD:100 m above ground:24 hour fcst:\n'
                '3:250:d=2026010112:VGRD:100 m above ground:24 hour fcst:\n'
                '4:450:d=2026010112:TMP:surface:24 hour fcst:\n')
        self.assertEqual(m.select_ranges(text), {'temperature': (0, 99), 'wind': (100, 449)})

    def test_missing_wind_component_is_rejected(self):
        m = self.implementation()
        with self.assertRaisesRegex(ValueError, 'VGRD'):
            m.select_ranges('1:0:d=2026010112:TMP:2 m above ground:24 hour fcst:\n'
                            '2:100:d=2026010112:UGRD:100 m above ground:24 hour fcst:\n'
                            '3:250:d=2026010112:TMP:surface:24 hour fcst:\n')

    def test_late_index_publication_is_rejected_even_if_grib_is_early(self):
        m = self.implementation()
        origin = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, 'after'):
            m.check_availability('2026-01-01T15:00:00Z', '2026-01-01T18:00:01Z', origin)
        self.assertEqual(m.check_availability('2026-01-01T15:00:00Z', '2026-01-01T15:30:00Z', origin),
                         '2026-01-01T15:30:00+00:00')

    def test_bilinear_interpolation_uses_coordinates_not_point_order(self):
        m = self.implementation()
        points = [{'lat': 1, 'lon': 1, 'value': 30}, {'lat': 0, 'lon': 0, 'value': 0},
                  {'lat': 1, 'lon': 0, 'value': 20}, {'lat': 0, 'lon': 1, 'value': 10}]
        self.assertAlmostEqual(m.bilinear(points, 0.25, 0.5), 10.0)
        self.assertAlmostEqual(m.bilinear(points, 1, 1), 30.0)

    def test_bilinear_rejects_extrapolation(self):
        m = self.implementation()
        points = [{'lat': y, 'lon': x, 'value': 1} for y in (0, 1) for x in (0, 1)]
        with self.assertRaises(ValueError):
            m.bilinear(points, 2, 0.5)

    def test_truncated_grib_range_is_rejected(self):
        m = self.implementation()
        valid = b'GRIB' + b'\x00\x00\x00\x02' + (20).to_bytes(8, 'big') + b'7777'
        m.validate_grib_messages(valid)
        with self.assertRaises(ValueError):
            m.validate_grib_messages(valid[:-1])
        with self.assertRaises(ValueError):
            m.validate_grib_messages(b'<Error>AccessDenied</Error>')

    def test_explicit_refresh_bypasses_saved_and_research_listings(self):
        m=self.implementation()
        xml=lambda key: f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Contents><Key>{key}</Key><ETag>etag</ETag></Contents></ListBucketResult>'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=root/'research/weather-check/raw/noaa-gfs-20260201T12-list.xml'
            old.parent.mkdir(parents=True)
            old.write_text(xml('stale'))
            cache=root/'cache'
            (cache/'listings').mkdir(parents=True)
            (cache/'listings/20260201.xml').write_text(xml('also-stale'))
            def download(url,path):
                path.write_text(xml('fresh'))
                return path.read_bytes()
            with patch.object(m,'ROOT',root),patch.object(m,'download',side_effect=download):
                entries=m.get_listing(datetime(2026,2,1,12,tzinfo=timezone.utc),cache,refresh=True)
            self.assertEqual(set(entries),{'fresh'})

    def test_point_cache_miss_reloads_index_and_grib_instead_of_relabelling_old_values(self):
        m = self.implementation()
        run = datetime(2025, 11, 4, 12, tzinfo=timezone.utc)
        key = 'gfs.20251104/12/atmos/gfs.t12z.pgrb2.0p25.f007'
        entries = {key: {'ETag': 'new-etag', 'LastModified': '2025-11-04T15:00:00Z'},
                   key + '.idx': {'ETag': 'new-index-etag', 'LastModified': '2025-11-04T15:01:00Z'}}
        old_index = ('1:0:d=2025110412:TMP:2 m above ground:7 hour fcst:\n'
                     '2:24:d=2025110412:UGRD:100 m above ground:7 hour fcst:\n'
                     '3:48:d=2025110412:VGRD:100 m above ground:7 hour fcst:\n'
                     '4:72:d=2025110412:TMP:surface:7 hour fcst:\n')
        fresh_index = ('1:0:d=2025110412:HGT:surface:7 hour fcst:\n'
                       '2:24:d=2025110412:TMP:2 m above ground:7 hour fcst:\n'
                       '3:48:d=2025110412:UGRD:100 m above ground:7 hour fcst:\n'
                       '4:72:d=2025110412:VGRD:100 m above ground:7 hour fcst:\n'
                       '5:96:d=2025110412:TMP:surface:7 hour fcst:\n')

        def message(marker):
            return b'GRIB' + b'\x00\x00\x00\x02' + (24).to_bytes(8, 'big') + marker + b'7777'

        fresh_grib = b''.join(message(marker) for marker in (b'skip', b'Tnew', b'Unew', b'Vnew'))

        def download(url, path, byte_range=None):
            payload = fresh_index.encode() if url.endswith('.idx') else fresh_grib[byte_range[0]:byte_range[1] + 1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return payload

        def decode(payload, actual_run, actual_lead):
            # Stand in for ecCodes only; real range selection, GRIB framing,
            # cache selection and forecast-value construction remain exercised.
            values = {b'Told': ('temperature', 273.15), b'Uold': ('u', 0), b'Vold': ('v', 1),
                      b'Tnew': ('temperature', 282.15), b'Unew': ('u', 3), b'Vnew': ('v', 4)}
            result = {}
            for start in range(0, len(payload), 24):
                name, value = values[payload[start + 16:start + 20]]
                result[name] = {turbine: {'value': value, 'corners': []} for turbine in (1, 2)}
            return result

        for old_point in (True, False):
            with self.subTest(changed_etag=old_point), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                messages = cache / 'messages/20251104'
                messages.mkdir(parents=True)
                (messages / 'f007.idx').write_text(old_index)
                (messages / 'f007-temperature.grib2').write_bytes(message(b'Told'))
                (messages / 'f007-wind.grib2').write_bytes(message(b'Uold') + message(b'Vold'))
                point = cache / 'points/20251104/f007.json'
                if old_point:
                    point.parent.mkdir(parents=True)
                    point.write_text(json.dumps({'schema_version': 1, 'etag': 'old-etag',
                                                 'available_at_utc': '2025-11-04T15:01:00+00:00'}))
                with patch.object(m, 'download', side_effect=download), patch.object(m, 'decode_fields', side_effect=decode):
                    result = m.fetch_hour(run, 7, entries, cache, keep_grib=True)
                self.assertEqual([row['wind_speed_100m_ms'] for row in result['rows']], [5, 5])
                self.assertEqual([row['temperature_2m_c'] for row in result['rows']], [9, 9])
                self.assertEqual(result['etag'], 'new-etag')
                self.assertEqual(result['schema_version'], 1)
                self.assertEqual(result['message_evidence']['temperature']['bytes'], [24, 47])
                self.assertEqual(json.loads(point.read_text()), json.loads(json.dumps(result)))

    def test_matching_legacy_point_cache_is_reused_without_downloading(self):
        m = self.implementation()
        run = datetime(2025, 11, 4, 12, tzinfo=timezone.utc)
        key = 'gfs.20251104/12/atmos/gfs.t12z.pgrb2.0p25.f007'
        entries = {key: {'ETag': 'unchanged', 'LastModified': '2025-11-04T15:00:00Z'},
                   key + '.idx': {'LastModified': '2025-11-04T15:01:00Z'}}
        point_data = {'schema_version': 1, 'etag': 'unchanged',
                      'available_at_utc': '2025-11-04T15:01:00+00:00', 'rows': [{'wind_speed_100m_ms': 5}]}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            point = cache / 'points/20251104/f007.json'
            point.parent.mkdir(parents=True)
            point.write_text(json.dumps(point_data))
            with patch.object(m, 'download', side_effect=AssertionError('Matching point cache must work offline')):
                result = m.fetch_hour(run, 7, entries, cache, keep_grib=False)
            self.assertEqual(result, point_data)


if __name__ == '__main__':
    unittest.main()
