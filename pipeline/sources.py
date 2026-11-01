"""出典を取りに行って、比べられる文字にする。

- 法令（laws.e-gov.go.jp/law/<法令ID>）：e-Gov 法令API v2 で、改正の履歴（いちばん新しい改正のID）と本文を取る。
- PDF：pypdf で文字を取り出す。
- それ以外のページ：HTMLのタグ・スクリプトを除いた本文。
"""
import base64
import hashlib
import html
import io
import json
import logging
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

logging.getLogger("pypdf").setLevel(logging.ERROR)
# 古い暗号方式のままの官公庁サイトにもつなげるようにする（証明書の確認はそのまま行う）
CTX = ssl.create_default_context()
CTX.set_ciphers("DEFAULT@SECLEVEL=1")
# 中身が変わらない出典（論文のDOIなど）は、見張らない
STATIC_HOSTS = {"doi.org"}

UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
EGOV = re.compile(r"^https://laws\.e-gov\.go\.jp/law/([0-9A-Za-z_]+)")


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read(), r.headers.get_content_charset(), r.headers.get_content_type()


def get(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            return _get(url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(3 * (i + 1))
    raise last


def decode(raw, charset):
    if not charset:
        m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', raw[:3000])
        charset = m.group(1).decode() if m else "utf-8"
    charset = {"shift_jis": "cp932", "sjis": "cp932", "x-sjis": "cp932", "euc-jp": "euc_jp"}.get(charset.lower(), charset)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def html_text(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer|header)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|dt|dd|table|section)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r　\xa0]+", " ", s)
    return "\n".join(x.strip() for x in s.split("\n") if x.strip())


def pdf_text(raw):
    """日本語のPDFは pdftotext（poppler）のほうが正確に読めるので、あればそちらを使う。"""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(raw)
            f.flush()
            r = subprocess.run(["pdftotext", "-q", "-layout", f.name, "-"], capture_output=True)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", errors="replace")
    import pypdf  # requirements.txt で入れる
    r = pypdf.PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def egov_id(url):
    m = EGOV.match(url)
    return m.group(1) if m else None


def egov_revision(law_id):
    raw, _, _ = get("https://laws.e-gov.go.jp/api/2/law_revisions/" + urllib.parse.quote(law_id))
    d = json.loads(raw)
    revs = d.get("revisions") or []
    if not revs:
        return None, None
    top = revs[0]
    return top.get("law_revision_id"), top


def egov_text(law_id):
    raw, _, _ = get("https://laws.e-gov.go.jp/api/2/law_data/" + urllib.parse.quote(law_id) + "?law_full_text_format=xml")
    d = json.loads(raw)
    x = base64.b64decode(d["law_full_text"]).decode("utf-8", errors="replace")
    x = re.sub(r"</(Sentence|ArticleTitle|ParagraphNum|ItemTitle|ArticleCaption|LawTitle)>", "\n", x)
    x = re.sub(r"<[^>]+>", "", x)
    return re.sub(r"\n\s*\n+", "\n", x)


def fetch_text(url):
    """(種類, 本文) を返す。"""
    lid = egov_id(url)
    if lid:
        return "egov", egov_text(lid)
    raw, cs, ctype = get(url)
    if ctype == "application/pdf" or url.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
        return "pdf", pdf_text(raw)
    return "html", html_text(decode(raw, cs))


def fingerprint(url):
    """変わったかどうかを比べるための指紋。法令は改正ID、それ以外は本文のハッシュ。"""
    lid = egov_id(url)
    if lid:
        rid, top = egov_revision(lid)
        info = ""
        if top:
            info = f"{top.get('amendment_law_title') or ''}（{top.get('amendment_law_num') or ''}、施行 {top.get('amendment_enforcement_date') or '未定'}）"
        return "egov", rid or "", info
    raw, cs, ctype = get(url)
    if ctype == "application/pdf" or url.lower().endswith(".pdf") or raw[:5] == b"%PDF-":
        kind, text = "pdf", pdf_text(raw)
        if len(text.strip()) < 50:  # 文字を取り出せないPDFは、ファイルそのもので比べる
            return "pdf", hashlib.sha256(raw).hexdigest()[:20], ""
    else:
        kind, text = "html", html_text(decode(raw, cs))
    # 日付や件数のような、毎日変わりやすい表示はなるべく無視する
    norm = re.sub(r"\d{4}[年/.-]\d{1,2}[月/.-]\d{1,2}日?(\s*\d{1,2}:\d{2})?", "", text)
    norm = re.sub(r"(更新日|最終更新|アクセス数|閲覧数)[^\n]*", "", norm)
    return kind, hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20], ""


def excerpt(text, hints, limit=12000):
    """長い本文から、ページの中身に関係ありそうな段落を選んで、limit 文字までにする。"""
    if len(text) <= limit:
        return text
    paras = [p for p in re.split(r"\n+", text) if p.strip()]
    toks = set()
    for h in hints:
        toks.update(re.findall(r"[一-鿿]{2,}|\d[\d,.]*", h or ""))
    scored = []
    for i, p in enumerate(paras):
        sc = sum(1 for t in toks if t in p)
        if sc:
            scored.append((sc, i))
    keep = set()
    total = 0
    for sc, i in sorted(scored, reverse=True):
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(paras) and j not in keep:
                keep.add(j)
                total += len(paras[j])
        if total > limit:
            break
    return "\n".join(paras[i] for i in sorted(keep))[:limit]
