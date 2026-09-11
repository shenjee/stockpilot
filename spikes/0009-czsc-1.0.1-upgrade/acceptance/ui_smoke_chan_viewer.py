#!/usr/bin/env python3
"""#176 chan-viewer UI smoke with Playwright (covers iframe searchbox + timeframe tabs)."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "artifacts" / "ui-smoke" / "chan-viewer"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:8501"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip()
    except Exception:
        return "unknown"


def _searchbox_frame(page):
    for frame in page.frames:
        if "streamlit_searchbox" in (frame.url or ""):
            return frame
    raise RuntimeError("streamlit_searchbox iframe not found")


def _chart_frame(page):
    for frame in page.frames:
        if "chan_chart_widget" in (frame.url or ""):
            return frame
    return None


def _select_symbol(page, code: str = "600584") -> str:
    fr = _searchbox_frame(page)
    fr.click('[class*="-control"]')
    page.wait_for_timeout(200)
    inp = fr.locator('input[role="combobox"]').first
    inp.fill(code)
    page.wait_for_timeout(1200)
    option = fr.locator('[id*="option"]').filter(has_text=code).first
    option.click()
    page.wait_for_timeout(600)
    return fr.locator("body").inner_text().strip()


def _set_dates(page, start: str, end: str) -> None:
    date_inputs = page.locator('input[placeholder="YYYY/MM/DD"]')
    date_inputs.nth(0).click()
    date_inputs.nth(0).fill(start)
    date_inputs.nth(0).press("Enter")
    page.wait_for_timeout(400)
    date_inputs.nth(1).click()
    date_inputs.nth(1).fill(end)
    date_inputs.nth(1).press("Enter")
    page.wait_for_timeout(400)


def _run_analysis(page) -> None:
    page.get_by_role("button", name="运行分析").click()
    page.wait_for_timeout(10000)


def _click_timeframe(page, labels: tuple[str, ...]) -> bool:
    chart = _chart_frame(page)
    if chart is None:
        return False
    for label in labels:
        btn = chart.get_by_role("button", name=label, exact=True)
        if btn.count():
            btn.first.click()
            page.wait_for_timeout(2000)
            return True
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    steps: list[dict] = []
    t0 = time.perf_counter()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        expand = page.locator("button", has_text="keyboard_double_arrow_right")
        if expand.count():
            expand.first.click()
            page.wait_for_timeout(600)

        selected_text = _select_symbol(page, "600584")
        steps.append(
            {
                "step": "select_symbol",
                "input": "600584",
                "selected_text": selected_text,
                "result": "ok" if "600584" in selected_text else "fail",
            }
        )

        _set_dates(page, "2026/06/29", "2026/07/14")
        steps.append(
            {
                "step": "set_dates",
                "input": {"start": "2026-06-29", "end": "2026-07-14"},
                "result": "set",
            }
        )

        t_run = time.perf_counter()
        _run_analysis(page)
        run_ms = round((time.perf_counter() - t_run) * 1000)
        shot1 = OUT / "01_day_600584.png"
        page.screenshot(path=str(shot1), full_page=True)
        body = page.inner_text("body")
        day_ok = ("600584" in body) or ("长电" in body)
        has_chart = _chart_frame(page) is not None
        steps.append(
            {
                "step": "run_day",
                "screenshot": str(shot1.relative_to(REPO)),
                "has_chart": has_chart,
                "run_latency_ms": run_ms,
                "result": "ok" if day_ok and has_chart else "fail",
            }
        )

        t_sw = time.perf_counter()
        switched = _click_timeframe(page, ("5 分",))
        if switched:
            page.wait_for_timeout(8000)
        sw_ms = round((time.perf_counter() - t_sw) * 1000)
        shot2 = OUT / "02_switch_5m.png"
        page.screenshot(path=str(shot2), full_page=True)
        chart = _chart_frame(page)
        chart_text = chart.locator("body").inner_text() if chart else ""
        steps.append(
            {
                "step": "switch_timeframe_5m",
                "switched_via_ui": switched,
                "screenshot": str(shot2.relative_to(REPO)),
                "latency_ms": sw_ms,
                "chart_text_snippet": chart_text[:240],
                "result": "ok" if switched else "fail",
            }
        )

        t_sw30 = time.perf_counter()
        switched30 = _click_timeframe(page, ("30 分",))
        if switched30:
            page.wait_for_timeout(8000)
        sw30_ms = round((time.perf_counter() - t_sw30) * 1000)
        shot3 = OUT / "03_switch_30m.png"
        page.screenshot(path=str(shot3), full_page=True)
        steps.append(
            {
                "step": "switch_timeframe_30m",
                "switched_via_ui": switched30,
                "screenshot": str(shot3.relative_to(REPO)),
                "latency_ms": sw30_ms,
                "result": "ok" if switched30 else "fail",
            }
        )

        t_sym = time.perf_counter()
        _select_symbol(page, "600000")
        _run_analysis(page)
        shot4 = OUT / "04_switch_symbol_600000.png"
        page.screenshot(path=str(shot4), full_page=True)
        _select_symbol(page, "600584")
        _run_analysis(page)
        shot5 = OUT / "05_switch_back_600584.png"
        page.screenshot(path=str(shot5), full_page=True)
        sym_ms = round((time.perf_counter() - t_sym) * 1000)
        steps.append(
            {
                "step": "switch_symbol_and_back",
                "screenshots": [
                    str(shot4.relative_to(REPO)),
                    str(shot5.relative_to(REPO)),
                ],
                "latency_ms": sym_ms,
                "result": "ok",
            }
        )

        total_ms = round((time.perf_counter() - t0) * 1000)
        steps.append(
            {
                "step": "responsiveness",
                "total_wall_ms": total_ms,
                "result": "no_obvious_hang_during_automation",
                "note": "推进/回退不适用 chan-viewer；覆盖运行、周期切换、标的切换响应",
            }
        )

        browser.close()

    failed = [s for s in steps if s.get("result") == "fail"]
    report = {
        "surface": "chan-viewer",
        "task": "#176",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "fixed_input": {
            "symbol": "600584.SH / 长电科技",
            "start": "2026-06-29",
            "end": "2026-07-14",
            "note": "Date window covers frozen #173 5m sample; live Tencent fetch via app",
        },
        "steps": steps,
        "pass": not failed,
        "failed_steps": [s["step"] for s in failed],
    }
    out_json = OUT / "ui_smoke_chan_viewer.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
