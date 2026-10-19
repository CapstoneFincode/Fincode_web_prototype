# 팀 공용 DB·백엔드 배포

## 선택한 구성

- 기존 Sites 프론트엔드: https://fincode-security-lab.tinyhawk128.chatgpt.site
- Railway 프로젝트 `fincode-team` / 환경 `production`에 PostgreSQL과 Python FastAPI를 함께 배치한다.
- 서비스 이름: `fincode-postgres`, `fincode-api`. 이름은 배포 계획이며 실제 리소스 생성 여부는 아래 상태를 확인한다.
- 소스: `CapstoneFincode/Fincode_web_prototype`의 `database` 브랜치. 기존 `main`은 변경하지 않는다.
- PostgreSQL은 Railway 내부 연결만 사용한다. 외부 TCP 프록시를 만들지 않는다.
- 사이트 Worker가 HTTPS로 API를 호출하고 API가 내부 주소로 DB에 접근한다. DB 주소·모델 키·서버 인증키는 브라우저에 전달하지 않는다.
- API는 1개 프로세스/1개 복제본으로 상시 실행한다. 실험 큐 처리가 필요하므로 Serverless/자동 휴면은 끈다. DB와 API는 같은 리전에 배치한다.

## 현재 상태 — 2026-10-19

배포 설정과 연결 코드는 준비했다. Railway 플러그인 설치는 확인됐지만 현재 대화에 Railway 실행 도구가 제공되지 않아 프로젝트·DB·백엔드 생성과 실제 연결은 아직 완료하지 못했다. 실제 서버 URL이나 DB 접속 문자열을 임의로 만들지 않았다.

## Railway 설정

`.railway/railway.ts`는 현재 공식 IaC 형식으로 DB와 API, 브랜치, 환경변수 참조, readiness 경로를 선언한다. Railway CLI가 평가하는 파일이며 프론트엔드 TypeScript 빌드에서는 제외한다. 레거시 `railway.toml`/`railway.json`과 혼용하지 않는다. 실제 계정에 대해 `railway config plan`을 검증한 결과는 아직 없다.

먼저 대상 환경에 다음 **공유 비밀 변수**를 등록한다. IaC는 해당 값을 읽지 않고 참조한다.

| 변수 | 값 / 용도 |
| --- | --- |
| `BACKEND_SHARED_TOKEN` | 새 난수 인증키. `secrets.token_urlsafe(48)` 수준의 난수, 32~256자, 공백 없는 ASCII. Sites에도 같은 값 설정 |
| `BAZE_API_KEY` | 사용자가 제공한 프로젝트용 모델 게이트웨이 키 |

API 환경변수는 IaC가 연결한다.

| 변수 | 설정 |
| --- | --- |
| `FINCODE_ENV` | `production` |
| `DATABASE_URL` | `fincode-postgres`의 내부 `DATABASE_URL` 참조 |
| `BACKEND_SHARED_TOKEN` | 환경 공유 비밀 변수 참조 |
| `BAZE_API_KEY` | 환경 공유 비밀 변수 참조 |
| `PORT` | Railway가 주입. 로컬 기본값 8000 |

루트 Dockerfile을 빌드한다. 시작 명령 `python -m backend.start`는 환경 검증 → migration → 중복 없는 sample seed → uvicorn 순으로 실행한다. DB 연결 또는 migration이 실패하면 HTTP 서버를 시작하지 않는다. 배포 확인 경로는 `/api/ready`, 확인 대기 300초다. `/api/ready`는 DB가 준비되어야 200을 반환한다. 모델 키가 없어도 DB 데이터 관리는 가능하며 실제 모델 호출은 별도 오류로 안내한다.

배포 시 확인할 값:

- 복제본 1, 자동 휴면 OFF, API와 DB 동일 리전.
- Dockerfile 자동 인식 여부, 공개 API HTTPS 도메인과 대상 `PORT` 일치.
- PostgreSQL의 영구 볼륨과 제공되는 백업 기능. 유료 옵션·요금제 변경은 비용 확인 후 진행한다.
- 기존 프로젝트가 있으면 먼저 내용을 확인하고 재사용한다. 선언 이름만 보고 중복 프로젝트를 만들지 않는다.

## 기존 사이트 연결 순서

1. API 배포에서 `/api/ready` 200, 인증 없는 `/api/data` 401을 확인한다.
2. 인증된 `/api/health`에서 `database.ready=true`, `/api/data`에서 seed 조회를 확인한다.
3. Sites의 **서버 환경변수**에 `BACKEND_API_URL`(생성된 HTTPS 루트 주소)과 같은 `BACKEND_SHARED_TOKEN`을 설정한다. 기존 `BAZE_API_KEY`는 유지한다.
4. 기존 Sites 프로젝트 `appgprj_6acc6daad91c8191931bddebb0cc597a`에 `database` 소스를 빌드·게시한다. 소유자 전용 접근 범위를 유지한다.
5. 사이트의 정상 이메일을 추가 → 새로고침 후 조회 → 수정 → 보관 처리하고, 저장된 실험을 조회한다. 실모델 테스트는 별도 합성 입력으로 1회 수행해 결과·토큰·시간을 확인한다.

사이트 연결 변수가 둘 다 없으면 기존 단건 요약/브라우저 모드를 유지하고 DB 미연결을 표시한다. 일부만 설정되거나 백엔드 장애가 나면 503으로 표시하며 DB 기록 없는 모델 호출로 자동 전환하지 않는다. 기존 브라우저 데이터를 공용 DB로 자동 업로드하지 않는다.

## 팀 사용과 운영

연결 후 웹의 **데이터 관리**에서 정상 이메일과 공격 시나리오를 등록·수정한다. **공격 실험**은 DB 시나리오를 자체 실행하고 **실험 결과**에 응답·사용량·시간·판정을 저장한다. 정상 이메일 기준 요약은 선택 사항이며 목적은 추후 방어 ON에서의 정상 입력 오차단/응답 거부 측정이다. 현재 방어 실행은 OFF만 지원한다.

팀원이 웹을 사용하려면 사이트 접근 권한을 별도로 부여해야 한다. Railway DB 공용화가 사이트 공유를 자동으로 바꾸지는 않는다. Railway 관리 콘솔의 팀원 권한도 별개다. 대상 계정이 확인되기 전 사이트를 전체 공개하지 않는다.

백업·복원 SQL 작업은 [DATABASE.md](DATABASE.md)를 따른다. 운영 DB에 `backend.tests.test_database`를 실행하지 않는다. 해당 통합 테스트는 폐기 가능한 테스트 DB의 실험 테이블을 초기화한다. 모델 호출은 자동 재시도하지 않으며 재배포 중 중단된 작업은 만료 후 별도 상태로 남는다.

## 확인한 공식 문서

2026-10-19 기준: [PostgreSQL](https://docs.railway.com/databases/postgresql), [내부 네트워크](https://docs.railway.com/networking/private-networking), [IaC](https://docs.railway.com/infrastructure-as-code/reference), [배포 점검](https://docs.railway.com/deployments/healthchecks), [요금](https://docs.railway.com/pricing/plans).

Hobby는 월 5달러 구독에 5달러의 리소스 사용량을 포함하며 초과 사용은 추가 과금된다. 월 총액 5달러 고정 상품이 아니다. 신규 Trial/잔여 크레딧과 실제 계정 요금은 계정 조회 후 확인한다. 이번 준비 작업에서 유료 구독이나 자원을 생성하지 않았다.
