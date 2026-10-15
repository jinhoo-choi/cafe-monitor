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
`auto`는 확인되지 않은 상태에서도 공개 범위 수집을 지속할 수 있도록 종료 코드와 인증 상태를 구분한다.

저장소 Actions variable `COOKIE_AUTO_RENEW_ENABLED=false`로 자동 갱신을 끌 수 있다.
수집 엔진, 수신자, 분석 정책, seen 이력은 바꾸지 않는다. 기존 쿠키 복구가 가능한지 별도로 확인해야 한다.
새 로그인으로 이전 세션이 무효화될 수 있어, 코드 되돌리기만으로 이전 쿠키가 복구된다고 보장하지 않는다.
다음 자연 회차의 Secret 재복원과 실제 회원 전용 본문 수집 확인 전까지 장기 운영 성공으로 평가하지 않는다.

## 오프라인 검증

로그인 중 예외는 `login_context_unknown`, `login_navigation_unknown`,
`login_form_unknown`, `login_submit_unknown`, `login_result_unknown`,
`login_storage_unknown`의 고정 코드로만 단계별 보고한다. 원시 예외 메시지,
쿠키·페이지 본문·스크린샷은 출력하지 않는다. 24시간 재시도 제한과 추가 인증 중단 기준은 유지한다.

기존 쿠키 점검은 파일 존재 여부, 로그인 Secret 설정 여부(참/거짓), 저장 형식과
`NID_AUT`/`NID_SES`의 고정 상태 코드만 진단한다. 만료·누락·빈 값·도메인 불일치·만료 형식 오류를
구분하며 쿠키 값·만료 시각·계정명·비밀번호는 출력하지 않는다. 이 진단을 위해 로그인하거나
재시도 제한을 변경하지 않는다. 진단은 담당자용이며 임원용 정상 메일에 삽입하지 않는다.

`python -m unittest -v test_cookie_refresh test_cookie_provenance test_negative_verification test_negative_subscription`
쿠키·큐·브라우저·SMTP를 모의 처리하고 실제 인증/유료 AI를 호출하지 않는다.

## 2026-10-15 회원 카페 쿠키 자동 갱신 개선안 (미적용)

요청 범위는 개선안 마련입니다. 이 절은 설계 제안이며 로그인·Secret 교체·예약 변경을 실행하지 않습니다.

### 현재 확인 결과

- 2026-10-15 20:08 KST 실행 38725107517: 쿠키 파일 존재, 로그인 Secret 설정 확인, 저장 형식 정상. NID_AUT와 NID_SES는 모두 expired, 인증 상태 invalid입니다. 실제 로그인 0회·Secret 교체 0회입니다.
- 마지막 로그인 예약은 2026-10-15 10:01:38 KST이며 기존 24시간 제한상 다음 허용은 10월 16일 10:01:38 KST 이후입니다. 기존 정기 수집만 유지하면 그 이후 첫 회차는 15시입니다. 별도 예약을 생성하지 않습니다.
- 현재 access_status()는 인증 컨텍스트를 닫기 전에 새 storage_state를 반환하지 않습니다. healthy 분기도 현재 쿠키 유지 후 종료하므로 정상 접속 중 서버가 바꾼 쿠키를 다음 실행의 Secret에 반영하지 못합니다.

### 권장 구조: 검증된 세션 저장 우선, 무효할 때만 재로그인

| 단계 | 조건 | 조치 |
|---|---|---|
| 매회 점검 | 기존 08·15·22시 수집 직전 | 기존 Secret 복원 → 핵심 쿠키 실제 만료 상태 → 서로 다른 회원 전용 본문 2건 확인 |
| 정상 세션 유지 | healthy | 검증한 동일 컨텍스트에서 서버가 발급·변경한 storage_state 회수. 인증 쿠키가 변경된 경우만 별도 컨텍스트로 재검증 후 암호화 Secret 교체 |
| 재발급 | expired/invalid, 24시간 제한 충족 | 기존 NAVER_ID/PW로 로그인 최대 1회. 두 회원 전용 본문 확인 후 저장·동일 실행 적용 |
| 자동 처리 중단 | 캡차·2단계 인증·새 기기 확인 또는 결과 불확실 | 담당자에게만 상태 알림. Secret과 기존 쿠키 보존, 미확인 회원 본문 보류. 공개 범위는 기존 정책대로 수집 |
| 회복 확정 | 다음 자연 회차 | 새 실행에서 Secret을 다시 복원하고 회원 본문 2건 재확인. 카페 14곳의 인증 실패·가입/등급 제한·삭제·통신 오류를 구분해 실제 회복 범위를 집계 |

쿠키 expires 값을 임의로 늘리지 않습니다. 만료까지 24시간 이하이면 담당자용 사전 상태로 표시하며, 서버가 새 쿠키를 내주지 않은 경우 연장 성공으로 집계하지 않습니다. 이미 만료된 쿠키는 정상 접속 유지 단계만으로 복구할 수 없으므로 최초 재로그인이 필요합니다.

### 기존 코드 재사용 범위

1. cookie_auth.py access_status(): 선택적 출력 경로로 검증 성공 컨텍스트의 storage_state를 호출자에게 전달합니다. 상태 코드만 쓰는 기존 호출은 유지합니다.
2. refresh_cookies.py healthy 분기: 인증 쿠키 변경 여부를 메모리에서 비교하고, 별도 컨텍스트 재검증 → 기존 secret_key()/store_secret() → write_storage() 순서로 최대 1회 저장합니다. 동시 실행 그룹·예약 잠금·24시간 로그인 제한을 재사용합니다.
3. monitor.yml: 현행 사전 점검 연결과 종료 시 0600 임시 쿠키 제거를 유지합니다. 추가 크론·별도 로그인 봇·새 유료 서비스는 만들지 않습니다.
4. 담당자 진단에만 session_reused / session_updated / login_renewed / human_required / cooldown / storage_uncertain을 구분해 남깁니다. 임원 메일에는 인증 진단·스택·정상으로 가장한 누락 결과를 넣지 않습니다.

### 검증·배포 기준

- 정상이며 변경 없는 쿠키: 로그인 0, Secret 쓰기 0.
- 서버 변경 쿠키: 두 본문 독립 재검증 성공 시만 Secret 1회 저장. 실패·통신 불확실·권한 제한에서는 저장 0.
- 만료·무효: 24시간 내 중복 로그인 0. 보안 인증 시 반복 입력 0.
- Secret 저장 timeout: storage_uncertain으로 보류하고 자동 PUT 재시도 0. 다음 실행의 복원·실제 접근 결과로 확인합니다.
- 테스트를 통과해도 실제 회복 완료는 다음 자연 회차의 회원 본문 확보와 실제 카페별 수집 결과로 판정합니다. 8개 카페 문제가 모두 쿠키 때문이라고 가정하지 않습니다.

### 공식 문서와 해석 범위

- [네이버 로그인 상태 유지](https://help.naver.com/service/5640/contents/19013?lang=ko&osType=COMMONOS): 로그인 상태 유지 및 쿠키 보존을 설명합니다. 특정 인증 쿠키의 만료가 모든 접속에서 연장된다는 보장은 확인하지 못했습니다.
- [Playwright 인증 상태 재사용](https://playwright.dev/python/docs/auth): storage_state로 인증 상태를 저장·복원하는 기능을 제공합니다. 인증 상태 파일은 비밀값이므로 저장소·artifact·로그로 내보내지 않습니다.
- [네이버 공식 Open API 목록](https://developers.naver.com/docs/common/openapiguide/apilist.md): 공개 카페 API의 안내 범위는 가입·글쓰기이며 회원 전용 본문 수집을 OAuth refresh token으로 대체할 근거는 확인되지 않습니다.

권장안은 서비스가 실제 발급한 인증 상태를 검증해 보존하는 방식입니다. 무기한 로그인이나 추가 인증 없는 완전 자동 복구를 보장하지 않습니다.
