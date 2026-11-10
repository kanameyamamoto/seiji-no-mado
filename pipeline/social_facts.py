"""SNS に出す「データの数字のポスト」を、このサイトのデータから機械で作る（ルール D-17。AIは使わない）。

決まり：
- 文は、下の決まった型（TEMPLATES）に、このサイトのデータを数えた数字を入れたものだけ。意見・評価・予想は書かない。
- 1本に、答えの数字（1文目）・数え方と出どころ（2文目）・このサイトのページへのリンクを入れる。ハッシュタグは使わない。
- 数字はグラフの画像にもする（数字は文と同じものだけ）。
- どれを出すかは人が選ばない：一番長く出していない型から順に出す。同じ型でも数字が変わっていなければ、30日は出さない。
  月曜はパブリックコメントの型を先にする（その週のしめきりがわかるように）。
- 個人の名前（議員を含む）は、ポストにも画像にも入れない。政党・会派の比べっこもしない。

使い方：
  python pipeline/social_facts.py            … いま出せる候補を全部、文と画像で out/social/ に書き出す（確かめる用）
  from social_facts import pick              … social.py から、次に出す1本を選ぶ
"""
import collections
import datetime
import hashlib
import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "out", "social")
JST = datetime.timezone(datetime.timedelta(hours=9))
NAVY, BLUE, PALE, GRAY = "#14264A", "#1F3F7A", "#A9B6CF", "#5A6680"
QUIET_DAYS = 30

# 質問主意書の題名で数える言葉（政策の分野。並びは五十音）
TOPICS = ["AI", "医療", "エネルギー", "外国人", "教育", "子ども", "災害", "消費税", "年金", "農業", "防衛"]


def today():
    return datetime.datetime.now(JST).date()


def load(name):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def jd(s):
    d = datetime.date.fromisoformat(s[:10])
    return f"{d.year}年{d.month}月{d.day}日"


def ym(s):
    d = datetime.date.fromisoformat(s[:10])
    return f"{d.year}年{d.month}月"


def yen(n):
    """円を、兆・億・万で読みやすく（四捨五入しない、上の2つの単位まで）。"""
    n = int(n)
    if n >= 10 ** 12:
        cho, oku = divmod(n // 10 ** 8, 10 ** 4)
        return f"{cho:,}兆{oku:,}億円" if oku else f"{cho:,}兆円"
    if n >= 10 ** 8:
        oku, man = divmod(n // 10 ** 4, 10 ** 4)
        return f"{oku:,}億{man:,}万円" if man else f"{oku:,}億円"
    return f"{n // 10 ** 4:,}万円"


def fact(fid, text, how, path, chart, data):
    """fid＝型と種類、text＝1文目、how＝数え方と出どころ、path＝サイトのページ、chart＝グラフ、data＝数字（変わったかの判定用）。"""
    key = hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    return {"id": f"fact:{fid}", "key": key, "text": text, "how": how, "path": path, "chart": chart, "title": chart.get("title", "")}


# ---------- 型 ----------
def t_questions_by_session(d):
    q = d["questions"]["items"]
    since = min(x["submitted"] for x in q if x.get("submitted"))
    c = collections.Counter(x["session"] for x in q)
    order = sorted(c, key=lambda s: int("".join(ch for ch in s if ch.isdigit()) or 0))
    top = max(order, key=lambda s: c[s])
    top_y = min(x["submitted"] for x in q if x["session"] == top and x.get("submitted"))[:4]
    return [fact("questions-by-session",
                 f"{ym(since)}から出された質問主意書（議員が内閣に文書で出す質問）は{len(q):,}件です。国会ごとに数えると、一番多かったのは{top}（{top_y}年）の{c[top]:,}件でした。",
                 "衆議院・参議院の質問主意書の一覧から数えました。", "questions/",
                 {"kind": "bar", "title": "国会ごとの質問主意書の数", "labels": [s.replace("第", "").replace("国会", "") for s in order], "values": [c[s] for s in order],
                  "xlabel": "国会の回", "unit": "件"}, {"n": len(q), "c": dict(c)})]


def t_questions_answer_days(d):
    q = [x for x in d["questions"]["items"] if x.get("answered") and x.get("submitted")]
    days = [(datetime.date.fromisoformat(x["answered"]) - datetime.date.fromisoformat(x["submitted"])).days for x in q]
    days = [x for x in days if x >= 0]
    med = statistics.median(days)
    qs = statistics.quantiles(days, n=4)
    edges = [(0, 7, "7日まで"), (8, 10, "8〜10日"), (11, 14, "11〜14日"), (15, 21, "15〜21日"), (22, 10 ** 6, "22日〜")]
    vals = [sum(1 for x in days if a <= x <= b) for a, b, _ in edges]
    return [fact("questions-answer-days",
                 f"質問主意書を出してから内閣の答えがとどくまでの日数は、真ん中で{med:g}日でした。4分の1は{qs[0]:g}日以内、4分の3は{qs[2]:g}日以内です。",
                 f"答えがとどいた{len(days):,}件の、出した日と答えの日から数えました。", "questions/",
                 {"kind": "bar", "title": "質問主意書の答えがとどくまでの日数", "labels": [x[2] for x in edges], "values": vals, "unit": "件"},
                 {"med": med, "qs": qs, "vals": vals})]


def t_questions_topic(d):
    q = d["questions"]["items"]
    since = min(x["submitted"] for x in q if x.get("submitted"))
    out = []
    for kw in TOPICS:
        hit = [x for x in q if kw in x.get("title", "")]
        if len(hit) < 10:
            continue
        by = collections.Counter(x["submitted"][:4] for x in hit if x.get("submitted"))
        years = sorted(by)
        out.append(fact(f"questions-topic-{kw}",
                        f"題名に「{kw}」が入っている質問主意書は、{ym(since)}から{len(hit):,}件ありました。全体の{len(hit) / len(q) * 100:.1f}%です。",
                        f"衆議院・参議院の質問主意書{len(q):,}件の題名から数えました。", "questions/",
                        {"kind": "bar", "title": f"題名に「{kw}」が入っている質問主意書（出した年ごと）", "labels": [f"{y}年" for y in years], "values": [by[y] for y in years], "unit": "件"},
                        {"kw": kw, "n": len(hit), "by": dict(by)}))
    return out


def t_laws_unanimous(d):
    laws = [x for x in d["feed"]["items"] if x.get("kind") == "法律が成立" and (x.get("votes") or {}).get("for")]
    if len(laws) < 10:
        return []
    since = min(x["date"] for x in laws)
    n_against = collections.Counter(min(len(x["votes"].get("against") or []), 3) for x in laws)
    u = n_against[0]
    labels = ["0", "1", "2", "3以上"]
    return [fact("laws-unanimous",
                 f"{ym(since)}から国会で決まった法律{len(laws)}件のうち、反対した会派が1つもなかったものは{u}件（{u / len(laws) * 100:.0f}%）でした。",
                 "このサイトの「国会で決まったこと」の記事で、最後の本会議の投票を数えました。", "laws/",
                 {"kind": "bar", "title": "法律が決まったときに反対した会派の数", "labels": labels, "values": [n_against[i] for i in range(4)], "xlabel": "反対した会派の数", "unit": "件"},
                 {"n": len(laws), "c": dict(n_against)})]


def t_laws_by_ministry(d):
    laws = [x for x in d["feed"]["items"] if x.get("kind") == "法律が成立"]
    c = collections.Counter(m for x in laws for m in (x.get("ministries") or [])[:1])
    if not c:
        return []
    since = min(x["date"] for x in laws)
    top = c.most_common(8)
    return [fact("laws-by-ministry",
                 f"{ym(since)}から国会で決まった法律{len(laws)}件を、受け持つ役所ごとに数えると、一番多いのは{top[0][0]}の{top[0][1]}件でした。",
                 "このサイトの「国会で決まったこと」の記事で、はじめに書いた役所で数えました。", "laws/",
                 {"kind": "hbar", "title": "国会で決まった法律の数（受け持つ役所ごと）", "labels": [k for k, _ in top], "values": [v for _, v in top], "unit": "件"},
                 {"n": len(laws), "top": top})]


def t_pubcom_open(d):
    p = d.get("pubcom")
    if not p:
        return []
    t = today()
    open_ = [x for x in p["items"] if x.get("status") == "意見募集中" and x.get("deadline", "") >= t.isoformat()]
    if not open_:
        return []
    week = [x for x in open_ if x["deadline"] <= (t + datetime.timedelta(days=7)).isoformat()]
    c = collections.Counter(x.get("category") or "そのほか" for x in open_).most_common(6)
    first = min(x["deadline"] for x in open_)
    tail = f"そのうち{len(week)}件は、7日以内にしめきりです。" if week else f"一番早いしめきりは{jd(first)}です。"
    return [fact("pubcom-open",
                 f"{t.month}月{t.day}日のときに意見を募集しているパブリックコメント（国が決まりをつくる前に、だれでも意見を出せるしくみ）は{len(open_)}件です。{tail}",
                 "e-Gov のパブリックコメントの一覧から数えました。", "pubcom/",
                 {"kind": "hbar", "title": "意見を募集しているパブリックコメント（件数の多い分野）", "labels": [k for k, _ in c], "values": [v for _, v in c], "unit": "件"},
                 {"d": t.isoformat(), "n": len(open_), "w": len(week)})]


def t_councils_by_ministry(d):
    c0 = d.get("councils")
    if not c0:
        return []
    items = c0["items"]
    c = collections.Counter(x["ministry"] for x in items).most_common(8)
    old = min((x for x in items if x.get("established")), key=lambda x: x["established"])
    return [fact("councils-by-ministry",
                 f"国の審議会（役所が専門家などの意見を聞く会議）は{len(items)}あります。役所ごとに数えると、一番多いのは{c[0][0]}の{c[0][1]}でした。一番古いのは{jd(old['established'])}にできた{old['name']}です。",
                 "内閣人事局の「審議会総覧」から数えました。", "councils/",
                 {"kind": "hbar", "title": "国の審議会の数（役所ごと）", "labels": [k for k, _ in c], "values": [v for _, v in c], "unit": ""},
                 {"n": len(items), "c": c, "old": old["id"]})]


def t_kakugi_kinds(d):
    k = d.get("kakugi")
    if not k:
        return []
    y = today().year
    m = [x for x in k["meetings"] if x["date"].startswith(str(y))]
    if len(m) < 5:
        return []
    c = collections.Counter(x["kind"] for x in m)
    last = max(x["date"] for x in m)
    kinds = ["定例", "臨時", "持ち回り"]
    return [fact("kakugi-kinds",
                 f"{y}年の閣議（大臣全員の会議）は、{jd(last)}までに{len(m)}回でした。決まった曜日に開く定例が{c['定例']}回、臨時が{c['臨時']}回、集まらずに文書を回して決める持ち回りが{c['持ち回り']}回です。",
                 "首相官邸の閣議の案件の一覧から数えました。", "kakugi/",
                 {"kind": "bar", "title": f"{y}年の閣議の回数（開き方ごと）", "labels": kinds, "values": [c[x] for x in kinds], "unit": "回"},
                 {"n": len(m), "c": dict(c), "last": last})]


def t_budget_top(d):
    r = d.get("rs")
    if not r:
        return []
    items = [x for x in r["items"] if (x.get("budget") or {}).get("current")]
    y = items[0]["budget"].get("year")
    out = []
    by = collections.defaultdict(list)
    for x in items:
        by[x["ministry"]].append(x)
    for mi, xs in sorted(by.items(), key=lambda kv: -sum(x["budget"]["current"] for x in kv[1]))[:12]:
        if len(xs) < 5:
            continue
        top = sorted(xs, key=lambda x: -x["budget"]["current"])[:6]
        out.append(fact(f"budget-top-{mi}",
                        f"{mi}の予算事業{len(xs):,}件のうち、{y}年度の予算が一番大きいのは「{top[0]['name']}」の{yen(top[0]['budget']['current'])}でした。",
                        "行政事業レビュー（国の予算事業の公開データ）から、補正後の予算額で数えました。", "budget/",
                        {"kind": "hbar", "title": f"{mi}の予算事業（{y}年度の予算額が大きい順）", "labels": [x["name"] for x in top],
                         "values": [x["budget"]["current"] for x in top], "unit": "", "money": True},
                        {"mi": mi, "n": len(xs), "top": [(x["id"], x["budget"]["current"]) for x in top]}))
    return out


def t_bills_now(d):
    b = d.get("bills")
    if not b:
        return []
    sess = max((x["session"] for x in b["items"]), key=lambda s: int("".join(ch for ch in s if ch.isdigit()) or 0))
    xs = [x for x in b["items"] if x["session"] == sess and x.get("result") == "審議中"]
    if not xs:
        return []
    c = collections.Counter(x["category"] for x in xs)
    names = {"閣法": "内閣", "衆法": "衆議院議員", "参法": "参議院議員"}
    return [fact("bills-now",
                 f"いまの{sess}で話し合われている法律の案は{len(xs)}件です。内閣が出したものが{c['閣法']}件、議員が出したものが{c['衆法'] + c['参法']}件です。",
                 f"衆議院・参議院の議案の一覧から、{jd(today().isoformat())}のときの数を数えました。", "bills/",
                 {"kind": "bar", "title": f"{sess}で話し合われている法律の案（出した人ごと）", "labels": [names[k] for k in ("閣法", "衆法", "参法")],
                  "values": [c[k] for k in ("閣法", "衆法", "参法")], "unit": "件"},
                 {"s": sess, "c": dict(c)})]


TEMPLATES = [t_pubcom_open, t_laws_unanimous, t_questions_by_session, t_budget_top, t_questions_answer_days, t_councils_by_ministry,
             t_questions_topic, t_laws_by_ministry, t_kakugi_kinds, t_bills_now]


def all_facts():
    d = {"questions": load("questions.json"), "feed": load("feed.json"), "pubcom": load("pubcom.json"), "councils": load("councils.json"),
         "kakugi": load("kakugi.json"), "rs": load(os.path.join("rs", "projects.json")), "bills": load("bills.json")}
    out = []
    for t in TEMPLATES:
        try:
            out += [dict(f, template=t.__name__) for f in t(d)]
        except Exception as e:  # noqa: BLE001
            print(f"[facts] {t.__name__} を作れませんでした: {e}")
    return out


def pick(posts, platform):
    """次に出す1本。posts＝これまでの投稿の記録（work/social.json の posts）。"""
    facts = all_facts()
    mine = [p for p in posts if p["platform"] == platform and p["id"].startswith("fact:")]
    last_id = {}
    last_key = {}
    for p in mine:
        last_id[p["id"]] = max(last_id.get(p["id"], ""), p["date"])
        last_key[(p["id"], p.get("key"))] = max(last_key.get((p["id"], p.get("key")), ""), p["date"])
    quiet = (datetime.datetime.now(JST) - datetime.timedelta(days=QUIET_DAYS)).isoformat()
    last_tpl = {}
    for f in facts:
        last_tpl[f["template"]] = max(last_tpl.get(f["template"], ""), last_id.get(f["id"], ""))
    ok = [f for f in facts if last_key.get((f["id"], f["key"]), "") < quiet and last_id.get(f["id"], "") < quiet]
    if not ok:
        return None
    monday = today().weekday() == 0
    order = {t.__name__: i for i, t in enumerate(TEMPLATES)}
    ok.sort(key=lambda f: (not (monday and f["template"] == "t_pubcom_open"), last_tpl.get(f["template"], ""), last_id.get(f["id"], ""), order[f["template"]], f["id"]))
    return ok[0]


# ---------- グラフ ----------
def wrap(s, n=12):
    """長い名前は12字ごとに折り返し、2行をこえる分は「…」にする。"""
    lines = [s[i:i + n] for i in range(0, len(s), n)]
    return "\n".join(lines[:2]) + ("…" if len(lines) > 2 else "")


def chart_png(f, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for name in ("Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic"):
        if any(name in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    else:
        try:  # 日本語の字がない環境（pip install japanize-matplotlib で入る）
            import japanize_matplotlib  # noqa: F401
        except ImportError:
            print("[facts] 日本語の字が見つかりません。グラフの字が化けます")
    c = f["chart"]
    fig = plt.figure(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.08, 0.16, 0.86, 0.66] if c["kind"] == "bar" else [0.25, 0.12, 0.62, 0.70])
    vals, labels = c["values"], c["labels"]
    if c["kind"] == "hbar":
        y = list(range(len(vals)))[::-1]
        bars = ax.barh(y, vals, color=[BLUE] + [PALE] * (len(vals) - 1), height=0.62)
        ax.set_yticks(y, [wrap(x) for x in labels], fontsize=14 if any(len(x) > 12 for x in labels) else 15, color=NAVY, linespacing=1.15)
        for b, v in zip(bars, vals):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, " " + (yen(v) if c.get("money") else f"{v:,.0f}{c['unit']}"), va="center", fontsize=14, color=NAVY)
        ax.set_xticks([])
        ax.set_xlim(0, max(vals) * 1.22)
    else:
        top = max(range(len(vals)), key=lambda i: vals[i])
        bars = ax.bar(range(len(vals)), vals, color=[BLUE if i == top else PALE for i in range(len(vals))], width=0.66)
        ax.set_xticks(range(len(vals)), labels, fontsize=13 if len(vals) <= 10 else 11, color=NAVY)
        for i, (b, v) in enumerate(zip(bars, vals)):
            if len(vals) <= 12 or i == top:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:,.0f}", ha="center", va="bottom", fontsize=13, color=NAVY)
        ax.set_yticks([])
        ax.set_ylim(0, max(vals) * 1.15)
        if c.get("xlabel"):
            ax.set_xlabel(c["xlabel"], fontsize=13, color=GRAY)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(PALE)
    ax.tick_params(length=0)
    fig.text(0.04, 0.91, c["title"], fontsize=22, fontweight="bold", color=NAVY)
    fig.text(0.04, 0.035, f["how"], fontsize=11.5, color=GRAY)
    fig.text(0.96, 0.035, "せいじのまど", fontsize=13, fontweight="bold", color=NAVY, ha="right")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def main():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    facts = all_facts()
    for f in facts:
        name = f["id"].replace("fact:", "").replace("/", "_")
        chart_png(f, os.path.join(OUT, name + ".png"))
        print(f"--- {f['id']}  ({f['template']})\n{f['text']}\n{f['how']}\nhttps://seijinomado.com/{f['path']}\n")
    print(f"[facts] {len(facts)}本")


if __name__ == "__main__":
    main()
