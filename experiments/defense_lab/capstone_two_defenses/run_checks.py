"""원본의 환경·DB를 연결하지 않는 검사 진입점."""
import os
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parent
allowed=('PATH','LANG','LC_ALL')
env={k:os.environ[k] for k in allowed if k in os.environ}
env.update({'PYTHONPATH':str(root/'project_copy'),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','TMPDIR':str(root/'tmp')})
result=subprocess.run([str(root/'.venv/bin/python'),'-B','-m','unittest','discover','-s',str(root/'lab_tests'),'-v'],cwd=root,env=env,capture_output=True,text=True)
(root/'results/unit_tests.txt').write_text(result.stdout+result.stderr)
print(result.stdout+result.stderr)
raise SystemExit(result.returncode)
