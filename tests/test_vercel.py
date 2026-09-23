"""Serverless contracts: inline completion, stateless snapshots and origin checks."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_server import HTTPService
from windops.server import DemoApp, create_server
from api.index import CloudApp, handler


class InlineJobTests(unittest.TestCase):
    def test_cloud_archive_and_cache_work_without_source_writes(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            app = CloudApp(root)
            app.service.cache_dir = Path(directory) / 'cache'
            status = app.status()
            self.assertEqual(status['execution_mode'], 'inline')
            self.assertEqual(status['history_storage'], 'browser')
            self.assertFalse(status['weather_refresh_available'])
            self.assertEqual(len(status['available_issues']), 29)
            self.assertTrue(status['data_status']['february_ready'])
            forecast = app.service.get_forecast('2026-01-31')
            self.assertEqual(len(forecast['rows']), 96)
            identifier = app.start('2026-01-31', 'deterministic', False)
            self.assertEqual(app.get_job(identifier)['status'], 'complete')
            self.assertIsNone(CloudApp(root).get_job(identifier))
            self.assertEqual(CloudApp(root).list_jobs(), [])
            with patch('windops.server.AgentRunner.run') as run, self.assertRaisesRegex(ValueError, 'локальном'):
                app.start('2026-01-31', 'openai', True)
            run.assert_not_called()

    def test_cloud_only_accepts_its_domains_and_same_origin(self):
        request = handler.__new__(handler)
        with patch.dict('os.environ', {'VERCEL_URL': 'preview.vercel.app',
                                      'VERCEL_PROJECT_PRODUCTION_URL': 'windops.vercel.app'}):
            for host in ('preview.vercel.app', 'windops.vercel.app'):
                request.headers = {'Host': host, 'Origin': 'https://' + host}
                self.assertTrue(request.allowed_host())
                self.assertTrue(request.allowed_origin())
            request.headers = {'Host': 'evil.vercel.app', 'Origin': 'https://evil.vercel.app'}
            self.assertFalse(request.allowed_host())
            request.headers = {'Host': 'windops.vercel.app', 'Origin': 'https://evil.example'}
            self.assertFalse(request.allowed_origin())

    def test_inline_job_finishes_without_starting_background_thread_or_writing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DemoApp(root, HTTPService(root), inline=True, persist_jobs=False)
            with patch('windops.server.threading.Thread.start', side_effect=AssertionError('background')):
                identifier = app.start('2026-01-31', 'deterministic', False)
            job = app.get_job(identifier)
            self.assertEqual(job['status'], 'complete')
            self.assertEqual(len(job['result']['rows']), 96)
            self.assertFalse((root / 'outputs').exists())

    def test_inline_failure_returns_finished_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DemoApp(root, HTTPService(root), inline=True, persist_jobs=False)
            with patch('windops.server.AgentRunner.run', side_effect=RuntimeError('Unavailable')):
                identifier = app.start('2026-01-31', 'deterministic', False)
            self.assertEqual(app.get_job(identifier)['status'], 'failed')
            self.assertEqual(app.get_job(identifier)['error'], 'Unavailable')


class InlineHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = create_server(self.root, port=0, service=HTTPService(self.root))
        self.server.app = DemoApp(self.root, HTTPService(self.root), inline=True, persist_jobs=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, body):
        request = Request(f'http://127.0.0.1:{self.server.server_port}{path}',
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_inline_response_contains_complete_result(self):
        status, response = self.post('/api/run', {'issue_date': '2026-01-31', 'mode': 'deterministic'})
        self.assertEqual(status, 200)
        self.assertEqual(response['job']['status'], 'complete')
        self.assertEqual(response['job_id'], response['job']['job_id'])

    def test_ask_verifies_browser_snapshot_against_archive(self):
        forecast = self.server.app.service.get_forecast('2026-01-31')
        forecast['model'] = {'version': 'frozen-v1'}
        forecast['source'] = {'fingerprint': 'weather-sha'}
        body = {'issue_date': '2026-01-31', 'question': 'Прогноз?',
                'model_version': 'frozen-v1', 'source_fingerprint': 'weather-sha'}
        with patch.object(self.server.app.service, 'get_forecast', return_value=forecast), \
             patch('windops.server.load_settings', return_value={'OPENAI_API_KEY': 'test'}), \
             patch('windops.server.questions.answer_question', return_value={'answer': 'Ответ'}) as answer:
            status, response = self.post('/api/ask', body)
            self.assertEqual(status, 200)
            self.assertEqual(response['answer'], 'Ответ')
            self.assertEqual(answer.call_args.kwargs['forecast'], forecast)
            for change in ({'model_version': 'old'}, {'source_fingerprint': 'old'},
                           {'model_version': None}, {'job_id': 'a' * 32}):
                answer.reset_mock()
                with self.subTest(change=change), self.assertRaises(HTTPError) as caught:
                    self.post('/api/ask', {**body, **change})
                self.assertIn(caught.exception.code, (400, 409))
                caught.exception.close()
                answer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
