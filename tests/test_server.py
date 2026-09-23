import csv
import io
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
from windops.server import DemoApp, create_server
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
        for name,content in {
            'landing.html':'<h1>WindOps overview</h1>',
            'index.html':'<h1>Forecast dashboard</h1>',
            'landing.css':'body { color: navy; }',
            'landing.js':'document.body.dataset.page = "landing";',
            'style.css':'body { color: blue; }',
            'app.js':'document.body.dataset.page = "dashboard";',
        }.items():
            (self.root/'web'/name).write_text(content)
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
        try:
            with urlopen(request,timeout=5) as response:
                return response.read()
        except HTTPError as error:
            error.close()
            raise

    def completed_job(self):
        result=json.loads(self.request('/api/run',{'issue_date':'2026-01-31','mode':'deterministic'}))
        for _ in range(100):
            job=json.loads(self.request('/api/jobs/'+result['job_id']))
            if job['status']!='running':
                self.assertEqual(job['status'],'complete',job)
                return job
            time.sleep(.01)
        self.fail('Forecast did not complete')

    def restart_app(self):
        self.server.app=DemoApp(self.root,HTTPService(self.root))

    def test_history_survives_restart_with_timestamps_and_isolation(self):
        job=self.completed_job()
        self.assertIsNotNone(job.get('created_at'))
        self.assertIsNotNone(job.get('finished_at'))
        self.assertFalse(job['refresh'])
        self.assertIn('events_24h',job['result'])
        self.restart_app()
        restored=json.loads(self.request('/api/jobs/'+job['job_id']))
        self.assertEqual(restored['result']['rows'],job['result']['rows'])
        self.assertIn('events',restored['result'])
        history=json.loads(self.request('/api/jobs'))['jobs']
        self.assertEqual([item['job_id'] for item in history],[job['job_id']])
        self.assertTrue(history[0]['has_result'])
        self.assertNotIn('result',history[0])
        loaded=self.server.app.get_job(job['job_id'])
        loaded['result']['rows'][0]['power_normalized']=999
        self.assertEqual(self.server.app.get_job(job['job_id'])['result']['rows'][0]['power_normalized'],.1)

    def test_live_job_remains_accessible_if_its_saved_file_is_removed(self):
        job=self.completed_job()
        (self.root/'outputs/demo/jobs'/f'{job["job_id"]}.json').unlink()
        restored=self.server.app.get_job(job['job_id'])
        self.assertIsNotNone(restored)
        restored['result']['rows'][0]['power_normalized']=999
        self.assertEqual(self.server.app.get_job(job['job_id'])['result']['rows'][0]['power_normalized'],.1)
        self.assertEqual(json.loads(self.request('/api/jobs'))['jobs'][0]['job_id'],job['job_id'])

    def test_snapshot_download_does_not_recalculate_after_restart(self):
        job=self.completed_job()
        self.restart_app()
        with patch.object(self.server.app.service,'get_forecast',side_effect=AssertionError('Current forecast must not be loaded')):
            payload=self.request('/api/jobs/'+job['job_id']+'/download').decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(rows),96)
        self.assertEqual(float(rows[0]['power_normalized']),.1)

    def test_old_jobs_derive_dates_and_running_jobs_are_interrupted(self):
        job=self.completed_job()
        path=self.root/'outputs/demo/jobs'/f'{job["job_id"]}.json'
        job.pop('created_at',None)
        job.pop('finished_at',None)
        job.pop('refresh',None)
        job['result'].pop('events',None)
        job['result'].pop('events_24h',None)
        path.write_text(json.dumps(job))
        saved=path.read_bytes()
        self.restart_app()
        restored=json.loads(self.request('/api/jobs/'+job['job_id']))
        self.assertEqual(restored['created_at'],job['events'][0]['time'])
        self.assertEqual(restored['finished_at'],job['events'][-1]['time'])
        self.assertIsNone(restored['refresh'])
        self.assertIn('events',restored['result'])
        self.assertIn('events_24h',restored['result'])
        self.assertEqual(path.read_bytes(),saved)
        job.update(status='running',result=None)
        path.write_text(json.dumps(job))
        restored=json.loads(self.request('/api/jobs/'+job['job_id']))
        self.assertEqual(restored['status'],'interrupted')
        self.assertTrue(restored['error'])
        self.assertEqual(self.server.app.service.predictions,0)

    def test_corrupt_jobs_are_skipped_and_ids_cannot_escape_directory(self):
        directory=self.root/'outputs/demo/jobs'
        directory.mkdir(parents=True)
        for identifier,payload in (('a'*32,'{'),('b'*32,'[]'),('c'*32,json.dumps({'job_id':'c'*32}))):
            (directory/f'{identifier}.json').write_text(payload)
            with self.subTest(identifier=identifier),self.assertRaises(HTTPError) as caught:
                self.request('/api/jobs/'+identifier)
            self.assertEqual(caught.exception.code,404)
        self.assertEqual(json.loads(self.request('/api/jobs')),{'jobs':[]})
        for identifier in ('../other','A'*32,'a'*31,'a'*33,'a'*32+'/other'):
            self.assertIsNone(self.server.app.get_job(identifier))

    def test_history_merges_memory_and_disk_newest_first_and_limits_results(self):
        job=self.completed_job()
        for index in range(55):
            identifier=f'{index:032x}'
            self.server.app.save({**job,'job_id':identifier,'created_at':f'2026-02-{index//24+1:02d}T{index%24:02d}:00:00+00:00'})
        items=json.loads(self.request('/api/jobs'))['jobs']
        self.assertEqual(len(items),50)
        self.assertEqual(len({item['job_id'] for item in items}),50)
        self.assertEqual(items[0]['job_id'],job['job_id'])
        self.assertEqual(items[1]['job_id'],f'{54:032x}')

    def test_ask_uses_saved_snapshot_and_rejects_mismatched_or_incomplete_jobs(self):
        job=self.completed_job()
        self.restart_app()
        def answer(service,issue,question,forecast=None):
            return {'answer':str(forecast['rows'][0]['power_normalized']),
                    'usage':{'input_tokens':1,'output_tokens':1},'agent_model':'test','tools':[]}
        with patch('windops.server.questions.answer_question',side_effect=answer):
            response=json.loads(self.request('/api/ask',{'issue_date':'2026-01-31','question':'Что ожидается?','job_id':job['job_id']}))
        self.assertEqual(response['answer'],'0.1')
        for body,expected in (({'issue_date':'2026-02-01','question':'Ветер?','job_id':job['job_id']},400),
                              ({'issue_date':'2026-01-31','question':'Ветер?','job_id':'z'*32},404)):
            with self.subTest(body=body),self.assertRaises(HTTPError) as caught:
                self.request('/api/ask',body)
            self.assertEqual(caught.exception.code,expected)
        job.update(status='failed',result=None,error='failed')
        self.server.app.save(job)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?','job_id':job['job_id']})
        self.assertEqual(caught.exception.code,409)

    def test_ask_validates_key_size_fields_and_question_without_api_calls(self):
        for extra in ({'question':''},{'question':'   '},{'question':'a'*1001},{'question':5},
                      {'question':'Ветер?','mode':'openai'},{'question':'Ветер?','job_id':None}):
            with self.subTest(extra=extra),self.assertRaises(HTTPError) as caught:
                self.request('/api/ask',{'issue_date':'2026-01-31',**extra})
            self.assertEqual(caught.exception.code,400)
        with patch('windops.server.load_settings',return_value={'OPENAI_API_KEY':'','OPENAI_MODEL':'test'}):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?'})
        self.assertEqual(caught.exception.code,400)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/ask',{'issue_date':'2026-01-31','question':'a'*9000})
        self.assertEqual(caught.exception.code,400)

    def test_ask_lock_rejects_concurrent_requests_and_releases_after_failure(self):
        entered,release=threading.Event(),threading.Event()
        errors=[]
        def answer(*args,**kwargs):
            entered.set()
            release.wait(3)
            raise RuntimeError('Controlled test failure')
        def first_request():
            try:self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?'})
            except HTTPError as error:errors.append(error.code)
        with patch('windops.server.questions.answer_question',side_effect=answer):
            thread=threading.Thread(target=first_request)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(HTTPError) as caught:
                    self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?'})
                self.assertEqual(caught.exception.code,409)
            finally:
                release.set()
                thread.join()
        self.assertEqual(errors,[409])
        with patch('windops.server.questions.answer_question',return_value={'answer':'Готово','usage':{},'agent_model':'test','tools':[]}):
            self.assertEqual(json.loads(self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?'}))['answer'],'Готово')

    def test_ask_rejects_cross_origin_and_untrusted_host(self):
        for headers in ({'Origin':'https://evil.example'},{'Host':'evil.example'}):
            with self.subTest(headers=headers),self.assertRaises(HTTPError) as caught:
                self.request('/api/ask',{'issue_date':'2026-01-31','question':'Ветер?'},headers)
            self.assertEqual(caught.exception.code,403)

    def test_validation_route_returns_selected_turbine_horizon(self):
        directory=self.root/'outputs/baseline'
        directory.mkdir(parents=True)
        (directory/'predictions.csv').write_text(
            'model,score_eligible,target_hour_complete,utc_offset_hours,turbine_id,actual_power_normalized,predicted_power_normalized,lead_hours,valid_time_utc,forecast_origin_utc\n'
            'empirical_curve,True,True,5,2,0.2,0.5,25,2026-01-01T19:00:00Z,2025-12-31T18:00:00Z\n')
        result=json.loads(self.request('/api/validation?turbine_id=2&horizon=25-48'))
        self.assertEqual(result['turbine_id'],2)
        self.assertEqual(result['horizon'],'25-48')
        self.assertEqual(result['metrics']['count'],1)
        self.assertAlmostEqual(result['metrics']['mae'],.3)
        for path in ('/api/validation?turbine_id=3&horizon=1-24',
                     '/api/validation?turbine_id=1&horizon=1-48',
                     '/api/validation?turbine_id=1&horizon=1-24&extra=true',
                     '/api/validation?turbine_id=1&turbine_id=2&horizon=1-24'):
            with self.subTest(path=path),self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertEqual(caught.exception.code,400)

    def test_comparison_route_joins_forecasts_for_the_same_hours(self):
        original=self.server.app.service.get_forecast
        def forecast(issue):
            result=original(issue)
            result.update(model={'id':'empirical_curve'},source={'provider':'NOAA GFS'})
            for row in result['rows']:row['wind_speed_ms']=row['wind_speed_100m_ms']
            return result
        with patch.object(self.server.app.service,'get_forecast',side_effect=forecast):
            result=json.loads(self.request('/api/comparison?issue_date=2026-02-01'))
        self.assertEqual(len(result['rows']),48)
        self.assertEqual(result['older']['issue_date'],'2026-01-31')
        self.assertEqual(result['newer']['issue_date'],'2026-02-01')
        self.assertEqual(result['rows'][0]['old_lead_hours'],25)
        self.assertEqual(result['rows'][0]['new_lead_hours'],1)

    def test_forecast_events_do_not_mutate_the_service_snapshot(self):
        forecast=self.server.app.service.get_forecast('2026-01-31')
        with patch.object(self.server.app.service,'get_forecast',return_value=forecast):
            result=json.loads(self.request('/api/forecast?issue_date=2026-01-31'))
        self.assertEqual([event['kind'] for event in result['events']],['peak','peak'])
        self.assertNotIn('events',forecast)

    def test_24_hour_events_use_its_own_peak_and_exclude_the_next_hour_drop(self):
        forecast=self.server.app.service.get_forecast('2026-01-31')
        for row in forecast['rows']:
            if row['turbine_id']==1:
                lead=row['lead_hours']
                row['power_normalized']=(.2 if lead<24 else .7 if lead==24 else .1+.8*(lead-25)/23)
        with patch.object(self.server.app.service,'get_forecast',return_value=forecast):
            result=json.loads(self.request('/api/forecast?issue_date=2026-01-31'))
        full={event['kind']:event for event in result['events'] if event['turbine_id']==1}
        first_day={event['kind']:event for event in result.get('events_24h',[]) if event['turbine_id']==1}
        self.assertIn('peak',first_day)
        self.assertEqual(full['peak']['start_hour'],47)
        self.assertEqual(first_day['peak']['start_hour'],23)
        self.assertEqual(full['drop']['end_hour'],24)
        self.assertNotIn('drop',first_day)
        self.assertNotIn('events_24h',forecast)

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

    def test_landing_and_dashboard_have_distinct_routes(self):
        for path in ('/','/index.html'):
            with self.subTest(path=path),urlopen(self.url+path,timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(),'text/html')
                self.assertEqual(response.read(),b'<h1>WindOps overview</h1>')
        for path in ('/app','/app/'):
            with self.subTest(path=path),urlopen(self.url+path,timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(),'text/html')
                self.assertEqual(response.read(),b'<h1>Forecast dashboard</h1>')

    def test_landing_and_dashboard_assets_have_explicit_routes_and_mime_types(self):
        for name,mime in (('landing.css','text/css'),('landing.js','application/javascript'),
                          ('style.css','text/css'),('app.js','application/javascript')):
            with self.subTest(name=name),urlopen(self.url+'/'+name,timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(),mime)
                self.assertEqual(response.read(),(self.root/'web'/name).read_bytes())

    def test_static_server_cannot_serve_dotenv_or_parent_paths(self):
        (self.root/'web/private.txt').write_text('not a public asset')
        for path in ('/.env','/../.env','/%2e%2e/.env','/api/jobs/../../.env',
                     '/private.txt','/web/index.html','/app/index.html','/app/style.css'):
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
