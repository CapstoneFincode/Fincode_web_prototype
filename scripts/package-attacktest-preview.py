"""Build an isolated static judge + original data-management review UI."""
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
dashboard=dashboard.replace('<head>','<head>\n<meta name="fincode-preview" content="categories">')
dashboard=dashboard.replace('<title>핀코드 · LLM Security Lab</title>','<title>핀코드 · 공격 시나리오 저장 테스트</title>')
dashboard=dashboard.replace('</head>','<style>.preview-return{position:fixed;right:18px;bottom:18px;z-index:45;background:#265ce9;color:white;padding:11px 16px;border-radius:8px;font-size:12px;box-shadow:0 5px 16px #265ce930}</style></head>')
dashboard=dashboard.replace('</body>','<a class="preview-return" href="./">← 공격 판정 테스트로 돌아가기</a></body>')
(out/'dashboard.html').write_text(dashboard)
for item in (root/'preview/attacktest').iterdir():
    if item.is_file():shutil.copy2(item,out/item.name)
config=json.loads((root/'config/email-baseline.json').read_text())
(out/'baseline.json').write_text(json.dumps({'systemPrompt':config['systemPrompt'],'version':config['version']},ensure_ascii=False))
(out/'provenance.json').write_text(json.dumps({'sourceBranch':'attacktest','sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'mode':'offline-browser-preview','productionDatabase':False,'modelCalls':False}))
print('Packaged isolated attack evaluation preview; no model/DB credentials copied.')
