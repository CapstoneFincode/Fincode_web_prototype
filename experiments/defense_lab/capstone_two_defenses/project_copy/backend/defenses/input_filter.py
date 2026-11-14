"""한/영 구문·토큰·인코딩을 검사하는 결정적 입력 필터. 외부 호출 없음."""
import base64
import binascii
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote
import yaml


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
    def handle_data(self,data):
        self.parts.append(data)


def inspect_normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if unicodedata.category(c)!='Cf' and c not in '\u034f\ufe0e\ufe0f')


def inspection_views(text):
    """복호화 결과는 검사에만 쓰고 실행하거나 원본 대신 전달하지 않는다."""
    tag_payload=''.join(chr(ord(c)-0xE0000) for c in text if 0xE0020<=ord(c)<=0xE007E)
    tagged=''.join(chr(ord(c)-0xE0000) if 0xE0020<=ord(c)<=0xE007E else c for c in text)
    decoded=unescape(unquote(tagged))
    parser=_HTMLText()
    parser.feed(decoded)
    candidates=[('tag_payload',inspect_normalize(tag_payload))] if tag_payload else []
    candidates += [('normalized',inspect_normalize(text)),('decoded_markup',inspect_normalize(decoded)),('html_text',inspect_normalize(''.join(parser.parts))),('unicode_tags',inspect_normalize(tagged))]
    for index,match in enumerate(re.finditer(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,4096}={0,2}(?![A-Za-z0-9+/])',decoded)):
        if index>=32:break
        token=match.group()
        try:
            value=base64.b64decode(token+'='*(-len(token)%4),validate=True).decode('utf-8')
        except (ValueError,binascii.Error,UnicodeError):
            continue
        if value and sum(c.isprintable() or c.isspace() for c in value)/len(value)>.9:
            candidates.append(('base64',inspect_normalize(value)))
    for index,match in enumerate(re.finditer(r'(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}){16,1024}(?![0-9a-fA-F])',decoded)):
        if index>=16:break
        try:
            candidates.append(('hex',inspect_normalize(bytes.fromhex(match.group()).decode('utf-8'))))
        except (ValueError,UnicodeError):
            pass
    # 표현이 같은 여러 뷰가 규칙 점수를 반복해서 올리지 않게 한다.
    seen=set();result=[]
    for name,value in candidates:
        if value not in seen:
            seen.add(value);result.append((name,value))
    return result


class InputFilter:
    def __init__(self,rules_path=None):
        path=Path(rules_path) if rules_path else Path(__file__).with_name('rules.yaml')
        self.config=yaml.safe_load(path.read_text('utf-8'))
        self.rules=[{**r,'compiled':re.compile(r['pattern'])} for r in self.config['rules']]
    def check(self,email):
        text=email if isinstance(email,str) else '\n'.join(str(email.get(k,'')) for k in ('subject','sender','body'))
        if not isinstance(text,str):raise TypeError('검사 입력은 문자열이어야 합니다.')
        if len(text)>self.config['max_input_chars']:
            return {'blocked':True,'reason':'input_limit','score':1.0,'matchedRules':[],'version':self.config['version'],'scoreKind':'rule_weight_ratio'}
        views=inspection_views(text);matches=[];weight=0
        for rule in self.rules:
            for view_name,view in views:
                if rule.get('views') and view_name not in rule['views']:continue
                found=rule['compiled'].search(view)
                if found:
                    matches.append({'id':rule['id'],'category':rule['category'],'view':view_name,'span':list(found.span())})
                    weight+=rule['weight'];break
        threshold=self.config['threshold']
        return {'blocked':weight>=threshold,'reason':'pattern_match' if weight>=threshold else 'no_known_pattern','score':min(1.0,weight/threshold),'matchedRules':matches,'version':self.config['version'],'scoreKind':'rule_weight_ratio'}
