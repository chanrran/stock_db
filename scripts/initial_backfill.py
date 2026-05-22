"""
initial_backfill.py — 강제 재백필 (디버깅·복구용).

지정 종목들의 기존 CSV를 archive로 옮긴 후, 10년치를 새로 받습니다.
인자 없이 실행하면 시트의 모든 종목을 재백필.

사용 예:
    python scripts/initial_backfill.py                  # 모든 종목 재백필
    python scripts/initial_backfill.py 005930           # 특정 종목만
    python scripts/initial_backfill.py 005930 000660
"""
import logging
import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (  # noqa: E402
    archive_stock,
    fetch_pykrx_data_with_retry,
    fetch_stocks_from_google_sheets,
    get_kst_today,
    load_config,
    make_csv_path,
    normalize_code,
    setup_logging,
)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    config = load_config("config.yml")
    log_path = setup_logging(config["log_dir"])
    logging.info("=== Initial backfill start ===")
    logging.info("log file: %s", log_path)

    target_codes = (
        {normalize_code(c) for c in sys.argv[1:]} if len(sys.argv) > 1 else None
    )
    if target_codes:
        logging.info("target: %s", sorted(target_codes))
    else:
        logging.info("target: ALL stocks in sheet")

    sc = config["sheet_columns"]
    retry_cfg = config.get("retry", {})
    stocks = fetch_stocks_from_google_sheets(
        config["sheets_csv_url"], sc["code"], sc["name"], retry_cfg
    )
    if target_codes:
        stocks = [s for s in stocks if s["code"] in target_codes]
        missing = target_codes - {s["code"] for s in stocks}
        for m in sorted(missing):
            logging.warning("시트에서 종목 %s 못 찾음. skip.", m)

    today = get_kst_today(config["timezone"])
    years = int(config["backfill_years"])
    interval = float(config.get("request_interval_sec", 0.5))
    data_dir = config["data_dir"]
    archive_dir = config["archive_dir"]

    ok, fail = 0, 0
    for s in stocks:
        code, name = s["code"], s["name"]
        try:
            archived = archive_stock(code, data_dir, archive_dir)
            if archived:
                logging.info("[ARCHIVE_OLD] %s: %s", code, archived)
            start = today - timedelta(days=365 * years)
            df = fetch_pykrx_data_with_retry(
                code, start.strftime("%Y%m%d"), today.strftime("%Y%m%d"), retry_cfg
            )
            if df.empty:
                logging.warning("[EMPTY] %s %s: 데이터 없음", code, name)
                fail += 1
                continue
            os.makedirs(data_dir, exist_ok=True)
            path = make_csv_path(code, name, data_dir)
            df.to_csv(path, index=False, encoding="utf-8")
            logging.info("[BACKFILL] %s %s: %d rows → %s", code, name, len(df), path)
            ok += 1
        except Exception as e:  # noqa: BLE001
            logging.error("[ERROR] %s %s: %s", code, name, e)
            fail += 1
        time.sleep(interval)

    logging.info("=== Initial backfill done === ok=%d fail=%d", ok, fail)


if __name__ == "__main__":
    main()
