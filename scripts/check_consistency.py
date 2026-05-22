"""
check_consistency.py — CSV 무결성 검증 (수동 실행).

각 종목 CSV에 대해:
- 행 수
- 컬럼 스키마 일치
- 날짜 정렬·중복·결측치
- 종목코드 6자리 정규화
- 최신 거래일

사용:
    python scripts/check_consistency.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_kst_today, load_config, scan_data_dir  # noqa: E402


REQUIRED_COLS = [
    "date", "open", "high", "low", "close",
    "volume", "trading_value", "change_rate",
    "foreign_net", "institution_net",
]


def check_one(path):
    df = pd.read_csv(path, encoding="utf-8")
    issues = []
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        issues.append(f"missing cols: {missing}")
    rows = len(df)
    last_date = None
    if "date" in df.columns:
        try:
            pd.to_datetime(df["date"])
        except Exception as e:  # noqa: BLE001
            issues.append(f"date parse error: {e}")
        if df["date"].duplicated().any():
            issues.append("duplicate dates")
        if not df["date"].astype(str).is_monotonic_increasing:
            issues.append("dates not sorted ascending")
        last_date = df["date"].max()
    null_cols = [c for c in REQUIRED_COLS if c in df.columns and df[c].isnull().any()]
    if null_cols:
        issues.append(f"null in cols: {null_cols}")
    return rows, last_date, issues


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    config = load_config("config.yml")
    data_dir = config["data_dir"]
    files = scan_data_dir(data_dir)

    if not files:
        print(f"⚠️  {data_dir}/ 비어 있음. 백필을 먼저 실행하세요.")
        return

    today = get_kst_today(config["timezone"]).strftime("%Y-%m-%d")
    print("=== Consistency check ===")
    print(f"data dir : {data_dir}")
    print(f"files    : {len(files)}")
    print(f"today    : {today}")
    print()
    print(f"{'CODE':<8} {'ROWS':>6} {'LAST_DATE':<12} STATUS")
    print("-" * 70)

    total_issues = 0
    for code in sorted(files.keys()):
        path = files[code]
        try:
            rows, last_date, issues = check_one(path)
            status = "OK" if not issues else "; ".join(issues)
            print(f"{code:<8} {rows:>6} {str(last_date):<12} {status}")
            if issues:
                total_issues += 1
        except Exception as e:  # noqa: BLE001
            print(f"{code:<8} {'-':>6} {'-':<12} ERROR: {e}")
            total_issues += 1

    print()
    print(f"=== summary: {len(files)} files, {total_issues} with issues ===")
    if total_issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
