"""国の審議会等を、内閣人事局の「審議会総覧」から機械で整える（AIは使わない：ルールC-6・D-18）。

出どころ：内閣官房 内閣人事局「審議会総覧」（内閣府設置法第37条・第54条、国家行政組織法第8条の審議会等。2年ごと）
  一覧のページ … 府省ごとの審議会等の名前と、1つずつの PDF
  PDF … 主管省と庶務担当の部局課・ホームページ・根拠法令・設置年月日・所掌事務・分科会と部会・委員の定数と任期・委員の氏名・諮問と答申

整え方（人は選ばない）：
- 総覧に載っている審議会等を、すべて載せる。並びは総覧の順（府省の順 → その中の順）。
- 書いてあることは、総覧の表記のまま（行の折り返しだけつなぐ）。
- 電話番号と、委員の氏名は載せない（委員は定数・任期だけ）。氏名は総覧の PDF で見られるようにリンクする。
- 諮問・答申は、総覧に載っているもの（およそ直近2年分）をすべて、日付つきで載せる。和暦の日付（R6.9.9 など）は西暦に直した日付も添える。
- 一覧のページが変わったときだけ、PDF を読み直す。

書き出すもの：data/councils.json
使い方：python pipeline/councils.py   （PDF_CACHE=フォルダ で、保存した PDF を使う）
"""
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "councils.json")
BASE = "https://www.cas.go.jp/jp/gaiyou/jimu/jinjikyoku/"
LIST = BASE + "satei_01_04_03.html"
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
ERA = {"R": 2018, "H": 1988, "S": 1925}
ERA_K = {"令和": 2018, "平成": 1988, "昭和": 1925}
Z2H = str.maketrans("０１２３４５６７８９", "0123456789")


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def fetch(url):
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


# ---------- 一覧のページ ----------
def read_list(raw):
    t = raw.decode("cp932", errors="replace")
    edition = clean((re.search(r"審議会総覧（([^）]+現在)）", t) or [None, ""])[1])
    body = t[t.find("凡例"):]
    out, ministry, agency = [], "", ""
    for m in re.finditer(r"<a[^>]+href=\"(files/shingikaitou_(\d\d)_(\d\d)_(\d\d)\.pdf)\"[^>]*>(.*?)</a>|>([^<>]*?（[０-９\d]+）)<", body, flags=re.S):
        if m.group(6):
            head = clean(m.group(6))
            name = re.sub(r"（[０-９\d]+）$", "", head)
            if name.startswith("（"):
                agency = name.strip("（）")
            else:
                ministry, agency = name, ""
            continue
        path, a, b, c, label = m.group(1), m.group(2), m.group(3), m.group(4), clean(m.group(5))
        if a == "00":
            continue
        out.append({"id": f"c{a}{b}{c}", "name": re.sub(r"\(PDF\)$|（PDF）$", "", label).strip(), "ministry": ministry,
                    **({"agency": agency} if agency else {}), "pdf": BASE + path})
    return edition, out


# ---------- PDF ----------
def wareki(s):
    """「令和８年４月１日」→ 2026-04-01。読めなければ空。"""
    s = (s or "").translate(Z2H).replace(" ", "")
    m = re.search(r"(令和|平成|昭和)(元|\d+)年(\d+)月(\d+)日", s)
    if not m:
        return ""
    y = ERA_K[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
    return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def short_date(s):
    m = re.fullmatch(r"([RHS])(\d+)\.(\d+)\.(\d+)", s)
    if not m:
        return ""
    return f"{ERA[m.group(1)] + int(m.group(2)):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def join(lines):
    """行の折り返しをつなぐ（日本語なので、空白を入れずにつなぐ）。番号（１．・（１）・①）や「・」で始まる行は、新しい項目として改行を残す。"""
    out = ""
    for l in lines:
        l = l.strip()
        if not l:
            continue
        if out and re.match(r"^([０-９\d]+[．.]|[（(][０-９\d]+[）)]|[①-⑳]|・|＜)", l):
            out += "\n" + l
        elif out and re.search(r"[A-Za-z0-9/.:]$", out) and re.match(r"^[A-Za-z0-9]", l):
            out += " " + l
        else:
            out += l
    out = re.sub(r"[ 　]+", " ", out)
    out = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", out)  # 両端そろえで入った、日本語のあいだの空白
    return out.strip()


KEYS = [("office", "主管省及び庶務担当部局課"), ("_tel", "電話番号"), ("homepage", "ホームページ"), ("basis", "根拠法令"),
        ("established_text", "設置年月日"), ("duties", "所掌事務"), ("_sub", "分科会等"), ("_members", "委員＜定数＞"),
        ("_term", "＜任期＞"), ("_names", "＜氏名＞"), ("_qa", "諮問・答申事項等")]


def pdf_text(raw):
    p = subprocess.run(["pdftotext", "-layout", "-", "-"], input=raw, capture_output=True, check=True)
    return p.stdout.decode("utf-8", errors="replace")


def nospace(s):
    return re.sub(r"[ 　]", "", s)


def parse_pdf(text, ministry, name):
    lines = []
    for l in text.replace("\f", "\n").split("\n"):
        s = l.strip()
        if not s or re.fullmatch(r"[−\-－]\s*[\d０-９]+\s*[−\-－]", s) or nospace(s) in (ministry, nospace(name)):
            continue
        lines.append(s)
    marks = []
    for i, l in enumerate(lines):
        k = nospace(l)
        for key, word in KEYS:
            if key in [x[1] for x in marks]:
                continue
            j = k.find(word)
            if j == 0 or (key in ("_term", "_names") and j >= 0):
                # 見出しのうしろ（同じ行）の文字。見出しの文字のあいだの空白も飛ばす
                pat = r"^.*?" + r"[ 　]*".join(map(re.escape, word)) + r"[ 　]*"
                rest = re.sub(pat, "", l, count=1).strip()
                marks.append((i, key, rest))
                if key == "_term":  # 任期と氏名が同じ行のことがある
                    pass
                break
    marks.sort()
    sec = {}
    for j, (i, key, rest) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
        body = [rest] + lines[i + 1:end]
        if j + 1 < len(marks) and marks[j + 1][0] == i:
            body = [re.split(r"＜\s*(?:任\s*期|氏\s*名)\s*＞", rest)[0]]
        sec[key] = body
    it = {}
    if "office" in sec:
        it["office"] = join(sec["office"])
    if "homepage" in sec:
        urls = re.findall(r"https?://[^\s（）()、]+", "".join(sec["homepage"]))
        if urls:
            it["homepage"] = urls[0]
    for k in ("basis", "established_text", "duties"):
        if k in sec:
            it[k] = join(sec[k])
    if it.get("established_text"):
        it["established"] = wareki(it["established_text"])
    if "_sub" in sec:
        sub = join(sec["_sub"])
        if nospace(sub) not in ("", "なし", "＜分科会＞なし＜部会＞なし"):
            it["subcommittees"] = sub  # 分科会と部会は、書いてあるとおりの文で載せる（入れ子や説明の書き方が審議会ごとにちがうため）
    if "_members" in sec:
        mem = join(sec["_members"])
        it["seats"] = re.split(r"\n|うち常勤", mem)[0].strip()
        jo = re.search(r"うち常勤\s*([^\n＜]+)", mem)
        if jo:
            it["fulltime"] = jo.group(1).strip()
    if "_term" in sec:
        t = join(sec["_term"]).split("\n")[0].strip()
        if t:
            it["term"] = t
    if "_qa" in sec:
        it["qa"] = parse_qa(sec["_qa"])
    return it


def parse_qa(lines):
    body = join(lines)
    if nospace(body) in ("", "なし"):
        return []
    out = []
    for x in [x.strip() for x in re.split(r"(?:^|\n)・", body) if x.strip()]:
        z = x.translate(Z2H)
        q = {"text": x}
        for d, kind in re.findall(r"([RHS]\d+\.\d+\.\d+)\s*(諮問|答申|報告|建議|意見|決定|勧告|中間報告|中間答申|提言|附議)?", z):
            iso = short_date(d)
            if not iso:
                continue
            if kind == "諮問":
                q.setdefault("asked", iso)
            elif kind:
                q.setdefault("answered", iso)
                q.setdefault("answer_kind", kind)
            else:
                q.setdefault("date", iso)
        out.append(q)
    return out


def build(cache=None):
    raw = open(os.path.join(cache, "list.html"), "rb").read() if cache and os.path.exists(os.path.join(cache, "list.html")) else fetch(LIST)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    if old.get("hash") == digest and not os.environ.get("FORCE"):
        print("[councils] 審議会総覧の一覧は変わっていない")
        return False
    edition, items = read_list(raw)
    failed = []
    if len(items) < 50:
        print(f"[councils] 一覧の読み取りがおかしい（{len(items)}件）ので、前のままにします")
        return False
    for it in items:
        fn = it["pdf"].rsplit("/", 1)[1]
        p = os.path.join(cache, "pdf", fn) if cache else ""
        pdf = open(p, "rb").read() if p and os.path.exists(p) else (time.sleep(0.5) or fetch(it["pdf"]))
        try:
            d = parse_pdf(pdf_text(pdf), it["ministry"], it["name"])
            it.update(d)
        except Exception as e:  # noqa: BLE001
            print(f"[councils] 読めませんでした {it['name']}: {e}")
            failed.append(it["name"])
    if len(failed) > len(items) // 10:
        print(f"[councils] 読めない PDF が多い（{len(failed)}）ので、前のままにします")
        return False
    mins = []
    for it in items:
        if it["ministry"] not in mins:
            mins.append(it["ministry"])
    out = {"hash": digest, "edition": edition,
           "criteria": f"内閣人事局の「審議会総覧」（{edition}）に載っている国の審議会等（内閣府設置法第37条・第54条、国家行政組織法第8条の審議会等）を、すべて載せる（{len(items)}）。",
           "rule": "書いてあることは総覧の表記のまま（行の折り返しだけつなぐ）。電話番号と委員の氏名は載せない（氏名は総覧の PDF にある）。諮問・答申は総覧に載っているもの（およそ直近2年分）をすべて載せ、和暦の日付には西暦の日付を添える。並びは総覧の順。",
           "sources": [{"label": f"内閣官房 内閣人事局「審議会総覧」（{edition}）", "url": LIST}],
           "ministries": [{"name": m, "count": sum(1 for x in items if x["ministry"] == m)} for m in mins],
           "count": {"councils": len(items), "qa": sum(len(x.get("qa", [])) for x in items)},
           "checked": today(), "items": items}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[councils] {edition}：{len(items)}の審議会等・諮問と答申 {out['count']['qa']}件")
    return True


if __name__ == "__main__":
    build(os.environ.get("PDF_CACHE"))
