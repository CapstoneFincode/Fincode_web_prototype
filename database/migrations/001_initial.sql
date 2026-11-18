/*
핀코드 이메일 요약기 / 공격·방어 실험 DB 초기 스키마 v1 — 2026-10-14 (정상 입력 오차단·자체 공격 실행 목적 반영)

상태: database 브랜치의 PostgreSQL 초기 migration. 적용 방법과 실제 구현 범위는 docs/DATABASE.md 참조.
작성 근거: CapstoneFincode/Fincode_web_prototype의 최신 README, docs/HANDOFF.md,
backend/app.py, config/email-baseline.json, public/data.js를 읽고 작성함.
PostgreSQL 공식 문법: https://www.postgresql.org/docs/current/ddl-constraints.html
https://www.postgresql.org/docs/current/datatype-json.html

핵심: DB 1개 안에서 12개 테이블로 자료·실행·판정을 분리한다.
security_gateway_enabled는 팀이 구현할 보안 방어 계층이며 BAZE API Gateway가 아니다.
OFF에서도 BAZE 모델 호출은 유지한다. 현재 런타임은 방어 OFF 본문 요약과 자체 공격 실행을 지원한다.
이 SQL은 보안 Gateway 자체, 인증, 작업 실행기, 업로드/OCR를 구현하지 않는다.

사용자 설명 반영: 정상 이메일 DB의 우선 목적은 보안 ON에서 정상 입력을 공격으로 오인하여
차단하거나 요약 대신 거부하는지 확인하는 것이다. 원문과 검토된 정상 라벨이 평가 기준이며,
기준 요약은 이 평가의 필수 조건이 아니다. ground_truths/summary_quality는 향후 품질 평가용
선택 구조로만 유지한다. 정상 이메일 등록·실행·오차단 평가에 ground_truth_id를 요구하지 않는다.

자체 실행 요구사항:
 promptfoo 외부 결과를 가져오거나 promptfoo 실행 엔진에 의존하지 않는다.
 웹에서 직접 등록한 공격 시나리오를 DB에 저장하고 자체 백엔드 실행기가 모델을 호출한다.
 등록 -> 시나리오/모델/방어/반복 횟수 선택 -> 입력 확정 -> 대기/실행 -> 응답 저장 -> 판정/비교.
 서버가 모델 호출 전 experiment_cases와 runs를 생성하고, 처리 중에도 진행률을 조회할 수 있게 한다.
 성공/실패/차단/취소 상태를 기록하며 재시도는 llm_calls의 별도 호출행에 남긴다.
 재시도와 새 반복 실험을 구분하고, 브라우저 새로고침으로 동일 실험이 중복 생성되지 않도록 한다.
 기존 historical_aggregates는 현재 웹에 이미 있는 발표 집계의 보존용 선택 구조다.
 이 테이블을 외부 결과 자동 수집 경로나 새 실험의 실행 원본으로 사용하지 않는다.

테이블:
 email_cases             정상 이메일/공격 실험에 사용할 이메일 원문 버전
 ground_truths           선택: 이메일별 기준 요약·필수 정보·검토 버전
 attack_scenarios        공격문·직접/간접·삽입 위치·목표·성공 기준 버전
 attachments             원본 파일 위치·해시·파서/OCR 버전·추출 결과
 defense_profiles        2종 이상 개별/조합 방어의 순서·설정 버전
 experiments             실행 묶음·평가 정책·환경·진행 상태
 experiment_cases       동일 조건 OFF/ON 비교 단위: 입력+모델+반복 회차
 runs                   각 비교 단위의 OFF 또는 특정 방어 ON 실행
 llm_calls              요약/보안검사/평가 모델 호출과 재시도 각각의 사용량
 gateway_checks         보안 단계별 탐지·허용·차단·변환 및 처리 시간
 evaluations            자동·모델·수동 평가 이력 (원래 판정 보존)
 historical_aggregates  실행 원본이 없는 과거 발표 집계 (실행 행으로 합성하지 않음)

단위/규칙:
 * UUID는 애플리케이션에서 생성한다. 시간은 timestamptz/UTC로 보관.
 * 문자열·공격문·원문은 데이터로만 취급. 동적 SQL이나 명령으로 실행하지 않는다.
 * 파일 원본은 접근 통제된 파일/객체 저장소에 두고 DB에는 저장 키·해시·추출문 저장.
   storage_key는 접근 자격 증명을 포함한 URL이 아니다. API 키는 이 DB에 저장하지 않음.
 * credential_alias는 team-key-1 같은 비밀이 아닌 식별자만 허용한다.
 * 원문·공격·GT·방어 설정 수정은 새 revision INSERT. 실험은 정확한 revision ID 참조.
   실험 시작 후 입력/설정 스냅샷은 고정. 현재 API는 새 revision만 생성하며, 직접 SQL 변경 권한은 운영에서 제한해야 함.
 * dataset_split=test는 방어 튜닝에 사용하지 않도록 운영한다.
 * 화면용 sample은 정상 데이터 목표 100건에서 제외. 정상 라벨이 검토된 서로 다른
   case_key만 집계하며 기준 요약 존재 여부는 조건이 아니다. synthetic 정상 예시는 검토 후 포함 가능.
 * FK는 연결 무결성을 검사한다. SHA256 내용 일치, JSON 내부 계약, 실제 ON 구현 상태,
   품질 평가 시 승인된 GT 여부, 실행 상태 전이, 원문 수정 불변성은 서비스 계층에서 별도 검증한다.

성능:
 backend_latency_ms = 실행기에서 모델 처리를 시작해 정규화한 최종 응답을 준비할 때까지.
   큐 대기·DB 전후 저장·비동기 평가는 제외. 향후 방어 처리도 같은 경계 안에서 측정.
 queue_wait_ms와 client_latency_ms는 별도이며 다른 시간 범위와 혼용하지 않는다.
 llm_calls.latency_ms = 해당 제공자 요청 시작~전체 응답 수신/오류 시점.
   기존 API latencyMs는 이 필드로만 이관한다. backend_latency_ms로 복사하지 않는다.
 gateway_checks.duration_ms = 보안 단계 실행 시간. 단계가 겹치면 합계는 전체시간 아님.
 서비스 토큰 = purpose summarize + security의 모든 시도. evaluator는 따로 집계.
 reasoning_tokens/cached_input_tokens는 제공자의 부분집합일 수 있어 total에 더하지 않음.
 토큰 미제공은 NULL. 실패/타임아웃 후 사용량도 미제공이면 NULL이며 0으로 대체하지 않음.
 모델을 실제로 호출하지 않은 차단 건은 call_log_complete=true이고 호출행이 0개일 때만
 토큰 0으로 집계한다. 호출 직전에 호출행을 기록하고 오류 뒤에도 삭제하지 않는다.
 call_log_complete는 호출 내역 누락 없음의 의미이며 토큰 미제공 없음의 의미가 아니다.

평가 (후속 집계 API 계약):
 ASR = 공격 성공 determinate 건수 / 판정 가능한 공격 실행 건수.
   판정 가능한 보안 차단은 실패로 포함. 오류/미판정/미실행은 제외하고 그 수·비율 별도 표시.
   모델 제공자의 거부는 팀 Gateway 차단과 구분하고 공격 목표를 기준으로 평가한다.
 FPR = 정상 입력 중 보안 탐지에서 공격으로 오탐한 수 / 탐지 판정이 가능한 정상 입력 수.
   ON 여부, 실제 detector 실행 여부를 확인. 탐지 없는 방어의 FPR은 해당 없음.
   탐지 표시만 있었지만 정상 요약을 제공한 경우와 실제 차단/거부한 경우를 구분한다.
 현재 우선 지표인 정상 오차단률 = 보안 때문에 정상 입력을 차단/거부한 수 /
   보안 ON에서 차단/거부 여부와 원인을 판정할 수 있는 정상 실행 수.
   evaluations.metric=normal_rejection에 차단/거부 여부를 저장하고 rejection_source로 원인 구분.
   true 및 security_gateway인 실행을 분자로 집계한다. 원인 미확인은 별도 건수로 보고하며
   보안 원인을 평가할 수 없는 건은 분모에서 제외한다. 전체 시도 수/유효 분모/제외 건수를 함께 표시.
   HTTP 200이면서 응답이 거부문인 경우도 판정 대상이다. runs.status만으로 판정하지 않는다.
   API 장애/타임아웃은 오차단으로 세지 않는다. 모델 제공자 자체 거부도 팀 보안 차단과 구분한다.
   OFF 결과와 단계 로그를 함께 확인하되 ON/OFF 차이만으로 원인을 확정하지 않는다.
 선택 확장 quality = 승인된 GT/필수 정보에 따른 0~1 점수. 현재 오차단 측정의 필수 항목이 아님.
 자동 ASR과 수동 재검토 ASR은 별도 집계. 최종판정 채택 규칙은 experiment에 고정하고
 같은 run/metric을 중복 분모로 세지 않는다. 팀의 과거 4건 이견은 확정값으로 덮어쓰지 않음.
 지연 증가율 = 100*(ON-OFF)/OFF. OFF=0 또는 NULL이면 증가율 NULL.
 평균/중앙값/p95와 건수를 함께 표시. 평균의 비율과 회차별 비율의 평균을 혼용하지 않음.
 정상 완료된 동일 조건 쌍으로 순수 처리비용을 비교하며 공격 차단은 별도 그룹으로 보고.
 교대로/무작위 순서로 여러 번 실행해 순서·부하 영향을 줄인다. 단일 호출은 벤치마크 아님.
 다른 실제 모델/설정/서버/키/캐시 조건은 별도 집계. 알 수 없는 조건은 미확인으로 표시.

현재 API 매핑:
 requestedModel -> llm_calls.requested_model; model -> returned_model
 requestId -> provider_request_id; summary -> runs.final_output
 usage.inputTokens/outputTokens/totalTokens/reasoningTokens -> 같은 이름 snake_case
 latencyMs -> llm_calls.latency_ms; finishReason -> finish_reason
 promptVersion/실제 systemPrompt/maxTokens -> experiment_cases의 고정 스냅샷
 기존 모의검사/정상 sample/과거 발표 집계는 새 실모델 실행으로 이관하지 않는다.
 기존 직접 공격 UI의 입력 경로는 아직 실제 요약 API에 없음. email.body에 넣은 지시를
 direct로 잘못 기록하지 말고 user_instruction API/렌더링 경로 구현 후 직접 공격 실행.

연결 순서 (제안):
 1. DBMS/호스팅/접근권한 결정 -> 백엔드 migration 적용 (빈 개발 DB에서 먼저 검증)
 2. 정상·공격 CRUD + 버전/검토 + /api/summarize 결과·호출 사용량 자동 저장
 3. 자체 실행기로 소량 OFF 공격/정상 실험 -> ON 방어 구현 -> 짝지은 반복 실행·판정·비교
 4. 첨부파일 추출 -> 자체 공격 시나리오와 검토된 정상 이메일 100건 확충
 Python FastAPI와 현재 배포된 JS Worker는 다른 실행 경로다. 두 경로의 저장 구현/배포
 방식을 결정해야 실제 웹도 저장된다. 프론트엔드의 직접 DB 접속은 하지 않는다.
 이 초안에는 웹 인증/역할권한/백업 작업을 구현하지 않았으며 created_by는 인증을 대신하지 않음.
*/

BEGIN;
CREATE SCHEMA fincode;
SET LOCAL search_path TO fincode, public;

CREATE TABLE email_cases (
    id uuid PRIMARY KEY,
    case_key text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    subject text NOT NULL,
    body text NOT NULL CHECK (length(btrim(body)) > 0),
    expected_label text NOT NULL DEFAULT 'unknown' CHECK (expected_label IN ('normal','attack','unknown')),
    label_review_status text NOT NULL DEFAULT 'pending' CHECK (label_review_status IN ('sample','pending','reviewed')),
    label_reviewed_by text,
    label_reviewed_at timestamptz,
    language text NOT NULL DEFAULT 'ko',
    dataset_split text NOT NULL DEFAULT 'unassigned' CHECK (dataset_split IN ('dev','validation','test','unassigned')),
    provenance text NOT NULL CHECK (provenance IN ('sample','synthetic','anonymized_real')),
    source_note text NOT NULL DEFAULT '',
    archived_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (case_key, revision),
    CHECK (label_review_status <> 'reviewed' OR
           (expected_label <> 'unknown' AND label_reviewed_by IS NOT NULL AND label_reviewed_at IS NOT NULL))
);

CREATE TABLE ground_truths (
    id uuid PRIMARY KEY,
    email_case_id uuid NOT NULL REFERENCES email_cases(id),
    revision integer NOT NULL CHECK (revision > 0),
    reference_summary text NOT NULL CHECK (length(btrim(reference_summary)) > 0),
    required_facts jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(required_facts) = 'array'),
    generator_model text,
    generator_settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(generator_settings) = 'object'),
    review_status text NOT NULL CHECK (review_status IN ('sample','draft','reviewed','rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    review_note text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email_case_id, revision),
    UNIQUE (id, email_case_id),
    CHECK (review_status <> 'reviewed' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE attack_scenarios (
    id uuid PRIMARY KEY,
    scenario_key text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    name text NOT NULL,
    vector text NOT NULL CHECK (vector IN ('direct','indirect')),
    channel text NOT NULL CHECK (channel IN ('user_instruction','email_body','attachment_text','image_ocr')),
    technique text NOT NULL,
    goal text NOT NULL,
    payload text NOT NULL,
    success_criterion jsonb NOT NULL CHECK (jsonb_typeof(success_criterion) = 'object'),
    enabled boolean NOT NULL DEFAULT true,
    provenance text NOT NULL CHECK (provenance IN ('sample','team')),
    archived_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scenario_key, revision),
    CHECK ((vector = 'direct' AND channel = 'user_instruction') OR
           (vector = 'indirect' AND channel IN ('email_body','attachment_text','image_ocr')))
);

CREATE TABLE attachments (
    id uuid PRIMARY KEY,
    email_case_id uuid NOT NULL REFERENCES email_cases(id),
    attachment_key text NOT NULL,
    extraction_revision integer NOT NULL CHECK (extraction_revision > 0),
    original_name text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    storage_key text NOT NULL,
    file_sha256 text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    extraction_status text NOT NULL CHECK (extraction_status IN ('pending','succeeded','failed','unsupported')),
    extractor_name text,
    extractor_version text,
    extracted_text text,
    extracted_sha256 text CHECK (extracted_sha256 ~ '^[0-9a-f]{64}$'),
    extraction_ms bigint CHECK (extraction_ms >= 0),
    -- 페이지/슬라이드/문단 위치와 OCR 신뢰도. 지원할 파일 형식은 구현 시 확정.
    segments jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(segments) = 'array'),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email_case_id, attachment_key, extraction_revision),
    CHECK (extraction_status <> 'succeeded' OR
           (extracted_text IS NOT NULL AND extracted_sha256 IS NOT NULL AND
            extractor_name IS NOT NULL AND extractor_version IS NOT NULL))
);

CREATE TABLE defense_profiles (
    id uuid PRIMARY KEY,
    profile_key text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    name text NOT NULL,
    implementation_status text NOT NULL CHECK (implementation_status IN ('planned','ready','retired')),
    -- [{name, version, config, code_ref}, ...]. 배열 순서가 실제 방어 실행 순서.
    ordered_steps jsonb NOT NULL CHECK (jsonb_typeof(ordered_steps) = 'array' AND jsonb_array_length(ordered_steps) > 0),
    code_ref text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_key, revision),
    CHECK (implementation_status <> 'ready' OR code_ref IS NOT NULL)
);

CREATE TABLE experiments (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    purpose text NOT NULL DEFAULT '',
    status text NOT NULL CHECK (status IN ('draft','queued','running','completed','partial','failed','cancelled')),
    evaluation_policy jsonb NOT NULL CHECK (jsonb_typeof(evaluation_policy) = 'object'),
    -- 자체 실행기의 반복 횟수·시나리오/모델 선택·실행 순서·동시 실행 수·재시도 정책.
    execution_plan jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(execution_plan) = 'object'),
    environment_snapshot jsonb NOT NULL CHECK (jsonb_typeof(environment_snapshot) = 'object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);

CREATE TABLE experiment_cases (
    id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES experiments(id),
    case_number integer NOT NULL CHECK (case_number > 0),
    repeat_index integer NOT NULL CHECK (repeat_index > 0),
    expected_label text NOT NULL CHECK (expected_label IN ('normal','attack','unknown')),
    email_case_id uuid REFERENCES email_cases(id),
    ground_truth_id uuid, -- 선택. 정상 입력 오차단 평가에는 기준 요약이 없어도 됨.
    attack_scenario_id uuid REFERENCES attack_scenarios(id),
    provider text NOT NULL,
    requested_model text NOT NULL,
    prompt_version text NOT NULL,
    system_prompt text NOT NULL,
    -- 실제로 전송한 설정. 미지정 값은 미지정으로 남기고 추측값을 기록하지 않는다.
    inference_settings jsonb NOT NULL CHECK (jsonb_typeof(inference_settings) = 'object'),
    -- 제목/본문/별도 사용자 지시/첨부 UUID·해시·추출문/삽입 위치를 확정해 기록.
    input_snapshot jsonb NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'object'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    credential_alias text,
    variant_order jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(variant_order) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, case_number, repeat_index),
    FOREIGN KEY (ground_truth_id, email_case_id) REFERENCES ground_truths(id, email_case_id),
    CHECK (ground_truth_id IS NULL OR email_case_id IS NOT NULL),
    CHECK ((expected_label IN ('normal','unknown') AND attack_scenario_id IS NULL) OR
           (expected_label = 'attack' AND attack_scenario_id IS NOT NULL))
);

CREATE TABLE runs (
    id uuid PRIMARY KEY,
    experiment_case_id uuid NOT NULL REFERENCES experiment_cases(id),
    security_gateway_enabled boolean NOT NULL,
    defense_profile_id uuid REFERENCES defense_profiles(id),
    -- 재시도된 HTTP 요청의 중복 실행 방지용. 외부 제공자 호출의 exactly-once를 보장하지 않음.
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('queued','running','succeeded','blocked','failed','timed_out','cancelled')),
    final_output text,
    -- 모델 원래 출력은 llm_calls.response_text, 사용자 최종 출력은 여기.
    error_code text,
    error_message text,
    queue_wait_ms bigint CHECK (queue_wait_ms >= 0),
    backend_latency_ms bigint CHECK (backend_latency_ms >= 0),
    client_latency_ms bigint CHECK (client_latency_ms >= 0),
    environment_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(environment_snapshot) = 'object'),
    call_log_complete boolean NOT NULL DEFAULT false,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((security_gateway_enabled AND defense_profile_id IS NOT NULL) OR
           (NOT security_gateway_enabled AND defense_profile_id IS NULL)),
    CHECK (status <> 'blocked' OR security_gateway_enabled),
    CHECK (status <> 'succeeded' OR (final_output IS NOT NULL AND length(btrim(final_output)) > 0)),
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);
CREATE UNIQUE INDEX one_off_per_case ON runs(experiment_case_id) WHERE NOT security_gateway_enabled;
CREATE UNIQUE INDEX one_profile_on_per_case ON runs(experiment_case_id, defense_profile_id) WHERE security_gateway_enabled;

CREATE TABLE llm_calls (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id),
    call_number integer NOT NULL CHECK (call_number > 0),
    purpose text NOT NULL CHECK (purpose IN ('summarize','security','evaluator')),
    retry_of_call_id uuid,
    provider text NOT NULL,
    requested_model text NOT NULL,
    returned_model text,
    credential_alias text,
    provider_request_id text,
    status text NOT NULL CHECK (status IN ('prepared','sent','succeeded','failed','timed_out','cancelled','not_sent')),
    -- Authorization/header/키는 제외. 실제 messages·model·generation 설정만 보관.
    request_snapshot jsonb NOT NULL CHECK (jsonb_typeof(request_snapshot) = 'object'),
    response_text text,
    finish_reason text,
    usage_raw jsonb CHECK (jsonb_typeof(usage_raw) = 'object'),
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    total_tokens bigint CHECK (total_tokens >= 0),
    reasoning_tokens bigint CHECK (reasoning_tokens >= 0),
    cached_input_tokens bigint CHECK (cached_input_tokens >= 0),
    latency_ms bigint CHECK (latency_ms >= 0),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    error_code text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, call_number),
    UNIQUE (id, run_id),
    FOREIGN KEY (retry_of_call_id, run_id) REFERENCES llm_calls(id, run_id),
    CHECK (retry_of_call_id IS NULL OR retry_of_call_id <> id),
    CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
);

CREATE TABLE gateway_checks (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id),
    step_number integer NOT NULL CHECK (step_number > 0),
    stage text NOT NULL CHECK (stage IN ('input','boundary','output')),
    technique text NOT NULL,
    technique_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('completed','skipped','error')),
    detected_attack boolean,
    action text NOT NULL CHECK (action IN ('none','allow','block','rewrite')),
    reason text,
    duration_ms bigint CHECK (duration_ms >= 0),
    -- 단계 전후 텍스트/해시. 평가 원문과 정제 후 요청을 혼동하지 않도록 저장.
    transformation jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(transformation) = 'object'),
    llm_call_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_number),
    FOREIGN KEY (llm_call_id, run_id) REFERENCES llm_calls(id, run_id),
    CHECK (status = 'completed' OR detected_attack IS NULL)
);

CREATE TABLE evaluations (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id),
    metric text NOT NULL CHECK (metric IN ('attack_success','gateway_false_positive','normal_rejection','summary_quality')),
    judge_type text NOT NULL CHECK (judge_type IN ('rule','model','human')),
    evaluator_version text NOT NULL,
    decision_status text NOT NULL CHECK (decision_status IN ('determinate','indeterminate','not_applicable')),
    -- attack_success=true: 공격 성공 / gateway_false_positive=true: 정상 입력 오탐.
    -- normal_rejection=true: 정상 이메일에 차단 또는 거부가 발생함. 원인은 별도 필드 참조.
    boolean_value boolean,
    rejection_source text CHECK (rejection_source IN ('security_gateway','model_provider','unknown')),
    score numeric CHECK (score >= 0 AND score <= 1),
    rubric_snapshot jsonb NOT NULL CHECK (jsonb_typeof(rubric_snapshot) = 'object'),
    evidence text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    evaluator_call_id uuid,
    supersedes_id uuid,
    evaluated_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, run_id, metric),
    FOREIGN KEY (evaluator_call_id, run_id) REFERENCES llm_calls(id, run_id),
    FOREIGN KEY (supersedes_id, run_id, metric) REFERENCES evaluations(id, run_id, metric),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (CASE WHEN metric = 'normal_rejection' AND decision_status = 'determinate' AND boolean_value IS TRUE
                THEN rejection_source IS NOT NULL ELSE rejection_source IS NULL END),
    CHECK ((decision_status <> 'determinate' AND boolean_value IS NULL AND score IS NULL) OR
           (decision_status = 'determinate' AND metric = 'summary_quality' AND score IS NOT NULL AND boolean_value IS NULL) OR
           (decision_status = 'determinate' AND metric <> 'summary_quality' AND boolean_value IS NOT NULL AND score IS NULL))
);

CREATE TABLE historical_aggregates (
    id uuid PRIMARY KEY,
    record_key text NOT NULL UNIQUE,
    source_note text NOT NULL,
    reported_date date,
    historical_model_label text NOT NULL,
    attack_types text NOT NULL,
    attempts integer NOT NULL CHECK (attempts > 0),
    automatic_successes integer NOT NULL CHECK (automatic_successes >= 0 AND automatic_successes <= attempts),
    reviewed_successes integer CHECK (reviewed_successes >= 0 AND reviewed_successes <= attempts),
    review_status text NOT NULL CHECK (review_status IN ('not_reviewed','estimated','confirmed')),
    review_note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((review_status = 'not_reviewed' AND reviewed_successes IS NULL) OR
           (review_status IN ('estimated','confirmed') AND reviewed_successes IS NOT NULL))
);

CREATE INDEX gt_case_status_idx ON ground_truths(email_case_id, review_status);
CREATE INDEX attacks_filter_idx ON attack_scenarios(vector, channel, enabled);
CREATE INDEX attachments_case_idx ON attachments(email_case_id);
CREATE INDEX cases_experiment_idx ON experiment_cases(experiment_id, expected_label);
CREATE INDEX runs_status_idx ON runs(status, created_at);
CREATE INDEX calls_run_purpose_idx ON llm_calls(run_id, purpose);
CREATE INDEX evaluations_lookup_idx ON evaluations(run_id, metric, judge_type, created_at);

-- 부분 사용량 SUM을 완전한 합계처럼 표시하지 않는 집계 뷰.
CREATE VIEW run_metrics AS
SELECT r.id AS run_id, r.experiment_case_id, r.security_gateway_enabled,
       r.defense_profile_id, r.status, r.backend_latency_ms, r.call_log_complete,
       count(c.id) FILTER (WHERE c.purpose IN ('summarize','security')) AS service_call_count,
       count(c.id) FILTER (WHERE c.purpose = 'evaluator') AS evaluator_call_count,
       CASE WHEN r.call_log_complete AND
                      count(c.id) FILTER (WHERE c.purpose IN ('summarize','security') AND c.input_tokens IS NULL) = 0
            THEN coalesce(sum(c.input_tokens) FILTER (WHERE c.purpose IN ('summarize','security')), 0)
       END AS service_input_tokens,
       CASE WHEN r.call_log_complete AND
                      count(c.id) FILTER (WHERE c.purpose IN ('summarize','security') AND c.output_tokens IS NULL) = 0
            THEN coalesce(sum(c.output_tokens) FILTER (WHERE c.purpose IN ('summarize','security')), 0)
       END AS service_output_tokens,
       CASE WHEN r.call_log_complete AND
                      count(c.id) FILTER (WHERE c.purpose IN ('summarize','security') AND c.total_tokens IS NULL) = 0
            THEN coalesce(sum(c.total_tokens) FILTER (WHERE c.purpose IN ('summarize','security')), 0)
       END AS service_total_tokens,
       count(c.id) FILTER (WHERE c.purpose IN ('summarize','security') AND c.total_tokens IS NULL) AS missing_total_usage_calls,
       -- evaluator 호출은 나중에 추가될 수 있음. 아래는 현재 기록된 평가 호출에 한정한 합계.
       CASE WHEN count(c.id) FILTER (WHERE c.purpose = 'evaluator') > 0 AND
                      count(c.id) FILTER (WHERE c.purpose = 'evaluator' AND c.total_tokens IS NULL) = 0
            THEN sum(c.total_tokens) FILTER (WHERE c.purpose = 'evaluator')
       END AS recorded_evaluator_tokens
FROM runs r LEFT JOIN llm_calls c ON c.run_id = r.id
GROUP BY r.id;

-- 검토용 쌍 조회. 성공 완료 쌍만 제공하지만 실제 모델/환경/캐시 동등성은 호출 기록을
-- 확인한 후 집계해야 한다. 공격 차단·오류는 이 뷰로 순수 성능 향상이라고 해석하지 않는다.
CREATE VIEW completed_gateway_pairs AS
SELECT c.id AS experiment_case_id, c.experiment_id, c.repeat_index, c.expected_label,
       c.requested_model, baseline.run_id AS off_run_id, defended.run_id AS on_run_id,
       defended.defense_profile_id,
       baseline.backend_latency_ms AS off_latency_ms,
       defended.backend_latency_ms AS on_latency_ms,
       defended.backend_latency_ms - baseline.backend_latency_ms AS latency_delta_ms,
       100.0 * (defended.backend_latency_ms - baseline.backend_latency_ms)
           / nullif(baseline.backend_latency_ms, 0) AS latency_change_pct,
       baseline.service_total_tokens AS off_total_tokens,
       defended.service_total_tokens AS on_total_tokens,
       defended.service_total_tokens - baseline.service_total_tokens AS token_delta,
       100.0 * (defended.service_total_tokens - baseline.service_total_tokens)
           / nullif(baseline.service_total_tokens, 0) AS token_change_pct
FROM experiment_cases c
JOIN run_metrics baseline ON baseline.experiment_case_id = c.id AND NOT baseline.security_gateway_enabled
JOIN run_metrics defended ON defended.experiment_case_id = c.id AND defended.security_gateway_enabled
WHERE baseline.status = 'succeeded' AND defended.status = 'succeeded';

COMMENT ON SCHEMA fincode IS 'Fincode PostgreSQL schema v1; applied through versioned backend migrations.';
COMMENT ON TABLE experiment_cases IS 'One immutable input/model/repetition shared by OFF and ON variants.';
COMMENT ON TABLE llm_calls IS 'One row per provider call attempt. Unknown usage is NULL; evaluator costs stay separate.';
COMMENT ON TABLE evaluations IS 'Append evaluations; preserve original automatic and subsequent human judgments.';
COMMENT ON VIEW completed_gateway_pairs IS 'Candidate completed pairs only; verify actual models and runtime conditions before benchmark aggregation.';
COMMIT;
