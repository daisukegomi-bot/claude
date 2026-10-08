#!/usr/bin/env python3
"""Yahoo!ファイナンス (https://finance.yahoo.co.jp/stocks) をソースに、
終値ベースで「1年以上ぶり」に新高値を更新した銘柄をリストアップする。

手順:
  1. 日本株ランキング「年初来高値更新」(/stocks/ranking/yearToDateHigh) から候補銘柄を取得する。
  2. 各候補の時系列 (/quote/XXXX.T/history) を新しい順に遡り、
     最新終値「以上」の終値を付けた直近の日を探す（株式分割を考慮した調整後終値で比較）。
  3. その日が最新日から365日以上前（または遡れた範囲に存在しない）なら
     「終値ベースで1年以上ぶりの新高値」と判定する。

注意: 大引け後（15:30 JST 以降）に実行すること。場中は最新行が途中値になる。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://finance.yahoo.co.jp"
RANKING_URL = BASE_URL + "/stocks/ranking/yearToDateHigh?market=all&term=daily&page={page}"
HISTORY_URL = BASE_URL + "/quote/{code}.T/history?timeFrame=d&page={page}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
STATE_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", re.S)
DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


@dataclass
class Candidate:
    code: str
    name: str
    market: str


@dataclass
class PriceRow:
    date: dt.date
    close: float  # 調整後終値


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


def parse_ranking(html: str) -> tuple[list[Candidate], bool]:
    """ランキングページの埋め込み JSON から (銘柄一覧, 次ページ有無) を返す。"""
    m = STATE_RE.search(html)
    if not m:
        raise ValueError("ランキングページに __PRELOADED_STATE__ が見つかりません")
    state = json.loads(m.group(1))
    ranking = state.get("mainRankingList") or {}
    cands = [
        Candidate(r["stockCode"], r.get("stockName", ""), r.get("marketName", ""))
        for r in ranking.get("results") or []
        if r.get("stockCode")
    ]
    has_next = bool((ranking.get("paging") or {}).get("hasNext"))
    return cands, has_next


def parse_history(html: str) -> list[PriceRow]:
    """時系列ページの表 (#histlist) から (日付, 調整後終値) を新しい順に抽出する。

    列: 日付 | 始値 | 高値 | 安値 | 終値 | 出来高 | 調整後終値 | PER | PBR
    株式分割などの行（数値列が無い行）は無視する。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="histlist") or soup
    rows: dict[dt.date, PriceRow] = {}
    for tr in table.find_all("tr"):
        th = tr.find("th")
        m = DATE_RE.search(th.get_text()) if th else None
        if not m:
            continue
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        close = _to_float(tds[5].get_text()) if len(tds) > 5 else None
        if close is None:
            close = _to_float(tds[3].get_text())
        if close is None:
            continue
        date = dt.date(int(m[1]), int(m[2]), int(m[3]))
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


class Yahoo:
    def __init__(self, sleep: float = 1.0, timeout: float = 30.0):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        })
        self.sleep = sleep
        self.timeout = timeout

    def get(self, url: str) -> str:
        for attempt in range(4):
            try:
                time.sleep(self.sleep)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code in (403, 405):
                    # WAF によるブロック。リトライしても変わらないので即失敗
                    raise SystemExit(f"アクセスを拒否されました (HTTP {resp.status_code}): {url}")
                resp.raise_for_status()
                resp.encoding = "utf-8"
                return resp.text
            except requests.RequestException as exc:
                if attempt == 3:
                    raise
                wait = 2 ** (attempt + 1)
                print(f"  retry in {wait}s: {url} ({exc})", file=sys.stderr)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def candidates(self, max_pages: int = 50) -> list[Candidate]:
        out: dict[str, Candidate] = {}
        for page in range(1, max_pages + 1):
            found, has_next = parse_ranking(self.get(RANKING_URL.format(page=page)))
            new = [c for c in found if c.code not in out]
            for c in new:
                out[c.code] = c
            if not new or not has_next:
                break
        return list(out.values())

    def check(self, cand: Candidate, min_days: int, max_years: int) -> Result | None:
        rows: list[PriceRow] = []
        page = 1
        while True:
            got = parse_history(self.get(HISTORY_URL.format(code=cand.code, page=page)))
            got = [r for r in got if not rows or r.date < rows[-1].date]
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
        "ソース: [Yahoo!ファイナンス](https://finance.yahoo.co.jp/stocks) 「年初来高値更新」ランキング＋各銘柄の時系列（調整後終値）",
        "",
        f"該当 {len(results)} 銘柄",
        "",
        "| コード | 銘柄名 | 市場 | 終値 | 前回これ以上の終値 | 何年ぶり |",
        "|---|---|---|---:|---|---|",
    ]
    for r in results:
        prev = f"{r.prev_high_date.isoformat()} ({r.prev_high_close:,.1f})" if r.prev_high_date else "—"
        lines.append(
            f"| [{r.code}]({BASE_URL}/quote/{r.code}.T) | {r.name} | {r.market} "
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
    p.add_argument("--codes", nargs="*", help="ランキングの代わりに指定コードのみ判定する")
    args = p.parse_args(argv)

    yf = Yahoo(sleep=args.sleep)
    if args.codes:
        cands = [Candidate(c, "", "") for c in args.codes]
    else:
        cands = yf.candidates()
    print(f"候補 {len(cands)} 銘柄", file=sys.stderr)

    results: list[Result] = []
    for i, c in enumerate(cands, 1):
        try:
            r = yf.check(c, args.min_days, args.max_years)
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
