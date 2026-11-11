# autocommit

`autocommit`은 herdr 안에서 실행되는 보수적인 Git 자동 관리 루프입니다. 허용 목록에 있는 저장소만 살피고, 변경이 충분히 안정된 뒤 지정한 코딩 CLI(claude·opencode·agy·codex)에게 원자적인 커밋 단위를 만들도록 요청합니다. push는 별도 검증을 통과한 경우에만 시도합니다.

아래 문서에서 **hoc**는 커밋을 맡는 실행기(`AC_HOC`, 기본 `bin/autocommit-agent`)를 가리킵니다. 로그·audit 출력에도 같은 이름으로 나옵니다.

## 설치와 설정

```bash
git clone https://github.com/namu-k/autocommit.git
cd autocommit
./install.sh --init-config
```

이 명령은 `~/.local/bin/autocommit`을 이 저장소의 `bin/autocommit` 심볼릭 링크로 설치합니다. 저장소 폴더를 옮기거나 지우면 링크가 끊기므로 설치한 위치에 둡니다. 기존 일반 파일은 타임스탬프가 붙은 백업으로 옮기며, 다른 심볼릭 링크와 디렉터리는 덮어쓰지 않습니다.

`--init-config`는 `~/.config/autocommit/repos.conf`가 없을 때만 예제를 복사합니다. 기존 `~/.config/autocommit/repos.conf`와 `scan-ignore`는 설치 과정에서 수정하지 않습니다. 예제는 [examples/repos.conf](examples/repos.conf)와 [examples/scan-ignore](examples/scan-ignore)에 있습니다.

`repos.conf` 한 행은 `저장소 루트 / commit 브랜치 glob / push 원격 / push 브랜치 glob(선택)`입니다. `-`는 그 단계를 끕니다. 신규 저장소는 자동 등록되지 않으므로, 탐지 결과를 검토한 뒤 사람이 이 허용 목록에 추가합니다.

최소 시작 순서:

```bash
$EDITOR ~/.config/autocommit/repos.conf          # 자동 관리할 저장소를 적는다
export AC_AGENT=claude                            # 커밋을 맡길 CLI (아래 표)
export AC_REMOTE_ALLOW='github\.com[:/]<계정>/'   # push를 허용할 원격 URL 정규식. 비워 두면 push하지 않는다
autocommit plan                                   # 판정만 확인
autocommit up                                     # herdr 안에서 loop 시작
```

아직 커밋이 없는 저장소도 첫 커밋을 만들 수 있습니다. 최초 실행 전후에 브랜치와 ref 상태를 확인하며, 깨진 ref는 첫 커밋 전 상태로 취급하지 않습니다.

## 명령

| 명령 | 역할 |
| --- | --- |
| `autocommit up` | 현재 herdr workspace에 `autocommit` 탭을 만들고 loop를 시작합니다. 현재 `AC_*` 환경 변수는 공백·따옴표·셸 문자를 셸 인용으로 보존해 넘기며, 출력에는 변수 이름만 남깁니다. |
| `autocommit loop` | herdr pane에서 루프를 전면 실행합니다. |
| `autocommit once` | 한 번만 판정·커밋·push 단계를 실행합니다. |
| `autocommit plan` | 상태 파일이나 로그를 쓰지 않고 판정만 출력합니다. |
| `autocommit push-only` | 커밋 없이 push 단계만 실행합니다. |
| `autocommit scan` | 허용 목록 밖 Git 저장소를 읽기 전용으로 찾아 출력합니다. |
| `autocommit audit` | 자동으로 풀리지 않는 보류 상태를 읽기 전용으로 점검합니다. |
| `autocommit unblock <저장소> <브랜치>` | hoc 검증 실패 뒤 남은 해당 브랜치의 push 금지 마커만 해제합니다. 같은 저장소에 미해결 hoc 실행 기록이 있거나 loop가 잠금을 쥐고 있으면 거절하므로 loop를 멈춘 뒤 실행합니다. 삭제에 실패하면 실패로 끝나고 마커는 남습니다. |
| `autocommit unblock <저장소>` | 미해결 hoc 실행 기록(아래 참고)을 해제합니다. loop가 잠금을 쥐고 있으면 거절하므로 loop를 멈추고 실행 중인 hoc가 없을 때만 됩니다. 두 형태 모두 잠금을 잡은 뒤 대상을 확인하고 지웁니다. |
| `autocommit tidy` | 효과가 없다고 증명되는 상태 마커만 지웁니다. loop가 돌고 있으면 잠금 때문에 건너뜁니다. |
| `autocommit review` | 정리 뒤 남은 항목을 읽기 전용 에이전트가 분류한 보고서로 만들고 메일로 보냅니다. |

`up`, `loop`, `once`는 herdr 환경에서만 실행됩니다. `plan`, `scan`, `audit`는 읽기 전용 확인에 쓸 수 있습니다.

## 커밋 위임 CLI 선택 (`autocommit-agent`)

기본 커밋 실행기 `bin/autocommit-agent`는 `AC_AGENT`로 고른 CLI를 headless로 실행해 커밋시킵니다. `AC_AGENT`가 없으면 `up`·`loop`·`once`는 시작하지 않습니다. 커밋 작성자는 대상 저장소의 git 설정을 따르고, `AC_AGENT_MODEL`로 모델을 고를 수 있습니다. 쓰는 CLI는 미리 로그인해 둡니다. 다른 실행기를 쓰려면 `AC_HOC=<명령>`을 지정합니다. 그 명령은 저장소 폴더에서 `<명령> -c <추가 지시>`로 불리고, 성공하면 0으로 끝나야 합니다.

| `AC_AGENT` | 권한 제한 | 비고 |
| --- | --- | --- |
| `claude` | `--permission-mode dontAsk` + git 조회·`add --`·`commit`만 허용 | 사용자 `CLAUDE.md`·설정은 그대로 읽습니다. 금지 명령을 시도하면 커밋 뒤에도 비영 종료할 수 있고, 그러면 loop가 push 금지 마커를 남깁니다. |
| `opencode` | 이번 실행 설정으로 기본 거부 + git 허용 목록, 저장소 설정 무시, 전용 서버 | 문서화되지 않은 `OPENCODE_CONFIG_CONTENT`·`OPENCODE_CONFIG_PROJECT_DISABLE`에 기대므로 opencode 업데이트 뒤 다시 확인합니다. |
| `agy` | `--settings`로 파일 쓰기와 push·reset 등 거부 | |
| `codex` | 없음 | codex sandbox는 `.git`을 읽기 전용으로 둬 커밋할 수 없습니다. `AC_AGENT_UNRESTRICTED=1`일 때만 제한 없이 실행하며, push 금지는 지시문뿐입니다. |

어느 경우든 커밋 뒤 HEAD·브랜치·직선 이력 검증과 push 판정은 autocommit이 그대로 합니다.

## 마커 정리와 주간 점검

loop는 하루 한 번 `audit` 직전에 `tidy`를 실행합니다. 지우는 것은 지금 아무것도 막지 않는다고 확인되는 마커뿐입니다.

- `fail-*`: `AC_FAIL_COOLDOWN`이 지난 실패 기록
- `nochg-*`: `git status`가 성공했고 워크트리 지문이 기록과 달라진 경우
- `nopush-*`: 원격 목적지 검사(아래 push 조건과 같음)를 통과한 실제 원격 SHA(`ls-remote`)가 이미 그 브랜치를 포함하는 경우만. 원격 조회가 오류·timeout으로 끝나면 출력이 일부 있어도 지우지 않습니다. 해제 로그에 원래 사유를 남깁니다.

설정상 push가 꺼졌거나, push 허용 브랜치가 아니거나, upstream이 `<원격>/<같은 이름>`이 아닌 것은 "지금 비활성"일 뿐 위반이 풀린 것이 아니므로 `nopush`를 지우지 않습니다. 설정을 되돌리면 그대로 다시 막으며, review의 마커 목록에 "현재 비활성"으로 표시됩니다. 원격 조회 실패·목적지 검사 실패·대상 매핑 불명, 아직 작동 중인 `nochg`(hoc이 커밋을 거절한 생성물 등)도 지우지 않습니다. 이것들은 `autocommit review`가 보고합니다.

audit의 `[상기]`(`AC_AUDIT_REMIND`) 문구는 `참고`로 표시하고 해결할 항목 수와 loop 알림에서 뺍니다.

`install.sh`가 설치하는 `autocommit-review.timer`는 매주 월요일 09:00에 `review`를 실행합니다. review는 loop의 잠금이 비어 있을 때만 정리하고, 그 뒤 audit 출력과 남은 마커 전체(작동 중인 `nochg`는 `git status`와 `.gitignore` 포함)를 `codex exec` 읽기 전용 sandbox에 넘깁니다. codex가 없으면 분류 없이 원본 점검 출력만 보고서로 남깁니다. 보고서는 `~/.local/state/autocommit/review-<시각>.md`, 입력은 `.input`, 로그는 `.log`에 남습니다. 항목으로 세는 것은 push 보류, push 금지 마커, `AC_STALE_MARKER_DAYS`(기본 3일) 이상 된 작동 중 `nochg`·매핑 불명 마커입니다. 그보다 새 마커와 스스로 풀리는 `fail`은 참고로만 싣고, 항목이 0건이면 에이전트와 메일을 생략합니다. 메일은 `AC_REVIEW_MAIL_CMD`(인자 = 제목, 표준 입력 = 본문)로 보내며 기본은 메일 없음입니다. unit에 지정하려면 `systemctl --user edit autocommit-review.service`에서 `[Service]` 아래 `Environment=AC_REVIEW_MAIL_CMD=<명령>`을 추가합니다. codex가 `~/.local/bin`·`/usr/local/bin`·`/usr/bin` 밖(nvm 등)에 있으면 같은 방법으로 `Environment=PATH=...`를 덮어씁니다. timer의 review는 loop의 환경 변수를 물려받지 않으므로 `Environment=AC_REMOTE_ALLOW=<loop와 같은 정규식>`도 넣습니다. 없으면 원격 확인이 모두 실패해 `nopush` 마커를 풀지 못합니다(안전한 쪽으로 남음). 메일 발송이 실패하면 unit이 실패로 끝납니다.

### hoc 실행 기록과 수동 복구

hoc를 부르기 전에 저장소 단위 실행 기록 `inflight-<저장소 해시>`(실행 ID·저장소·워크트리·시작 브랜치·시작 HEAD·시각, `key=value` 줄)를 원자적으로 씁니다. 쓰지 못하면 hoc를 시작하지 않습니다. 이 기록은 다음 경우에만 지웁니다.

- hoc가 성공하고 브랜치·HEAD 조회와 직선 이력 확인까지 끝난 경우
- hoc가 커밋을 만들지 않았거나 실패했지만 브랜치와 HEAD가 그대로임을 확인한 경우(실패는 기존 cooldown 뒤 재시도)
- 위반(실패 중 HEAD 이동, 비직선 이력, 브랜치 변경)을 필요한 `nopush` 마커에 모두 기록한 경우. 브랜치가 바뀌면 원래 브랜치와 바뀐 브랜치를 모두 막습니다.

loop가 hoc 도중 중단됐거나, hoc 뒤 HEAD·브랜치·직선성 조회가 실패했거나, detached HEAD가 됐거나, 위반 마커를 쓰지 못하면 기록이 남습니다. 남은 기록은 그 저장소의 자동 commit·push를 모두 막고, 재시작해도 자동으로 지우거나 성공으로 보지 않습니다. 기존 `nopush` 사유는 덮어쓰지 않고 뒤에 덧붙입니다.

복구 순서: `autocommit audit`의 `[미해결 hoc 실행 기록]`에서 기록 내용과 확인 명령을 봅니다 → loop를 멈추고 남은 hoc 프로세스가 없는지 확인합니다 → 시작 HEAD와 현재 HEAD·브랜치, `hoc.log`를 비교합니다 → `autocommit unblock <저장소>`로 실행 기록만 풉니다 → 필요한 브랜치만 `autocommit unblock <저장소> <브랜치>`로 풉니다. 해제는 보류 제거일 뿐 안전 검증이나 즉시 push가 아니며, 다음 push도 모든 검사를 다시 거칩니다. unblock의 `<저장소>`는 `repos.conf`의 행과 같은 실제 경로이면 별칭(심볼릭 링크·끝 `/`)으로 적어도 됩니다. 같은 저장소를 가리키는 행이 여럿이고 각각 기록이 있으면 모호하므로 지우지 않습니다.

이 기록을 모르는 이전 실행기로 되돌리면 기록이 무시돼 보호가 사라집니다. 되돌리기 전에 `inflight-*`가 남아 있지 않은지 먼저 확인합니다.

## 능동적 관리 정책

기본 루프는 작업 중인 저장소를 빠르게 확인합니다. 깨끗한 상태가 계속되면 다음 전체 확인 주기를 `AC_IDLE_MAX_INTERVAL`(기본 86400초, 1일)까지 늘려 커밋·push 판정과 원격 조회를 줄입니다. 매 기본 tick마다 브랜치 ref, 워크트리 목록, 각 워크트리의 `git status`를 가볍게 비교하므로 HEAD 변경뿐 아니라 파일 수정도 다음 tick에서 바로 깨워 빠른 주기로 되돌아옵니다. 이 비교는 `git --no-optional-locks`로 실행해 작업 중인 에이전트와 Git 잠금을 다투지 않습니다. `once`와 `plan`은 이 대기 정책을 우회합니다.

새 저장소 탐지는 `AC_SCAN_INTERVAL`(기본 86400초, 1일)마다 실행됩니다. 발견해도 사람이 `repos.conf`에 추가해야 하므로 늦게 알아도 잃는 것이 없습니다. 발견 개수와 첫 저장소 경로를 herdr 알림 창에 표시하고 요청 알림음을 지정합니다. 전체 목록은 `~/.local/state/autocommit/autocommit.log`와 `autocommit scan`으로 확인합니다. herdr 호출이 실패하면 로그에 경고를 남깁니다. 실제 화면 표시와 소리는 herdr 및 운영체제 알림 설정에 따릅니다. 발견 내용은 `repos.conf`에는 쓰지 않습니다. `AC_SCAN_IGNORE`에 둔 경로는 이미 검토해 제외한 대상이므로 탐지에서 빼며, 이 파일도 자동으로 수정하지 않습니다.

오래 지속되는 dirty 워크트리(커밋하지 않은 변경이 있는 작업 폴더)는 같은 HEAD에서 처음 관측된 뒤 `AC_CHECKPOINT_AFTER`(기본 10800초, 3시간)가 지나면 현황 조사를 실행합니다. 기본은 **탭을 만들지 않는 백그라운드 실행**입니다. `codex exec`를 `AC_CHECKPOINT_MODEL`(기본: codex 기본 모델)·`AC_CHECKPOINT_EFFORT`(기본 `high`)와 읽기 전용 sandbox·승인 요청 없음으로 실행합니다. 조사 중에는 별도 잠금으로 해당 워크트리의 자동 커밋을 보류합니다. 변경을 완료·검증된 커밋 후보, 작업 중인 변경, 커밋하면 안 될 파일로 나눠 보고할 뿐 파일 수정·stage·커밋은 하지 않습니다. 보고서는 `~/.local/state/autocommit/ckpt-<작업폴더와 HEAD 해시>.md`, 실행 로그는 같은 이름의 `.log`, 종료 코드는 `.rc`로 보존하며 완료·실패 시 Herdr 알림으로 경로를 안내합니다. 실행은 최대 30분으로 제한합니다. codex가 없으면 조사 대신 알림만 남깁니다. 같은 HEAD에서는 한 번만 조사하고, 깨끗해지거나 커밋되면 시간을 다시 셉니다. `AC_CHECKPOINT_AFTER=0`이면 조사를 끕니다. 이전 대화형 탭 방식은 `AC_CHECKPOINT_TRANSPORT=tab`으로 선택할 수 있습니다. 기존 조사 탭은 자동으로 닫지 않습니다. 에이전트가 `working`, `blocked`, `unknown` 상태인 워크트리는 계속 보류합니다. hoc에는 완료·검증된 원자적 변경 단위를 요청하고, 실행기는 hoc의 종료 상태와 전후 HEAD·브랜치·직선 이력을 검증합니다.

현재 작업 중인 부분과 이전 완료 작업이 같은 워크트리에 섞이면 파일 수정 시간만으로 안전하게 분리할 수 없습니다. 작업을 수행하는 에이전트가 완료한 변경 단위와 검증 결과를 확인해 그 부분을 직접 커밋하는 방식을 권장합니다. autocommit은 활성 에이전트의 변경 범위를 알 수 없으므로 해당 워크트리의 보류를 해제하지 않습니다. 3시간은 현황파악 기준이며, 미완성 변경을 강제 커밋하는 기한이 아닙니다. 소환된 에이전트가 `working`인 동안에는 같은 워크트리의 hoc 호출도 보류됩니다.

자동 push도 해당 브랜치 워크트리의 에이전트가 작업 중이거나 점유 조회가 실패하면 보류합니다. 추적 파일에 staged/unstaged 변경이 있거나 Git 작업이 진행 중이어도 보류하며, 기존 미추적 파일만 남은 경우는 push를 영구 차단하지 않습니다. 단, commit 브랜치가 `-`이고 push 브랜치를 별도로 지정한 저장소(예: `~/notes - origin main`)는 미커밋 변경을 그대로 두고 캡처한 커밋 SHA만 push합니다. 이 경우도 에이전트 점유·Git 작업·상태 조회·SHA 안정화·위험 파일 검사는 유지합니다. 브랜치 SHA를 처음 관측한 뒤 `AC_SETTLE` 동안 유지돼야 push합니다. amend/reset으로 SHA가 바뀌거나 작업 중 상태가 관측되면 안정화 시간을 다시 셉니다. loop 시작 직후에는 `AC_FIRST_SETTLE`을 적용합니다. 미전송 커밋이 있는 저장소는 검사 간격을 기본 주기로 유지합니다. push 직전에 점유·Git 상태와 브랜치 SHA를 다시 확인합니다. `plan`은 안정화 상태를 기록하지 않습니다.

위험 파일 검사는 원격 추적 ref부터 캡처한 SHA까지 새로 게시될 모든 커밋을 봅니다(merge는 부모마다 비교, 이름 변경은 삭제+추가로 취급). 중간 커밋에서 추가했다 지우거나 줄인 금지 경로·`AC_MAX_FILE_MB`(기본 50MB) 초과 blob도 보류하며, 이력·객체 조회나 해석이 실패해도 보류합니다. 원격은 push URL이 정확히 하나이고 fetch URL과 같으며 `AC_REMOTE_ALLOW`에 맞을 때만 push하고, 서브모듈 원격으로의 재귀 push는 끕니다. 에이전트 점유는 워크트리 루트와 그 하위 경로(가장 긴 워크트리 우선)로 판정하며, 작업 중인데 위치를 알 수 없는 에이전트가 있거나 응답 해석이 실패하면 commit·push 모두 보류합니다.

사람이 기존 커밋의 즉시 전송을 명시하는 `push-only`는 안정화 대기만 생략하며, 점유·미커밋 변경·원격·위험 파일·SHA 재확인은 동일하게 적용합니다. hoc에는 기존 이력을 고치지 않고 새 커밋만 만들도록 요청합니다. 이미 push된 커밋을 외부 세션에서 amend/reset하면 다시 분기할 수 있으므로, 공개된 커밋의 수정은 새 커밋으로 남깁니다.

## 요구 사항

실행 환경에는 `bash`, `git`, `jq`, GNU coreutils(`timeout`), util-linux(`flock`)가 필요합니다. `up`·`loop`·`once`에는 herdr와 커밋을 맡길 CLI 하나(claude·opencode·agy·codex)가 필요합니다. checkpoint 현황파악과 주간 review는 codex가 있을 때만 에이전트를 부르고, 없으면 건너뜁니다. 설치 스크립트는 실행기의 Bash 문법을 먼저 검사하지만 loop를 시작하거나 설정·상태를 바꾸지는 않습니다.

## 테스트

테스트는 Python 표준 라이브러리만 사용합니다.

```bash
python3 -m unittest discover -s tests -v
```

## 환경 변수

자주 조정할 값은 다음과 같습니다.

| 변수 | 기본값 | 역할 |
| --- | ---: | --- |
| `AC_AGENT` | 없음 (필수) | 커밋을 맡길 CLI: `claude`·`opencode`·`agy`·`codex` |
| `AC_AGENT_MODEL` | CLI 기본 | 커밋 CLI의 모델 이름 |
| `AC_REMOTE_ALLOW` | 없음 | push를 허용할 원격 URL 정규식. 비어 있으면 어떤 원격에도 push하지 않습니다 |
| `AC_HOC` | `bin/autocommit-agent` | 커밋 실행기 명령 (`<명령> -c <지시>`) |
| `AC_INTERVAL` | 900초 (15분) | 활동 중인 저장소를 확인하는 기본 주기 |
| `AC_IDLE_MAX_INTERVAL` | 86400초 (1일) | 깨끗한 저장소의 최대 확인 주기 |
| `AC_SCAN_INTERVAL` | 86400초 (1일) | 새 Git 저장소 탐지 주기 (`0`이면 loop 탐지 끔) |
| `AC_CHECKPOINT_AFTER` | 10800초 (3시간) | 오래 지속된 변경의 현황파악 에이전트 소환 기준 (`0`이면 끔) |
| `AC_CHECKPOINT_TRANSPORT` | `background` | 탭 없는 조사 실행. `tab`이면 이전 대화형 방식 |
| `AC_CHECKPOINT_MODEL` | codex 기본 | 현황파악 에이전트 모델 (`AC_CHECKPOINT_KIND` 기본 `codex`) |
| `AC_CHECKPOINT_EFFORT` | `high` | 현황파악 에이전트 reasoning effort |
| `AC_SETTLE` | 600초 | 커밋 전 워크트리 지문, 자동 push 전 브랜치 SHA의 안정화 시간 |
| `AC_FIRST_SETTLE` | 3600초 (1시간) | loop 시작 직후 적용할 더 긴 안정화 시간 |
| `AC_PUSH` | 1 | `0`이면 자동 push 단계 전체를 끕니다 |
| `AC_REMOTE_TIMEOUT` | 60초 | 원격 브랜치 조회 제한 |
| `AC_PUSH_TIMEOUT` | 900초 | 실제 push 전송 제한. 큰 이력의 압축·업로드에 조회보다 긴 시간을 허용합니다 |
| `AC_MAX_FILE_MB` | 50 | 게시될 이력의 blob 크기 상한(MB). 초과하면 push 보류 |

전체 변수와 검증 조건은 [bin/autocommit](bin/autocommit) 머리말을 참고하세요.

## 라이선스

[MIT](LICENSE)
