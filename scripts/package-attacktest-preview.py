"""Preview the existing scenario-based experiment flow; keep judge checks secondary."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
out=args.output.resolve()
if out==root or out in root.parents or root/'public'==out or root/'public' in out.parents:
    raise SystemExit('Choose a separate preview output directory.')
out.mkdir(parents=True,exist_ok=True)
for item in (root/'public').iterdir():
    if item.is_file():shutil.copy2(item,out/item.name)
dashboard=(out/'index.html').read_text()
dashboard=dashboard.replace('<head>','<head>\n<meta name="fincode-preview" content="categories">\n<meta name="fincode-workflow-preview" content="true">')
dashboard=dashboard.replace('<title>핀코드 · LLM Security Lab</title>','<title>핀코드 · 기존 공격 실험 흐름 미리보기</title>')
for item in (root/'preview/attacktest').iterdir():
    if item.is_file():shutil.copy2(item,out/('judge.html' if item.name=='index.html' else item.name))
(out/'index.html').write_text(dashboard)
(out/'dashboard.html').write_text(dashboard)
config=json.loads((root/'config/email-baseline.json').read_text())
(out/'baseline.json').write_text(json.dumps({'systemPrompt':config['systemPrompt'],'version':config['version']},ensure_ascii=False))
(out/'provenance.json').write_text(json.dumps({'sourceBranch':'attacktest','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'mode':'offline-browser-preview','productionDatabase':False,'modelCalls':False}))
print('Packaged existing scenario-selection workflow; no model/DB credentials copied.')
