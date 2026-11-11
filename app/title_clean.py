"""从文件名或杂乱的内嵌元数据里提取干净的书名，用于刮削查询和扫描时的显示标题。

典型输入：
    Kindle电子书:《三体》            -> 三体
    刘慈欣《三体》 / 《三体》刘慈欣   -> 三体（作者提示：刘慈欣）
    【精校版】三体2：黑暗森林(epub)  -> 三体2:黑暗森林（查询回退：三体2）
    三体 - 刘慈欣 / 三体_刘慈欣      -> 三体（作者提示：刘慈欣）
    Jane Austen - Pride and Prejudice -> Pride and Prejudice
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 来源/营销/格式类的杂词（小写比较；按长度降序匹配，避免“电子书”先于“kindle电子书”命中）
_JUNK_WORDS = [
    # 来源/格式
    "kindle电子书", "kindle电子版", "kindle版", "kindle", "电子书下载", "电子书", "电子版",
    "ebook", "e-book", "epub", "mobi", "azw3", "azw", "pdf", "txt", "cbz", "fb2", "djvu",
    # 版本营销
    "精校版", "精校", "精排版", "精排", "完整版", "完整", "全本", "高清版", "高清", "扫描版",
    "修订版", "校对版", "珍藏版", "文字版", "无删减版", "无删减", "未删减版", "未删减",
    "完结版", "完结", "全文", "免费下载", "免费", "下载", "网盘", "多看版", "多看",
    "排版", "未校对", "已校对", "插图版", "最新版", "典藏版", "畅销版",
    # 常见书源/站点
    "z-library", "z-lib", "zlibrary", "zlib", "1lib", "anna's archive", "annas archive",
    "安娜的档案", "书伴", "bookfere", "鸠摩搜书", "苦瓜书盘", "周读", "ireadweek", "知轩藏书",
    "zxcs", "sobooks", "epubee", "读书小站", "雅书", "好书分享", "微信读书", "掌阅",
    "chinese edition",
]
_JUNK_SET = {w.casefold() for w in _JUNK_WORDS}


def _glue_safe(word: str) -> bool:
    """可以和书名直接粘连的杂词：纯西文词、以“版”“电子书”结尾的词。其余的必须有分隔符才剥离，
    避免把「完整的人」「下载」「全文」这类真实书名的一部分削掉。"""
    return bool(re.fullmatch(r"[a-z0-9' .\-]+", word)) or word.endswith(("版", "电子书", "电子版"))


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_JUNK_ALT = _alt(_JUNK_SET)
_GLUE_ALT = _alt(w for w in _JUNK_SET if _glue_safe(w))
_SEP_CHARS = r"\s:;,，、|/\\._\-—–~·+&"
_JUNK_ONLY_RE = re.compile(rf"^(?:[{_SEP_CHARS}]*(?:{_JUNK_ALT}))+[{_SEP_CHARS}]*$", re.IGNORECASE)
# 西文杂词后面不能紧跟西文字母/数字（避免把 "Pdfs" "Kindleberger" 削掉）
_LEAD_JUNK_RE = re.compile(
    rf"^(?:[{_SEP_CHARS}]*(?:(?:{_GLUE_ALT})(?![a-z0-9])|(?:{_JUNK_ALT})(?=[{_SEP_CHARS}]|$)))+",
    re.IGNORECASE,
)
_TRAIL_JUNK_RE = re.compile(
    rf"(?:(?:(?<![a-z0-9])(?:{_GLUE_ALT})|(?:(?<=[{_SEP_CHARS}])|^)(?:{_JUNK_ALT}))[{_SEP_CHARS}]*)+$",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|cn|cc|me|io|top|xyz|club|info)(?:/\S*)?", re.IGNORECASE)
_ISBN_RE = re.compile(r"(?:isbn[:\s]*)?(?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx](?!\d)", re.IGNORECASE)

# 括注（NFKC 之后全角括号已变为半角；【】〔〕〖〗 保持原样）
_BRACKET_RE = re.compile(r"[\[\(【〔〖{]([^\[\]\(\)【】〔〕〖〗{}]*)[\]\)】〕〗}]")
_BOOK_QUOTE_RE = re.compile(r"《([^《》]+)》")
_AUTHOR_LABEL_RE = re.compile(r"^(?:作者|著者|author|by)\s*[:：]?\s*", re.IGNORECASE)
_AUTHOR_SUFFIX_RE = re.compile(r"\s*(?:\(?(?:著|编著|编|主编|译|等著|等)\)?)$")
_NATION_RE = re.compile(r"^[\[\(【〔]\s*[\u4e00-\u9fff]{1,3}\s*[\]\)】〕]\s*")
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
# 常见中文姓氏（单姓 + 常见复姓），用于判断「两三个汉字」更像人名还是书名
_SURNAMES_1 = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈"
    "姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
    "常温康施文牛樊葛邢安齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕单季"
    "裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆农司卓古吉缪简车项连芦麦褚娄窦"
    "戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻"
    "苑迟邝官封谈匡鞠惠荆乐冀郁胥南班储原栗燕楚鄢劳谌奚皮粟冼蔺楼盘满闻位厉伊仝区郜海阚花权强帅屠豆朴盖练"
    "廉禹井祖漆巴丰支卿国狄平计索宣晋相初门云容敬来扈晁芮都普阙浦戈伏鹿薄邸雍辜羊阿乌母裘亓修邰赫杭况那宿"
    "鲜印逯隆茹诸战慕危玉银亢嵇公哈湛宾戎勾茅利於呼居揭干但尉冶斯元束檀衣信展阴昝智幸奉植衡富尧闭由"
)
_SURNAMES_2 = {"欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "长孙", "宇文", "司徒", "夏侯", "令狐", "端木", "独孤", "南宫", "西门", "百里", "呼延", "轩辕", "太史", "钟离", "澹台", "第五"}
_EDGE_PUNCT = " \t:;,，、|/\\._-—–~·+&!！?？\"'“”‘’「」『』"


@dataclass
class TitleHint:
    title: str | None
    author: str | None = None
    raw: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.title) and _key(self.title) != _key(self.raw)


def _key(text: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def _nfkc(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_edges(text: str) -> str:
    return text.strip(_EDGE_PUNCT).strip()


def is_junk(text: str | None) -> bool:
    """整段都是来源/格式/营销杂词（或空）。"""
    t = _strip_edges(_nfkc(text or ""))
    if not t:
        return True
    if _URL_RE.fullmatch(t):
        return True
    return bool(_JUNK_ONLY_RE.match(t))


def _strip_junk(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    prev = None
    while prev != text:
        prev = text
        text = _strip_edges(text)
        text = _LEAD_JUNK_RE.sub("", text)
        text = _TRAIL_JUNK_RE.sub("", text)
    return _strip_edges(re.sub(r"\s+", " ", text))


def _clean_author(text: str | None) -> str | None:
    if not text:
        return None
    t = _strip_edges(_nfkc(text))
    t = _AUTHOR_LABEL_RE.sub("", t)
    t = _NATION_RE.sub("", t)
    t = _AUTHOR_SUFFIX_RE.sub("", t)
    t = _strip_junk(t)
    t = _strip_edges(t)
    if not t or is_junk(t):
        return None
    return t


def looks_like_name(text: str | None) -> bool:
    """粗略判断是否像人名：中文 2-5 字（可含 ·），或 1-4 个西文单词。"""
    t = _strip_edges(_nfkc(text or ""))
    if not t or is_junk(t) or any(ch.isdigit() for ch in t):
        return False
    if _AUTHOR_LABEL_RE.match(t) or _AUTHOR_SUFFIX_RE.search(t) and len(t) > 1:
        return True
    if _NATION_RE.match(t):
        return True
    if _CJK_RE.search(t):
        core = t.replace("·", "").replace("•", "").replace(" ", "")
        if not all(_CJK_RE.match(c) for c in core):
            return False
        if "·" in t or "•" in t:  # 译名：卡勒德·胡赛尼
            return 3 <= len(core) <= 12
        if not 2 <= len(core) <= 4:
            return False
        return core[0] in _SURNAMES_1 or core[:2] in _SURNAMES_2
    words = t.split()
    return 1 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*,?", w) for w in words)


def _has_author_marker(text: str) -> bool:
    t = _strip_edges(_nfkc(text))
    return bool(_AUTHOR_LABEL_RE.match(t) or _NATION_RE.match(t) or re.search(r"(?:著|编著|主编|等著)\)?$", t))


def _split_title_author(left: str, right: str) -> tuple[str, str | None]:
    """把「A - B」两段判成 (书名, 作者)。"""
    if is_junk(left):
        return right, None
    if is_junk(right):
        return left, None
    lm, rm = _has_author_marker(left), _has_author_marker(right)
    if lm and not rm:
        return right, left
    if rm and not lm:
        return left, right
    ln, rn = looks_like_name(left), looks_like_name(right)
    cjk = bool(_CJK_RE.search(left + right))
    if cjk:
        # 中文命名习惯以「书名 - 作者」为主
        if rn and not ln:
            return left, right
        if ln and not rn:
            return right, left
        return left, right
    # 西文：沿用旧规则「较短的一侧更像作者」（Calibre 风格 Author - Title）
    if ln and not rn:
        return right, left
    if rn and not ln:
        return left, right
    if len(left) <= len(right):
        return right, left
    return left, right


def _drop_brackets(text: str) -> tuple[str, str | None]:
    """去掉括注；若括注里写的是作者（“作者：X”“X 著”），作为作者提示返回。"""
    author: str | None = None

    def repl(m: re.Match) -> str:
        nonlocal author
        inner = m.group(1)
        if author is None and not is_junk(inner):
            if _has_author_marker(inner):
                author = _clean_author(inner)
            elif m.start() == 0 and looks_like_name(inner):
                # 开头的「[刘慈欣]三体」
                author = _clean_author(inner)
        return " "

    prev = None
    while prev != text:
        prev = text
        text = _BRACKET_RE.sub(repl, text)
    # 不成对的残余括号
    text = re.sub(r"[\[\]\(\)【】〔〕〖〗{}]", " ", text)
    return re.sub(r"\s+", " ", text).strip(), author


def _clean_plain(text: str) -> tuple[str | None, str | None]:
    """没有书名号时的清洗。返回 (书名, 作者提示)。"""
    text, author = _drop_brackets(text)
    text = _ISBN_RE.sub(" ", text)
    text = _strip_junk(text)
    if not text:
        return None, author

    # 冒号：左边是杂词前缀就丢掉；否则视为「主标题:副标题」保留
    if ":" in text:
        left, sep, right = re.split(r"(\s*:\s*)", text, maxsplit=1)
        if is_junk(left) or _strip_junk(left) == "":
            text = _strip_junk(right)
        elif is_junk(right):
            text = _strip_junk(left)
        else:
            # 保留原有写法：西文「Dune: Messiah」，中文「三体2:黑暗森林」
            sep = ": " if sep.endswith(" ") or not _CJK_RE.search(left) else ":"
            text = f"{_strip_junk(left)}{sep}{_strip_junk(right)}"

    # 「作者 - 书名」「书名 - 作者」「书名——作者」
    parts = re.split(r"\s+[-–—]+\s+|\s*——\s*", text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        title, hint = _split_title_author(_strip_junk(parts[0]), _strip_junk(parts[1]))
        text = title
        author = author or _clean_author(hint)
    elif "_" in text:
        segs = [s for s in (_strip_junk(x) for x in text.split("_")) if s]
        if len(segs) == 2:
            title, hint = _split_title_author(segs[0], segs[1])
            text = title
            author = author or _clean_author(hint)
        else:
            text = " ".join(segs)
    elif author is None and " " in text:
        # 「活着 余华」：两段纯中文，其中恰好一段像人名
        segs = text.split(" ")
        if len(segs) == 2 and all(_CJK_RE.fullmatch(c) for seg in segs for c in seg):
            a_name, b_name = looks_like_name(segs[0]), looks_like_name(segs[1])
            if b_name and not a_name:
                text, author = segs[0], segs[1]
            elif a_name and not b_name:
                text, author = segs[1], segs[0]
    text = _strip_junk(text)
    return (text or None), author


def clean_title(raw: str | None) -> TitleHint:
    """清洗书名。尽量保守：拿不准时保留原文（只做空白规范化）。"""
    if not raw:
        return TitleHint(title=None, raw=raw)
    text = _nfkc(raw).replace("\u3000", " ")
    m = _BOOK_QUOTE_RE.search(text)
    if m:
        inner, _ = _clean_plain(m.group(1))
        inner = inner or _strip_edges(m.group(1))
        outside = (text[: m.start()] + " " + text[m.end():])
        outside = _BOOK_QUOTE_RE.sub(" ", outside)
        outside, bracket_author = _drop_brackets(outside)
        outside = _ISBN_RE.sub(" ", outside)
        # 外面的部分按分隔符切开，挑一个像作者的
        author = bracket_author
        if author is None:
            for seg in re.split(r"[:;,，、|/_\-—–~]+|\s{1,}", outside):
                seg = _strip_junk(seg)
                if seg and not is_junk(seg) and (looks_like_name(seg) or _has_author_marker(seg)):
                    author = _clean_author(seg)
                    if author:
                        break
        return TitleHint(title=inner or None, author=author, raw=raw)
    title, author = _clean_plain(text)
    if not title:
        # 全是杂词：退回原文的规范化版本，避免丢失信息
        title = _strip_edges(text) or None
    return TitleHint(title=title, author=author, raw=raw)


def main_title(title: str | None) -> str | None:
    """去掉冒号/破折号后的副标题。"""
    if not title:
        return None
    head = re.split(r"\s*[:：]\s*|\s+[-–—]+\s+|\s*——\s*", _nfkc(title), maxsplit=1)[0]
    head = _strip_edges(head)
    return head or None


def query_titles(title: str | None) -> list[str]:
    """刮削查询用的书名序列：先清洗后的全名，再逐步缩短。"""
    if not title:
        return []
    hint = clean_title(title)
    out: list[str] = []

    def add(value: str | None) -> None:
        value = _strip_edges(_nfkc(value or ""))
        if value and value.casefold() not in {v.casefold() for v in out}:
            out.append(value)

    if not is_dirty_title(title):
        # 看起来正常的标题（如 "Sapiens - A Brief History"）先按原样查，查不到再用清洗结果
        add(title)
    add(hint.title)
    add(main_title(hint.title))
    # 「三体2」这类带卷号的再去掉卷号
    mt = main_title(hint.title) or ""
    stripped = re.sub(r"\s*(?:\d+|[一二三四五六七八九十]+|第[一二三四五六七八九十\d]+[部卷册集])$", "", mt)
    if stripped and stripped != mt and len(stripped) >= 2:
        add(stripped)
    if not out:
        add(title)
    return out


def scoring_title(title: str | None) -> str | None:
    """打分用的书名：脏标题用清洗结果，正常标题保持原样。"""
    if title and is_dirty_title(title):
        return clean_title(title).title or title
    return title


def is_dirty_title(title: str | None) -> bool:
    """内嵌标题是否明显是“文件名式”的脏标题（含书名号、来源杂词、格式词、括注杂词等）。"""
    if not title:
        return False
    hint = clean_title(title)
    if not hint.title or not hint.changed:
        return False
    t = _nfkc(title)
    if _BOOK_QUOTE_RE.search(t):
        return True
    if _URL_RE.search(t) or _ISBN_RE.search(t):
        return True
    # 清洗掉的部分里有杂词，才认为是脏标题（避免把正常的「作者 - 书名」式标题改坏）
    low = t.casefold()
    return any(w in low for w in _JUNK_SET if len(w) >= 2)
