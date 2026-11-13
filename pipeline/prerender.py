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
               "about": "このサイトについて", "terms": "ことばの一覧", "match": "投票記録とくらべる", "bills": "審議中・採決されなかった法律案", "accounts": "公式サイト・SNSの一覧", "questions": "質問主意書と答弁書", "budget": "国の予算事業と支出先", "pubcom": "パブリックコメント（意見募集）", "kakugi": "閣議の案件", "councils": "国の審議会", "org": "公式サイト・SNSの一覧", "diary": "国会と政府のうごき", "search": "検索", "api": "データを使う（公開API）", "support": "運営を支える（募金）"}
    SECTION_PATH = {"a": "laws/", "party": "parties/", "member": "members/", "org": "accounts/"}

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
    site = Site(out, cfg.get("site_url", "https://seijinomado.com/"))
    feed, learn, links, parties, media = load("feed"), load("learn"), load("links"), load("parties"), load("media")
    acc = load("accounts")
    acc_order = acc.get("order", [])
    acc_rows = [x for g in acc.get("groups", []) for x in g.get("items", [])]

    def sns_html(x, with_site=True):
        """公式サイト・SNS（ルール D-11）。"""
        if not x:
            return ""
        items = []
        if with_site and x.get("url"):
            items.append(f"<li>公式サイト：{link(x['url'], x['url'])}</li>")
        for k in acc_order:
            lst_ = (x.get("sns") or {}).get(k) or []
            if lst_:
                n = (x.get("counts") or {}).get(k, len(lst_))
                items.append(f"<li>{e(k)}：" + "、".join(link(y["url"], y["id"]) for y in lst_[:10]) + (f" ほか{n - 10}件" if n > 10 else "") + "</li>")
        if len(items) == (1 if with_site and x.get("url") else 0):
            items.append("<li>公式サイトのトップページとSNSの一覧ページに、SNSへのリンクは見つかりませんでした。</li>")
        return "<h2>公式サイト・SNS</h2><ul>" + "".join(items) + "</ul>"

    # 国会で決まったこと
    for it in feed["items"]:
        meta = [it.get("kind", ""), jdate(it.get("date")), it.get("session", ""), it.get("number", ""), ("提出 " + it["submitter"] + (f"（{'、'.join(it['submitter_groups'])}）" if it.get("submitter_groups") else "")) if it.get("submitter") else "",
                it.get("law_no", ""), (jdate(it["promulgated"]) + " 公布") if it.get("promulgated") else ""]
        body = f"<h1>{e(it['title'])}</h1><div class=\"meta\">" + "".join(f"<span>{html.escape(m)}</span>" for m in meta if m) + "</div>"
        if it.get("formal") and it["formal"] != it["title"]:
            body += f"<p class=\"note\">正式名：{e(it['formal'])}</p>"
        rejected = it.get("kind") == "法律案が否決"
        if rejected:
            where = "衆議院" if (it.get("process") or {}).get("shugiin", {}).get("result") == "否決" else "参議院"
            body += f"<p><b>この法律案は{where}で否決され、成立していません。</b></p>"
        body += f"<p class=\"lead\">{e(it.get('lead'))}</p>"
        if it.get("facts"):
            body += "<h2>主な内容</h2><ul>" + "".join(f"<li>{e(f.get('text'))}</li>" for f in it["facts"]) + "</ul>"
        mo = it.get("more") or {}
        if mo.get("reason"):
            body += f"<h2>なぜ出されたか</h2><p>{e(mo['reason']['text'])}</p>"
        if mo.get("who"):
            body += f"<h2>だれに関係するか</h2><p>{e(mo['who']['text'])}</p>"
        if mo.get("committee") or mo.get("views"):
            body += "<h2>国会での議論</h2>"
            if mo.get("committee"):
                body += f"<p>{e(mo['committee']['text'])}</p>"
            if mo.get("views"):
                body += "<ul>" + "".join(f"<li>【{e(x['side'])}】{e(x['group'])}：{e(x['text'])}</li>" for x in mo["views"]) + "</ul>"
        v = it.get("votes") or {}
        if v.get("detail"):
            body += (f"<h2>会派ごとの賛否（{e(v.get('chamber'))} {jdate(v.get('date'))}）</h2>"
                     + (f"<p>賛成 {v['totals']['for']}票・反対 {v['totals']['against']}票</p>" if v.get("totals") else "")
                     + "<div class=\"tw\"><table><thead><tr><th>会派</th><th>所属</th><th>賛成</th><th>反対</th></tr></thead><tbody>"
                     + "".join(f"<tr><td>{e(d['group'])}</td><td class=\"n\">{d.get('seats', '')}</td><td class=\"n\">{d.get('for', '')}</td><td class=\"n\">{d.get('against', '')}</td></tr>" for d in v["detail"])
                     + "</tbody></table></div>")
        elif v.get("for") or v.get("against"):
            body += (f"<h2>会派ごとの賛否（{e(v.get('chamber'))} {jdate(v.get('date'))}）</h2>"
                     f"<p>賛成：{e('、'.join(v.get('for', [])))}</p><p>反対：{e('、'.join(v.get('against', [])))}</p>")
        srcs = [s for f in it.get("facts", []) for s in f.get("sources", [])] + [x.get("source") for x in (it.get("process"), v) if x and x.get("source")]
        srcs += [s for k in ("reason", "who", "committee") for s in ((mo.get(k) or {}).get("sources") or [])] + [s for x in (mo.get("views") or []) for s in x.get("sources", [])]
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
                             "legislationJurisdiction": "日本",
                             **({} if rejected else {"legislationPassedBy": {"@type": "Organization", "name": "国会"}})},
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
                if name == "links":
                    body += sns_html(next((x for x in acc_rows if x.get("page") == p["id"]), None))
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
        body += sns_html(next((x for x in acc_rows if x.get("party") == key), None))
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
    qbym = {}
    for q in load("questions").get("items", []):
        if q.get("member"):
            qbym.setdefault(q["member"], []).append(q)
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
        mq = qbym.get(m["id"], [])
        if mq:
            body += f"<h2>出した質問主意書（{len(mq)}件）</h2><ul>" + "".join(
                f"<li>{e(q['session'])} {q['number']}号 {link(q['question_url'], q['title']) if q.get('question_url') else html.escape(q['title'])}（{e(q['status'])}）</li>" for q in mq) + "</ul>"
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
    tabs.append(("support/", "運営を支える（募金）", "せいじのまどは広告なし・登録なし・無料。運営のお金は募金で支えている。募金した人が中身に関わることはなく、政党・政治団体・候補者からは受け取らない（ルール S-4）。",
                 "<h1>運営を支える（募金）</h1><p>せいじのまどは、広告をのせず、だれでも無料で使えるようにしています。運営のお金は募金で支えています。募金した人や団体が中身・並び順に関わることはなく、政党・政治団体・候補者からは受け取りません。</p>", "support", ""))
    body = "<h1>国会議員</h1>" + "".join(
        f"<h2>{hs}（{sum(1 for m in M['members'] if m['house'] == hs)}人）</h2>" +
        lst([(f"member/{m['id']}/", f"{m['name']}（{m['kaiha']}・{m['district']}）") for m in M["members"] if m["house"] == hs]) for hs in ("衆議院", "参議院"))
    tabs.append(("members/", "国会議員", "衆議院・参議院のすべての国会議員の一覧（会派・選挙区・当選回数）。参議院議員は本会議での賛否も。", body, "members", M.get("checked")))
    body = (f"<h1>知事</h1><p>{jdate(G.get('as_of'))}現在。</p><ul>" +
            "".join(f"<li>{g['pref']}：{html.escape(g['name'])}（{g['yomi']}）・{g['terms']}期目・任期 {jdate(g['term_end'])}まで</li>" for g in G["governors"]) +
            "</ul>" + sources_html([G["source"]]))
    tabs.append(("governors/", "知事", "47都道府県の知事の一覧（期数・初めての就任日・任期）。", body, "governors", G.get("checked")))
    rj_ok = lambda it: (it.get("votes") or {}).get("chamber") == "参議院" and any(g != "各派に属しない議員" for g in it["votes"].get("for", []) + it["votes"].get("against", []))
    mitems = [it for it in feed["items"] if rj_ok(it)]
    body = ("<h1>投票記録とくらべる</h1><p>参議院の本会議で押しボタン式で採決された法律・予算・条約と、否決された法律案のすべてについて、あなたなら賛成か反対かを答えると、"
            "各会派が実際にどちらに投票したかとくらべて、同じ側だった本数を数えます。公約や発言から賛否を推し量ることはせず、近い党を判定したり、投票先をすすめたりはしません。"
            "答えは使う人の端末の中だけに保存します。</p>"
            f"<h2>くらべる法律など（{len(mitems)}本）</h2><ul>" +
            "".join(f'<li><a href="a/{it["id"]}/">{html.escape(it["title"])}</a>（{jdate(it["date"])} {html.escape(it["kind"])}）</li>' for it in mitems) + "</ul>")
    tabs.append(("match/", "投票記録とくらべる", "参議院で採決された法律などに、自分なら賛成か反対かを答えて、各会派の実際の投票とくらべる。人が法律を選ばず、判定もしない。", body, "match", feed.get("checked")))
    bl = load("bills")
    body = f"<h1>審議中・採決されなかった法律案</h1><p>{e(bl.get('criteria'))}</p><p>{e(bl.get('status_rule'))}</p>"
    by = {}
    for b in bl.get("items", []):
        by.setdefault(b["session"], []).append(b)
    for ses, rows in by.items():
        body += f"<h2>{e(ses)}に出されたもの（{len(rows)}件）</h2><ul>" + "".join(
            f"<li>{html.escape(b['title'])}（{e(b['number'])}・提出 {e(b.get('proposer') or b['submitter'])}" + (f"（{e('、'.join(b['groups']))}）" if b.get("groups") else "") + f"・{e(b['result'])}）"
            + (f' <a href="a/{b["article"]}/">記事</a>' if b.get("article") else "") + "</li>" for b in rows) + "</ul>"
    body += sources_html([bl["source"]]) if bl.get("source") else ""
    tabs.append(("bills/", "審議中・採決されなかった法律案", "国会で審議中の法律案と、採決されずに終わった法律案（審議未了・継続審査・撤回）のすべて。題名・提出者・提出会派・結果を、衆議院の議案情報の表記のまま。", body, "bills", bl.get("checked")))
    qs = load("questions")
    body = f"<h1>質問主意書と答弁書</h1><p>{e(qs.get('criteria'))}</p><p>{e(qs.get('rule'))}</p>"
    qgroups = {}
    for q in qs.get("items", []):
        qgroups.setdefault(f"{q['session']}・{q['house']}", []).append(q)
    for k, rows in qgroups.items():
        body += f"<h2>{e(k)}（{len(rows)}件）</h2><ul>" + "".join(
            f"<li>{q['number']}号 {link(q['question_url'], q['title']) if q.get('question_url') else html.escape(q['title'])}（提出 "
            + (f'<a href="member/{q["member"]}/">{html.escape(q["submitter"])}</a>' if q.get("member") else html.escape(q["submitter"]))
            + (f"・{jdate(q['submitted'])}" if q.get("submitted") else "") + f"・{e(q['status'])}" + (f"・{link(q['answer_url'], '答弁書')}" if q.get("answer_url") else "") + "）</li>" for q in rows) + "</ul>"
    body += sources_html(qs.get("sources"))
    tabs.append(("questions/", "質問主意書と答弁書", "国会議員が内閣に文書で出した質問主意書と、内閣の答弁書のすべて。件名・提出者・日付は衆議院・参議院のページの表記のまま。", body, "questions", qs.get("checked")))
    tabs.append(("diary/", "国会と政府のうごき", "国会で決まったこと・閣議・質問主意書・パブリックコメントを、起きた日ごとにまとめたページ。人は選ばず、日付の新しい順に並べる。",
                 "<h1>国会と政府のうごき</h1><p>国会で決まったこと、閣議、質問主意書、パブリックコメントを、起きた日ごとにまとめています。</p>", "diary", ""))
    pc = load("pubcom")
    body = f"<h1>パブリックコメント（意見募集）</h1><p>{e(pc.get('criteria'))}</p><ul>" + "".join(
        f"<li>{link(x.get('url') or x.get('result_url'), x['title'])}（{e(x['status'])}・{e(x.get('category', ''))}・{e(x.get('office', ''))}" + (f"・公示 {jdate(x['published'])}" if x.get("published") else "") + "）</li>" for x in pc.get("items", [])) + "</ul>" + sources_html(pc.get("sources"))
    tabs.append(("pubcom/", "パブリックコメント（意見募集）", "国の役所が政令・省令などを決める前に出す案への意見募集と、その結果のすべて。e-Gov の表記のまま。", body, "pubcom", pc.get("checked")))
    kg = load("kakugi")
    body = f"<h1>閣議の案件</h1><p>{e(kg.get('criteria'))}</p>" + "".join(
        f"<h2>{jdate(m['date'])}（{e(m['kind'])}）</h2><ul>" + "".join(f"<li>{e(i['section'])}：{html.escape(i['title'])}" + (f"（{html.escape(i['offices'])}）" if i.get("offices") else "") + "</li>" for i in m["items"]) + "</ul>"
        for m in kg.get("meetings", [])[:60] if m["items"]) + sources_html(kg.get("sources"))
    cnp = os.path.join(ROOT, "data", "councils.json")
    if os.path.exists(cnp):
        C = json.load(open(cnp, encoding="utf-8"))
        body = f"<h1>国の審議会</h1><p>{e(C['criteria'])}</p><p>{e(C['rule'])}</p>"
        cur = None
        for x in C["items"]:
            g = x["ministry"] + (f"（{x['agency']}）" if x.get("agency") else "")
            if g != cur:
                body += ("</dl>" if cur else "") + f"<h2>{e(g)}</h2><dl>"
                cur = g
            qa = x.get("qa", [])
            body += (f"<dt>{link(x.get('homepage') or x['pdf'], x['name'])}</dt><dd>根拠法令：{e(x.get('basis', ''))}・設置：{e(x.get('established_text', ''))}・委員の定数：{e(x.get('seats', ''))}"
                     f"<br>{html.escape(x.get('duties', '')).replace(chr(10), '<br>')}"
                     + (f"<br>諮問と答申など（{len(qa)}件）：" + "／".join(html.escape(q['text']) for q in qa[:8]) + ("ほか" if len(qa) > 8 else "") if qa else "")
                     + f"<br>{link(x['pdf'], '審議会総覧（PDF）')}</dd>")
        body += ("</dl>" if cur else "") + sources_html(C.get("sources"))
        tabs.append(("councils/", "国の審議会", "国の審議会等（審議会総覧に載っているもの）のすべて。根拠法令・所掌事務・委員の定数・諮問と答申。総覧の表記のまま。", body, "councils", C.get("checked")))
    tabs.append(("kakugi/", "閣議の案件", "閣議で決まった法律案・政令・方針などのすべて（人事を除く）。首相官邸のページの表記のまま。", body, "kakugi", kg.get("checked")))
    rsp = os.path.join(ROOT, "data", "rs", "projects.json")
    if os.path.exists(rsp):
        R = json.load(open(rsp, encoding="utf-8"))
        body = f"<h1>国の予算事業と支出先（{R['year']}年度 行政事業レビュー）</h1><p>{e(R['criteria'])}</p><p>{e(R['rule'])}</p>"
        for m in R["ministries"]:
            rows = [x for x in R["items"] if x["ministry"] == m["name"]]
            body += f"<h2>{e(m['name'])}（{m['count']}事業）</h2><ul>" + "".join(
                f'<li><a href="budget/{html.escape(x["id"])}/">{html.escape(x["name"])}</a>（当初予算 {x["budget"].get("initial", 0):,}円）</li>' for x in rows) + "</ul>"
        body += sources_html(R.get("sources"))
        tabs.append(("budget/", "国の予算事業と支出先", "行政事業レビューにある国の事業すべての予算と、お金の支払い先（法人番号のある支出先）。府省庁ごと・金額順に見られる。", body, "budget", R.get("checked")))
    body = f"<h1>公式サイト・SNSの一覧</h1><p>{e(acc.get('criteria'))}</p><p>{e(acc.get('rule'))}</p>"
    for g in acc.get("groups", []):
        body += f"<h2>{e(g['name'])}（{len(g['items'])}）</h2>" + "".join(f"<h3>{e(x['name'])}</h3>" + sns_html(x).replace("<h2>公式サイト・SNS</h2>", "") for x in g["items"])
    tabs.append(("accounts/", "公式サイト・SNSの一覧", "政府（外局まで）・国会・裁判所・会計検査院・日本銀行・政党の公式サイトと、公式サイトにリンクされているSNSアカウントの一覧。機械で集め、人は選ばない。", body, "accounts", max([x.get("checked", "") for x in acc_rows] or [""])))
    for args in tabs:
        site.page(*args)
    # 組織のページ（D-19）：つながりに自分のページがない国の機関
    byid = {x["id"]: x for g in acc.get("groups", []) if g["id"] != "party" for x in g["items"]}
    pg_title = {(tab, p["id"]): p["title"] for tab, src_ in (("learn", learn), ("links", links)) for sec in src_.get("sections", []) for p in sec.get("pages", [])}
    for g in acc.get("groups", []):
        if g["id"] == "party":
            continue
        for x in g["items"]:
            if x.get("page"):
                continue
            par = byid.get(x.get("parent"))
            body = f"<h1>{e(x['name'])}</h1><p>{e(g['name'])}。このページは、せいじのまどのほかのページに載っているもののうち、この組織のものを機械で集めたものです。</p>"
            if par:
                href = f"links/{par['page']}/" if par.get("page") else f"org/{par['id']}/"
                body += f'<p>置かれている府省：<a href="{href}">{e(par["name"])}</a></p>'
            kids = [k for k in byid.values() if k.get("parent") == x["id"]]
            if kids:
                body += "<h2>置かれている組織</h2><ul>" + "".join(f'<li><a href="org/{k["id"]}/">{e(k["name"])}</a></li>' for k in kids) + "</ul>"
            ab = [(t, i) for t, i in x.get("about", []) if (t, i) in pg_title]
            if ab:
                body += "<h2>しくみの説明</h2><ul>" + "".join(f'<li><a href="{t}/{i}/">{e(pg_title[(t, i)])}</a></li>' for t, i in ab) + "</ul>"
            body += sns_html(x)
            site.page(f"org/{x['id']}/", x["name"], f"{x['name']}（{g['name']}）の公式サイトとSNS、このサイトにある関係のページ。", body, "org-" + x["id"], x.get("checked"))

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
