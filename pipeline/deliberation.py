"""記事を厚くするための資料を、機械で集める（AIは使わない）。

1. 提出の理由：衆議院「議案本文情報」の法律案の最後にある「理由」（閣法・衆法・参法）。
2. 国会での議論：国立国会図書館「国会会議録検索システム」の API（https://kokkai.ndl.go.jp/api.html）から、
   その法律が議決された日の本会議（衆議院・参議院）の発言のうち、その法律が議題になった部分だけを取る。
   - 委員長報告（委員会での質疑・修正・附帯決議のまとめ）
   - 討論（各会派の賛成・反対の理由）
   本会議で討論がないことも多いので、委員長報告に出てくる委員会の会議録からも、その法律の「討論」を取る
   （委員長の「〜を議題といたします」で区切り、「討論に入ります」から「討論は終局」までの発言）。
   議長の「〜を議題といたします」から、次の議題までをひとまとまりとして、その中に法律の名前が出てくるものを使う。
3. 議案要旨（参議院）または議案要綱（衆議院）：記事の本体を書いたときと同じ資料。

集めた資料は work/sources/more-<記事ID>.json に残し、同じ記事では同じ資料を使う（AIへの依頼と答えを毎回同じにするため）。
会議録がまだ公開されていない（議決から数日〜数週間かかる）ときは、None を返して次の回に回す。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import shugiin as H  # noqa: E402
import numcheck  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work", "sources")
NDL = "https://kokkai.ndl.go.jp/api/speech"
UA = "seiji-no-mado-bot/1.0 (+https://github.com/kanameyamamoto/seiji-no-mado)"
MAX_SPEECH = 6000  # 1つの発言の長さの上限（文字）。長い討論は前から切る
_day_cache = {}


def norm(s):
    """比べるために、空白を消し、漢数字を算用数字にそろえる。"""
    s = re.sub(r"[\s　]", "", s or "")
    return numcheck.normalize(s)


def ndl_day(house, d):
    """その日の本会議の発言を、順番どおりにすべて返す。"""
    k = (house, d)
    if k in _day_cache:
        return _day_cache[k]
    out, start = [], 1
    while True:
        q = urllib.parse.urlencode({"nameOfHouse": house, "nameOfMeeting": "本会議", "from": d, "until": d,
                                    "recordPacking": "json", "maximumRecords": 100, "startRecord": start})
        req = urllib.request.Request(f"{NDL}?{q}", headers={"User-Agent": UA})
        for i in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    j = json.loads(r.read().decode("utf-8"))
                break
            except Exception:  # noqa: BLE001
                if i == 2:
                    raise
                time.sleep(5 * (i + 1))
        recs = j.get("speechRecord") or []
        out += recs
        nxt = j.get("nextRecordPosition")
        if not nxt or not recs:
            break
        start = nxt
        time.sleep(1)  # 国会図書館のサーバーに負担をかけない
    out.sort(key=lambda s: s.get("speechOrder", 0))
    _day_cache[k] = out
    return out


def ndl(params):
    q = urllib.parse.urlencode({**params, "recordPacking": "json", "maximumRecords": 100})
    req = urllib.request.Request(f"{NDL}?{q}", headers={"User-Agent": UA})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


_issue_cache = {}


def issue(issue_id):
    """1回の会議の発言を、順番どおりにすべて返す。"""
    if issue_id in _issue_cache:
        return _issue_cache[issue_id]
    out, start = [], 1
    while True:
        j = ndl({"issueID": issue_id, "startRecord": start})
        recs = j.get("speechRecord") or []
        out += recs
        nxt = j.get("nextRecordPosition")
        if not nxt or not recs:
            break
        start = nxt
        time.sleep(1)
    out.sort(key=lambda s: s.get("speechOrder", 0))
    _issue_cache[issue_id] = out
    return out


CHAIR = re.compile(r"^○(\S{1,12}?(委員長|会長)[　 ]|(委員長|会長)（)")  # 衆議院「○冨樫委員長　」、参議院「○委員長（辻元清美君）」


def committee_talks(house, committee, until, bill):
    """委員会の会議録から、その法律の討論（賛成・反対の理由）を取る。"""
    import datetime
    if not committee or not until:
        return []
    frm = (datetime.date.fromisoformat(until) - datetime.timedelta(days=120)).isoformat()
    j = ndl({"nameOfHouse": house, "nameOfMeeting": committee, "from": frm, "until": until, "any": "討論に入ります"})
    ids = []
    for s in j.get("speechRecord") or []:
        if s["issueID"] not in ids:
            ids.append(s["issueID"])
    key = norm(bill)
    out = []
    for iid in reversed(ids):  # 新しい会議から
        time.sleep(1)
        sp = issue(iid)
        agenda_ok, in_debate = False, False
        for s in sp:
            t = s.get("speech", "")
            chair = bool(CHAIR.match(t))
            if chair and "議題といたします" in t:
                agenda_ok = key in norm(t)
                in_debate = False
                continue
            if not agenda_ok:
                continue
            if chair and "討論に入ります" in t:
                in_debate = "申出がありません" not in t and "申出がございません" not in t
                continue
            if chair and ("討論は終局" in t or "採決に入ります" in t or "採決いたします" in t):
                in_debate = False
                continue
            if in_debate and not chair:
                tt = re.sub(r"\r\n", "\n", t).strip()
                out.append({"speaker": s.get("speaker", ""), "group": s.get("speakerGroup") or "", "text": tt[:MAX_SPEECH],
                            "cut": len(tt) > MAX_SPEECH, "url": s.get("speechURL", ""), "meeting": f"{house} {committee} {s.get('date', '')}"})
        if out:
            break
    return out


def is_agenda(s):
    t = s.get("speech", "")
    return s.get("speakerPosition") in ("議長", "副議長") and ("議題といたします" in t or "日程に追加して" in t or "議題とすることに" in t)


def segments(speeches):
    """議長が議題を告げたところで区切る。"""
    segs, cur = [], None
    for s in speeches:
        if is_agenda(s):
            cur = [s]
            segs.append(cur)
        elif cur is not None:
            cur.append(s)
    return segs


def debate(house, d, bill):
    """その日の本会議で、その法律が議題になった部分の 委員長報告 と 討論 を返す。見つからなければ None。"""
    if not d:
        return None
    speeches = ndl_day(house, d)
    if not speeches:
        return None
    key = norm(bill)
    for seg in segments(speeches):
        if key not in norm("".join(s.get("speech", "") for s in seg[:3])):
            continue
        report, talks = None, []
        for s in seg[1:]:
            if s.get("speakerPosition") in ("議長", "副議長"):
                continue
            t = re.sub(r"\r\n", "\n", s.get("speech", "")).strip()
            item = {"speaker": s.get("speaker", ""), "group": s.get("speakerGroup") or "", "text": t[:MAX_SPEECH],
                    "cut": len(t) > MAX_SPEECH, "url": s.get("speechURL", ""), "meeting": f"{house} 本会議 {d}"}
            if report is None and "ただいま議題となりました" in t[:120]:
                report = item
            else:
                talks.append(item)
        committee_list = []
        if report:
            m = re.search(r"([^\s、。「」（）]+?(?:委員会|審査会))における", report["text"])
            if m:
                try:
                    committee_list = committee_talks(house, m.group(1), d, bill)
                except Exception as e:  # noqa: BLE001
                    print(f"[more] 委員会の会議録を読めませんでした {house} {m.group(1)}: {e}")
        return {"house": house, "date": d, "report": report, "talks": talks, "committee_talks": committee_list,
                "minutes": (seg[0].get("meetingURL") or "")}
    return None


def reason(item):
    """衆議院の議案本文情報から、法律案の「理由」を取る（閣法・衆法・参法）。"""
    m = re.match(r"(閣法|衆法|参法)第(\d+)号", item.get("number", ""))
    sm = re.match(r"第(\d+)回", item.get("session", ""))
    if not m or not sm:
        return None
    cat, no, ses = m.group(1), m.group(2), sm.group(1)
    rows = [r for r in H.list_bills(ses) if r["cat"] == cat and r["no"] == no and norm(r["title"]) == norm(item.get("bill", ""))]
    if not rows:
        rows = [r for r in H.list_bills(ses) if r["cat"] == cat and norm(r["title"]) == norm(item.get("bill", ""))]
    if not rows or not rows[0]["honbun"]:
        return None
    url = H.houan_url(rows[0]["honbun"])
    t = H.body_text(url)
    i = max(t.rfind("理　由"), t.rfind("理 由"), t.rfind("\n理由\n"))
    if i < 0:
        return None
    body = t[i:].split("\n", 1)[1].strip() if "\n" in t[i:] else ""
    if not body:
        return None
    return {"text": body, "source": {"label": f"衆議院 議案本文情報（{item.get('session', '')} {cat}第{no}号・提出時の法律案の理由）", "url": url}}


def gather(item, summary_text):
    """記事1本ぶんの資料。会議録がまだなら None。"""
    path = os.path.join(WORK, f"more-{item['id']}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    pr = item.get("process") or {}
    talks = []
    for house, key in (("衆議院", "shugiin"), ("参議院", "sangiin")):
        d = (pr.get(key) or {}).get("date")
        if not d:
            continue
        x = debate(house, d, item.get("bill") or item.get("formal", ""))
        if x is None:
            return None  # 議決した本会議の会議録がまだない（または見つからない）→ 次の回に
        talks.append(x)
    try:
        rs = reason(item)
    except Exception as e:  # noqa: BLE001
        print(f"[more] 理由を読めませんでした {item['id']}: {e}")
        rs = None
    mats = {"id": item["id"], "summary": summary_text, "reason": rs, "debates": talks}
    os.makedirs(WORK, exist_ok=True)
    json.dump(mats, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return mats


def as_text(mats):
    """AIに渡す資料の文。"""
    out = ["【議案の要旨】", mats.get("summary") or "（なし）", ""]
    if mats.get("reason"):
        out += ["【提出の理由（法律案の最後の「理由」）】", mats["reason"]["text"], ""]
    else:
        out += ["【提出の理由】", "（資料なし）", ""]
    for d in mats.get("debates", []):
        out.append(f"【{d['house']} 本会議（{d['date']}）】")
        if d.get("report"):
            r = d["report"]
            out += [f"〔委員長報告〕{r['speaker']}（{r['group']}）", r["text"], ""]
        else:
            out.append("〔委員長報告〕（なし）")
        if d.get("talks"):
            for i, t in enumerate(d["talks"], 1):
                out += [f"〔本会議の討論{i}〕{t['speaker']}（{t['group']}）" + ("（長いので途中まで）" if t.get("cut") else ""), t["text"], ""]
        else:
            out += ["〔本会議の討論〕（なし）", ""]
        for i, t in enumerate(d.get("committee_talks") or [], 1):
            out += [f"〔委員会の討論{i}〕{t['speaker']}（{t['group']}）・{t['meeting']}" + ("（長いので途中まで）" if t.get("cut") else ""), t["text"], ""]
    return "\n".join(out)
