"""検索エンジン用のページを書き出す（公開のたびに作り直す）。

サイト本体（index.html）は1つのページの中で表示を切り替えるので、URLの「#」から後ろは
検索エンジンからは同じページに見える。そこで、記事・ページごとに、中身をそのまま書いた
HTMLを作り（中身はサイト本体と同じ。開くとそのページの表示で始まる）、サイトマップ（sitemap.xml）に載せる。

- 文章は「ふつう」のレベル（std）で書く。中身はサイト本体と同じデータから作るので、食い違わない。

使い方：python pipeline/prerender.py _site
"""
import datetime
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LEVEL = "std"
JST = datetime.timezone(datetime.timedelta(hours=9))


def load(name):
    return json.load(open(os.path.join(DATA, name + ".json"), encoding="utf-8"))


def t(x):
    """読むレベルのある文なら「ふつう」を、{{用語}} の印は外して返す。"""
    if isinstance(x, dict):
        x = x.get(LEVEL) or x.get("std") or next((v for v in x.values() if isinstance(v, str)), "")
    return re.sub(r"\{\{([^}|]+)(\|[^}]*)?\}\}", r"\1", str(x or ""))


def e(x):
    return html.escape(t(x))


def jdate(s):
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", s or "")
    if not m:
        return s or ""
    return f"{int(m.group(1))}年{int(m.group(2))}月" + (f"{int(m.group(3))}日" if m.group(3) else "")


def link(url, label):
    return f'<a href="{html.escape(url)}" rel="noopener">{html.escape(label or url)}</a>'


def sources_html(srcs):
    srcs = [s for s in (srcs or []) if s and s.get("url")]
    if not srcs:
        return ""
    return "<section><h2>出典</h2><ul class=\"src\">" + "".join(f"<li>{link(s['url'], s.get('label'))}</li>" for s in srcs) + "</ul></section>"


def hist_html(o):
    rows = [("訂正", r) for r in o.get("corrections", [])] + [("更新", r) for r in o.get("updates", [])]
    if not rows:
        return ""
    rows.sort(key=lambda x: x[1].get("date", ""), reverse=True)
    return "<section><h2>更新の記録</h2><ul>" + "".join(f"<li>{jdate(r['date'])} {k}：{e(r.get('text'))}</li>" for k, r in rows) + "</ul></section>"


def block(b):
    ty = b.get("type")
    title = f"<h2>{e(b['title'])}</h2>" if b.get("title") else ""
    if ty == "hook":
        return f'<p class="hook">{e(b.get("text"))}</p>'
    if ty == "p":
        return f"<p>{e(b.get('text'))}</p>"
    if ty in ("note",):
        return f'<p class="note">{e(b.get("text"))}</p>'
    if ty == "near":
        return f"<section>{title or '<h2>あなたの暮らしでは</h2>'}<p>{e(b.get('text'))}</p></section>"
    if ty == "list":
        return f"<section>{title}<ul>" + "".join(f"<li>{e(i)}</li>" for i in b.get("items", [])) + "</ul></section>"
    if ty == "steps":
        return "<section><ol>" + "".join(f"<li><b>{e(i.get('title'))}</b> {e(i.get('text'))}</li>" for i in b.get("items", [])) + "</ol></section>"
    if ty == "stats":
        return "<dl class=\"stats\">" + "".join(f"<div><dt>{e(i.get('label'))}</dt><dd>{e(i.get('value'))}{e(i.get('unit'))}</dd></div>" for i in b.get("items", [])) + "</dl>"
    if ty == "table":
        head = "".join(f"<th>{e(h)}</th>" for h in b.get("head", []))
        rows = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in b.get("rows", []))
        note = f'<p class="note">{e(b["note"])}</p>' if b.get("note") else ""
        return f"<section>{title}<div class=\"tw\"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>{note}</section>"
    if ty == "bars":
        u = e(b.get("unit"))
        rows = "".join(f"<tr><td>{e(i.get('label'))}</td><td class=\"n\">{i.get('value'):,}{u}</td></tr>" if isinstance(i.get("value"), (int, float))
                       else f"<tr><td>{e(i.get('label'))}</td><td class=\"n\">{e(i.get('value'))}{u}</td></tr>" for i in b.get("items", []))
        return f"<section>{title}<div class=\"tw\"><table><tbody>{rows}</tbody></table></div></section>"
    if ty == "views":
        items = "".join(f"<h3>{e(i.get('label'))}</h3><p>{e(i.get('text'))}</p>" for i in b.get("items", []))
        return f"<section>{title}{items}<p class=\"note\">どの考えが正しいかは、このサイトでは書きません（ルールP-2）。</p></section>"
    if ty == "links":
        return f"<section>{title}<ul>" + "".join(f"<li>{link(i.get('url', ''), i.get('label'))}{(' — ' + e(i['note'])) if i.get('note') else ''}</li>" for i in b.get("items", [])) + "</ul></section>"
    if ty == "diagram":
        parts = [title]
        for st in b.get("steps", []) or []:
            if isinstance(st, dict):
                parts.append(f"<p><b>{e(st.get('t') or st.get('title') or st.get('label'))}</b> {e(st.get('s') or st.get('text'))}"
                             + (f"（{e(st['tag'])}）" if st.get("tag") else "") + "</p>")
        if b.get("note"):
            parts.append(f'<p class="note">{e(b["note"])}</p>')
        return "<section>" + "".join(parts) + "</section>"
    return ""


class Site:
    """検索用のページ＝サイト本体（index.html）の写し。
    ・<base> で、データや画像の場所をサイトの一番上にそろえる
    ・サイト本体は住所（例：learn/what-is-politics/）からページを決めるので、開くとそのページが表示される
    ・本体が表示されるまでの間（と、検索エンジン）のために、中身の文章を #app に入れておく（本体が表示されると置きかわる）
    ・タイトル・説明・canonical などは、そのページのものにする
    """
    def __init__(self, out, base):
        self.out, self.base, self.urls = out, base.rstrip("/") + "/", []
        tpl = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        # 見た目（CSS）とプログラム（JS）は別のファイルにして、全ページで共有する（各ページは中身だけになり、軽くなる）
        import hashlib
        css = re.search(r"<style>\n?(.*?)</style>", tpl, flags=re.S)
        js = re.search(r"\n<script>\n(.*?)\n</script>\n", tpl, flags=re.S)
        assert css and js, "index.html の <style> と <script> が見つかりません"
        ver = hashlib.sha1((css.group(1) + js.group(1)).encode("utf-8")).hexdigest()[:10]
        os.makedirs(out, exist_ok=True)
        open(os.path.join(out, "app.css"), "w", encoding="utf-8").write(css.group(1))
        open(os.path.join(out, "app.js"), "w", encoding="utf-8").write(js.group(1))
        tpl = tpl.replace(css.group(0), f'<link rel="stylesheet" href="app.css?v={ver}">', 1)
        tpl = tpl.replace(js.group(0), f'\n<script src="app.js?v={ver}"></script>\n', 1)
        self.tpl = tpl
        open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(tpl)  # サイトの一番上のページも同じ形に
        for need in ('<base href="./">', "<title>せいじのまど</title>", '<div class="wrap" id="app"></div>'):
            assert need in self.tpl, f"index.html に {need!r} がありません"

    SECTION = {"a": "国会で決まったこと", "laws": "国会で決まったこと", "learn": "学ぶ", "links": "つながり", "party": "政党・選挙", "parties": "政党・選挙",
               "member": "国会議員", "members": "国会議員", "governors": "知事", "elections": "選挙の結果", "media": "メディア",
               "about": "このサイトについて", "terms": "ことばの一覧", "match": "投票記録とくらべる", "search": "検索", "api": "データを使う（公開API）"}
    SECTION_PATH = {"a": "laws/", "party": "parties/", "member": "members/"}

    def ld(self, path, title, desc, lastmod, extra):
        """構造化データ（schema.org の JSON-LD）。どのページかを、検索エンジンや AI が読める形で書く。"""
        org = {"@type": "Organization", "name": "せいじのまど", "url": self.base}
        first = path.split("/")[0]
        crumbs = [{"@type": "ListItem", "position": 1, "name": "せいじのまど", "item": self.base}]
        sec_path = self.SECTION_PATH.get(first, first + "/")
        if sec_path != path:
            crumbs.append({"@type": "ListItem", "position": 2, "name": self.SECTION.get(first, first), "item": self.base + sec_path})
        crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": title, "item": self.base + path})
        main = {"@type": "WebPage", "name": title, "description": desc, "url": self.base + path, "inLanguage": "ja", "publisher": org}
        if lastmod and re.match(r"\d{4}-\d{2}-\d{2}", lastmod):
            main["dateModified"] = lastmod[:10]
        if extra:
            main.update(extra)
            if extra.get("@type") in ("Article", "LearningResource"):
                main.setdefault("headline", title)
                main["publisher"] = org
                main["author"] = org
        main["isAccessibleForFree"] = True
        main["license"] = "https://creativecommons.org/licenses/by/4.0/"
        return {"@context": "https://schema.org", "@graph": [main, {"@type": "BreadcrumbList", "itemListElement": crumbs}]}

    def page(self, path, title, desc, body, app_route, lastmod="", ld=None):
        up = "../" * path.count("/")
        url = self.base + path
        desc = re.sub(r"\s+", " ", t(desc))[:120]
        full = f"{title}｜せいじのまど"
        h = self.tpl
        h = h.replace('<base href="./">', f'<base href="{up}">', 1)
        h = h.replace("<title>せいじのまど</title>", f"<title>{html.escape(full)}</title>", 1)
        h = re.sub(r'<meta name="description" content="[^"]*">', lambda m: f'<meta name="description" content="{html.escape(desc)}">', h, count=1)
        h = re.sub(r'<meta property="og:description" content="[^"]*">', lambda m: f'<meta property="og:description" content="{html.escape(desc)}">', h, count=1)
        h = h.replace('<meta property="og:title" content="せいじのまど">', f'<meta property="og:title" content="{html.escape(full)}">', 1)
        h = h.replace('<meta property="og:type" content="website">', '<meta property="og:type" content="article">', 1)
        h = re.sub(r'<meta property="og:url" content="[^"]*">', f'<meta property="og:url" content="{url}">', h, count=1)
        h = re.sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{url}">', h, count=1)
        h = h.replace('<div class="wrap" id="app"></div>', f'<div class="wrap" id="app"><article class="seo-static">{body}</article></div>', 1)
        jl = json.dumps(self.ld(path, title, desc, lastmod or "", ld), ensure_ascii=False).replace("</", "<\\/")
        h = h.replace("</head>", f'<script type="application/ld+json">{jl}</script>\n</head>', 1)
        d = os.path.join(self.out, path)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(h)
        self.urls.append((url, lastmod, title, path))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "_site"
    cfg = load("config")
    site = Site(out, cfg.get("site_url", "https://kanameyamamoto.github.io/seiji-no-mado/"))
    feed, learn, links, parties, media = load("feed"), load("learn"), load("links"), load("parties"), load("media")

    # 国会で決まったこと
    for it in feed["items"]:
        meta = [it.get("kind", ""), jdate(it.get("date")), it.get("session", ""), it.get("number", ""), ("提出 " + it["submitter"]) if it.get("submitter") else "",
                it.get("law_no", ""), (jdate(it["promulgated"]) + " 公布") if it.get("promulgated") else ""]
        body = f"<h1>{e(it['title'])}</h1><div class=\"meta\">" + "".join(f"<span>{html.escape(m)}</span>" for m in meta if m) + "</div>"
        if it.get("formal") and it["formal"] != it["title"]:
            body += f"<p class=\"note\">正式名：{e(it['formal'])}</p>"
        body += f"<p class=\"lead\">{e(it.get('lead'))}</p>"
        if it.get("facts"):
            body += "<h2>主な内容</h2><ul>" + "".join(f"<li>{e(f.get('text'))}</li>" for f in it["facts"]) + "</ul>"
        v = it.get("votes") or {}
        if v.get("detail"):
            body += (f"<h2>会派ごとの賛否（{e(v.get('chamber'))} {jdate(v.get('date'))}）</h2>"
                     + (f"<p>賛成 {v['totals']['for']}票・反対 {v['totals']['against']}票</p>" if v.get("totals") else "")
                     + "<div class=\"tw\"><table><thead><tr><th>会派</th><th>所属</th><th>賛成</th><th>反対</th></tr></thead><tbody>"
                     + "".join(f"<tr><td>{e(d['group'])}</td><td class=\"n\">{d.get('seats', '')}</td><td class=\"n\">{d.get('for', '')}</td><td class=\"n\">{d.get('against', '')}</td></tr>" for d in v["detail"])
                     + "</tbody></table></div>")
        srcs = [s for f in it.get("facts", []) for s in f.get("sources", [])] + [x.get("source") for x in (it.get("process"), v) if x and x.get("source")]
        seen, uniq = set(), []
        for s in srcs:
            if s and s.get("url") not in seen:
                seen.add(s.get("url"))
                uniq.append(s)
        body += sources_html(uniq) + hist_html(it)
        site.page(f"a/{it['id']}/", it["title"], it.get("lead"), body, "a-" + it["id"], it.get("checked") or it.get("date"),
                  {"@type": "Article", "datePublished": it.get("date"), "articleSection": it.get("kind"),
                   "about": {"@type": "Legislation", "name": it.get("formal") or it.get("title"),
                             **({"legislationIdentifier": it["law_no"]} if it.get("law_no") else {}),
                             **({"legislationDate": it["promulgated"]} if it.get("promulgated") else {}),
                             "legislationJurisdiction": "日本", "legislationPassedBy": {"@type": "Organization", "name": "国会"}},
                   "isBasedOn": [s["url"] for s in uniq]})

    # 学ぶ・つながり
    for name, d in (("learn", learn), ("links", links)):
        for s in d["sections"]:
            for p in s["pages"]:
                body = (f"<div class=\"meta\"><span>{'学ぶ' if name == 'learn' else 'つながり'}・{html.escape(s['name'])}</span>"
                        + (f"<span>確認日 {jdate(p['checked'])}</span>" if p.get("checked") else "") + "</div>"
                        + f"<h1>{e(p['title'])}</h1>")
                hooks = [b for b in p.get("blocks", []) if b.get("type") == "hook"]
                body += "".join(block(b) for b in hooks)
                if p.get("summary"):
                    body += f"<p class=\"lead\">{e(p['summary'])}</p>"
                body += "".join(block(b) for b in p.get("blocks", []) if b.get("type") != "hook")
                body += sources_html(p.get("sources")) + hist_html(p)
                site.page(f"{name}/{p['id']}/", p["title"], p.get("summary") or p["title"], body, f"pg-{name}-{p['id']}", p.get("checked"),
                          {"@type": "LearningResource" if name == "learn" else "Article", "articleSection": s["name"],
                           "isBasedOn": [x["url"] for x in (p.get("sources") or []) if x.get("url")][:30]})

    # 政党
    def party_page(p, key, route):
        pr = p.get("profile") or {}
        m = p.get("members") or {}
        body = f"<h1>{e(p['name'])}</h1>"
        meta = [("結成 " + p["founded"]) if p.get("founded") else "", p.get("leader", ""), p.get("kind", "")]
        body += "<div class=\"meta\">" + "".join(f"<span>{html.escape(x)}</span>" for x in meta if x) + "</div>"
        if pr.get("hook"):
            body += f"<p class=\"hook\">{e(pr['hook'])}</p>"
        body += f"<p class=\"lead\">{e(pr.get('catch') or p.get('summary') or p.get('detail'))}</p>"
        if m:
            body += ("<dl class=\"stats\">" + (f"<div><dt>衆議院の所属議員（{jdate(m.get('as_of'))}）</dt><dd>{m.get('shu')}人</dd></div>" if m.get("shu") not in (None, "") else "")
                     + (f"<div><dt>参議院の所属議員（{jdate(m.get('as_of_san'))}）</dt><dd>{m.get('san')}人</dd></div>" if m.get("san") not in (None, "") else "") + "</dl>")
        if pr.get("history"):
            body += "<h2>成り立ちと歩み</h2><ul>" + "".join(f"<li>{jdate(h.get('date'))}　{e(h.get('text'))}</li>" for h in pr["history"]) + "</ul>"
        ideas = pr.get("ideas")
        if ideas:
            body += ("<h2>掲げる考え方</h2>" + (f"<blockquote>「{e(ideas.get('quote'))}」</blockquote>" if isinstance(ideas, dict) and ideas.get("quote") else "")
                     + f"<p>{e(ideas.get('summary') or ideas.get('text') if isinstance(ideas, dict) else ideas)}</p>"
                     + (sources_html([ideas['source']]).replace('<h2>出典</h2>', '') if isinstance(ideas, dict) and ideas.get('source') else ""))
        if pr.get("policies"):
            body += "<h2>主な政策</h2><ul>" + "".join(f"<li><b>{e(x.get('title'))}</b> {e(x.get('text'))}</li>" for x in pr["policies"]) + "</ul>"
        if pr.get("leader_rule"):
            body += f"<h2>代表の決め方</h2><p>{e(pr['leader_rule'])}</p>"
        if pr.get("near"):
            body += f"<h2>あなたの暮らしでは</h2><p>{e(pr['near'])}</p>"
        body += sources_html(pr.get("sources")) + hist_html(p)
        site.page(f"party/{key}/", p["name"], pr.get("catch") or p.get("summary") or p.get("detail") or p["name"], body, route, p.get("checked") or parties.get("checked"),
                  {"@type": "ProfilePage", "mainEntity": {"@type": "PoliticalParty", "name": p["name"], **({"url": p["url"]} if p.get("url") else {}),
                                                         **({"foundingDate": p["founded"]} if p.get("founded") else {})}})
    for p in parties["national"]:
        party_page(p, p["id"], "p-" + p["id"])
    for i, p in enumerate(parties["others"]):
        party_page(p, f"o{i}", f"p-o{i}")

    # 国会議員・知事
    M, G, MV = load("members"), load("governors"), load("member_votes")
    pname = {p["id"]: p["name"] for p in parties["national"]}
    fe = {it["id"]: it for it in feed["items"]}
    for m in M["members"]:
        meta = [m["yomi"], m["house"] + "議員", f"会派 {m['kaiha']}", m["district"], ((lambda mm: f"当選{mm.group(1)}回" + (f"（参議院で{mm.group(2)}回）" if mm.group(2) else ""))(re.match(r"(\d+)(?:[（(]参(\d+)[）)])?", m["terms"])) if m.get("terms") else ""),
                (f"任期 {jdate(m['term_end'])}まで" if m.get("term_end") else "")]
        body = f"<h1>{html.escape(m['name'])}</h1><div class=\"meta\">" + "".join(f"<span>{html.escape(x)}</span>" for x in meta if x) + "</div>"
        if m.get("parties"):
            body += "<p>" + ("この会派を組む党：" if len(m["parties"]) > 1 else "政党：") + "・".join(f'<a href="party/{pid}/">{html.escape(pname.get(pid, pid))}</a>' for pid in m["parties"]) + "</p>"
        if m["house"] == "参議院":
            sv = MV.get("votes", {}).get(m["id"], "")
            rows = [(fe[i], sv[k] if k < len(sv) else "-") for k, i in enumerate(MV.get("items", [])) if i in fe]
            lab = {"Y": "賛成", "N": "反対", "-": "記録なし"}
            if any(v != "-" for _, v in rows):
                body += ("<h2>国会での賛否（参議院の本会議）</h2><ul>" +
                         "".join(f'<li>{lab[v]}：<a href="a/{it["id"]}/">{html.escape(it["title"])}</a>（{jdate(it["date"])}）</li>' for it, v in rows) + "</ul>")
        srcs = ([{"label": "参議院 議員のページ", "url": m["url"]}] if m.get("url") else []) + [(M["shu"] if m["house"] == "衆議院" else M["san"])["source"]]
        body += sources_html(srcs)
        site.page(f"member/{m['id']}/", m["name"], f"{m['house']}議員（{m['kaiha']}・{m['district']}）。会派・選挙区・当選回数" + ("と、参議院の本会議での賛否。" if m["house"] == "参議院" else "。"),
                  body, "m-" + m["id"], M.get("checked"),
                  {"@type": "ProfilePage", "mainEntity": {"@type": "Person", "name": m["name"], "alternateName": m["yomi"], "jobTitle": m["house"] + "議員",
                                                         "memberOf": [{"@type": "Organization", "name": m["kaiha"]}] + [{"@type": "PoliticalParty", "name": pname.get(x, x)} for x in m.get("parties", [])],
                                                         **({"sameAs": [m["url"]]} if m.get("url") else {})}})

    # 選挙の結果
    E = parties["elections"]
    body = "<h1>選挙の結果</h1>"
    for r in E.get("recent", []):
        body += (f"<h2>{e(r['name'])}（{jdate(r['date'])}）</h2><p>{e(r.get('seats_label', ''))} {r.get('seats_total', '')}"
                 + (f"、投票率 {r['turnout']}%" if r.get("turnout") is not None else "") + "</p><div class=\"tw\"><table><tbody>"
                 + "".join(f"<tr><td>{e(x['party'])}</td><td class=\"n\">{x['seats']}議席</td></tr>" for x in r.get("results", [])) + "</tbody></table></div>"
                 + (f"<p class=\"note\">{e(r['note'])}</p>" if r.get("note") else "") + sources_html(r.get("sources")))
    body += hist_html(E)
    site.page("elections/", "選挙の結果", "最近の衆議院選挙・参議院選挙の結果（政党ごとの議席数・投票率）。", body, "elections", E.get("checked") or parties.get("checked"))

    # メディア（一覧はタブのページに、解説ページは media/<id>/ に）
    body = "<h1>メディアの一覧</h1>"
    for sec in media["sections"]:
        body += f"<h2>{e(sec['name'])}</h2>" + (f"<p>{e(sec['lead'])}</p>" if sec.get("lead") else "")
        if sec.get("directory"):
            body += "<ul>" + "".join(f"<li>{link(x.get('url', ''), x.get('name'))}{(' — ' + e(x['sub'])) if x.get('sub') else ''}</li>" for x in sec["directory"]) + "</ul>"
        for p in sec.get("pages", []):
            body += f'<p><a href="media/{p["id"]}/">{e(p["title"])}</a></p>'
            pb = (f"<div class=\"meta\"><span>メディア・{html.escape(sec['name'])}</span></div><h1>{e(p['title'])}</h1>"
                  + (f"<p class=\"lead\">{e(p['summary'])}</p>" if p.get("summary") else "") + "".join(block(b) for b in p.get("blocks", []))
                  + sources_html(p.get("sources")) + hist_html(p))
            site.page(f"media/{p['id']}/", p["title"], p.get("summary") or p["title"], pb, f"pg-media-{p['id']}", p.get("checked") or media.get("checked"))
    tabs = []
    tabs.append(("media/", "メディアの一覧", "新聞・テレビ・ラジオ・ネットメディアなど、日本の主なメディアの一覧。", body, "media", media.get("checked")))

    def lst(items):
        return "<ul>" + "".join(f'<li><a href="{u}">{html.escape(ti)}</a></li>' for u, ti in items) + "</ul>"

    # タブのページ（それぞれの一覧。検索エンジンがすべてのページをたどれるように）
    body = f"<h1>国会で決まったこと</h1><p>{e(feed.get('criteria'))}</p>" + lst([(f"a/{it['id']}/", f"{it['title']}（{jdate(it['date'])} {it['kind']}）") for it in feed["items"]])
    tabs.append(("laws/", "国会で決まったこと", feed.get("criteria", ""), body, "laws", feed.get("checked")))
    body = "<h1>学ぶ</h1>" + "".join(f"<h2>{e(sec['name'])}</h2>" + lst([(f"learn/{p['id']}/", p["title"]) for p in sec["pages"]]) for sec in learn["sections"])
    tabs.append(("learn/", "学ぶ", "政治のしくみを、コースで順番に学べるページの一覧。", body, "learn", learn.get("checked")))
    body = "<h1>つながり</h1>" + "".join(f"<h2>{e(sec['name'])}</h2>" + lst([(f"links/{p['id']}/", p["title"]) for p in sec["pages"]]) for sec in links["sections"])
    tabs.append(("links/", "つながり", "予算・省庁・政治資金など、政治と暮らしのつながりを数字とグラフで見るページの一覧。", body, "links", links.get("checked")))
    body = ("<h1>政党</h1><h2>国政政党</h2>" + lst([(f"party/{p['id']}/", p["name"]) for p in parties["national"]])
            + "<h2>そのほかの政党・政治団体</h2>" + lst([(f"party/o{i}/", p["name"]) for i, p in enumerate(parties["others"])])
            + '<p><a href="elections/">選挙の結果</a>・<a href="laws/">国会で決まったこと</a></p>')
    tabs.append(("parties/", "政党・選挙", "国政政党の成り立ち・考え方・主な政策・国会での賛否を、すべての党で同じ項目で紹介。", body, "parties", parties.get("checked")))
    rules = load("rules")
    body = "<h1>このサイトについて</h1><p>どの党にもメディアにも偏らないよう、事実と意見を分け、結論は書きません。書き方のルールは、すべて公開しています。</p>"
    for g in rules.get("groups", []):
        body += f"<h2>{e(g['name'])}</h2><ul>" + "".join(f"<li><b>{e(r['id'])}</b> {e(r['text'])}</li>" for r in g["rules"]) + "</ul>"
    tabs.append(("about/", "このサイトについて", "せいじのまどの書き方のルール（事実と意見を分ける・結論は書かない など）の一覧。", body, "about", rules.get("updated")))
    gl = load("glossary")
    body = "<h1>ことばの一覧</h1><dl>" + "".join(f"<dt>{html.escape(k)}</dt><dd>{e(v.get('text') if isinstance(v, dict) and 'text' in v else v)}</dd>" for k, v in sorted(gl.items())) + "</dl>"
    tabs.append(("terms/", "ことばの一覧", "政治のニュースに出てくることばと、過去の政党の説明の一覧。", body, "terms", ""))
    tabs.append(("search/", "検索", "せいじのまどの中を検索する。", "<h1>検索</h1>", "search", ""))
    tabs.append(("api/", "データを使う（公開API）", "せいじのまどのデータ（国会で成立した法律・政党・国会議員・知事・政治のしくみの解説）を、登録なし・無料で使える公開API（JSON）。CC BY 4.0。",
                 "<h1>データを使う（公開API）</h1><p>このサイトのデータは、登録やキーなしで、無料で使えます。一覧は <a href=\"api/v1/index.json\">api/v1/index.json</a>。ライセンスは CC BY 4.0。</p>", "api", ""))
    body = "<h1>国会議員</h1>" + "".join(
        f"<h2>{hs}（{sum(1 for m in M['members'] if m['house'] == hs)}人）</h2>" +
        lst([(f"member/{m['id']}/", f"{m['name']}（{m['kaiha']}・{m['district']}）") for m in M["members"] if m["house"] == hs]) for hs in ("衆議院", "参議院"))
    tabs.append(("members/", "国会議員", "衆議院・参議院のすべての国会議員の一覧（会派・選挙区・当選回数）。参議院議員は本会議での賛否も。", body, "members", M.get("checked")))
    body = (f"<h1>知事</h1><p>{jdate(G.get('as_of'))}現在。</p><ul>" +
            "".join(f"<li>{g['pref']}：{html.escape(g['name'])}（{g['yomi']}）・{g['terms']}期目・任期 {jdate(g['term_end'])}まで</li>" for g in G["governors"]) +
            "</ul>" + sources_html([G["source"]]))
    tabs.append(("governors/", "知事", "47都道府県の知事の一覧（期数・初めての就任日・任期）。", body, "governors", G.get("checked")))
    rj = load("rejected")
    rj_ok = lambda it: (it.get("votes") or {}).get("chamber") == "参議院" and any(g != "各派に属しない議員" for g in it["votes"].get("for", [])) and any(g != "各派に属しない議員" for g in it["votes"].get("against", []))
    mitems = sorted([it for it in feed["items"] + rj.get("items", []) if rj_ok(it)], key=lambda it: it["date"], reverse=True)
    body = ("<h1>投票記録とくらべる</h1><p>参議院の本会議で会派の賛否が分かれた法律・予算・条約と、否決された法律案のすべてについて、あなたなら賛成か反対かを答えると、"
            "各会派が実際にどちらに投票したかとくらべて、同じ側だった本数を数えます。公約や発言から賛否を推し量ることはせず、近い党を判定したり、投票先をすすめたりはしません。"
            "答えは使う人の端末の中だけに保存します。</p>"
            f"<h2>くらべる法律など（{len(mitems)}本）</h2><ul>" +
            "".join((f'<li><a href="a/{it["id"]}/">{html.escape(it["title"])}</a>（{jdate(it["date"])} 成立）</li>' if it.get("kind") != "法律案が否決"
                     else f'<li>{html.escape(it["title"])}（{jdate(it["date"])} 参議院で否決・{html.escape(it.get("number", ""))}）</li>') for it in mitems) + "</ul>")
    tabs.append(("match/", "投票記録とくらべる", "参議院で賛否が分かれた法律に、自分なら賛成か反対かを答えて、各会派の実際の投票とくらべる。人が法律を選ばず、判定もしない。", body, "match", feed.get("checked")))
    for args in tabs:
        site.page(*args)

    # サイトマップと robots.txt
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
           f"<url><loc>{site.base}</loc><lastmod>{today}</lastmod></url>"]
    for u, lm, _, _ in site.urls:
        xml.append(f"<url><loc>{html.escape(u)}</loc>" + (f"<lastmod>{lm[:10]}</lastmod>" if re.match(r"\d{4}-\d{2}-\d{2}", lm or "") else "") + "</url>")
    xml.append("</urlset>")
    open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write("\n".join(xml) + "\n")
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(f"User-agent: *\nAllow: /\nSitemap: {site.base}sitemap.xml\n")
    # どの住所にもないとき（GitHub Pages が 404.html を返す）：本体の写しで、住所からページを決めて表示する
    root = re.sub(r"^https?://[^/]+", "", site.base)
    nf = site.tpl.replace('<base href="./">', f'<base href="{root}">', 1).replace("<head>", '<head>\n<meta name="robots" content="noindex">', 1)
    open(os.path.join(out, "404.html"), "w", encoding="utf-8").write(nf)
    print(f"検索用のページ {len(site.urls)} 件と sitemap.xml を書き出しました")


if __name__ == "__main__":
    main()
