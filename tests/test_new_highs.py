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
        self.assertEqual(rows, [m.PriceRow(d2, 1300.0), m.PriceRow(d1, 1200.5)])


def series(latest, n_days, base, peaks=()):
    peaks = dict(peaks)
    return [(latest - dt.timedelta(days=i), peaks.get(latest - dt.timedelta(days=i), base))
            for i in range(n_days)]


class CheckTest(unittest.TestCase):
    def run_check(self, rows, per_page=20):
        pages = [rows[i:i + per_page] for i in range(0, len(rows), per_page)]

        def fake_get(url):
            page = int(url.rsplit("page=", 1)[1])
            return history_html(pages[page - 1]) if page <= len(pages) else history_html([])

        yf = m.Yahoo(sleep=0)
        with mock.patch.object(yf, "get", side_effect=fake_get):
            return yf.check(m.Candidate("1234", "X", "東証PRM"), 365, 5)

    def test_over_one_year(self):
        latest, prev = dt.date(2026, 10, 8), dt.date(2024, 3, 1)
        r = self.run_check(series(latest, 1200, 100, {latest: 150, prev: 160}))
        self.assertEqual(r.prev_high_date, prev)
        self.assertEqual(r.gap_label, "約2年7カ月ぶり")

    def test_within_one_year(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 1200, 100, {latest: 150, dt.date(2026, 1, 5): 150})))

    def test_highest_in_range(self):
        latest = dt.date(2026, 10, 8)
        r = self.run_check(series(latest, 500, 100, {latest: 150}))
        self.assertIsNone(r.prev_high_date)
        self.assertIn("以上ぶり", r.gap_label)

    def test_short_history(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 200, 100, {latest: 150})))


if __name__ == "__main__":
    unittest.main()
