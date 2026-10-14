# 핀코드 · LLM Security Lab

한림대학교 캡스톤디자인 **LLM 프롬프트 인젝션 공격·방어 실험 플랫폼**입니다. 현재는 이메일 본문 요약기를 BAZE/HAI-GPT 게이트웨이에 연결하는 단계입니다.

- 팀 저장소: https://github.com/CapstoneFincode/Fincode_web_prototype
- 비공개 웹: https://fincode-security-lab.tinyhawk128.chatgpt.site
- 인수인계: [docs/HANDOFF.md](docs/HANDOFF.md)

## 현재 기능과 연결 조건

실제 서버 API, 모델 목록 조회, 본문 요약, 입력·출력·전체·추론 토큰과 응답시간 표시를 구현했습니다. **유효한 서버 환경변수 `BAZE_API_KEY`가 있어야 실제 모델을 호출할 수 있습니다.** 키 미등록 상태나 모의 응답 테스트 통과를 실모델 성공으로 표시하지 않습니다.

이메일 요약기에서 연결 상태 새로고침 → 실제 모델 선택 → 본문 입력 → 실제 요약 실행 순으로 사용합니다. 저장된 예시 요약은 별도 버튼이며 실제 생성 결과와 구분합니다. 실제 결과는 입력·모델·프롬프트 버전·사용량과 함께 JSON으로 내보낼 수 있습니다. 브라우저에는 키를 입력하거나 저장하지 않습니다.

기존 데이터 관리, 미실행 공격 계획, 과거 실험 6건의 집계·CSV, JSON 백업 기능은 유지합니다. 공격 반복 실행, 자동 성공 판정, 방어 실행, 공용 DB, 첨부파일 추출·OCR는 아직 구현 전입니다. 파일명 선택은 가능하지만 파일이 있으면 실제 요약을 실행하지 않습니다. 현재 실호출은 **방어 미적용 베이스라인**만 지원합니다.

## 빠른 실행: Python + Docker

```bash
git clone https://github.com/CapstoneFincode/Fincode_web_prototype.git
cd Fincode_web_prototype
cp .env.example .env
```

Windows PowerShell에서는 마지막 명령 대신 `Copy-Item .env.example .env`를 사용합니다. `.env`의 `BAZE_API_KEY=` 뒤에 **이 게이트웨이에서 발급받은 키**를 입력합니다.

```bash
docker compose up --build -d
```

http://localhost:8000 에서 사용합니다. 키 변경 후에는 `docker compose up -d --force-recreate`, 종료는 `docker compose down`입니다. 키가 없어도 UI와 `/api/health`는 작동하며 실제 호출만 503 오류로 안내합니다.

기본 포트는 `127.0.0.1`에만 열립니다. Docker 버전에는 로그인 기능이 없으므로 외부 공개 시 인증과 사용량 제한을 별도로 구성해야 합니다. 실제 키가 있는 `.env`는 Git과 이미지에 포함되지 않습니다.

### Docker 없이 실행

Python 3.11 이상을 사용합니다.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

실행 전에 현재 터미널의 `BAZE_API_KEY` 환경변수를 설정해야 합니다. 일반 uvicorn은 `.env`를 자동으로 읽지 않으며 위 Docker Compose 경로가 `.env`를 사용합니다.

## API 계약

| 경로 | 기능 |
| --- | --- |
| `GET /api/health` | 키 등록 여부와 구현 기능 확인. 유효성 검증이나 모델 호출은 하지 않음 |
| `GET /api/models` | 게이트웨이의 현재 텍스트 모델 목록. Chat Completions 미지원 Codex 계열 제외 |
| `POST /api/summarize` | 본문 요약과 실제 usage·latency 반환 |

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
- 실제 실행 시 제목·본문이 BAZE 및 선택 모델 제공자에게 전송됩니다. 앱 서버에는 본문·응답 DB를 만들지 않았습니다. 기존 데이터 관리의 직접 등록 자료는 브라우저에 남습니다.

## 두 실행 환경

1. **팀 개발·Docker:** `backend/app.py`의 Python FastAPI가 `public/`와 API를 함께 제공합니다. Node 설치나 웹 빌드 없이 실행할 수 있습니다.
2. **현재 비공개 웹:** `server/baze.mjs`의 JavaScript Worker가 같은 API 계약을 제공합니다. 두 환경은 `config/email-baseline.json`의 프롬프트와 설정을 공유합니다. 호스팅에는 Vinext/Cloudflare의 `sites()` 빌드 통합을 유지합니다. 키는 호스팅 서버의 비밀 환경변수로 별도 등록합니다.

사이트와 GitHub는 자동 동기화되지 않습니다. 사이트는 기존 소유자 전용이며 GitHub Organization 멤버십과 별개입니다. GitHub Pages 정적 호스팅만으로는 비밀 키가 필요한 서버 API가 실행되지 않습니다.

호스팅 보조 소스도 보관합니다. Node 22.13 이상과 `package.json`에 고정한 pnpm을 사용합니다. `build/`, `scripts/`, `lib/`, `vite.config.ts`, `.openai/hosting.json`은 이 호스팅용입니다. 팀의 기본 실행·재현 경로는 위 Python/Docker입니다.

## 검사

```bash
node --check public/app.js
node --test tests/*.test.mjs
python -m unittest discover -s backend/tests -v
```

모의 게이트웨이 테스트는 요청 매핑, 토큰 누락·빈 응답, 인증 오류, 입력 제한, 미지원 기능, 타임아웃과 UI 상태 전환을 확인합니다. 키를 사용하지 않는 테스트이며 실모델 검증을 대체하지 않습니다. 실제 검증은 모델 목록 성공 후 합성 정상 이메일 1건으로 수행합니다.

이번 환경에는 지원되는 브라우저 QA 도구와 Docker 엔진이 없어 시각·실제 클릭 검사와 Docker 이미지 실행 검증을 수행하지 못했습니다. 빌드·단위/API 검사의 확인 범위와 구분합니다.

## 데이터 해석과 다음 단계

- `public/data.js`의 기존 실험은 **2026-09-30 발표자료의 과거 집계**입니다. 현재 웹에서 재실행한 수치가 아닙니다.
- DeepSeek 확장 원본 ASR은 30/49 = 61.22%. 팀 이견 4건 제외 시 26/49 = 53.06%는 재검토 추정치입니다.
- 합성 정상 이메일 5건과 공격 예시 7건은 Ground Truth 완료 수에 포함하지 않습니다. 직접 등록 후 검토 완료 표시한 원문·기준 요약만 목표 100건 진행 수에 포함합니다.
- 브라우저 데이터는 `fincode-lab-v1`에 저장합니다. 팀 공용 DB가 아니므로 JSON으로 백업하세요.
- 다음 단계: 실키 연결 확인 → 정상·공격 소량 실험 → 공용 DB·첨부 추출 → 방어 2종 이상 → ASR/FPR·요약 품질 평가.

## 공식 연동 자료

- https://docs.mindlogic.ai/agent-setup/prompt.md
- https://docs.mindlogic.ai/openapi.json
- https://docs.mindlogic.ai/docs/general/api-gateway/reference/chat-completions.md

게이트웨이는 `https://factchat-cloud.mindlogic.ai/v1/gateway`로 고정합니다. 코딩 에이전트 자체의 모델 제공자나 전역 설정은 이 앱 통합과 별개이며 변경하지 않았습니다.
