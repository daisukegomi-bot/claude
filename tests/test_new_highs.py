import datetime as dt
import json
import unittest
from unittest import mock

import new_highs as m


def ranking_html(results, has_next=False):
    state = {"pageInfo": {}, "mainRankingList": {
        "results": results, "paging": {"hasNext": has_next, "page": 1}}}
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state, ensure_ascii=False)}</script>"


def num(v):
    return f'<td class="x"><span><span><span class="v">{v}</span></span></span></td>'


def history_html(rows, split_on=None):
    """rows: [(date, adj_close)]。実ページ同様 終値列と調整後終値列を持つ。"""
    trs = []
    for d, c in rows:
        trs.append(f'<tr><th class="h">{d.year}/{d.month}/{d.day}</th>'
                   + num("1") + num("1") + num("1") + num(f"{c * 2:,}") + num("100")
                   + num(f"{c:,}") + num("10") + num("1") + "</tr>")
        if split_on == d:
            trs.append(f'<tr><th>{d.year}/{d.month}/{d.day}</th><td colspan="8">分割: 1株 -&gt; 2株</td></tr>')
    return ('<table id="histlist"><thead><tr><th>日付</th><th>始値</th></tr></thead><tbody>'
            + "".join(trs) + "</tbody></table>")


class ParseTest(unittest.TestCase):
    def test_ranking(self):
        html = ranking_html([
            {"rank": "1", "stockCode": "153A", "marketName": "東証GRT", "stockName": "(株)カウリス"},
            {"rank": "2", "stockCode": "7203", "marketName": "東証PRM", "stockName": "トヨタ自動車(株)"},
        ], has_next=True)
        cands, has_next = m.parse_ranking(html)
        self.assertEqual([(c.code, c.name, c.market) for c in cands],
                         [("153A", "(株)カウリス", "東証GRT"), ("7203", "トヨタ自動車(株)", "東証PRM")])
        self.assertTrue(has_next)

    def test_history_uses_adjusted_close_and_skips_split_rows(self):
        d1, d2 = dt.date(2026, 10, 7), dt.date(2026, 10, 8)
        rows = m.parse_history(history_html([(d2, 1300), (d1, 1200.5)], split_on=d1))
        self.assertEqual([(r.date, r.close) for r in rows], [(d2, 1300.0), (d1, 1200.5)])
        # 高値は 調整後終値/終値 の比率で調整される (高値1, 終値=2×調整後終値)
        self.assertEqual(rows[0].high, 0.5)


def series(latest, n_days, base, peaks=()):
    peaks = dict(peaks)
    return [(latest - dt.timedelta(days=i), peaks.get(latest - dt.timedelta(days=i), base))
            for i in range(n_days)]


class FakeYahoo:
    """日次系列から Yahoo の時系列ページ（日足/週足、from/to、20行/ページ）を模倣する。"""

    def __init__(self, rows):
        self.daily = sorted(rows, reverse=True)  # [(date, close)] 新しい順
        self.calls = []

    def weekly(self, rows):
        weeks = {}
        for d, c in rows:
            ws = d - dt.timedelta(days=d.weekday())
            w = weeks.setdefault(ws, {"close": None, "high": c})
            w["close"] = w["close"] if w["close"] is not None else c  # 最新日の終値
            w["high"] = max(w["high"], c)
        return [(ws, w["close"], w["high"]) for ws, w in sorted(weeks.items(), reverse=True)]

    def get(self, url):
        self.calls.append(url)
        q = dict(kv.split("=") for kv in url.split("?", 1)[1].split("&"))
        page = int(q["page"])
        rows = self.daily
        if "from" in q:
            lo = dt.datetime.strptime(q["from"], "%Y%m%d").date()
            hi = dt.datetime.strptime(q["to"], "%Y%m%d").date()
            rows = [r for r in rows if lo <= r[0] <= hi]
        elif q["timeFrame"] == "d":
            rows = [r for r in rows if (rows[0][0] - r[0]).days < 365]  # 既定は直近1年のみ
        if q["timeFrame"] == "w":
            rows = self.weekly(rows)
        else:
            rows = [(d, c, c) for d, c in rows]
        chunk = rows[(page - 1) * 20: page * 20]
        return history_html_hl(chunk)


def history_html_hl(rows):
    trs = "".join(
        f'<tr><th>{d.year}/{d.month}/{d.day}</th>' + num("1") + num(f"{h:,}") + num("1")
        + num(f"{c:,}") + num("100") + num(f"{c:,}") + "</tr>"
        for d, c, h in rows)
    return f'<table id="histlist">{trs}</table>'


class CheckTest(unittest.TestCase):
    def run_check(self, rows):
        fake = FakeYahoo(rows)
        yf = m.Yahoo(sleep=0)
        with mock.patch.object(yf, "get", side_effect=fake.get):
            r = yf.check(m.Candidate("1234", "X", "東証PRM"), 365, 5)
        self.calls = fake.calls
        return r

    def test_over_one_year(self):
        latest, prev = dt.date(2026, 10, 8), dt.date(2024, 3, 1)
        r = self.run_check(series(latest, 1500, 100, {latest: 150, prev: 160, prev - dt.timedelta(days=30): 170}))
        self.assertEqual(r.prev_high_date, prev)
        self.assertEqual(r.gap_label, "約2年7カ月ぶり")
        self.assertLess(len(self.calls), 20)

    def test_older_than_default_one_year_window(self):
        # 既定の時系列（直近1年）だけでは見えない高値も週足経由で見つける
        latest, prev = dt.date(2026, 10, 8), dt.date(2025, 9, 1)
        r = self.run_check(series(latest, 800, 100, {latest: 150, prev: 150}))
        self.assertEqual(r.prev_high_date, prev)

    def test_within_one_year(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 1200, 100, {latest: 150, dt.date(2026, 1, 5): 150})))

    def test_within_last_month(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 1200, 100, {latest: 150, dt.date(2026, 10, 1): 151})))

    def test_highest_in_range(self):
        latest = dt.date(2026, 10, 8)
        r = self.run_check(series(latest, 600, 100, {latest: 150}))
        self.assertIsNone(r.prev_high_date)
        self.assertIn("以上ぶり", r.gap_label)

    def test_short_history(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 200, 100, {latest: 150})))


if __name__ == "__main__":
    unittest.main()
