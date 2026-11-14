# 두 방어 기법 격리 보관 브랜치

계정: zzia0. 원본 기준: bdc899d6900f17cf4418fd2375074c50b69d4a0a.

main의 실행 파일은 변경하지 않고 이 디렉터리 내부에 새로 작성한 방어 구현과 통합 diff만 보관한다. 원본 전체 복사본과 내부 배포 정보·데이터는 업로드하지 않는다. 기본 방어 OFF. main 병합·배포는 수행하지 않았다.

입력 필터 v2와 명령·데이터 분리 프롬프트, 단건 요약 통합, Mock 비교 UI·러너·회귀 테스트를 포함한다. docs의 선정 근거·개선 보고서·이식 가이드를 먼저 읽는다.

## 보관 결과

자동 테스트41개 통과. 실제로 확보한 공격69건에서 필터69건 탐지, 정상100건에서 차단0건. 기존 데이터로 보정한 결과이므로 미지 공격·실제 ASR 효과는 미입증이다. 실제 모델 호출0회.

## 데이터와 실행

개인정보와 비밀정보 보호를 위해 snapshots의 실제 이메일·Ground Truth·공격·첨부 추출문, 행별 응답과 로그는 이 브랜치에 올리지 않았다. 따라서 내려받은 상태에서 전체 데이터 기반 러너·UI·일부 통합 테스트를 즉시 실행할 수 없다. 로컬에 보관한 동일 형식의 승인된 snapshots/cases.json이 필요하다. API 키도 미포함이다.

원본 복사본은 업로드하지 않았으므로 실행 전에 위 기준 커밋의 소스를 별도 격리 project_copy 폴더로 준비하고 docs/integration_changes.diff를 그 복사본에만 적용해야 한다. backend/defense_lab_api.py와 public/defense-lab.html은 이 보관본에 포함한다. 통합 diff는 원본 DB·worker·storage를 비활성화하고 실험 키만 사용하도록 하는 격리용 변경도 포함한다. 원본 소스의 requirements에 PyYAML>=6,<7을 추가한다. 기존 launch.py/run_checks.py는 이 디렉터리의 .venv/bin/python을 사용한다. 환경 생성·설치와 tmp 디렉터리 준비도 이 폴더 안에서만 한다. requirements는 원본 의존성과 PyYAML을 포함한다.

```bash
python -m venv .venv
mkdir -p tmp cache/pip
TMPDIR="$PWD/tmp" PIP_CACHE_DIR="$PWD/cache/pip" .venv/bin/python -m pip install -r project_copy/requirements.txt
python -B run_checks.py
python -B launch.py
```

실제 데이터가 없는 상태에서 데이터 기반 테스트가 실패하면 예제 데이터로 자동 대체하지 않는다. 입력 필터 자체는 PYTHONPATH=project_copy로 backend.defenses를 가져와 별도로 검사할 수 있다.

실제 모델은 LAB_BAZE_API_KEY만 사용하며 기본 실행은 Mock이다. 키를 파일·git·명령행 인자에 기록하지 않는다. 메인 DB 환경은 launch가 상속하지 않는다. 운영 저장형 실험 큐 통합은 미완료이며 별도 승인 대상이다.

메인 적용은 사용자 '적용' 응답 후 최신 변경 목록과 diff를 다시 제시하고 추가 확인을 받아야 한다.
