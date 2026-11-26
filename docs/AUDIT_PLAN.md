# 감사 로그 구현 전 생각할 사항들

현재의 감사 기록 방식을 개선할 계획 또는 고려할 사항들을 작성한다.
게이트웨이는 허용·차단 요청 모두에 대해 결과와 `request_id`를 기록하고,
거절·오류에는 `reason_code`를 더한다.
CLI에서 `--audit`를 지정하면 이 기록이 stderr에 표시된다.

이 작업 내용은 기존의 개발 원칙과 상충될 가능성이 존재하므로 가능하면 기능 준비가 충분히 되기 전까지 이 브랜치를 원격으로 push하지 않고,
여기서 작업한 내용도 main으로 merge하는 작업을 피한다.

## CLI 출력과 파일 보관

CLI 감사 로그는 `--audit`를 지정한 실행에서만 활성화되며 stderr로 출력한다. 일반 CLI의 Tool 응답 JSON은
stdout에 출력한다. CLI는 감사 로그 파일을 만들거나 회전시키지 않는다. 파일 보관이 필요하면 실행자가
workspace 밖의 저장 위치를 선택하고 셸 리다이렉션을 사용한다.

```sh
python main.py --audit < requests.jsonl 2>> /path/outside-workspace/recent.log
```

`2>>`는 stderr 전체를 파일에 추가하므로 일반 오류 메시지도 함께 저장될 수 있다. `recent.log`는 예시 이름이다.
파일 권한과 보존량, 여러 실행의 기록을 한 파일에 모을지는 실행 환경에서 관리한다.

## 로그에 포함할 내용

로그에는 분석에 필요한 정보만 남기고, 파일 내용·프롬프트·API 키·원본 예외 문자열은 남기지 않는다.
기본 감사 로그는 경로를 생략한다. diff 모드를 함께 사용하면 파일 변경 요청의 검증된 상대 경로를 포함한다.

### Tool 요청과 실행 경계 형식

한 요청이 끝난 뒤 한 줄을 기록한다. 결과에 따라 필요한 필드만 붙인다.

```text
TIME [ALLOW] TOOL run_id=RUN request_id=ID [path=PATH]
TIME [BLOCK] TOOL run_id=RUN request_id=ID reason_code=CODE [path=PATH]
TIME [ERROR] TOOL run_id=RUN request_id=ID reason_code=CODE [path=PATH]
```

실행마다 난수 기반 `run_id`를 만들고 요청 줄과 실행 경계 줄에 같은 값을 기록한다. `START`와 `END`는
실행 경계 이벤트이며 Tool 이름과 `request_id`가 없다. 여러 실행의 줄이 섞여도 `run_id`로 묶을 수 있다.
강제 종료나 출력 실패 시 `END`가 빠질 수 있다.

```text
TIME [START] run_id=RUN mode=stdin|gemini
TIME [END] run_id=RUN exit_code=N requests=N
```

| 요소 | 규칙 |
|---|---|
| `TIME` | 기록 시각. UTC ISO 8601, 밀리초까지 표시하고 `Z`로 끝낸다. |
| `RESULT` | 아래 표에 따라 게이트웨이 응답의 `decision`, `execution.status`, `reason_code`에서 결정한다. |
| `TOOL` | 등록된 Tool 이름이면 그대로 기록한다. 알 수 없거나 추출할 수 없으면 `<unknown>`을 쓴다. 원본의 임의 Tool 문자열은 기록하지 않는다. |
| `run_id` | 실행마다 생성하는 고유 식별자. 같은 실행의 요청과 실행 경계에 동일한 값을 쓴다. |
| `request_id` | 계약에 맞는 ID만 기록한다. 없거나 잘못되었으면 `<unknown>`을 쓴다. 클라이언트가 정하는 값이므로 고유하거나 신뢰할 만한 실행 식별자로 취급하지 않는다. |
| `reason_code` | `BLOCK`과 `ERROR`에서 필수다. 응답의 `reason_code`를 그대로 기록하며, 사람이 읽는 `reason`과 예외 원문은 기록하지 않는다. `ALLOW`의 `OK`는 결과와 의미가 같으므로 생략한다. |
| `path` | diff 모드에서 파일 변경 요청의 검증된 workspace 상대 경로를 기록한다. |

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
| `ALLOW` | `decision=ALLOW`, `execution.status=SUCCESS` | 승인한 Tool 실행이 성공함. |
| `BLOCK` | `decision=BLOCK`, `execution=null`, 아래 시스템 오류 코드가 아님 | 요청 형식·정책·안전성 검사 등으로 차단됨. |
| `ERROR` | `decision=ALLOW`, `execution.status=ERROR` | 정책상 승인했으나 실제 실행에서 오류가 남. 쓰기·생성은 부분 변경 가능성이 있다. |
| `ERROR` | `decision=BLOCK`, `reason_code`가 `POLICY_ERROR`, `CONFIG_ERROR`, `UNSUPPORTED_PLATFORM`, `INTERNAL_ERROR` 중 하나 | 설정·플랫폼·내부 문제로 요청을 정상 처리하지 못함. 승인되거나 실행됐다는 뜻은 아니다. |

로그는 요청마다 `RESULT` 하나를 기록한다. `ERROR`는 승인 후 실행 실패와 설정·내부 문제로 인한 차단을
포함하며, 둘은 `reason_code`로 구별한다. 예상하지 못한 응답 조합은 `ERROR`로 분류한다.

```text
2026-11-09T12:01:13.824Z [ALLOW] write_file run_id=run-1 request_id=req-1 path=notes.txt
2026-11-09T12:01:14.010Z [BLOCK] read_file run_id=run-1 request_id=req-2 reason_code=NOT_ALLOWED
2026-11-09T12:01:14.051Z [BLOCK] <unknown> run_id=run-1 request_id=<unknown> reason_code=INVALID_REQUEST
2026-11-09T12:01:14.090Z [ERROR] create_file run_id=run-1 request_id=req-3 reason_code=ALREADY_EXISTS path=drafts/new.txt
2026-11-09T12:01:14.110Z [ERROR] read_file run_id=run-2 request_id=req-4 reason_code=POLICY_ERROR
```

위 예시의 `path`는 상세 감사 모드를 가정한다. `--audit`만 지정하면 `path`를 생략한다.
파일 변경 요청은 `BLOCK`이어도 경로가 계약 검사를 통과했다면 diff 모드의 감사 줄에 경로를 기록한다.
`INVALID_PATH`나 `PROTECTED_PATH` 같은 검증 오류에는 `path`를 생략한다.

현재 게이트웨이는 실행 중 `FenceError`가 발생하면 `BLOCK` 응답을 반환한다. 예를 들어 안전하지 않은 대상이나
읽기 크기 제한에 걸린 경우가 여기에 포함될 수 있다. 위 표는 **현행 응답의 분류**를 따르므로 이들은
`BLOCK`으로 표시한다. 실제 실행이 시작됐는지를 로그만으로 단정하려면 게이트웨이의 오류 상태 모델을 먼저
세분화해야 한다. `ERROR`를 모든 실행 단계 오류의 이름으로 쓰고 싶다면 구현 전에 응답 계약도 함께 개정한다.

## 파일 변경 diff 보관

`--audit-diff-dir DIR`을 지정하면 workspace 전체에서 `write_file`, `create_file`, `delete_file`이 수행한
변경을 파일별로 보관한다. `DIR`은 기존의 보호된 디렉터리여야 한다. `--audit`를 함께 지정하면 stderr 감사 줄에
변경 요약과 diff 식별자를 추가한다. `read_file`과 `list_files`에는 diff를 만들지 않는다.

### 파일 형식과 연결

diff 하나는 호출 하나의 변경을 담는다. workspace와 저장소 밖의 보호된 감사 디렉터리에
`patches/<patch_id>.diff`로 저장한다. `patch_id`는 시스템이 생성한 고유 값이다.
상세 모드의 감사 줄에는 `run_id`, `request_id`와 함께 저장이 끝난 diff의 `patch_id`를 기록한다.

```text
2026-11-09T12:01:13.824Z [ALLOW] write_file run_id=run-1 request_id=req-1 path=notes.txt change=modified patch_id=9d576cabe90a40de9741422f7586bec9
```

파일은 UTF-8로 읽을 수 있는 Git 스타일 unified diff로 기록한다. 헤더는 `a/`와 `b/` 뒤에 검증된
workspace 상대 경로를 사용하고 변경 구간에는 앞뒤 문맥을 최대 한 줄씩 붙인다. 생성·삭제 diff는
존재하지 않는 쪽을 `/dev/null`로 표시하며 파일 전체 내용이 들어갈 수 있다. 용도는 변경 검토다.

```diff
diff --git a/notes.txt b/notes.txt
--- a/notes.txt
+++ b/notes.txt
@@ -1,2 +1,2 @@
 회의 메모
-일정: 월요일
+일정: 화요일
```

### 생성 조건과 보관 한도

- 파일 Tool이 실제로 연 대상의 변경 전후 상태를 비교한다. 쓰기 요청의 `content`는 실제 결과와 다를 수 있다.
  수정 전후의 바이트가 같으면 `change=unchanged`로 기록한다. 외부 프로세스가 같은 파일을 동시에 수정하는
  환경에서는 관측한 전후 차이를 해당 Tool의 변경으로 확정할 수 없다.
- 쓰기 도중 `ERROR`가 나도 관측 가능한 변경은 위와 같은 규칙으로 기록한다. 변경 여부를 확인하지
  못하면 `change=unknown`으로 표시한다. 파일 변경 결과와 diff 저장 결과는 별도로 기록한다.
- 전후 입력은 각각 64 KiB와 2,048줄, diff 한 건은 32 KiB로 제한한다. 한도를 넘으면
  `diff=omitted_size`, UTF-8 텍스트로 다룰 수 없으면 `diff=omitted_format`, 전후 내용을 읽지 못하면
  `diff=capture_failed`를 남긴다. 새 diff를 저장할 때 7일이 지난 파일과 전체 20 MiB 한도를 넘게 하는
  오래된 파일을 지운다. 정리 후에는 로그의 `patch_id`만 남을 수 있다.
- 같은 디렉터리의 임시 파일에 기록을 마친 뒤 최종 이름으로 옮긴다. 저장에 실패하면 `diff=save_failed`를
  남기고 `patch_id`는 기록하지 않는다. diff는 선택적 검토 자료이므로 저장 실패가 Tool 결과를 바꾸지 않는다.
- 삭제된 줄과 문맥에도 민감한 내용이 남을 수 있다. diff 디렉터리는 운영 계정으로 접근을 제한하고,
  diff 본문을 stderr나 Tool 응답에 포함하지 않는다.
