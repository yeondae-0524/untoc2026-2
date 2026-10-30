# AgentFence — A팀/B팀 통합 MVP

B-2 개요서에 맞춘 정책 엔진 사용법과 새 정책 형식은 [B-2 정책 안내](docs/B2_POLICY.md)를 참고하세요.
현재 `config/policy.json`은 파일·디렉터리 권한을 분리하며 파일 생성은 부모 디렉터리의 `create` 권한으로 판단합니다.

Python 직접 구현 · Gemini API 우선 · JSON Allowlist · 함수형 게이트웨이 · Sandbox 제외.
Python 3.11 이상, 표준 라이브러리만 사용합니다. API 키 없이 파일 Tool과 테스트를 실행할 수 있습니다.

```text
A팀 Agent / Gemini → B팀 handle(request)
                    → 요청 검증 → 정확한 경로 Allowlist → 안전한 핸들 열기
                    → 내부 일회 승인 → A팀 파일 Tool → 공통 응답
```

## 바로 실행

저장소를 받은 뒤 루트 폴더에서 실행합니다. Windows PowerShell:

```powershell
'{"version":"1.0","request_id":"demo-1","tool":"read_file","arguments":{"path":"public.txt"}}' | python main.py
python -m unittest discover -s tests -v
```

Linux/macOS의 POSIX Python:

```sh
printf '%s\n' '{"version":"1.0","request_id":"demo-1","tool":"read_file","arguments":{"path":"public.txt"}}' | python main.py
python -m unittest discover -s tests -v
```

정상 응답:

```json
{"version":"1.0","request_id":"demo-1","decision":"ALLOW","reason_code":"OK","reason":"Approved and executed.","execution":{"status":"SUCCESS","result":{"path":"public.txt","content":"Hello from AgentFence.\n","bytes":23}}}
```

차단 확인: 위 요청의 경로를 `../config/policy.json`으로 바꿉니다. `BLOCK`, `INVALID_PATH`, `execution: null`이 반환됩니다.
`--audit` 옵션은 요청 ID·판정·사유 코드만 표준 오류에 기록합니다.

## Gemini 연결

운영자가 `GEMINI_API_KEY`와 사용할 모델 ID `GEMINI_MODEL`을 환경변수로 설정합니다.
예제 키, `.env` 파일, 실제 비밀정보를 저장소나 `workspace/`에 넣지 마세요.
모델 ID는 계정에서 사용 가능한 값으로 지정하며 코드가 특정 모델에 고정되지 않습니다.

```powershell
python main.py --gemini "public.txt를 읽고 내용을 요약해줘"
```

이 모드는 프롬프트와 허용된 파일 Tool 결과를 Gemini API에 전송합니다.
수동 Function Calling을 사용하므로 Gemini가 함수 이름과 인자를 제안해도 반드시 게이트웨이를 거칩니다.
API 키를 읽는 역할은 Gemini 어댑터만 담당합니다. 키는 Tool 인자, 정책, 응답, 감사 로그에 넣지 않습니다.
오프라인 테스트는 가짜 전송을 사용하며 실제 Gemini API 호출과 과금은 하지 않습니다.

## 통합 인터페이스

```python
from agentfence.gateway import build_gateway

# B팀/신뢰하는 조립 코드에서 한 번 생성합니다.
handle = build_gateway(workspace="workspace", policy_path="config/policy.json")

# A팀 Agent에는 이 함수만 주입합니다.
response = handle({
    "version": "1.0", "request_id": "example-1",
    "tool": "read_file", "arguments": {"path": "public.txt"},
})
```

- `read_file(path)`: UTF-8 읽기, 최대 65,536바이트.
- `write_file(path, content)`: 기존 일반 파일의 전체 내용 교체. 새 파일 생성 안 함.
- `list_files(path)`: 한 단계 목록. 루트는 `.`. 읽기/목록 권한이 있는 이름만 표시.
- `create_file(path, content)`: 새 파일만 배타적으로 생성. 덮어쓰기/부모 폴더 자동 생성 안 함.
- `delete_file(path)`: 일반 파일 하나 삭제. 폴더/재귀 삭제 안 함.

위 표기는 JSON Tool 인자입니다. 내부 Python 함수는 원시 경로 대신 게이트웨이의 내부 승인 객체를 받습니다.
`ALLOW` 응답은 이미 실행한 결과이며 Agent가 다시 Tool을 실행하는 신호가 아닙니다.

## 팀별 담당 파일

| 팀 | 담당 | 연동 기준 |
|---|---|---|
| A팀 1명 | `agent/agent.py`, `agent/models/gemini.py` | `Model.start/resume`와 `handle`만 사용 |
| A팀 1명 | `tools/file_tools.py`, `tools/_filesystem.py` | 내부 승인 + 안전한 핸들 사용 |
| B팀 1명 | `agentfence/gateway.py`, `main.py` | 검증·판정 후에만 dispatch |
| B팀 1명 | `agentfence/policy.py`, `config/policy.json` | 정확한 경로 Allowlist, 기본 차단 |
| B팀 1명 | `tests/`, 감사 로그, CI | 결과와 실제 파일 부작용 함께 검증 |

GPT/Claude를 추가할 때는 `agent/agent.py`의 `Model` 프로토콜을 구현하는 어댑터만 추가합니다.
게이트웨이 요청/응답, Allowlist, 파일 Tool 계약은 그대로 유지합니다.

전체 규약은 [계약 문서](docs/CONTRACT.md), 위협 모델과 TOCTOU 한계는 [보안 설계](SECURITY.md),
확인한 테스트 범위는 [검증 기록](docs/VALIDATION.md)을 참고하세요.
