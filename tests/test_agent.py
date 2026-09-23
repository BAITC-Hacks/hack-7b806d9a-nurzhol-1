import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from windops.agent import AgentError, AgentRunner, OpenAIClient, load_settings


class ExampleService:
    def __init__(self, root):
        self.root = Path(root)
        self.predictions = 0
        self.late = False

    def get_weather(self, issue_date, refresh=False):
        origin = pd.Timestamp(f'{issue_date} 18:00:00', tz='UTC')
        rows = []
        for turbine in (1, 2):
            for lead in range(1, 49):
                rows.append(dict(turbine_id=turbine, forecast_origin_utc=origin.isoformat(),
                                 run_time_utc=(origin-pd.Timedelta(hours=6)).isoformat(),
                                 valid_time_utc=(origin+pd.Timedelta(hours=lead)).isoformat(),
                                 available_at_utc=(origin+pd.Timedelta(hours=1 if self.late else -2)).isoformat(),
                                 lead_hours=lead, wind_speed_100m_ms=5.0, temperature_2m_c=0.0,
                                 wind_direction_100m_deg=180.0))
        return {'rows': rows, 'source_fingerprint':'weather-a', 'issue_date':issue_date,
                'forecast_origin_utc':origin.isoformat(), 'source':{'provider':'NOAA GFS'}}

    def predict(self, issue_date, weather):
        self.predictions += 1
        rows = []
        for row in weather['rows']:
            rows.append({**row, 'power_normalized':0.1 if row['turbine_id']==1 else 0.3,
                         'valid_time_local':pd.Timestamp(row['valid_time_utc']).tz_convert('Etc/GMT-5').isoformat()})
        return {'issue_date':issue_date, 'rows':rows, 'cached':False}


class ScriptedClient:
    model = 'test-only-client'
    def __init__(self, outputs):
        self.outputs = iter(outputs)
    def create(self, **kwargs):
        return {'output':next(self.outputs), 'usage':{'input_tokens':10,'output_tokens':2}}


def call(name, arguments='{}'):
    return [{'type':'function_call','name':name,'arguments':arguments,'call_id':name}]


def message(text):
    return [{'type':'message','content':[{'type':'output_text','text':text}]}]


class AgentWorkflowTests(unittest.TestCase):
    def test_deterministic_workflow_computes_summary_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ExampleService(directory)
            events=[]
            result=AgentRunner(service, events.append).run('2026-01-31', mode='deterministic')
            self.assertEqual(result['mode'],'deterministic')
            self.assertEqual(len(result['forecast']['rows']),96)
            self.assertIn('0,100',result['explanation'])
            self.assertIn('0,300',result['explanation'])
            self.assertEqual([e['tool'] for e in events if e['status']=='complete'],
                             ['get_weather','validate_weather','predict_power','analyze_forecast'])

    def test_late_weather_never_reaches_power_model(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ExampleService(directory)
            service.late=True
            with self.assertRaises(AgentError):
                AgentRunner(service).run('2026-01-31')
            self.assertEqual(service.predictions,0)

    def test_openai_must_complete_tools_before_its_summary_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            client=ScriptedClient([call('predict_power'),call('get_weather'),call('validate_weather'),
                                   call('predict_power'),call('analyze_forecast'),message('Проверенный прогноз готов.')])
            events=[]
            service=ExampleService(directory)
            result=AgentRunner(service,events.append,client).run('2026-01-31',mode='openai')
            self.assertEqual(service.predictions,1)
            self.assertEqual(result['mode'],'openai')
            self.assertEqual(result['explanation'],'Проверенный прогноз готов.')
            self.assertTrue(any(e['tool']=='predict_power' and e['status']=='failed' for e in events))
            self.assertEqual(result['usage']['input_tokens'],60)

    def test_premature_llm_text_cannot_masquerade_as_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            client=ScriptedClient([message('Everything is done')]*8)
            service=ExampleService(directory)
            with self.assertRaises(AgentError):
                AgentRunner(service,client=client).run('2026-01-31',mode='openai')
            self.assertEqual(service.predictions,0)

    def test_tools_cannot_change_issue_date_or_execute_arbitrary_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            client=ScriptedClient([call('get_weather','{"issue_date":"2030-01-01"}')]*8)
            service=ExampleService(directory)
            with self.assertRaises(AgentError):
                AgentRunner(service,client=client).run('2026-01-31',mode='openai')
            self.assertEqual(service.predictions,0)

    def test_missing_key_has_actionable_error(self):
        with self.assertRaisesRegex(AgentError,'OPENAI_API_KEY'):
            OpenAIClient('', 'gpt-5.4-mini')

    def test_failed_weather_retry_invalidates_previously_completed_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            service=ExampleService(directory)
            original=service.get_weather
            count=0
            def fail_second(*args,**kwargs):
                nonlocal count
                count+=1
                if count>1:raise RuntimeError('source unavailable')
                return original(*args,**kwargs)
            service.get_weather=fail_second
            client=ScriptedClient([call('get_weather'),call('validate_weather'),call('predict_power'),
                                   call('analyze_forecast'),call('get_weather')]+[message('Готово')]*3)
            with self.assertRaises(AgentError):
                AgentRunner(service,client=client).run('2026-01-31',mode='openai')

    def test_malformed_key_and_transport_header_errors_never_expose_secret(self):
        for secret in ('synthetic-secret\n','synthetic-secret\rvalue','synthetic-secret☃'):
            with self.subTest(secret=secret):
                with self.assertRaises(AgentError) as caught:
                    OpenAIClient(secret,'gpt-5.4-mini')
                self.assertNotIn('synthetic-secret',str(caught.exception))
        with patch('windops.agent.urlopen',side_effect=ValueError('Bearer synthetic-secret')):
            with self.assertRaises(AgentError) as caught:
                OpenAIClient('synthetic-secret','gpt-5.4-mini').create(inputs=[],tools=[],instructions='')
        self.assertNotIn('synthetic-secret',str(caught.exception))

    def test_incomplete_openai_response_is_explicitly_rejected(self):
        reply=io.BytesIO(json.dumps({'status':'incomplete','output':message('Обрезанный ответ')}).encode())
        with patch('windops.agent.urlopen',return_value=reply):
            with self.assertRaises(AgentError):
                OpenAIClient('synthetic-secret','gpt-5.4-mini').create(inputs=[],tools=[],instructions='')

    def test_settings_read_only_named_values_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'.env').write_text('OPENAI_API_KEY="file-secret"\nOPENAI_MODEL=gpt-5.4-mini\nOTHER=ignored\n')
            with patch.dict('os.environ',{'OPENAI_API_KEY':'environment-secret'},clear=True):
                settings=load_settings(Path(directory))
            self.assertEqual(settings['OPENAI_API_KEY'],'environment-secret')
            self.assertEqual(settings['OPENAI_MODEL'],'gpt-5.4-mini')
            self.assertNotIn('OTHER',settings)


if __name__=='__main__':
    unittest.main()
