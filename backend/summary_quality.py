"""Offline reference similarity and deliberately conservative fact confirmation.

The numeric score is Unicode-character ROUGE-L F1, NOT semantic accuracy.
Fact confirmation is a separate lower bound; unconfirmed facts are not errors.
No generated text is executed, interpreted as instructions, or sent to a judge.
"""
from __future__ import annotations

import re
import unicodedata


VERSION = 'summary-quality-char-rouge-l-v1'
TOKENIZER = 'nfc-casefold-unicode-alnum-char-v1'
MAX_TEXT_CHARS = 60_000
MAX_ROUGE_TOKENS = 16_000
MAX_FACTS = 100
MAX_FACT_CHARS = 2_000
MAX_UNITS = 1_000

_SENTENCES = re.compile(r'(?:[.!?。！？](?=\s|$)|[\n;])+')
_BULLET = re.compile(r'^\s*(?:[-*•]\s+|\d+[.)]\s+)')
_NUMBER = re.compile(r'\d+(?:[,:./-]\d+)*')
_LETTERS = re.compile(r'[^\W\d_]+', re.UNICODE)
_PARTICLE = re.compile(r'(?:으로부터|에서는|에서도|에게는|에는|에서|에게|으로|까지|부터|은|는|이|가|을|를|와|과|의|에|로|도)$')
_ENDINGS = re.compile(r'(?:입니다|합니다|됩니다|한다|된다|이다|했다|였다|하다)$')
_STOP = frozenset(('것', '경우', '내용', '현재', '최종', '필요', '아직', '이미', '하다', '있다', '없다',
                   '않는다', '않다', '미정', '확정', '변경', '유지', '대상', '기한', '일시', '시각', '시간',
                   '상태', 'the', 'and', 'are', 'will', 'was', 'this', 'that', 'for', 'with', 'not'))
_NEGATIVE = re.compile(r'없|않|아니|불가|불필요|금지|미확정|보류|취소|제외|\b(?:no|not|never|without|cancelled|canceled|pending|forbidden|excluded)\b')
_REFUSAL = re.compile(
    r'(?:요약|분석|처리|응답|답변|요청).{0,24}(?:할 수 없|할 수는 없|하지 않겠|거부합니다|불가합니다)|'
    r'도와드릴 수 없|도움을 드릴 수 없|'
    r'(?:요청|지시).{0,16}(?:따를 수 없|따르지 않겠)|'
    r'요약.{0,8}(?:제공|생성).{0,8}(?:불가|어렵)|'
    r'\b(?:cannot|can\x27t|will not|won\x27t|am unable to|unable to)\s+(?:summari[sz]e|assist|help|comply|provide|process)\b')
_INVALIDATION = re.compile(
    r'(?:위|앞|다음|인용한|이)\s*(?:문장|내용|요약|진술).{0,24}(?:사실이 아(?:니|닙)|틀렸|잘못|거짓|예시일 뿐)|'
    r'(?:위와|앞과)\s*반대로|정정하면|'
    r'\b(?:above|quoted|following)\b.{0,30}\b(?:false|incorrect|not true|not correct)\b')
_FULL_CLAUSE = re.compile(r'(?:다|요|음|함|됨|임|불가|금지|없음|필요|예정)$|\b(?:is|are|will|must|not|no|please|confirmed|cancelled|canceled)\b')

LIMITATIONS = [
    '유사도는 사실 정확성·완전성·문체 품질·보안 성능을 보장하지 않습니다.',
    '핵심 정보 자동 확인률은 규칙으로 확인한 비율이며 정답률이나 의미 기반 재현율이 아닙니다.',
    '누락 후보·충돌 후보·검토 필요는 자동 오답 판정이 아닙니다.',
    '기준 요약이 모든 필수 핵심 정보를 포함한다고 가정하지 않습니다.',
    '규칙으로 발견하지 못한 바꿔쓰기·환각·조건/주체/부정 의미 변경이 있을 수 있습니다.',
]


def _normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text).casefold()).strip()


def _unit(text):
    return _normalize(_BULLET.sub('', text)).rstrip(' .!?。！？;')


def _units(text):
    # Newlines and sentence boundaries are retained until this split. Colons,
    # hyphens, decimal points and commas inside values are NOT clause separators.
    normalized = unicodedata.normalize('NFC', text).casefold()
    return [value for part in _SENTENCES.split(normalized) if (value := _unit(part))]


def _tokens(text):
    return [char for char in unicodedata.normalize('NFC', text).casefold() if char.isalnum()]


def _lcs_length(left, right):
    """Exact bit-vector LCS; uses the shorter sequence for bounded bit widths.

    Each row bit marks a +1 step in the ordinary LCS dynamic-programming row.
    No approximate substring search or quadratic Python matrix is allocated.
    """
    if len(left) > len(right):
        left, right = right, left
    masks = {}
    for index, token in enumerate(left):
        masks[token] = masks.get(token, 0) | (1 << index)
    row = 0
    for token in right:
        matches = row | masks.get(token, 0)
        row = matches & ~(matches - ((row << 1) | 1))
    return row.bit_count()


def _rouge(reference, response):
    reference_tokens, response_tokens = _tokens(reference), _tokens(response)
    counts = {'referenceTokens': len(reference_tokens), 'responseTokens': len(response_tokens)}
    if not reference_tokens:
        return None, 'empty_reference_tokens', counts
    if not response_tokens:
        return None, 'empty_response_tokens', counts
    if max(len(reference_tokens), len(response_tokens)) > MAX_ROUGE_TOKENS:
        return None, 'token_limit', counts
    common = _lcs_length(reference_tokens, response_tokens)
    precision, recall = common / len(response_tokens), common / len(reference_tokens)
    f1 = 2 * common / (len(reference_tokens) + len(response_tokens))
    return {'precision': precision, 'recall': recall, 'f1': f1, 'lcsLength': common, **counts}, None, counts


def _anchors(text):
    result = set()
    for word in _LETTERS.findall(text):
        word = _PARTICLE.sub('', _ENDINGS.sub('', word))
        if len(word) >= 2 and word not in _STOP:
            result.add(word)
    return result


def _related(left, right):
    common = _anchors(left) & _anchors(right)
    return len(common) >= 2 or any(len(anchor) >= 4 for anchor in common)


def _description(unit):
    return (unit, _anchors(unit), bool(_NEGATIVE.search(unit)), _NUMBER.findall(unit), _NUMBER.sub('#', unit))


def _possible_conflict(expected, actual, known_units):
    text, anchors, negative, numbers, skeleton = expected
    other, other_anchors, other_negative, other_numbers, other_skeleton = actual
    if text == other:
        return False
    if numbers and other_numbers and numbers != other_numbers and skeleton == other_skeleton:
        return True
    common = anchors & other_anchors
    related = len(common) >= 2 or any(len(anchor) >= 4 for anchor in common)
    return other not in known_units and related and negative != other_negative


def _fact_results(required_facts, response, source_body, fact_evidence, response_guard):
    if not isinstance(required_facts, (list, tuple)) or len(required_facts) > MAX_FACTS:
        return [], None, 'invalid_fact_list'
    response_units = _units(response)
    if len(response_units) > MAX_UNITS:
        return [], None, 'response_unit_limit'
    source_units = set(_units(source_body)) if source_body else set()
    aligned = isinstance(fact_evidence, (list, tuple)) and len(fact_evidence) == len(required_facts)
    candidates = []
    for index, fact in enumerate(required_facts):
        valid = (isinstance(fact, str) and bool(fact.strip()) and len(fact) <= MAX_FACT_CHARS and
                 bool(_FULL_CLAUSE.search(_unit(fact))))
        values = {_unit(fact)} if valid else set()
        quote = fact_evidence[index] if aligned else None
        # A source quote must be literally present, be a complete source unit,
        # and contain a clause rather than only dates, amounts, or noun fragments.
        if (valid and isinstance(quote, str) and 8 <= len(quote) <= MAX_FACT_CHARS and
                quote in source_body and _unit(quote) in source_units and _FULL_CLAUSE.search(_unit(quote)) and
                _related(_unit(fact), _unit(quote)) and
                bool(_NEGATIVE.search(_unit(fact))) == bool(_NEGATIVE.search(_unit(quote))) and
                (not _NUMBER.findall(fact) or _NUMBER.findall(fact) == _NUMBER.findall(quote))):
            values.add(_unit(quote))
        candidates.append(values)
    known_units = set().union(*candidates) if candidates else set()
    output_descriptions = [_description(unit) for unit in response_units]
    normalized_response = _normalize(response)
    results = []
    for index, fact in enumerate(required_facts):
        values = candidates[index]
        result = {'fact': fact if isinstance(fact, str) else '', 'status': 'needs_review', 'evidence': '', 'reason': 'unsupported_fact'}
        if not values:
            results.append(result)
            continue
        if response_guard:
            result.update(reason=response_guard, evidence=response[:400])
            results.append(result)
            continue
        matches = [unit for unit in response_units if unit in values]
        expected_descriptions = [_description(value) for value in values]
        conflict = next((actual[0] for actual in output_descriptions if actual[0] not in values and any(
            _possible_conflict(expected, actual, known_units) for expected in expected_descriptions)), None)
        if conflict:
            result.update(status='conflict_candidate', evidence=conflict[:400], reason='competing_value_or_polarity')
        elif matches:
            result.update(status='confirmed', evidence=matches[0][:400], reason='complete_clause_exact_match')
        elif any(value in normalized_response for value in values):
            # A quote, prefix, negating suffix, or a larger clause can repeat the
            # right words without asserting this fact. Never confirm a substring.
            result.update(reason='embedded_or_quoted_clause', evidence=response[:400])
        elif any(len(expected[1] & actual[1]) >= 2 or any(len(anchor) >= 4 for anchor in expected[1] & actual[1])
                 for expected in expected_descriptions for actual in output_descriptions):
            result.update(reason='paraphrase_or_relation_needs_review')
        else:
            result.update(status='missing_candidate', reason='no_exact_clause_observed')
        results.append(result)
    total = len(results)
    confirmed = sum(row['status'] == 'confirmed' for row in results)
    return results, confirmed / total if total else None, None


def evaluate_summary(reference, required_facts, response, *, source_body='', fact_evidence=None):
    """Return the fields consumed by ``evaluations.summary_quality``.

    ``fact_evidence`` may be None or an aligned list of strings/None. Bad evidence
    is ignored; it never grants confirmation. ``source_body`` is evidence only and
    is not added to ROUGE's reference. All limits are explicit, never truncation.
    """
    details = {'tokenizer': TOKENIZER, 'metric': 'rouge-l-f1', 'scoreMeaning': 'reference_similarity_only',
               'rougeL': None, 'factCoverage': None, 'confirmedFacts': 0, 'totalFacts': 0,
               'needsReviewFacts': 0, 'facts': [], 'limitations': list(LIMITATIONS),
               'limits': {'maxTextChars': MAX_TEXT_CHARS, 'maxRougeTokens': MAX_ROUGE_TOKENS,
                          'maxFacts': MAX_FACTS, 'maxFactChars': MAX_FACT_CHARS, 'maxResponseUnits': MAX_UNITS},
               'truncated': False, 'responseSignal': None}
    if isinstance(required_facts, (list, tuple)) and len(required_facts) <= MAX_FACTS:
        details.update(totalFacts=len(required_facts), needsReviewFacts=len(required_facts),
                       facts=[{'fact': fact if isinstance(fact, str) and len(fact) <= MAX_FACT_CHARS else '',
                               'status': 'needs_review', 'evidence': '', 'reason': 'evaluation_unavailable'}
                              for fact in required_facts])
    result = {'evaluator_version': VERSION, 'decision_status': 'indeterminate', 'score': None,
              'boolean_value': None, 'evidence': '', 'details': details}
    issue = None
    if not isinstance(reference, str) or not reference.strip():
        issue = 'missing_reference'
    elif not isinstance(response, str) or not response.strip():
        issue = 'missing_response'
    elif not isinstance(source_body, str):
        issue = 'invalid_source_body'
    elif any(len(value) > MAX_TEXT_CHARS for value in (reference, response, source_body)):
        issue = 'text_limit'
    if issue:
        details['reason'] = issue
        result['evidence'] = '기준 요약·응답 또는 비교 한도를 확인해야 하므로 자동 점수를 부여하지 않았습니다.'
        return result
    rouge, issue, counts = _rouge(reference, response)
    details['rougeL'] = rouge
    details['tokenCounts'] = counts
    normalized_response = _normalize(response)
    guard = ('refusal_candidate' if _REFUSAL.search(normalized_response) else
             'context_invalidates_assertion' if _INVALIDATION.search(normalized_response) else None)
    facts, coverage, fact_issue = _fact_results(required_facts, response, source_body, fact_evidence, guard)
    if fact_issue and not facts:
        facts = details['facts']
    details.update(facts=facts, factCoverage=coverage, totalFacts=len(facts),
                   confirmedFacts=sum(row['status'] == 'confirmed' for row in facts),
                   needsReviewFacts=sum(row['status'] != 'confirmed' for row in facts),
                   factRule='complete-clause-exact-conservative-v1', factIssue=fact_issue,
                   responseSignal=guard)
    if not issue and guard:
        issue = guard
    if not issue:
        result.update(decision_status='determinate', score=rouge['f1'],
                      evidence='기준 요약과 응답의 문자 ROUGE-L F1입니다. 사실 정확성이나 합격 여부를 판정한 점수가 아닙니다.')
        details['reason'] = 'reference_similarity_computed'
    else:
        details['reason'] = issue
        result['evidence'] = '비교 한도 또는 거부·문맥 신호로 자동 품질 점수를 보류했습니다. 상세 근거를 검토해 주세요.'
    return result
