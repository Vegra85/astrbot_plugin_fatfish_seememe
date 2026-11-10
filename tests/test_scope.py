#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看图结果 / 表情包词条分不分对话（v0.4.1）：python tests/test_scope.py [插件目录 默认上一层]
不用装 AstrBot（用一个假的 astrbot.api、假的看图模型、假的消息），不连外网，只在临时文件夹里跑。
假的看图模型故意把提示里给它的聊天上下文（谁发的、发图时说的、之前几句）抄进「坑」「意思」「一般用来」——
真模型抄不抄是另一回事，这里只看插件把哪一段给了哪个对话：
  A 私聊里带着上下文看出来的那段：别的对话拿不到（只拿得到「字 / 画面」，要结合上下文就在自己的对话里重新看）；
    同一个对话、同一个发图人再发 → 照样复用，不再花钱看
  B 老版本留下的 state.json（整段按图存的）：读进来只留「字 / 画面」
  C 表情包词典：私聊里的话不进词典、不带进深度认的提示；私聊的会话标识不存；群里的用法照常记
  D 走一遍真的流程（每条消息 → 回话请求）
"""

import asyncio
import base64
import hashlib
import importlib
import json
import sys
import tempfile
import time
import types
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
RES = []
LOGS = []
ROOT = Path(tempfile.mkdtemp(prefix="seememe_scope_"))
DATA = {"dir": ROOT / "a"}


def check(c, m, why=""):
    RES.append((bool(c), m))
    print(
        ("  [过]   " if c else "  [没过] ")
        + m
        + ("" if c or not why else f"\n           {str(why)[:600]}")
    )


def done():
    ok = sum(1 for c, _ in RES if c)
    bad = [m for c, m in RES if not c]
    print(
        "\n"
        + "=" * 50
        + f"\n  结果：{ok} 项过了"
        + (f"，{len(bad)} 项没过" if bad else "，全过")
    )
    for m in bad:
        print("    -", m)
    sys.exit(1 if bad else 0)


# ───────── 假的 astrbot（main.py 只用到这几样）─────────
class _Logger:
    def info(self, m, *a, **k):
        LOGS.append(str(m))

    warning = error = debug = info


def _deco(*a, **k):
    def w(f):
        return f

    return w


class _Enum:
    def __getattr__(self, k):
        return k


class _Filter:
    EventMessageType = _Enum()

    def __getattr__(self, k):
        return _deco


class Plain:
    def __init__(self, text=""):
        self.text = text


class Image:
    def __init__(self, file="", url="", path=""):
        self.file, self.url, self.path = file, url, path


class Reply:
    def __init__(self, id="", chain=None, sender_nickname="", sender_id=""):
        self.id, self.chain = id, chain or []
        self.sender_nickname, self.sender_id = sender_nickname, sender_id


class Forward:
    def __init__(self, id=""):
        self.id = id


class ProviderRequest:
    def __init__(self, prompt="", system_prompt=""):
        self.prompt, self.system_prompt = prompt, system_prompt
        self.extra_user_content_parts = []


class Star:
    def __init__(self, context=None):
        self.context = context


class StarTools:
    @staticmethod
    def get_data_dir(name):
        p = DATA["dir"] / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_mod("astrbot")
_mod("astrbot.api", logger=_Logger(), AstrBotConfig=dict)
_mod("astrbot.api.event", AstrMessageEvent=object, filter=_Filter())
_mod("astrbot.api.provider", ProviderRequest=ProviderRequest)
_mod("astrbot.api.star", Context=object, Star=Star, StarTools=StarTools)
_mod("astrbot.api.message_components", Plain=Plain, Image=Image, Reply=Reply, Forward=Forward)
sys.modules["astrbot"].api = sys.modules["astrbot.api"]
sys.modules["astrbot.api"].message_components = sys.modules["astrbot.api.message_components"]
sys.path.insert(0, str(d.parent))
M = importlib.import_module(f"{d.name}.main")
seer = importlib.import_module(f"{d.name}.seer")

# ───────── 假的看图模型 ─────────
import re  # noqa: E402

CALLS = []  # 发给看图模型的原话
MODEL = {"fail": False, "gate": None, "free": False}


class Resp:
    def __init__(self, t):
        self.completion_text, self.usage = t, None


class Ctx:
    async def get_using_provider_async(self, umo=None):
        return None

    def get_config(self, umo=None):
        return {"provider_settings": {}}

    def register_web_api(self, *a, **k):
        pass

    async def llm_generate(self, chat_provider_id=None, prompt="", image_urls=None, **kw):
        CALLS.append(prompt)
        if MODEL["gate"] is not None:
            await MODEL["gate"].wait()
        if MODEL["fail"]:
            raise RuntimeError("看图模型挂了（测试）")
        said = re.search(r"(?:发图的时候还说了|聊天记录里这条消息的字)：「([^」]*)」", prompt)
        chat = re.search(r"(?:发这张图之前大家在聊|聊天记录里这张图前面几句)：([^\n]*)", prompt)
        who = re.search(r"里(?:有人发的转发的聊天记录中)?「([^」]*)」发的", prompt)
        used = re.findall(r"^- (之前：.*)$", prompt, re.M)
        who, said, chat = (m.group(1) if m else "" for m in (who, said, chat))
        if "表情包词典" in prompt:  # 深度认：照提示「有真人用它的上下文就照那个来」
            return Resp(
                "名字：委屈猫\n字：在吗\n画面：一只猫探头\n梗：不确定\n意思："
                + (chat[:40] or (used[-1][:40] if used else "") or "打招呼")
                + "\n一般用来："
                + (said or "找人")
                + "\n把握：80"
            )
        if MODEL["free"]:  # 不按格式写的模型
            return Resp(f"一只猫探头，{who}说「{said}」")
        return Resp(
            "字：「在吗」。画面：一只猫探头。梗：看不出出处。"
            f"坑：{who}说「{said}」像在试探（之前在聊 {chat}）"
        )


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PRIV, GRP, GRP2 = "aiocqhttp:FriendMessage:111", "aiocqhttp:GroupMessage:999", "aiocqhttp:GroupMessage:888"
SID = "10000"
A_NAME = "私聊的人"
SAID = "我跟对象吵架了先别告诉别人"
CHAT = ["我想分手", "你别跟别人说"]
LEAKS = ("吵架", "分手", "别跟别人说", A_NAME)
PLAIN = "字：「在吗」。画面：一只猫探头"
CONF = {
    "enable": True,
    "vision_provider_id": "fake",
    "avatar_mode": "不用",
    "bot_names": ["小助手"],
    "meme_lookup": False,
    "puns": False,
    "prefetch": False,
    "remember": True,
    "hints": True,
    "sticker_dict": True,
    "sticker_after": 3,
    "sticker_daily": 30,
    "forward_images": True,
    "also_when_main_sees": True,
    "max_chars": 180,
    "wait_sec": 30,
    "timeout_sec": 40,
    "hourly_max": 60,
    "daily_max": 300,
    "cache_keep": 600,
    "day_tz": "Asia/Shanghai",
}


def leaks(x):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return [w for w in LEAKS if w in s]


def new_plugin(name, **conf):
    DATA["dir"] = ROOT / name
    return M.FatfishSeeMeme(Ctx(), {**CONF, **conf})


def data_file(name, fn):
    return ROOT / name / M.PLUGIN_NAME / fn


def write_img(name="cat.png", extra=b""):
    p = ROOT / name
    p.write_bytes(PNG + extra)  # 尾巴上多几个字节：内容（md5）不一样的另一张，照样是能打开的 PNG
    return str(p)


def image(key, ref, sender, uid, said="", kind=""):
    return {
        "ref": ref,
        "alt": "",
        "key": key,
        "sender": sender,
        "uid": uid,
        "t": time.time(),
        "quoted": False,
        "kind": kind,
        "said": said,
    }


def priv_ctx(said=SAID):
    return {
        "sender": A_NAME,
        "to_bot": True,
        "chat": [f"{A_NAME}: {x}" for x in CHAT],
        "said": said,
        "ask": "",
        "place": "QQ 私聊",
        "private": True,
    }


def grp_ctx(sender="乙", ask="这图啥意思"):
    return {
        "sender": sender,
        "to_bot": True,
        "chat": ["丙: 今晚吃啥"],
        "said": "",
        "ask": ask,
        "place": "QQ 群",
        "private": False,
    }


class Ev:
    """假的消息：segs = [("text", 字) / ("image", QQ 文件名, 本地路径, 是不是表情包)]"""

    _n = 0

    def __init__(self, umo, uid, name, segs, at=False):
        Ev._n += 1
        self.unified_msg_origin = umo
        self._uid, self._name = str(uid), name
        self._private = ":FriendMessage:" in umo
        self._gid = "" if self._private else umo.rsplit(":", 1)[-1]
        self.is_at_or_wake_command = bool(at)
        self.bot = None
        self._extra = {}
        comps, raw, text = [], [], ""
        for s in segs:
            if s[0] == "text":
                comps.append(Plain(s[1]))
                raw.append({"type": "text", "data": {"text": s[1]}})
                text += s[1]
            else:
                comps.append(Image(file=s[2], path=s[2]))
                raw.append(
                    {
                        "type": "image",
                        "data": {"file": s[1], "sub_type": "1" if s[3] else "0"},
                    }
                )
        self._comps = comps
        self.message_str = text
        self.message_obj = types.SimpleNamespace(
            raw_message={"message": raw}, message_id=f"m{Ev._n}"
        )

    def get_self_id(self):
        return SID

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def get_messages(self):
        return self._comps

    def is_private_chat(self):
        return self._private

    def get_group_id(self):
        return self._gid

    def get_platform_name(self):
        return "aiocqhttp"

    def set_extra(self, k, v):
        self._extra[k] = v

    def get_extra(self, k):
        return self._extra.get(k)


async def settle(p):
    """等插件后台的任务（先看好 / 深度认）跑完"""
    for _ in range(20):
        ts = [t for t in p._tasks if not t.done()]
        if not ts:
            return
        await asyncio.wait(ts, timeout=5)


async def say(p, umo, uid, name, segs, at=False, reply=False):
    """一条消息进来；reply = 机器人要回这一条 → 返回回话请求里插件加的那几段（拼成一段字）"""
    ev = Ev(umo, uid, name, segs, at=at)
    await p.on_message(ev)
    if not reply:
        await settle(p)
        return ""
    req = ProviderRequest(prompt=ev.message_str)
    await p.on_llm_req(ev, req)
    await settle(p)
    out = []
    for part in req.extra_user_content_parts:
        out.append(part.get("text", "") if isinstance(part, dict) else getattr(part, "text", ""))
    return "\n".join(out)


async def main():
    img = write_img()
    md5 = "m:" + hashlib.md5(Path(img).read_bytes()).hexdigest()
    KEY = "f:" + "AB12" * 8 + ".png"

    # ═════════ A 看图结果：分对话 ═════════
    print("[A 私聊里带上下文看出来的那段，别的对话拿不到]")
    p = new_plugin("a")
    im_a = image(KEY, img, A_NAME, "111", said=SAID)
    cap1 = await p._caption(im_a, priv_ctx(), PRIV, SID, where="私聊")
    check(
        bool(cap1) and "吵架" in cap1 and len(CALLS) == 1,
        "（前提）私聊里那次：看了一次，假模型把私聊的话抄进了看图结果",
        cap1,
    )
    n = len(CALLS)
    cap2 = await p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    check(
        bool(cap2) and not leaks(cap2),
        "群里别人发同一张（同一个文件名）：拿到的看图结果里没有私聊带出来的内容",
        cap2,
    )
    check(
        len(CALLS) == n + 1 and not leaks(CALLS[-1]) and "今晚吃啥" in CALLS[-1],
        "群里那次是在自己的对话里重新看的（提示里只有这个群的上下文）",
        (len(CALLS) - n, CALLS[-1][:200]),
    )
    check(bool(cap2) and "在吗" in cap2 and "一只猫探头" in cap2, "群里照样看到了图上的字和画面", cap2)
    n = len(CALLS)
    cap3 = await p._caption(
        dict(im_a, t=time.time()), priv_ctx(), PRIV, SID, where="私聊"
    )
    check(
        cap3 == cap1 and len(CALLS) == n,
        "同一个人在同一个私聊里再发：直接用上次那段，不再花钱看",
        (len(CALLS) - n, cap3),
    )
    cap4 = await p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    check(
        cap4 == cap2 and len(CALLS) == n,
        "群里同一个人再发：用群里那次看的，不再花钱看",
        (len(CALLS) - n, cap4),
    )
    cap4b = await p._caption(
        dict(image("u:" + "ab" * 10, img, "乙", "222"), quoted=True),
        grp_ctx("丙", ask="他这张啥意思"),
        GRP,
        SID,
        where="群 999",
    )
    check(
        cap4b == cap2 and len(CALLS) == n,
        "同一个群里别人引用他那张（重新下载的、凭据换了，靠内容认）：用他那次看的，不再花钱看",
        (len(CALLS) - n, cap4b),
    )
    # 内容一样、换了个文件名（引用的图、被转存过的图）：靠内容的 md5 认出来
    KEY_B = "f:" + "EE99" * 8 + ".png"
    MODEL["fail"] = True
    cap5 = await p._caption(image(KEY_B, img, "丁", "444"), grp_ctx("丁"), GRP2, SID, where="群 888")
    MODEL["fail"] = False
    check(
        cap5 == PLAIN,
        "另一个群、换了文件名的同一张图，这次看图模型不能用：只拿得到「字 / 画面」（没有 梗 / 跟你 / 坑）",
        cap5,
    )
    n = len(CALLS)
    cap6 = await p._caption(
        image(KEY, img, "戊", "555"), grp_ctx("戊"), GRP2, SID, background=True, where="群 888"
    )
    check(
        cap6 == PLAIN and len(CALLS) == n,
        "没人找机器人、只是先看好（后台）：按图记住的「字 / 画面」够用，不另外花钱看",
        (len(CALLS) - n, cap6),
    )
    cap7 = await p._caption(image(KEY, img, "戊", "555"), grp_ctx("戊"), GRP2, SID, where="群 888")
    check(
        bool(cap7) and not leaks(cap7) and "戊" in cap7 and len(CALLS) == n + 1,
        "轮到机器人要回他了：在这个群里结合上下文看一次",
        (len(CALLS) - n, cap7),
    )
    await p.terminate()
    st = json.loads(data_file("a", "state.json").read_text(encoding="utf-8"))
    by_img = st.get("cache") or {}
    check(
        bool(by_img)
        and not leaks(by_img)
        and all(
            seer.fields([v.get("cap")]) == {"梗": [], "跟你": [], "坑": []}
            for v in by_img.values()
        ),
        "存档里按图共用的那份（state.json 的 cache）：只有「字 / 画面」",
        by_img,
    )
    keys_txt = json.dumps([k for sec in st.values() if isinstance(sec, dict) for k in sec], ensure_ascii=False)
    check(
        "FriendMessage" not in keys_txt and ":111" not in keys_txt and ":999" not in keys_txt,
        "存档的凭据里不出现对话的标识 / QQ 号（另存的那份只存哈希）",
        keys_txt[:300],
    )
    # 重启以后：两份都还在
    n = len(CALLS)
    p = new_plugin("a")
    cap8 = await p._caption(dict(im_a, t=time.time()), priv_ctx(), PRIV, SID, where="私聊")
    cap9 = await p._caption(image(KEY, img, "己", "666"), grp_ctx("己"), GRP, SID, background=True, where="群 999")
    check(
        cap8 == cap1 and cap9 == PLAIN and len(CALLS) == n,
        "重启以后：同一个私聊同一个人照样复用；别的对话照样只有「字 / 画面」",
        (len(CALLS) - n, cap8, cap9),
    )
    await p.terminate()

    # 两边同时在看同一张（私聊那次还没看完，群里也来了）
    print("\n[A2 两个对话同时看同一张]")
    p = new_plugin("a2")
    MODEL["gate"] = asyncio.Event()
    n = len(CALLS)
    t1 = asyncio.create_task(p._caption(image(KEY, img, A_NAME, "111", said=SAID), priv_ctx(), PRIV, SID, where="私聊"))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999"))
    t3 = asyncio.create_task(p._caption(image(KEY, img, A_NAME, "111", said=SAID), priv_ctx(), PRIV, SID, where="私聊"))
    await asyncio.sleep(0.05)
    MODEL["gate"].set()
    c1, c2, c3 = await asyncio.gather(t1, t2, t3)
    MODEL["gate"] = None
    check(
        bool(c1) and "吵架" in c1 and bool(c2) and not leaks(c2),
        "私聊那次还在看、群里同一张也来了：群里等到的不是私聊那段",
        c2,
    )
    check(
        c3 == c1 and len(CALLS) == n + 2,
        "同一个私聊同一个人连着发两遍：只看一次（第二遍等第一遍的结果）",
        (len(CALLS) - n, c3),
    )
    await p.terminate()

    # 不按格式写的模型：整段没法分出「字 / 画面」→ 按图那份不存
    print("\n[A3 看图模型没按「字 / 画面 / …」的格式写]")
    p = new_plugin("a3")
    MODEL["free"] = True
    c1 = await p._caption(image(KEY, img, A_NAME, "111", said=SAID), priv_ctx(), PRIV, SID, where="私聊")
    MODEL["free"] = False
    MODEL["fail"] = True
    c2 = await p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    MODEL["fail"] = False
    check(
        bool(c1) and "吵架" in c1 and not c2,
        "分不出哪部分是图本身的：整段都不给别的对话（那边看不成就是没看成）",
        (c1, c2),
    )
    await p.terminate()

    # ═════════ B 老版本的存档 ═════════
    print("\n[B 老版本留下的 state.json：整段是按图存的]")
    old_full = "字：「在吗」。画面：一只猫探头。梗：看不出出处。坑：" + A_NAME + "说「" + SAID + "」像在试探"
    DATA["dir"] = ROOT / "b"
    data_file("b", "x").parent.mkdir(parents=True, exist_ok=True)
    data_file("b", "state.json").write_text(
        json.dumps(
            {
                "cache": {
                    KEY: {"cap": old_full, "t": 1},
                    md5: {"cap": old_full, "t": 1},
                    "f:" + "0F0F" * 8 + ".png": {"cap": "一只猫，" + A_NAME + "说要分手", "t": 1},
                },
                "stats": {},
                "recent": [],
                "errors": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    p = new_plugin("b")
    MODEL["fail"] = True
    c1 = await p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    c2 = await p._caption(image("f:" + "0F0F" * 8 + ".png", write_img("other.png", b"\x00\x01"), "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    MODEL["fail"] = False
    check(c1 == PLAIN, "老存档里的整段：只留下「字 / 画面」，别的丢掉", c1)
    check(not c2, "老存档里分不出「字 / 画面」的：整条丢掉", c2)
    n = len(CALLS)
    c3 = await p._caption(image(KEY, img, "乙", "222"), grp_ctx(), GRP, SID, where="群 999")
    check(
        bool(c3) and not leaks(c3) and len(CALLS) == n + 1,
        "老存档里有的图：下次要回话时重新看一次（结合这个对话）",
        (len(CALLS) - n, c3),
    )
    await p.terminate()
    check(
        not leaks(json.loads(data_file("b", "state.json").read_text(encoding="utf-8")).get("cache")),
        "存回去的 state.json：按图那份里没有老存档混着的聊天内容",
    )

    # ═════════ C 表情包词典 ═════════
    print("\n[C 表情包词典：私聊里的话不进词典]")
    p = new_plugin("c")
    SK = "CD34" * 8 + ".gif"
    n = len(CALLS)
    for line in CHAT:
        await say(p, PRIV, "111", A_NAME, [("text", line)])
    await say(p, PRIV, "111", A_NAME, [("text", SAID), ("image", SK, img, True)])
    deep_prompts = [c for c in CALLS[n:] if "表情包词典" in c]
    check(len(deep_prompts) == 1, "私聊里直接拿表情包找机器人：照样认一次、记成词条", len(deep_prompts))
    check(
        bool(deep_prompts) and not leaks(deep_prompts[0]),
        "私聊里认的时候：提示里不带私聊的上下文（谁发的、说了什么、前面几句）",
        deep_prompts[0][:300] if deep_prompts else "",
    )
    item = p.stickers.get("f:" + SK) or {}
    check(
        item.get("n") == 1 and not leaks(item) and not item.get("ctx"),
        "私聊里那次只记次数：前后说了什么不记进词典",
        item,
    )
    check(
        not any("111" in str(c) or "Friend" in str(c) for c in item.get("convs") or []),
        "私聊的会话标识（里面有对方的 QQ 号）不存",
        item.get("convs"),
    )
    entry = p.stickers.lookup("f:" + SK)
    check(bool(entry) and not leaks(entry), "认好的词条里没有私聊的内容", entry)
    n = len(CALLS)
    out = await say(p, GRP, "222", "乙", [("image", SK, img, True)], at=True, reply=True)
    check(
        "委屈猫" in out and not leaks(out) and len(CALLS) == n,
        "群里别人发同一张表情包：直接用词条（不再看），给机器人的那段里没有私聊的内容",
        (len(CALLS) - n, out[:300]),
    )
    # 群里的用法照常记
    SK2 = "EF56" * 8 + ".gif"
    await say(p, GRP, "333", "丙", [("text", "今晚吃啥")])
    await say(p, GRP, "222", "乙", [("text", "随便"), ("image", SK2, img, True)])
    await say(p, GRP, "333", "丙", [("text", "笑死")])
    it2 = p.stickers.get("f:" + SK2) or {}
    c0 = (it2.get("ctx") or [{}])[-1]
    check(
        it2.get("convs") == [GRP]
        and (c0.get("before") or [""])[-1] == "今晚吃啥"
        and c0.get("said") == "随便"
        and c0.get("after") == "笑死",
        "群里发的照常记：在哪个群、发它前后说了什么",
        it2,
    )
    # 私聊里发群里见过的那张：次数加一，群里记的那几句不动
    await say(p, PRIV, "111", A_NAME, [("text", "别跟别人说哦"), ("image", SK2, img, True)])
    it2 = p.stickers.get("f:" + SK2) or {}
    check(
        it2.get("n") == 2 and len(it2.get("ctx") or []) == 1 and it2.get("convs") == [GRP] and not leaks(it2.get("ctx")),
        "私聊里发群里见过的那张：次数照算，群里记的那几句不动、私聊的话不加进去",
        it2,
    )
    await p.terminate()
    sj = data_file("c", "stickers.json").read_text(encoding="utf-8")
    check(not leaks(sj) and "FriendMessage" not in sj, "stickers.json 里没有私聊的话、没有私聊的会话标识", sj[:400])

    # 转发的聊天记录里的图该深度认了：转发里的话也不带（词条是所有对话共用的）
    print("\n[C2 转发的聊天记录里的表情包]")
    p = new_plugin("c2")
    FK = "f:" + "7A7A" * 8 + ".gif"
    for i in range(3):
        p.stickers.seen(FK, GRP, f"u{i}", before=["群里的话"], said="", sticker=True)
    n = len(CALLS)
    fctx = {
        "sender": A_NAME,
        "to_bot": True,
        "chat": [f"{A_NAME}: {x}" for x in CHAT],
        "said": SAID,
        "ask": "看看这个",
        "place": "QQ 群",
        "via": "转发的聊天记录",
        "private": False,
    }
    await p._caption(image(FK, img, A_NAME, "", said=SAID, kind="表情包"), fctx, GRP, SID, where="群 999")
    dp = [c for c in CALLS[n:] if "表情包词典" in c]
    check(
        len(dp) == 1 and not leaks(dp[0]) and "群里的话" in dp[0],
        "转发的聊天记录里的表情包该深度认了：转发里的话不带进提示，群里真人的用法照带",
        dp[0][:300] if dp else "",
    )
    await p.terminate()

    # ═════════ D 走一遍真的流程 ═════════
    print("\n[D 每条消息 → 回话请求]")
    p = new_plugin("d", sticker_dict=False)
    for line in CHAT:
        await say(p, PRIV, "111", A_NAME, [("text", line)])
    n = len(CALLS)
    out1 = await say(p, PRIV, "111", A_NAME, [("text", SAID), ("image", "AB12" * 8 + ".png", img, False)], reply=True)
    check(
        len(CALLS) == n + 1 and "吵架" in out1,
        "（前提）私聊里发图：看了一次，机器人这一轮拿到的是带私聊上下文的那段",
        out1[:200],
    )
    await say(p, GRP, "333", "丙", [("text", "今晚吃啥")])
    out2 = await say(p, GRP, "222", "乙", [("text", "这图啥意思"), ("image", "AB12" * 8 + ".png", img, False)], at=True, reply=True)
    check(
        "在吗" in out2 and not leaks(out2),
        "之后群里有人发同一张并 @ 机器人：给机器人的那几段（记进聊天记录的、只给这一轮的）里都没有私聊的内容",
        out2[:400],
    )
    n = len(CALLS)
    out3 = await say(p, PRIV, "111", A_NAME, [("text", "再看看"), ("image", "AB12" * 8 + ".png", img, False)], reply=True)
    check(
        len(CALLS) == n and "吵架" in out3,
        "同一个人回到私聊再发：还是他自己那段（不再看）",
        (len(CALLS) - n, out3[:200]),
    )
    await p.terminate()


asyncio.run(main())
done()
