# 인증 미복구 시 수동 보완

`negative_manual.py`는 사용자가 권한 있는 원문에서 직접 확인한 내용을 기존 검수 입력 형식으로 만든다.
오프라인 파일만 생성한다. 브라우저·로그인·큐·메일·유료 API·운영 이력을 호출하거나 변경하지 않는다.
쿠키 인증 상태는 항상 `unconfirmed`, `cookie_auth_verified=false`다.

## 입력과 실행

저장소 밖 비공개 경로 또는 Git 제외 디렉터리 `.negative_manual_data/`에 UTF-8 JSON을 둔다.
게시글 ID·기본 HTTPS 주소·제목·본문·게시 시각 원문·직접 확인 시각·본문 확인 여부가 필요하다.
본문은 접근 권한이 있는 계정에서만 확인한다. 등급 제한은 쿠키 갱신으로 해결됐다고 간주하지 않는다.
쿠키·비밀번호·검색 인증 매개변수·제삼자 연락처를 입력하지 않는다.

```json
{
  "schema_version": 1,
  "items": [{
    "post_id": "stocktraining:101",
    "url": "https://cafe.naver.com/f-e/cafes/29798500/articles/101",
    "title": "직접 확인한 실제 제목",
    "body": "접근 권한이 있는 원문에서 직접 확인한 실제 본문을 입력합니다. 빈 본문과 미확인 내용은 보완 완료로 처리할 수 없습니다.",
    "date_raw": "2026.10.21. 15:00",
    "observed_at": "2026-10-21T16:00:00+09:00",
    "body_verified": true
  }]
}
```

위 주소와 내용은 형식 예시다. 실제 글을 확인한 후 해당 항목으로 교체해야 한다.
게시 시각을 모르면 `date_raw`는 빈 문자열로 둔다. 시각을 추정해 채우지 않는다.
확인 전 템플릿에는 `body_verified=false`, `body=""`, `observed_at=""`를 둔다. 이 상태는 입력 검증에서 거절된다.

```bash
python negative_manual.py --input .negative_manual_data/input.json \
  --output .negative_manual_data/review.json --job-id negative-manual-20261021-01
```

기존 `seen_posts.json` 스냅샷과 비교해 처리된 ID는 제외하고 중복·ID/주소 불일치를 거절한다.
입력은 1~200건이며 현재 등록된 카페와 네이버 블로그의 기본 주소만 허용한다.
출력은 0600 권한으로 새 파일에만 저장하고 기존 파일을 덮어쓰지 않는다.
게시일만 있거나 게시 시각이 불명확하면 기존 검수기가 `needs_review`만 허용한다.

## 기존 검수와 연결

출력은 기존 `negative_verification.validate(job, result)`로 검증할 수 있는
`mode=verification`, `self_test=true`, `dataset=historical` 입력이다.
기존 정책·스키마·실제 인용·개인정보 마스킹 검사를 그대로 적용한다.
운영 모드로 바꾸면 기존의 과거 자료 혼입 차단 검사에 걸린다.
준비된 입력은 생성 후 120분 동안 유효하다. 만료 시 원문 확인 시각을 보존해 새 검수 입력을 만든다.

확인한 입력과 별도 구독 분석 결과를 비공개 저장소의 `jobs/`, `results/`에 넣고
기존 검수기를 통과시키면 본인 검수 단계에 연결할 수 있다. 자동으로 등록하거나 소비하지 않는다.
운영 `negative_subscription_request.json`, `index.json`, `seen_posts.json`은 수정하지 않는다.
기존 완료 회차를 재소비하거나 과거 메일을 재발송하지 않는다. 운영 반영은 별도 확인한 신규 회차에서만 한다.

미확보 글은 수동 보완 대상에 남겨 두고 부정·비부정 또는 처리 완료로 선기록하지 않는다.
기간 검색으로 찾지 못한 글은 개수 0으로 확정하지 않는다. 삭제·검색 누락·회원 등급 때문에 미확인일 수 있다.
