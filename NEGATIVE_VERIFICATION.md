# 부정여론 개선안 검수

검수는 codex/negative-subscription-verification에서 본인 전용으로 실행한다. 운영은 main의 negative_subscription_config.json engine=subscription일 때만 수집·소비한다. 기존 수집과 SMTP 수신자는 유지하고 감성 판정·유사 묶음·다발 답변의 유료 API를 차단한다. engine=paused는 발송·새 회차 쓰기를 중지하며, API 복귀는 비용 재발생을 설명하고 사용자가 별도로 결정해야 한다. 자동 API 폴백은 없다.

## 실제 환경

기존 네이버 로그인 쿠키와 같은 14개 카페·블로그 수집 함수를 재사용한다. 최신 24시간의 미처리 입력과 지정한 실제 과거 게시글을 구분해 읽는다. 공개 블로그 프로필 URL은 게시글로 취급하지 않는다. 기존에 수집한 게시글 ID와 운영 로그의 과거 점수는 비교 근거로만 사용하며 유료 API로 재분석하지 않는다.

본문을 읽지 못하면 needs_review·score=null로 보류한다. 대상 회사·본문·단순 문의·광고·사칭·별도 계열사·시장 손실을 구분한다. 감성 점수와 현재 주문/출금 등 우선 확인 여부를 분리한다. 모든 입력 ID와 원문 연속 인용·수치를 검증한다.

보이지 않는 공백·구두점·마스킹 링크만 있는 본문도 미확보로 취급한다. 한투 단축명은 신한투자증권·대한투자증권 이름에 포함된 글자와 구분한다. 이전 입력의 검색 신호가 잘못 잡혔더라도 실제 한국투자증권 언급 없이 부정 결과를 소비하지 않는다.

긴 본문은 대상 회사가 언급된 문단을 먼저 발췌 예산에 넣은 뒤, 앞부분·끝부분·불편 표현을 추가한다. 원문 순서로 정렬한 뒤 전체 길이를 잘라 회사 문단을 잃지 않도록 한다. 이미 저장한 입력은 덮어쓰지 않는다. 전체 원문의 대상 언급 신호만 있고 제목·발췌에 대상 문단이 없으면 결과 검증도 확인 필요 외 판정을 거절한다.

요약은 작성자 주장과 확인되지 않은 사실을 구별한다. 내부 점검과 검토용 답변 초안을 따로 표시하며 허구의 개인 경험·해결 완료·원인을 적지 않는다. 글을 자동 게시하지 않는다. 그룹은 같은 증상·같은 실제 사건 근거가 있을 때만 구성하고 게시글 수를 피해자 수로 표시하지 않는다. 30개 초과 이슈는 비공개 deferred 자료로 보존하고 운영 이력에 넣지 않아 다음 수집에서 재검토한다. 확인 필요도 처리 완료로 저장하지 않는다.

메일에는 부정 이슈와 보류 목록만 넣고 비부정 전체 표·모델 확인 메타데이터는 비공개 결과에 보관한다. 수집원 오류·접근 보류(곳), 본문 미수집(건), 발췌·귀속 확인(건)을 따로 표시한다. 최근 회차에 과거 시뮬레이션 자료를 혼입하지 않는다.

## 비공개와 본인 발송

입력·분석·HTML·영수증은 jinhoo-choi/insight-bot-data main의 subscription/negative에만 저장한다. 쿠키는 브라우저에만 사용하고 큐에 저장하지 않는다. 입력 전 연락처·이메일·계좌·외부 주소를 마스킹한다. 공개 요청과 로그에는 제한된 메타데이터만 남긴다.

TEST_MODE=1, NOTIFY_EMAIL=GMAIL_USER, EMAIL_CC 비어 있음이 모두 확인돼야 발송한다. SMTP envelope에도 GMAIL_USER 한 주소만 넣는다. sending 선기록 후 한 번만 발송하고, SMTP accepted와 이력 불변을 확인한 경우만 verified·self_test=true로 기록한다. sending/uncertain/verified는 다시 발송하지 않는다.

검수 워크플로는 운영 이력을 읽기 전용 파일로 받고 DB를 커밋하지 않는다. NEGATIVE_QUEUE_TOKEN은 사용자 승인으로 기존 정상 큐 키를 암호화 복사한 Secret이며 기존 ADMIN_GITHUB_TOKEN을 변경하지 않는다.

운영 수집은 DB 쓰기·메일 없이 production 회차만 만든다. 120분 지난 pending 중 영수증 없는 회차만 expired로 보존하고 다음 수집을 허용한다. sending/uncertain/delivery_complete는 잠금을 유지한다. 소비는 원문·해시·ID·점수·인용·수치·만료를 검증한 뒤 기존 수신자를 순서대로 한 주소씩 SMTP에 제출한다. 부정 이슈가 없으면 기존처럼 본인 상태메일만 제출한다. SMTP 로그인 실패는 pending을 유지하고, 접수 시도 후 연결 끊김·일부 거부는 uncertain으로 잠그며 자동 재발송하지 않는다.

모든 intended 수신자의 accepted 확인 후 delivery_complete로 기록한다. 비부정과 실제 발송한 그룹의 ID만 seen_posts.json에 원자적으로 저장하며 기존 30일 이력 유지 기간을 적용한다. main push는 3회 재시도하고 rebase 충돌 시 중단한다. main 이력 해시·ID를 다시 확인한 ACK가 있어야 completed가 된다. push·ACK 실패 시 접수된 메일을 재발송하지 않는다. 수집·소비 워크플로는 같은 concurrency 잠금을 쓰고 실패 시 본인 오류 알림을 보낸다.

GitHub GET 500 등 일시적 읽기 오류만 최대 3번 재시도한다. 401·403과 모든 PUT는 자동 재시도하지 않는다. SMTP 실패·사용량 부족을 유료 API로 우회하지 않는다.

## 순서

1. connection으로 비공개 읽기·쓰기 확인: API0/SMTP0.
2. collect로 실제 최근·과거 입력 수집: API0/SMTP0/운영 DB 쓰기0.
3. ChatGPT 구독에서 입력 전체 판정과 이슈별 문구 작성, 비공개 results에 저장.
4. consume·self_test=true로 본인 메일, private verified·accepted·self_test=true·API0 확인.
5. 사용자가 2026-10-14 보완·실환경 시뮬레이션 후 운영 반영을 승인했다. 추가 실제 본인 검수와 CI를 통과한 버전을 main에 반영한 뒤 다음 자연 회차를 확인한다. 기존 외부 08/15/22시 수집 호출은 유지하고 구독 분석은 08:15/15:15/22:15 KST로 연결한다. 다른 봇과 블로그 예약을 변경하지 않는다.

## 롤백

negative_subscription_config.json의 engine을 paused로 바꿔 새 분석·발송을 중지한다. 최신 seen_posts.json·비공개 영수증을 보존하며 이미 SMTP에 접수한 메일을 재발송하거나 이전 이력으로 덮어쓰지 않는다. 보내지 않은 영수증 없는 pending만 수동 취소할 수 있다. API 모드 복귀는 유료 비용 재발생에 대한 사용자의 별도 결정으로만 한다. 기존 코드·워크플로 기준 커밋은 5ea33a3cd64807da02cd0d30c622894eea101a92이다.

## 2026-10-14 검수 증거

- 실제 수집: 최근 22건·과거 3건. [수집 Actions](https://github.com/jinhoo-choi/cafe-monitor/actions/runs/38502718385) 성공.
- 구독 판정: 부정 1건·비부정 22건·확인 필요 2건. 본문 미수집 1건과 대상 문단이 없는 발췌 1건은 정상으로 단정하지 않았다.
- 본인 SMTP: 12:04 KST, accepted 1건. [본인 검수 Actions](https://github.com/jinhoo-choi/cafe-monitor/actions/runs/38504292554) 성공.
- 비공개 회차 negative-verification-38502718385: verified·self_test=true, paid_ai_calls=0, production_recipients=0, production_seen_writes=0. SMTP 접수와 받은편지함 수신은 별도다.
- 모바일 390px·데스크톱 900px 메일 미리보기에서 가로 넘침 없음. 원문·문구·HTML·수신자 영수증은 비공개 큐에만 저장한다.
- 이 코드는 검수 전용이며 main 병합·운영 예약 전환·운영 메일 재발송을 수행하지 않았다.


## 임원용 본문과 운영자용 진단 분리

운영 발송은 기존 `GMAIL_USER`를 운영자 주소로 사용한다. 같은 회차라도 운영자는
수집 오류, 쿠키 상태, 본문 미수집·귀속 확인, 이월 안내가 포함된 전용 사본을 받고,
`NOTIFY_EMAIL`·`EMAIL_CC` 수신자는 확인된 부정 이슈 카드만 받는다. 주소별 개별
SMTP envelope와 기존 접수·중복 방지 상태 관리는 유지한다. 확인된 이슈가 없으면
기존대로 운영자에게만 상태 또는 확인필요 메일을 보내며 임원에게 정상 결과를
지어내 보내지 않는다. 실제 고객의 주문 오류 등 이슈 원문·요약은 제거하지 않는다.

회귀 검증: `python -m unittest -v test_negative_verification test_negative_subscription test_cookie_refresh test_cookie_provenance`
(실제 SMTP·유료 API 호출 없이 검증)
