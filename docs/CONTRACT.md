# A팀/B팀 공통 계약 1.0

2026-10-30 확정. 두 팀은 아래 계약을 기본값으로 사용합니다.

## 1. 공개 Python API

```python
build_gateway(*, workspace: str | Path, policy_path: str | Path) -> Callable[[dict], dict]
handle(request: dict) -> dict
```

B팀이 신뢰하는 설정으로 게이트웨이를 만들고 A팀 Agent에 `handle`만 전달합니다.
`workspace`와 `policy_path`는 Tool 요청에 포함하지 않습니다. Agent는 정책이나 실행 함수를 주입할 수 없습니다.
게이트웨이는 정책 스냅샷과 workspace 식별자를 생성 시 고정합니다.
정책 변경은 새 게이트웨이를 만들어 적용합니다. 실행 중 파일을 바꿔도 기존 정책에 반영되지 않습니다.
설정/정책 오류가 있으면 생성된 함수는 모든 유효 요청을 차단합니다.
한 게이트웨이 내부 요청은 직렬 실행됩니다.

## 2. 요청 JSON

```json
{
  "version": "1.0",
  "request_id": "req-001",
  "tool": "write_file",
  "arguments": {"path": "notes.txt", "content": "새 메모"}
}
```

최상위 키 네 개와 각 Tool의 인자는 정확히 일치해야 합니다. 추가 키, 중복 JSON 키, 잘못된 자료형은 거부합니다.
`request_id`: 영문/숫자/`_`/`-` 1~64자. 요청과 응답의 상관관계 식별용이며 재실행 방지 기능은 없습니다.
CLI는 JSON Lines 형식으로 한 줄에 요청 하나를 받습니다. 한 줄 최대 524,288바이트입니다.
Python API 호출자는 JSON 파싱을 담당하며 파싱 시 중복 키를 거부해야 합니다. `unique_object`를 재사용할 수 있습니다.

| tool | arguments | 성공 result |
|---|---|---|
| `read_file` | `path: str` | `{path, content, bytes}` |
| `write_file` | `path: str, content: str` | `{path, bytes}` |
| `list_files` | `path: str` | `{path, entries: [{path, type}]}` |
| `create_file` | `path: str, content: str` | `{path, bytes}` |
| `delete_file` | `path: str` | `{path, deleted: true}` |

UTF-8 텍스트만 지원하며 읽기 및 쓰기 내용의 한도는 각각 65,536바이트입니다.
`write_file`은 존재하는 일반 파일만 교체합니다. `create_file`은 존재하지 않는 파일만 만듭니다.
`delete_file`은 일반 파일만 삭제합니다. 디렉터리는 운영자가 미리 생성합니다.
`list_files`는 재귀 탐색하지 않고, 해당 디렉터리의 직접 자식 중 정확한 `read_file` 또는 `list_files` grant가 있는 항목만 반환합니다.
수정/생성/삭제 권한만 있는 이름, 숨김 파일, 링크, 특수 파일은 목록에서 제외합니다. 경로 순으로 정렬합니다.

## 3. 응답 JSON

허용 후 실행 성공:

```json
{
  "version": "1.0", "request_id": "req-001", "decision": "ALLOW",
  "reason_code": "OK", "reason": "Approved and executed.",
  "execution": {"status": "SUCCESS", "result": {"path": "notes.txt", "bytes": 10}}
}
```

`새 메모`의 UTF-8 크기는 10바이트입니다.

권한/안전성 검사 차단:

```json
{
  "version": "1.0", "request_id": "req-002", "decision": "BLOCK",
  "reason_code": "NOT_ALLOWED", "reason": "No matching allowlist grant.",
  "execution": null
}
```

허용된 대상의 실행 오류:

```json
{
  "version": "1.0", "request_id": "req-003", "decision": "ALLOW",
  "reason_code": "NOT_FOUND", "reason": "Approved target does not exist.",
  "execution": {"status": "ERROR", "result": null}
}
```

`decision`은 권한/안전성 판정, `execution.status`는 실제 실행 결과입니다.
안전성 검사가 실패한 요청은 변경 전에 `BLOCK`으로 종료합니다.
OS 오류, 잘못된 UTF-8 파일, 쓰기 실패 등은 `ALLOW` + `ERROR`입니다.
정책 검사 중 예외가 발생하면 `BLOCK` + `POLICY_ERROR`이며 실행기를 호출하지 않습니다.
실행기에 진입한 뒤 예상 밖 예외가 발생하면 이미 파일이 변경되었을 수 있으므로
`ALLOW` + `ERROR`, `reason_code: INTERNAL_ERROR`를 반환합니다.
쓰기 오류에는 부분 변경이 남을 수 있으므로 자동 재시도하지 않습니다. 이 MVP는 트랜잭션/원자적 쓰기를 제공하지 않습니다.
잘못된/누락된 요청 ID는 응답에서 `null`입니다. `reason`은 사람이 읽는 설명이며 분기는 `reason_code`로 합니다.

현재 감사 로그는 최선 노력 방식입니다. 로그 핸들러가 실패해도 이미 결정된 응답을 그대로 반환하고
파일 작업을 재실행하지 않습니다. 로그 기록 성공을 실행의 필수 조건으로 삼는 기능은 제공하지 않습니다.

| reason_code | 의미 |
|---|---|
| `OK` | 승인 및 실행 성공 |
| `INVALID_REQUEST` | 요청 구조/버전/ID 오류 |
| `UNKNOWN_TOOL` | 미등록 Tool |
| `INVALID_ARGUMENTS` | 인자 구조/텍스트 형식 오류 |
| `INVALID_PATH` | 경로 문법 오류/우회 시도 |
| `PROTECTED_PATH` | 제어 파일/비밀정보용 이름 |
| `NOT_ALLOWED` | 정확한 Allowlist 규칙 없음 |
| `POLICY_ERROR` | 정책 로드/검증 실패 |
| `CONFIG_ERROR` | workspace/정책 배치 오류 |
| `UNSUPPORTED_PLATFORM` | 안전한 파일 접근 미지원 |
| `UNSAFE_TARGET` | 링크/특수 파일/하드링크/루트 교체 |
| `SIZE_LIMIT` | 읽기/쓰기 크기 초과 |
| `NOT_FOUND` | 허용된 대상/상위 폴더 없음 |
| `ALREADY_EXISTS` | 생성 대상 이미 존재 |
| `EXECUTION_ERROR` | 승인 후 OS/텍스트 처리 오류 |
| `INTERNAL_ERROR` | 예상하지 못한 내부 오류 |

## 4. 경로와 정책

경로는 workspace 기준의 `/` 구분 상대 경로입니다. 정규화로 우회 경로를 허용하지 않고 문법 단계에서 거부합니다.
루트 목록에만 `.`을 사용합니다. 절대 경로, `..`, 빈 경로 조각, 역슬래시, 콜론/ADS, NUL,
끝의 점/공백, 숨김 파일, Windows 장치 이름과 8.3 별칭(`~`)을 허용하지 않습니다.
MVP의 각 경로 조각은 영문/숫자/`_`/`-`로 시작하고 이후 영문/숫자/`_`/`-`/`.`만 사용합니다.
조각 100자, 전체 512자, 최대 20조각입니다. 한글 파일명 지원은 다음 계약 버전에서 확장할 수 있습니다.
JSON 경로 대소문자는 정확히 일치해야 하므로 Windows의 대소문자 별칭도 권한을 넓히지 않습니다.

```json
{
  "version": "1.0",
  "allow": [
    {"tool": "read_file", "path": "public.txt"},
    {"tool": "write_file", "path": "notes.txt"},
    {"tool": "list_files", "path": "."}
  ]
}
```

권한은 `(tool, path)`의 정확한 쌍에만 적용됩니다. 와일드카드, prefix, 디렉터리 상속, 암시적 읽기 권한은 없습니다.
정책은 최대 1,000개 규칙/262,144자입니다. 중복 규칙/중복 키/알 수 없는 키/잘못된 규칙 하나라도 있으면 전체 차단합니다.
`config`, `secrets`, `credentials`, `policy.json`, `api_key`, `api_keys` 이름,
`policy.`, `credentials.`, `api_key.` 접두 이름, `.key/.pem/.p12/.pfx` 파일은 경로 어느 단계에서도 금지합니다.
모든 숨김 경로도 금지하므로 `.env`, `.git`, `.aws` 등에 접근할 수 없습니다.
운영자는 키/정책/소스 코드를 workspace 밖에 둡니다. 이름 검사는 임의 파일의 내용이 비밀인지 판단하지 않습니다.

## 5. 내부 파일 Tool API

```python
read_file(call: _ApprovedCall) -> dict
write_file(call: _ApprovedCall) -> dict
list_files(call: _ApprovedCall) -> dict
create_file(call: _ApprovedCall) -> dict
delete_file(call: _ApprovedCall) -> dict
```

게이트웨이의 비공개 dispatch만 요청 검증/정책 승인을 마친 뒤 승인 객체를 생성합니다.
승인은 Tool/인자에 묶이고 한 번만 소비됩니다. `approved=True`나 외부 `ALLOW` 문자열을 받지 않습니다.
Agent는 이 객체, `_make_dispatch`, `Workspace`, 파일 Tool을 전달받지 않습니다.
Python의 비공개 이름은 OS 권한 경계가 아닙니다. 같은 프로세스에서 임의 Python 코드를 실행할 수 있는 공격자에 대한 보장은 [SECURITY.md](../SECURITY.md)에 명시합니다.

## 6. Gemini 및 추가 모델

`Model.start(prompt) -> Turn`, `Model.resume([(ToolCall, gateway_response), ...]) -> Turn`을 구현합니다.
`ToolCall(name, arguments, call_id)`와 `Turn(text, calls)`는 제공자와 독립적인 자료형입니다.
Gemini는 REST `generateContent`를 사용하고 함수 선언만 등록합니다. 자동 함수 실행/코드 실행은 사용하지 않습니다.
모든 제안 호출을 공통 JSON으로 바꿔 게이트웨이에 보내고 전체 `BLOCK/ALLOW` 응답을 모델에 돌려줍니다.
병렬 제안도 순서대로 검증/실행합니다. 원본 모델 content, thought signature, 호출 ID를 보존합니다.
한 실행에서 최대 8회 왕복/16개 Tool Call을 처리하며 자동 재시도하지 않습니다.
GPT/Claude 어댑터는 위 `Model` 프로토콜에 맞춰 메시지와 호출 ID를 변환합니다.
