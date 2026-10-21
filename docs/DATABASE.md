# PostgreSQL 연동

> UI 브랜치의 카테고리별 개수 선택을 포함한 소스 기준입니다. 10월 19일 운영 배포 기록과 구분하며 실제 반영 상태는 [UI-CATEGORIES.md](UI-CATEGORIES.md)를 확인하세요.

정상 이메일과 공격 시나리오를 팀 DB에 저장하고, 웹에서 선택한 시나리오를 Python 백엔드가 반복 실행합니다. promptfoo 외부 데이터, 결과 파일, 실행 엔진을 사용하지 않습니다. 정상 이메일의 우선 목적은 **보안 ON에서 정상 입력을 공격으로 오인해 차단하거나 답변을 거부하는지 확인**하는 것입니다. 기준 요약은 필수가 아닙니다.

현재 실행 가능한 것은 **방어 OFF** 본문 요약·직접/간접 공격·정상 이메일 응답 확인입니다. 보안 방어는 구현 전이므로 ON 요청을 거절합니다. ON/OFF 짝 비교와 보안 판정 저장 구조는 준비되어 있지만, 아직 ON 측정값이나 Gateway 오차단율이 생성되지는 않습니다. BAZE API Gateway는 모델 접속 경로이며 팀의 보안 Gateway와 다릅니다.

## 실행

### Docker Compose (팀 기본 경로)

```bash
git clone --branch UI https://github.com/CapstoneFincode/Fincode_web_prototype.git
cd Fincode_web_prototype
cp .env.example .env
# .env에 POSTGRES_PASSWORD 설정. 모델 호출에는 BAZE_API_KEY도 설정.
docker compose up --build -d
```

- http://localhost:8000 접속. PostgreSQL 17이 내부 네트워크에서 시작됩니다. DB 포트는 호스트에 공개하지 않습니다.
- 앱 시작 시 `python -m backend.migrate`가 마이그레이션과 합성 예시 5개/공격 예시 7개를 등록한 뒤 서버를 시작합니다. 재시작해도 중복 등록하지 않습니다.
- `/api/health`의 `database.configured`, `database.ready`로 DB 상태를 확인합니다. `configured` 최상위 필드는 기존과 같이 모델 키 설정 여부입니다.
- 모델 키 없이도 데이터 CRUD는 사용 가능합니다. 모델 목록·요약·실험 실행에만 키가 필요합니다.
- DB 데이터는 `fincode_pg` Docker 볼륨에 남습니다. `docker compose down`은 볼륨을 유지합니다. **`down -v`는 DB 데이터를 삭제합니다.**
- 로컬 실행에서는 `BACKEND_SHARED_TOKEN`을 비워둡니다. 서버는 기본적으로 127.0.0.1에만 바인딩합니다.

### 기존 PostgreSQL + Python

Python 3.11 이상에서 `requirements.txt`를 설치하고 환경변수를 설정합니다. DB 이름·계정·비밀번호는 실제 환경에 맞춰 넣으세요. 일반 Python 실행은 `.env`를 자동으로 읽지 않습니다.

```bash
python -m pip install -r requirements.txt
# DATABASE_URL: PostgreSQL URL 또는 libpq conninfo 문자열
# 예: host=localhost port=5432 dbname=fincode user=fincode
# 비밀번호는 PGPASSWORD 또는 안전한 서버 환경변수로 전달
python -m backend.migrate
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

`DATABASE_URL` 미설정 시 기존 브라우저 저장·단건 요약 모드입니다. 설정했는데 DB에 장애가 생기면 서버 저장 API는 503을 반환합니다. 저장 실패를 브라우저 저장 성공으로 바꾸지 않습니다. 모델 호출 전 DB 저장이 실패하면 호출을 시작하지 않습니다. 응답 후 저장 실패 시 관측한 응답·사용량과 `database.saved: false`를 반환하고 자동으로 다시 호출하지 않습니다.

## 웹 사용 흐름

1. **데이터 관리**에서 정상 이메일/공격 시나리오를 등록합니다. 정상 원문과 제목만 필수이며 기준 요약·핵심 정보는 선택입니다. 정상 라벨을 확인한 뒤 검토 완료로 표시합니다. 합성 예시는 목표 100건에 포함하지 않습니다.
2. **공격 실험**에서 실제 제공 모델, 기준 이메일, 카테고리와 카테고리별 시나리오 개수를 선택합니다. 활성·지원 시나리오를 최초 등록 순서대로 선택해 각 1회 실행합니다(합계 최대 100개). 정상 실험은 별도 반복 횟수(1~10회, 기본 1회)만큼 선택한 이메일을 실행하며 공격문을 삽입하지 않습니다. 실행 버튼은 실제 모델 사용량을 발생시킵니다.
3. **웹에서 실행한 DB 실험**에서 진행률·결과를 확인합니다. 서버 작업이므로 브라우저를 닫아도 실행됩니다. 상세 창은 버튼으로 상태를 새로고침합니다.
4. 응답, 실행 당시 메시지, 모델/설정, 토큰·응답시간, 자동 판정·수동 판정 이력을 확인하거나 실험 JSON으로 내보냅니다. 정상 응답 거부는 수동으로 판정합니다.

직접 공격은 원문 메시지 다음의 별도 `user` 메시지에 삽입합니다. 간접 공격은 이메일 본문 마지막에 두 줄 개행 후 붙입니다. 실제 전송 메시지와 삽입 방식을 스냅샷으로 남깁니다. 첨부파일/OCR 경로는 시나리오 저장만 가능하며 실행은 거절합니다.

`expectedOutput`이 있는 시나리오는 응답 전체의 양끝 공백을 제거한 **정확 일치**만 자동 성공으로 판정합니다. 부분 포함은 성공이 아닙니다. 비어 있으면 자동 판정 없이 수동 검토합니다. 최신 수동 판정을 우선하되 원래 규칙 판정과 후속 판정을 모두 남깁니다. 오류·타임아웃·미확정은 ASR/정상 거부율 분모에서 제외하고 판정 수/전체 수를 함께 표시합니다. 정상 거부의 원인(모델/제공자, 불명)은 별도이며 **OFF 결과를 보안 Gateway 오차단으로 기록할 수 없습니다.**

기존 localStorage 데이터는 자동 업로드하지 않습니다. DB가 연결되면 DB 자료를 보여주고 기존 브라우저 자료는 그대로 보관합니다. 필요한 항목은 기존 JSON 백업을 참고해 DB에 직접 등록하세요. 대량 JSON 가져오기는 브라우저 모드에서만 지원합니다. 실험 계획·의사결정 메모는 여전히 브라우저에 저장되고, 9월 발표자료의 과거 집계는 별도로 유지합니다.

## 테이블 구조

DB 하나의 `fincode` 스키마에 13개 도메인 테이블을 둡니다.

| 테이블 | 역할 / 현재 연결 범위 |
| --- | --- |
| `email_cases` | 정상 이메일 원문·검토 라벨·수정 버전·선택 핵심 정보 |
| `ground_truths` | 선택 기준 요약·검토 상태. 정상 오차단 평가의 필수 조건 아님 |
| `attack_scenarios` | 자체 공격문·경로·기법·목표·판정 기준·카테고리·버전 |
| `attack_categories` | 공격 카테고리·정규화된 고유 이름·표시 순서 |
| `experiments` | 실험 묶음·정책·반복 계획·요청 중복 방지 키 |
| `experiment_cases` | 특정 원문/시나리오 버전·모델·반복·실제 메시지 스냅샷 |
| `runs` | 실행 상태·최종 응답·오류·시간·OFF/ON 정보·복구용 만료 시각 |
| `llm_calls` | 호출별 요청·응답·실제 모델·사용량·응답시간. 요약/보안/평가 호출 분리 |
| `evaluations` | 자동/수동 판정과 근거를 추가 기록. 기존 판정 덮어쓰지 않음 |
| `defense_profiles` | 향후 방어 구현·버전·실행 순서 저장 구조 |
| `gateway_checks` | 향후 방어 단계별 탐지/차단/변형·소요시간 저장 구조 |
| `attachments` | 향후 파일 위치·해시·추출 버전·OCR 결과 저장 구조 |
| `historical_aggregates` | 과거 집계의 별도 보관 구조. 현재 발표자료 UI는 기존 파일 사용 |

`public.fincode_schema_migrations`는 적용 버전·체크섬을 기록하는 운영 메타데이터 테이블입니다. 적용된 SQL의 내용을 바꾸면 시작을 중단합니다. 후속 변경은 새 번호의 migration으로 추가하세요. 초기화는 트랜잭션과 advisory lock으로 보호합니다.

`run_metrics`는 요약+보안 호출의 토큰을 집계합니다. 하나라도 누락되면 전체 합계를 `NULL`로 둡니다. 평가 모델 토큰은 따로 계산합니다. `completed_gateway_pairs`는 같은 case의 성공한 OFF/ON 실행만 묶고 응답시간·토큰 차이와 변화율을 제공합니다. 차단·오류 결과를 빠른 성공으로 포함하지 않으며 실제 모델·캐시·환경 동등성을 추가 확인해야 합니다.

수정은 같은 logical key의 새 revision을 만들고 실험은 이전 UUID를 계속 참조합니다. 목록 삭제는 보관 처리이며 결과를 연쇄 삭제하지 않습니다. 서버에 팀 인증이 연결되기 전 `created_by/evaluated_by`는 `team`입니다. 이 값은 개인별 감사/인증을 의미하지 않습니다.

## API

기존 `GET /api/models`, `POST /api/summarize`와 함께 다음 API를 제공합니다. 변경 요청에는 같은 출처의 `application/json`을 사용합니다.

| 경로 | 기능 |
| --- | --- |
| `GET /api/health` | 모델 키·DB 연결·기능 상태 |
| `GET /api/data` | 최신 비보관 정상 이메일·공격 시나리오·카테고리. 공격은 원등록 시각 `registeredAt` 순서 |
| `POST /api/categories` | 새 카테고리 등록 |
| `POST /api/data/normal` / `attacks` | 새 자료 등록 |
| `PUT /api/data/{kind}/{logical_id}` | `dbVersion` 확인 후 새 버전 저장 |
| `DELETE /api/data/{kind}/{logical_id}` | JSON `dbVersion` 확인 후 보관 처리 |
| `POST /api/experiments` | DB 작업 등록, HTTP 202 |
| `GET /api/experiments` | 최근 100개 실험 진행률 |
| `GET /api/experiments/{uuid}` | 입력·결과·호출·판정 상세 |
| `POST /api/runs/{uuid}/evaluations` | 수동 판정 추가 |

실험 등록 예:

```json
{
  "name": "직접 공격 기준 실험",
  "kind": "attack",
  "model": "실제 제공 모델 ID",
  "emailId": "정상 이메일의 dbId(UUID)",
  "scenarioIds": ["공격 시나리오의 dbId(UUID)"],
  "repeat": 1,
  "defense": "off",
  "idempotencyKey": "클라이언트가 생성한 요청 UUID"
}
```

공격 시나리오 ID는 중복 없이 1~100개이며 새 UI는 `repeat: 1`을 사용합니다. 이전 클라이언트의 `repeat` 1~10 요청은 지원하되 총 호출 수(시나리오 수 × 반복 수)는 100회를 넘을 수 없습니다. 정상 실험 UI는 별도의 반복 횟수 1~10을 전송하며 `scenarioIds`는 빈 배열입니다. 공격 선택 개수와 정상 반복 횟수는 서로 영향을 주지 않습니다.

같은 키·입력은 같은 실험을 반환합니다. 같은 키에 다른 입력은 409입니다. 의도적인 새 실험은 새 키를 사용하세요. 단건 요약도 DB 설정 시 실행 기록을 저장하며 키를 지정하면 완료 응답을 재사용합니다. 임의 요약 입력은 검토되지 않은 `unknown` 라벨이므로 정상 거부율 분모에 넣지 않습니다.

단일 앱 프로세스는 한 번에 한 실험 호출을 처리합니다. `FOR UPDATE SKIP LOCKED`로 중복 작업 선점을 막으며 모델 호출은 최대 120초, 실행 lease는 180초입니다. 서버 중단 후 만료된 실행은 `WORKER_INTERRUPTED`/사용량 미확인으로 종료합니다. 모델 호출이 이미 전송됐을 수 있어 자동 재시도하지 않습니다. 외부 모델에 대한 exactly-once 또는 공급자 취소는 보장하지 않습니다. 대규모 실행, 사용자별 쿼터, 작업 취소 API는 후속 범위입니다.

## 기존 Sites 호스팅 연결

2026-10-19 기존 사이트를 Railway PostgreSQL 18·Python API에 연결했습니다. 실제 주소·버전·권한·운영 설정은 [DEPLOYMENT.md](DEPLOYMENT.md)를 따릅니다. GitHub 브랜치와 Sites 배포는 별개이며, 아래는 연결을 재현하는 절차입니다.

Python 서버는 PostgreSQL과 함께 지속 실행되어야 합니다. 외부 HTTPS 백엔드를 마련한 뒤:

1. Python 서버에 `DATABASE_URL`, `BAZE_API_KEY`, `BACKEND_SHARED_TOKEN`을 설정합니다.
2. Worker 서버 환경에 `BACKEND_API_URL=https://백엔드호스트`와 같은 `BACKEND_SHARED_TOKEN`을 설정하고 이 브랜치 코드를 배포합니다. URL은 HTTPS 루트 주소이며 경로/사용자정보/쿼리가 없어야 합니다.
3. `build/sites-worker.ts` → `server/backend-proxy.mjs`가 `/api/*`를 이 백엔드로 연결합니다. 브라우저에는 토큰을 보내지 않습니다. 교차 출처 요청과 리디렉션을 거절하고 브라우저 쿠키/Authorization을 백엔드로 전달하지 않습니다.

백엔드 설정이 전혀 없으면 기존 Worker 단건 요약 모드를 유지합니다. 설정이 있는데 연결이 실패하면 503을 반환하며 기록 없는 별도 모델 호출로 대체하지 않습니다. 외부 백엔드는 공유 토큰이 필요하고, `/api/health`와 `/api/ready`만 비밀 없는 상태 확인용으로 공개합니다. 팀별 사용자 로그인·사용량 제한은 배포 접근 제어 계층에서 별도로 제공해야 합니다. 로컬 Compose는 기본적으로 외부 공개를 하지 않습니다.

## 검증

2026-10-19 배포 준비 검사: JavaScript 34개 + Python 34개(실DB 11개 포함) + workerd 1개, 총 69개 통과. 이후 Railway에서 실제 Dockerfile 빌드·API/DB 배포 성공과 기존 사이트 연결을 확인했습니다. 사이트 경유 정상 데이터 생성·새 클라이언트 조회·버전 2 수정·보관, 공격 시나리오 생성·조회·보관, DB 작업 큐의 실모델 1회 호출과 결과 재조회를 검증했습니다. 모델은 `gemini-3.5-flash-lite`, 방어 OFF, 입력 128·출력 61·전체 189토큰, 2,263ms입니다. 검증용 원문·시나리오는 보관 처리했고 실험 기록은 남겼습니다. [실배포 검증 기록](verification/2026-10-19-railway-live.json). 브라우저 실제 클릭과 로컬 PostgreSQL 17 Compose 실행은 검증하지 않았습니다.

아래는 이전 DB 개발 당시의 검사 기록입니다.

```bash
node --test tests/*.test.mjs
python -m unittest discover -s backend/tests -v
```

DB 통합 검사는 **테스트 전용 폐기 가능한 DB**를 `FINCODE_TEST_DATABASE_URL`로 지정해야 실행됩니다. 이 테스트는 매 사례마다 실험 및 종속 실행 테이블을 `TRUNCATE`합니다. 운영 DB 주소를 사용하지 마세요. 미지정이면 해당 11개만 건너뜁니다.

2026-10-14 검증: JavaScript 34개, Python 29개(실제 DB 통합 11개 포함), 총 63개 통과. DB는 PGlite 0.5.9의 PostgreSQL 18.6 WASM 엔진/TCP 어댑터, 모델 HTTP는 mock으로 검사했습니다. DB 자체를 mock으로 대체하지 않았습니다. TCP 어댑터의 세션 공유 제약 때문에 psycopg 자동 prepared statement는 비활성화했습니다. 일반 PostgreSQL에도 같은 설정이 적용됩니다.

DB 초기화 재실행·중복 seed, 선택 기준 요약, 버전 충돌/보관·과거 입력 보존, 직접/간접 반복 실행, 중복 요청, 사용량 누락/실패 소비량, 규칙/수동 판정, 중단 복구와 인증 경계를 확인했습니다. Docker 엔진과 지원되는 브라우저 QA 도구가 없어 **Docker/PostgreSQL 17 컨테이너 실행, 브라우저 실제 클릭, 새 코드의 실모델 호출·라이브 배포는 미검증**입니다. Worker 프록시는 Node mock 회귀 검사로 검증했습니다. 기존 workerd 테스트는 호스팅 의존성이 없어 이번 작업에서는 건너뛰었습니다. 예전 단일 모델 라이브 검증 기록은 이번 DB 통합 검증과 별도입니다.

## 백업

```bash
docker compose exec -T postgres pg_dump -U fincode -d fincode -Fc > fincode-backup.dump
```

복구는 별도의 빈 DB에서 `pg_restore`로 먼저 확인하세요. UI JSON 내보내기는 선택 데이터·실험의 검토용이며 전체 DB 백업을 대신하지 않습니다.
