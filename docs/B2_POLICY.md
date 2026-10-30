# B-2 Allowlist 정책 엔진

담당: `agentfence/policy.py`, `config/policy.json`, 기본 테스트 `tests/test_policy.py`.
실제 파일 Tool 실행과 최종 공통 응답 JSON 구성은 B-1 게이트웨이가 맡습니다.

## 정책 형식

```json
{
  "default": "BLOCK",
  "files": {
    "public.txt": ["read"],
    "notes.txt": ["read", "write"],
    "drafts/old.txt": ["read", "write", "delete"]
  },
  "directories": {
    ".": ["list"],
    "drafts": ["list", "create"]
  }
}
```

파일의 read/write/delete 권한과 디렉터리의 list/create 권한을 분리합니다.
`create_file("drafts/new.txt")`는 `drafts`의 create 권한을 확인합니다.
권한은 하위 디렉터리에 상속되지 않으며 생성한 파일의 읽기·수정·삭제 권한도 자동으로 생기지 않습니다.
덮어쓰기 방지는 A팀 파일 Tool이 실행 단계에서 수행합니다.
정책에 등록된 파일은 아직 존재하지 않아도 됩니다. `drafts/old.txt`는 예시 권한이며 실행 성공을 보장하지 않습니다.

정책 경로는 workspace 기준 상대 경로이며 루트 디렉터리는 `.`입니다.
기존 공통 경로 검사기를 재사용하므로 절대 경로, `..`, 역슬래시, 보호 파일, 숨김 파일은 거부합니다.
현재 경로 검사기는 영문/숫자와 `_`, `-`, `.`만 지원합니다.
대소문자와 전체 경로가 정확하게 일치해야 하며 와일드카드를 지원하지 않습니다.

정책은 최대 262,144자, 파일·디렉터리 합계 1,000항목입니다.
잘못된 자료형, 중복 JSON 키, 중복 권한, 알 수 없는 권한/필드, default가 BLOCK이 아닌 정책은 전체 오류로 처리합니다.
빈 권한 목록은 아무 작업도 허용하지 않습니다.
로딩 실패는 `FenceError(code="POLICY_ERROR")`이며 게이트웨이는 모든 요청을 차단합니다.
정책은 로딩 시 고정되므로 변경 적용 시 게이트웨이를 새로 생성해야 합니다.

## B-1 연동

```python
from agentfence.policy import Policy

policy = Policy.load("config/policy.json")
result = policy.check_permission("create_file", "drafts/new.txt")
print(result.decision)     # ALLOW
print(result.reason_code)  # POLICY_ALLOWED
print(result.reason)

assert policy.allows("read_file", "drafts/new.txt") is False
```

`check_permission(tool, path)`는 불변 PolicyDecision을 반환합니다.
사유 코드는 POLICY_ALLOWED, POLICY_DENIED, INVALID_TOOL, INVALID_PATH입니다.
정책 로딩 오류는 위와 같이 예외로 반환합니다. 요청 전체 형식과 content 검증은 게이트웨이 담당입니다.
이 모듈은 대상 파일을 열거나 실행하지 않습니다. 심볼릭 링크와 경로 교체 방어는 기존 파일 백엔드가 실행 시 수행합니다.

기존 게이트웨이/파일 Tool용 `allows()`와 `visible_children()`도 유지합니다.
목록에는 기존 방식대로 읽기/목록 권한이 있는 직접 자식만 표시합니다.
기존 `{ "version": "1.0", "allow": [{"tool": "read_file", "path": "public.txt"}] }`
정책도 호환하며, 이 형식은 create도 파일 경로 하나씩 허용합니다. 두 정책 형식은 혼합하지 않습니다.

기존 게이트웨이의 외부 응답 코드는 아직 OK/NOT_ALLOWED/UNKNOWN_TOOL,
실행 오류 상태는 ERROR입니다. 개요서의 POLICY_ALLOWED/POLICY_DENIED/INVALID_TOOL,
FAILED로 맞추는 작업은 B-1과 조율해야 합니다. B-2 판정 API는 개요서 코드를 사용합니다.
기존 `docs/CONTRACT.md`의 정책 형식 설명은 이전 형식이며 새 B-2 정책 형식은 이 문서를 기준으로 합니다.
향후 사용자/역할 정책은 신뢰하는 호출자 인증 정보와 별도 스키마를 정의한 뒤 확장합니다.
현재는 LLM이 전달한 사용자/역할/권한 필드를 받지 않습니다.

## 검증

```powershell
python -m unittest discover -s tests -p test_policy.py -v
python -m unittest discover -s tests -v
```

테스트는 pytest에서도 수집할 수 있습니다: `python -m pytest tests/test_policy.py -q`.
