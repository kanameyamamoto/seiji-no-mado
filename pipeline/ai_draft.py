"""data/pending/ の下書きから、記事を作って確かめる。

流れ（ルールC-1〜C-4）
  1. 機械で作れる項目（番号・日付・審議の経過・会派ごとの賛否）は、AIを使わずに資料から作る。
  2. 書く担当AI（Anthropic）が、見出し・要約・主な内容を4つのレベルで書く。
  3. AIを使わないプログラムで、数字と日付を元の資料と照合する（C-2）。
  4. 確かめる担当AI（OpenAI）が、誤り・もれ・評価語・偏りを確かめる（C-1）。
  5. 通らなければ、指摘を渡して書き直す。最大3回（C-3）。それでも通らなければ公開しない（status=held）。
  6. 指示文・モデル名・チェック結果を data/logs/<key>.json に残す（C-4）。

APIキーがないときは何もしない（下書きは fetched のまま残り、次の回に作る）。
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ai  # noqa: E402
import numcheck  # noqa: E402
import sangiin as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PENDING = os.path.join(DATA, "pending")
LOGS = os.path.join(DATA, "logs")
PROMPTS = os.path.join(ROOT, "prompts")
LEVELS = ("es", "hs", "std", "univ")
MAX_REWRITES = 3
JST = datetime.timezone(datetime.timedelta(hours=9))


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def prompt_body(name):
    t = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    return t.split("---", 1)[1].strip() if "---" in t else t


# ---------- 1. 機械で作る部分 ----------

def session_label(feed, ses):
    for s in feed.get("sessions", []):
        if s.get("name") == f"第{ses}回国会":
            return f"第{ses}回国会（{s['kind']}）" if s.get("kind") else s["name"]
    return f"第{ses}回国会"


def chamber(r, house):
    m = r.get(f"{house}本会議経過/採決方法", "")
    return {"date": S.wdate(r.get(f"{house}本会議経過/議決日", "")),
            "result": r.get(f"{house}本会議経過/議決", ""),
            "method": re.sub(r"（.*", "", m).strip(),
            "mode": r.get(f"{house}本会議経過/採決態様", "")}


def votes_of(v):
    if not v:
        return None
    out = {"chamber": "参議院", "date": v["date"], "for": [], "against": [], "split": []}
    for name, seats, f, a in v["groups"]:
        if f and a:
            out["split"].append(name)
        elif f:
            out["for"].append(name)
        elif a:
            out["against"].append(name)
    if not out["split"]:
        del out["split"]
    t = v.get("total") or [0, 0, 0]
    out["totals"] = {"votes": int(t[0]), "for": int(t[1]), "against": int(t[2])}
    out["detail"] = [{"group": n, "seats": s, "for": f, "against": a} for n, s, f, a in v["groups"]]
    out["agenda"] = v.get("anken", "")
    out["source"] = {"label": f"参議院 本会議投票結果（{v['date']}）", "url": v["url"]}
    out["rule"] = "会派ごとの賛成票・反対票の数で分類：賛成のみ→for、反対のみ→against、両方あり→split。会派の並びは投票結果ページの順。"
    return out


def skeleton(raw, feed):
    r = raw["bill"]
    cat, ses, no = raw["category"], raw["session"], raw["key"].split("-")[-1]
    year = int((raw["date"] or today())[:4]) - 2018
    rejected = raw.get("kind") == "法律案が否決"
    if rejected:
        lid = f"r{year}-rej-{ses}-" + {"閣法": "kakuho", "衆法": "shuho", "参法": "sanho"}[cat] + f"-{no}"
    elif cat in ("閣法", "衆法", "参法"):
        lid = f"r{year}-law-{int(raw['law_no']):03d}" if raw.get("law_no", "").isdigit() else f"r{year}-law-{ses}-{cat}-{no}"
    elif cat == "予算":
        lid = f"r{year}-budget-{ses}-{no}"
    else:
        lid = f"r{year}-treaty-{ses}-{int(no):02d}" if no.isdigit() else f"r{year}-treaty-{ses}-{no}"
    formal = raw["formal"]
    if cat in ("閣法", "衆法", "参法") and not rejected:  # 否決されたものは「法律案」のまま
        formal = re.sub(r"案$", "", formal)
    elif cat == "条約":
        formal = re.sub(r"の締結について承認を求めるの件$", "", formal)
    ministries = [m for m in (r.get("その他の/所管官庁", "") or "").split("、") if m]
    topic = "外務省（条約）" if cat == "条約" else (ministries[0] if ministries else ("内閣" if cat == "閣法" else "国会（議員立法）"))
    item = {
        "id": lid, "src_key": raw["src_key"], "kind": raw["kind"], "date": raw["date"],
        "session": session_label(feed, ses), "title": "", "formal": formal, "bill": raw["formal"],
        "number": raw["number"] or f"{cat}（提出番号{no}）",
        "submitter": "内閣" if cat in ("閣法", "予算", "条約") else "議員",
        "topic": topic, "ministries": ministries,
    }
    if raw.get("bill_id"):
        item["bill_id"] = raw["bill_id"]  # data/bills.json の id
    if raw.get("groups"):
        item["submitter_groups"] = raw["groups"]  # 衆議院の議案審議経過情報の「議案提出会派」
    if raw.get("law_no", "").isdigit():
        item["law_no"] = f"令和{year}年法律第{raw['law_no']}号"
    if raw.get("promulgated"):
        item["promulgated"] = raw["promulgated"]
    first = "衆議院" if r.get("議案審議一覧/先議区分", "").startswith("衆") else "参議院"
    if raw.get("process"):  # 衆議院で否決（衆議院の経過情報から作ったもの）
        item["process"] = raw["process"]
    else:
        item["process"] = {"first_chamber": first, "shugiin": chamber(r, "衆議院"), "sangiin": chamber(r, "参議院"),
                           "source": raw["source"]}
        for h in ("shugiin", "sangiin"):
            if not item["process"][h].get("result"):
                del item["process"][h]
    v = raw.get("votes") or votes_of(raw.get("vote"))
    if v:
        item["votes"] = v
    return item


# ---------- 2〜5. 書く・照合する・確かめる ----------

def source_text(raw):
    r = raw["bill"]
    keep = ["議案審議一覧/件名", "議案審議一覧/種別", "議案審議一覧/提出番号", "参議院本会議経過/議決日",
            "衆議院本会議経過/議決日", "その他の/公布年月日", "その他の/法律番号", "その他の/所管官庁"]
    keep += ["提出者", "提出会派", "衆議院本会議経過/議決", "参議院本会議経過/議決"]
    lines = [f"{k.split('/')[-1]}：{r.get(k, '')}" for k in keep if r.get(k)]
    head = ""
    if raw.get("kind") == "法律案が否決":
        where = "衆議院" if r.get("衆議院本会議経過/議決") == "否決" else "参議院"
        head = f"【結果】この法律案は{where}の本会議で否決され、成立していない。\n"
    name = "議案要綱" if raw.get("yoshi_source") else "議案要旨"
    return head + "\n".join(lines) + f"\n\n【{name}】\n" + (raw.get("yoshi") or "（なし）")


def texts_of(draft):
    out = [draft.get("title", "")] + [draft.get("lead", {}).get(l, "") for l in LEVELS]
    for f in draft.get("facts", []):
        out += [f.get(l, "") for l in LEVELS]
    return out


def shape_problems(draft):
    p = []
    if not draft.get("title"):
        p.append("title がありません")
    for l in LEVELS:
        if not draft.get("lead", {}).get(l):
            p.append(f"lead.{l} がありません")
    facts = draft.get("facts", [])
    if not 1 <= len(facts) <= 8:
        p.append("facts は1〜8項目にしてください")
    for i, f in enumerate(facts):
        for l in LEVELS:
            if not f.get(l):
                p.append(f"facts[{i}].{l} がありません")
    return p


def run_one(path, feed):
    raw = json.load(open(path, encoding="utf-8"))
    log = {"key": raw["key"], "date": today(), "rules": ["C-1", "C-2", "C-3", "C-4"],
           "writer_prompt": "prompts/write_law.md", "checker_prompt": "prompts/check_law.md", "rounds": []}
    if not raw.get("yoshi"):
        raw["status"] = "held"
        raw["held_reason"] = "議案要旨がまだ出ていないため、書かずに待つ（F-1）。"
        json.dump(raw, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[held] {raw['key']}: 議案要旨なし")
        return
    src = source_text(raw)
    wsys, csys = prompt_body("write_law.md"), prompt_body("check_law.md")
    feedback = ""
    draft = None
    for rnd in range(MAX_REWRITES + 1):
        user = "【元の資料】\n" + src
        if feedback:
            user += "\n\n【前回の記事】\n" + json.dumps(draft, ensure_ascii=False) + "\n\n【直してほしいこと】\n" + feedback
        text, wmodel = ai.write(wsys, user)
        entry = {"round": rnd + 1, "writer_model": wmodel}
        try:
            draft = ai.extract_json(text)
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"JSONとして読めない: {e}"
            log["rounds"].append(entry)
            feedback = "指定の形のJSONだけを返してください。"
            continue
        entry["draft"] = draft
        probs = shape_problems(draft)
        miss = numcheck.compare(texts_of(draft), src)
        entry["numcheck"] = [{"type": t, "value": v, "in": s} for t, v, s in miss]
        if probs or miss:
            entry["result"] = "機械の照合で不合格"
            log["rounds"].append(entry)
            feedback = "\n".join(probs + [f"{t}「{v}」が元の資料にありません（{s}…）" for t, v, s in miss])
            continue
        ctext, cmodel = ai.check(csys, "【元の資料】\n" + src + "\n\n【書かれた記事】\n" + json.dumps(draft, ensure_ascii=False))
        entry["checker_model"] = cmodel
        try:
            verdict = ai.extract_json(ctext)
        except Exception as e:  # noqa: BLE001
            verdict = {"pass": False, "problems": [{"type": "確認できない", "detail": str(e)}]}
        entry["check"] = verdict
        if verdict.get("pass") and not verdict.get("problems"):
            entry["result"] = "合格"
            log["rounds"].append(entry)
            break
        entry["result"] = "確かめる担当AIの指摘あり"
        log["rounds"].append(entry)
        feedback = "\n".join(f"- {p.get('where', '')}：{p.get('detail', '')}（{p.get('fix', '')}）" for p in verdict.get("problems", []))
    else:
        raw["status"] = "held"
        raw["held_reason"] = f"{MAX_REWRITES}回書き直してもチェックに通らなかったため、公開しない（C-3）。"
        log["final"] = "公開しない"
        save_log(log)
        json.dump(raw, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[held] {raw['key']}: チェック不合格")
        return

    item = skeleton(raw, feed)
    item["title"] = draft["title"]
    item["lead"] = {l: draft["lead"][l] for l in LEVELS}
    yoshi_src = raw.get("yoshi_source") or {"label": re.sub(r"）$", "・議案要旨）", raw["source"]["label"]), "url": raw["source"]["url"]}
    item["facts"] = [{"text": {l: f[l] for l in LEVELS}, "rules": ["F-1", "F-3", "F-4"], "sources": [yoshi_src]}
                     for f in draft["facts"]]
    item["checked"] = today()
    item["ai_log"] = f"data/logs/{raw['key']}.json"
    raw["status"] = "passed"
    raw["item"] = item
    log["final"] = "公開する"
    save_log(log)
    json.dump(raw, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[passed] {raw['key']} {item['title']}")


def save_log(log):
    os.makedirs(LOGS, exist_ok=True)
    json.dump(log, open(os.path.join(LOGS, f"{log['key']}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    if not (ai.writer_available() and ai.checker_available()):
        print("確かめる担当のAPIキー（GEMINI_API_KEY）がないので、記事は作りません（下書きのまま待ちます）。")
        return
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    for fn in sorted(os.listdir(PENDING)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(PENDING, fn)
        raw = json.load(open(p, encoding="utf-8"))
        if raw.get("status") == "fetched":
            ai.set_item(raw["key"])
            try:
                run_one(p, feed)
                ai.finish(raw["key"])
            except ai.NeedWriter:
                print(f"[waiting] {raw['key']}：Claude（スケジュール）が書くのを待っています")
            except ai.RetryLater as e:
                print(f"[waiting] {raw['key']}：{e}。次の回にもう一度")
            except Exception as e:  # noqa: BLE001
                print(f"[error] {fn}: {e}（次の回にもう一度試す）")


if __name__ == "__main__":
    main()
