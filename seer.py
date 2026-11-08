# -*- coding: utf-8 -*-
"""
看图看懂梗：提示词、解析、给机器人的「看到了什么」「这一轮的图」（不依赖 AstrBot，单独能测）

来自「大肥鱼」QQ 机器人 v1.5.0 的看图：以前识图只写「画了什么」，群友拿长得像机器人的图逗它、
发历史事件的地狱梗、发擦边图挖坑，它都看不懂 —— 回出来像客服：先把画面复述一遍，再表个态。
现在看图要写五样：字 / 画面 / 梗（出处、在玩什么）/ 跟你（图里有没有它、是不是在拿它开涮）/ 坑（擦边、挖坑、钓鱼），
再给机器人「这一轮的图」：梗点、图在不在说它、有没有坑（v0.3.0 起只写事实，不写怎么回）。
v0.3.0：一条消息里几张图按位置标「第几张」（slot_label）；「今天」按配置的时区算（make_tz / day_of）。
"""

import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

_FIELD = re.compile(r"(梗|跟你|坑)：([^。\]\n]+)")
# 模型会写「坑：没坑」「坑：没有，纯属玩梗」「跟你：没关系」—— 这些都算没有
_NONE = re.compile(
    r"^(无|没|不是|不清楚|看不出|与你无|和你无|跟你无|纯(属)?(搞笑|玩梗|逗)|就是(逗|玩|搞笑))"
)
_LEAD = re.compile(r"^(这是|图中是|图片中?是?|画面中?是?)一(张|幅)[^，,。]*?[，,。]")
# QQ 的图片文件名一般是图片内容的哈希（同一张图再发一次名字不变）—— 只有长得像哈希的才拿来当「同一张图」的凭据
_HASHY = re.compile(r"\{?[0-9A-Fa-f][0-9A-Fa-f-]{15,}\}?(\.[A-Za-z0-9]{1,5})?")


def key_ok(file):
    """图片的 file 字段能不能直接当缓存的键（「image.jpg」「0.png」这种不行，会撞）"""
    return bool(file) and bool(_HASHY.fullmatch(str(file)))


def who_line(names, look, ref=False):
    names = (
        "、".join(
            dict.fromkeys(str(x).strip() for x in (names or []) if str(x).strip())
        )
        or "这个机器人"
    )
    s = (
        f"看图的是群聊机器人「{names}」"
        + (f"，它长这样：{look}" if look else "")
        + "。"
    )
    if look or ref:
        s += SELF_RULE
    return s


# v0.3.2（借作者自己的 QQ 机器人 11-06 v1.17.23–24）：以前写的是「长得像它的角色就是在说它」——
#   只对上发色就认成它（别的蓝发角色被认成机器人自己）；给了它的长相以后，蓝色的小动物也往它身上靠（猫猫虫认成「小鲸鱼」）
SELF_RULE = (
    "图里画的是它本人（它的 Q 版、群友给它画的图）、或者图里写着它的名字 / 外号，就是在说它。"
    "认长相要对上它至少两样明显的特征（发色发型、头饰、耳朵、尾巴、标志性的配饰这些）才算是它；只对上一样（只是发色像、只是衣服像）不算。"
    "它的长相只拿来认它本人：别的角色、动物照常按你认得的写（是哪个角色 / 哪种动物就写哪个），别因为颜色、形状有点像就往它身上靠。"
)
# v0.3.2（借作者自己的 QQ 机器人 11-06 v1.17.21–22）：动图拼图角上的帧号「1、2、3、4」、角落的「AI生成」水印，看图模型会照抄成图里的字
TEXT_NOT = "（动图拼成的那张：角上的小数字是帧的顺序，不是图里的字；角落里「AI生成」这种水印也不算字）"
_FRAME_NUMS = re.compile(r"^\s*\d{1,2}(\s*[、,，.。\s]\s*\d{1,2}){2,}\s*$")
_WATERMARK = re.compile(r"[（(]?\s*AI\s*生成\s*[)）]?", re.I)


def clean_text(t):
    """图里的字：动图拼图的帧号（1、2、3、4、5、6）、单独一段的「AI生成」水印不算（v0.3.2）"""
    t = str(t or "").strip()
    q = "「」“”\"'"
    if _FRAME_NUMS.match(t.strip(q)):
        return ""
    segs = [
        x
        for x in re.split(r"\s*[；;/|｜]\s*", t)
        if x.strip(q + " ") and not _WATERMARK.fullmatch(x.strip(q + " "))
    ]
    return "；".join(segs).strip()


def build_prompt(
    names=(),
    look="",
    ctx=None,
    ref=False,
    note="",
    kind="",
    max_chars=180,
    deep=False,
    usage=(),
):
    """给看图的模型的话。
    ctx：{"sender": 谁发的, "to_bot": 是不是冲着机器人来的, "said": 发图那条消息里的字,
          "ask": 后来 @ 机器人时说的话, "chat": [发图之前的几句], "place": 「QQ 群」「私聊」这种,
          "via": 「转发的聊天记录」（图是转发的聊天记录里的）}
    ref = 第一张是机器人的头像（对照用）；note = 动图说明；kind = 「表情包」「QQ 表情「狗头」」这种
    deep = 这次是给「表情包词典」认真认一次（见 deep_rules）；usage = 群里真人几次发它时前后在说什么"""
    ctx = ctx or {}
    place = str(ctx.get("place") or "群聊")
    via = str(ctx.get("via") or "")
    p = (
        f"这是{' ' if place[:1].isascii() else ''}{place}里"
        + (f"有人发的{via}中" if via else "")
        + (f"「{ctx['sender']}」" if ctx.get("sender") else "别人")
        + "发的一张"
        + (kind or "图")
        + ("（是发给机器人看的 / 冲着它来的）" if ctx.get("to_bot") else "")
        + "。\n"
        + who_line(names, look, ref)
        + "\n"
    )
    if ref:
        p += "第一张是机器人自己的头像（只是给你对照用的）；要描述的是第二张。\n"
    said = str(ctx.get("said") or "").strip()[:80]
    if said:
        p += (
            "聊天记录里这条消息的字" if via else "发图的时候还说了"
        ) + f"：「{said}」\n"
    ask = str(ctx.get("ask") or "").strip()[:80]
    if ask and ask != said:
        p += f"后来有人找机器人说：「{ask}」\n"
    chat = [str(x)[:60] for x in (ctx.get("chat") or []) if x][-4:]
    if chat:
        p += (
            ("聊天记录里这张图前面几句：" if via else "发这张图之前大家在聊：")
            + " / ".join(chat)
            + "\n"
        )
    if deep:
        return p + deep_rules(usage, note)
    p += (
        "用中文写一段话 让看不到图的机器人也能接话（不分行 不说「这是一张图片」）按这个顺序：\n"
        "字：图里的文字原样抄（没有就不写这项）" + TEXT_NOT + "\n"
        "画面：有什么、谁在干什么（只写要紧的：动作 表情 姿势 场景）\n"
        "梗：这是什么梗 / 出处 / 在玩什么（影视截图、历史事件、网络模板比如「找一车X来弄你」「给X什么都做」都点名说清；"
        "认不出就写「看不出出处」 别编）\n"
        "跟你：图里画的就是它本人（长相对上至少两样明显的特征），或者图里的字明着说 AI、token、机器人、它的名字 才写（别从聊天里硬往它身上扯；没关系就整项不写）\n"
        "坑：有擦边、挖坑、钓鱼、等它上当的地方才写（没有就整项不写 别写「坑：没有」）\n"
        f"格式：字：「…」。画面：…。梗：…。跟你：…。坑：…。总共不超过 {int(max_chars)} 字。"
    )
    if note:
        p += (
            f"\n注意：这是一张{note}。画面那项当成一个动作过程来说（一开始…然后…最后…）"
            "不要一格一格分开讲 不要提「拼图」「第几帧」。"
        )
    return p


_THINK = re.compile(r"<(think|thinking)>.*?</\1>", re.S | re.I)
_REFUSE = re.compile(
    r"^(抱歉|对不起|很抱歉|不好意思|我(无法|不能|看不到|没法)|无法(查看|识别|看到|处理)|作为(一个)?(AI|人工智能)|"
    r"I'?m sorry|Sorry|I can(no|'?)t|I am (not able|unable)|As an AI)",
    re.I,
)


def refused(cap):
    """看图模型拒绝了 / 说它看不到图（这种不算看懂，也别记住）"""
    cap = str(cap or "").strip()
    return bool(_REFUSE.match(cap)) and "画面：" not in cap


def trim(cap, hard=260):
    """按句子收尾 不在半句话中间砍断（顺手去掉推理模型漏出来的 <think>…</think>）"""
    cap = _THINK.sub("", cap or "")
    cap = re.sub(r"\s+", " ", cap).strip()
    cap = _LEAD.sub("", cap).strip()
    if len(cap) <= hard:
        return cap.rstrip("。")
    cut = cap[:hard]
    i = max(cut.rfind(x) for x in "。！？；，,")
    return (cut[:i] if i > hard * 0.5 else cut).rstrip("。，,") + "…"


def fields(caps):
    """几条描述里的 梗 / 跟你 / 坑（「没有」那种不算）"""
    out = {"梗": [], "跟你": [], "坑": []}
    for t in caps:
        for m in _FIELD.finditer(t or ""):
            v = m.group(2).strip()
            while (
                v
                and v[-1] in "」）)"
                and v.count({"」": "「", "）": "（", ")": "("}[v[-1]]) < v.count(v[-1])
            ):
                v = v[:-1].strip()
            if v and not _NONE.match(v) and v not in out[m.group(1)]:
                out[m.group(1)].append(v[:80])
    return out


HEAD = "[看图看懂] 下面是系统帮你看过的图 你就当亲眼看到了："


# 防伪造标记（v0.2.1，借作者自己的 QQ 机器人 v1.16.3）：「看到了什么」那段以「[看图看懂]」开头，图写成「[图片：…]」「[表情包：…]」，
# 都是插件自己加的（不在他说的话里）。群友在消息里自己打一段一样的，模型分不出哪段是系统给的 →
# 他说的话、AstrBot 群聊上下文里出现就加上「他打的字：」（夹零宽字符、全角括号、【】也认；已经加过的不再加）
_ZW = "[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]*"
FAKE_MARK = re.compile(
    r"[\[［【]\s*" + _ZW + "(" + _ZW.join("看图看懂") + r")" + _ZW + r"\s*[\]］】]"
    r"|[\[［【]\s*"
    + _ZW
    + r"(图"
    + _ZW
    + "片|表"
    + _ZW
    + "情"
    + _ZW
    + r"包)"
    + _ZW
    + r"\s*[：:]"
)


# 跟「表情包懂你」这类插件一起装（复查后加）：它让模型在回复里写「[表情包:编号]」（数字编号），由它自己换成图；
# 它给模型的说明里也会写「[表情包:编号]」这个占位。这两种都不是伪造的看图结果（没有描述），不改（半角、全角冒号都放过）
_STICKER_CODE = re.compile(r"\s*(?:\d{1,6}|编号|序号|数字|id|ID|[NnXx])\s*[\]］】]")


def neutral_marks(text):
    """用户手打的「[看图看懂]」「[图片：…」「[表情包：…」→ 加上「他打的字：」；没有就原样返回
    「[表情包:3]」「[表情包：12]」「[表情包:编号]」这种表情包插件的编号不改"""
    s = text or ""
    if not s or not FAKE_MARK.search(s):
        return text

    def fix(m):
        if m.group(1):
            return "[他打的字：看图看懂]"
        name = re.sub(_ZW, "", m.group(2))
        if name == "表情包" and _STICKER_CODE.match(m.string, m.end()):
            return m.group(0)
        return "[他打的字：" + name + "："

    out = FAKE_MARK.sub(fix, s)
    return text if out == s else out


def slot_label(n=1, poss=()):
    """一条消息里第几张（v0.3.0，借作者自己的 QQ 机器人 11-03 v1.17.13 的「按位置配」）：
    这条消息只有一张图 → 「图」（跟以前一样）；几张 → 「3 张图里第 2 张」；同一张图发了两遍 → 「3 张图里第 1、2 张（同一张图）」"""
    n = int(n or 1)
    poss = sorted({int(p) for p in poss or () if p})
    if n <= 1 or not poss:
        return "图"
    s = f"{n} 张图里第 {'、'.join(str(p) for p in poss)} 张"
    return s + ("（同一张图）" if len(poss) > 1 else "")


# 没看成的那张也占着它的位置（v0.3.0）：这一轮有别的图看懂了时，写一行这个，后面那张的描述就不会被当成前面那张的
MISS = "没看成"
LATE = "还没看完"


def caption_block(items, more=()):
    """items：[(谁发的, 描述, 是不是引用的[, 第几张])] → 「看到了什么」那段（可以记进聊天记录）
    第几张：slot_label 给的（「3 张图里第 2 张」）；没有 = 「图」（跟以前一样）
    more：另外几段已经排好的（转发的聊天记录，见 forward_lines）"""
    items = [it for it in items if it[1]]
    more = [m for m in more or () if m]
    if not items and not more:
        return ""
    lines = [HEAD]
    for it in items:
        who, cap, quoted = it[0], it[1], it[2]
        label = (it[3] if len(it) > 3 else "") or "图"
        sep = " " if label[:1].isdigit() else ""  # 「发的 2 张图里第 1 张」
        lines.append(
            f"- {'引用的消息里' if quoted else ''}「{who or '有人'}」发的{sep}{label}：{cap}"
        )
    lines += more
    return "\n".join(lines)


HINT_HEAD = "# 这一轮的图（只给这一轮看）"


def hints_block(caps, blind=False):
    """「这一轮的图」（只给这一轮看 不进聊天记录）：梗点、图在不在说它、有没有坑 —— 只写事实（v0.3.0，作者的规矩 E1：
    给模型看的只写发生了什么、能不能做、为什么，不写台词、不写示范句；以前这里有「好笑的回法」「别用客服句式（「好的呢」…）」
    「自嘲、反咬一口都行」这种语气指令和示范句，去掉了）。
    caps 是这一轮的描述 —— 空的也给头两行（主模型自己看图、插件没另外看的时候）；
    blind = 对话模型自己看不到图（AstrBot 会在后面注一句原图没给它）"""
    caps = [c for c in caps if c and c not in (MISS, LATE)]
    f = fields(caps)
    lines = [HINT_HEAD, "- 图本身大家都看得到（就发在聊天里）。"]
    if blind and caps:
        lines.append(
            "- 后面写着图「not included」「[Image]」的：是说原图没直接发给你；上面「[看图看懂]」那段就是看懂的结果。"
        )
    for m in f["梗"][:2]:
        lines.append(f"- 梗点：{m}")
    if f["跟你"]:
        lines.append(f"- 图在说你（{f['跟你'][0]}）：说的是你，不是别人。")
    if f["坑"]:
        lines.append(f"- 图里有坑（{f['坑'][0]}）。是擦边的话：露骨的话不能说。")
    return "\n".join(lines)


def block(items, hints=True):
    """看到了什么 + 这一轮的图（合在一起；面板「试一张图」、不记进聊天记录的时候用）"""
    head = caption_block(items)
    if not head:
        return ""
    return head + ("\n\n" + hints_block([it[1] for it in items]) if hints else "")


# 「上面那张图什么意思」「这图笑死」「看看楼上」—— 说到图了（没带图、没引用的时候 拿群里最近一张）
TEXT_REFERS = re.compile(
    r"(这|那|上面|上头|刚才|刚刚|前面|楼上)(一)?(张|个|幅)?(图|照片|相片|表情|截图|梗图|动图)"
    r"|(?<![地插构企蓝草试意制版贴美修拼海])(图|照片|截图|动图)(呢|里|上|中|啥意思|什么意思|是啥|是什么)"
    r"|表情包?(啥意思|什么意思|是啥|是什么)"
    r"|(看|瞅|瞧)(看|一下|下|一眼)?(这|那|楼上|上面)"
    r"|(啥|什么)(图|表情)|发的(图|照片|表情)|这张|那张|上面那|看图|识图"
)


class Cache:
    """看过的图：key → 描述（同一张图 换个群发也不再花钱看）。持久化交给外面（dump / load）
    一张图一般有两个凭据（文件名 / 网址 + 内容的 md5），所以最多存 keep × 2 条"""

    def __init__(self, keep=600):
        self.keep = max(1, int(keep)) * 2
        self.d = OrderedDict()

    def get(self, *keys):
        for k in keys:
            if k and k in self.d:
                self.d.move_to_end(k)
                return self.d[k]["cap"]
        return None

    def put(self, keys, cap):
        now = int(time.time())
        for k in keys:
            if k:
                self.d[k] = {"cap": cap, "t": now}
                self.d.move_to_end(k)
        while len(self.d) > self.keep:
            self.d.popitem(last=False)

    def dump(self):
        return dict(self.d)

    def load(self, d):
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict) and v.get("cap"):
                    self.d[k] = v
        while len(self.d) > self.keep:
            self.d.popitem(last=False)


# ───────── 转发的聊天记录（大肥鱼机器人 v1.15.15）─────────
# 以前转发的聊天记录里的图一律写「[图片]」：一条全是图的转发，机器人只看到一排「[图片]」，
# 就会说「一张都没加载出来」。现在把里面的图（最多几张）一起看了，看懂的写进那一行，没看成的照旧「[图片]」。
_CQ = re.compile(r"\[CQ:(\w+)((?:,[^\]]*)?)\]")


def _cq_unescape(s):
    return (
        str(s)
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _segs(msg):
    """消息段：数组格式原样；字符串（CQ 码）格式拆成数组"""
    if isinstance(msg, list):
        return [s for s in msg if isinstance(s, dict)]
    if not isinstance(msg, str):
        return []
    out, pos = [], 0
    for m in _CQ.finditer(msg):
        if m.start() > pos:
            out.append(
                {"type": "text", "data": {"text": _cq_unescape(msg[pos : m.start()])}}
            )
        data = {}
        for kv in m.group(2).strip(",").split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                data[k] = _cq_unescape(v)
        out.append({"type": m.group(1), "data": data})
        pos = m.end()
    if pos < len(msg):
        out.append({"type": "text", "data": {"text": _cq_unescape(msg[pos:])}})
    return out


def forward_nodes(payload):
    """get_forward_msg 的返回 / 转发消息段里带的 content → 一条条消息（认 NapCat 和 OneBot 标准两种格式）"""
    d = payload
    for _ in range(2):  # 有的实现外面还包一层 {"data": …}
        if (
            isinstance(d, dict)
            and isinstance(d.get("data"), (dict, list))
            and not any(
                isinstance(d.get(k), list) for k in ("messages", "message", "nodes")
            )
        ):
            d = d["data"]
    if isinstance(d, dict):
        for k in ("messages", "message", "nodes", "nodeList", "content"):
            if isinstance(d.get(k), list):
                return d[k]
        return []
    return d if isinstance(d, list) else []


def parse_forward(payload, max_rows=30):
    """→ (rows, imgs)
    rows：[{"who": 谁, "parts": [文字 或 ("img", 第几张)]}]；imgs：[{"ref": 图的地址, "file": 文件名, "kind": 表情包…, "row": 第几行}]
    转发里再套的转发只写「[聊天记录]」（不往里钻）"""
    rows, imgs = [], []
    for n in forward_nodes(payload)[: max(1, int(max_rows))]:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "node":  # OneBot 标准
            nd = n.get("data") or {}
            who = nd.get("nickname") or nd.get("name") or ""
            msg = nd.get("content") or nd.get("message") or []
        else:  # NapCat：每条就是一条消息
            snd = n.get("sender") or {}
            who = snd.get("card") or snd.get("nickname") or n.get("nickname") or ""
            msg = n.get("message") or n.get("content") or []
        parts = []
        for s in _segs(msg):
            t, d = s.get("type"), s.get("data") or {}
            if t in ("text", "plain"):
                if d.get("text"):
                    parts.append(str(d["text"]))
            elif t in ("image", "mface"):
                url = str(d.get("url") or "")
                file = str(d.get("file") or "")
                ref = (
                    url
                    if url.startswith("http")
                    else (
                        file
                        if file.startswith(("http", "base64://", "file://"))
                        else ""
                    )
                )
                if t == "mface":
                    name = str(d.get("summary") or "").strip("[]")[:20]
                    kind = f"QQ 表情「{name}」" if name else "QQ 表情"
                    file = str(d.get("emoji_id") or "")
                else:
                    kind = (
                        "表情包"
                        if str(d.get("sub_type")) == "1"
                        or "表情" in str(d.get("summary") or "")
                        else ""
                    )
                imgs.append({"ref": ref, "file": file, "kind": kind, "row": len(rows)})
                parts.append(("img", len(imgs) - 1))
            elif t == "face":
                parts.append("[表情]")
            elif t == "at":
                parts.append("@" + str(d.get("name") or d.get("qq") or ""))
            elif t in ("forward", "node", "nodes"):
                parts.append("[聊天记录]")
            elif t == "video":
                parts.append("[视频]")
            elif t == "record":
                parts.append("[语音]")
            elif t == "file":
                parts.append("[文件]")
            elif t in ("json", "xml"):
                parts.append("[卡片]")
        if parts:
            rows.append({"who": str(who or "某人")[:20], "parts": parts})
    return rows, imgs


def _img_tag(im, cap=None):
    lab = "表情包" if im.get("kind") else "图片"
    return f"[{lab}：{cap}]" if cap else f"[{lab}]"


def row_text(row, imgs=(), caps=None, clip=80):
    """一行聊天记录 → 文字；图：看懂的写成「[图片：…]」，没看的「[图片]」。clip 只算文字部分"""
    caps, out, used = caps or {}, [], 0
    for p in row.get("parts") or []:
        if isinstance(p, tuple):
            im = imgs[p[1]] if p[1] < len(imgs) else {}
            out.append(_img_tag(im, caps.get(p[1])))
        else:
            s = str(p)
            if used + len(s) > clip:
                s = s[: max(0, clip - used)] + "…"
            used += len(s)
            out.append(s)
            if used >= clip:
                break
    return "".join(out).strip()


def forward_lines(who, rows, imgs, caps, quoted=False, total=None, max_chars=1500):
    """一段转发的聊天记录 → 「看到了什么」里的一项（caps：{第几张图: 看懂的}）"""
    total = total or len(rows)
    lines = [
        f"- {'引用的那条' if quoted else ''}「{who or '有人'}」转发的聊天记录（{total} 条）："
    ]
    used = len(lines[0])
    for i, r in enumerate(rows):
        line = f"  {r['who']}: {row_text(r, imgs, caps)}"
        if used + len(line) > max_chars and i:
            lines.append(f"  …（后面还有 {len(rows) - i} 条）")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ───────── 表情包词典（大肥鱼机器人 v1.15.4）─────────
# 以前：看过的图按 id 缓存，第一眼看错了以后每次都是那句错的；也不知道群里拿它表达什么。
# 现在：同一张图在群里反复出现（默认第 3 次）、或者有人直接拿表情包找机器人 → 认真认一次（逐字抄字、
# 是什么梗 / 出处、群里一般什么时候发它，参考真人发它前后在说什么），记成词条；以后再见到直接用，不再花钱看。
DEEP_TAG = "表情包词典"
DEEP_MIN_CONF = 40  # 把握低于这个的词条不拿来用（这次照样用它当看图结果，只是不当词条）


def deep_rules(usage=(), note=""):
    p = f"这张图要收进「{DEEP_TAG}」：以后再见到它就直接用你这次写的、不再看图 —— 错了会一直错，请认真认。\n"
    usage = [str(x)[:120] for x in usage or () if x][-5:]
    if usage:
        p += (
            "群里真人发它的那几次（前后在说什么）：\n"
            + "\n".join(f"- {x}" for x in usage)
            + "\n"
        )
    p += (
        "按下面的格式，一行一项（没有的写「无」）：\n"
        "名字：大家会怎么叫它（短；没有通用叫法就按画面起个短名；画的是机器人本人就按画面起，别用别的作品里长得像的角色的名字）\n"
        "字：图里所有的字，逐字照抄（看不清的用▢）" + TEXT_NOT + "\n"
        "画面：谁 / 什么东西、在做什么、什么表情和动作（只写要紧的）\n"
        "梗：是哪个梗 / 哪部作品 / 哪个名场面 / 哪个固定的表情包系列、出处；不认得写「不确定」（别编）\n"
        "意思：它在群聊里表达什么情绪 / 态度（有真人用它的上下文就照那个来）\n"
        "一般用来：一般什么时候发它（一句）\n"
        "跟你：图里画的就是机器人本人（长相对上至少两样明显的特征），或者图里的字明着说 AI、机器人、它的名字 才写（别从聊天里硬往它身上扯）\n"
        "坑：有擦边、挖坑、钓鱼的地方才写\n"
        "把握：0~100，你对名字、梗、意思有几成把握"
    )
    if note:
        p += (
            f"\n注意：这是一张{note}。画面那项当成一个动作过程来说（一开始…然后…最后…）"
            "不要一格一格分开讲 不要提「拼图」「第几帧」。"
        )
    return p


_DEEP_KEYS = {
    "名字": "name",
    "一般用来": "usage",
    "画面": "scene",
    "意思": "meaning",
    "跟你": "you",
    "把握": "conf",
    "梗": "meme",
    "坑": "trap",
    "字": "text",
}
_DEEP_FIELD = re.compile(
    r"(?:^|(?<=[\n。；;]))[ \t\-*•·\d.、]*(名字|一般用来|画面|意思|跟你|把握|梗|坑|字)\s*[:：]"
)
_DEEP_NONE = re.compile(r"^(无|没有|不确定|不知道|看不出|认不出|不清楚|—+|-+|/)")


def parse_deep(raw):
    """深度认的回复 → 词条 {name, text, scene, meme, meaning, usage, you, trap, conf}；一项都没有 → None"""
    raw = _THINK.sub("", str(raw or ""))
    ms = list(_DEEP_FIELD.finditer(raw))
    if not ms:
        return None
    e = {v: "" for v in _DEEP_KEYS.values()}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(raw)
        v = re.sub(r"\s+", " ", raw[m.end() : end]).strip().strip("。；;").strip()
        k = _DEEP_KEYS[m.group(1)]
        if k == "conf":
            d = re.search(r"\d{1,3}", v)
            e[k] = max(0, min(100, int(d.group(0)))) if d else 0
            continue
        if k == "text":
            v = clean_text(v.strip('「」"“”'))  # 帧号 / 水印不算字（v0.3.2）
        if k == "name":
            v = _WATERMARK.sub("", v).strip() or v  # 名字里的「AI生成」去掉（v0.3.2）
        if _DEEP_NONE.match(v) or _NONE.match(v) and k in ("you", "trap"):
            v = ""
        if v and not e[k]:
            e[k] = v[: 80 if k in ("text", "meaning") else 60]
    e["conf"] = e.get("conf") or 0
    return (
        e if any(e[k] for k in ("name", "text", "scene", "meme", "meaning")) else None
    )


def deep_caption(e, kind="", anim=False):
    """词条 → 给机器人看的那句（和普通看图结果一样的「字 / 画面 / 梗 / 跟你 / 坑」写法，「这一轮的图」照样拆得出来）"""
    if not e:
        return ""
    head = ("表情包" if kind else "常见的图") + (
        f"「{e['name']}」" if e.get("name") else ""
    )
    head += "（动图）" if anim else ""
    parts = []
    if e.get("text"):
        parts.append(f"字：「{e['text']}」")
    if e.get("scene"):
        parts.append("画面：" + e["scene"])
    parts.append("梗：" + (e.get("meme") or "看不出出处"))
    if e.get("meaning"):
        parts.append("意思：" + e["meaning"])
    if e.get("usage"):
        parts.append("一般用来：" + e["usage"])
    if e.get("you"):
        parts.append("跟你：" + e["you"])
    if e.get("trap"):
        parts.append("坑：" + e["trap"])
    return head + "：" + "。".join(parts)


# ───────── 按哪个时区算「今天」（v0.3.0）─────────
# 以前按服务器的时间：服务器在国外 / Docker 默认 UTC 时，北京时间早上 8 点才「过零点」，每天的上限、表情包词典每天的名额都跟着错开。
# 现在按配置的时区（默认北京时间）。中国 1991 年以后没有夏令时，北京时间就是固定的 UTC+8，不用装 tzdata（Windows 上常常没有）。
_FIXED_TZ = {
    "asia/shanghai": 8,
    "asia/chongqing": 8,
    "asia/harbin": 8,
    "asia/urumqi": 8,
    "prc": 8,
    "asia/hong_kong": 8,
    "asia/macau": 8,
    "asia/taipei": 8,
    "asia/singapore": 8,
    "asia/tokyo": 9,
    "asia/seoul": 9,
    "utc": 0,
    "gmt": 0,
    "etc/utc": 0,
}
_OFFSET = re.compile(r"^(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$")
BEIJING = timezone(timedelta(hours=8), "北京时间")


def make_tz(name):
    """配置里的时区 → (tzinfo, 怎么叫它, 认没认出来)
    留空 → (None, 「服务器时间」, True)：跟服务器（以前的做法）；认不出 → 北京时间"""
    s = str(name or "").strip()
    if not s:
        return None, "服务器时间", True
    k = s.lower().replace(" ", "")
    if k in _FIXED_TZ:
        off = _FIXED_TZ[k]
        if off == 8:
            return BEIJING, "北京时间", True
        return timezone(timedelta(hours=off), s), s, True
    m = _OFFSET.match(k)
    if m:
        h, mi = int(m.group(2)), int(m.group(3) or 0)
        if h <= 14 and mi < 60:
            sign = 1 if m.group(1) == "+" else -1
            label = f"UTC{m.group(1)}{h}" + (f":{mi:02d}" if mi else "")
            off = timedelta(hours=h, minutes=mi) * sign
            return (
                (BEIJING, "北京时间", True)
                if off == timedelta(hours=8)
                else (
                    timezone(off, label),
                    label,
                    True,
                )
            )
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(s), s, True
    except Exception:  # Windows 没装 tzdata / 写错了
        return BEIJING, "北京时间", False


def day_of(now=None, tz=None):
    """某个时刻是哪一天（「2026-11-03」）；tz = None 按服务器的时间（以前的做法）"""
    ts = time.time() if now is None else float(now)
    if tz is None:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def _day(now=None, tz=None):
    return day_of(now, tz)


class StickerDict:
    """表情包词典（跨群共用；持久化交给外面 dump / load）
    items：凭据（QQ 文件名 = 图片内容的哈希；商城表情的 id）→ {n 见过几次, convs 在哪些会话, ctx 最近几次前后在说什么, deep 词条…}
    alias：别的凭据（图片内容的 md5）→ 那条的凭据
    tz：每天深度认几张按哪个时区分天（v0.3.0；None = 服务器的时间）"""

    def __init__(self, after=3, daily=30, keep=2000, tz=None):
        self.items, self.alias, self.days = {}, {}, {}
        self.pending = {}  # 会话 -> (凭据, 时间, 谁发的)：发完以后别人接的那句记进这次的上下文
        self.dirty = False
        self.tz = tz
        self.configure(after, daily, keep)

    def configure(self, after=3, daily=30, keep=2000, tz=...):
        self.after = max(1, int(after))
        self.daily = max(0, int(daily))
        self.keep = max(10, int(keep))
        if tz is not ...:
            self.tz = tz

    def _k(self, key):
        return self.alias.get(key, key)

    def get(self, key):
        return self.items.get(self._k(key))

    def seen(
        self,
        key,
        conv,
        uid="",
        before=(),
        said="",
        to_bot=False,
        sticker=False,
        now=None,
    ):
        """有人发了这张 → 记一次；返回这张现在该不该深度认"""
        if not key:
            return False
        now = float(now or time.time())
        k = self._k(key)
        e = self.items.setdefault(
            k, {"n": 0, "first": round(now), "convs": [], "ctx": []}
        )
        e["n"] = int(e.get("n", 0)) + 1
        e["last"] = round(now)
        if sticker:
            e["sticker"] = True
            if to_bot:  # 直接拿表情包找机器人：不用等第 3 次
                e["want"] = True
        conv = str(conv or "")
        if conv and conv not in e["convs"]:
            e["convs"] = (e["convs"] + [conv])[-10:]
        e["ctx"] = (e.get("ctx") or []) + [
            {
                "before": [str(x)[:40] for x in list(before or ())[-2:] if x],
                "said": str(said or "")[:40],
                "after": "",
            }
        ]
        del e["ctx"][:-5]
        self.pending[conv] = (k, now, str(uid))
        self.dirty = True
        self.prune()
        return self.due(k, now)

    def after_msg(self, conv, uid, text, now=None):
        """发完那张以后（60 秒内）别人接的第一句 → 记进那次的上下文（看群里拿它表达什么）"""
        p = self.pending.get(str(conv or ""))
        now = float(now or time.time())
        text = str(text or "").strip()
        if not p or not text or str(uid) == p[2]:
            return
        self.pending.pop(str(conv or ""), None)
        if now - p[1] > 60:
            return
        e = self.items.get(p[0])
        if e and e.get("ctx") and not e["ctx"][-1].get("after"):
            e["ctx"][-1]["after"] = text[:40]
            self.dirty = True

    def due(self, key, now=None):
        e = self.get(key)
        if not e or e.get("deep"):
            return False
        if not (int(e.get("n", 0)) >= self.after or e.get("want")):
            return False
        return int(self.days.get(_day(now, self.tz), 0)) < self.daily

    def started(self, now=None):
        """要深度认一张了（算今天的次数）—— 外面在确认图下载到了、是图片以后才调（v0.3.0）"""
        day = _day(now, self.tz)
        keep = _day((now or time.time()) - 7 * 86400, self.tz)
        self.days = {d: n for d, n in self.days.items() if d >= keep}
        self.days[day] = int(self.days.get(day, 0)) + 1
        self.dirty = True

    def put_deep(self, key, entry, also=()):
        k = self._k(key)
        e = self.items.setdefault(
            k, {"n": 0, "first": round(time.time()), "convs": [], "ctx": []}
        )
        e["deep"] = dict(entry)
        e.pop("want", None)
        for a in also or ():
            if a and a != k:
                self.alias[a] = k
        self.dirty = True

    def set_meme(self, key, meme):
        """查梗查到了（v0.4.0）：认好的那条「梗」还空着 → 写进去（词条里的「梗：看不出出处」换掉），下次直接用、不用再查
        → 写了没有。看图模型自己认出来的梗不覆盖；没认好的（没有词条）不写"""
        e = self.get(key) if key else None
        dp = (e or {}).get("deep")
        meme = re.sub(r"[。\n\[\]]", "，", str(meme or "")).strip()[:60]
        if not dp or not meme or dp.get("meme"):
            return False
        dp["meme"] = meme
        cap = str(dp.get("cap") or "")
        if "梗：看不出出处" in cap:
            dp["cap"] = cap.replace("梗：看不出出处", "梗：" + meme, 1)
        self.dirty = True
        return True

    def link(self, key, same):
        """key 跟 same 是同一张（内容一样）→ 以后按 same 那条用"""
        t = self._k(same)
        if key and key != t and t in self.items and key not in self.items:
            self.alias[key] = t
            self.dirty = True

    @staticmethod
    def usable(e):
        dp = (e or {}).get("deep") or {}
        return bool(dp.get("cap")) and int(dp.get("conf") or 0) >= DEEP_MIN_CONF

    def lookup(self, *keys):
        """认好的那句（把握够的）；没有 → None"""
        for key in keys:
            e = self.get(key) if key else None
            if self.usable(e):
                return e["deep"]["cap"]
        return None

    def usage(self, key):
        out = []
        for c in ((self.get(key) or {}).get("ctx") or [])[-5:]:
            s = "之前：" + (" / ".join(c.get("before") or []) or "（没人说话）")
            if c.get("said"):
                s += f"；发图的人说：{c['said']}"
            if c.get("after"):
                s += f"；之后有人接：{c['after']}"
            out.append(s)
        return out

    def count(self, now=None):
        return {
            "tracked": len(self.items),
            "known": sum(1 for e in self.items.values() if self.usable(e)),
            "today": int(self.days.get(_day(now, self.tz), 0)),
        }

    def listing(self, n=10):
        rows = [
            {
                "name": e["deep"].get("name") or "",
                "cap": e["deep"].get("cap") or "",
                "conf": e["deep"].get("conf") or 0,
                "n": e.get("n", 0),
                "groups": len(e.get("convs") or []),
                "t": e["deep"].get("t") or 0,
            }
            for e in self.items.values()
            if self.usable(e)
        ]
        rows.sort(key=lambda x: -int(x["t"] or 0))
        return rows[:n]

    def prune(self):
        """太多了：先扔没认过、只见过一次、最久没见的；认好的最后才扔"""
        over = len(self.items) - self.keep
        if over <= 0:
            return
        order = sorted(
            self.items,
            key=lambda k: (
                bool(self.items[k].get("deep")),
                int(self.items[k].get("n", 0)) > 1,
                self.items[k].get("last", 0),
            ),
        )
        for k in order[:over]:
            self.items.pop(k, None)
        dead = set(order[:over])
        self.alias = {a: k for a, k in self.alias.items() if k not in dead}
        self.dirty = True

    def dump(self):
        return {"items": self.items, "alias": self.alias, "days": self.days}

    def load(self, d):
        if not isinstance(d, dict):
            return
        items = d.get("items")
        if isinstance(items, dict):
            self.items = {
                str(k): v for k, v in items.items() if isinstance(v, dict) and "n" in v
            }
        alias = d.get("alias")
        if isinstance(alias, dict):
            self.alias = {str(a): str(k) for a, k in alias.items() if k in self.items}
        days = d.get("days")
        if isinstance(days, dict):
            self.days = {str(x): int(n) for x, n in days.items() if str(n).isdigit()}
        self.prune()
