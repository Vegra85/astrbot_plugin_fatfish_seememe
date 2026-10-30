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
    names=(), look="", ctx=None, ref=False, note="", kind="", max_chars=180
):
    """给看图的模型的话。
    ctx：{"sender": 谁发的, "to_bot": 是不是冲着机器人来的, "said": 发图那条消息里的字,
          "ask": 后来 @ 机器人时说的话, "chat": [发图之前的几句], "place": 「QQ 群」「私聊」这种}
    ref = 第一张是机器人的头像（对照用）；note = 动图说明；kind = 「表情包」「QQ 表情「狗头」」这种"""
    ctx = ctx or {}
    place = str(ctx.get("place") or "群聊")
    p = (
        f"这是{' ' if place[:1].isascii() else ''}{place}里"
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
        p += f"发图的时候还说了：「{said}」\n"
    ask = str(ctx.get("ask") or "").strip()[:80]
    if ask and ask != said:
        p += f"后来有人找机器人说：「{ask}」\n"
    chat = [str(x)[:60] for x in (ctx.get("chat") or []) if x][-4:]
    if chat:
        p += "发这张图之前大家在聊：" + " / ".join(chat) + "\n"
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


def caption_block(items):
    """items：[(谁发的, 描述, 是不是引用的)] → 「看到了什么」那段（可以记进聊天记录）"""
    items = [(w, c, q) for w, c, q in items if c]
    if not items:
        return ""
    lines = [HEAD]
    for who, cap, quoted in items:
        lines.append(
            f"- {'引用的消息里' if quoted else ''}「{who or '有人'}」发的图：{cap}"
        )
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
