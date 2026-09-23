"""Local-only demo API. All forecasts and tool traces are real application outputs."""
import csv
import hashlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from windops.agent import AgentRunner, load_settings
from windops.forecast import ForecastService, ISSUE_DATES


class DemoApp:
    def __init__(self, root, service=None):
        self.root = Path(root).resolve()
        self.service = service or ForecastService(self.root)
        self.jobs = {}
        self.lock = threading.RLock()

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
        directory = self.root/'outputs/demo/jobs'
        directory.mkdir(parents=True,exist_ok=True)
        target = directory/(job['job_id']+'.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(job,ensure_ascii=False,allow_nan=False,indent=2))
        temporary.replace(target)

    def start(self, issue_date, mode, refresh):
        with self.lock:
            if any(job['status']=='running' for job in self.jobs.values()):
                raise RuntimeError('Расчёт уже выполняется. Дождитесь его завершения.')
            while len(self.jobs)>=50:
                self.jobs.pop(next(iter(self.jobs)))
            identifier = uuid.uuid4().hex
            job = {'job_id':identifier,'status':'running','events':[], 'result':None,
                   'explanation':'','error':None,'mode':mode,'issue_date':issue_date,'usage':None}
            self.jobs[identifier] = job
            self.save(job)

        def event(value):
            with self.lock:
                job['events'].append(value)
                self.save(job)

        def work():
            try:
                output = AgentRunner(self.service,event).run(issue_date,mode,refresh)
                with self.lock:
                    job.update(status='complete',result=output['forecast'],explanation=output['explanation'],
                               usage=output['usage'],agent_model=output.get('agent_model'))
                    self.save(job)
            except Exception as error:
                with self.lock:
                    job.update(status='failed',error=str(error))
                    self.save(job)
        threading.Thread(target=work,name='forecast-'+identifier[:8],daemon=True).start()
        return identifier


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

    def allowed_host(self):
        return self.headers.get('Host','') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed_host():
            self.send_json({'error':'Этот сервер доступен только через localhost.'},403)
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        app = self.server.app
        try:
            if url.path == '/api/status':
                self.send_json(app.status())
            elif url.path in ('/api/forecast','/api/download'):
                issue = parse_issue(query.get('issue_date',[None])[0])
                forecast = app.service.get_forecast(issue)
                if url.path == '/api/forecast':
                    self.send_json(forecast)
                else:
                    stream = io.StringIO(newline='')
                    rows = forecast['rows']
                    writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                    self.send_bytes(stream.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8',
                                    filename=f'windops-{issue}-48h.csv')
            elif url.path == '/api/download-february':
                payload = app.february_export()
                if payload is None:
                    self.send_json({'error':'Пересоздайте февральский экспорт для текущей версии модели.'},409)
                else:
                    self.send_bytes(payload,'text/csv; charset=utf-8',filename='windops-february-2026.csv')
            elif url.path.startswith('/api/jobs/'):
                identifier = url.path.removeprefix('/api/jobs/')
                with app.lock:
                    job = app.jobs.get(identifier)
                    self.send_json(job if job else {'error':'Расчёт не найден.'},200 if job else 404)
            else:
                files = {'/':('index.html','text/html; charset=utf-8'),
                         '/index.html':('index.html','text/html; charset=utf-8'),
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
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
            self.send_json({'error':'Запрос с другого сайта запрещён.'},403)
            return
        if urlsplit(self.path).path != '/api/run':
            self.send_json({'error':'Не найдено.'},404)
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:
                raise ValueError('Неверный размер запроса.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict) or set(data).difference({'issue_date','mode','refresh'}):
                raise ValueError('Неверные параметры запроса.')
            issue = parse_issue(data.get('issue_date'))
            mode, refresh = data.get('mode','deterministic'),data.get('refresh',False)
            if mode not in ('deterministic','openai') or not isinstance(refresh,bool):
                raise ValueError('Неверный режим или параметр обновления.')
            if mode=='openai' and not load_settings(self.server.app.root)['OPENAI_API_KEY']:
                raise ValueError('Добавьте OPENAI_API_KEY в .env на сервере и обновите страницу.')
            identifier = self.server.app.start(issue,mode,refresh)
            self.send_json({'job_id':identifier},202)
        except (ValueError,TypeError,json.JSONDecodeError) as error:
            self.send_json({'error':str(error)},400)
        except RuntimeError as error:
            self.send_json({'error':str(error)},409)


def create_server(root,port=8765,service=None):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    server.app = DemoApp(root,service)
    return server
