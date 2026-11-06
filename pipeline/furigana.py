"""ふりがなと漢字の範囲を、機械で確かめる（ルール L-5。AIは使わない）。

ふりがなの書き方：漢字《かんじ》 … 《 》の前に続く漢字に、ふりがなをつける。
                  ｜日本銀行《にっぽんぎんこう》 … 漢字でない文字をまたぐときや、どこからかをはっきりさせたいときは ｜ で始まりを示す。
決まり：
  かんたん（es）… すべての漢字にふりがなをつける。
  やさしい（hs）… 小学校で習わない漢字には、ふりがなをつける。
  どちらも、ふりがなはひらがな（外国の言葉はカタカナでもよい）。
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_K = json.load(open(os.path.join(ROOT, "data", "kanji.json"), encoding="utf-8"))
ELEMENTARY = set(_K["elementary"])
JOYO = set(_K["joyo"])
KANJI = r"㐀-䶿一-鿿豈-﫿々〆ヶ"
RUBY = re.compile(r"｜([^｜《》\n]+)《([^《》\n]*)》|([" + KANJI + r"]+)《([^《》\n]*)》")
IS_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def plain(s):
    """ふりがなを外した文（検索・数字の照合・API の plain に使う）。"""
    return RUBY.sub(lambda m: m.group(1) or m.group(3), s or "")


def parts(s):
    """[(文字列, ふりがな or None)] に分ける。"""
    out, i = [], 0
    for m in RUBY.finditer(s or ""):
        if m.start() > i:
            out.append((s[i:m.start()], None))
        out.append((m.group(1) or m.group(3), m.group(2) if m.group(1) else m.group(4)))
        i = m.end()
    if i < len(s or ""):
        out.append((s[i:], None))
    return out


def problems(s, level):
    """決まりに合わないところを、文で返す。"""
    p = []
    if "《" in (s or "") or "》" in (s or ""):
        rest = RUBY.sub("", s)
        if "《" in rest or "》" in rest:
            p.append("《》の形が正しくないところがある")
    for base, rt in parts(s):
        if rt is not None:
            if not rt:
                p.append(f"「{base}」のふりがなが空")
            elif not re.fullmatch(r"[ぁ-ゖァ-ヺー・]+", rt):
                p.append(f"「{base}」のふりがな「{rt}」に、かな以外の文字がある")
            continue
        bad = []
        for ch in base:
            if not IS_KANJI.match(ch):
                continue
            if level == "es" or (level == "hs" and ch not in ELEMENTARY):
                bad.append(ch)
        if bad:
            ctx = base[max(0, base.find(bad[0]) - 6):base.find(bad[0]) + 8]
            p.append(f"ふりがながない漢字「{''.join(dict.fromkeys(bad))}」（…{ctx}…）")
    return p


def walk_levels(obj, path=""):
    """4つのレベルの文（{es, hs, std, univ}）を、場所つきですべて返す。"""
    if isinstance(obj, dict):
        if all(k in obj for k in ("es", "hs", "std")) and all(isinstance(obj[k], str) for k in ("es", "hs", "std")):
            if not any(k in obj for k in ("self", "parent", "home", "senior")):  # 立場ごとの例（L-4）は、レベルの文ではない
                yield path, obj
            return
        for k, v in obj.items():
            if k in ("sources", "updates", "log", "votes", "process"):
                continue
            yield from walk_levels(v, f"{path}/{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_levels(v, f"{path}/{i}")


def at(obj, path):
    for k in path.split("/"):
        obj = obj[int(k)] if isinstance(obj, list) else obj[k]
    return obj


def check_item(obj):
    """1つの記事やページの、かんたん・やさしいの文の問題をまとめて返す。"""
    out = []
    for path, t in walk_levels(obj):
        for lv in ("es", "hs"):
            for x in problems(t.get(lv, ""), lv):
                out.append(f"{path}（{lv}）：{x}")
    return out


if __name__ == "__main__":
    import sys
    for s in sys.argv[1:]:
        print(parts(s), problems(s, "es"), problems(s, "hs"))
