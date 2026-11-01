"""参議院の公開ページ（議案情報・本会議投票結果）を読み取る。

- 外部ライブラリは使わない（標準ライブラリだけ）。
- SANGIIN_CACHE を指定すると、ネットに出ずにそのフォルダの保存済みHTMLを読む（テスト用）。
"""
import html
import os
import re
import time
import urllib.request

BASE = "https://www.sangiin.go.jp"
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
CACHE = os.environ.get("SANGIIN_CACHE")


def fetch(url, retries=3):
    """URLのHTMLを文字列で返す。参議院のページはUTF-8。"""
    if CACHE:
        name = url.rstrip("/").split("/")[-1]
        for cand in (os.path.join(CACHE, name), os.path.join(CACHE, "meisai", url.split("/gian/")[-1].replace("/meisai/", "_")),
                     os.path.join(CACHE, "vote", name)):
            if os.path.exists(cand):
                return open(cand, encoding="utf-8", errors="replace").read()
        raise FileNotFoundError(url)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r]+", " ", s).strip()


def session_index_url(session):
    return f"{BASE}/japanese/joho1/kousei/gian/{session}/gian.htm"


def list_bills(session):
    """その国会の議案一覧ページから、議案ごとの詳細ページURLを返す。"""
    b = fetch(session_index_url(session))
    hrefs = re.findall(r'href="\./meisai/(m\d+\.htm)"', b)
    seen, out = set(), []
    for h in hrefs:
        if h not in seen:
            seen.add(h)
            out.append(f"{BASE}/japanese/joho1/kousei/gian/{session}/meisai/{h}")
    return out


def parse_bill(url):
    """議案の詳細ページを辞書にする（キーは「表の名前/項目名」）。"""
    b = fetch(url)
    session = re.search(r"/gian/(\d+)/", url).group(1)
    r = {"url": url, "session": session}
    for summ, body in re.findall(r'<table[^>]*summary="([^"]*)"[^>]*>(.*?)</table>', b, flags=re.S):
        sec = summ.replace("情報", "")
        for th, td in re.findall(r'<th scope="row"[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>', body, flags=re.S):
            r[sec + "/" + clean(th)] = clean(td)
            v = re.search(r'href="([^"]*vote[^"]*)"', td)
            if v:
                fn = v.group(1).rstrip("/").split("/")[-1]
                r[sec + "/vote"] = f"{BASE}/japanese/touhyoulist/{session}/{fn}"
    m = re.search(r'summary="議案要旨情報".*?<td[^>]*>(.*?)</td>', b, flags=re.S)
    if m:
        r["yoshi"] = clean(m.group(1))
    return r


def parse_vote(url):
    """本会議投票結果ページから、会派ごとの賛成・反対の票数を読む。"""
    b = fetch(url)
    anken = re.search(r'<dl class="ankenmei">\s*<dt>[^<]*</dt>\s*<dd>(.*?)</dd>', b, flags=re.S)
    tot = re.search(r"投票総数\s*(\d+)<br><span>賛成票\s*(\d+)\s*反対票\s*(\d+)", clean_keep_br(b))
    groups = []
    for name, seats, body in re.findall(r'<h4 class="party">(.*?)\(\s*(\d+)名\)</h4>\s*<dl class="sanpilist">(.*?)</dl>', b, flags=re.S):
        m = re.search(r"賛成票\s*(\d+)\s*反対票\s*(\d+)", clean(body))
        groups.append([clean(name), int(seats), int(m.group(1)), int(m.group(2))])
    d = re.search(r"(\d{4})年\s*(\d+)月\s*(\d+)日", b)
    return {
        "anken": clean(anken.group(1)) if anken else "",
        "total": list(tot.groups()) if tot else None,
        "date": "%s-%02d-%02d" % (d.group(1), int(d.group(2)), int(d.group(3))) if d else "",
        "groups": groups,
        "url": url,
    }


def clean_keep_br(s):
    s = re.sub(r"<(?!br|/?span)[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r　]+", " ", s)


def wdate(s):
    """「令和8年3月31日」→「2026-03-31」。"""
    m = re.match(r"令和\s*(\d+)年\s*(\d+)月\s*(\d+)日", s or "")
    if not m:
        return ""
    return "%d-%02d-%02d" % (2018 + int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_enacted(r):
    """両院で可決・承認されたか（＝成立・承認）。"""
    s = r.get("参議院本会議経過/議決", "")
    h = r.get("衆議院本会議経過/議決", "")
    return s in ("可決", "承認", "修正") and h in ("可決", "承認", "修正")
