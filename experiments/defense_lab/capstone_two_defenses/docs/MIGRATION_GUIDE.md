# 메인 이식 검토 가이드 — 실행하지 않음

이 문서는 적용 승인 자료다. 원본에 파일을 복사하거나 병합하지 않았다. 사용자가 '적용'이라고 답한 뒤에도 최신 main 기준 변경 파일 목록과 diff를 다시 보여주고 추가 확인을 받아야 한다.

## 이식 후보

1. backend/defenses/__init__.py, input_filter.py, separation.py, gateway.py, rules.yaml
2. backend/app.py의 DefenseGateway 가져오기·모드 검증·단건 요약 호출 래핑 부분
3. public/app.js의 단건 요약 방어 선택과 요청 필드
4. requirements.txt의 PyYAML 의존성

실제 구현 diff는 isolated_changes.diff에 있다. 이 diff 전체를 main에 적용하면 안 된다. 복사본의 DB 비활성화·실험 키·worker 비활성화·hosting manifest 제거와 실험 API 설치는 격리용 변경이므로 운영 이식 대상에서 제외한다. snapshots, 실험 데이터, lab API·HTML, .venv, upstream_reference, 결과·캐시는 이식하지 않는다. 예제 데이터도 추가하지 않는다.

## 최소 연결 형태

```python
from backend.defenses.gateway import DefenseGateway

gateway = DefenseGateway()
plan = gateway.prepare(messages, defense_mode)
result = gateway.execute_prepared(generate, model, plan, max_tokens)
```

위 예시는 실제 함수 인자 순서를 따른 연결 설명이다. 기존 응답·usage 보존과 사전 차단의 modelCalled=false를 함께 처리해야 한다. defense_mode 기본값은 off, 단일 기능은 filter 또는 separate, 동시 활성화는 both다.

현재 격리 구현은 단건 요약과 실험 러너에 연결되어 있다. 운영의 저장형 공격 실험·성능 평가 큐에는 아직 연결하지 않았다. 그 경로는 작업 생성 시 방어 설정을 고정해 저장하고 worker가 같은 gateway를 거치도록 별도 변경해야 한다. 실제 전송문과 원문·정책 버전을 구분하고, 사전 차단은 호출 토큰 0·모델 미호출로 기록해야 한다. 모델 거부·API 실패·공격 실패도 구분한다. 이에 필요한 DB 변경은 이번 작업에서 수행하지 않았다.

## 검토와 롤백

먼저 실제 모델의 네 설정 반복 비교를 수행해 ASR 감소율, 정상 FPR, ROUGE-L, 토큰과 지연을 확인한다. 권고 기준 예시는 ASR 상대 감소 50% 이상·FPR 5% 이하·품질 하락 5% 이하·지연 2배 이하이며, 기준선 ASR 0일 때 상대 감소는 정의하지 않는다. 현재 Mock만으로 승인할 수 없다.

배포 후 이상이 있으면 모드를 off로 돌려 기존 메시지를 그대로 전달한다. 코드 롤백은 승인된 이식 커밋만 되돌리고 데이터·스냅샷·DB 행을 삭제하지 않는다. 이번 단계에서는 배포·설정 변경·마이그레이션을 실행하지 않았다.
