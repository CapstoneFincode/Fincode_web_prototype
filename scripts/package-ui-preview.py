"""Copy the public UI into an isolated, model-free static review site."""
import argparse
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
out = args.output.resolve()
source = root / 'public'
if out == source.resolve() or source.resolve() in out.parents:
    raise SystemExit('Use a separate preview output directory.')
out.mkdir(parents=True, exist_ok=True)
for item in source.iterdir():
    if item.is_file():
        shutil.copy2(item, out / item.name)
index = (out / 'index.html').read_text('utf-8')
index = index.replace('<head>', '<head>\n  <meta name="fincode-preview" content="categories">')
index = index.replace('<title>핀코드 · LLM Security Lab</title>', '<title>핀코드 · 카테고리 UI 검토</title>')
index = index.replace('<script type="module"', '<script>if (!location.hash) location.hash = "experiments";</script>\n  <script type="module"', 1)
(out / 'index.html').write_text(index, 'utf-8')
print('Packaged isolated category preview assets.')
