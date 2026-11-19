# 감사 로그 구현 전 생각할 사항들

현재의 감사 기록 방식을 개선할 계획 또는 고려할 사항들을 작성한다.
게이트웨이는 허용·차단 요청 모두에 대해 결과와 `request_id`를 기록하고,
거절·오류에는 `reason_code`를 더한다.
CLI에서 `--audit`를 지정하면 이 기록이 stderr에 표시된다.

이 작업 내용은 기존의 개발 원칙과 상충될 가능성이 존재하므로 가능하면 기능 준비가 충분히 되기 전까지 이 브랜치를 원격으로 push하지 않고,
여기서 작업한 내용도 main으로 merge하는 작업을 피한다.

## 로그의 영속성

현재 CLI는 `--audit`가 있을 때에만 stderr로 감사 기록을 출력하고, 파일은 만들지 않는다.
향후에는 CLI 실행 시 workspace 밖의 보호된 위치에 자동으로 기록하고 `--audit`는 stderr 동시 출력 옵션으로
둘 수 있다. Python API 사용자는 CLI의 자동 파일 설정을 거치지 않으므로 별도 설정이 필요하다.

### 저장 방식 비교

| 방식 | 장점 | 한계·비용 | 판단 |
|---|---|---|---|
| 실행별 파일 | 실행의 시작·종료와 요청이 한 파일에 모인다. 서로 다른 CLI 프로세스가 같은 파일을 쓰지 않는다. | 짧은 실행이 많으면 작은 파일이 쌓이고 검색·보존 기간 관리가 번거롭다. | 동시 실행을 허용하면서 별도 수집기를 두지 않을 때 적합하다. |
| 공유 `recent.log`에 무제한 추가 | 구현과 `tail` 확인이 쉽다. 파일 수가 적다. | 오래 운영하면 크기가 끝없이 증가한다. 여러 프로세스의 직접 동시 기록은 안전하다고 가정할 수 없다. | 장기 기본값으로 부적합하다. |
| 공유 `recent.log` + 크기 기준 회전 | 현재 파일 이름이 일정하고 전체 사용량을 제한할 수 있다. Python 표준 `RotatingFileHandler`가 있다. | 회전이 실행 중간에 일어날 수 있다. 여러 프로세스가 같은 파일·회전 파일을 직접 관리하면 충돌할 수 있다. | **단일 기록자를 보장할 수 있을 때 1순위.** |
| 공유 파일 + 시간 기준 회전 | 날짜별 조회와 보존 기간 정책에 직관적이다. | 기록이 없으면 정해진 시각에 바로 회전하지 않는다. 크기 상한도 별도로 필요하다. | 저빈도·소규모 실행의 첫 단계에는 이점이 작다. |
| stderr → OS/외부 수집기 | 여러 프로세스의 기록·보존·회전을 수집기 한 곳에 맡길 수 있다. | 실행 환경에 수집기 설정이 필요하고, 단독 CLI 실행만으로 로컬 파일이 생기지는 않는다. | 서비스/배포 환경에서 적합하다. |

Gemini Agent는 한 실행에서 Tool Call이 최대 16개지만, 일반 stdin JSON Lines 모드는 EOF까지 요청을 계속
받는다. 따라서 실행당 호출 수가 작다는 가정만으로 크기 제한을 생략하지 않는다. 압축된 회전 파일은
저장량이 실제 문제가 될 때 검토한다. 초기부터 `.gz`/`.zip` 로테이터를 직접 구현할 이점은 작다.

### 권장안: 단일 기록자일 때 공유 파일 + 크기 기준 회전

- 활성 파일은 `recent.log`, 이전 파일은 `recent.log.1` 등으로 둔다. `maxBytes`와 `backupCount`를 모두
  양수로 설정하고, 디스크 예산과 예상 기록량에 맞춰 값을 정한다. 회전 파일을 포함한 사용량을 제한한다.
- CLI 시작 시 난수 기반 `run_id`를 만들고 `START run_id=... mode=stdin|gemini`를 기록한다. 요청별 기록에도
  같은 `run_id`를 붙인다. 정상 종료 시 `END run_id=... exit_code=... requests=...`를 기록한다.
  사용자가 제공하는 `request_id`는 중복될 수 있으므로 실행 식별자로 쓰지 않는다.
- `START`와 `END` 사이의 줄이 항상 붙어 있다고 가정하지 않는다. 회전으로 여러 파일에 나뉠 수 있고,
  동시 실행을 허용하면 서로 다른 실행의 줄이 섞일 수 있다. 분석할 때는 `run_id`로 묶는다.
- `END`는 정상 종료 및 처리 가능한 예외에서만 남길 수 있다. 강제 종료, 전원 손실, 로그 쓰기 실패에는
  빠질 수 있으므로 `END`가 없는 실행을 곧바로 비정상 종료로 확정하지 않는다.
- 기본 경로는 workspace 밖에서 정하고, 사용자 지정 `--workspace`를 포함해 실제 설정에서도 로그 경로가
  workspace 내부가 아닌지 검사한다. 로그 디렉터리/파일은 해당 사용자만 읽고 쓸 수 있게 만들고,
  저장소에 커밋되지 않게 한다. 운영체제별 권한 설정 방법은 구현 시 확정한다.
- 표준 `FileHandler`/`RotatingFileHandler`만으로는 여러 프로세스의 동일 파일 기록을 보장하지 못한다.
  1차 구현에서 공유 파일을 선택한다면 단일 CLI 기록자라는 조건을 명시하고 실제로 보장해야 한다.
  이 조건을 보장하기 어렵다면 실행별 파일이나 별도 로그 수집기로 전환한다.

자동 파일 기록을 보안상 필수 감사로 취급할지도 별도 결정이 필요하다. 파일을 열지 못하면 첫 Tool 실행 전에
중단할 수 있지만, 실행이 끝난 뒤 기록하는 현재 흐름에서는 **작업은 수행되고 해당 감사 줄만 실패**할 수 있다.
기록 실패를 감지해 이후 요청을 막더라도 이미 발생한 작업은 되돌릴 수 없다. 모든 작업이 빠짐없이 기록되어야
한다면 실행 전 기록, 디스크 동기화 및 실행 후 결과 기록을 포함한 별도 설계가 필요하다. 단순 파일 핸들러의
오류 처리를 그 보장으로 간주하지 않는다.

근거: [Python Logging Cookbook: 다중 프로세스 파일 기록](https://docs.python.org/3.11/howto/logging-cookbook.html#logging-to-a-single-file-from-multiple-processes),
[Python logging.handlers: 크기·시간 기준 회전](https://docs.python.org/3.11/library/logging.handlers.html#rotatingfilehandler).

## 로그의 출력형식

현재 감사 로그는 UTC 시각과 `[ACCEPTED]`·`[REJECTED]`·`[ERROR]`, Tool 이름, 요청 ID,
필요한 경우 사유 코드로 이루어진 텍스트 형식이다.
stdout의 JSON 객체는 감사 로그가 아니라 게이트웨이 응답이다.
로그를 분석하기 좋은 형태로 만드려면 텍스트 형식은 grep, JSON 형식은 jq 등으로 빠르게 원하는 데이터를 찾고 분석할 수 있게 만들어야 한다.

예를 들어 특정한 시간대에 특정 디렉토리를 건드리다가 거절된 tool call들을 확인한다던가 하는 등의 질의가 가능할 필요가 있다.

## 로그에 포함할 내용

로그에는 분석에 필요한 정보만 남기고, 파일 내용·프롬프트·API 키·원본 예외 문자열은 남기지 않는다.
경로 기록은 현재 `SECURITY.md`의 경로 제외 원칙을 변경한다.
따라서 경로가 필요한 상세 감사 모드를 도입한다면 기록 대상, 접근 권한, 보존 기간을 함께 정하고 보안 문서도 수정해야 한다.

### 형식 1안

1차 구현은 `agentfence/audit.py`에서 결과를 분류하고 한 줄을 만들어 `--audit`의 stderr에 출력한다.
파일 저장과 경로를 포함하는 상세 감사 모드는 이후 구현 대상으로 둔다.

한 요청이 끝난 뒤 한 줄을 기록한다. 결과에 따라 필요한 필드만 붙인다.

```text
TIME [ACCEPTED] TOOL request_id=ID [path=PATH]
TIME [REJECTED] TOOL request_id=ID reason_code=CODE [path=PATH]
TIME [ERROR] TOOL request_id=ID reason_code=CODE [path=PATH]
```

위 형식은 현재 stderr 구현을 기준으로 한다. 영속 파일을 도입하면 모든 요청 줄의 `request_id` 앞에
`run_id=RUN`을 추가하고, 같은 파일에 다음 실행 경계 줄을 기록한다. `START`·`END`는 Tool 요청이
아니므로 Tool 이름과 `request_id`가 없다.

```text
TIME [START] run_id=RUN mode=stdin|gemini
TIME [ACCEPTED] TOOL run_id=RUN request_id=ID
TIME [END] run_id=RUN exit_code=N requests=N
```

| 요소 | 규칙 |
|---|---|
| `TIME` | 기록 시각. UTC ISO 8601, 밀리초까지 표시하고 `Z`로 끝낸다. |
| `RESULT` | 아래 표에 따라 게이트웨이 응답의 `decision`, `execution.status`, `reason_code`에서 결정한다. |
| `TOOL` | 등록된 Tool 이름이면 그대로 기록한다. 알 수 없거나 추출할 수 없으면 `<unknown>`을 쓴다. 원본의 임의 Tool 문자열은 기록하지 않는다. |
| `request_id` | 계약에 맞는 ID만 기록한다. 없거나 잘못되었으면 `<unknown>`을 쓴다. 클라이언트가 정하는 값이므로 고유하거나 신뢰할 만한 실행 식별자로 취급하지 않는다. |
| `reason_code` | `REJECTED`와 `ERROR`에서 필수다. 응답의 `reason_code`를 그대로 기록하며, 사람이 읽는 `reason`과 예외 원문은 기록하지 않는다. `ACCEPTED`의 `OK`는 결과와 의미가 같으므로 생략한다. |
| `path` | 상세 감사 모드에서만 기록한다. 계약의 경로 검사를 통과한 workspace 상대 경로만 허용하며, 검사 전 원문은 기록하지 않는다. 경로를 확보하지 못했다면 필드 자체를 생략한다. `.`은 루트 목록 요청에만 사용한다. |

1차 구현에서는 미등록 Tool 이름을 `<unknown>`으로 기록한다. 임의 문자열의 개행으로 로그 줄을 위조하거나,
과도하게 긴 값·프롬프트·비밀정보를 로그에 남기지 않기 위한 선택이다. 대신 어떤 이름으로 호출했는지
분석할 수 없다는 단점이 있다. 실제 이름이 필요해지면 길이 제한, 개행·구분자 이스케이프,
비문자열 처리, 민감정보 노출 범위를 정한 뒤 기록 방식을 변경한다.

`path` 외에는 Tool 인자를 기록하지 않는다. 특히 `write_file`과 `create_file`의 `content`는 항상 제외한다.
필드 이름과 순서는 위 형식으로 고정한다. 기록할 값에 공백·개행·`=` 등이 허용되도록 형식을 확장한다면
이스케이프 규칙을 먼저 정의해야 한다. 현재 제안의 값은 계약으로 제한된 ID·Tool·경로, 고정된 결과·사유 코드,
또는 `<unknown>`뿐이므로 공백과 개행을 포함하지 않는다. `path` 앞에 `./`를 덧붙이지 않고 계약의 상대 경로를 그대로 쓴다.

| `RESULT` | 게이트웨이 응답 | 의미 |
|---|---|---|
| `ACCEPTED` | `decision=ALLOW`, `execution.status=SUCCESS` | 승인한 Tool 실행이 성공함. |
| `REJECTED` | `decision=BLOCK`, `execution=null`, 아래 시스템 오류 코드가 아님 | 요청 형식·정책·안전성 검사 등으로 차단됨. Tool이 실행되지 않았다는 뜻으로 일반화하지 않는다. |
| `ERROR` | `decision=ALLOW`, `execution.status=ERROR` | 정책상 승인했으나 실제 실행에서 오류가 남. 쓰기·생성은 부분 변경 가능성이 있다. |
| `ERROR` | `decision=BLOCK`, `reason_code`가 `POLICY_ERROR`, `CONFIG_ERROR`, `UNSUPPORTED_PLATFORM`, `INTERNAL_ERROR` 중 하나 | 설정·플랫폼·내부 문제로 요청을 정상 처리하지 못함. 승인되거나 실행됐다는 뜻은 아니다. |

`ACCEPTED`와 `REJECTED`만 보면 각각 `ALLOW`와 `BLOCK`을 알 수 있어 `decision`을 다시 쓰지 않는다.
`ERROR`는 승인 후 실행 실패(`ALLOW`)와 설정·내부 문제로 인한 차단(`BLOCK`)을 함께 가리킬 수 있다.
둘은 한 요청에서 동시에 반환되는 두 결과가 아니라, 오류가 발생한 단계가 다른 경우다. 현재의
`reason_code`로 두 종류를 구별한다. 위 표의 시스템 오류 코드는 처리 실패를, `NOT_FOUND`,
`ALREADY_EXISTS`, `EXECUTION_ERROR` 등은 승인 후 실행 실패를 나타낸다.
예상하지 못한 응답 조합은
`ERROR`로 기록하고 별도 검증 실패로 취급한다. 로그 분류 오류가 실행 결과를 바꿔서는 안 된다.

```text
2026-11-09T12:01:13.824Z [ACCEPTED] write_file request_id=req-1 path=notes.txt
2026-11-09T12:01:14.010Z [REJECTED] read_file request_id=req-2 reason_code=NOT_ALLOWED path=private.txt
2026-11-09T12:01:14.051Z [REJECTED] <unknown> request_id=<unknown> reason_code=INVALID_REQUEST
2026-11-09T12:01:14.090Z [ERROR] create_file request_id=req-3 reason_code=ALREADY_EXISTS path=drafts/new.txt
2026-11-09T12:01:14.110Z [ERROR] read_file request_id=req-4 reason_code=POLICY_ERROR path=public.txt
```

위 예시의 `path`는 상세 감사 모드를 가정한다. 기본 stderr 모드에서는 다섯 줄 모두 `path`를 생략한다.
`REJECTED`라도 경로가 계약 검사를 통과했다면 상세 모드에서 기록할 수 있다. `INVALID_PATH`나
`PROTECTED_PATH`처럼 검사를 통과하지 못한 경로의 원문은 기록하지 않는다.

현재 게이트웨이는 실행 중 `FenceError`가 발생하면 `BLOCK` 응답을 반환한다. 예를 들어 안전하지 않은 대상이나
읽기 크기 제한에 걸린 경우가 여기에 포함될 수 있다. 위 표는 **현행 응답의 분류**를 따르므로 이들은
`REJECTED`로 표시한다. 실제 실행이 시작됐는지를 로그만으로 단정하려면 게이트웨이의 오류 상태 모델을 먼저
세분화해야 한다. `ERROR`를 모든 실행 단계 오류의 이름으로 쓰고 싶다면 구현 전에 응답 계약도 함께 개정한다.

#### 다음 구현 전에 결정할 점

- 단일 기록자 조건을 보장할 방법. 보장할 수 없다면 공유 파일을 쓰지 않는다.
- 로그 파일 경로, 접근 권한, 회전 크기·보관 파일 수의 기본값.
- 상세 감사 모드의 활성화 방법과 경로 기록 허용 범위.
- 감사 기록 실패 시 요청 처리를 계속할지, 실행 전에 중단할지. 보안상 필수 기록이라면 실행 후 기록 실패를
  어떻게 다룰지도 설계해야 한다.

### 형식 2안
