# -*- coding: utf-8 -*-
"""
看图看懂梗 · 谐音（v0.3.0）：图里的字 → 拼音 → 可能的谐音。本地查，不调模型、不花钱（不依赖 AstrBot，单独能测）

为什么：中文梗图最难的是谐音（「你可真是夺笋啊」= 多损，「我太南了」= 我太难了）。
PunMemeCN（EMNLP 2025，1959 张中文梗图，其中 653 张是双关梗）：最强的看图模型也常常认不出谐音背后的那个词；
论文里给模型加上图里文字的拼音，也基本没用 —— 所以这里不光给拼音，还直接给「可能是 A = B」这种候选。

怎么查：
  1 字面：图里的字里有下面词表里的谐音写法（「夺笋」「蚌埠住了」「栓Q」「520」）→ 候选
  2 读音：图里连着几个字跟词表里的原词读音一样（不管声调）、字又不一样 → 候选
     - 三个字以上的原词才按读音找（两个字的太容易撞上正常的词，只认字面写法）
     - 四个字以上的允许一处近音：n/l、平翘舌（z/zh c/ch s/sh）、前后鼻音（an/ang en/eng in/ing）、f/hu（「灰常」= 非常）
  3 看图模型自己已经写出来的（梗那项里已经有「多损」）不再重复给
拼音：用 pypinyin 的单字读音表（多音字的几个读音都认；只读它的数据文件，不 import 整个库 —— 那样要多占 50MB 内存）。
  没装 pypinyin：只按字面写法认，不按读音找、也不写读音。

词表（TABLE）：作者按常识整理的常见网络谐音、成语 / 吉祥话的谐音（广告、表情包里常见）、外语和数字的谐音。
都是网上公开流传的说法，一条一句，不是从哪本词典或哪个网站成段抄的。不收脏话、黄的。
格式：原词|谐音写法（空格隔开，可以没有）|说明（可以没有）
"""

import json
import re
import unicodedata
from pathlib import Path

TABLE = """
# 网络上常见的谐音
多损|夺笋
难受想哭|蓝瘦香菇
太难了|太南了 太蓝了
压力山大|鸭梨山大
与你无关|雨女无瓜
不知道|布吉岛
真的假的|尊嘟假嘟
太酷啦|泰裤辣
绷不住了|蚌埠住了
一坨大便|依托答辩|骂东西烂
我不理解|我不李姐
怎么回事|怎么肥四 怎么肥事
我服了|我佛了
姐妹|集美
喜欢|稀饭
什么|神马 虾米 啥米
怎么|肿么
这样子|酱紫
不要这样子|表酱紫
不开心|伐开心
有没有|有木有
没有|木有
同学|童鞋
朋友|盆友
姑娘|菇凉
男孩子|蓝孩子 男孩纸
女孩子|女孩纸
强烈|墙裂
非常|灰常
长知识|涨姿势
激动|鸡冻
泪流满面|内牛满面
悲剧|杯具
和谐|河蟹|被和谐 = 被删、被屏蔽
你知道吗|你造吗
那味儿|内味
那个|内个
这个|介个
人家|银家
我的神|额滴神
举个例子|举个栗子
厉害了我的哥|厉害了word哥
出大事了|粗大事了
出来|粗来
杀了我|鲨了我
祝你|猪你
数你最美|鼠你最美
加油呀|加油鸭
冲呀|冲鸭
愉快|鱼快
余生|鱼生
与你|鱼你
# 职业的谐音叫法
程序员|程序猿 程序媛
工程师|攻城狮
设计师|射鸡师
# 成语、吉祥话的谐音（广告、过年过节的表情包里常见）
机不可失|鸡不可失
默默无闻|默默无蚊
百依百顺|百衣百顺
刻不容缓|咳不容缓
其乐无穷|骑乐无穷
一往情深|一网情深
无稽之谈|无鸡之谈
百感交集|百感交鸡
有备无患|有杯无患
天长地久|天长地酒
贤妻良母|闲妻良母
随心所欲|随心所浴
步步为营|步步为赢
十全十美|食全食美
一见钟情|衣见钟情
别来无恙|鳖来无恙
马到成功|码到成功
前途无量|钱途无量
前途似锦|前兔似锦 钱途似锦
大展宏图|大展宏兔
前途|钱途
心想事成|鑫想事成
万事如意|万柿如意
事事如意|柿柿如意
年年有余|年年有鱼
连年有余|莲年有鱼
岁岁平安|碎碎平安
早生贵子|枣生桂子
大吉大利|大鸡大利
喜气洋洋|喜气羊羊
三阳开泰|三羊开泰
扭转乾坤|牛转乾坤
时来运转|蛇来运转
福到了|福倒了
# 外语的谐音（写成中文字）
thank you|栓Q 三克油 3Q|谢谢，常带点无奈
barbecue|芭比Q|烧烤；「芭比Q了」= 完了
go die|狗带|去死，开玩笑的说法
sorry|骚瑞|对不起
interesting|因吹斯听|有意思
happy|哈皮|开心
I love you|爱老虎油|我爱你
nice|耐撕|好
OK 了|欧了
だめ|达咩|日语：不行
すごい|斯国一|日语：厉害
かわいい|卡哇伊|日语：可爱
ありがとう|阿里嘎多|日语：谢谢
さようなら|撒由那拉|日语：再见
わたし|瓦塔西|日语：我
好犀利|猴赛雷|粤语：好厉害
你好|雷猴|粤语
# 数字的谐音
我爱你|520 521
一生一世|1314
我爱你一生一世|5201314
拜拜了|886
呜呜呜|555|哭
溜|666|很厉害
气死我了|7456
就是就是|9494
我想你|530
二百五|250|骂人傻
去死吧|748|骂人的
"""

# 汉字（基本区 + 扩展 A + 兼容区）
_HAN_RANGE = "%s-%s%s-%s" % (chr(0x3400), chr(0x9FFF), chr(0xF900), chr(0xFAFF))
_HAN = re.compile("[" + _HAN_RANGE + "]")
_TONE = str.maketrans("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹḿ", "aaaaeeeeiiiioooouuuuvvvvvnnnm")
# 词表里原词用到的字的繁体：繁体写的原词（「壓力山大」「怎麼回事」）不是谐音，别当成谐音
_TRAD = dict(
    zip(
        "難壓與無繃麼這樣開沒長識淚滿嗎兒舉個厲殺數員師設計機聞順緩樂窮談備賢隨營見鍾鐘別馬錦圖萬餘連歲貴氣陽轉時運愛嗚關為來說們",
        "难压与无绷么这样开没长识泪满吗儿举个厉杀数员师设计机闻顺缓乐穷谈备贤随营见钟钟别马锦图万余连岁贵气阳转时运爱呜关为来说们",
    )
)
# 数字谐音别把价钱、日期、数量当成谐音（「250 克」「88 元」「第 520 期」）
_UNIT_AFTER = set("元块角毛分克斤两米个只条件号年月日点时秒折倍度岁人次份张楼层期%‰")
_UNIT_BEFORE = set("¥$￥#第号")
_R = {"d": None, "how": ""}  # 单字读音表：{码位: "nán,nā"}（第一个最常用）
_T = {"key": None, "rows": None}  # 词表（加上用户自己的）解析好的


def _load():
    """单字读音表。先直接读 pypinyin 的数据文件（不 import 整个库：省 50MB 内存，也不动别的插件用的 pypinyin）；
    读不到（以后换了地方 / 老版本）→ 退回正常 import；都不行 → 空表（只按字面写法认）"""
    if _R["d"] is not None:
        return _R["d"]
    d, how = {}, "没装 pypinyin"
    try:
        import importlib.util

        spec = importlib.util.find_spec("pypinyin")
        loc = list(spec.submodule_search_locations or []) if spec else []
        p = Path(loc[0]) / "pinyin_dict.json" if loc else None
        if p is not None and p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            d = {int(k): str(v) for k, v in raw.items()}
            how = "pypinyin 的单字读音表"
        elif spec is not None:
            from pypinyin import pinyin_dict  # 老版本：数据直接写在 .py 里

            d = {int(k): str(v) for k, v in pinyin_dict.pinyin_dict.items()}
            how = "pypinyin"
    except Exception as e:  # 装坏了：当没装
        d, how = {}, f"pypinyin 读不出来（{e!r}"[:80] + "）"
    _R["d"], _R["how"] = d, how
    _T["key"] = None  # 读音表变了：词表重新算
    return d


def ready():
    """能不能按读音找（装了 pypinyin）"""
    return bool(_load())


def how():
    _load()
    return _R["how"]


def _reads(ch):
    """一个字的几个读音（带声调，第一个最常用）；不是汉字 / 查不到 → ()"""
    v = _load().get(ord(ch)) if len(ch) == 1 else None
    return tuple(x for x in v.split(",") if x) if v else ()


def plain(py):
    """去掉声调：nǚ → nv、lüè → lve"""
    s = str(py).translate(_TONE)
    if not s.isascii():
        s = "".join(c for c in unicodedata.normalize("NFD", s) if c.isascii())
    return s


def near(s):
    """近音归成一类：平翘舌、n/l、f/hu、前后鼻音（「我太蓝了」= 我太难了、「灰常」= 非常）"""
    if s[:2] in ("zh", "ch", "sh"):
        s = s[0] + s[2:]
    elif s[:1] == "n" and s[:2] != "ng":
        s = "l" + s[1:]
    if s[:2] == "hu":
        r = s[2:]
        s = "f" + {"": "u", "i": "ei", "n": "en"}.get(r, r)
    for a, b in (
        ("iang", "ian"),
        ("uang", "uan"),
        ("ang", "an"),
        ("eng", "en"),
        ("ing", "in"),
    ):
        if s.endswith(a):
            return s[: -len(a)] + b
    return s


def _one(c):
    """一个字 → 拿来比的样子：全角变半角、英文变小写（「栓Ｑ」「５２０」也认），一个字还是一个字"""
    return ((unicodedata.normalize("NFKC", c)[:1] or c).lower()[:1]) or c


def _compact(raw):
    """去掉空格和标点（「蓝瘦，香菇」也认得）→ (比的字串, 每个字在原文里的位置)"""
    keep, pos = [], []
    for k, c in enumerate(str(raw)):
        o = _one(c)
        if o.isalnum() or _HAN.match(o):
            keep.append(o)
            pos.append(k)
    return "".join(keep), pos


def parse(lines):
    """词表的行（「原词|写法 写法|说明」）/ 用户填的（「夺笋=多损」「多损」）→ [(原词, [写法], 说明)]"""
    out = []
    for raw in lines or ():
        s = str(raw or "").strip()
        if not s or s.startswith("#"):
            continue
        if "|" in s:
            parts = [x.strip() for x in s.split("|")]
            tgt, forms = parts[0], (parts[1].split() if len(parts) > 1 else [])
            note = parts[2] if len(parts) > 2 else ""
        else:  # 用户自己加的：「谐音写法=原词」，或者只写原词（按读音找）
            s = s.replace("＝", "=")
            if "=" in s:
                f, tgt = (x.strip() for x in s.split("=", 1))
                forms = [f] if f else []
            else:
                tgt, forms = s, []
            note = ""
        if tgt:
            out.append((tgt[:20], [f[:20] for f in forms if f], note[:30]))
    return out


def _rows(extra=()):
    """解析好的词表：[{tgt, forms, note, reads（原词每个字的读音）, sound（能不能按读音找）}]"""
    _load()
    key = (tuple(extra or ()), bool(_R["d"]))
    if _T["key"] == key and _T["rows"] is not None:
        return _T["rows"]
    rows = []
    for tgt, forms, note in parse(TABLE.splitlines()) + parse(extra):
        reads = [_reads(c) for c in tgt]
        rows.append(
            {
                "tgt": tgt,
                "forms": [_compact(f)[0] for f in forms],
                "note": note,
                "reads": reads,
                "sound": len(tgt) >= 3 and all(reads),
            }
        )
    _T["key"], _T["rows"] = key, rows
    return rows


def _edge_ok(t, i, j, form):
    """字面写法两头是英文 / 数字的：前后不能紧挨着别的英文数字（「3Q」别从「13Q」里认）；纯数字的还不能是价钱、数量、日期"""
    a = t[i - 1] if i > 0 else ""
    b = t[j] if j < len(t) else ""
    if form[:1].isascii() and form[:1].isalnum() and a.isascii() and a.isalnum():
        return False
    if form[-1:].isascii() and form[-1:].isalnum() and b.isascii() and b.isalnum():
        return False
    if form.isdigit() and (b in _UNIT_AFTER or a in _UNIT_BEFORE):
        return False
    return True


def _match(win_reads, row, n_near):
    """一段字的读音 跟原词逐字对：每个字要么读音一样（不管声调），要么近音（最多 n_near 处）
    → (近音几处, [(写的字的读音, 原词的字的读音)])；对不上 → None。读音按最常用的先对"""
    near_n, pairs = 0, []
    for ra, rb in zip(win_reads, row["reads"]):
        pa, pb = {}, {}
        for x in ra:
            pa.setdefault(plain(x), x)
        for x in rb:
            pb.setdefault(plain(x), x)
        hit = next((k for k in pa if k in pb), None)
        if hit is not None:
            pairs.append((pa[hit], pb[hit]))
            continue
        na, nb = {}, {}
        for k in pa:
            na.setdefault(near(k), k)
        for k in pb:
            nb.setdefault(near(k), k)
        hit = next((k for k in na if k in nb), None)
        if hit is None:
            return None
        near_n += 1
        if near_n > n_near:
            return None
        pairs.append((pa[na[hit]], pb[nb[hit]]))
    return near_n, pairs


def find(text, extra=(), most=3, skip=None):
    """图里的字 → 可能的谐音 [{form 图里写的, tgt 原词, note, rw 写的读音, rt 原词读音, pos 在原文里的位置}]
    长的先、不重叠、最多 most 个；skip(原词) 为真的不要（看图模型自己已经写出来的）"""
    raw = str(text or "")[:120]
    t, where = _compact(raw)
    if not t:
        return []
    rows = _rows(extra)
    tgts_all = {r["tgt"] for r in rows}
    reads = [_reads(c) if _HAN.match(c) else () for c in t]
    found = []
    for row in rows:
        L = len(row["tgt"])
        for f in row["forms"]:  # 1 字面
            if not f or f == row["tgt"]:
                continue
            i = t.find(f)
            while i >= 0:
                j = i + len(f)
                if _edge_ok(t, i, j, f):
                    pairs = None
                    if j - i == L and all(reads[i:j]) and all(row["reads"]):
                        # 字数一样：按读音对一下，读音写对上的那个（「蚌埠」按 bèng bù，不按最常用的 bàng）
                        got = _match(reads[i:j], row, L)
                        pairs = got[1] if got else None
                    found.append(
                        {"i": i, "j": j, "row": row, "near": 0, "pairs": pairs}
                    )
                i = t.find(f, i + 1)
        if not row["sound"]:  # 2 读音（三个字以上的原词）
            continue
        n_near = 0 if L < 4 else (1 if L < 6 else 2)
        for i in range(0, len(t) - L + 1):
            win = "".join(_TRAD.get(c, c) for c in t[i : i + L])
            diff = sum(1 for a, b in zip(win, row["tgt"]) if a != b)
            # 一个字都没换（只是繁体）不算；三个字的只认换了一个字的（「台南了」不当成「太难了」）；
            # 图上写的本身就是词表里的另一个原词（「年年有余」）不算「连年有余」的谐音
            if not diff or (L == 3 and diff > 1) or win in tgts_all:
                continue
            if not all(reads[i : i + L]):
                continue
            got = _match(reads[i : i + L], row, n_near)
            if got:
                found.append(
                    {"i": i, "j": i + L, "row": row, "near": got[0], "pairs": got[1]}
                )
    found.sort(key=lambda x: (-(x["j"] - x["i"]), x["near"], x["i"]))
    out, used, tgts = [], set(), set()
    for x in found:
        span = set(range(x["i"], x["j"]))
        if span & used or x["row"]["tgt"] in tgts:
            continue
        if skip is not None and skip(x["row"]["tgt"]):
            continue
        used |= span
        tgts.add(x["row"]["tgt"])
        out.append(_view(raw, t, where, x))
        if len(out) >= most:
            break
    out.sort(key=lambda x: x["pos"][0])
    return out


def _view(raw, t, where, x):
    """→ 给外面用的：图里写的那几个字（原样，带中间的标点）、原词、两边的读音"""
    row, i, j = x["row"], x["i"], x["j"]
    form = raw[where[i] : where[j - 1] + 1]
    if x["pairs"]:
        rw = " ".join(a for a, _b in x["pairs"])
        rt = " ".join(b for _a, b in x["pairs"])
    elif all(_reads(c) for c in t[i:j]) and all(row["reads"]):
        rw = " ".join(_reads(c)[0] for c in t[i:j])
        rt = " ".join(r[0] for r in row["reads"])
    else:  # 带英文 / 数字的、原词是外语的：不写读音
        rw, rt = "", ""
    return {
        "form": form,
        "tgt": row["tgt"],
        "note": row["note"],
        "rw": rw,
        "rt": rt,
        "pos": where[i:j],
    }


# 图里的字：看图结果里的「字：「…」」（深度认的词条也是这个写法）；「名字：」「文字：」不算
_TEXT_AT = re.compile("(?<![" + _HAN_RANGE + "])字\\s*[:：]\\s*")
_QUOTES = {"「": "」", "“": "”", '"': '"', "『": "』", "《": "》"}


def image_text(cap):
    """看图结果 → 图里的字（没有 → ""）"""
    s = str(cap or "")
    m = _TEXT_AT.search(s)
    if not m:
        return ""
    rest = s[m.end() :]
    if rest[:1] in _QUOTES:
        op, cl, depth = rest[0], _QUOTES[rest[0]], 0
        for k, c in enumerate(rest):
            if c == op and (op != cl or k == 0):
                depth += 1
            elif c == cl:
                depth -= 1
                if depth <= 0:
                    return rest[1:k].strip()
        return rest[1:].split("。")[0].strip()
    return re.split("[。；;\n]", rest, maxsplit=1)[0].strip()


def reading(text, found=()):
    """整段字的读音（汉字写拼音、英文数字照写、标点去掉）；认出谐音的那几个字用对上的那个读音"""
    raw = str(text or "")
    fixed = {}
    for f in found or ():
        rs = (f.get("rw") or "").split()
        if rs and len(rs) == len(f.get("pos") or ()):
            fixed.update(zip(f["pos"], rs))
    out, word = [], ""
    for k, c in enumerate(raw):
        r = _reads(c) if _HAN.match(c) else ()
        if r:
            if word:
                out.append(word)
                word = ""
            out.append(fixed.get(k, r[0]))
        elif _one(c).isascii() and _one(c).isalnum():  # 全角的英文数字也照写
            word += _one(c) if not c.isascii() else c
        elif word:
            out.append(word)
            word = ""
    if word:
        out.append(word)
    return " ".join(out)


def note(cap, extra=(), most=3):
    """看图结果 → 给机器人看的那句谐音（只写事实：图里的字怎么读、可能是哪个词）；没有字 / 查不到 → ""
    看图模型自己已经写出原词的（梗那项里有「多损」）、原词就写在图上的，不再给"""
    text = image_text(cap)
    if not text:
        return ""
    rest = str(cap).replace(text, "", 1)
    found = find(
        text,
        extra,
        most,
        skip=lambda tgt: tgt.split("（")[0] in rest or tgt in text,
    )
    if not found:
        return ""
    han = len(_HAN.findall(text))
    whole = reading(text, found) if 0 < han <= 12 and ready() else ""
    pairs = []
    for f in found:
        s = f["form"]
        if f["rw"] and not whole:
            s += f"（{f['rw']}）"
        s += f" = {f['tgt']}"
        more = [x for x in (f["rt"], f["note"]) if x]
        if more:
            s += "（" + "；".join(more) + "）"
        pairs.append(s)
    head = f"图里的字读作「{whole}」，" if whole else ""
    return "谐音（按读音查的）：" + head + "可能是 " + "、".join(pairs)
