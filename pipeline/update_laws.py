"""国会で新しく成立した法律・予算・条約と、否決された法律案を見つけて、下書き（data/pending/）を作る。

載せる範囲（feed.json の criteria）：第220回国会（2026年1月召集）以降に成立した法律・予算・条約と否決された法律案を、すべて載せる。人は選ばない。
ここでは「両院で可決・承認された議案」を参議院の議案情報から機械的に集めるだけで、
文章は書かない（文章は ai_draft.py）。
否決された法律案（衆議院・参議院のどちらでも）も、成立したものと同じように下書きを作る（ルールD-5）。
否決されたものは data/bills.json（bills.py が衆議院の議案の一覧から作る）から見つける。
- 参議院で否決：参議院の議案情報（議案要旨）と本会議投票結果（押しボタン）を使う。
- 衆議院で否決：衆議院の議案審議経過情報（賛成会派・反対会派）と議案要綱を使う。

使い方：
  python pipeline/update_laws.py              # data/config.json の sessions を確認
  python pipeline/update_laws.py --sessions 222 223
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import sangiin as S  # noqa: E402
import shugiin as H  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PENDING = os.path.join(DATA, "pending")

KIND = {"法律案（内閣提出）": ("閣法", "法律が成立"), "法律案（衆法）": ("衆法", "法律が成立"),
        "法律案（参法）": ("参法", "法律が成立"), "予算": ("予算", "予算が成立"), "条約": ("条約", "条約を承認")}


def key_of(session, cat, no):
    return f"{session}-{cat}-{no}"


def existing_keys(feed):
    keys = set()
    for it in feed.get("items", []):
        if it.get("src_key"):
            keys.add(it["src_key"])
            continue
        # 既存データ（src_key がない分）は、参議院のURLから作る
        for f in it.get("facts", []) + [it.get("process", {})]:
            for s in (f.get("sources") or ([f.get("source")] if f.get("source") else [])):
                u = (s or {}).get("url", "")
                if "/meisai/m" in u:
                    keys.add(u.split("/meisai/")[1])
    return keys


SAN_CODE = {"閣法": "08", "衆法": "09", "参法": "10"}  # 参議院の議案情報のページ名：m<回次><種類><回次4けた><番号3けた>.htm


def rejected_drafts(feed, have, pending_now):
    """data/bills.json で「衆議院で否決」「参議院で否決」になった法律案の下書きを作る。"""
    path = os.path.join(DATA, "bills.json")
    if not os.path.exists(path):
        return 0
    bills = json.load(open(path, encoding="utf-8"))
    done = {it.get("bill_id") for it in feed.get("items", [])}
    n = 0
    for b in bills.get("items", []):
        if b.get("result") not in ("衆議院で否決", "参議院で否決"):
            continue
        ses, cat, no = b["id"].split("-")[0], b["category"], b["id"].split("-")[-1]
        k = key_of(ses, cat, no)
        if f"{k}.json" in pending_now or b["id"] in done:
            continue
        try:
            raw = rejected_raw(b, ses, cat, no, k)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 否決された法律案を読めませんでした {k}: {e}")
            continue
        if raw.get("src_key", "") in have:
            continue
        json.dump(raw, open(os.path.join(PENDING, f"{k}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        n += 1
        print(f"[new] {k} {raw['formal']}（{b['result']}）")
    return n


def rejected_raw(b, ses, cat, no, k):
    base = {"key": k, "kind": "法律案が否決", "category": cat, "session": ses, "number": f"{cat}第{no}号",
            "formal": b["title"], "groups": b.get("groups", []), "proposer": b.get("proposer", ""),
            "bill_id": b["id"], "status": "fetched"}
    if b["result"] == "参議院で否決":
        u = f"https://www.sangiin.go.jp/japanese/joho1/kousei/gian/{ses}/meisai/m{ses}{SAN_CODE[cat]}{int(ses):04d}{int(no):03d}.htm"
        r = S.parse_bill(u)
        vote = S.parse_vote(r["参議院本会議経過/vote"]) if r.get("参議院本会議経過/vote") else None
        base.update({"src_key": u.split("/meisai/")[1], "date": S.wdate(r.get("参議院本会議経過/議決日", "")) or b["sangiin"].get("date", ""),
                     "ministry": (r.get("その他の/所管官庁", "") or "").split("、")[0], "yoshi": r.get("yoshi", ""), "bill": r, "vote": vote,
                     "source": {"label": f"参議院 議案情報（第{ses}回国会 {cat}第{no}号）", "url": u}})
        return base
    # 衆議院で否決：参議院の投票記録はないので、衆議院の経過情報の賛成会派・反対会派を使う
    outline = H.body_text(b["outline"]) if b.get("outline") else ""
    lines = outline.split("\n")
    for i, x in enumerate(lines[:12]):
        if x.endswith("要綱"):
            lines = lines[i + 1:]
            break
    src = {"label": f"衆議院 議案審議経過情報（第{ses}回国会 {cat}第{no}号）", "url": b["source"]["url"]}
    base.update({
        "src_key": f"shu:{b['id']}", "date": b["shugiin"].get("date", ""), "ministry": "",
        "yoshi": "\n".join(lines), "yoshi_source": {"label": f"衆議院 議案要綱（第{ses}回国会 {cat}第{no}号）", "url": b["outline"]} if outline else None,
        "bill": {"議案審議一覧/件名": b["title"], "議案審議一覧/種別": cat, "議案審議一覧/提出番号": no, "提出者": b.get("proposer", ""),
                 "提出会派": "、".join(b.get("groups", [])), "衆議院本会議経過/議決": "否決", "衆議院本会議経過/議決日": b["shugiin"].get("date", "")},
        "process": {"first_chamber": "衆議院", "shugiin": {"date": b["shugiin"].get("date", ""), "result": "否決", "method": "", "mode": ""}, "source": src},
        "votes": {"chamber": "衆議院", "date": b["shugiin"].get("date", ""), "for": b.get("votes", {}).get("for", []), "against": b.get("votes", {}).get("against", []),
                  "source": src, "rule": "衆議院の議案審議経過情報の「衆議院審議時賛成会派」「衆議院審議時反対会派」をそのまま載せる（衆議院は会派ごとの票数を公表していない）。"},
        "source": src,
    })
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", nargs="*")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    sessions = [str(x) for x in (a.sessions or cfg["sessions"])]
    have = existing_keys(feed)
    os.makedirs(PENDING, exist_ok=True)
    pending_now = {f for f in os.listdir(PENDING) if f.endswith(".json")}
    new = 0
    for ses in sessions:
        try:
            urls = S.list_bills(str(ses))
        except Exception as e:  # noqa: BLE001
            print(f"[skip] 第{ses}回国会の一覧を読めませんでした: {e}")
            continue
        for u in urls:
            page = u.split("/meisai/")[1]
            if page in have:
                continue
            r = S.parse_bill(u)
            if not S.is_enacted(r):
                continue
            typ = r.get("議案審議一覧/種別", "")
            if typ not in KIND:  # 決算・承認案件（NHK予算など）・人事は載せる範囲の外（feed.json の criteria_detail）
                continue
            cat, kind = KIND[typ]
            no = r.get("議案審議一覧/提出番号", "")
            k = key_of(ses, cat, no)
            fn = f"{k}.json"
            if fn in pending_now:
                continue
            vote = None
            if r.get("参議院本会議経過/vote"):
                try:
                    vote = S.parse_vote(r["参議院本会議経過/vote"])
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 投票結果を読めませんでした {k}: {e}")
            dates = [S.wdate(r.get("参議院本会議経過/議決日", "")), S.wdate(r.get("衆議院本会議経過/議決日", ""))]
            raw = {
                "src_key": page,
                "key": k,
                "kind": kind,
                "category": cat,
                "session": ses,
                "number": f"{cat}第{no}号" if cat in ("閣法", "衆法", "参法") else "",
                "formal": r.get("議案審議一覧/件名", ""),
                "date": max(d for d in dates if d) if any(dates) else "",
                "promulgated": S.wdate(r.get("その他の/公布年月日", "")),
                "law_no": r.get("その他の/法律番号", ""),
                "ministry": (r.get("その他の/所管官庁", "") or "").split("、")[0],
                "yoshi": r.get("yoshi", ""),
                "bill": r,
                "vote": vote,
                "source": {"label": f"参議院 議案情報（第{ses}回国会 {cat}第{no}号）", "url": u},
                "status": "fetched",
            }
            json.dump(raw, open(os.path.join(PENDING, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            new += 1
            print(f"[new] {k} {raw['formal']}")
    new += rejected_drafts(feed, have, pending_now)
    print(f"新しく見つかった件数: {new}")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as gh:
        gh.write(f"new={new}\n")


if __name__ == "__main__":
    main()
