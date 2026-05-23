# stock_db — 한국 주식 일봉 시세 DB

한국거래소 일봉 시세를 매 평일 자동 수집해 종목별 CSV로 저장하는 자동화 시스템.

## 한 줄 요약

Google Sheets에 종목을 적어두면, 매 평일 KST 21:00에 GitHub Actions가 자동으로 시세를 받아 `data/` 폴더에 저장하고 commit합니다.

## 데이터 흐름

```
Google Sheets (종목 마스터)
        ↓ publish-to-web CSV
GitHub Actions (매 평일 KST 21:00)
        ↓
pykrx (한국거래소 데이터)
        ↓
data/{종목코드}_{종목명}.csv
        ↓
git commit & push (자동)
```

## 데이터 구조

종목당 CSV 1개. 컬럼 20개:

**가격·거래량 (8개)**
| 컬럼 | 설명 |
|---|---|
| `date` | 거래일 (YYYY-MM-DD) |
| `open` / `high` / `low` / `close` | 시가 / 고가 / 저가 / 종가 (정수) |
| `volume` | 거래량 (주) |
| `trading_value` | 거래대금 (원) |
| `change_rate` | 등락률 (%) |

**투자자별 순매수 (12개, 단위: 주)**
| 컬럼 | 의미 |
|---|---|
| `individual_net` | 개인 |
| `foreign_net` | 외국인합계 |
| `institution_net` | 기관합계 |
| `pension_net` | 연기금 |
| `trust_net` | 투신 |
| `privateequity_net` | 사모 |
| `insurance_net` | 보험 |
| `bank_net` | 은행 |
| `financial_net` | 금융투자 |
| `other_corp_net` | 기타법인 |
| `other_foreign_net` | 기타외국인 |
| `other_finance_net` | 기타금융 |

음수면 순매도. 날짜는 오름차순(과거 → 최신) 정렬, UTF-8 인코딩.

## 사용 방법

### 새 종목 추가
1. Google Sheets에서 관심종목 시트에 행 1개 추가 (`종목명`, `종목코드` 필수)
2. 다음 평일 21:00에 자동으로 10년치 백필 + 일별 업데이트 시작

### 종목 제외
1. Google Sheets에서 해당 행 삭제
2. 다음 실행 시 해당 CSV가 `archive/` 폴더로 자동 이동 (보존, 삭제 X)
3. 복구하려면 `archive/`에서 `data/`로 옮기면 됩니다

### 실행 결과 확인
- GitHub repo 상단 **Actions** 탭에서 최근 실행 로그 확인
- `logs/update_YYYYMMDD.log` 파일도 자동 commit됨
- 매번 commit history에 변경 종목이 보임

### 에러 알림
- Actions 실패 시 GitHub이 등록된 이메일로 자동 알림 (별도 셋업 불필요)

### 수동 실행 (즉시 동작 확인용)
GitHub repo → **Actions** 탭 → 좌측 `Daily Stock Data Update` → 우상단 **Run workflow** 클릭
- 기본값(`force_backfill: false`): 일일 업데이트만
- `force_backfill: true` 선택: 모든 종목 강제 재백필 (10년치 새로 받음. 기존 CSV는 archive로 이동)

### 강제 재백필이 필요한 경우
- 스키마가 변경됐을 때
- 특정 종목 데이터를 처음부터 다시 받고 싶을 때 → `python scripts/initial_backfill.py 005930` 로컬 실행도 가능

## 자동 실행 스케줄

- 매주 월~금 KST 21:00 (UTC 12:00)
- NXT 야간장(20:00 마감) 종료 후 안전 마진 1시간 두고 실행
- 휴장일은 자동 감지 → 변경 없음 → commit 자동 skip

## 폴더 구조

```
stock_db/
├── .github/workflows/daily_update.yml   # 자동 실행 워크플로우
├── scripts/
│   ├── utils.py                          # 공통 함수
│   ├── daily_update.py                   # 메인 (매일 실행)
│   ├── initial_backfill.py               # 강제 재백필 (수동)
│   └── check_consistency.py              # 무결성 검증 (수동)
├── data/                                  # 종목별 시세 CSV
├── archive/                               # 관심종목 제외된 종목 보존
├── logs/                                  # 실행 로그
├── config.yml                             # 설정 (Sheets URL 등)
└── requirements.txt                       # Python 의존성
```

## 분석 활용 (Claude 등)

Claude는 GitHub raw URL로 CSV를 직접 fetch할 수 있습니다:

```
https://raw.githubusercontent.com/chanrran/stock_db/main/data/005930_삼성전자.csv
```

Private repo는 토큰 헤더 필요, 또는 Claude에게 직접 CSV 첨부.
분석 시 필요한 기간만 잘라 사용하면 토큰 절약 (예: 최근 60일 → 60줄만).

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| Actions 실패 (특정 종목) | 상장폐지·종목코드 오타. 시트 확인 후 재실행 |
| Actions 실패 (전체) | Sheets URL 만료/게시 해제 또는 KRX 인증 실패. `config.yml`의 `sheets_csv_url` 재발급 또는 GitHub Secrets의 KRX_ID/KRX_PW 재확인 |
| 로그에 "KRX 로그인 실패" | GitHub Secrets에 `KRX_ID`/`KRX_PW` 미등록 또는 오타 |
| 로그에 "비밀번호 변경이 필요" (CD010) | KRX 사이트에서 비밀번호 변경 → GitHub Secrets `KRX_PW` 재등록 |
| 한글 종목명 깨짐 | encoding 미지정. utils가 utf-8 강제 사용 중 |
| 종목코드 앞 0 사라짐 | `utils.normalize_code()`가 6자리 zero-pad |
| Actions push 권한 오류 | workflow의 `permissions: contents: write` 확인 |
| `KRX_ID 환경 변수 필요` 에러 | pykrx 신버전 설치됨. `requirements.txt`의 `pykrx==1.0.45` 고정 확인 |

## 인증 (KRX)

pykrx v1.2.8부터 KRX 사이트가 일부 endpoint에 로그인을 요구합니다. 따라서 본 시스템은 KRX 계정 인증을 사용합니다.

**셋업 (이미 완료된 경우 skip)**:
1. https://data.krx.co.kr 회원가입
2. GitHub repo Settings → Secrets and variables → Actions → 두 비밀 등록:
   - `KRX_ID`: KRX 로그인 ID
   - `KRX_PW`: KRX 비밀번호

**작동 방식**: GitHub Actions가 실행될 때 Secrets를 환경변수로 주입 → pykrx가 자동 로그인. 1시간 만료 시 자동 재로그인. 비밀번호 만료(CD010) 시 KRX 사이트에서 변경 후 Secrets 재등록 필요.

**비밀번호 절대 금지**: 코드, config.yml, README, 채팅 어디에도 평문으로 저장하지 마세요.

## 기술 메모

- 데이터 소스: `pykrx` 1.2.8 (KRX 통합회원 인증 필요)
- 저장 포맷: 종목별 CSV (`{종목코드}_{종목명}.csv`). 종목코드가 안정 키 — 종목명이 바뀌어도 코드 기준으로 매칭, 파일 자동 rename
- 휴장일: pykrx가 빈 결과 반환 → 자동 skip. 공휴일 캘린더 관리 불필요
- 재시도: 네트워크 오류 시 3회 지수 백오프 (1s → 2s → 4s)
- 종목간 요청 간격: 0.5s (pykrx rate limit 보호)
- 종목 단위 실패는 격리 — 한 종목 실패가 다른 종목 처리를 막지 않음

## 보안

이 repo와 GitHub Actions는 **읽기 전용 무료 공개 데이터**(한국거래소)만 사용합니다. API 키·계정·토큰 등이 코드/config에 들어가지 않습니다.
