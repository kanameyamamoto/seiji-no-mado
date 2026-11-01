"""AIを使わない機械の照合（ルールC-2）。

AIが書いた文章に出てくる数字・日付が、元の資料（議案要旨・議案情報・投票結果）に
本当にあるかを確かめる。漢数字（二十二兆八千六百八十億）も数字に直して比べる。
"""
import re

KD = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SMALL = {"十": 10, "百": 100, "千": 1000}
BIG = {"万": 10**4, "億": 10**8, "兆": 10**12}
ZEN = str.maketrans("０１２３４５６７８９，．", "0123456789,.")


def kanji_to_int(s):
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in KD:
            num = num * 10 + KD[ch]
        elif ch in SMALL:
            section += (num or 1) * SMALL[ch]
            num = 0
        elif ch in BIG:
            section += num
            total += section * BIG[ch]
            section, num = 0, 0
    return total + section + num


KRUN = "〇零一二三四五六七八九十百千万億兆"
UNIT = "年月日号条項人円兆億万回次歳割倍件か箇・分目"


def normalize(text):
    """全角数字を半角に、漢数字を算用数字にする（「令和九年四月一日」→「令和9年4月1日」、「〇・五」→「0.5」）。
    「一部」「統一」のような言葉の中の漢字は変えないよう、単位の前の漢数字か、2文字以上続く漢数字だけを変える。"""
    t = (text or "").translate(ZEN)
    t = re.sub(r"[\d一二三四五六七八九十]+つ", "", t)  # 「3つ」「二つ」など、数え方の数は照合しない
    t = re.sub(r"([〇零一二三四五六七八九]+)・([〇零一二三四五六七八九]+)",
               lambda m: str(kanji_to_int(m.group(1))) + "." + "".join(str(KD[c]) for c in m.group(2)), t)

    def rep(m):
        s, nxt = m.group(0), t[m.end():m.end() + 1]
        if all(ch in "万億兆" for ch in s):
            return s
        if len(s) >= 2 or (nxt and nxt in UNIT):
            # 「二十二兆八千六百八十億」は「22兆8680億」の形にして、後の読み取りにまかせる
            out, buf = "", ""
            for ch in s:
                if ch in "兆億万":
                    out += (str(kanji_to_int(buf)) if buf else "") + ch
                    buf = ""
                else:
                    buf += ch
            return out + (str(kanji_to_int(buf)) if buf else "")
        return s
    return re.sub("[" + KRUN + "]+", rep, t)


def values_in(text):
    """文章に出てくる数の値（単位の兆・億・万をかけた値）を集める。"""
    t = normalize(text)
    out = set()
    # 算用数字（3兆1,135億 などの組み合わせも）
    for m in re.finditer(r"(?:(\d[\d,]*)兆)?(?:(\d[\d,]*)億)?(?:(\d[\d,]*)万)?(\d[\d,]*(?:\.\d+)?)?", t):
        if not m.group(0):
            continue
        parts = [m.group(1), m.group(2), m.group(3)]
        v = 0
        for p, mul in zip(parts, (10**12, 10**8, 10**4)):
            if p:
                v += int(p.replace(",", "")) * mul
        if m.group(4):
            try:
                tail = float(m.group(4).replace(",", ""))
            except ValueError:
                continue
            if any(parts):
                v += int(tail)
            else:
                out.add(tail)
                continue
        if v:
            out.add(float(v))
    return out


def dates_in(text):
    t = normalize(text)
    out = set()
    for y, mo, d in re.findall(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", t):
        out.add(f"{int(y)}-{int(mo):02d}-{int(d):02d}")
    for y, mo, d in re.findall(r"令和\s*(\d+|元)年\s*(\d{1,2})月\s*(\d{1,2})日", t):
        yy = 2018 + (1 if y == "元" else int(y))
        out.add(f"{yy}-{int(mo):02d}-{int(d):02d}")
    for y, mo, d in re.findall(r"平成\s*(\d+|元)年\s*(\d{1,2})月\s*(\d{1,2})日", t):
        yy = 1988 + (1 if y == "元" else int(y))
        out.add(f"{yy}-{int(mo):02d}-{int(d):02d}")
    for y, mo, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", t):
        out.add(f"{y}-{mo}-{d}")
    return out


def compare(generated_texts, source_text):
    """元の資料にない数字・日付の一覧を返す（空なら合格）。"""
    src_vals = values_in(source_text)
    src_dates = dates_in(source_text)
    missing = []
    for t in generated_texts:
        for v in values_in(t):
            if 1900 <= v <= 2100 and v.is_integer():  # 年は dates_in で照合
                continue
            if v not in src_vals:
                missing.append(("数字", v, t[:60]))
        for d in dates_in(t):
            if d not in src_dates:
                missing.append(("日付", d, t[:60]))
    return missing
