#!/usr/bin/env python3
"""株探 (https://kabutan.jp/) をソースに、終値ベースで「1年以上ぶり」に
新高値を更新した銘柄をリストアップする。

手順:
  1. 株探の「本日、年初来高値を更新した銘柄」一覧 (/warning/?mode=3_1) から候補銘柄を取得する。
  2. 各候補の日足時系列 (/stock/kabuka?code=XXXX&ashi=day) を新しい順に遡り、
     最新終値「以上」の終値を付けた直近の日を探す。
  3. その日が最新日から365日以上前（または取得できた範囲に存在しない）なら
     「終値ベースで1年以上ぶりの新高値」と判定する。

注意: 株価の取得は大引け後（15:30 JST 以降）に実行すること。場中は最新行が
途中値になる。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://kabutan.jp"
NEW_HIGH_LIST_URL = BASE_URL + "/warning/?mode=3_1"
KABUKA_URL = BASE_URL + "/stock/kabuka?code={code}&ashi=day&page={page}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CODE_RE = re.compile(r"code=([0-9A-Z]{4})")


@dataclass
class Candidate:
    code: str
    name: str
    market: str


@dataclass
class PriceRow:
    date: dt.date
    close: float


@dataclass
class Result:
    code: str
    name: str
    market: str
    date: dt.date
    close: float
    prev_high_date: dt.date | None  # 最新終値以上の終値を付けた直近日 (None = 取得範囲内に無し)
    prev_high_close: float | None
    oldest_checked: dt.date

    @property
    def days_since(self) -> int | None:
        return (self.date - self.prev_high_date).days if self.prev_high_date else None

    @property
    def gap_label(self) -> str:
        if self.prev_high_date is None:
            return f"{_span_label(self.date, self.oldest_checked)}以上ぶり（取得範囲内の最高値）"
        return f"{_span_label(self.date, self.prev_high_date)}ぶり"


def _span_label(newer: dt.date, older: dt.date) -> str:
    months = (newer.year - older.year) * 12 + (newer.month - older.month)
    if newer.day < older.day:
        months -= 1
    years, months = divmod(max(months, 0), 12)
    if years and months:
        return f"約{years}年{months}カ月"
    if years:
        return f"約{years}年"
    return f"約{months}カ月"


# ---------------------------------------------------------------- parsing


def _to_float(text: str) -> float | None:
    text = text.strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_candidates(html: str) -> list[Candidate]:
    """年初来高値更新銘柄一覧ページから銘柄を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.stock_table") or soup
    out: dict[str, Candidate] = {}
    for tr in table.find_all("tr"):
        link = tr.find("a", href=CODE_RE)
        if not link:
            continue
        code = CODE_RE.search(link["href"]).group(1)
        th = tr.find("th")
        name = th.get_text(strip=True) if th else ""
        tds = tr.find_all("td")
        market = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        out.setdefault(code, Candidate(code, name, market))
    return list(out.values())


def has_next_page(html: str, page: int) -> bool:
    return f"page={page + 1}" in html


def parse_daily_prices(html: str) -> list[PriceRow]:
    """日足時系列ページから (日付, 終値) を新しい順に抽出する。

    行は <th><time datetime="YYYY-MM-DD"></th><td>始値</td><td>高値</td>
    <td>安値</td><td>終値</td>... の形式。
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[dt.date, PriceRow] = {}
    for tr in soup.find_all("tr"):
        t = tr.find("time", attrs={"datetime": True})
        if not t:
            continue
        try:
            date = dt.date.fromisoformat(t["datetime"][:10])
        except ValueError:
            continue
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        close = _to_float(tds[3].get_text())
        if close is None:
            continue
        rows.setdefault(date, PriceRow(date, close))
    return sorted(rows.values(), key=lambda r: r.date, reverse=True)


# ---------------------------------------------------------------- logic


def find_prev_high(rows: list[PriceRow]) -> PriceRow | None:
    """最新終値以上の終値を付けた直近の日を返す（rows は新しい順）。"""
    latest = rows[0]
    for r in rows[1:]:
        if r.close >= latest.close:
            return r
    return None


class Kabutan:
    def __init__(self, sleep: float = 1.0, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.sleep = sleep
        self.timeout = timeout

    def get(self, url: str) -> str:
        for attempt in range(4):
            try:
                time.sleep(self.sleep)
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except requests.RequestException as exc:
                if attempt == 3:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  retry in {wait}s: {url} ({exc})", file=sys.stderr)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def candidates(self, max_pages: int = 20) -> list[Candidate]:
        out: dict[str, Candidate] = {}
        for page in range(1, max_pages + 1):
            html = self.get(f"{NEW_HIGH_LIST_URL}&page={page}")
            found = parse_candidates(html)
            new = [c for c in found if c.code not in out]
            for c in new:
                out[c.code] = c
            if not new or not has_next_page(html, page):
                break
        return list(out.values())

    def check(self, cand: Candidate, min_days: int, max_years: int) -> Result | None:
        rows: list[PriceRow] = []
        page = 1
        while True:
            html = self.get(KABUKA_URL.format(code=cand.code, page=page))
            got = [r for r in parse_daily_prices(html) if not rows or r.date < rows[-1].date]
            if not got:
                break
            rows.extend(got)
            prev = find_prev_high(rows)
            if prev is not None:
                if (rows[0].date - prev.date).days < min_days:
                    return None  # 1年以内に同等以上の終値あり
                break
            if (rows[0].date - rows[-1].date).days >= max_years * 366:
                break
            if not has_next_page(html, page):
                break
            page += 1
        if len(rows) < 2:
            return None
        prev = find_prev_high(rows)
        if prev is None and (rows[0].date - rows[-1].date).days < min_days:
            # 上場1年未満などで判定期間に満たない
            return None
        latest = rows[0]
        return Result(
            code=cand.code,
            name=cand.name,
            market=cand.market,
            date=latest.date,
            close=latest.close,
            prev_high_date=prev.date if prev else None,
            prev_high_close=prev.close if prev else None,
            oldest_checked=rows[-1].date,
        )


# ---------------------------------------------------------------- output


def write_outputs(results: list[Result], out_dir: Path, run_date: dt.date) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = sorted(results, key=lambda r: (r.days_since is not None, -(r.days_since or 0), r.code))
    csv_path = out_dir / f"{run_date.isoformat()}.csv"
    md_path = out_dir / f"{run_date.isoformat()}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["コード", "銘柄名", "市場", "日付", "終値", "前回高値日", "前回高値終値", "経過日数", "何年ぶり"])
        for r in results:
            w.writerow([
                r.code, r.name, r.market, r.date.isoformat(), r.close,
                r.prev_high_date.isoformat() if r.prev_high_date else "",
                r.prev_high_close if r.prev_high_close is not None else "",
                r.days_since if r.days_since is not None else "",
                r.gap_label,
            ])

    lines = [
        f"# 終値ベースで1年以上ぶりに新高値を更新した銘柄 ({run_date.isoformat()})",
        "",
        "ソース: [株探](https://kabutan.jp/) 「本日、年初来高値を更新した銘柄」＋各銘柄の日足時系列",
        "",
        f"該当 {len(results)} 銘柄",
        "",
        "| コード | 銘柄名 | 市場 | 終値 | 前回これ以上の終値 | 何年ぶり |",
        "|---|---|---|---:|---|---|",
    ]
    for r in results:
        prev = f"{r.prev_high_date.isoformat()} ({r.prev_high_close:,.1f})" if r.prev_high_date else "—"
        lines.append(
            f"| [{r.code}](https://kabutan.jp/stock/?code={r.code}) | {r.name} | {r.market} "
            f"| {r.close:,.1f} | {prev} | {r.gap_label} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-days", type=int, default=365, help="何日以上ぶりを対象にするか (既定: 365)")
    p.add_argument("--max-years", type=int, default=5, help="「何年ぶり」を調べる最大遡及年数 (既定: 5)")
    p.add_argument("--sleep", type=float, default=1.0, help="リクエスト間隔 秒 (既定: 1.0)")
    p.add_argument("--out-dir", type=Path, default=Path("results"))
    p.add_argument("--codes", nargs="*", help="候補一覧の代わりに指定コードのみ判定する")
    args = p.parse_args(argv)

    kb = Kabutan(sleep=args.sleep)
    if args.codes:
        cands = [Candidate(c, "", "") for c in args.codes]
    else:
        cands = kb.candidates()
    print(f"候補 {len(cands)} 銘柄", file=sys.stderr)

    results: list[Result] = []
    for i, c in enumerate(cands, 1):
        try:
            r = kb.check(c, args.min_days, args.max_years)
        except requests.RequestException as exc:
            print(f"[{i}/{len(cands)}] {c.code} 取得失敗: {exc}", file=sys.stderr)
            continue
        mark = f"○ {r.gap_label}" if r else "-"
        print(f"[{i}/{len(cands)}] {c.code} {c.name} {mark}", file=sys.stderr)
        if r:
            results.append(r)

    run_date = max((r.date for r in results), default=dt.date.today())
    csv_path, md_path = write_outputs(results, args.out_dir, run_date)
    print(md_path.read_text(encoding="utf-8"))
    print(f"saved: {csv_path}, {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
