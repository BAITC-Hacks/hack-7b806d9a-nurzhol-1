"""Local-only demo API. All forecasts and tool traces are real application outputs."""
import csv
from copy import deepcopy
import hashlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from windops.agent import AgentRunner, load_settings
from windops import analytics, questions
from windops.forecast import ForecastService, ISSUE_DATES


class DemoApp:
    def __init__(self, root, service=None, *, inline=False, persist_jobs=True):
        self.root = Path(root).resolve()
        self.service = service or ForecastService(self.root)
        self.inline = inline
        self.persist_jobs = persist_jobs
        self.jobs = {}
        self.lock = threading.RLock()
        self.ask_lock = threading.Lock()

    def february_ready(self):
        return self.february_export() is not None

    def february_export(self):
        directory = self.root / 'outputs/february'
        if not (directory / 'forecast_day_ahead_february.csv').is_file():
            return None
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
            version = getattr(self.service, 'model', {}).get('version')
            if not version or manifest['model']['version'] != version:
                return None
            fingerprints = manifest['issue_fingerprints']
            if set(fingerprints) != set(ISSUE_DATES):
                return None
            csv_path = directory / 'forecast_day_ahead_february.csv'
            expected = manifest['files']['day_ahead_csv']
            payload = csv_path.read_bytes()
            if (expected['name'] != csv_path.name
                    or hashlib.sha256(payload).hexdigest() != expected['sha256']):
                return None
            if all(self.service.get_weather(issue)['source_fingerprint'] == fingerprints[issue]
                   for issue in ISSUE_DATES):
                return payload
            return None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def status(self):
        dates = self.service.list_issues()
        settings = load_settings(self.root)
        model = dict(getattr(self.service, 'model', {'id': 'empirical_curve', 'label': 'Эмпирическая кривая'}))
        selected_ml = model['id'] != 'empirical_curve'
        metrics = []
        path = self.root / ('outputs/gfs_ml/january_descriptive/metrics.csv' if selected_ml
                            else 'outputs/baseline/metrics.csv')
        if path.exists():
            with path.open() as stream:
                for row in csv.DictReader(stream):
                    candidate = row.get('candidate') if selected_ml else row.get('model')
                    if (row.get('utc_offset_hours', '5') == '5' and candidate == model['id']
                            and row['horizon'] == '1-48'):
                        metrics.append({'turbine_id':int(row['turbine_id']),
                                        'mae':float(row['mae']), 'rmse':float(row['rmse'])})
        return {'ready':bool(dates),'available_issues':dates,
                'default_issue_date':'2026-01-31' if '2026-01-31' in dates else (dates[0] if dates else None),
                'timezone':'UTC+5','openai_configured':bool(settings['OPENAI_API_KEY']),
                'model':model,
                'agent_model':settings['OPENAI_MODEL'], 'baseline_metrics':metrics,
                'evaluation': {'earlier_training_version': selected_ml,
                               'label': 'Январь · версия до января' if selected_ml else 'Проверка на январе',
                               'description': ('Ошибка версии, обученной до января. Рабочая версия дообучена на доступных данных января. Фактических данных февраля нет.'
                                               if selected_ml else 'Историческая ошибка модели на горизонте 1–48 ч. Фактические значения февраля отсутствуют.')},
                'data_status':{'february_ready':self.february_ready(),'available_issue_count':len(dates),
                               'expected_issue_count':29,'message':f'Погодные выпуски: {len(dates)} из 29'}}

    def save(self, job):
        with self.lock:
            if not valid_job_id(job.get('job_id')):
                raise ValueError('Неверный идентификатор расчёта.')
            if not self.persist_jobs:
                return
            directory = self.root/'outputs/demo/jobs'
            directory.mkdir(parents=True,exist_ok=True)
            target = directory/(job['job_id']+'.json')
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(job,ensure_ascii=False,allow_nan=False,indent=2))
            temporary.replace(target)

    def get_job(self, identifier):
        if not valid_job_id(identifier):
            return None
        with self.lock:
            live = identifier in self.jobs
            if not live and not self.persist_jobs:
                return None
            path = self.root/'outputs/demo/jobs'/f'{identifier}.json'
            try:
                job = deepcopy(self.jobs[identifier]) if live else json.loads(path.read_text())
                if (not isinstance(job,dict) or job.get('job_id') != identifier
                        or job.get('status') not in ('running','complete','failed','interrupted')
                        or job.get('mode') not in ('deterministic','openai')
                        or not isinstance(job.get('events'),list)):
                    return None
                parse_issue(job.get('issue_date'))
                fallback = (datetime.now(timezone.utc).isoformat() if live else
                            datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat())
                times = [utc_timestamp(event.get('time')) for event in job['events'] if isinstance(event,dict)]
                times = sorted(value for value in times if value)
                job['created_at'] = utc_timestamp(job.get('created_at')) or (times[0] if times else fallback)
                if job['status']=='running' and not live:
                    job.update(status='interrupted',error='Расчёт прерван перезапуском сервера. Запустите новый расчёт.')
                job['finished_at'] = (None if job['status']=='running' else
                                      utc_timestamp(job.get('finished_at')) or (times[-1] if times else fallback))
                job['refresh'] = job.get('refresh') if isinstance(job.get('refresh'),bool) else None
                job.setdefault('agent_model',None)
                job.setdefault('error',None)
                result = job.get('result')
                if job['status']=='complete':
                    if not isinstance(result,dict) or not isinstance(result.get('rows'),list) or not result['rows']:
                        return None
                    job['result'] = with_forecast_events(result)
                return job
            except (OSError,ValueError,KeyError,TypeError,OverflowError):
                return None

    def list_jobs(self):
        with self.lock:
            directory = self.root/'outputs/demo/jobs'
            identifiers = set(self.jobs)
            if self.persist_jobs:
                identifiers |= {path.stem for path in directory.glob('*.json')}
            jobs = [job for identifier in identifiers if (job := self.get_job(identifier)) is not None]
        jobs.sort(key=lambda job:(job['created_at'],job['job_id']),reverse=True)
        fields = ('job_id','status','issue_date','mode','created_at','finished_at','refresh','agent_model','error')
        return [{**{field:job.get(field) for field in fields},
                 'has_result':job['status']=='complete' and isinstance(job.get('result'),dict)}
                for job in jobs[:50]]

    def start(self, issue_date, mode, refresh):
        with self.lock:
            if any(job['status']=='running' for job in self.jobs.values()):
                raise RuntimeError('Расчёт уже выполняется. Дождитесь его завершения.')
            while len(self.jobs)>=50:
                self.jobs.pop(next(iter(self.jobs)))
            identifier = uuid.uuid4().hex
            job = {'job_id':identifier,'status':'running','events':[], 'result':None,
                   'explanation':'','error':None,'mode':mode,'issue_date':issue_date,'usage':None,
                   'created_at':datetime.now(timezone.utc).isoformat(),'finished_at':None,'refresh':refresh}
            self.save(job)
            self.jobs[identifier] = job

        def event(value):
            with self.lock:
                job['events'].append(deepcopy(value))
                self.save(job)

        def work():
            try:
                output = AgentRunner(self.service,event).run(issue_date,mode,refresh)
                result = with_forecast_events(deepcopy(output['forecast']))
                with self.lock:
                    job.update(status='complete',result=result,explanation=output['explanation'],
                               usage=output['usage'],agent_model=output.get('agent_model'),
                               finished_at=datetime.now(timezone.utc).isoformat())
                    self.save(job)
            except Exception as error:
                with self.lock:
                    job.update(status='failed',error=str(error),finished_at=datetime.now(timezone.utc).isoformat())
                    self.save(job)
        try:
            if self.inline:
                work()
            else:
                threading.Thread(target=work,name='forecast-'+identifier[:8],daemon=True).start()
        except Exception as error:
            with self.lock:
                job.update(status='failed',error=str(error),finished_at=datetime.now(timezone.utc).isoformat())
                self.save(job)
            raise
        return identifier


def with_forecast_events(forecast):
    result = dict(forecast)
    if 'events' not in result:
        result['events'] = analytics.forecast_events(forecast)
    if 'events_24h' not in result:
        result['events_24h'] = analytics.forecast_events({
            **forecast,'rows':[row for row in forecast['rows'] if row['lead_hours']<=24]})
    return result


def valid_job_id(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{32}',value) is not None


def utc_timestamp(value):
    if not isinstance(value,str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def parse_issue(value):
    if not isinstance(value,str):
        raise ValueError('Укажите дату выпуска.')
    try:
        date = datetime.strptime(value,'%Y-%m-%d').date()
    except ValueError:
        raise ValueError('Дата выпуска должна иметь формат YYYY-MM-DD.') from None
    if date.isoformat()!=value or not '2026-01-31'<=value<='2026-02-28':
        raise ValueError('Выберите дату выпуска с 31 января по 28 февраля 2026.')
    return value


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Request paths and credentials are never printed by the demo server.
        pass

    def send_bytes(self, value, content_type, code=200, filename=None):
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(value)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(value)

    def send_json(self,value,code=200):
        self.send_bytes(json.dumps(value,ensure_ascii=False,allow_nan=False).encode(),'application/json; charset=utf-8',code)

    def send_forecast_csv(self, forecast, filename):
        stream = io.StringIO(newline='')
        rows = forecast['rows']
        if not rows:
            raise RuntimeError('В расчёте нет строк для экспорта.')
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        self.send_bytes(stream.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8',filename=filename)

    def allowed_host(self):
        return self.headers.get('Host','') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def allowed_origin(self):
        origin = self.headers.get('Origin')
        return not origin or origin in (f'http://127.0.0.1:{self.server.server_port}',
                                        f'http://localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed_host():
            self.send_json({'error':'Этот сервер доступен только через localhost.'},403)
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query,keep_blank_values=True)
        app = self.server.app
        try:
            if url.path.startswith('/api/'):
                fields = ({'issue_date'} if url.path in ('/api/forecast','/api/download','/api/comparison') else
                          {'turbine_id','horizon'} if url.path=='/api/validation' else set())
                if set(query).difference(fields) or any(len(values)!=1 for values in query.values()):
                    raise ValueError('Неверные параметры запроса.')
            if url.path == '/api/status':
                self.send_json(app.status())
            elif url.path in ('/api/forecast','/api/download'):
                issue = parse_issue(query.get('issue_date',[None])[0])
                forecast = app.service.get_forecast(issue)
                if url.path == '/api/forecast':
                    self.send_json(with_forecast_events(forecast))
                else:
                    self.send_forecast_csv(forecast,f'windops-{issue}-48h.csv')
            elif url.path == '/api/comparison':
                issue = parse_issue(query.get('issue_date',[None])[0])
                self.send_json(analytics.compare_forecasts(app.service,issue))
            elif url.path == '/api/validation':
                turbine, horizon = query.get('turbine_id',[None])[0],query.get('horizon',[None])[0]
                if turbine not in ('1','2') or horizon not in ('1-24','25-48'):
                    raise ValueError('Выберите турбину 1 или 2 и горизонт 1–24 или 25–48 часов.')
                self.send_json(analytics.january_validation(app.root,int(turbine),horizon))
            elif url.path == '/api/download-february':
                payload = app.february_export()
                if payload is None:
                    self.send_json({'error':'Пересоздайте февральский экспорт для текущей версии модели.'},409)
                else:
                    self.send_bytes(payload,'text/csv; charset=utf-8',filename='windops-february-2026.csv')
            elif url.path == '/api/jobs':
                self.send_json({'jobs':app.list_jobs()})
            elif url.path.startswith('/api/jobs/'):
                parts = url.path.removeprefix('/api/jobs/').split('/')
                download = len(parts)==2 and parts[1]=='download'
                job = app.get_job(parts[0]) if len(parts)==1 or download else None
                if job is None:
                    self.send_json({'error':'Расчёт не найден.'},404)
                elif download:
                    if job['status']!='complete' or not job.get('result'):
                        raise RuntimeError('Этот расчёт не завершён; экспорт недоступен.')
                    self.send_forecast_csv(job['result'],f'windops-{job["issue_date"]}-{job["job_id"][:8]}-48h.csv')
                else:
                    self.send_json(job)
            else:
                files = {'/':('landing.html','text/html; charset=utf-8'),
                         '/index.html':('landing.html','text/html; charset=utf-8'),
                         '/app':('index.html','text/html; charset=utf-8'),
                         '/app/':('index.html','text/html; charset=utf-8'),
                         '/landing.js':('landing.js','application/javascript; charset=utf-8'),
                         '/landing.css':('landing.css','text/css; charset=utf-8'),
                         '/app.js':('app.js','application/javascript; charset=utf-8'),
                         '/style.css':('style.css','text/css; charset=utf-8')}
                if url.path not in files:
                    self.send_json({'error':'Не найдено.'},404)
                    return
                filename, mime = files[url.path]
                self.send_bytes((app.root/'web'/filename).read_bytes(),mime)
        except (ValueError,KeyError) as error:
            self.send_json({'error':str(error)},400)
        except (FileNotFoundError,RuntimeError) as error:
            self.send_json({'error':str(error)},409)
        except Exception:
            self.send_json({'error':'Не удалось выполнить запрос. Проверьте локальные данные и повторите.'},500)

    def do_POST(self):
        if not self.allowed_host():
            self.send_json({'error':'Этот сервер доступен только через localhost.'},403)
            return
        if not self.allowed_origin():
            self.send_json({'error':'Запрос с другого сайта запрещён.'},403)
            return
        path = urlsplit(self.path).path
        if path not in ('/api/run','/api/ask'):
            self.send_json({'error':'Не найдено.'},404)
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:
                raise ValueError('Неверный размер запроса.')
            data = json.loads(self.rfile.read(length))
            fields = ({'issue_date','mode','refresh'} if path=='/api/run' else
                      {'issue_date','question','job_id','source_fingerprint','model_version'})
            if not isinstance(data,dict) or set(data).difference(fields):
                raise ValueError('Неверные параметры запроса.')
            issue = parse_issue(data.get('issue_date'))
            app = self.server.app
            if path=='/api/ask':
                question = data.get('question')
                if not isinstance(question,str) or not question.strip() or len(question)>1000:
                    raise ValueError('Введите вопрос длиной от 1 до 1000 символов.')
                forecast = None
                if 'source_fingerprint' in data or 'model_version' in data:
                    if ('job_id' in data or not isinstance(data.get('source_fingerprint'),str)
                            or not isinstance(data.get('model_version'),str)):
                        raise ValueError('Неверные параметры сохранённого прогноза.')
                    forecast = app.service.get_forecast(issue)
                    if (forecast.get('source',{}).get('fingerprint') != data['source_fingerprint']
                            or forecast.get('model',{}).get('version') != data['model_version']):
                        raise RuntimeError('Архив или модель изменились. Откройте текущий прогноз перед вопросом агенту.')
                if 'job_id' in data:
                    if not isinstance(data['job_id'],str):
                        raise ValueError('Неверный идентификатор расчёта.')
                    job = app.get_job(data['job_id'])
                    if job is None:
                        self.send_json({'error':'Расчёт не найден.'},404)
                        return
                    if job['issue_date']!=issue:
                        raise ValueError('Дата вопроса не совпадает с сохранённым расчётом.')
                    if job['status']!='complete' or not job.get('result'):
                        raise RuntimeError('Выберите завершённый расчёт для вопроса.')
                    forecast = job['result']
                if not load_settings(app.root)['OPENAI_API_KEY']:
                    raise ValueError('Добавьте OPENAI_API_KEY в .env на сервере и обновите страницу.')
                if not app.ask_lock.acquire(blocking=False):
                    raise RuntimeError('Ответ на вопрос уже формируется. Дождитесь его завершения.')
                try:
                    response = questions.answer_question(app.service,issue,question.strip(),forecast=forecast)
                finally:
                    app.ask_lock.release()
                self.send_json(response)
                return
            mode, refresh = data.get('mode','deterministic'),data.get('refresh',False)
            if mode not in ('deterministic','openai') or not isinstance(refresh,bool):
                raise ValueError('Неверный режим или параметр обновления.')
            if mode=='openai' and not load_settings(self.server.app.root)['OPENAI_API_KEY']:
                raise ValueError('Добавьте OPENAI_API_KEY в .env на сервере и обновите страницу.')
            identifier = self.server.app.start(issue,mode,refresh)
            if app.inline:
                self.send_json({'job_id':identifier,'job':app.get_job(identifier)})
            else:
                self.send_json({'job_id':identifier},202)
        except (ValueError,TypeError,json.JSONDecodeError) as error:
            self.send_json({'error':str(error)},400)
        except RuntimeError as error:
            self.send_json({'error':str(error)},409)
        except Exception:
            self.send_json({'error':'Не удалось выполнить запрос. Проверьте локальные данные и повторите.'},500)


def create_server(root,port=8765,service=None):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    server.app = DemoApp(root,service)
    return server
