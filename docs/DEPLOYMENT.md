# 팀 공용 DB·백엔드 배포

## 운영 상태 — 2026-10-19

Railway의 PostgreSQL과 Python FastAPI를 배포하고 기존 Sites 웹에 연결했다. 사이트 `/api/health`에서 `database.configured=true`, `database.ready=true`이며 모델 목록 60개와 정상 이메일 5건·공격 예시 7건을 조회했다. 사이트의 현재 접근 범위는 소유자 전용이다.

| 구성 | 실제 배포 |
| --- | --- |
| 사용자 웹 | https://fincode-security-lab.tinyhawk128.chatgpt.site |
| 백엔드 HTTPS | https://fincode-api-production.up.railway.app |
| Railway 콘솔 | https://railway.com/project/59ec5ab2-d868-4807-8a67-38608120960f |
| 계정 / 프로젝트 / 환경 | `cobas67` / `fincode-team` / `production` |
| API 서비스 | `fincode-api`, ID `fc696122-c41d-4923-b3b9-54a6873ad047` |
| DB 서비스 | `Postgres`, ID `d44514bb-6184-4d50-aea2-e2a8babc1d5c` |
| DB 엔진 / 데이터베이스 | 공식 `postgres` 템플릿, PostgreSQL 18 / `fincode` |
| DB 영구 저장 | `postgres-volume`, 500 MB, `/var/lib/postgresql/data` |
| 실행 위치 | API와 DB 모두 `iad`, 서비스별 복제본 1개 |
| 자원 상한 | 서비스별 CPU 1개·메모리 500 MB, 자동 휴면 OFF |

웹 Worker → HTTPS API → Railway 내부 PostgreSQL 순서로 연결한다. DB는 공개 도메인과 TCP 프록시가 없다. 브라우저에 DB 주소·모델 키·서버 인증키를 전달하지 않는다. 인증 없는 `/api/data`는 401, 인증된 요청은 200이다. `/api/health`와 `/api/ready`만 비밀 없는 상태 확인용으로 공개한다.

## 배포 버전

- API 소스: `CapstoneFincode/Fincode_web_prototype`, `database` 브랜치의 `152311baa3c7d36e08dc54cb1704bfb0ac763f48`에 고정.
- API 배포: `c557c7b6-00b2-463c-837a-20f6bd28facf`, **SUCCESS**.
- DB 배포: `e0e6e5fe-0ee1-4f8a-ad94-572b30418d22`, **SUCCESS**.
- Sites 버전 6: 소스 `3f685c6928bf0447edae5299ec42949d71d0bb01`.
- DB 연결을 적용한 Sites 배포: `appgdep_6ad59d9fbd188191a44c4b7926a904e7`, 환경변수 revision **2**, **succeeded**.

GitHub의 후속 문서 커밋은 API에 자동 반영되지 않는다. 다음 코드 배포는 새 커밋을 검토한 뒤 Railway 소스의 고정 커밋을 명시적으로 갱신한다. Sites 소스/배포도 별도다. `main`은 변경하지 않았다.

실제 자원은 Railway 플러그인으로 생성·검증했다. `.railway/railway.ts`는 참고 선언이며 실제 자원 한도·볼륨·도메인·고정 커밋 전체를 담지 않는다. IaC로 전환하려면 기존 프로젝트/환경을 연결해 현재 설정을 먼저 가져오고 계획을 검토한다. 이 파일을 그대로 적용해 운영 자원을 재생성하지 않는다.

## 서버 환경변수와 실행

Railway 환경의 공유 비밀 변수에 `BACKEND_SHARED_TOKEN`과 `BAZE_API_KEY`를 등록했다. 실제 값은 저장소에 넣지 않는다.

| API 변수 | 설정 |
| --- | --- |
| `FINCODE_ENV` | `production` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — 내부 연결 참조 |
| `BACKEND_SHARED_TOKEN` | `${{shared.BACKEND_SHARED_TOKEN}}` |
| `BAZE_API_KEY` | `${{shared.BAZE_API_KEY}}` |
| `PORT` | `8000` — 공개 도메인의 대상 포트와 일치 |

Sites 서버 환경에는 `BACKEND_API_URL=https://fincode-api-production.up.railway.app`과 동일한 비밀 `BACKEND_SHARED_TOKEN`을 설정했다. 기존 `BAZE_API_KEY`도 유지한다. 환경변수를 바꾸면 저장된 Sites 버전을 재배포해야 적용된다. 키 회전은 Railway와 Sites 양쪽을 함께 갱신한다.

루트 Dockerfile을 빌드한다. `python -m backend.start`가 배포 환경 검증 → migration → 중복 없는 sample seed → uvicorn 단일 프로세스 순서로 실행한다. DB 또는 migration 오류가 나면 HTTP 서버를 시작하지 않는다. `/api/ready`는 DB 준비 시 200, 실패 시 503이다. readiness 대기 300초, 종료 대기 135초를 설정했다.

사이트 연결 변수가 모두 없으면 기존 단건 요약/브라우저 모드다. 일부만 설정되거나 백엔드 장애가 생기면 503을 반환하며 DB 기록 없는 모델 호출로 전환하지 않는다. 기존 브라우저 데이터를 자동 업로드하지 않는다.

## 웹에서 관리하기

1. [데이터 관리](https://fincode-security-lab.tinyhawk128.chatgpt.site/#data)에서 정상 이메일 또는 공격 시나리오를 추가한다. 저장 후 다시 조회하면 공용 DB의 자료가 표시된다.
2. 정상 이메일에는 제목·원문을 입력한다. 정상 라벨 검토를 완료하면 목표 데이터 수에 반영한다. 기준 요약은 선택이다. 목적은 추후 보안 ON에서 정상 입력의 오차단·응답 거부를 확인하는 것이다.
3. [공격 실험](https://fincode-security-lab.tinyhawk128.chatgpt.site/#experiments)에서 모델·기준 이메일·DB 시나리오·반복 수를 정하고 실행한다. promptfoo 데이터나 실행 엔진을 사용하지 않는다.
4. [실험 결과](https://fincode-security-lab.tinyhawk128.chatgpt.site/#results)의 DB 실험 상세에서 응답·토큰·시간·판정 이력을 확인한다.

현재 방어 실행은 **OFF만 지원**한다. 서버 간 인증키는 이메일의 공격 여부를 검사하는 보안 Gateway가 아니다. ON/OFF 데이터 구조는 준비되어 있지만 보안 ON 실행과 오차단율 측정은 방어 구현 후 가능하다.

팀원이 웹을 사용하려면 대상 계정에 사이트 접근 권한을 부여해야 한다. Railway 관리자 권한도 별도다. DB 공용화가 사이트를 전체 공개하거나 팀원을 자동 초대하지 않는다. DB의 작성자 표기는 현재 `team`이며 개인별 감사 기록은 아니다.

## 검증과 운영

- 실제 사이트 경유 CRUD·실험 검증 결과: [2026-10-19-railway-live.json](verification/2026-10-19-railway-live.json).
- 기존 회귀 검사: JavaScript 34개 + Python 34개(실DB 11개 포함) + workerd 1개, 총 69개 통과. 이 검사의 모델 HTTP는 mock이다. 실제 모델 검증은 위 별도 기록을 따른다.
- 운영 DB에 `backend.tests.test_database`를 실행하지 않는다. 해당 테스트는 폐기 가능한 DB의 실험 테이블을 초기화한다.
- DB 볼륨은 배포와 별도로 유지된다. 자동 백업 일정과 복원은 아직 설정·검증하지 않았다. [DATABASE.md](DATABASE.md#백업)의 전체 DB 백업 절차를 참고한다. UI JSON 내보내기는 전체 백업을 대신하지 않는다.
- 모델 요청은 자동 재시도하지 않는다. 서버 중단으로 만료된 실행은 중단 상태로 기록하며 같은 실험을 무조건 다시 호출하지 않는다.
- 유료 요금제 변경은 수행하지 않았다. 현재 계정의 잔여 크레딧·요금제·청구액은 도구로 확인되지 않았다. 실행 중인 서비스의 사용량은 Railway Usage/Billing에서 확인한다. CPU·메모리 상한은 월 청구액 상한이 아니다.

공식 참고: [PostgreSQL](https://docs.railway.com/databases/postgresql), [내부 네트워크](https://docs.railway.com/networking/private-networking), [IaC](https://docs.railway.com/infrastructure-as-code/reference), [배포 점검](https://docs.railway.com/deployments/healthchecks), [요금](https://docs.railway.com/pricing/plans).
