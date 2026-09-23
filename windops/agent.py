"""Bounded forecast tools, deterministic workflow and real Responses orchestration."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from scripts.evaluate_baseline import validate_weather


class AgentError(RuntimeError):
    pass


def load_settings(root):
    values = {'OPENAI_API_KEY':'', 'OPENAI_MODEL':'gpt-5.4-mini'}
    path = Path(root) / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:]
            key, sep, value = line.partition('=')
            if sep and key.strip() in values:
                values[key.strip()] = value.strip().strip('\"\'')
    for key in values:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class OpenAIClient:
    def __init__(self, api_key, model):
        if not api_key:
            raise AgentError('Добавьте OPENAI_API_KEY в .env или окружение сервера и повторите расчёт.')
        if any(ord(char)<33 or ord(char)>126 for char in api_key):
            raise AgentError('Неверный формат OPENAI_API_KEY: удалите пробелы и переносы строк.')
        self._api_key = api_key
        self.model = model

    def create(self, *, inputs, tools, instructions):
        body = {'model':self.model, 'input':inputs, 'tools':tools,
                'instructions':instructions, 'max_output_tokens':1200,
                'parallel_tool_calls':False, 'store':False}
        try:
            request = Request('https://api.openai.com/v1/responses',
                              data=json.dumps(body).encode(), method='POST',
                              headers={'Authorization':'Bearer '+self._api_key,
                                       'Content-Type':'application/json'})
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
        except HTTPError as error:
            hints = {401:'Проверьте API-ключ.', 403:'Нет доступа к выбранной модели.',
                     404:'Проверьте OPENAI_MODEL.', 429:'Проверьте API-баланс и лимиты.'}
            raise AgentError(f'OpenAI HTTP {error.code}. {hints.get(error.code,"Повторите запрос позже.")}') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise AgentError('Не удалось получить ответ OpenAI. Проверьте соединение и повторите расчёт.') from None
        if result.get('error') or result.get('status') in ('failed','cancelled','incomplete'):
            raise AgentError('OpenAI не завершил запрос. Повторите расчёт.')
        return result


TOOL_DESCRIPTIONS = {
    'get_weather':'Получить архивный прогноз NOAA для выбранной даты выпуска. Начните с этого инструмента.',
    'validate_weather':'Проверить полноту 48 часов и публикацию погоды до выпуска. После get_weather.',
    'predict_power':'Рассчитать мощность обеих турбин замороженной моделью. Только после validate_weather.',
    'analyze_forecast':'Вычислить средние, диапазоны и скачки мощности для пояснения. После predict_power.',
}
TOOLS = [{'type':'function','name':name,'description':description,
          'parameters':{'type':'object','properties':{},'required':[], 'additionalProperties':False},
          'strict':True} for name, description in TOOL_DESCRIPTIONS.items()]


def analyze(forecast):
    rows = forecast['rows']
    turbines = []
    for turbine in sorted({r['turbine_id'] for r in rows}):
        group = sorted((r for r in rows if r['turbine_id']==turbine), key=lambda r:r['lead_hours'])
        values = [r['power_normalized'] for r in group]
        changes = [(abs(b['power_normalized']-a['power_normalized']), b)
                   for a,b in zip(group,group[1:])]
        change, target = max(changes, key=lambda x:x[0])
        turbines.append({'turbine_id':turbine, 'mean_power':sum(values)/len(values),
                         'min_power':min(values), 'max_power':max(values),
                         'mean_first_24h':sum(values[:24])/24, 'mean_second_24h':sum(values[24:])/24,
                         'largest_hourly_change':change, 'largest_change_time_local':target['valid_time_local']})
    return {'units':'normalized power 0..1', 'timezone':'UTC+5', 'turbines':turbines,
            'actual_february_available':False, 'uncertainty_interval_available':False}


def describe(analysis):
    def number(value):
        return f'{value:.3f}'.replace('.',',')
    sentences = []
    for t in analysis['turbines']:
        sentences.append(f'Турбина {t["turbine_id"]}: средняя мощность за 48 часов {number(t["mean_power"])}, '
                         f'диапазон {number(t["min_power"])}–{number(t["max_power"])}.')
    return ' '.join(sentences)+' Мощность нормализована в шкале 0–1. Это прогноз; фактических данных февраля нет.'


class AgentRunner:
    def __init__(self, service, emit=None, client=None):
        self.service = service
        self.emit = emit or (lambda event:None)
        self.client = client

    def event(self, tool, status, message):
        self.emit({'time':datetime.now(timezone.utc).isoformat(), 'tool':tool,
                   'status':status, 'message':message})

    def execute(self, name, arguments):
        if name not in TOOL_DESCRIPTIONS or arguments != {}:
            raise AgentError('Недопустимый инструмент или аргументы: дата и модель зафиксированы приложением.')
        self.event(name,'running', TOOL_DESCRIPTIONS[name])
        try:
            if name == 'get_weather':
                self.weather = None
                self.validated = False
                self.forecast = self.analysis = None
                self.weather = self.service.get_weather(self.issue_date, refresh=self.refresh)
                result = {k:v for k,v in self.weather.items() if k != 'rows'}
                result['row_count'] = len(self.weather['rows'])
                detail = 'Погода получена: 48 часов для двух турбин.'
            elif name == 'validate_weather':
                self.validated = False
                self.forecast = self.analysis = None
                if self.weather is None:
                    raise AgentError('Сначала получите погоду.')
                w = validate_weather(pd.DataFrame(self.weather['rows']))
                expected = pd.Timestamp(f'{self.issue_date}T18:00:00Z')
                if not w.forecast_origin_utc.eq(expected).all() or set(w.turbine_id) != {'1','2'}:
                    raise AgentError('Дата или состав турбин не совпадает с запросом.')
                if len(w)!=96 or not w.groupby('turbine_id').lead_hours.apply(lambda x:sorted(x)==list(range(1,49))).all():
                    raise AgentError('Нужны все 48 часов для каждой турбины.')
                self.validated = True
                result = {'valid':True,'rows':96,'published_before_issue':True}
                detail = '96 строк проверены; погода опубликована до выпуска.'
            elif name == 'predict_power':
                self.forecast = self.analysis = None
                if not self.validated:
                    raise AgentError('Сначала проверьте доступность и полноту погоды.')
                self.forecast = self.service.predict(self.issue_date,self.weather)
                result = {'rows':len(self.forecast['rows']), 'model':self.forecast.get('model'),
                          'cached':self.forecast.get('cached',False)}
                detail = 'Рассчитаны 48 часов мощности для обеих турбин.'
            else:
                self.analysis = None
                if self.forecast is None:
                    raise AgentError('Сначала рассчитайте мощность.')
                self.analysis = analyze(self.forecast)
                result = self.analysis
                detail = 'Вычислены диапазоны, средние и изменения мощности.'
        except Exception as error:
            self.event(name,'failed',str(error))
            if isinstance(error, AgentError):
                raise
            raise AgentError(str(error)) from error
        self.event(name,'complete',detail)
        return result

    def run(self, issue_date, mode='deterministic', refresh=False):
        try:
            date = datetime.strptime(issue_date,'%Y-%m-%d').date()
        except (ValueError,TypeError):
            raise AgentError('Дата выпуска должна иметь формат YYYY-MM-DD.') from None
        if date.isoformat()!=issue_date or not '2026-01-31'<=issue_date<='2026-02-28':
            raise AgentError('Выберите дату выпуска с 31 января по 28 февраля 2026.')
        if mode not in ('deterministic','openai'):
            raise AgentError('Неизвестный режим расчёта.')
        self.issue_date, self.refresh = issue_date, refresh
        self.weather = self.forecast = self.analysis = None
        self.validated = False
        usage = {'input_tokens':0,'output_tokens':0}
        if mode == 'deterministic':
            for name in TOOL_DESCRIPTIONS:
                self.execute(name,{})
            return {'forecast':self.forecast,'explanation':describe(self.analysis),
                    'analysis':self.analysis,'mode':mode,'usage':usage}
        client = self.client
        if client is None:
            settings = load_settings(self.service.root)
            client = OpenAIClient(settings['OPENAI_API_KEY'],settings['OPENAI_MODEL'])
        inputs = [{'role':'user','content':f'Сформируй и объясни прогноз двух турбин на 48 часов. Дата выпуска {issue_date}, 23:00 UTC+5.'}]
        instructions = ('Ты оператор прогнозирования ВЭС. Выполни полный цикл инструментами get_weather, validate_weather, '
                        'predict_power, analyze_forecast в этом порядке. При ошибке инструмента исправь порядок или сообщи о проблеме. '
                        'После анализа дай краткое объяснение на русском, до 120 слов. Все числа бери только из инструментов; '
                        'мощность в шкале 0..1, не МВт. Фактических февральских данных и доверительного интервала нет. '
                        'Укажи отличие первых и вторых суток. Не утверждай, что прогноз подтверждён наблюдениями. '
                        'Дата, источник, модель и пути фиксированы приложением, инструменты не принимают аргументы.')
        calls = 0
        for _ in range(8):
            response = client.create(inputs=inputs,tools=TOOLS,instructions=instructions)
            for key in usage:
                usage[key] += int(response.get('usage',{}).get(key,0))
            output = response.get('output',[])
            inputs.extend(output)
            requested = [item for item in output if item.get('type')=='function_call']
            if requested:
                for item in requested:
                    calls += 1
                    if calls>10:
                        raise AgentError('Достигнут лимит вызовов агента. Повторите расчёт.')
                    try:
                        arguments = json.loads(item.get('arguments','{}'))
                        result = self.execute(item.get('name',''),arguments)
                    except (AgentError,json.JSONDecodeError) as error:
                        result = {'error':str(error)}
                    inputs.append({'type':'function_call_output','call_id':item['call_id'],
                                   'output':json.dumps(result,ensure_ascii=False)})
                continue
            text = '\n'.join(part.get('text','') for item in output if item.get('type')=='message'
                             for part in item.get('content',[]) if part.get('type')=='output_text').strip()
            if self.analysis is not None and text:
                self.event('openai','complete','Агент объяснил вычисленный прогноз.')
                return {'forecast':self.forecast,'explanation':text,'analysis':self.analysis,
                        'mode':mode,'usage':usage,'agent_model':client.model}
            inputs.append({'role':'user','content':'Полный цикл ещё не завершён. Вызови недостающие инструменты перед итоговым ответом.'})
        raise AgentError('Агент не завершил полный цикл за 8 шагов. Повторите расчёт.')
