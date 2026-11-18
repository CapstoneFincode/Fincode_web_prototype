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
    # 문자열에 적힌 유니코드/hex 이스케이프는 eval 없이 한 글자씩 해석한다.
    decoded=re.sub(r'\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2}))',lambda m:chr(int(next(x for x in m.groups() if x is not None),16)) if int(next(x for x in m.groups() if x is not None),16)<=0x10FFFF else m.group(),decoded)
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
    # 난독화 뷰는 최대 두 번만 펼친다. 원문은 절대 수정하지 않는다.
    initial=list(candidates)
    for view_name,value in initial:
        folded=re.sub(r'(?<![A-Za-z])(?:[A-Za-z][ \t·._-]){3,}[A-Za-z](?![A-Za-z])',lambda m:re.sub(r'[ \t·._-]','',m.group()),value)
        candidates.append((view_name+'_letterfold',folded))
        # 한글 구문 규칙만 이 뷰를 보게 해 영문 단어 경계 파괴를 피한다.
        candidates.append((view_name+'_ko_compact',re.sub(r'\s+','',value)))
        for match in list(re.finditer(r'(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{16,4096}={0,2}(?![A-Za-z0-9+/_-])',value))[:8]:
            try:
                nested=base64.b64decode(match.group()+'='*(-len(match.group())%4),altchars=b'-_',validate=True).decode('utf-8')
                if nested and sum(c.isprintable() or c.isspace() for c in nested)/len(nested)>.9:candidates.append(('nested_base64',inspect_normalize(nested)))
            except (ValueError,binascii.Error,UnicodeError):pass
    seen=set();result=[];budget=0
    for name,value in candidates:
        # 뷰 종류도 구분한다. 태그 전용 뷰가 일반 뷰를 없애지 않게 한다.
        key=(name,value)
        if key not in seen and len(result)<96 and budget+len(value)<=1200000:
            seen.add(key);result.append((name,value));budget+=len(value)
    return result


def educational_quotes(text):
    """완결된 짧은 인용문+명시적 교육/검토 문맥에만 점수 감경 후보를 둔다."""
    educational=bool(re.search(r'보안\s*교육|교육\s*자료|공격\s*예시|인용문.{0,25}검토',text))
    review=bool(re.search(r'설명하는\s*교육안|차이.{0,25}설명|실행하지\s*(?:마|않)|따르지\s*(?:마|않)',text))
    execute=bool(re.search(r'인용문.{0,35}(?:그대로\s*실행|따르세요|실행하세요)|(?:execute|follow|apply).{0,30}(?:quoted|quotation)',text,re.I))
    if not educational or not review or execute:return []
    return [m.span() for m in re.finditer(r'“[^“”\n]{1,300}”|「[^「」\n]{1,300}」|"[^"\n]{1,300}"',text)]



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
        views=inspection_views(text);matches=[];weight=0;quoted_weight=0
        quote_spans=educational_quotes(inspect_normalize(text))
        for rule in self.rules:
            for view_name,view in views:
                if rule.get('views') and view_name not in rule['views']:continue
                if view_name.endswith('_ko_compact') and not re.search('[가-힣]',rule['pattern']):continue
                found=rule['compiled'].search(view)
                if found:
                    quote_only=(view_name=='normalized' and any(a<=found.start() and found.end()<=b for a,b in quote_spans))
                    # 동일한 인용문이 다른 변환 뷰에서 다시 매칭돼 감경을 무효화하지 않게 한다.
                    if quote_only:
                        remainder=view
                        for a,b in reversed(quote_spans):remainder=remainder[:a]+' '*(b-a)+remainder[b:]
                        quote_only=not any(rule['compiled'].search(v) for n,v in inspection_views(remainder) if (not rule.get('views') or n in rule['views']) and (not n.endswith('_ko_compact') or re.search('[가-힣]',rule['pattern'])))
                    matches.append({'id':rule['id'],'category':rule['category'],'view':view_name,'span':list(found.span()),'educationalQuote':quote_only})
                    if quote_only:quoted_weight+=rule['weight']
                    else:weight+=rule['weight']
                    break
        weight+=min(quoted_weight,self.config.get('educational_quote_weight_cap',1))
        threshold=self.config['threshold']
        return {'blocked':weight>=threshold,'reason':'pattern_match' if weight>=threshold else 'no_known_pattern','score':min(1.0,weight/threshold),'matchedRules':matches,'version':self.config['version'],'scoreKind':'rule_weight_ratio'}
