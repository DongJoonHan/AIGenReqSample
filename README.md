# AIGenReqSample

`SwRS2.md`의 전자식 차일드락 요구사항을 기준으로 파이썬 도메인 모델과 단위 테스트를 구현했다.

구현 범위:
- 수동 HMI 제어
- 음성 제어와 신뢰도 검증
- 후측방 위험 기반 자동 활성화와 최소 유지 시간
- 화재 기반 최우선 자동 비활성화
- 성인 탑승 판정 기반 자동 비활성화
- 차체 제어 명령/피드백 모델
- 진단 이벤트, 입력 타임아웃 감시, 상태 표시
- 전원 재기동 시 기본값/마지막 상태 복원 정책

주요 파일:
- `src/aigenreqsample/model.py`
- `src/aigenreqsample/controller.py`
- `tests/test_controller.py`

테스트 실행:

```bash
python -m pytest -q
```
