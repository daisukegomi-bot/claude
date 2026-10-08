import datetime as dt
import unittest
from unittest import mock

import kabutan_new_highs as m

LIST_HTML = """
<table class="stock_table"><tbody>
<tr><td class="tac"><a href="/stock/?code=7203">7203</a></td><th scope="row">トヨタ自動車</th><td>東Ｐ</td><td>3,000</td></tr>
<tr><td class="tac"><a href="/stock/?code=130A">130A</a></td><th scope="row">ベリテ</th><td>東Ｇ</td><td>500</td></tr>
</tbody></table>
"""


def kabuka_html(rows):
    trs = "".join(
        f'<tr><th><time datetime="{d.isoformat()}">x</time></th>'
        f"<td>1</td><td>1</td><td>1</td><td>{c:,}</td><td>0</td></tr>"
        for d, c in rows
    )
    return f'<table class="stock_kabuka_dwm">{trs}</table>'


class ParseTest(unittest.TestCase):
    def test_candidates(self):
        cs = m.parse_candidates(LIST_HTML)
        self.assertEqual([(c.code, c.name, c.market) for c in cs],
                         [("7203", "トヨタ自動車", "東Ｐ"), ("130A", "ベリテ", "東Ｇ")])

    def test_daily_prices(self):
        html = kabuka_html([(dt.date(2026, 10, 7), 1200.5), (dt.date(2026, 10, 8), 1300)])
        rows = m.parse_daily_prices(html)
        self.assertEqual(rows[0], m.PriceRow(dt.date(2026, 10, 8), 1300.0))
        self.assertEqual(rows[1].close, 1200.5)


def series(latest, n_days, base, peaks=()):
    """latest から n_days 日遡る日次系列。peaks は {date: close} の上書き。"""
    out = []
    for i in range(n_days):
        d = latest - dt.timedelta(days=i)
        out.append((d, dict(peaks).get(d, base)))
    return out


class CheckTest(unittest.TestCase):
    def run_check(self, rows, per_page=30):
        pages = [rows[i:i + per_page] for i in range(0, len(rows), per_page)]

        def fake_get(url):
            page = int(url.rsplit("page=", 1)[1])
            if page > len(pages):
                return "<table></table>"
            html = kabuka_html(pages[page - 1])
            if page < len(pages):
                html += f'<a href="?page={page + 1}">next</a>'
            return html

        kb = m.Kabutan(sleep=0)
        with mock.patch.object(kb, "get", side_effect=fake_get):
            return kb.check(m.Candidate("1234", "X", "東Ｐ"), 365, 5)

    def test_over_one_year(self):
        latest = dt.date(2026, 10, 8)
        prev = dt.date(2024, 3, 1)
        rows = series(latest, 1200, 100, {latest: 150, prev: 160})
        r = self.run_check(rows)
        self.assertIsNotNone(r)
        self.assertEqual(r.prev_high_date, prev)
        self.assertEqual(r.gap_label, "約2年7カ月ぶり")

    def test_within_one_year(self):
        latest = dt.date(2026, 10, 8)
        rows = series(latest, 1200, 100, {latest: 150, dt.date(2026, 1, 5): 150})
        self.assertIsNone(self.run_check(rows))

    def test_all_time_in_range(self):
        latest = dt.date(2026, 10, 8)
        rows = series(latest, 500, 100, {latest: 150})
        r = self.run_check(rows)
        self.assertIsNone(r.prev_high_date)
        self.assertIn("以上ぶり", r.gap_label)

    def test_short_history(self):
        latest = dt.date(2026, 10, 8)
        self.assertIsNone(self.run_check(series(latest, 200, 100, {latest: 150})))


if __name__ == "__main__":
    unittest.main()
