"""組織のページの写真を、ウィキデータとウィキメディア・コモンズから機械で選ぶ（AIは使わない：ルール D-20）。

選び方（人は選ばない）：
1. その組織のウィキデータの項目（data/accounts_src.json の wikidata。ないときは、名前で探して公式サイトの住所が一致するもの）
2. 写真の候補の順番
   a. 「本部の所在地」（P159）が建物の項目なら、その建物の画像（P18）
   b. その組織の項目の画像（P18）
3. 次のものは使わない
   - 自由に使えるライセンスでないもの（使うのは パブリックドメイン・CC0・CC BY・CC BY-SA だけ）
   - ロゴ・紋章、人が写っている写真（ファイル名やコモンズの分類に logo・emblem・people・portrait・politician などがあるもの、
     ウィキデータに「描かれているもの」（P180）として人が登録されているもの）
4. 撮った人・ライセンス・コモンズのページを、写真の下に必ず書く。

書き出すもの：assets/img/org/<組織のid>.jpg（幅960ピクセルまで）、data/images.json
使い方：python pipeline/images.py
"""
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "images.json")
IMG = os.path.join(ROOT, "assets", "img", "org")
UA = {"User-Agent": "seiji-no-mado-bot/1.0 (https://github.com/kanameyamamoto/seiji-no-mado; images for organization pages)"}
WD = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
BUILDING = {"Q41176", "Q1021645", "Q16831714", "Q11303", "Q811979", "Q1497364", "Q1081138", "Q24354", "Q16560", "Q2977", "Q19844914", "Q12819564"}
FREE = re.compile(r"^(public domain|pd|cc0|cc[- ]by(-sa)?([- ]\d(\.\d)?)?)", re.I)
NG_WORDS = re.compile(r"logo|emblem|seal|flag|mark|symbol|portrait|people|person|politician|minister|meeting|conference|signing|ceremony|visit|press|会見|ロゴ|紋章|記章|首相|大臣", re.I)
WIDTH = 960


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def get(url, params, tries=5):
    u = url + "?" + urllib.parse.urlencode({**params, "format": "json"})
    for i in range(tries):
        try:
            time.sleep(1)
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i + 1 < tries:
                time.sleep(int(e.headers.get("Retry-After") or 30))
                continue
            raise


def entities(ids):
    out = {}
    ids = [x for x in dict.fromkeys(ids) if x]
    for i in range(0, len(ids), 50):
        out.update(get(WD, {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": "claims|labels|sitelinks", "languages": "ja"})["entities"])
    return out


def vals(e, p):
    return [c["mainsnak"]["datavalue"]["value"] for c in (e or {}).get("claims", {}).get(p, []) if c["mainsnak"].get("datavalue")]


def host(u):
    return urllib.parse.urlparse(u or "").netloc.lower().removeprefix("www.")


def search(name, url):
    """名前で探し、公式サイト（P856）の住所が一致する項目を返す。"""
    r = get(WD, {"action": "wbsearchentities", "search": name, "language": "ja", "type": "item", "limit": 10})
    ids = [x["id"] for x in r.get("search", [])]
    if not ids:
        return ""
    for q, e in entities(ids).items():
        if any(host(s) == host(url) for s in vals(e, "P856")):
            return q
    return ""


def file_info(title):
    r = get(COMMONS, {"action": "query", "titles": "File:" + title, "prop": "imageinfo|categories", "iiprop": "url|extmetadata|mime",
                      "iiurlwidth": WIDTH, "cllimit": "max"})
    page = next(iter(r["query"]["pages"].values()))
    if "imageinfo" not in page:
        return None
    ii = page["imageinfo"][0]
    md = {k: v.get("value", "") for k, v in ii.get("extmetadata", {}).items()}
    cats = " ".join(c["title"] for c in page.get("categories", []))
    return {"pageid": page.get("pageid"), "title": title, "thumb": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl"), "mime": ii.get("mime", ""),
            "license": re.sub(r"<[^>]+>", "", md.get("LicenseShortName", "")).strip(), "license_url": md.get("LicenseUrl", ""),
            "author": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", md.get("Artist", "")))).strip()[:120],
            "cats": cats + " " + md.get("Categories", ""), "desc": re.sub(r"<[^>]+>", "", md.get("ImageDescription", ""))[:300]}


def depicts_people(pageid):
    """コモンズの「描かれているもの」（P180）に、人（Q5）が登録されているか。"""
    if not pageid:
        return False
    try:
        r = get(COMMONS, {"action": "wbgetentities", "ids": f"M{pageid}"})
        st = next(iter(r.get("entities", {}).values()), {}).get("statements", {})
        dep = [c["mainsnak"]["datavalue"]["value"]["id"] for c in st.get("P180", []) if c["mainsnak"].get("datavalue")]
        if not dep:
            return False
        return any("Q5" in [v["id"] for v in vals(e, "P31")] for e in entities(dep).values())
    except Exception:  # noqa: BLE001
        return False


def ok(fi):
    if not fi or not fi["mime"].startswith("image/") or fi["mime"] == "image/svg+xml":
        return "画像でない"
    if not FREE.match(fi["license"]):
        return f"ライセンス（{fi['license']}）"
    if NG_WORDS.search(fi["title"]) or NG_WORDS.search(fi["cats"]):
        return "ロゴや人の写真の分類"
    if depicts_people(fi["pageid"]):
        return "人が写っている"
    return ""


def main():
    src = json.load(open(os.path.join(DATA, "accounts_src.json"), encoding="utf-8"))
    orgs = [x for g in src["groups"] for x in g["items"]]
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"orgs": {}}
    qid = {}
    for o in orgs:
        q = o.get("wikidata")
        if not q:
            try:
                q = search(o["name"], o["url"])
            except Exception as e:  # noqa: BLE001
                print(f"[images] {o['name']} をウィキデータで探せませんでした: {e}")
        if q:
            qid[o["id"]] = q
    E = entities(list(qid.values()))
    H = entities([v["id"] for q in qid.values() for v in vals(E.get(q), "P159")])
    os.makedirs(IMG, exist_ok=True)
    res, log = {}, []
    for o in orgs:
        q = qid.get(o["id"])
        if not q:
            log.append(f"{o['name']}：ウィキデータの項目が見つからない")
            continue
        cands = []
        for h in vals(E.get(q), "P159"):
            he = H.get(h["id"])
            if set(v["id"] for v in vals(he, "P31")) & BUILDING:
                cands += [("本部の建物", h["id"], f) for f in vals(he, "P18")]
        cands += [("組織", q, f) for f in vals(E.get(q), "P18")]
        chosen = None
        for kind, item, f in cands:
            try:
                fi = file_info(f)
            except Exception as e:  # noqa: BLE001
                log.append(f"{o['name']}：{f} を読めない（{e}）")
                continue
            why = ok(fi)
            if why:
                log.append(f"{o['name']}：{f} は使わない（{why}）")
                continue
            chosen = (kind, item, fi)
            break
        if not chosen:
            log.append(f"{o['name']}：使える写真がない")
            continue
        kind, item, fi = chosen
        path = os.path.join(IMG, o["id"] + ".jpg")
        prev = old.get("orgs", {}).get(o["id"], {})
        if prev.get("title") != fi["title"] or not os.path.exists(path):
            with urllib.request.urlopen(urllib.request.Request(fi["thumb"], headers=UA), timeout=120) as r:
                open(path, "wb").write(r.read())
            time.sleep(1)
        res[o["id"]] = {"file": f"assets/img/org/{o['id']}.jpg", "title": fi["title"], "page": fi["page"], "author": fi["author"] or "（コモンズのページを見てください）",
                        "license": fi["license"], "license_url": fi["license_url"], "what": kind, "wikidata": item}
        log.append(f"{o['name']}：{kind}の写真 {fi['title']}（{fi['license']}）")
    for f in os.listdir(IMG):
        if f.endswith(".jpg") and f[:-4] not in res:
            os.remove(os.path.join(IMG, f))
    out = {"criteria": "公式サイト・SNSの一覧にある国の機関の写真。ウィキデータで、その組織の本部の建物の画像、なければ組織の画像を、ウィキメディア・コモンズから使う。",
           "rule": "人は選ばない。自由に使えるライセンス（パブリックドメイン・CC0・CC BY・CC BY-SA）のものだけを使い、ロゴ・紋章や人が写っている写真は使わない。撮った人・ライセンス・元のページを写真の下に書く。",
           "checked": today(), "orgs": res}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n".join(log))
    print(f"[images] 写真あり {len(res)}／{len(orgs)}")


if __name__ == "__main__":
    main()
