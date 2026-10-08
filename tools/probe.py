"""一時的な調査用: 時系列ページの遡及可能範囲を確認する。"""
import re, sys, time
sys.path.insert(0, ".")
import new_highs as m

yf = m.Yahoo(sleep=0.5)
def rng(url):
    html = yf.get(url)
    rows = m.parse_history(html)
    tot = re.findall(r'"totalPage":\s*(\d+)|totalPage[^0-9]{0,10}(\d+)', html)[:2]
    print(url, len(rows), rows[0].date if rows else None, rows[-1].date if rows else None, "totalPage?", tot)

base = "https://finance.yahoo.co.jp/quote/7203.T/history"
for p in (12, 13, 14, 20, 40):
    rng(f"{base}?timeFrame=d&page={p}")
for q in ("from=20200101&to=20261008&timeFrame=d&page=1",
          "from=20200101&to=20261008&timeFrame=d&page=40",
          "from=20200101&to=20261008&timeFrame=d&page=80",
          "from=20230101&to=20231231&timeFrame=d&page=1",
          "from=20200101&to=20261008&timeFrame=w&page=1",
          "from=20200101&to=20261008&timeFrame=w&page=10"):
    rng(f"{base}?{q}")
