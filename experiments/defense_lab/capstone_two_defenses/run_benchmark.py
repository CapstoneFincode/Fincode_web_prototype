"""메인 연결 변수를 제거하고 격리 평가 러너를 실행한다."""
import os
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parent
allowed=('PATH','LANG','LC_ALL','HTTPS_PROXY','https_proxy','SSL_CERT_FILE','SSL_CERT_DIR','LAB_BAZE_API_KEY')
env={k:os.environ[k] for k in allowed if k in os.environ}
env.update({'PYTHONPATH':str(root/'project_copy'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','TMPDIR':str(root/'tmp')})
raise SystemExit(subprocess.call([str(root/'.venv/bin/python'),'-B',str(root/'benchmark_worker.py'),*sys.argv[1:]],cwd=root,env=env))
