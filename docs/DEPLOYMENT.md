# 현재 운영 배포: 공격 성공 판정·카테고리별 결과 — 2026-11-04

아래 이전 기록보다 이 절을 우선한다. 기능 커밋 `7d62fa55a83f55e3f3900a874103a57a8ff996fd`를 배포했다.

- Railway `fincode-api`/`production`은 위 main 커밋에 고정. 배포 `f6e4698c-e6b0-43f9-ba85-6eb8e489a89b` SUCCESS. staged 변경 없음. 기존 PostgreSQL, 환경변수, 자원 및 도메인 설정 유지.
- 운영 사이트: https://fincode-security-lab.xogus090721.chatgpt.site. 소스 `a37a73b81070723adc7230e7e2ffa474a765b914`, 배포 `appgdep_6aeae07a717c8191ad6f569626d044e5` succeeded. 소유자 전용 접근 및 환경 revision 2 유지.
- 새 migration은 없다. 정상 5건/공격 9건 및 기존 실험 24개/실행 38개의 기존 저장 필드를 배포 전후 대조했다. API 준비 상태, DB 연결, 4개 판정 방식 지원, 운영 정적 파일 7개 일치를 확인했다. 실모델 호출과 로그인 후 브라우저 클릭 검증은 하지 않았다.
- 후속 문서 커밋은 자동 배포하지 않는다. 다음 배포도 검증한 main 커밋으로 고정값을 갱신하고 백엔드 → 지원 기능 조회 → 프론트엔드 순서로 적용한다. 상세: HANDOFF.md 및 `docs/verification/2026-11-04-attack-release.json`.

---

# 이전 운영 전환 — 2026-11-04

이 절이 아래 10월 기록보다 우선한다. 이후 개발 브랜치는 main이다.

- GitHub PR #2로 UI를 main에 병합했다. 기능 배포 커밋: `a75bc6051ba70c6829aae2f06a91683ee25d8db8`.
- Railway API 소스는 main의 위 커밋에 고정했다. 배포 `5e6d4180-df76-413c-bb48-7231b5112cbc` SUCCESS. 운영 API는 이후 문서 커밋만으로 자동 재배포되지 않는다. 다음 기능 수정은 검증한 main 커밋으로 고정값을 갱신한다.
- 시작 시 추가 migration 003/004를 적용한다. 001~003 기존 SQL 원문은 수정하지 않았다. 새 category scope 조회와 readiness를 확인했고 사용자 공격 2건 분류 및 운영 웹 갱신을 완료했다.
- 변경 전 데이터 및 전체 API 실험 상세 스냅샷: 정상 5건, 공격 9건, 실험 11개/실행 19개. 진행 중 run 없음. 이 스냅샷은 API 데이터 보관이며 전체 PostgreSQL 물리 백업은 아니다.
- 운영 웹: https://fincode-security-lab.xogus090721.chatgpt.site. Sites 소스 `76d24817da84be7b0e343624ebc03b30c96cf351`, 배포 `appgdep_6aeaba96e0ac8191b841319de8869368` succeeded. 접근은 소유자 전용이며 환경 revision 2를 유지한다.
- 공격 9건/정상 5건 유지, 미분류 0건. 과거 실험 11개/실행 19개 상세가 변경 전과 동일함을 확인했다. 나머지 검증과 다음 작업은 HANDOFF.md 최신 절 참고.

---

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
