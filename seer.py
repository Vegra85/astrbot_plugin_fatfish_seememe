# -*- coding: utf-8 -*-
"""
看图看懂梗：提示词、解析、给机器人的「这一轮怎么接」（不依赖 AstrBot，单独能测）

来自「大肥鱼」QQ 机器人 v1.5.0 的看图：以前识图只写「画了什么」，群友拿长得像机器人的图逗它、
发历史事件的地狱梗、发擦边图挖坑，它都看不懂 —— 回出来像客服：先把画面复述一遍，再表个态。
现在看图要写五样：字 / 画面 / 梗（出处、在玩什么）/ 跟你（图里有没有它、是不是在拿它开涮）/ 坑（擦边、挖坑、钓鱼），
再告诉机器人这一轮怎么接：别复述画面、对梗点做反应、认出图在说自己、看穿坑反挖回去。
"""

import re
import time
from collections import OrderedDict

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
        s += "图里出现长得像它的角色（同样的长相、它的 Q 版、它的名字或外号）就是在说它本人。"
    return s


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
        "字：图里的文字原样抄（没有就不写这项）\n"
        "画面：有什么、谁在干什么（只写要紧的：动作 表情 姿势 场景）\n"
        "梗：这是什么梗 / 出处 / 在玩什么（影视截图、历史事件、网络模板比如「找一车X来弄你」「给X什么都做」都点名说清；"
        "认不出就写「看不出出处」 别编）\n"
        "跟你：图里真有机器人本人 / 像它的角色，或者图里的字明着说 AI、token、机器人、它的名字 才写（别从聊天里硬往它身上扯；没关系就整项不写）\n"
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


def caption_block(items, more=()):
    """items：[(谁发的, 描述, 是不是引用的)] → 「看到了什么」那段（可以记进聊天记录）
    more：另外几段已经排好的（转发的聊天记录，见 forward_lines）"""
    items = [(w, c, q) for w, c, q in items if c]
    more = [m for m in more or () if m]
    if not items and not more:
        return ""
    lines = [HEAD]
    for who, cap, quoted in items:
        lines.append(
            f"- {'引用的消息里' if quoted else ''}「{who or '有人'}」发的图：{cap}"
        )
    lines += more
    return "\n".join(lines)


def hints_block(caps, blind=False):
    """「这一轮怎么接」（只给这一轮看 不进聊天记录）；caps 是这一轮的描述 —— 空的也给通用的那几条
    （主模型自己看图、插件没另外看的时候）；blind = 对话模型自己看不到图（AstrBot 会在后面注一句原图没给它）"""
    f = fields([c for c in caps if c])
    lines = [
        "# 这一轮怎么接（别像客服）",
        "- 别把画面复述一遍（大家都看得到 你一描述就像客服）。直接对梗点做反应。",
    ]
    if blind and any(caps):
        lines.append(
            "- 后面要是写着图「not included」「[Image]」：那只是说原图没直接发给你 —— 上面就是看懂的结果，照着接，别说你看不到图。"
        )
    for m in f["梗"][:2]:
        lines.append(f"- 梗点：{m}")
    if f["跟你"]:
        lines.append(
            f"- 图在说你（{f['跟你'][0]}）：认出来是在拿你开涮 接住 —— 自嘲、反咬一口、将错就错都行；别当成在说别人。"
        )
    if f["坑"]:
        lines.append(
            f"- 图里有坑（{f['坑'][0]}）：别上当、别正面回答、别认真解释；看穿了就反挖回去。"
            "擦边的照样不接（不说露骨的），但用一句玩笑挡回去，别说教。"
        )
    lines.append(
        "- 好笑的回法：一句话、有个拐弯（反转、夸张、自嘲、顺杆爬再拐都行）；"
        "别用客服句式（「这张图展示了…」「我看到了…」「好的呢」），别解释梗，别一本正经。"
    )
    return "\n".join(lines)


def block(items, hints=True):
    """看到了什么 + 这一轮怎么接（合在一起；面板「试一张图」、不记进聊天记录的时候用）"""
    head = caption_block(items)
    if not head:
        return ""
    return head + ("\n\n" + hints_block([c for _, c, _ in items]) if hints else "")


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
        "名字：大家会怎么叫它（短；没有通用叫法就按画面起个短名）\n"
        "字：图里所有的字，逐字照抄（看不清的用▢）\n"
        "画面：谁 / 什么东西、在做什么、什么表情和动作（只写要紧的）\n"
        "梗：是哪个梗 / 哪部作品 / 哪个名场面 / 哪个固定的表情包系列、出处；不认得写「不确定」（别编）\n"
        "意思：它在群聊里表达什么情绪 / 态度（有真人用它的上下文就照那个来）\n"
        "一般用来：一般什么时候发它（一句）\n"
        "跟你：图里真有机器人本人 / 像它的角色，或者图里的字明着说 AI、机器人、它的名字 才写（别从聊天里硬往它身上扯）\n"
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
            v = v.strip('「」"“”')
        if _DEEP_NONE.match(v) or _NONE.match(v) and k in ("you", "trap"):
            v = ""
        if v and not e[k]:
            e[k] = v[: 80 if k in ("text", "meaning") else 60]
    e["conf"] = e.get("conf") or 0
    return (
        e if any(e[k] for k in ("name", "text", "scene", "meme", "meaning")) else None
    )


def deep_caption(e, kind="", anim=False):
    """词条 → 给机器人看的那句（和普通看图结果一样的「字 / 画面 / 梗 / 跟你 / 坑」写法，「这一轮怎么接」照样拆得出来）"""
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


def _day(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


class StickerDict:
    """表情包词典（跨群共用；持久化交给外面 dump / load）
    items：凭据（QQ 文件名 = 图片内容的哈希；商城表情的 id）→ {n 见过几次, convs 在哪些会话, ctx 最近几次前后在说什么, deep 词条…}
    alias：别的凭据（图片内容的 md5）→ 那条的凭据"""

    def __init__(self, after=3, daily=30, keep=2000):
        self.items, self.alias, self.days = {}, {}, {}
        self.pending = {}  # 会话 -> (凭据, 时间, 谁发的)：发完以后别人接的那句记进这次的上下文
        self.dirty = False
        self.configure(after, daily, keep)

    def configure(self, after=3, daily=30, keep=2000):
        self.after = max(1, int(after))
        self.daily = max(0, int(daily))
        self.keep = max(10, int(keep))

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
        return int(self.days.get(_day(now), 0)) < self.daily

    def started(self, now=None):
        """要深度认一张了（算今天的次数）"""
        day = _day(now)
        keep = _day((now or time.time()) - 7 * 86400)
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
            "today": int(self.days.get(_day(now), 0)),
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
