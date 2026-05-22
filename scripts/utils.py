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


# ── pykrx 정규화 ─────────────────────────────────────────────
_OHLCV_COL_MAP = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
    "등락률": "change_rate",
}

_FINAL_COLS = [
    "date", "open", "high", "low", "close",
    "volume", "trading_value", "change_rate",
    "foreign_net", "institution_net",
]


def _find_col(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def _merge_and_normalize(df_ohlcv: pd.DataFrame, df_trader: pd.DataFrame) -> pd.DataFrame:
    """pykrx 결과(한글 컬럼, 날짜 index)를 우리 스키마로 변환."""
    if df_ohlcv is None or df_ohlcv.empty:
        return pd.DataFrame(columns=_FINAL_COLS)

    df_o = df_ohlcv.rename(columns=_OHLCV_COL_MAP).copy()
    df_o.index.name = "date"
    df_o = df_o.reset_index()
    df_o["date"] = pd.to_datetime(df_o["date"]).dt.strftime("%Y-%m-%d")

    if df_trader is not None and not df_trader.empty:
        df_t = df_trader.copy()
        df_t.index.name = "date"
        df_t = df_t.reset_index()
        df_t["date"] = pd.to_datetime(df_t["date"]).dt.strftime("%Y-%m-%d")
        foreign_col = _find_col(df_t.columns, ["외국인합계", "외국인"])
        inst_col = _find_col(df_t.columns, ["기관합계", "기관"])
        out_t = pd.DataFrame({"date": df_t["date"]})
        out_t["foreign_net"] = df_t[foreign_col] if foreign_col else 0
        out_t["institution_net"] = df_t[inst_col] if inst_col else 0
        df_o = df_o.merge(out_t, on="date", how="left")
    else:
        df_o["foreign_net"] = 0
        df_o["institution_net"] = 0

    # 누락 컬럼 보강
    for c in _FINAL_COLS:
        if c not in df_o.columns:
            df_o[c] = 0
    df_o = df_o[_FINAL_COLS]

    # 타입
    int_cols = ["open", "high", "low", "close", "volume", "trading_value",
                "foreign_net", "institution_net"]
    for c in int_cols:
        df_o[c] = pd.to_numeric(df_o[c], errors="coerce").fillna(0).astype("int64")
    df_o["change_rate"] = (
        pd.to_numeric(df_o["change_rate"], errors="coerce").fillna(0.0).round(4)
    )

    df_o = df_o.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return df_o.reset_index(drop=True)


def fetch_pykrx_data_with_retry(
    code: str,
    fromdate: str,
    todate: str,
    retry_config: Optional[dict] = None,
) -> pd.DataFrame:
    """OHLCV + 외인/기관 수급을 한 DataFrame으로 반환.
    빈 결과(휴장 등)는 빈 DataFrame.
    """
    cfg = retry_config or {}
    attempts = int(cfg.get("max_attempts", 3))
    backoff = float(cfg.get("backoff_base_sec", 1.0))
    last_exc: Optional[Exception] = None

    for i in range(attempts):
        try:
            df_ohlcv = stock.get_market_ohlcv_by_date(
                fromdate=fromdate, todate=todate, ticker=code
            )
            try:
                df_trader = stock.get_market_trading_volume_by_date(
                    fromdate=fromdate, todate=todate, ticker=code, detail=False
                )
            except Exception as e_trader:  # 수급은 옵션. 실패해도 OHLCV는 살린다.
                logging.warning("[%s] trader fetch 실패: %s. 수급 0으로 채움.", code, e_trader)
                df_trader = None
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
