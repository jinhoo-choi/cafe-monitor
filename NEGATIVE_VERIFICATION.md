# 부정여론 개선안 검수

운영 main의 monitor.py·정기 발송·수신자·seen_posts.json은 변경하지 않는다. 검수 코드는 codex/negative-subscription-verification에서만 실행하며 production 회차를 받지 않는다. 기존 감성 판정·유사 묶음·다발 답변 API와 운영 발송·DB 쓰기 진입점을 검수 프로세스에서 차단한다.

## 실제 환경

기존 네이버 로그인 쿠키와 같은 14개 카페·블로그 수집 함수를 재사용한다. 최신 24시간의 미처리 입력과 지정한 실제 과거 게시글을 구분해 읽는다. 공개 블로그 프로필 URL은 게시글로 취급하지 않는다. 기존에 수집한 게시글 ID와 운영 로그의 과거 점수는 비교 근거로만 사용하며 유료 API로 재분석하지 않는다.

본문을 읽지 못하면 needs_review·score=null로 보류한다. 대상 회사·본문·단순 문의·광고·사칭·별도 계열사·시장 손실을 구분한다. 감성 점수와 현재 주문/출금 등 우선 확인 여부를 분리한다. 모든 입력 ID와 원문 연속 인용·수치를 검증한다.

긴 본문은 대상 회사가 언급된 문단을 먼저 발췌 예산에 넣은 뒤, 앞부분·끝부분·불편 표현을 추가한다. 원문 순서로 정렬한 뒤 전체 길이를 잘라 회사 문단을 잃지 않도록 한다. 이미 저장한 입력은 덮어쓰지 않는다. 전체 원문의 대상 언급 신호만 있고 제목·발췌에 대상 문단이 없으면 결과 검증도 확인 필요 외 판정을 거절한다.

요약은 작성자 주장과 확인되지 않은 사실을 구별한다. 내부 점검과 검토용 답변 초안을 따로 표시하며 허구의 개인 경험·해결 완료·원인을 적지 않는다. 글을 자동 게시하지 않는다. 그룹은 같은 증상·같은 실제 사건 근거가 있을 때만 구성하고 게시글 수를 피해자 수로 표시하지 않는다. 30개 초과 이슈는 비공개 deferred 자료로 보존한다.

## 비공개와 본인 발송

입력·분석·HTML·영수증은 jinhoo-choi/insight-bot-data main의 subscription/negative에만 저장한다. 쿠키는 브라우저에만 사용하고 큐에 저장하지 않는다. 입력 전 연락처·이메일·계좌·외부 주소를 마스킹한다. 공개 요청과 로그에는 제한된 메타데이터만 남긴다.

TEST_MODE=1, NOTIFY_EMAIL=GMAIL_USER, EMAIL_CC 비어 있음이 모두 확인돼야 발송한다. SMTP envelope에도 GMAIL_USER 한 주소만 넣는다. sending 선기록 후 한 번만 발송하고, SMTP accepted와 이력 불변을 확인한 경우만 verified·self_test=true로 기록한다. sending/uncertain/verified는 다시 발송하지 않는다.

워크플로는 운영 이력을 읽기 전용 파일로 받고 DB를 커밋하지 않는다. 운영 코드·DB diff가 없음을 확인한다. 검수용 키는 사용자 승인으로 기존 정상 큐 키를 NEGATIVE_QUEUE_TOKEN Secret에 암호화 등록하며, 정상 운영용 키나 기존 ADMIN_GITHUB_TOKEN을 변경하지 않는다.

GitHub GET 500 등 일시적 읽기 오류만 최대 3번 재시도한다. 401·403과 모든 PUT는 자동 재시도하지 않는다. SMTP 실패·사용량 부족을 유료 API로 우회하지 않는다.

## 순서

1. connection으로 비공개 읽기·쓰기 확인: API0/SMTP0.
2. collect로 실제 최근·과거 입력 수집: API0/SMTP0/운영 DB 쓰기0.
3. ChatGPT 구독에서 입력 전체 판정과 이슈별 문구 작성, 비공개 results에 저장.
4. consume·self_test=true로 본인 메일, private verified·accepted·self_test=true·API0 확인.
5. 문구·수신 검토 후 운영 전환은 별도 결정. 기존 API 운영과 다른 봇·예약은 이번 검수에서 변경하지 않는다.

## 2026-10-14 검수 증거

- 실제 수집: 최근 22건·과거 3건. [수집 Actions](https://github.com/jinhoo-choi/cafe-monitor/actions/runs/38502718385) 성공.
- 구독 판정: 부정 1건·비부정 22건·확인 필요 2건. 본문 미수집 1건과 대상 문단이 없는 발췌 1건은 정상으로 단정하지 않았다.
- 본인 SMTP: 12:04 KST, accepted 1건. [본인 검수 Actions](https://github.com/jinhoo-choi/cafe-monitor/actions/runs/38504292554) 성공.
- 비공개 회차 negative-verification-38502718385: verified·self_test=true, paid_ai_calls=0, production_recipients=0, production_seen_writes=0. SMTP 접수와 받은편지함 수신은 별도다.
- 모바일 390px·데스크톱 900px 메일 미리보기에서 가로 넘침 없음. 원문·문구·HTML·수신자 영수증은 비공개 큐에만 저장한다.
- 이 코드는 검수 전용이며 main 병합·운영 예약 전환·운영 메일 재발송을 수행하지 않았다.
