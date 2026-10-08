"""一時的な調査用: Yahoo!ファイナンスのページ構造をログに出す。"""
import json, re, sys
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "ja"}

def show(obj, path="", depth=0, maxd=5):
    if depth > maxd: return
    if isinstance(obj, dict):
        for k, v in obj.items():
            t = type(v).__name__
            extra = f" len={len(v)}" if isinstance(v, (list, dict, str)) else f" = {v!r}"
            if isinstance(v, str): extra = f" = {v[:60]!r}"
            print(f"{'  '*depth}{path}.{k}: {t}{extra}")
            show(v, path + "." + k, depth + 1, maxd)
    elif isinstance(obj, list) and obj:
        print(f"{'  '*depth}{path}[0]:")
        show(obj[0], path + "[0]", depth + 1, maxd)

for url in sys.argv[1:]:
    r = requests.get(url, headers=H, timeout=30)
    print("=" * 80); print(url, r.status_code, len(r.text), r.url)
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if m:
        st = json.loads(m.group(1))
        for k in st: print("TOP", k)
        interesting = {k: v for k, v in st.items() if re.search(r"(?i)rank|histor|price|stock|quote|pag", k)}
        show(interesting, maxd=4)
    else:
        print("no __PRELOADED_STATE__; snippet:")
        i = r.text.find("<table")
        print(r.text[i:i+3000] if i >= 0 else r.text[:3000])
    links = sorted(set(re.findall(r'href="([^"]*(?:page=|history)[^"]*)"', r.text)))[:20]
    print("LINKS", links)
