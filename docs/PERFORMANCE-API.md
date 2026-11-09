# 정상 이메일 성능 평가 API

단일 모델 평가와 2~5개 모델 비교는 같은 실행·결과 API를 사용한다. 기존 이메일 요약과 공격 시나리오 입력 구성은 유지한다. 현재 방어는 `off`만 지원하며, LLM 심사 모델을 호출하지 않는다.

## 데이터셋과 승인 상태

`GET /api/performance/dataset`

```json
{"dataset":{"id":"ground_truth_set/v1","name":"합성 정상 이메일 100건 v1","total":100,"available":100,"reviewedLabels":100,"reviewedReferences":0,"items":[{"id":"DB 이메일 UUID","sourceId":"GT-SYN-001","caseKey":"DB 논리 ID","title":"제목","reviewStatus":"reviewed","referenceReviewStatus":"draft","dbVersion":2,"groundTruthId":"기준 정답 UUID"}]}}
```

- 서버에 포함된 `datasets/ground_truth_set/v1/emails.json`의 파일 해시를 manifest와 대조한다.
- 원본 100건의 제목·본문 해시로 DB의 **최신 버전**, 활성, 정상, 비예시 항목을 연결한다. 기존 예시 5건과 다른 이메일은 포함하지 않는다.
- `available`은 현재 원본과 일치하는 항목 수다. `items`는 일치 항목만 포함한다. 동일 원본에 여러 DB 항목이 일치하면 `409 DATASET_AMBIGUOUS`로 거부한다.
- 정상 라벨은 `reviewed`가 필수다. 기준 정답이 `draft`면 실행은 가능하지만 해당 평가 전체를 `preliminary: true`로 기록한다. 기준 정답이 없거나 `rejected`/`sample`이면 실행하지 않는다.
- 정상 라벨 승인과 기준 정답 검수는 별도다. 승인 표시만으로 독립적인 사람 검수를 수행했다고 주장하지 않는다.

## 실행 생성

`POST /api/performance` → `202 {"id":"평가 UUID","existing":false}`

```json
{"name":"모델 비교","datasetId":"ground_truth_set/v1","emailIds":["DB 이메일 UUID"],"models":["실제 모델 ID A","실제 모델 ID B"],"defense":"off","idempotencyKey":"클라이언트의 고유 요청 키"}
```

- 이메일 1~100개, 서로 다른 모델 1~5개, 조합마다 1회 실행한다. `repeat`를 전달하면 정수 `1`만 허용한다. 개별 평가 최대 500회이며, 전체 성능 평가의 대기·실행 요청도 합계 500회까지 허용한다. 초과하면 `409 PERFORMANCE_QUEUE_FULL`이다.
- UI는 실제 `/api/models` 목록에서 모델을 선택한다. 서버는 ID 형식·중복·개수와 Codex 제외를 검사하고, 제공자의 실제 이용 가능 여부는 호출 결과에 기록한다.
- 같은 키와 같은 요청은 기존 평가 ID를 반환하며 호출을 중복 생성하지 않는다. 같은 키에 다른 요청은 `409 IDEMPOTENCY_CONFLICT`다. 불확실한 네트워크 응답 후 같은 키로 재조회할 수 있다.
- 선택한 이메일·정답 UUID와 버전, 내용·원본 데이터 해시, 정답 검토 상태, 채점 버전, 시스템 프롬프트와 실제 전송 설정을 실행 시점에 고정한다. 이후 데이터 수정은 과거 평가에 반영하지 않는다.
- 모델 요청에는 시스템 프롬프트와 이메일 제목·본문만 전송한다. 기준 정답·필수 사실·채점 기준은 모델 메시지에 포함하지 않는다.
- source에 대응하는 사실 근거는 사실 문자열이 일치하는 항목에만 연결한다. 현재 기준 정답의 사실이 바뀌면 기존 근거를 잘못 재사용하지 않는다.

## 큐와 오류 처리

- 기존 `experiments → experiment_cases → runs → llm_calls` 큐를 재사용한다. 한 평가 내 모델×이메일 조합은 고유 case 번호를 갖는다.
- 이메일 단위로 모델을 교차 실행하며, 다음 이메일에서는 첫 번째 모델을 회전한다. 큐는 마지막 배정 시각에 따라 실험을 순환한다.
- 여러 워커가 있어도 큐의 미완료 실행은 한 건씩 배정한다. 기존 즉시 이메일 요약은 별도 경로이므로 큐와 동시에 시작될 수 있다. 측정 시간은 `llm_calls.latency_ms`의 게이트웨이 요청·응답 왕복 시간이다. 큐 대기와 채점 시간은 제외하지만 네트워크·서버 부하는 포함하므로 순수 모델 계산 시간이 아니다.
- 인증 오류(401/403)는 해당 평가의 남은 요청 전체를 중단한다. 요청 한도(429)는 **해당 평가의 해당 모델** 대기 요청을 중단한다. 기타 실행 오류가 해당 모델에서 3회 연속 발생하면 그 모델의 대기 요청을 중단한다. 성공하면 연속 오류 구간은 끊긴다.
- 실패·시간 초과·사용량 불명확 상태는 자동 재시도하지 않는다. 새 평가의 명시적 실행만 새 호출을 만든다. 실험 생성의 중복 방지는 제공자 호출의 exactly-once 보장을 뜻하지 않는다.
- 서버 중단 후 만료된 실행은 `WORKER_INTERRUPTED` 시간 초과로 남긴다. 사용량을 0으로 추정하지 않는다.

## 진행 조회와 비교

`GET /api/performance` → `{"evaluations":[summary]}` (최신 50개)

`GET /api/performance/{id}` → `summary`

공통 summary 필드:

```text
id, name, status, datasetId, createdAt, models[], defense:"off",
total, finished, succeeded, failed, cancelled, running, queued,
preliminary, commonCompletedEmails, stopReason,
modelResults:[{
  model, total, finished, succeeded, failed, cancelled, running, queued, gradedCount,
  meanRougeL, meanFactCoverage, confirmedFacts, totalFacts, needsReviewFacts,
  latencyMsMean, latencyMsMedian,
  tokens:{inputMean,outputMean,totalMean,reportedCount},
  common:{count,meanRougeL,latencyMsMean,totalTokensMean}
}]
```

- `finished`는 성공·실패·시간 초과·취소 등 종료 상태 합계다. `failed`에는 시간 초과와 차단 상태가 포함된다. 실패·취소를 요약 품질 0점으로 섞지 않는다.
- 품질 평균은 성공했고 채점이 확정된 결과만 사용하며 `gradedCount`에 그 건수를 표시한다. 채점 불가 결과도 알려진 전체 사실 수와 검토 필요 수에 포함한다. 응답 시간·토큰 평균은 성공 결과의 보고된 값만 사용한다. 보고된 값이 하나도 없으면 `null`이다. `tokens.reportedCount`는 총토큰이 보고된 성공 결과 수다.
- `commonCompletedEmails`와 각 모델의 `common`은 **모든 선택 모델에서 실행 성공·채점 확정된 같은 이메일 UUID들의 교집합**만 비교한다. 일반 평균과 표본 수를 함께 표시한다.
- ROUGE-L은 문자 단위 텍스트 중복 보조 지표다. 필수 사실 확인은 보수적 규칙에 따른 확인이며 의미 정확도의 완전한 판정이 아니다. 미확인 사실은 검토 필요로 남긴다. 임의의 종합 승자를 만들지 않는다.
- 조회는 본문·출력·전체 제공자 payload를 제외한 집계용 열만 한 번에 조회한다. 케이스마다 추가 쿼리를 하지 않는다.

## 개별 결과

`GET /api/performance/{id}/cases?offset=0&limit=20`

```text
{total,offset,limit,cases:[{
  id, sourceId, title, emailId, requestedModel, returnedModel, status,
  summary, reference, body, finishReason, warnings[],
  usage:{inputTokens,outputTokens,totalTokens}, latencyMs,
  error:{code,message}|null,
  quality:{...채점 details,score,version,decisionStatus,evidence}|null,
  referenceReviewStatus
}]}
```

- `offset >= 0`, `1 <= limit <= 50`. 결과 화면에 필요한 본문·기준 정답은 페이지 단위로 조회한다.
- `quality`는 `rougeL:{precision,recall,f1}`, `factCoverage`, `confirmedFacts`, `totalFacts`, `needsReviewFacts`, `facts` 등을 포함한다. 점수는 0~1이다.
- 모델 실행 오류는 `quality:null`이다. 채점기 오류나 실행 시 고정한 채점 버전을 사용할 수 없는 경우 모델 출력은 보존하고, `score:null`, `decisionStatus:"indeterminate"`, `errorCode:"SCORING_UNAVAILABLE"`로 기록한다.
- `requestedModel`과 `returnedModel`을 별도로 표시한다. 사용량 미보고는 `null`로 유지한다.
- `finishReason`과 `warnings`에는 출력 토큰 한도에 따른 잘림 등 제공자 응답 경고를 보존한다. 잘린 결과의 점수를 완전한 응답의 품질로 해석하지 않는다.

## 취소

`POST /api/performance/{id}/cancel` body `{}` → 현재 summary

아직 요청하지 않은 `queued` 실행만 `cancelled`로 변경한다. 진행 중인 제공자 호출은 계속 완료·저장하므로 취소 직후 `running`이 남을 수 있다. 미실행 건은 `started_at:null`, 실제 취소 시각만 `completed_at`에 저장한다. 반복 취소는 새 호출이나 기록을 만들지 않는다.

## 배포와 검증

- `005_performance_runtime.sql`은 실험의 마지막 배정 시각·중단 사유와 미실행 취소 시간 제약만 추가·수정한다. 기존 데이터/실험을 삭제하지 않는다.
- Docker 이미지에는 고정 데이터 원본과 manifest를 포함한다.
- 검증은 폐기 가능한 로컬 PostgreSQL과 가짜 모델 HTTP로 수행한다. 운영 DB 쓰기·유료 모델 호출을 테스트에 사용하지 않는다.
- 운영 배포와 마이그레이션 적용은 별도 승인 단계이며, 코드 구현 완료가 운영 배포 완료를 의미하지 않는다.
