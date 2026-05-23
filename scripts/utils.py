"""
utils.py — 공통 함수.

모든 스크립트가 import해서 사용합니다.
비밀 정보(토큰/계정)는 어디에도 저장하지 않습니다.
"""
import logging
import os
import re
import shutil
import time
from datetime import datetime
from io import StringIO
from typing import Dict, List, Optional

import pandas as pd
import pytz
import requests
import yaml
from pykrx import stock


# ── 설정 ──────────────────────────────────────────────────────
def load_config(path: str = "config.yml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── 시간 ──────────────────────────────────────────────────────
def get_kst_today(tz: str = "Asia/Seoul") -> datetime:
    return datetime.now(pytz.timezone(tz))


# ── 종목코드 ──────────────────────────────────────────────────
def normalize_code(code) -> str:
    """문자열 변환 + 6자리 zero-pad."""
    return str(code).strip().zfill(6)


# ── 파일명 ────────────────────────────────────────────────────
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")


def safe_filename(name: str) -> str:
    """공백·특수문자만 언더스코어로 치환. 한글은 유지."""
    s = _INVALID_FILENAME_CHARS.sub("_", str(name))
    s = _WHITESPACE.sub("_", s)
    return s.strip("_") or "unknown"


def make_csv_path(code: str, name: str, data_dir: str) -> str:
    return os.path.join(data_dir, f"{normalize_code(code)}_{safe_filename(name)}.csv")


# ── HTTP 재시도 ──────────────────────────────────────────────
def _http_get_with_retry(url: str, retry_config: Optional[dict] = None) -> str:
    cfg = retry_config or {}
    attempts = int(cfg.get("max_attempts", 3))
    backoff = float(cfg.get("backoff_base_sec", 1.0))
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = backoff * (2 ** i)
            logging.warning(
                "HTTP GET 실패 (%d/%d): %s — %.1fs 후 재시도", i + 1, attempts, e, wait
            )
            if i + 1 < attempts:
                time.sleep(wait)
    raise RuntimeError(f"HTTP GET 최종 실패: {url} ({last_exc})")


# ── Google Sheets 종목 마스터 ────────────────────────────────
def fetch_stocks_from_google_sheets(
    url: str,
    code_col: str,
    name_col: str,
    retry_config: Optional[dict] = None,
) -> List[Dict[str, str]]:
    """publish-to-web CSV → [{'code': '005930', 'name': '삼성전자'}, ...]"""
    text = _http_get_with_retry(url, retry_config)
    df = pd.read_csv(StringIO(text))
    if code_col not in df.columns or name_col not in df.columns:
        raise ValueError(
            f"Sheets에서 컬럼을 찾을 수 없습니다. "
            f"기대: '{code_col}', '{name_col}'. 실제 헤더: {list(df.columns)}"
        )
    stocks: List[Dict[str, str]] = []
    seen = set()
    for _, row in df.iterrows():
        code_raw, name_raw = row[code_col], row[name_col]
        if pd.isna(code_raw) or pd.isna(name_raw):
            continue
        code = normalize_code(code_raw)
        name = str(name_raw).strip()
        if not code or not name:
            continue
        if code in seen:
            logging.warning("중복 종목코드: %s. 첫 번째만 사용.", code)
            continue
        seen.add(code)
        stocks.append({"code": code, "name": name})
    return stocks


# ── 로컬 스캔 ────────────────────────────────────────────────
_CODE_PREFIX = re.compile(r"^(\d{6})_")


def scan_data_dir(data_dir: str) -> Dict[str, str]:
    """data/ 안의 종목 CSV → {code: filepath}"""
    result: Dict[str, str] = {}
    if not os.path.isdir(data_dir):
        return result
    for f in os.listdir(data_dir):
        if not f.endswith(".csv"):
            continue
        m = _CODE_PREFIX.match(f)
        if not m:
            continue
        result[m.group(1)] = os.path.join(data_dir, f)
    return result


def find_csv_by_code(code: str, data_dir: str) -> Optional[str]:
    return scan_data_dir(data_dir).get(normalize_code(code))


# ── pykrx 컬럼 매핑 ──────────────────────────────────────────
# OHLCV: pykrx 1.0.45의 응답 컬럼명이 docstring과 실제가 다를 수 있어
# 다양한 후보를 매핑. 예상 컬럼: 시가/고가/저가/종가/거래량/거래대금/등락률
_OHLCV_COL_MAP = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
    "거래대금(원)": "trading_value",
    "등락률": "change_rate",
    "등락률(%)": "change_rate",
}

# Trader (detail=True): 한국 시장 12개 투자자 분류 + 외국인/기관 합계
# 가능한 컬럼명 후보 → 우리 영문 키 매핑
_TRADER_COL_MAP = {
    "개인": "individual_net",
    "외국인합계": "foreign_net",
    "외국인": "foreign_net",  # detail=True에서 합계 대신 "외국인" 단독일 수 있음
    "기관합계": "institution_net",
    "기관": "institution_net",
    "연기금": "pension_net",
    "연기금 등": "pension_net",
    "사모": "privateequity_net",
    "사모펀드": "privateequity_net",
    "투신": "trust_net",
    "투자신탁": "trust_net",
    "보험": "insurance_net",
    "은행": "bank_net",
    "금융투자": "financial_net",
    "기타법인": "other_corp_net",
    "기타외국인": "other_foreign_net",
    "기타금융": "other_finance_net",
}

# 디버깅: 첫 호출에만 컬럼명 로그
_DEBUG_COLS_LOGGED = False
_DEBUG_COLS_LOGGED_TRADER = False

_OHLCV_COLS = ["open", "high", "low", "close", "volume", "trading_value", "change_rate"]
_TRADER_COLS = [
    "individual_net", "foreign_net", "institution_net", "pension_net",
    "trust_net", "privateequity_net", "insurance_net", "bank_net",
    "financial_net", "other_corp_net", "other_foreign_net", "other_finance_net",
]
_FINAL_COLS = ["date"] + _OHLCV_COLS + _TRADER_COLS


def _find_col(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def _merge_and_normalize(df_ohlcv: pd.DataFrame, df_trader: pd.DataFrame) -> pd.DataFrame:
    """pykrx 결과(한글 컬럼, 날짜 index)를 우리 스키마(영문)로 변환."""
    if df_ohlcv is None or df_ohlcv.empty:
        return pd.DataFrame(columns=_FINAL_COLS)

    # OHLCV 정규화
    df_o = df_ohlcv.rename(columns=_OHLCV_COL_MAP).copy()
    df_o.index.name = "date"
    df_o = df_o.reset_index()
    df_o["date"] = pd.to_datetime(df_o["date"]).dt.strftime("%Y-%m-%d")

    # Trader 정규화 (한글 → 영문 _net)
    if df_trader is not None and not df_trader.empty:
        df_t = df_trader.copy()
        df_t.index.name = "date"
        df_t = df_t.reset_index()
        df_t["date"] = pd.to_datetime(df_t["date"]).dt.strftime("%Y-%m-%d")
        # 매핑 적용 (한글 컬럼이 매핑 dict에 있으면 영문으로 rename)
        df_t = df_t.rename(columns=_TRADER_COL_MAP)
        # 우리가 쓸 trader 컬럼들만 (없는 건 무시)
        keep_cols = ["date"] + [c for c in _TRADER_COLS if c in df_t.columns]
        df_t = df_t[keep_cols]
        # 합계 보정: 기관합계가 없으면 세부 기관들 합산
        if "institution_net" not in df_t.columns:
            sub_inst = [c for c in [
                "financial_net", "insurance_net", "trust_net", "privateequity_net",
                "bank_net", "other_finance_net", "pension_net",
            ] if c in df_t.columns]
            if sub_inst:
                df_t["institution_net"] = df_t[sub_inst].sum(axis=1)
        # 외국인합계가 없으면 외국인 + 기타외국인 합산 (이미 매핑됐을 수 있음)
        # (별도 처리 불필요 — 매핑이 알아서 처리)
        df_o = df_o.merge(df_t, on="date", how="left")

    # 누락 컬럼 0으로 보강
    for c in _FINAL_COLS:
        if c not in df_o.columns:
            df_o[c] = 0
    df_o = df_o[_FINAL_COLS]

    # 타입 정리
    int_cols = ["open", "high", "low", "close", "volume", "trading_value"] + _TRADER_COLS
    for c in int_cols:
        df_o[c] = pd.to_numeric(df_o[c], errors="coerce").fillna(0).astype("int64")
    df_o["change_rate"] = (
        pd.to_numeric(df_o["change_rate"], errors="coerce").fillna(0.0).round(4)
    )

    df_o = df_o.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return df_o.reset_index(drop=True)


def _fetch_trader_with_fallback(code: str, fromdate: str, todate: str):
    """수급 데이터 fetch. detail=True를 우선 시도 (세부 12개 항목 확보)."""
    for attempt in [
        {"detail": True},   # 세부 12개 (개인/연기금/사모/투신/보험/은행/금융투자/...)
        {"detail": False},  # 합계만 (외인합계/기관합계/개인/기타법인)
    ]:
        try:
            df = stock.get_market_trading_volume_by_date(
                fromdate=fromdate, todate=todate, ticker=code, **attempt
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logging.debug("[%s] trader fetch attempt %s 실패: %s", code, attempt, e)
            continue
    return None


def fetch_pykrx_data_with_retry(
    code: str,
    fromdate: str,
    todate: str,
    retry_config: Optional[dict] = None,
) -> pd.DataFrame:
    """OHLCV + 외인/기관/세부 수급을 한 DataFrame으로 반환.
    빈 결과(휴장 등)는 빈 DataFrame.
    """
    global _DEBUG_COLS_LOGGED, _DEBUG_COLS_LOGGED_TRADER
    cfg = retry_config or {}
    attempts = int(cfg.get("max_attempts", 3))
    backoff = float(cfg.get("backoff_base_sec", 1.0))
    last_exc: Optional[Exception] = None

    for i in range(attempts):
        try:
            df_ohlcv = stock.get_market_ohlcv_by_date(
                fromdate=fromdate, todate=todate, ticker=code
            )
            # 첫 비어있지 않은 응답에서 컬럼명 디버깅 로그
            if not _DEBUG_COLS_LOGGED and df_ohlcv is not None and not df_ohlcv.empty:
                logging.info(
                    "[DEBUG_COLS] OHLCV columns for %s: %s",
                    code, list(df_ohlcv.columns),
                )
                _DEBUG_COLS_LOGGED = True

            df_trader = _fetch_trader_with_fallback(code, fromdate, todate)
            if df_trader is None:
                logging.warning("[%s] trader fetch 실패. 수급 0으로 채움.", code)
            elif not _DEBUG_COLS_LOGGED_TRADER:
                logging.info(
                    "[DEBUG_COLS] Trader columns for %s: %s",
                    code, list(df_trader.columns),
                )
                _DEBUG_COLS_LOGGED_TRADER = True

            return _merge_and_normalize(df_ohlcv, df_trader)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = backoff * (2 ** i)
            logging.warning(
                "[%s] pykrx 실패 (%d/%d): %s — %.1fs 후 재시도",
                code, i + 1, attempts, e, wait,
            )
            if i + 1 < attempts:
                time.sleep(wait)
    raise RuntimeError(f"[{code}] pykrx 최종 실패: {last_exc}")


# ── archive ─────────────────────────────────────────────────
def archive_stock(code: str, data_dir: str, archive_dir: str) -> Optional[str]:
    """data → archive 이동. 이동된 dst 경로 반환. 파일 없으면 None."""
    src = find_csv_by_code(code, data_dir)
    if not src:
        return None
    os.makedirs(archive_dir, exist_ok=True)
    fname = os.path.basename(src)
    base, ext = os.path.splitext(fname)
    today = datetime.now().strftime("%Y%m%d")
    dst = os.path.join(archive_dir, f"{base}_archived_{today}{ext}")
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(archive_dir, f"{base}_archived_{today}_{i}{ext}")
        i += 1
    shutil.move(src, dst)
    return dst


# ── 로깅 ─────────────────────────────────────────────────────
def setup_logging(log_dir: str) -> str:
    """logs/update_YYYYMMDD.log + stdout. 기존 핸들러 정리."""
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    log_path = os.path.join(log_dir, f"update_{today}.log")
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    return log_path
