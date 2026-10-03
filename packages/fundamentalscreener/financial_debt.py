"""Quarantine the unsupported AkShare debt mapping (issue #183).

These source labels all use the same Sina financial endpoint. Keep the guard
until an equivalent field and its provenance have been explicitly verified.
"""
from .financial_pit import FINANCIAL_DEDUP_ORDER

UNVERIFIED_DEBT_SOURCES = ("akshare", "akshare_ths", "akshare_em")


def add_debt_quality_issues(conn, report, analysis_date, classification_system):
    rows = conn.execute(
        "SELECT code, source, interest_bearing_debt_ratio, report_period, fetch_run_id "
        "FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY "
        + FINANCIAL_DEDUP_ORDER + ") AS rn FROM financial_metrics "
        "WHERE disclosure_date <= ? AND as_of_date <= ?) f WHERE rn = 1 "
        "AND EXISTS (SELECT 1 FROM sector_constituents sc WHERE sc.code = f.code "
        "AND sc.classification_system = ? AND sc.as_of_date = ("
        "SELECT MAX(as_of_date) FROM sector_constituents WHERE sector_id = sc.sector_id "
        "AND classification_system = ? AND as_of_date <= ?))",
        (analysis_date, analysis_date, classification_system,
         classification_system, analysis_date),
    ).fetchall()
    for code, source, value, period, run_id in rows:
        if source not in UNVERIFIED_DEBT_SOURCES:
            continue
        cached = value is not None
        report.add_issue(
            "interest_bearing_debt_cache_ignored" if cached
            else "interest_bearing_debt_unavailable",
            "info",
            f"{code} {period}: "
            + ("旧缓存有息负债率来自非等价映射，已忽略。" if cached
               else "当前财务源未提供已核实的有息负债率。")
            + "长期负债比率不能代用；有息负债率按缺失处理。"
            "资产负债率可用时，杠杆分仅由它计算，杠杆分量权重仍为15%；"
            "整个杠杆分量缺失时，财务总分才按剩余分量权重重新归一。",
            entity_type="company", entity_id=code,
            raw_field_name="长期负债比率(%)",
            details={"source": source, "fetch_run_id": run_id,
                     "report_period": period, "field": "interest_bearing_debt_ratio"},
        )
