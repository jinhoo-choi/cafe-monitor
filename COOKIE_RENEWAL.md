# 부정여론봇 쿠키 점검과 조건부 갱신

`monitor.yml`의 기존 수집 바로 앞에서 실행한다. 별도 주기 로그인은 만들지 않는다.
갱신과 수집은 `naver-cafe-monitor` 동시 실행 그룹을 공유한다.

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

- 정상 쿠키는 비밀번호 로그인과 Secret 쓰기 없이 유지한다.
- 실제 인증 실패가 확인된 경우에만 기존 `NAVER_ID`, `NAVER_PW`, `ADMIN_GITHUB_TOKEN`을 사용한다.
  해당 토큰에는 이 저장소의 Actions Secrets 쓰기 권한이 필요하다. 기존 큐 토큰의 Contents 권한만으로는 안 된다.
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
공개 `cookie_refresh_request.json`은 `operation`, `mode`, `request_id`만 받으며 `main`에서만 실행한다.
`auto`는 확인되지 않은 상태에서도 공개 범위 수집을 지속할 수 있도록 종료 코드와 인증 상태를 구분한다.

저장소 Actions variable `COOKIE_AUTO_RENEW_ENABLED=false`로 자동 갱신을 끌 수 있다.
수집 엔진, 수신자, 분석 정책, seen 이력은 바꾸지 않는다. 기존 쿠키 복구가 가능한지 별도로 확인해야 한다.
새 로그인으로 이전 세션이 무효화될 수 있어, 코드 되돌리기만으로 이전 쿠키가 복구된다고 보장하지 않는다.
다음 자연 회차의 Secret 재복원과 실제 회원 전용 본문 수집 확인 전까지 장기 운영 성공으로 평가하지 않는다.

## 오프라인 검증

`python -m unittest -v test_cookie_refresh test_cookie_provenance test_negative_verification test_negative_subscription`
쿠키·큐·브라우저·SMTP를 모의 처리하고 실제 인증/유료 AI를 호출하지 않는다.
