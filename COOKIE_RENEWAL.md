# 부정여론봇 쿠키 갱신 표준 절차

## 기본 경로 — 2026-11-23 소유자 확정

**국내 Windows CMD → 사용자가 네이버 로그인 → 쿠키 JSON 전체를 GitHub Secret에 붙여넣기 → 실행자가 읽기 전용 인증 검증** 순서로 처리한다.

사용자가 “쿠키 갱신”, “기존 방식”이라고 하면 이 경로를 기본으로 사용한다. 일반 갱신에 GitHub CLI OAuth 로그인, 광범위한 `repo` 권한 추가, 자동 로그인 러너 `mode=auto`를 끼워 넣지 않는다. 기존 자동 갱신 코드는 남아 있지만 기본 사용자 절차는 아니다. 자동 로그인 경로는 사용자가 그 방식을 명시적으로 별도 요청했을 때만 검토한다.

이미 새 쿠키 파일이 있으면 로그인·파일 생성을 반복하지 않고 **2번 Secret 등록부터** 진행한다. JSON 형식과 필수 쿠키 상태를 값 노출 없이 확인하면 된다. 현재 정상 인증이 확인된 Secret을 문서나 도구 변경 때문에 다시 등록하지 않는다.

### 1. 국내 PC에서 쿠키 파일 만들기

가장 간단한 방법은 저장소의 [renew_naver_cookies.cmd](renew_naver_cookies.cmd)를 국내 Windows PC에 내려받아 실행하는 것이다.

- 최초 실행에는 Python의 `py` 명령이 필요하다. CMD가 사용자 폴더에 가상환경과 Playwright를 준비하고 다음 실행에서 재사용한다.
- 열린 브라우저에서 사용자가 네이버 로그인·추가 인증을 직접 완료한다.
- 같은 브라우저에서 회원 카페 게시글 본문이 열리는지 확인한다.
- 브라우저와 Playwright Inspector를 정상 종료하면 쿠키 파일이 저장된다.
- CMD는 JSON 형식과 `NID_AUT`·`NID_SES`의 값 존재·도메인·만료 상태를 검사한다. 이 검사는 실제 회원 본문 인증 성공 판정이 아니다.
- 원본 쿠키 값은 유지하고 줄바꿈 없는 Secret용 JSON을 별도 생성해 클립보드에 복사한다. 한 줄 JSON은 Actions에서 JSON 구두점까지 광범위하게 마스킹되는 일을 줄이기 위한 형식이며 쿠키 값·만료를 바꾸지 않는다.
- GitHub Secret 설정 화면이 열린다. CMD는 로그인 정보나 쿠키를 채팅·저장소·artifact에 보내지 않으며 CLI OAuth나 자동 로그인 갱신을 실행하지 않는다.
- 이미 만들어진 원본 JSON도 정상 형식이면 그대로 등록할 수 있다. 한 줄 형식으로 바꾸기 위해 정상 Secret을 다시 저장할 필요는 없다.

CMD에 직접 붙여넣는 기존 명령을 원하면 아래를 안내한다.

```cmd
if not exist "%USERPROFILE%\naver-cookie-refresh" mkdir "%USERPROFILE%\naver-cookie-refresh"
cd /d "%USERPROFILE%\naver-cookie-refresh"

if not exist ".venv\Scripts\python.exe" py -m venv .venv
.venv\Scripts\python.exe -m pip install playwright
.venv\Scripts\python.exe -m playwright install chromium

set "COOKIE_REFRESH_FILE=%CD%\naver_cookies_%RANDOM%_%RANDOM%.json"
.venv\Scripts\python.exe -m playwright codegen --save-storage="%COOKIE_REFRESH_FILE%" https://nid.naver.com/nidlogin.login
```

로그인·본문 확인 후 브라우저와 Inspector를 닫고 같은 CMD에서 이어간다.

```cmd
clip < "%COOKIE_REFRESH_FILE%"
start "" "https://github.com/jinhoo-choi/cafe-monitor/settings/secrets/actions"
```

이 직접 명령 방식은 파일 검사를 자동 수행하지 않는다. 파일이 없거나 로그인 완료가 불확실하면 등록하지 말고 값을 노출하지 않는 JSON·필수 쿠키 검사를 먼저 한다. 자동 검사가 필요하면 위 CMD 파일을 사용한다.

### 2. NAVER_COOKIES_JSON에 JSON 전체 붙여넣기

설정 화면: https://github.com/jinhoo-choi/cafe-monitor/settings/secrets/actions

1. 기존 `NAVER_COOKIES_JSON`의 수정 버튼을 누른다. 없을 때만 같은 이름으로 생성한다.
2. Value에 생성된 JSON **내용 전체**를 붙여넣는다. 파일명·경로·코드 블록 표시·설명 문구·base64를 넣지 않는다.
3. **Update secret**을 누르고 저장 완료 표시를 확인한다.
4. 담당 대화에 **“Secret 저장 완료. 쿠키 인증 검증해줘.”**라고 알린다.
5. 저장 후 클립보드를 비운다. CMD 파일은 사용자가 저장 후 키를 누르면 이를 수행한다.

직접 명령 방식에서는 다음으로 클립보드를 비울 수 있다.

```cmd
powershell -NoProfile -Command "Set-Clipboard -Value ''"
```

쿠키 원문·비밀번호·인증번호를 채팅에 보낼 필요가 없다. Secret 저장은 사용자 웹 화면의 기존 저장소 권한으로 처리한다. 실행자의 API/CLI에서 `403`이 나면 그 경로의 권한 오류로 기록하고 웹 붙여넣기 절차를 그대로 사용한다. 추가 OAuth 로그인이나 권한 확대로 해결된다고 추정하거나 사용자를 반복 인증시키지 않는다.

### 3. 실행자가 읽기 전용으로 검증하기

사용자가 저장 완료를 알리면 실행자가 기존 실행 상태를 먼저 확인하고, 중복 검증이 없을 때 아래 **한 경로만** 사용한다.

**국내 러너가 온라인이면 기존 `check` 요청:**

- 최신 main의 engine·워크플로·요청 파일 SHA와 관련 실행 상태를 확인한다.
- `cookie_refresh_request.json`에 다음 메타데이터만 최신 SHA로 갱신한다. `request_id`는 이번 검증을 구분하는 고유 값으로 만든다.
- 요청을 재조회하고 해당 커밋의 Actions 실행 ID를 확인한다.
- `mode=check`는 새 로그인·Secret 쓰기·메일·운영 수집 없이 국내 검사 후 클라우드 검사를 수행한다.

```json
{"operation":"cookie_preflight","mode":"check","request_id":"고유한-검증-ID"}
```

**아직 요청하지 않았고 국내 러너를 사용할 수 없다면:**

- 국내 러너에 의존하지 않는 기존 `monitor.yml`의 **main, dry_run=true** 검증을 사용한다.
- 실행자가 기존 연결로 dispatch할 수 없으면 사용자에게 아래 화면에서 **main → dry_run=true → Run workflow**만 요청한다. CLI 추가 로그인은 요구하지 않는다.
- 이미 `check`가 제출돼 배정 대기라면 같은 검증을 중복 요청하거나 자동 로그인으로 바꾸지 않는다. 기존 실행의 상태와 필요한 러너 조치를 알린다.
- 검증 경로를 전환할 때는 기존 대기 실행이 해소됐는지 먼저 확인한다. 실행 중인 검증을 이유 없이 취소하거나 중복 생성하지 않는다.

워크플로 화면: https://github.com/jinhoo-choi/cafe-monitor/actions/workflows/monitor.yml

### 완료 기준

- Secret에서 복원된 `NID_AUT`와 `NID_SES`가 존재하고 유효하다.
- 서로 다른 회원 게시글 2건 모두 익명 접근에서는 제한되고, 인증된 컨텍스트에서는 실제 본문이 확보된다.
- 본문 요청에 인증 쿠키가 실제 전달됐는지 정제 진단을 확인한다.
- **클라우드 `healthy`가 필수**다. 국내와 클라우드를 모두 검사했다면 각각 결과를 구분한다.
- `login_attempted=false`, `secret_updated=false`, 유료 AI/API 0, 메일·운영 수집·운영 이력 쓰기 0을 확인한다. 여기서 `secret_updated=false`는 검증 실행이 Secret을 다시 쓰지 않았다는 뜻이며, 사용자의 직전 웹 등록 실패를 뜻하지 않는다.
- `session_reused`는 복원된 Secret의 인증 성공을 뜻한다. 정상 쿠키를 다시 로그인하거나 덮어쓰지 않는다.
- Actions success·쿠키 파일 존재·Secret 저장 화면만으로 완료라고 하지 않는다.
- 읽기 전용 검사로 현재 인증을 확인한 것과 다음 자연 운영 회차의 실제 수집·분석·발송 성공을 구분한다. 실패 회차를 소급 수집·발송하지 않는다.

최종 안내는 다음처럼 짧게 한다.

> 쿠키 갱신 반영 확인: 국내/클라우드 healthy, 회원 본문 2건 확보, 로그인·Secret 재변경·메일·유료 API 0. 검증 Actions 링크. 다음 자연 운영 회차는 별도 확인.

### 실패하면 해당 단계만 확인

| 상태 | 다음 조치 |
| --- | --- |
| JSON 오류·필수 인증 쿠키 누락·만료 | 파일/로그인 완료 여부 확인. 파일이 잘못됐을 때만 새로 생성 |
| Secret 웹 저장 미완료 | JSON 전체를 올바른 Secret에 붙여넣고 Update secret 확인 |
| 실행자의 Secrets API 403 | 웹 등록 유지. 권한 확대·반복 OAuth로 우회하지 않음 |
| 국내 runner queued | 온라인/할당 상태를 구분. Secrets 갱신 실패로 단정하지 않음 |
| cloud auth invalid / human_required | 실제 원문 상태 보고, 추가 인증은 사용자 처리. 반복 로그인 금지 |
| 요청/저장 결과 불확실 | 반영 여부부터 확인. 무작정 재요청하지 않음 |
| Actions JSON 로그 일부 마스킹 | 보이는 고정 상태·본문 판정·전송 상태만 대조. 마스킹 내용을 복원하지 않음 |

### 재사용할 실제 확인 근거

2026-11-23 KST 수동 웹 등록 후 [검증 실행 45037202665](https://github.com/jinhoo-choi/cafe-monitor/actions/runs/45037202665)가 성공했다. 국내 `00:15:16`, 클라우드 `00:16:31` 검사 모두 `healthy`였으며 익명 `auth_required` 2건, 인증 `body` 2건, 본문 요청 쿠키 전달을 확인했다. 새 로그인·Secret 재변경·메일·유료 AI/API는 없었다. 이 기록은 절차의 실제 검증 근거이며 다음번 쿠키의 정상 여부를 대신하지 않는다.

---

## 기존 자동 갱신 구현 참고 — 일반 수동 갱신의 기본 경로 아님

아래는 기존 코드의 동작·보호 조건에 대한 기술 참고다. 일반 사용자 갱신은 위 수동 절차를 우선한다. 기존 코드·엔진·수신자·일정은 이 문서 정리로 변경하지 않는다.

수집은 `monitor.yml`의 GitHub Ubuntu에서 실행하고, 쿠키 갱신만 국내 노트북에서 실행한다.
수집 전에는 저장된 GitHub Secret 쿠키를 복원해 `check`로 실제 회원 본문 접근만 확인한다.
클라우드에서 비밀번호 로그인·Secret 갱신·갱신 요청이나 노트북 완료 대기를 실행하지 않는다.
확인 실패 시 회차 생성 전에 수집을 보류하고 기존 본인 오류 주소로 인증 확인 필요를 알린다.
국내에서 유효한 쿠키라도 클라우드 접속 환경에서 거절될 수 있으며 부정 없음으로 취급하지 않는다.

수집은 `naver-cafe-monitor`, 갱신은 `naver-cafe-cookie-renewal` 그룹으로 분리한다.
노트북이 꺼져 갱신이 대기해도 클라우드 수집 잠금을 점유하지 않는다.
사용자는 갱신 필요 알림을 받으면 노트북을 켜서 기존 갱신 경로 또는 수동 로그인을 사용한다.
상시 노트북 실행·자동 깨우기·새 주기 로그인/예약을 만들지 않는다.
`monitor.yml`의 `dry_run=true`는 큐 쓰기·메일 없이 클라우드 출구 국가와 회원 본문 인증을 검증한다.

## 인증 기준

`NAVER_COOKIE_PROBES_JSON` Secret 또는 비공개 큐의
`subscription/negative/auth/cookie_probes.json`에 서로 다른 카페 게시글의 기본 HTTPS 주소 2개를 지정한다.
파일 구조는 `{"schema_version":1,"urls":["주소1","주소2"]}`이다.
검색 링크의 `art` 등 인증·검색 매개변수는 저장하지 않는다. 게시글 주소는 공개 요청 파일에 넣지 않는다.

두 게시글 모두 익명 상태에서 로그인/회원 접근 제한이 확인되고, 현재 쿠키의 별도 컨텍스트에서
실제 본문이 확보될 때만 `healthy`로 본다. 공개 게시글, 삭제된 글, 등급 제한,
네트워크 장애, HTML 메뉴, 공개 네이버 홈 HTTP 200은 인증 성공 근거로 사용하지 않는다.
확인할 수 없는 경우 기존 수집 시도를 유지하면서 인증 미확인을 수집 메타데이터와 기존 HTML 안내에 표시한다.
실제 인증 실패 또는 추가 인증 상태가 확인되면 회원 전용 수집을 보류하고 공개 범위는 계속 수집한다.

## 갱신과 비밀값

- 정상 쿠키는 비밀번호 로그인 없이 재사용한다. 두 회원 본문을 확인한 동일 컨텍스트에서
  서버가 갱신한 상태를 메모리로 회수한다. 인증 쿠키가 같으면 Secret 쓰기 없이 유지한다.
  인증 쿠키가 바뀐 경우에만 그 후보를 별도 컨텍스트의 두 회원 본문으로 재검증하고
  Secret을 1회 암호화 교체한 뒤 0600 파일로 같은 실행에 적용한다.
  추적 쿠키만 바뀐 경우 저장하지 않으며 만료 시각을 임의로 늘리지 않는다.
- 실제 인증 실패가 확인된 경우에만 기존 `NAVER_ID`, `NAVER_PW`와 승인된 인사이트 전환용 키를 사용한다.
  인사이트 `INSIGHT_QUEUE_TOKEN`을 10/14 암호화 복사한 `NEGATIVE_QUEUE_TOKEN`을
  쿠키 단계의 `ADMIN_GITHUB_TOKEN` 환경변수에 연결한다. Secret 값을 로그·코드·채팅으로 읽거나
  기존 `ADMIN_GITHUB_TOKEN` Secret을 덮어쓰지 않는다. 무효한 기존 ADMIN 키로 폴백하지 않는다.
  해당 토큰에는 이 저장소의 Actions Secrets 쓰기 권한이 필요하다. Contents 권한만으로는 안 되므로
  `check_access`의 Secrets 공개키 GET을 먼저 확인하고, 거부되면 로그인·Secret 교체 없이 중단한다.
- 기존 `NEGATIVE_QUEUE_TOKEN`으로 비공개 `auth/cookie_control.json`에 로그인 시도를 먼저 예약한다.
  blob SHA 충돌, 저장/재조회 실패, 24시간 이내 기존 시도가 있으면 로그인하지 않는다.
  이 파일에는 시도 시각·실행 ID·알림 시각만 있고 쿠키·비밀번호·본문·주소는 없다.
- 캡차/보안인증에서는 1회만 시도하고 중단한다. 인증을 우회하지 않는다.
- 새 쿠키는 별도 컨텍스트에서 두 실제 본문을 검증한 후에만 Secret을 암호화 교체한다.
  `NID_AUT`/`NID_SES`의 실제 expiry를 확인하고 세션 쿠키 `expires=-1`을 만료로 간주하지 않는다.
  임의 만료 연장은 하지 않는다.
- Secret PUT에는 timeout이 있고 자동 재시도가 없다. timeout은 `storage_uncertain`으로 분리한다.
  성공한 Secret 저장과 본인 알림 실패를 구분한다. 쿠키 파일은 0600으로 같은 실행에 적용하고 종료 시 제거한다.
- 쿠키·비밀번호·브라우저 본문·스크린샷·추적 자료는 로그, 공개 파일, artifact에 남기지 않는다.

## 검수와 복구

Actions의 수동 실행은 기본 `check`다. `check`는 새 로그인·Secret·비공개 제어 상태 쓰기·메일 없이
기존 인증만 확인하고, 정상으로 확인되지 않으면 실패 상태로 종료한다.
`check_access`는 인사이트 전환용 키를 연결한 ADMIN_GITHUB_TOKEN 환경변수로 Secrets 공개키 GET만 진단한다.
쿠키 복원·브라우저·네이버 로그인·큐 쓰기·Secret PUT·메일은 실행하지 않는다.
공개 `cookie_refresh_request.json`은 `operation`, `mode`, `request_id`만 받으며 `main`에서만 실행한다.
`auto`도 인증 실패·미확인·갱신 실패 시 종료 코드 2로 종료한다. Actions의 성공 표시가 인증 실패를 가리지 않도록 한다.
공개 수집 분리는 별도 변경 대상이며 현재 클라우드 수집의 사전 인증 검사는 그대로 유지한다.

`check` 요청은 국내 노트북 점검 뒤 클라우드의 읽기 전용 점검도 수행한다. 국내 본문 확인이 실패해도
모드 검증이 완료된 `check`라면 클라우드 결과를 따로 확인한다. 두 단계 모두 비밀번호·Secret 쓰기·메일·
수집·운영 이력을 사용하지 않는다. 클라우드 후속 점검에는 로그인 및 발송 자격 증명을 전달하지 않는다.

본문 API 요청의 `NID_AUT`/`NID_SES`는 `present` / `missing` / `unverified`만 기록한다.
쿠키 값·요청 헤더·원문 주소를 출력하지 않는다. 저장 상태의 `root` / `restricted` / `multiple`은
쿠키 범위 진단이며 정상 인증 판정은 계속 두 실제 회원 본문에만 근거한다.

로그인 결과는 고정 2.5초 대기 대신 최대 20초 동안 결과 신호를 기다린다.
`credentials_rejected`, `login_form_rejected`, `human_required`, `login_result_timeout`,
`login_cookie_missing`을 구분한다. 추가 인증은 우회하지 않으며 새 로그인은 기존 24시간 제한 내 1회뿐이다.

저장소 Actions variable `COOKIE_AUTO_RENEW_ENABLED=false`로 자동 갱신을 끌 수 있다.
수집 엔진, 수신자, 분석 정책, seen 이력은 바꾸지 않는다. 기존 쿠키 복구가 가능한지 별도로 확인해야 한다.
새 로그인으로 이전 세션이 무효화될 수 있어, 코드 되돌리기만으로 이전 쿠키가 복구된다고 보장하지 않는다.
다음 자연 회차의 Secret 재복원과 실제 회원 전용 본문 수집 확인 전까지 장기 운영 성공으로 평가하지 않는다.

## 오프라인 검증

로그인 중 예외는 `login_context_unknown`, `login_navigation_unknown`,
`login_form_unknown`, `login_submit_unknown`, `login_result_unknown`,
`login_storage_unknown`의 고정 코드로만 단계별 보고한다. 원시 예외 메시지,
쿠키·페이지 본문·스크린샷은 출력하지 않는다. 24시간 재시도 제한과 추가 인증 중단 기준은 유지한다.
예외 진단에는 고정 분류 `timeout` / `unexpected`만 추가한다.
2026-10-16 공개 로그인 폼에서 확인한 `loginBtn_column` / `loginBtn_row` 중
표시된 버튼을 1회 누른다. 구형 `log.login` 버튼 호환성도 유지한다.

정상 세션의 담당자용 `renewal_action`은 `session_reused` / `session_updated`로 구분한다.
`check` 모드는 서버 변경 상태를 저장하지 않는다. 변경 후보의 재검증 실패·Secret 권한 오류·
저장 timeout에서는 기존 로컬 쿠키를 보존한다. 저장 timeout은 자동 PUT 재시도를 하지 않는다.
갱신 상태는 로그용 `probe_checks`와 분리된 메모리 객체로만 전달한다.

기존 쿠키 점검은 파일 존재 여부, 로그인 Secret 설정 여부(참/거짓), 저장 형식과
`NID_AUT`/`NID_SES`의 고정 상태 코드만 진단한다. 만료·누락·빈 값·도메인 불일치·만료 형식 오류를
구분하며 쿠키 값·만료 시각·계정명·비밀번호는 출력하지 않는다. 이 진단을 위해 로그인하거나
재시도 제한을 변경하지 않는다. 진단은 담당자용이며 임원용 정상 메일에 삽입하지 않는다.

`python -m unittest -v test_cookie_refresh test_cookie_provenance test_negative_verification test_negative_subscription`
쿠키·큐·브라우저·SMTP를 모의 처리하고 실제 인증/유료 AI를 호출하지 않는다.
