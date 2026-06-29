"""同花顺(THS) 板块详情页抓取与解析。

akshare 没有稳定的 THS 成分接口；在真实环境通过抓取页面获取成分股表格（含分页）。
为保持主数据源可在最小依赖环境 import，这里仍采用惰性导入 requests / bs4。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


def scrape_ths_constituents(
    sector_id: str,
    *,
    parse_stock_table: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
    parse_total_pages: Optional[Callable[[Any], int]] = None,
) -> List[Dict[str, Any]]:
    """抓取 THS 板块详情页，返回 ``[{"代码": "...", "名称": "..."}, ...]``。"""

    if not sector_id:
        return []

    import requests  # type: ignore[import-not-found]
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]

    if parse_stock_table is None:
        parse_stock_table = parse_ths_stock_table
    if parse_total_pages is None:
        parse_total_pages = parse_ths_total_pages

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"
        )
    }

    all_records: List[Dict[str, Any]] = []

    main_url = f"http://q.10jqka.com.cn/thshy/detail/code/{sector_id}/"
    resp = requests.get(main_url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, features="lxml")
    all_records.extend(parse_stock_table(soup))
    total_pages = parse_total_pages(soup)

    for page in range(2, total_pages + 1):
        ajax_url = (
            f"http://q.10jqka.com.cn/thshy/detail/code/{sector_id}/page/{page}/ajax/1/"
        )
        try:
            resp = requests.get(ajax_url, headers=headers, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, features="lxml")
            all_records.extend(parse_stock_table(soup))
        except Exception:
            continue

    return all_records


def parse_ths_stock_table(soup: Any) -> List[Dict[str, Any]]:
    """从页面中提取成分股代码和名称。"""

    records: List[Dict[str, Any]] = []
    for table in soup.find_all("table"):
        tbody = table.find("tbody")
        if not tbody:
            continue
        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            code = tds[1].text.strip()
            name = tds[2].text.strip()
            if code and code[0].isdigit():
                records.append({"代码": code, "名称": name})
    return records


def parse_ths_total_pages(soup: Any) -> int:
    """从分页元素解析总页数。"""

    pager = soup.find(class_="m-pager")
    if pager:
        match = re.search(r"/(\d+)", pager.text)
        if match:
            return int(match.group(1))
    return 1


__all__ = [
    "parse_ths_stock_table",
    "parse_ths_total_pages",
    "scrape_ths_constituents",
]
