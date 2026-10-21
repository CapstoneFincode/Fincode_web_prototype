# 핀코드 · LLM Security Lab

> **UI 브랜치 · 2026-10-21:** 공격 카테고리별 개수 선택·필터·등록 개선판입니다. 카테고리마다 등록 순서대로 선택해 각 1회 실행합니다. [검토용 프로토타입](https://fincode-ui-categories.tinyhawk128.chatgpt.site)과 [변경·DB 적용 안내](docs/UI-CATEGORIES.md)를 참고하세요. 검토용 화면은 브라우저에만 저장하며 모델을 호출하지 않습니다. 아래 운영 배포 기록은 10월 19일 기준입니다.

한림대학교 캡스톤디자인 **LLM 프롬프트 인젝션 공격·방어 실험 플랫폼**입니다. 이 `database` 브랜치는 PostgreSQL 데이터 관리와 자체 공격 실행·결과 저장을 연결합니다. BAZE/HAI-GPT로 모델을 호출하며 현재 실행 방어는 OFF입니다.

- 팀 저장소: https://github.com/CapstoneFincode/Fincode_web_prototype
- 비공개 웹: https://fincode-security-lab.tinyhawk128.chatgpt.site
- DB 구조·API·호스팅 연결: [docs/DATABASE.md](docs/DATABASE.md)
- 팀 공용 Railway 배포 설정·진행 상태: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- 인수인계: [docs/HANDOFF.md](docs/HANDOFF.md)

## 현재 기능과 연결 조건

**2026-10-19 공용 DB 연결 완료:** Railway PostgreSQL 18·Python 백엔드를 기존 웹에 연결했습니다. 정상 이메일/공격 시나리오 저장·조회·수정·보관과 실제 모델 실행 결과의 DB 재조회를 확인했습니다. [데이터 관리](https://fincode-security-lab.tinyhawk128.chatgpt.site/#data)에서 바로 등록할 수 있습니다. 사이트는 소유자 전용이며 팀원 접근 권한은 별도입니다.

검증용 정상 이메일 1건을 `gemini-3.5-flash-lite`로 실행해 입력 128·출력 61·전체 189토큰, 응답시간 2,263ms와 실제 응답을 저장했습니다. 방어 OFF의 연결 확인이며 보안 효과나 성능 벤치마크가 아닙니다. [실배포 검증 기록](docs/verification/2026-10-19-railway-live.json)을 참고하세요.

실제 서버 API, 모델 목록 조회, 본문 요약, 입력·출력·전체·추론 토큰과 응답시간 표시를 구현했습니다. **유효한 서버 환경변수 `BAZE_API_KEY`가 있어야 실제 모델을 호출할 수 있습니다.** 키 미등록 상태나 모의 응답 테스트 통과를 실모델 성공으로 표시하지 않습니다.

2026-10-14 13:53 KST, 비공개 호스팅 API에서 모델 목록 60개 조회와 `gemini-3.5-flash-lite`의 합성 정상 이메일 `GT-S01` 1건 요약을 확인했습니다. 입력 153·출력 114·전체 267토큰, 응답시간 2,144ms였으며 필수 정보 4개가 포함되었습니다. 추론 토큰은 미제공(`null`)입니다. 단일 호출의 연결 검증이며 전체 모델 검증이나 성능 벤치마크는 아닙니다. [검증 기록](docs/verification/2026-10-14-baze-live.json)을 참고하세요.

[이메일 요약기](https://fincode-security-lab.tinyhawk128.chatgpt.site/#email)에서 연결 상태 확인 → 실제 모델 선택 → 정상 예시 불러오기 또는 본문 입력 → 실제 요약 실행 순으로 사용합니다. 저장된 예시 요약은 별도 버튼이며 실제 생성 결과와 구분합니다. 실제 결과는 입력·모델·프롬프트 버전·사용량과 함께 JSON으로 내보낼 수 있습니다. 브라우저에는 키를 입력하거나 저장하지 않습니다.

DB 설정 시 정상 이메일·공격 시나리오 CRUD, 수정 버전 보관, 자체 공격 반복 실행, 정확 일치 자동 판정·수동 검토, 응답·토큰·시간 기록을 제공합니다. 정상 이메일은 기준 요약 없이 등록할 수 있습니다. 기존 브라우저 계획·메모와 과거 실험 집계도 유지합니다. 보안 방어 실행과 첨부파일 추출·OCR는 구현 전입니다. 파일명 선택은 가능하지만 파일이 있으면 실제 요약을 실행하지 않습니다. 현재 실호출은 **방어 미적용 베이스라인**만 지원합니다.

## 빠른 실행: Python + Docker

```bash
git clone --branch database https://github.com/CapstoneFincode/Fincode_web_prototype.git
cd Fincode_web_prototype
cp .env.example .env
```

Windows PowerShell에서는 마지막 명령 대신 `Copy-Item .env.example .env`를 사용합니다. `.env`에 PostgreSQL 시작에 필요한 `POSTGRES_PASSWORD`를 설정하고, `BAZE_API_KEY=` 뒤에 **이 게이트웨이에서 발급받은 키**를 입력합니다.

```bash
docker compose up --build -d
```

http://localhost:8000 에서 사용합니다. 키 변경 후에는 `docker compose up -d --force-recreate`, 종료는 `docker compose down`입니다. 키가 없어도 UI·DB 데이터 관리·`/api/health`는 작동하며 실제 모델 호출만 503 오류로 안내합니다. DB migration과 seed는 앱 시작 시 자동 적용됩니다. `docker compose down` 후에도 데이터 볼륨은 유지됩니다.

기본 포트는 `127.0.0.1`에만 열립니다. Docker 버전에는 로그인 기능이 없으므로 외부 공개 시 인증과 사용량 제한을 별도로 구성해야 합니다. 실제 키가 있는 `.env`는 Git과 이미지에 포함되지 않습니다.

### Docker 없이 실행

Python 3.11 이상을 사용합니다.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# DB 연결 시 먼저 DATABASE_URL 및 PGPASSWORD를 설정하고 실행:
python -m backend.migrate
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

실행 전에 현재 터미널의 `BAZE_API_KEY`와 DB 연결용 `DATABASE_URL` 환경변수를 설정합니다. DB 미설정이면 migration을 생략하고 기존 단건 요약/브라우저 모드로 사용할 수 있습니다. 일반 uvicorn은 `.env`를 자동으로 읽지 않으며 위 Docker Compose 경로가 `.env`를 사용합니다.

## API 계약

| 경로 | 기능 |
| --- | --- |
| `GET /api/health` | 키 등록 여부와 구현 기능 확인. 유효성 검증이나 모델 호출은 하지 않음 |
| `GET /api/models` | 게이트웨이의 현재 텍스트 모델 목록. Chat Completions 미지원 Codex 계열 제외 |
| `POST /api/summarize` | 본문 요약과 실제 usage·latency 반환, DB 설정 시 실행 저장 |
| `/api/data` | 정상 이메일·공격 시나리오 관리 |
| `/api/experiments` | 자체 실험 등록·진행률·상세 조회 |
| `POST /api/runs/{id}/evaluations` | 수동 판정 추가 |

변경 메서드·요청 예시는 [DB API 문서](docs/DATABASE.md#api)에 있습니다.

```json
{
  "model": "모델 목록에서 선택한 id",
  "email": {"subject": "회의 안내", "body": "오늘 오후 5시 연구실에서 만납시다."},
  "defense": "off",
  "attachments": []
}
```

성공 응답: `mode: "live"`, `summary`, `model`, `requestedModel`, `requestId`, `createdAt`, `promptVersion`, `defense`, `usage`, `latencyMs`, `finishReason`, `warnings`. 사용량 필드: `inputTokens`, `outputTokens`, `totalTokens`, `reasoningTokens`. 오류는 `{ "error": { "code": "...", "message": "..." } }` 형식이며 빈 모델 응답에도 전달받은 사용량이 있으면 반환합니다.

- 토큰은 게이트웨이 응답값입니다. 누락값은 `null`/`—`이며 추정값이나 0으로 채우지 않습니다.
- 응답시간은 서버에서 게이트웨이 요청 시작부터 전체 JSON 응답을 읽은 시점까지입니다. TTFT·브라우저 왕복시간·모델 내부 연산시간과 다릅니다.
- 최대 출력 예산은 16,384 토큰이며 추론 모델의 내부 추론도 포함될 수 있습니다. 실제 사용량과 최대 예산은 다릅니다.
- 모델 목록은 최대 20초, 요약은 최대 120초 대기합니다. 자동 재시도하지 않습니다. 취소·타임아웃이 이미 시작된 호출의 처리나 사용량을 취소한다고 보장하지 않습니다.
- 본문 최대 30,000자, 제목 200자, 요청 200 KB입니다. 원문은 입력 탐지 필터 없이 기본 요약 프롬프트와 함께 보냅니다. 모델 제공자의 자체 정책은 유지됩니다.
- 실제 실행 시 제목·본문이 BAZE 및 선택 모델 제공자에게 전송됩니다. DB 설정 시 원문 스냅샷·응답·측정값을 PostgreSQL에 저장합니다. 기존 브라우저 데이터는 자동 업로드하지 않습니다.

## 두 실행 환경

1. **팀 개발·Docker:** `backend/app.py`의 Python FastAPI가 `public/`와 API를 함께 제공합니다. Node 설치나 웹 빌드 없이 실행할 수 있습니다.
2. **기존 비공개 웹과 호스팅 어댑터:** `server/baze.mjs`의 JavaScript Worker가 같은 API 계약을 제공합니다. 두 환경은 `config/email-baseline.json`의 프롬프트와 설정을 공유합니다. 호스팅에는 Vinext/Cloudflare의 `sites()` 빌드 통합을 유지합니다. 키는 서버의 비밀 환경변수로 등록합니다. DB 모드는 외부 HTTPS Python 백엔드의 `BACKEND_API_URL`·`BACKEND_SHARED_TOKEN`을 Worker에 설정해 같은 DB API로 연결합니다. 현재 운영 웹은 Railway PostgreSQL/Python에 연결되어 있습니다. 이후 브랜치 업로드만으로 Sites가 바뀌지는 않습니다. Railway API도 검증한 커밋에 고정되어 있어 명시적으로 소스를 갱신해 배포합니다.

사이트와 GitHub는 자동 동기화되지 않습니다. 사이트는 기존 소유자 전용이며 GitHub Organization 멤버십과 별개입니다. GitHub Pages 정적 호스팅만으로는 비밀 키가 필요한 서버 API가 실행되지 않습니다.

호스팅 보조 소스도 보관합니다. Node 22.13 이상과 `package.json`에 고정한 pnpm을 사용합니다. `build/`, `scripts/`, `lib/`, `vite.config.ts`, `.openai/hosting.json`은 이 호스팅용입니다. 팀의 기본 실행·재현 경로는 위 Python/Docker입니다.

## 검사

```bash
node --check public/app.js
node --test tests/*.test.mjs
python -m unittest discover -s backend/tests -v
```

배포 준비에서 JavaScript 34개 + Python 34개(실제 DB 통합 11개 포함) + workerd 1개, 총 69개 통과했습니다. 모델 HTTP는 mock, DB는 PGlite의 PostgreSQL 18.6 엔진/TCP 어댑터로 검사했습니다. DB 통합 테스트는 `FINCODE_TEST_DATABASE_URL`을 폐기 가능한 전용 DB로 지정해야 하며 해당 DB의 실험 테이블을 초기화합니다. 미지정 시 DB 11개는 건너뜁니다.

검증 범위와 테스트 실행법은 [DB 문서](docs/DATABASE.md#검증)에 있습니다. 2026-10-19 Railway에서 실제 Dockerfile 빌드·PostgreSQL 18 배포와 사이트 경유 실모델·DB 저장을 확인했습니다. 로컬 Compose의 PostgreSQL 17 컨테이너와 브라우저 실제 클릭 검증은 미수행입니다.

## 데이터 해석과 다음 단계

- `public/data.js`의 기존 실험은 **2026-09-30 발표자료의 과거 집계**입니다. 현재 웹에서 재실행한 수치가 아닙니다.
- DeepSeek 확장 원본 ASR은 30/49 = 61.22%. 팀 이견 4건 제외 시 26/49 = 53.06%는 재검토 추정치입니다.
- 합성 정상 이메일 5건과 공격 예시 7건은 정상 라벨 검토 완료 수에 포함하지 않습니다. 직접 등록 후 정상 라벨을 검토한 이메일만 목표 100건 진행 수에 포함하며, 기준 요약은 선택 사항입니다.
- DB 연결 시 팀 데이터와 실행 기록은 PostgreSQL에 저장됩니다. 미연결 시 브라우저 데이터는 `fincode-lab-v1`에 저장합니다. 계획·메모는 항상 브라우저에 남습니다.
- 다음 단계: 정상·공격 실데이터 확충 → 방어 2종 이상 구현 → 동일 조건 OFF/ON 실행 → ASR·정상 오차단·시간/토큰 비교. 첨부 추출과 선택 요약 품질 평가는 후속 확장입니다.

## 공식 연동 자료

- https://docs.mindlogic.ai/agent-setup/prompt.md
- https://docs.mindlogic.ai/openapi.json
- https://docs.mindlogic.ai/docs/general/api-gateway/reference/chat-completions.md

게이트웨이는 `https://factchat-cloud.mindlogic.ai/v1/gateway`로 고정합니다. 코딩 에이전트 자체의 모델 제공자나 전역 설정은 이 앱 통합과 별개이며 변경하지 않았습니다.
