#!/usr/bin/env python3
"""Run the WindOps local demonstration. No API calls are made on startup."""
import argparse
from pathlib import Path

from windops.server import create_server


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=create_server(Path(__file__).resolve().parent,args.port)
    print(f'WindOps: http://127.0.0.1:{server.server_port}',flush=True)
    print('Ctrl+C — остановить. API-ключ читается из .env только на сервере.',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
