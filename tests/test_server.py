import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_agent import ExampleService
from windops.server import create_server
from windops.forecast import ForecastService, ISSUE_DATES
from test_forecast_service import weather_rows
import pandas as pd


class HTTPService(ExampleService):
    def list_issues(self):
        return ['2026-01-31']
    def get_forecast(self, issue_date, refresh=False):
        return self.predict(issue_date,self.get_weather(issue_date,refresh))


class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'web').mkdir()
        (self.root/'web/index.html').write_text('<h1>Forecast</h1>')
        (self.root/'.env').write_text('OPENAI_API_KEY=secret-never-serve\n')
        self.server=create_server(self.root,port=0,service=HTTPService(self.root))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        request=Request(self.url+path,data=None if body is None else json.dumps(body).encode(),
                        headers={'Content-Type':'application/json',**(headers or {})})
        with urlopen(request,timeout=5) as response:
            return response.read()

    def test_status_has_availability_but_never_api_key(self):
        raw=self.request('/api/status')
        result=json.loads(raw)
        self.assertEqual(result['available_issues'],['2026-01-31'])
        self.assertTrue(result['openai_configured'])
        self.assertNotIn(b'secret-never-serve',raw)

    def test_status_reports_active_algorithm_and_its_descriptive_january_metrics(self):
        self.server.app.service.model = {"id": "catboost_gfs", "label": "GFS model", "version": "new"}
        folder = self.root / "outputs/gfs_ml/january_descriptive"
        folder.mkdir(parents=True)
        (folder / "metrics.csv").write_text("candidate,turbine_id,horizon,mae,rmse\ncatboost_gfs,1,1-48,0.12,0.18\nraw_empirical,1,1-48,0.2,0.3\n")
        status = json.loads(self.request('/api/status'))
        self.assertEqual(status['model']['id'], 'catboost_gfs')
        self.assertEqual(status['baseline_metrics'], [{'turbine_id': 1, 'mae': .12, 'rmse': .18}])
        self.assertTrue(status['evaluation']['earlier_training_version'])

    def test_month_export_from_an_old_model_is_not_served_after_activation(self):
        self.server.app.service.model = {"id": "catboost_gfs", "version": "new"}
        folder = self.root / "outputs/february"
        folder.mkdir(parents=True)
        (folder / "forecast_day_ahead_february.csv").write_text("old,forecast\n")
        (folder / "manifest.json").write_text(json.dumps({'model': {'version': 'old'}}))
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/download-february')
        self.assertEqual(caught.exception.code, 409)
        self.assertFalse(json.loads(self.request('/api/status'))['data_status']['february_ready'])

    def test_month_export_rejects_changed_weather_or_csv_under_the_same_model(self):
        folder = self.root / "outputs/baseline"
        folder.mkdir(parents=True)
        curves = {str(t): {"bin_width_ms": .5, "bin_centers_ms": [.25, 1.25],
                           "bin_mean_power_normalized": [0., .8]} for t in (1, 2)}
        (folder / "empirical_curves.json").write_text(json.dumps(curves))
        weather_path = self.root / "data/weather/noaa_february.csv"
        weather_path.parent.mkdir(parents=True)
        rows = [row for issue in ISSUE_DATES for row in weather_rows(issue)]
        pd.DataFrame(rows).to_csv(weather_path, index=False)
        service = ForecastService(self.root)
        service.export_february()
        self.server.app.service = service
        self.assertTrue(self.server.app.february_ready())
        csv_path = self.root / "outputs/february/forecast_day_ahead_february.csv"
        original_csv, original_weather = csv_path.read_bytes(), weather_path.read_bytes()
        for changed in ('csv', 'weather'):
            with self.subTest(changed=changed):
                csv_path.write_bytes(original_csv)
                weather_path.write_bytes(original_weather)
                if changed == 'csv':
                    csv_path.write_bytes(original_csv + b'corrupted\n')
                else:
                    weather_path.write_bytes(original_weather.replace(b',0.75,', b',1.25,', 1))
                self.assertFalse(self.server.app.february_ready())
                with self.assertRaises(HTTPError) as caught:
                    self.request('/api/download-february')
                self.assertEqual(caught.exception.code, 409)
        csv_path.write_bytes(original_csv)
        weather_path.write_bytes(original_weather)
        original_get_weather = service.get_weather

        def export_replaced_while_checking_weather(issue):
            weather = original_get_weather(issue)
            csv_path.write_bytes(b'unverified replacement\n')
            return weather

        with patch.object(service, 'get_weather', side_effect=export_replaced_while_checking_weather):
            self.assertEqual(self.request('/api/download-february'), original_csv)

    def test_static_server_cannot_serve_dotenv_or_parent_paths(self):
        self.assertIn(b'Forecast',self.request('/'))
        for path in ('/.env','/../.env','/%2e%2e/.env','/api/jobs/../../.env'):
            with self.subTest(path=path),self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertEqual(caught.exception.code,404)

    def test_cross_origin_post_is_rejected_before_any_job_runs(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/run',{'issue_date':'2026-01-31','mode':'openai'}, {'Origin':'https://evil.example'})
        self.assertEqual(caught.exception.code,403)
        self.assertEqual(self.server.app.service.predictions,0)

    def test_invalid_date_and_boolean_mode_are_rejected(self):
        for body in ({'issue_date':'2026-03-01','mode':'deterministic'},
                     {'issue_date':'2026-01-31','mode':True},
                     {'issue_date':'2026-01-31','mode':'deterministic','refresh':'false'}):
            with self.subTest(body=body),self.assertRaises(HTTPError) as caught:
                self.request('/api/run',body)
            self.assertEqual(caught.exception.code,400)

    def test_async_job_returns_forecast_real_events_and_csv(self):
        result=json.loads(self.request('/api/run',{'issue_date':'2026-01-31','mode':'deterministic','refresh':False}))
        for _ in range(100):
            job=json.loads(self.request('/api/jobs/'+result['job_id']))
            if job['status']!='running':break
            time.sleep(.01)
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(len(job['result']['rows']),96)
        self.assertEqual(len([e for e in job['events'] if e['status']=='complete']),4)
        self.assertEqual(job['mode'],'deterministic')
        self.assertEqual(len(self.request('/api/download?issue_date=2026-01-31').decode().splitlines()),97)
        self.assertTrue((self.root/'outputs/demo/jobs'/f'{result["job_id"]}.json').exists())


if __name__=='__main__':
    unittest.main()
