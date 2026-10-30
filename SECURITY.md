# 보안 범위와 TOCTOU 설계

## 보호 범위

AgentFence는 게이트웨이를 경유하는 다섯 파일 Tool을 통제합니다.
신뢰 대상은 Python 코드/설정 로더/운영자/OS이고, 비신뢰 입력은 모델의 Tool 이름·인자·텍스트입니다.
Agent에 파일 실행기나 정책 수정 기능을 주입하지 않으며 eval, shell, Python 실행 Tool을 제공하지 않습니다.
정책에 없는 작업은 파일 Tool dispatch 전에 차단됩니다. 허용된 이름도 실행 직전 실제 핸들의 안전성을 검사합니다.

Sandbox와 OS 계정 격리는 구현하지 않습니다. 같은 프로세스의 악성 Python 코드가 비공개 모듈을 import하거나
`os.open`을 직접 호출하는 것을 Python 객체만으로 막을 수는 없습니다.
모델이 생성한 코드를 실행하거나 임의 Python 플러그인을 붙이면 이 위협 모델을 벗어납니다.
이를 허용하려면 별도 프로세스/계정/OS 권한 경계를 설계해야 합니다.

## 정책·API 키 분리

`config/policy.json`과 소스 코드는 `workspace/` 밖에 둡니다. workspace 안의 정책 경로는 구성 오류로 차단합니다.
키는 Gemini 어댑터가 환경변수로만 읽습니다. Tool은 환경변수 조회 기능을 제공하지 않습니다.
보호 이름과 확장자는 요청 및 정책 규칙 모두에서 거부합니다.
외부 정책/키 파일의 하드링크를 workspace에 만들어도 링크 수가 2개 이상인 일반 파일은 거부합니다.
워크스페이스에는 실제 비밀정보를 보관하지 않습니다. 평범한 이름으로 복사해 놓은 비밀 내용을 자동 식별하지는 않습니다.

## Windows 경로 처리

검증 범위는 로컬 Windows NTFS입니다. UNC/장치 경로를 설정으로 받지 않습니다.
로컬 드라이브 루트에서 시작하여 `NtCreateFile`의 `RootDirectory` 핸들을 기준으로 각 경로 조각을 하나씩 엽니다.
각 단계에 `FILE_OPEN_REPARSE_POINT`를 적용하고 핸들의 reparse 속성을 검사하여 링크, junction,
마운트/클라우드 reparse 지점을 거부합니다. `OBJ_DONT_REPARSE`도 함께 사용하지만 이것만을 방어 근거로 삼지 않습니다.
원래 경로 문자열을 다시 따라가서 파일 데이터에 접근하지 않습니다. 목록 조회도 자식 핸들을 검사합니다.
파일 핸들의 일반 파일 여부와 링크 수를 확인한 뒤 읽기/쓰기를 수행합니다.
삭제는 `SetFileInformationByHandle(FileDispositionInfo)`로 검사한 파일 객체를 대상으로 합니다.

공유 모드는 다른 쓰기/삭제를 제한하지만 최신 Windows의 POSIX 방식 이름 변경까지 막는다고 가정하지 않습니다.
테스트는 상위 디렉터리를 옮기고 원래 이름에 외부 junction을 넣어도 열린 상위 핸들 아래의 파일만 읽는지 검증합니다.
OneDrive Files On-Demand 등 reparse 지점이 포함된 경로는 안전 우회 없이 차단됩니다.
해당 경우 일반 로컬 폴더를 workspace로 지정합니다. 네트워크/가상 파일시스템/커스텀 필터는 검증 범위에 포함하지 않습니다.

## POSIX 경로 처리

`dir_fd`, `O_DIRECTORY`, `O_NOFOLLOW`로 루트부터 각 디렉터리를 엽니다.
leaf도 같은 상위 디스크립터를 기준으로 `O_NOFOLLOW`로 열고 `fstat`한 객체에만 읽기/쓰기를 수행합니다.
FIFO 등에서 멈추지 않도록 `O_NONBLOCK`을 사용하고 일반 파일이 아니면 거부합니다.
쓰기에는 검사 전 `O_TRUNC`를 사용하지 않습니다. 생성은 `O_CREAT | O_EXCL`을 사용합니다.
삭제는 고정된 상위 디스크립터 아래에서 inode를 다시 비교한 뒤 `unlink`합니다.
`unlink`는 leaf 심볼릭 링크를 따라가서 외부 파일을 삭제하지 않습니다.

## 남아 있는 동시 변경 한계

파일 핸들은 검사 후 다른 경로로 바뀐 심볼릭 링크를 따라가는 문제를 줄이지만 동시 로컬 공격자를 완전히 격리하지 않습니다.
같은 OS 권한의 외부 프로세스가 열린 디렉터리를 workspace 밖으로 옮기면 열린 핸들은 원래 객체를 계속 가리킵니다.
POSIX의 inode 비교와 `unlink` 사이에도 이름 교체가 가능하므로, 교체된 workspace 내부 leaf 이름이 삭제될 가능성이 남습니다.
POSIX에서 검사 직후 다른 프로세스가 하드링크를 추가하거나 파일 내용을 바꾸는 경쟁도 완전히 배제하지 않습니다.
mount/bind-mount 교체, 악성 파일시스템, 관리자/커널 공격은 범위 밖입니다.

따라서 운영자는 workspace/상위 경로를 신뢰할 수 없는 로컬 프로세스가 동시에 바꾸지 못하도록 OS 권한을 설정해야 합니다.
테스트 결과를 OS 전체 파일 격리 또는 모든 TOCTOU 공격 방지라고 해석하지 않습니다.

## 오류·로그·데이터 전송

차단 응답에는 실행 결과를 넣지 않습니다. 오류 응답에는 원본 예외/절대 경로/파일 내용을 노출하지 않습니다.
감사 로그는 요청 ID·판정·사유 코드만 기록하고 로그 sink는 운영자가 설정합니다.
쓰기/생성은 트랜잭션이 아니므로 실행 오류 시 부분 내용이나 빈 파일이 남을 수 있습니다.
`--gemini` 실행은 프롬프트와 허용된 파일 결과를 Gemini API에 전송하므로 필요한 파일에만 읽기 권한을 부여합니다.

## 구현 근거

- [Python os: dir_fd, O_NOFOLLOW, fstat](https://docs.python.org/3/library/os.html)
- [Microsoft NtCreateFile: RootDirectory와 reparse 처리](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile)
- [Microsoft 파일 reparse 지점 처리](https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points-and-file-operations)
- [Microsoft SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
- [Gemini generateContent API](https://ai.google.dev/api/generate-content)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
