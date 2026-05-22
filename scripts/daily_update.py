"""
daily_update.py — 매일 실행되는 메인 스크립트.

1) Google Sheets 종목 마스터 fetch
2) 로컬 data/ 스캔
3) 분기 처리:
     - 신규 종목  → 10년치 백필
     - 기존 종목  → 마지막 날짜+1일 ~ 오늘 fetch해서 append
     - 시트에서 빠진 종목 → archive로 이동 (삭제 X)
4) 로그 저장. 실제 commit/push는 GitHub Actions가 처리.
"""
import logging
import os
import sys
import time
from datetime import timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (  # noqa: E402
    archive_stock,
    fetch_pykrx_data_with_retry,
    fetch_stocks_from_google_sheets,
    get_kst_today,
    load_config,
    make_csv_path,
    scan_data_dir,
    setup_logging,
)


def backfill_new(code, name, today, years, retry_cfg, data_dir):
    start = today - timedelta(days=365 * years)
    from_date = start.strftime("%Y%m%d")
    to_date = today.strftime("%Y%m%d")
    df = fetch_pykrx_data_with_retry(code, from_date, to_date, retry_cfg)
    if df.empty:
        logging.warning("[BACKFILL_EMPTY] %s %s: 데이터 없음", code, name)
        return "skip"
    os.makedirs(data_dir, exist_ok=True)
    path = make_csv_path(code, name, data_dir)
    df.to_csv(path, index=False, encoding="utf-8")
    logging.info("[BACKFILL] %s %s: %d rows → %s", code, name, len(df), path)
    return "backfill"


def update_existing(code, name, csv_path, today, retry_cfg, data_dir):
    df_existing = pd.read_csv(csv_path, encoding="utf-8")
    if df_existing.empty or "date" not in df_existing.columns:
        logging.warning("[EMPTY_FILE] %s: 파일 비어있음/스키마 깨짐. skip.", code)
        return "skip"

    last_date = pd.to_datetime(df_existing["date"].max()).date()
    today_date = today.date() if hasattr(today, "date") else today

    if last_date >= today_date:
        logging.info("[SKIP] %s %s: already up to date (%s)", code, name, last_date)
        return "skip"

    from_date = (last_date + timedelta(days=1)).strftime("%Y%m%d")
    to_date = today_date.strftime("%Y%m%d")

    df_new = fetch_pykrx_data_with_retry(code, from_date, to_date, retry_cfg)
    if df_new.empty:
        logging.info("[HOLIDAY] %s %s: 신규 거래일 없음 (휴장 또는 미정산)", code, name)
        return "holiday"

    combined = pd.concat([df_existing, df_new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_csv(csv_path, index=False, encoding="utf-8")
    logging.info(
        "[UPDATE] %s %s: +%d rows (total %d)", code, name, len(df_new), len(combined)
    )

    # 종목명 변경 시 파일 rename
    expected_path = make_csv_path(code, name, data_dir)
    if expected_path != csv_path and not os.path.exists(expected_path):
        os.rename(csv_path, expected_path)
        logging.info("[RENAME] %s: %s → %s", code, csv_path, expected_path)

    return "update"


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    config = load_config("config.yml")
    log_path = setup_logging(config["log_dir"])

    logging.info("=== Daily update start ===")
    logging.info("log file: %s", log_path)

    data_dir = config["data_dir"]
    archive_dir = config["archive_dir"]
    backfill_years = int(config["backfill_years"])
    interval = float(config.get("request_interval_sec", 0.5))
    retry_cfg = config.get("retry", {"max_attempts": 3, "backoff_base_sec": 1.0})
    sc = config["sheet_columns"]

    # 1) 종목 마스터
    try:
        stocks = fetch_stocks_from_google_sheets(
            config["sheets_csv_url"], sc["code"], sc["name"], retry_cfg
        )
    except Exception as e:  # noqa: BLE001
        logging.error("종목 마스터 fetch 실패. 작업 중단: %s", e)
        sys.exit(1)
    logging.info("종목 마스터: %d 종목", len(stocks))
    if not stocks:
        logging.error("종목 리스트가 비어 있습니다. 작업 중단.")
        sys.exit(1)

    sheet_codes = {s["code"] for s in stocks}
    local = scan_data_dir(data_dir)
    logging.info("로컬 CSV: %d 종목", len(local))

    today = get_kst_today(config["timezone"])
    stats = {
        "backfill": 0, "update": 0, "skip": 0,
        "holiday": 0, "archive": 0, "error": 0,
    }

    for s in stocks:
        code, name = s["code"], s["name"]
        try:
            if code in local:
                result = update_existing(
                    code, name, local[code], today, retry_cfg, data_dir
                )
            else:
                result = backfill_new(
                    code, name, today, backfill_years, retry_cfg, data_dir
                )
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:  # noqa: BLE001
            logging.error("[ERROR] %s %s: %s", code, name, e)
            stats["error"] += 1
        time.sleep(interval)

    # 2) 시트에서 빠진 종목 → archive
    for code in sorted(set(local.keys()) - sheet_codes):
        try:
            dst = archive_stock(code, data_dir, archive_dir)
            if dst:
                logging.info("[ARCHIVE] %s: moved to %s", code, dst)
                stats["archive"] += 1
        except Exception as e:  # noqa: BLE001
            logging.error("[ERROR archive] %s: %s", code, e)
            stats["error"] += 1

    logging.info("=== Daily update done === stats: %s", stats)


if __name__ == "__main__":
    main()
