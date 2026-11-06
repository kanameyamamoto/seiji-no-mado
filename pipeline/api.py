"""公開API（無料）を書き出す。サーバーは使わず、GitHub Pages に置く JSON のファイル。

  {サイト}/api/v1/index.json        … 一覧（ここから全部たどれる）
  {サイト}/api/v1/laws.json         … 国会で決まったこと（一覧）
  {サイト}/api/v1/laws/<id>.json    … 1件（事実・審議の経過・会派ごとの賛否・出典・作成と更新の記録）
  {サイト}/api/v1/parties.json, parties/<id>.json
  {サイト}/api/v1/members.json, members/<id>.json（参議院議員は1件ずつの賛否つき）
  {サイト}/api/v1/governors.json
  {サイト}/api/v1/bills.json        … 審議中・採決されなかった法律案（審議中・継続審査・審議未了・撤回）
  {サイト}/api/v1/questions.json    … 質問主意書と答弁書（ルール D-12）
  {サイト}/api/v1/accounts.json     … 政府・国会・政党の公式サイトとSNS（ルール D-11）
  {サイト}/api/v1/learn.json, learn/<id>.json、links.json, links/<id>.json
  {サイト}/api/v1/glossary.json, rules.json

文章は4つの読むレベル（es・hs・std・univ）をそのまま入れる。ライセンスは CC BY 4.0。
あわせて、AI 向けの案内 llms.txt をサイトの一番上に書き出す。

使い方：python pipeline/api.py _site
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
JST = datetime.timezone(datetime.timedelta(hours=9))
LICENSE = {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/deed.ja",
           "attribution": "出典として「せいじのまど」の名前と、使ったページか API の URL を示してください。"}


def load(name):
    return json.load(open(os.path.join(DATA, name + ".json"), encoding="utf-8"))


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "_site"
    cfg = load("config")
    base = cfg.get("site_url", "https://seijinomado.com/").rstrip("/") + "/"
    api = os.path.join(out, "api", "v1")
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    n = 0

    def put(rel, obj):
        nonlocal n
        p = os.path.join(api, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        n += 1

    def wrap(data, **kw):
        return {"site": "せいじのまど", "generated": now, "license": LICENSE, **kw, "data": data}

    feed, parties, M, MV, G = load("feed"), load("parties"), load("members"), load("member_votes"), load("governors")
    learn, links, gl, rules = load("learn"), load("links"), load("glossary"), load("rules")

    put("laws.json", wrap([{"id": it["id"], "title": it["title"], "kind": it["kind"], "date": it["date"], "session": it.get("session"),
                            "number": it.get("number"), "law_no": it.get("law_no"), "topic": it.get("topic"),
                            "page": f"{base}a/{it['id']}/", "api": f"{base}api/v1/laws/{it['id']}.json"} for it in feed["items"]],
                          criteria=feed.get("criteria"), criteria_detail=feed.get("criteria_detail"), sessions=feed.get("sessions")))
    for it in feed["items"]:
        put(f"laws/{it['id']}.json", wrap(it, page=f"{base}a/{it['id']}/"))

    pl = []
    for p in parties["national"]:
        pl.append({"id": p["id"], "name": p["name"], "short": p.get("short"), "page": f"{base}party/{p['id']}/", "api": f"{base}api/v1/parties/{p['id']}.json"})
        put(f"parties/{p['id']}.json", wrap(p, page=f"{base}party/{p['id']}/",
                                            members=[m["id"] for m in M["members"] if p["id"] in (m.get("parties") or [])]))
    for i, p in enumerate(parties["others"]):
        pl.append({"id": f"o{i}", "name": p["name"], "page": f"{base}party/o{i}/", "api": f"{base}api/v1/parties/o{i}.json"})
        put(f"parties/o{i}.json", wrap(p, page=f"{base}party/o{i}/"))
    put("parties.json", wrap(pl, order=parties.get("order"), elections=parties.get("elections"), field_notes=parties.get("field_notes")))

    titles = {it["id"]: it["title"] for it in feed["items"]}
    lab = {"Y": "賛成", "N": "反対", "-": "記録なし"}
    ml = []
    for m in M["members"]:
        ml.append({k: m.get(k) for k in ("id", "house", "name", "yomi", "kaiha", "parties", "district", "elected_by", "terms", "term_end")}
                  | {"page": f"{base}member/{m['id']}/", "api": f"{base}api/v1/members/{m['id']}.json"})
        d = dict(m)
        if m["house"] == "参議院":
            sv = MV.get("votes", {}).get(m["id"], "")
            d["votes"] = [{"law": i, "title": titles.get(i), "vote": lab.get(sv[k] if k < len(sv) else "-")} for k, i in enumerate(MV.get("items", []))]
        put(f"members/{m['id']}.json", wrap(d, page=f"{base}member/{m['id']}/"))
    put("members.json", wrap(ml, shu=M.get("shu"), san=M.get("san"), votes_note=MV.get("note")))
    put("governors.json", wrap(G["governors"], as_of=G.get("as_of"), source=G.get("source")))

    for name, d, path in (("learn", learn, "learn"), ("links", links, "links")):
        lst = []
        for sec in d["sections"]:
            for p in sec["pages"]:
                lst.append({"id": p["id"], "title": p["title"], "section": sec["name"], "page": f"{base}{path}/{p['id']}/", "api": f"{base}api/v1/{name}/{p['id']}.json"})
                put(f"{name}/{p['id']}.json", wrap(p, section=sec["name"], page=f"{base}{path}/{p['id']}/"))
        put(f"{name}.json", wrap(lst))
    put("glossary.json", wrap(gl))
    put("rules.json", wrap(rules))
    bl = load("bills")
    put("bills.json", wrap(bl.get("items", []), criteria=bl.get("criteria"), status_rule=bl.get("status_rule"), order=bl.get("order"), source=bl.get("source")))
    qs = load("questions")
    put("questions.json", wrap(qs.get("items", []), criteria=qs.get("criteria"), rule=qs.get("rule"), sources=qs.get("sources")))
    pc, kg = load("pubcom"), load("kakugi")
    put("pubcom.json", wrap(pc.get("items", []), criteria=pc.get("criteria"), rule=pc.get("rule"), sources=pc.get("sources")))
    put("kakugi.json", wrap(kg.get("meetings", []), criteria=kg.get("criteria"), rule=kg.get("rule"), sources=kg.get("sources")))
    ac = load("accounts")
    put("accounts.json", wrap(ac.get("groups", []), criteria=ac.get("criteria"), rule=ac.get("rule"), order=ac.get("order")))
    eps = {k: f"{base}api/v1/{k}.json" for k in ("laws", "bills", "questions", "pubcom", "kakugi", "parties", "members", "governors", "accounts", "learn", "links", "glossary", "rules")}
    eps.update({"budget_projects": f"{base}data/rs/projects.json", "budget_detail": f"{base}data/rs/detail-{{府省庁のslug}}.json", "budget_corps": f"{base}data/rs/corps.json"})
    put("index.json", wrap(eps, about="せいじのまど（政治の情報を、どの党にもメディアにも偏らずに届けるサイト）の公開データ。サーバーを使わない静的な JSON で、登録やキーは不要。毎日自動で更新される。",
                           levels={"es": "かんたん", "hs": "やさしい", "std": "ふつう", "univ": "くわしく"}, docs=f"{base}api/"))

    # AI 向けの案内（llms.txt）
    lines = [
        "# せいじのまど",
        "",
        "> 日本の政治の情報を、どの党にもメディアにも偏らずに届けるサイト。事実と意見を分け、結論は書かない。書き方のルールはすべて公開している。",
        "",
        "- 中身：国会で成立した法律・予算・条約と否決された法律案（すべて）、審議中・採決されなかった法律案の一覧、政治のしくみの解説、政党（全党を同じ項目で）、国会議員と知事（全員を同じ項目で）、予算・省庁などのデータ、メディアの一覧。",
        "- 出典：すべての事実に一次情報（条文・国会の議案情報と投票結果・官公庁の資料）の出典をつけている。各ページに確認日と、更新・訂正の記録がある。",
        "- 文章は4つの読むレベル（かんたん・やさしい・ふつう・くわしく）で書いている。ページ（HTML）は「ふつう」、API はすべてのレベルを含む。",
        "- 書くのは AI（Claude）、確かめるのは別の会社の AI（Gemini）と、AI を使わないプログラム。AI への指示文とチェックの結果も公開している。",
        f"- ライセンス：CC BY 4.0（出典として「せいじのまど」とURLを示せば自由に使える）。",
        "",
        "## 主なページ",
        f"- [国会で決まったこと]({base}laws/)：成立した法律・予算・条約と否決された法律案の一覧と、1件ずつの事実・会派ごとの賛否",
        f"- [審議中・採決されなかった法律案]({base}bills/)：審議中・継続審査・審議未了・撤回の法律案すべて（衆議院の議案情報の表記のまま）",
        f"- [質問主意書と答弁書]({base}questions/)：国会議員が内閣に文書で出した質問と、内閣の答弁書のすべて（両院のページの表記のまま）",
        f"- [パブリックコメント]({base}pubcom/)：政令・省令などの案への意見募集と、その結果（e-Gov の表記のまま）",
        f"- [国会と政府のうごき]({base}diary/)：国会で決まったこと・閣議・質問主意書・パブリックコメントを、起きた日ごとに並べたもの",
        f"- [閣議の案件]({base}kakugi/)：閣議で決まった法律案・政令・方針など（人事を除く。首相官邸のページの表記のまま）",
        f"- [国の予算事業と支出先]({base}budget/)：行政事業レビューにある国の事業すべての予算と、法人番号のある支出先（データ：{base}data/rs/projects.json・{base}data/rs/corps.json）",
        f"- [公式サイト・SNSの一覧]({base}accounts/)：政府・国会・政党の公式サイトと、公式サイトにリンクされているSNSアカウント（機械で集め、人は選ばない）",
        f"- [学ぶ]({base}learn/)：政治のしくみの解説",
        f"- [政党・選挙]({base}parties/)：政党の成り立ち・考え方・政策・国会での賛否",
        f"- [国会議員]({base}members/)・[知事]({base}governors/)",
        f"- [投票記録とくらべる]({base}match/)：参議院で採決された法律などに自分の賛否を答え、参議院の各会派の実際の投票とくらべる（判定や推薦はしない）",
        f"- [つながり]({base}links/)：予算・省庁・政治資金などの数字",
        f"- [書き方のルール]({base}about/)",
        f"- [サイトマップ]({base}sitemap.xml)",
        "",
        "## データ（公開API・JSON・キー不要）",
        f"- [API の一覧]({base}api/v1/index.json)・[使い方]({base}api/)",
    ] + [f"- [{k}]({v})" for k, v in eps.items()]
    if cfg.get("api_live"):
        lines += ["", "## AI から使う（MCP）・検索API（キー不要・無料・1分60回まで）",
                  "- MCP（Streamable HTTP・ログインなし）：https://api.seijinomado.com/mcp （道具：search・get・list・about）",
                  "- 検索API：https://api.seijinomado.com/v1/search?q=ことば ・使い方：https://api.seijinomado.com/"]
    open(os.path.join(out, "llms.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"公開API {n} ファイルと llms.txt を書き出しました")


if __name__ == "__main__":
    main()
