"""Vercel entrypoint using the same forecast API as the local demo."""
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace

from windops.forecast import ForecastService
from windops.server import DemoApp, Handler


ROOT = Path(__file__).resolve().parents[1]


class CloudForecastService(ForecastService):
    def __init__(self, root):
        super().__init__(root)
        self.cache_dir = Path(tempfile.gettempdir()) / 'windops' / 'cache'

    def _refresh_weather(self, issue_date):
        raise ValueError('Обновление NOAA доступно в локальном приложении. На сайте используется проверенный архив.')


class CloudApp(DemoApp):
    def __init__(self, root=ROOT):
        super().__init__(root, CloudForecastService(root), inline=True, persist_jobs=False)

    def status(self):
        return {**super().status(), 'execution_mode': 'inline', 'history_storage': 'browser',
                'weather_refresh_available': False}

    def start(self, issue_date, mode, refresh):
        if refresh:
            self.service._refresh_weather(issue_date)
        return super().start(issue_date, mode, refresh)


class handler(Handler):
    def __init__(self, request, client_address, server):
        # Request-scoped jobs cannot leak between visitors or depend on warm instances.
        super().__init__(request, client_address,
                         SimpleNamespace(app=CloudApp(), server_port=443))

    def allowed_host(self):
        host = self.headers.get('Host', '').lower()
        hosts = {os.environ.get(key, '').lower() for key in
                 ('VERCEL_URL', 'VERCEL_BRANCH_URL', 'VERCEL_PROJECT_PRODUCTION_URL')}
        hosts.update(value.strip().lower() for value in os.environ.get('WINDOPS_PUBLIC_HOSTS', '').split(','))
        return bool(host and re.fullmatch(r'[a-z0-9.-]+(?::\d+)?', host) and host in hosts)

    def allowed_origin(self):
        origin = self.headers.get('Origin')
        return not origin or origin == 'https://' + self.headers.get('Host', '').lower()
