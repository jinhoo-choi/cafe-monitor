# 부정여론봇 쿠키 점검과 조건부 갱신

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
