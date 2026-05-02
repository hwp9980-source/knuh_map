# 의료·보건정책 데일리 카드뉴스 — 구현 계획 v2.0

작성일 2026.04.28 · 개정일 2026.04.29 · 대상 운영자 1인 (의료·정책 도메인)
채택 스택 **GitHub Actions + Gemini Paid API** · 월 운영비 **~$1 미만**

---

## 변경 이력

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| v1.0 | 2026.04.28 | 최초 작성 (Tier 2: Make.com + Claude Sonnet) |
| v2.0 | 2026.04.29 | **GitHub Actions + Gemini Free로 전환, 비용 $0 달성** |
| v2.1 | 2026.05.02 | **Gemini 유료 API 결제 전환 (요청 한도 해제)** |

---

## 1. 목표

* 매일 **07:00 KST** 의료·보건정책 핵심 6\~8건을 카드뉴스 형태로 자동 생산·배포
* 커버리지 도메인

  * 강원대병원 (운영·인사·사고·이슈)
  * 국립대병원 일반 (이관·예산·R\&D)
  * 보건정책·의료개혁 (복지부·식약처·국회 복지위)
  * 강원도 지방의료 (3개 대학병원·의료원·재택의료)
  * 기타 의료계 일반이슈
* 주관식 평가지표

  * 검토 시간 ≤ 5분
  * 카드뉴스→원문 클릭 1탭 이내
  * 미커버 핵심뉴스 ≤ 1건/주

---

## 2. 산출물

* 카드뉴스 HTML (모바일 380px · PC 720px 양 대응)
* 백업 HTML 아카이브 (`output/card_news_YYYYMMDD.html`)
* Notion DB 레코드 (검색·아카이브)
* 매일 00:00 기준 24시간 뉴스 윈도우

---

## 3. 아키텍처 (v2.0 확정)

### 3.1 스택 비교 — v2.0 채택 사유

| 항목 | v1.0 (Make.com) | **v2.0 (GitHub Actions) ★** |
|------|-----------------|------------------------------|
| 월 비용 | $12~15 | **$0** |
| 셋업 방식 | GUI 드래그&드롭 | Python 코드 |
| 복잡 로직 | 제한적 | 무제한 |
| 벤더 락인 | 높음 (Make.com) | 없음 (코드는 내 것) |
| LLM | Claude Sonnet ($3/MTok) | **Gemini Flash (종량제, 요청한도 없음)** |
| 장기 유지보수 | GUI 재학습 필요 | git history로 관리 |
| 기존 코드 재사용 | 불가 | **가능** (notebooklm_automation 패턴) |

→ Python 역량 보유 + 장기 운영 목표 → **GitHub Actions 선택**

### 3.2 파이프라인 (6단계)

```
[RSS·네이버검색·구글뉴스 피드 수집]
        ↓  feedparser (24시간 필터)
[키워드 점수 계산 + URL SHA-256 중복 제거]
        ↓  seen_urls.json (git 영속화)
[카테고리 regex 분류]
        ↓
[Gemini 2.0 Flash API → 카드뉴스 JSON]
        ↓  3회 재시도 + 폴백 구조
[Jinja2 HTML 렌더링]
        ↓
[Gmail SMTP 발송 + Notion DB 저장]
        ↓
[seen_urls.json 커밋 → git push]
```

### 3.3 GitHub Actions 스케줄

```yaml
on:
  schedule:
    - cron: '30 21 * * 0-4'   # KST 06:30 평일 (UTC 21:30)
  workflow_dispatch:            # 수동 실행 버튼
```

* 월 실행 시간: ~30분 → 무료 한도 2,000분의 **1.5%** 사용

---

## 4. 데이터 소스

### 4.1 RSS 피드 (검증됨)

```
https://www.kado.net/rss/S1N6.xml           # 강원도민일보 지역
https://www.kado.net/rss/S1N7.xml           # 강원도민일보 강원인
https://www.mstoday.co.kr/rss/allArticle.xml # MS투데이 (춘천)
https://www.korea.kr/rss/policy.xml          # 정책브리핑
https://www.mohw.go.kr/rss/rss.jsp?seq=1    # 보건복지부 보도자료
```

### 4.2 RSS 피드 (첫날 검증 필수)

```
https://www.medifonews.com/rss/allArticle.xml
https://www.medigatenews.com/rss/allArticle.xml
https://www.dailymedi.com/rss/allArticle.xml
https://www.docdocdoc.co.kr/rss/allArticle.xml
https://www.rapportian.com/rss/allArticle.xml
https://www.themedical.kr/rss/allArticle.xml
https://www.khanews.com/rss/allArticle.xml
https://www.medicaltimes.com/rss/allArticle.xml
https://www.doctorsnews.co.kr/rss/allArticle.xml
```

### 4.3 검색 RSS (RSS 미공개 매체 자동 흡수)

네이버 검색 RSS — 키워드 8종

```
강원대병원 / 강원대학교병원 / 국립대병원 / 강원특별자치도 의료
의료개혁 / 보건복지부 / 지역필수의료 / 권역책임의료기관
```

구글 뉴스 RSS — 핵심 키워드 4종 (강원대병원·강원대학교병원·국립대병원·의료개혁)

---

## 5. 수집 단계 (`src/collector.py`)

* `feedparser` 라이브러리로 전체 피드 파싱
* 24시간 이내 기사만 추출 (`published_parsed` → UTC 비교)
* 개별 피드 실패 → warn 로그 후 계속
* 피드 3개 이상 동시 실패 → error 로그

---

## 6. 정제 단계 (`src/filter.py`)

### 6.1 키워드 화이트리스트

핵심 (점수 +2)

```
강원대병원, 강원대학교병원, KNUH
국립대병원, 국립대학교병원
의료개혁, 의료혁신, 필수의료
보건복지부, 복지부
```

연관 (점수 +1)

```
강원특별자치도 의료, 강원 의료, 도립의료원
원주세브란스, 강릉아산, 한림대 춘천성심
의대 정원, 전공의, 의대생
응급실, 응급의료센터
첨단재생의료, 통합돌봄, 남우동, 건정심, 식약처
```

### 6.2 블랙리스트 (제외)

```
부고, 별세, 광고, 협찬, 주가, 시세
```

### 6.3 중복 제거

* URL SHA-256 해시 → `data/seen_urls.json` 조회
* 30일 TTL 자동 만료 정리
* JSON은 매 실행 후 git commit으로 영속화

### 6.4 카테고리 분류

| 카테고리 | regex 패턴 |
|----------|-----------|
| 강원대병원 | `강원대(학교)?병원\|KNUH` |
| 국립대병원 | `국립대(학교)?병원\|상급종합병원` |
| 보건정책 | `보건복지부\|식약처\|복지부\|건정심` |
| 강원지역의료 | `강원\|춘천\|원주\|강릉\|동해\|속초\|삼척\|영월` |
| 의료계 | `전공의\|의대생\|의협\|개원의\|의료기사법` |
| 사이버보안 | `랜섬웨어\|해킹\|개인정보 유출` |

---

## 7. AI 요약 단계 (`src/summarizer.py`)

### 7.1 모델 선택 (v2.0)

| 항목 | v1.0 | **v2.0** |
|------|------|----------|
| 분류·중복판정 | claude-haiku-4-5 | **Gemini 2.0 Flash (Paid)** |
| 카드뉴스 생성 | claude-sonnet-4-6 | **Gemini 2.0 Flash (Paid)** |
| 비용 | ~$2/월 | **~$1 미만/월** |

**Gemini Paid Tier:** 15 RPM 제한이 해제되어 더욱 안정적이고 빠른 생성이 가능합니다.

### 7.2 시스템 프롬프트

```
당신은 의료·보건정책 데일리 카드뉴스 큐레이터입니다.

[독자 프로파일]
- 의료·임상 도메인 종사자 (강원대학교병원 소속)
- 의료정책·강원지역 의료 동향에 관심
- 검토 시간 5분 이내, 모바일 환경

[출력 규칙]
- 개조식, 경어체 사용 금지
- 헤드라인 ≤ 20자
- 불릿 1줄 ≤ 35자, 카드당 2~3개
- 카테고리당 최대 1건 (동일 사건 통합)
- TODAY 카드는 직역갈등·입법·환자안전 우선

[가중치]
- 강원대병원: 절대 우선 (출현 시 무조건 카드 1장 + TODAY 후보)
- 국립대병원 이관·예산·인사: 가중치 ×2
- 의료개혁·의료기사법 직역갈등: TODAY 우선
- 단순 인사·동정: 제외 (강원대병원 한정 포함 허용)

[출력 형식]
순수 JSON만 반환. 마크다운 코드 펜스(```) 절대 금지.
```

### 7.3 출력 JSON 스키마

```json
{
  "issue_no": 1,
  "date": "2026-04-29",
  "today_card": {
    "category_label": "입법 · 직역갈등",
    "headline": "string ≤20자",
    "bullets": ["string ≤35자"],
    "source_name": "메디게이트뉴스",
    "source_url": "https://...",
    "urgency": "high|medium|low"
  },
  "cards": [
    {
      "category_tag": "강원대병원|국립대병원|보건정책|강원지역의료|의료계|사이버보안",
      "category_color": "blue|purple|amber|teal|coral|red",
      "date": "MM.DD",
      "headline": "string",
      "bullets": ["string"],
      "source_name": "string",
      "source_url": "string"
    }
  ],
  "tomorrow_watch": ["string"],
  "hashtags": ["#키워드1"]
}
```

### 7.4 에러 핸들링

* JSON 파싱 실패 → 마크다운 펜스 제거 후 재시도
* API 실패 → 지수 백오프 3회 재시도
* 3회 모두 실패 → 폴백: 원본 기사 제목 그대로 카드 생성 후 발송

---

## 8. 렌더링 단계 (`src/renderer.py` · `templates/card_news.html`)

### 8.1 HTML 템플릿 구조

* 외곽 컨테이너 380\~720px (반응형)
* 다크 그라디언트 헤더 (Issue 번호 + 날짜)
* TODAY 카드 1장 (적색 강조)
* 일반 카드 2열 그리드 (카테고리 컬러)
* Tomorrow Watch 박스 (다크 배경)
* 해시태그 영역
* 카드 전체 `<a>` 래핑 → 원문 이동

### 8.2 카테고리 색상 시스템

| 카테고리 | 배경 | 텍스트 | 테두리 |
|----------|------|--------|--------|
| TODAY | #FCEBEB | #A32D2D | #E8B4B4 |
| 강원대병원 | #E6F1FB | #0C447C | #A8C9EC |
| 국립대병원 | #EEEDFE | #3C3489 | #B8B5F5 |
| 보건정책 | #FAEEDA | #633806 | #E8C97A |
| 강원지역의료 | #E1F5EE | #085041 | #7FD4B8 |
| 의료계 | #FAECE7 | #712B13 | #E8B09A |
| 사이버보안 | #FCEBEB | #791F1F | #E8A0A0 |

---

## 9. 배포 단계 (`src/notifier.py`)

### 9.1 채널 우선순위

1. **이메일 발송** (Gmail SMTP, App Password) — 본문 인라인 HTML
2. **Notion DB 자동 추가** — 영구 아카이브, 검색 가능
3. **HTML 아카이브** — `output/card_news_YYYYMMDD.html` (GitHub Actions Artifacts 30일 보관)

### 9.2 이메일 셋업

* subject: `[의료데일리] {date} · {today_headline}`
* 발송 시각: 06:38 KST (파이프라인 시작 8분 후)
* Gmail App Password 방식 (OAuth 불필요)

### 9.3 Notion DB 스키마

| 컬럼 | 타입 | 비고 |
|------|------|------|
| 헤드라인 | Title | |
| 일자 | Date | |
| Issue No | Number | |
| 카테고리 | Select | 7종 |
| 불릿 | Text | |
| 원문URL | URL | |
| 매체 | Select | |
| 검토상태 | Status | 미확인/확인/심층조사 |

---

## 10. 운영 스케줄

| 시각 (KST) | 동작 |
|-----------|------|
| 06:30 | GitHub Actions 트리거 (cron) |
| 06:32 | RSS 수집 + 필터링 + 카테고리 분류 |
| 06:34 | Gemini API 카드뉴스 JSON 생성 |
| 06:36 | Jinja2 HTML 렌더링 |
| 06:38 | Gmail 발송 + Notion DB 저장 |
| 06:40 | seen_urls.json git commit & push |
| 07:00 | 운영자 이메일 수신·5분 검토 |

### 10.1 에러 핸들링

* RSS 피드 1개 실패 → warn 로그, 계속 진행
* RSS 3개 이상 실패 → error 로그 (Actions 실패 알림)
* Gemini API 실패 → 3회 재시도 후 폴백 HTML 발송
* Notion API 실패 → 이메일은 발송, Notion 다음 실행 시 재시도 불필요 (이메일이 주 채널)

### 10.2 모니터링

* GitHub Actions 실행 로그 (90일 보관)
* Actions 실패 시 자동 이메일 알림 (GitHub 기본 기능)
* 주간 KPI — 발송 성공률 · 평균 카드 수 · 미커버 사건 (수동 점검)

---

## 11. 비용 (v2.0)

| 항목 | v1.0 | **v2.0** |
|------|------|----------|
| 오케스트레이션 | Make.com Core $9 | **GitHub Actions $0** |
| LLM API | Claude API ~$2 | **Gemini 유료 API ~$1 미만** |
| 이메일 발송 | Resend $0 | Gmail SMTP $0 |
| 아카이브 | Notion $0 | Notion $0 + GitHub Artifacts |
| 도메인 | $1 (선택) | 불필요 |
| **합계** | **$12~15/월** | **~$1 미만/월** |

연간 절감액: 약 $150 (18만원)

---

## 12. 프로젝트 파일 구조

```
projects/card_news/
├── .github/workflows/
│   └── card_news.yml       # 스케줄 + 시크릿 주입
├── src/
│   ├── config.py           # RSS·키워드·색상 설정
│   ├── collector.py        # feedparser RSS 수집
│   ├── filter.py           # 키워드 점수 + 중복 제거
│   ├── summarizer.py       # Gemini API 카드뉴스 생성
│   ├── renderer.py         # Jinja2 HTML 렌더링
│   └── notifier.py         # Gmail SMTP + Notion API
├── templates/
│   └── card_news.html      # 반응형 다크테마 카드뉴스
├── data/
│   ├── seen_urls.json      # URL 중복 체크 (git 관리)
│   └── issue_no.json       # 발행 번호 (git 관리)
├── output/                 # 생성된 HTML 아카이브
├── main.py                 # 6단계 파이프라인 진입점
├── requirements.txt        # feedparser, google-generativeai, jinja2, notion-client
└── README.md               # 셋업 가이드
```

---

## 13. 셋업 체크리스트

### D-2 (사전 준비)

* \[ ] GitHub 레포 생성 (Private) + 코드 푸시
* \[ ] [Gemini API 키 발급](https://aistudio.google.com/apikey) (무료)
* \[ ] Gmail 앱 비밀번호 생성 (2FA 필요)
* \[ ] Notion 워크스페이스에 DB 생성 (10. 스키마 적용) + 통합 토큰 발급
* \[ ] RSS 피드 9개 (4.2) 브라우저 검증

### D-1 (GitHub Secrets 등록)

레포 → Settings → Secrets → Actions → New repository secret

| Secret 이름 | 내용 |
|-------------|------|
| `GEMINI_API_KEY` | Gemini API 키 |
| `GMAIL_FROM` | 발신 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 |
| `GMAIL_TO` | 수신 주소 (쉼표 구분) |
| `NOTION_TOKEN` | Notion 통합 토큰 (선택) |
| `NOTION_DB_ID` | Notion DB ID (선택) |

### D0 (테스트 실행)

* \[ ] 로컬 테스트: `pip install -r requirements.txt && python main.py`
* \[ ] `output/card_news_YYYYMMDD.html` 모바일·PC 시각 확인
* \[ ] Notion 1건 정상 기록 확인
* \[ ] 이메일 발송 도착 확인 (스팸함 포함)
* \[ ] GitHub Actions → **Run workflow** 수동 실행 성공 확인

### D+1 (운영 시작)

* \[ ] 매일 06:30 cron 트리거 활성화 확인
* \[ ] 첫 1주 일일 검토 (카드 품질·미커버 사건 추적)

---

## 14. 향후 확장 (Tier 3 마이그레이션)

### 3개월차

* 자체 크롤러 추가 (Playwright)
  * 강원대병원 공지 (`knuh.or.kr`)
  * 강원일보 (네이버 검색 의존도 낮춤)

### 6개월차

* Supabase Postgres — 30일 백워드 검색
* 분석 대시보드 (Metabase)

### 12개월차

* Make.com 의존도 없이 이미 독립적 → 추가 마이그레이션 불필요
* React 카드뉴스 컴포넌트 (현재 HTML 템플릿 이식)
* 음성 브리핑 TTS — 출퇴근용 5분 팟캐스트

---

## 15. 리스크 및 대응

| 리스크 | 영향 | 완화책 |
|--------|------|--------|
| RSS 피드 URL 변경 | 누락 | 월 1회 검증 스크립트 실행 |
| API 비용 과다 청구 | 예산 초과 | Google Cloud Billing 요금 알림 설정 |
| Gmail SMTP 차단 | 발송 중단 | Resend 무료 플랜으로 전환 |
| GitHub Actions 지연 | ±10분 지연 | 허용 범위 내 (KPI 무영향) |
| 매체 차단·robots.txt | 크롤링 실패 | 네이버 검색 RSS 의존 강화 |
| 저작권 이슈 | 법적 위험 | 본문 미수록·요약+링크만, 매체명 명시 |

---

## 16. 다음 액션 (즉시)

* \[ ] GitHub 레포 생성 + `git push`
* \[ ] Secrets 5개 등록 (Gemini, Gmail 필수)
* \[ ] Actions → **Run workflow** 수동 실행 1회 검증
* \[ ] RSS 피드 9개 작동 URL 확정

---

부록

* A. RSS 피드 검증 스크립트 (별도 산출물 요청 시 제공)
* B. Gemini API 실측 프롬프트 케이스북 (운영 1주 후 보완)
* C. `card_news.html` 풀 템플릿 → `projects/card_news/templates/card_news.html` 구현 완료
* D. GitHub Actions 워크플로 → `projects/card_news/.github/workflows/card_news.yml` 구현 완료
