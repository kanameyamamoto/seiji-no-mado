"""検索エンジン用のページを書き出す（公開のたびに作り直す）。

サイト本体（index.html）は1つのページの中で表示を切り替えるので、URLの「#」から後ろは
検索エンジンからは同じページに見える。そこで、記事・ページごとに、中身をそのまま書いた
普通のHTMLを作り、サイトマップ（sitemap.xml）に載せる。

- 文章は「ふつう」のレベル（std）で書く。中身はサイト本体と同じデータから作るので、食い違わない。
- 各ページから「サイトで開く」で、読むレベルや立場を選べるサイト本体に移れる。

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


CSS = """
:root{--bg:#EEF1F6;--surface:#fff;--line:#D3DAE5;--fg:#13213A;--muted:#4A5872;--accent:#1F3F7A;--nav:#14264A}
@media (prefers-color-scheme:dark){:root{--bg:#0F1726;--surface:#172236;--line:#2A3852;--fg:#E8EDF6;--muted:#A9B6CF;--accent:#9DB5E6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:'Hiragino Sans','Yu Gothic UI','Noto Sans JP',sans-serif;line-height:1.8}
header{background:var(--nav);color:#fff}header .w{display:flex;align-items:center;gap:16px;min-height:56px}
header a{color:#fff;text-decoration:none;font-weight:700;white-space:nowrap}header nav{display:flex;gap:14px;overflow-x:auto;font-size:14px;scrollbar-width:none}header nav a{font-weight:400;opacity:.85}
.w{max-width:760px;margin:0 auto;padding:0 16px}main.w{background:var(--surface);border:1px solid var(--line);border-radius:16px;margin:20px auto;padding:24px 20px;width:calc(100% - 32px)}
h1{font-size:26px;line-height:1.4;margin:.2em 0 .4em}h2{font-size:19px;margin:1.6em 0 .4em}h3{font-size:16px;margin:1.2em 0 .2em}
.meta{color:var(--muted);font-size:14px;display:flex;flex-wrap:wrap;gap:4px 14px}.lead{font-size:17px}.hook{font-weight:700;color:var(--accent)}
.note{color:var(--muted);font-size:14px}.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.stats{display:flex;flex-wrap:wrap;gap:12px}.stats div{border:1px solid var(--line);border-radius:10px;padding:8px 14px}
dt{font-size:13px;color:var(--muted)}dd{margin:0;font-weight:700}a{color:var(--accent)}.src{font-size:14px}
.open{display:block;text-align:center;background:var(--nav);color:#fff;border-radius:12px;padding:14px;font-weight:700;text-decoration:none;margin:28px 0 8px}
footer{color:var(--muted);font-size:13px;padding:8px 0 40px}
"""


class Site:
    def __init__(self, out, base):
        self.out, self.base, self.urls = out, base.rstrip("/") + "/", []

    def page(self, path, title, desc, body, app_route, lastmod=""):
        depth = path.count("/")
        up = "../" * depth
        url = self.base + path
        desc = re.sub(r"\s+", " ", t(desc))[:120]
        doc = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}｜せいじのまど</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:title" content="{html.escape(title)}｜せいじのまど">
<meta property="og:description" content="{html.escape(desc)}"><meta property="og:url" content="{url}">
<meta property="og:image" content="{self.base}assets/og.png"><meta property="og:site_name" content="せいじのまど"><meta property="og:locale" content="ja_JP">
<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#14264A">
<style>{CSS}</style></head><body>
<header><div class="w"><a href="{up}">せいじのまど</a><nav><a href="{up}#learn">学ぶ</a><a href="{up}#parties">政党・選挙</a><a href="{up}#laws">国会で決まったこと</a><a href="{up}#links">つながり</a><a href="{up}#media">メディア</a><a href="{up}pages/">ページの一覧</a></nav></div></header>
<main class="w">{body}
<a class="open" href="{up}#{app_route}">サイトで開く（読むレベル・あなたの立場を選べます）</a>
</main>
<footer class="w">このページは、検索エンジン向けに「ふつう」のレベルの文章を書き出したものです。どの党にもメディアにも偏らないよう、事実と意見を分け、結論は書きません。書き方のルールは<a href="{up}#about">すべて公開</a>しています。</footer>
</body></html>
"""
        d = os.path.join(self.out, path)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(doc)
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
        site.page(f"a/{it['id']}/", it["title"], it.get("lead"), body, "a-" + it["id"], it.get("checked") or it.get("date"))

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
                site.page(f"{name}/{p['id']}/", p["title"], p.get("summary") or p["title"], body, f"pg-{name}-{p['id']}", p.get("checked"))

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
        site.page(f"party/{key}/", p["name"], pr.get("catch") or p.get("summary") or p.get("detail") or p["name"], body, route, p.get("checked") or parties.get("checked"))
    for p in parties["national"]:
        party_page(p, p["id"], "p-" + p["id"])
    for i, p in enumerate(parties["others"]):
        party_page(p, f"o{i}", f"p-o{i}")

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

    # メディア
    for s in media["sections"]:
        body = f"<div class=\"meta\"><span>メディア</span></div><h1>{e(s['name'])}</h1>"
        if s.get("lead"):
            body += f"<p class=\"lead\">{e(s['lead'])}</p>"
        if s.get("directory"):
            body += "<ul>" + "".join(f"<li>{link(x.get('url', ''), x.get('name'))}{(' — ' + e(x['sub'])) if x.get('sub') else ''}</li>" for x in s["directory"]) + "</ul>"
        for p in s.get("pages", []):
            body += f"<h2>{e(p['title'])}</h2>" + (f"<p>{e(p['summary'])}</p>" if p.get("summary") else "") + "".join(block(b) for b in p.get("blocks", [])) + sources_html(p.get("sources"))
        site.page(f"media/{s['id']}/", s["name"], s.get("lead") or s["name"], body, "media", s.get("checked") or media.get("checked"))

    # ページの一覧（検索エンジンが全ページをたどれるように）
    groups = {"a": "国会で決まったこと", "learn": "学ぶ", "links": "つながり", "party": "政党", "elections": "選挙", "media": "メディア"}
    body = "<h1>ページの一覧</h1><p>このサイトのすべてのページです。</p>"
    for g, label in groups.items():
        items = [(u, ti, pth) for u, _, ti, pth in site.urls if pth.split("/")[0] == g]
        body += f"<h2>{label}（{len(items)}）</h2><ul>" + "".join(f'<li><a href="../{pth}">{html.escape(ti)}</a></li>' for _, ti, pth in items) + "</ul>"
    site.page("pages/", "ページの一覧", "せいじのまどの全ページの一覧。国会で決まったこと・学ぶ・つながり・政党・選挙・メディア。", body, "home",
              datetime.datetime.now(JST).strftime("%Y-%m-%d"))

    # サイトマップと robots.txt
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
           f"<url><loc>{site.base}</loc><lastmod>{today}</lastmod></url>"]
    for u, lm, _, _ in site.urls:
        xml.append(f"<url><loc>{html.escape(u)}</loc>" + (f"<lastmod>{lm[:10]}</lastmod>" if re.match(r"\d{4}-\d{2}-\d{2}", lm or "") else "") + "</url>")
    xml.append("</urlset>")
    open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write("\n".join(xml) + "\n")
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(f"User-agent: *\nAllow: /\nSitemap: {site.base}sitemap.xml\n")
    print(f"検索用のページ {len(site.urls)} 件と sitemap.xml を書き出しました")


if __name__ == "__main__":
    main()
