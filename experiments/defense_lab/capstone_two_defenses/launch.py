"""격리 복사본만 실행한다. 메인 DB·키·스토리지 환경을 상속하지 않는다."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--live',action='store_true',help='명시적으로 실제 BAZE 호출 허용')
parser.add_argument('--port',type=int,default=8765)
args=parser.parse_args()
if not 1024<=args.port<=65535:raise SystemExit('port는 1024~65535')
if args.live and not os.environ.get('LAB_BAZE_API_KEY'):raise SystemExit('LAB_BAZE_API_KEY 없음')
allowed=('PATH','LANG','LC_ALL','HTTPS_PROXY','https_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
env={k:os.environ[k] for k in allowed if k in os.environ}
env.update({'PYTHONPATH':str(ROOT/'project_copy'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','TMPDIR':str(ROOT/'tmp'),'LAB_MODE':'live' if args.live else 'mock','LAB_PORT':str(args.port)})
if args.live:env['LAB_BAZE_API_KEY']=os.environ['LAB_BAZE_API_KEY']
python=ROOT/'.venv/bin/python'
raise SystemExit(subprocess.call([str(python),'-B',str(ROOT/'serve.py')],env=env,cwd=ROOT))
