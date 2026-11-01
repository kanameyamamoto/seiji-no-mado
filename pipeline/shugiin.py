"""衆議院の公開ページ（議案の一覧・議案審議経過情報・要綱）を読み取る。

- 外部ライブラリは使わない（標準ライブラリだけ）。衆議院のページの文字コードは Shift_JIS（cp932）。
- SHUGIIN_CACHE を指定すると、ネットに出ずにそのフォルダの保存済みHTMLを読む（テスト用）。
"""
import html
import os
import re
import time
import urllib.request

BASE = "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian"
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
CACHE = os.environ.get("SHUGIIN_CACHE")
CATS = ("閣法", "衆法", "参法")


def fetch(url, retries=3):
    if CACHE:
        p = os.path.join(CACHE, url.split("/gian/")[-1].replace("/", "_"))
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("cp932", errors="replace")
            if CACHE:
                os.makedirs(CACHE, exist_ok=True)
                open(os.path.join(CACHE, url.split("/gian/")[-1].replace("/", "_")), "w", encoding="utf-8").write(text)
            return text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("　", " ")
    return re.sub(r"[ \t\r]+", " ", s).strip()


def session_url(ses):
    return f"{BASE}/kaiji{ses}.htm"


def list_bills(ses):
    """その国会の議案の一覧から、法律案（閣法・衆法・参法）を返す。
    [{cat, session（提出回次）, no, title, status（審議状況の表記のまま）, keika, honbun}]"""
    b = fetch(session_url(ses))
    out = []
    for cap, body in re.findall(r"<caption[^>]*>(.*?)</caption>(.*?)</table>", b, flags=re.S | re.I):
        cat = clean(cap).replace("の一覧", "")
        if cat not in CATS:
            continue
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S | re.I):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)
            if len(tds) < 4:
                continue
            ke = re.search(r'href="\./keika/([^"]+)"', row, flags=re.I)
            hb = re.search(r'href="\./honbun/([^"]+)"', row, flags=re.I)
            out.append({"cat": cat, "session": clean(tds[0]), "no": clean(tds[1]), "title": clean(tds[2]), "status": clean(tds[3]),
                        "keika": f"{BASE}/keika/{ke.group(1)}" if ke else "", "honbun": f"{BASE}/honbun/{hb.group(1)}" if hb else ""})
    return out


def parse_keika(url):
    """議案審議経過情報を {項目: 内容} にする。「A／B」の項目は A と B に分ける。"""
    b = fetch(url)
    r = {}
    for th, td in re.findall(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", b, flags=re.S | re.I):
        k, v = clean(th), clean(td)
        if not k or k == "項目":
            continue
        if "／" in k:
            ks, vs = k.split("／"), [x.strip() for x in v.split("／")]
            for i, kk in enumerate(ks):
                r[kk.strip()] = vs[i] if i < len(vs) else ""
        else:
            r[k] = v
    return r


def groups(s):
    return [x.strip() for x in re.split(r"[;；]", s or "") if x.strip()]


def youkou_url(honbun):
    return honbun.replace("/honbun/", "/honbun/youkou/") if honbun else ""


def houan_url(honbun):
    return honbun.replace("/honbun/", "/honbun/houan/") if honbun else ""


def body_text(url):
    """要綱・法律案の本文ページから、本文だけを取り出す。"""
    b = fetch(url)
    m = re.search(r'<div[^>]*id="mainlayout"[^>]*>(.*)', b, flags=re.S | re.I)
    t = clean(m.group(1) if m else b)
    lines = [x.strip() for x in t.split("\n") if x.strip()]
    cut = [i for i, x in enumerate(lines) if x in ("ホームページについて", "Webアクセシビリティ")]
    if cut:
        lines = lines[:cut[0]]
    return "\n".join(lines)


def wdate(s):
    """「令和 8年 4月23日」→「2026-04-23」。"""
    m = re.match(r"令和\s*(\d+)年\s*(\d+)月\s*(\d+)日", s or "")
    return "%d-%02d-%02d" % (2018 + int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ""
