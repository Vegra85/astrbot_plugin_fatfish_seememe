#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seer.py 单独测（不用装 AstrBot）：python tests/test_seer.py [插件目录 默认上一层]"""

import importlib.util
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
spec = importlib.util.spec_from_file_location("seer", d / "seer.py")
seer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seer)
RES = []


def check(c, m, why=""):
    RES.append((bool(c), m))
    print(
        ("  [过]   " if c else "  [没过] ")
        + m
        + ("" if c or not why else f"\n           {str(why)[:600]}")
    )


print("[1 同一张图的凭据]")
check(
    seer.key_ok("8A3C3B7CF2D94E1A0B5C6D7E8F901234.jpg")
    and seer.key_ok("{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}.png")
    and seer.key_ok("a3c5e8b0d2f4a6c8e0b2d4f6a8c0e2b4"),
    "QQ 的哈希文件名 / 表情 id 能当凭据",
)
check(
    not any(
        seer.key_ok(x)
        for x in (
            "image.jpg",
            "0.png",
            "",
            None,
            "https://x.cn/a.jpg",
            "file:///tmp/a.png",
            "abc123.gif",
        )
    ),
    "「image.jpg」「0.png」、网址、短名字不行（会撞）",
)

print("\n[2 给看图模型的话]")
ctx = {
    "sender": "隔壁的座头鲸",
    "to_bot": True,
    "said": "这不就是你吗",
    "ask": "你自己看看",
    "place": "QQ 群",
    "chat": ["a: 一", "b: 二", "c: 三", "d: 四", "e: 五"],
}
p = seer.build_prompt(
    ["大肥鱼", "肥鱼"],
    "蓝头发、戴鱼形发卡的女孩",
    ctx,
    ref=True,
    kind="表情包",
    max_chars=150,
)
check(
    p.startswith("这是 QQ 群里「隔壁的座头鲸」发的一张表情包") and "冲着它来的" in p,
    "在哪、谁发的、是表情包、是冲着机器人来的",
    p[:120],
)
check(
    "「大肥鱼、肥鱼」" in p and "蓝头发" in p and "就是在说它本人" in p,
    "机器人的名字、长相、「像它就是在说它」",
    p,
)
check(
    "第一张是机器人自己的头像" in p and "第二张" in p,
    "带头像对照：说清楚第一张是头像",
    p,
)
check(
    "发图的时候还说了：「这不就是你吗」" in p
    and "后来有人找机器人说：「你自己看看」" in p,
    "发图时说的话、后来 @ 它说的话",
    p,
)
check(
    "a: 一" not in p and "b: 二 / c: 三 / d: 四 / e: 五" in p,
    "之前的聊天只带最后 4 句",
    p,
)
check(
    "不超过 150 字" in p and "跟你：" in p and "坑：" in p and "别写「坑：没有」" in p,
    "五样 + 字数 + 没有就别写",
    p[-400:],
)
p2 = seer.build_prompt([], "", {"sender": "老王", "said": "笑死", "ask": "笑死"})
check(
    p2.startswith("这是群聊里「老王」")
    and "「这个机器人」" in p2
    and "长得像它" not in p2
    and "第一张" not in p2
    and "后来有人找" not in p2,
    "没名字没长相没头像：不硬说「像它就是它」；后来说的跟发图时一样就不重复",
    p2[:300],
)
p3 = seer.build_prompt(
    ["大肥鱼"], "", {}, ref=True, note="动图（按顺序抽了几帧拼成一张）"
)
check(
    "就是在说它本人" in p3 and "当成一个动作过程" in p3 and "不要提「拼图」" in p3,
    "只有头像也能认它；动图当动作过程讲",
    p3[-200:],
)

print("\n[3 收尾]")
check(
    seer.trim("这是一张搞笑的图片，  字：「给token」。\n画面：一条鱼。")
    == "字：「给token」。 画面：一条鱼",
    "去掉「这是一张…」、空白合并、末尾句号",
    seer.trim("这是一张搞笑的图片，  字：「给token」。\n画面：一条鱼。"),
)
long = "画面：" + "一条鱼在游，" * 60 + "最后。"
t = seer.trim(long, 100)
check(
    len(t) <= 101 and t.endswith("…") and not t.endswith("，…"),
    "太长：在标点处截断、加省略号",
    t,
)

check(
    seer.trim("<think>先想想这是什么</think>画面：一条鱼。") == "画面：一条鱼",
    "推理模型漏出来的 <think> 去掉",
)
check(
    all(
        seer.refused(x)
        for x in (
            "抱歉，我无法查看图片",
            "我看不到这张图",
            "I'm sorry, I can't help with that",
            "作为AI，我不能",
        )
    )
    and not any(
        seer.refused(x)
        for x in (
            "画面：一条鱼。梗：看不出出处",
            "抱歉体的表情包。画面：一个人鞠躬",
            "字：「对不起」。画面：一只猫鞠躬",
        )
    ),
    "看图模型拒绝了 / 说看不到图：认得出；图里的字是「对不起」不算",
)

print("\n[4 拆出 梗 / 跟你 / 坑]")
caps = [
    "字：「给token」。画面：一条胖鱼抱着金币。梗：「给X什么都做」网络模板。跟你：图里的胖鱼是机器人的 Q 版。坑：没有，纯属玩梗",
    "画面：一车鲸鱼。梗：网络模板「找一车X来弄你」]。跟你：没关系。坑：问它要不要上车）",
]
f = seer.fields(caps)
check(
    f["梗"] == ["「给X什么都做」网络模板", "网络模板「找一车X来弄你」"],
    "梗：两张都拆出来（不吞后面的 ]）",
    f["梗"],
)
check(f["跟你"] == ["图里的胖鱼是机器人的 Q 版"], "跟你：「没关系」那条不算", f["跟你"])
check(
    f["坑"] == ["问它要不要上车"],
    "坑：「没有，纯属玩梗」不算；多出来的 ）去掉",
    f["坑"],
)
f2 = seer.fields(
    ["坑：没坑。跟你：无。梗：看不出出处", "跟你：与你无关。坑：就是逗你玩"]
)
check(
    not f2["跟你"] and not f2["坑"] and not f2["梗"],
    "没坑 / 无 / 看不出 / 与你无关 / 就是逗你玩：都不算",
    f2,
)

print("\n[5 给机器人看的那几段]")
cb = seer.caption_block(
    [("老王", caps[0], False), ("小鲸", caps[1], True), ("空的", "", False)]
)
check(
    cb.startswith(seer.HEAD)
    and "- 「老王」发的图：字：「给token」" in cb
    and "- 引用的消息里「小鲸」发的图" in cb
    and "空的" not in cb,
    "看到了什么：谁发的、引用的标出来、空的不写",
    cb,
)
check(seer.caption_block([]) == "" and seer.block([]) == "", "没图：什么都不加")
hb = seer.hints_block(caps)
check(
    "别把画面复述一遍" in hb
    and "梗点：「给X什么都做」网络模板" in hb
    and "图在说你（图里的胖鱼是机器人的 Q 版）" in hb
    and "图里有坑（问它要不要上车）" in hb
    and "别说教" in hb
    and "客服句式" in hb,
    "怎么接：梗点、图在说你、有坑",
    hb,
)
check(
    "not included" not in hb
    and "not included" in seer.hints_block(caps, blind=True)
    and "not included" not in seer.hints_block([], blind=True),
    "对话模型自己看不到图：提醒它 AstrBot 后面那句「原图没给你」不碍事（没看图结果就不说）",
)
hb0 = seer.hints_block([])
check(
    "别把画面复述一遍" in hb0
    and "客服句式" in hb0
    and "- 梗点：" not in hb0
    and "图在说你" not in hb0
    and "有坑" not in hb0,
    "没看图结果（对话模型自己看图）：只给通用的几条",
    hb0,
)
b = seer.block([("老王", caps[0], False)])
check(
    b.startswith(seer.HEAD)
    and "# 这一轮怎么接" in b
    and "# 这一轮怎么接" not in seer.block([("老王", caps[0], False)], hints=False),
    "合在一起的 / 关掉提示的",
    b[-200:],
)

print("\n[6 说到图了没有]")
yes = [
    "上面那张图什么意思",
    "这图笑死",
    "看看楼上",
    "图里是谁啊",
    "楼上发的图",
    "这表情包啥意思",
    "这是什么图",
    "刚才那张",
    "那个截图",
    "你瞅一眼这个",
    "大肥鱼你看图",
]
no = [
    "图书馆几点关门",
    "地图上标的那个店",
    "今天吃啥",
    "我企图说服你",
    "这个想法不错",
    "拼图好难",
]
check(
    all(seer.TEXT_REFERS.search(x) for x in yes),
    "说到图的都认得",
    [x for x in yes if not seer.TEXT_REFERS.search(x)],
)
check(
    not any(seer.TEXT_REFERS.search(x) for x in no),
    "图书馆 / 地图上 / 企图 / 拼图 不算",
    [x for x in no if seer.TEXT_REFERS.search(x)],
)

print("\n[7 记住看过的图]")
c = seer.Cache(keep=2)  # 两张图 = 最多 4 条凭据
c.put(["f:a", "m:1"], "一")
c.put(["f:b", "m:2"], "二")
check(
    c.get("f:a") == "一" and c.get("x", "m:1") == "一" and c.get("nope") is None,
    "按任意一个凭据都能找到",
)
c.put(["f:c", "m:3"], "三")  # 满了：丢最久没用的（「二」：刚才用过「一」）
check(
    c.get("f:b") is None
    and c.get("m:2") is None
    and c.get("f:a") == "一"
    and c.get("m:3") == "三"
    and len(c.d) == 4,
    "满了丢最久没用的那张（两个凭据一起丢）",
    list(c.d),
)
c2 = seer.Cache(keep=1)
c2.load({**c.dump(), "bad": {"x": 1}, "bad2": "y"})
check(
    set(c2.d) == {"f:a", "m:3"}, "存档读回来：坏的跳过、超了只留最近用过的", list(c2.d)
)


def need(*names):
    """v0.2.0 新加的函数：老代码里没有 → 这一节记成没过（不让整个脚本崩掉）"""
    miss = [n for n in names if not hasattr(seer, n)]
    if miss:
        check(False, "seer.py 里有 " + "、".join(miss), "没有这些函数")
    return not miss


print("\n[8 转发的聊天记录（v0.2.0）]")
if need("parse_forward", "forward_lines", "forward_nodes", "row_text"):
    U = "http://img.example/"
    napcat = {
        "messages": [
            {
                "sender": {"user_id": 1, "nickname": "鲸鱼甲", "card": ""},
                "message": [
                    {"type": "text", "data": {"text": "你们看这个"}},
                    {
                        "type": "image",
                        "data": {
                            "file": "AB12CD34EF56AB12CD34EF56AB12CD34.jpg",
                            "url": U + "1.png",
                            "sub_type": 0,
                        },
                    },
                ],
            },
            {
                "sender": {"user_id": 2, "nickname": "鲸鱼乙", "card": "乙乙"},
                "message": [
                    {
                        "type": "image",
                        "data": {"file": "x.jpg", "url": U + "2.png", "sub_type": 1},
                    }
                ],
            },
            {
                "sender": {"nickname": "鲸鱼丙"},
                "message": [{"type": "forward", "data": {"id": "123"}}],
            },
            {"sender": {"nickname": "鲸鱼丁"}, "message": []},
        ]
    }
    rows, imgs = seer.parse_forward(napcat)
    check(
        [r["who"] for r in rows] == ["鲸鱼甲", "乙乙", "鲸鱼丙"]
        and len(imgs) == 2
        and imgs[0]["ref"] == U + "1.png"
        and imgs[0]["file"].startswith("AB12")
        and imgs[0]["kind"] == ""
        and imgs[1]["kind"] == "表情包"
        and imgs[1]["row"] == 1,
        "NapCat 格式：谁发的（群名片优先）、图的地址和文件名、表情包认得出、空消息跳过",
        (rows, imgs),
    )
    check(
        seer.row_text(rows[2]) == "[聊天记录]"
        and seer.row_text(rows[0], imgs) == "你们看这个[图片]",
        "套在里面的转发只写「[聊天记录]」；没看的图照旧「[图片]」",
        [seer.row_text(r, imgs) for r in rows],
    )
    std = {
        "data": {
            "message": [
                {
                    "type": "node",
                    "data": {
                        "nickname": "老张",
                        "content": "笑死[CQ:image,file=a.jpg,url=http://img.example/3.png&#44;x]",
                    },
                },
                {
                    "type": "node",
                    "data": {
                        "name": "老李",
                        "content": [
                            {
                                "type": "mface",
                                "data": {
                                    "summary": "[狗头]",
                                    "url": U + "4.png",
                                    "emoji_id": "e1",
                                },
                            }
                        ],
                    },
                },
            ]
        }
    }
    rows2, imgs2 = seer.parse_forward(std)
    check(
        [r["who"] for r in rows2] == ["老张", "老李"]
        and imgs2[0]["ref"] == "http://img.example/3.png,x"
        and imgs2[1]["kind"] == "QQ 表情「狗头」",
        "OneBot 标准格式（node）、CQ 码字符串、外面再包一层 data、商城表情",
        (rows2, imgs2),
    )
    check(
        seer.parse_forward(None) == ([], [])
        and seer.parse_forward({"messages": "坏的"}) == ([], [])
        and seer.forward_nodes([{"a": 1}]) == [{"a": 1}],
        "读不出来的：空着，不报错",
    )
    fl = seer.forward_lines(
        "老王", rows, imgs, {1: "画面：一只猫。梗：猫猫震惊"}, total=4
    )
    check(
        fl.startswith("- 「老王」转发的聊天记录（4 条）：")
        and "  鲸鱼甲: 你们看这个[图片]" in fl
        and "  乙乙: [表情包：画面：一只猫。梗：猫猫震惊]" in fl
        and seer.fields([fl])["梗"] == ["猫猫震惊"],
        "排成一段：看懂的写进那一行，没看成的照旧；「这一轮怎么接」拆得出梗",
        fl,
    )
    many = [{"who": f"人{i}", "parts": ["一句话" * 10]} for i in range(30)]
    fl2 = seer.forward_lines("老王", many, [], {}, quoted=True, max_chars=300)
    check(
        fl2.startswith("- 引用的那条「老王」转发的聊天记录（30 条）")
        and len(fl2) < 420
        and "后面还有" in fl2,
        "太长：截断、说后面还有几条；引用的标出来",
        fl2,
    )
    long_row = {"who": "甲", "parts": ["字" * 200, ("img", 0)]}
    check(
        len(seer.row_text(long_row, [{}], {}, clip=80)) <= 82,
        "一行的文字最多 80 字",
        seer.row_text(long_row, [{}], {}),
    )
    pf = seer.build_prompt(
        ["大肥鱼"],
        "",
        {
            "sender": "鲸鱼甲",
            "to_bot": True,
            "via": "转发的聊天记录",
            "said": "你们看这个",
            "chat": ["鲸鱼乙: 前面一句"],
            "place": "QQ 群",
        },
    )
    check(
        pf.startswith("这是 QQ 群里有人发的转发的聊天记录中「鲸鱼甲」发的一张图")
        and "聊天记录里这条消息的字：「你们看这个」" in pf
        and "聊天记录里这张图前面几句：鲸鱼乙: 前面一句" in pf
        and "让看不到图的机器人也能接话" in pf,
        "看转发里的图：告诉看图模型这是转发的聊天记录里谁发的、前面几句",
        pf[:400],
    )
    cb2 = seer.caption_block([], [fl])
    check(
        cb2.startswith(seer.HEAD) and fl in cb2 and seer.caption_block([], []) == "",
        "「看到了什么」里能只有转发那段",
        cb2,
    )

print("\n[9 表情包词典：深度认（v0.2.0）]")
if need("parse_deep", "deep_caption", "deep_rules"):
    pd = seer.build_prompt(
        ["大肥鱼"],
        "",
        {"sender": "老王", "place": "QQ 群"},
        kind="表情包",
        deep=True,
        usage=["之前：今天又加班 / 好累；发图的人说：裂开", "之前：（没人说话）"],
        note="动图（按顺序抽了几帧拼成一张）",
    )
    check(
        seer.DEEP_TAG in pd
        and "逐字照抄" in pd
        and "梗：" in pd
        and "一般用来：" in pd
        and "把握：" in pd
        and "- 之前：今天又加班 / 好累；发图的人说：裂开" in pd
        and "当成一个动作过程" in pd
        and "让看不到图的机器人也能接话" not in pd
        and pd.startswith("这是 QQ 群里「老王」发的一张表情包"),
        "深度认的提示：逐字抄字、梗 / 出处、一般什么时候发、把握、群里真人怎么用它、动图",
        pd,
    )
    raw = (
        "<think>想想</think>名字：熊猫头裂开\n字：「我裂开了」\n画面：熊猫头的脸裂成两半\n"
        "梗：熊猫头表情包系列\n意思：崩溃、无语\n一般用来：被坑了、心态崩了的时候\n跟你：无\n坑：没有\n把握：85"
    )
    e = seer.parse_deep(raw)
    check(
        e
        and e["name"] == "熊猫头裂开"
        and e["text"] == "我裂开了"
        and e["meme"] == "熊猫头表情包系列"
        and e["usage"].startswith("被坑了")
        and e["you"] == ""
        and e["trap"] == ""
        and e["conf"] == 85,
        "拆词条：一行一项、去掉 <think>、「无」「没有」算空、把握是数",
        e,
    )
    e2 = seer.parse_deep(
        "名字：鞠躬猫。字：无。画面：一只猫鞠躬。梗：不确定。意思：道歉。一般用来：认错的时候。跟你：图里的猫戴着机器人的发卡。把握：30分"
    )
    check(
        e2
        and e2["name"] == "鞠躬猫"
        and e2["text"] == ""
        and e2["meme"] == ""
        and e2["you"].startswith("图里的猫")
        and e2["conf"] == 30,
        "写成一行也拆得开；「不确定」的梗算空",
        e2,
    )
    check(
        seer.parse_deep("抱歉，我无法查看图片") is None and seer.parse_deep("") is None,
        "什么都没写出来 → None",
    )
    dc = seer.deep_caption(e, kind="表情包")
    check(
        dc.startswith("表情包「熊猫头裂开」：字：「我裂开了」。画面：")
        and "一般用来：被坑了" in dc
        and seer.fields([dc])["梗"] == ["熊猫头表情包系列"]
        and not seer.fields([dc])["跟你"],
        "词条 → 给机器人看的那句（梗拆得出来，没关系的「跟你」不写）",
        dc,
    )
    dc2 = seer.deep_caption(e2, anim=True)
    check(
        dc2.startswith("常见的图「鞠躬猫」（动图）：")
        and "梗：看不出出处" in dc2
        and seer.fields([dc2])["跟你"] == ["图里的猫戴着机器人的发卡"]
        and not seer.fields([dc2])["梗"],
        "认不出的梗写「看不出出处」（不当梗点）；图在说它照样拆得出",
        dc2,
    )

print("\n[10 表情包词典：记几次、什么时候认、存档（v0.2.0）]")
if need("StickerDict"):
    T = 1_800_000_000.0
    sd = seer.StickerDict(after=3, daily=2, keep=50)
    check(
        not sd.seen("f:A", "g1", "u1", before=["前一句"], said="哈", now=T)
        and not sd.seen("f:A", "g2", "u2", now=T + 10),
        "前两次：不认",
    )
    sd.after_msg("g2", "u2", "自己接自己不算", now=T + 11)
    sd.after_msg("g2", "u3", "笑死", now=T + 12)
    sd.after_msg("g2", "u4", "第二句不算", now=T + 13)
    check(
        sd.seen("f:A", "g1", "u3", now=T + 20)
        and sd.get("f:A")["n"] == 3
        and sd.get("f:A")["convs"] == ["g1", "g2"],
        "同一张第 3 次（换群也算）：该认了",
    )
    us = sd.usage("f:A")
    check(
        us[0] == "之前：前一句；发图的人说：哈"
        and us[1].endswith("之后有人接：笑死")
        and len(us) == 3,
        "群里真人发它前后在说什么（发完 60 秒内别人接的第一句）",
        us,
    )
    sd.seen("f:B", "g1", "u1", now=T)
    check(not sd.due("f:B", T), "普通的图第一次直接发给机器人：不认")
    check(
        sd.seen("f:S", "g1", "u1", to_bot=True, sticker=True, now=T)
        and not seer.StickerDict().seen("f:S", "g1", "u1", sticker=True, now=T),
        "表情包直接发给机器人：第一次就认；没发给它的不急",
    )
    sd.started(T)
    sd.started(T)
    check(not sd.due("f:A", T), "每天深度认的张数到上限：先不认")
    check(sd.due("f:A", T + 86400), "第二天又能认了")
    sd.put_deep("f:A", {**e, "cap": dc, "t": int(T)}, also=["m:md5a"])
    check(
        not sd.due("f:A", T + 86400)
        and sd.lookup("f:A") == dc
        and sd.lookup("nope", "m:md5a") == dc
        and sd.get("f:A")["n"] == 3,
        "认好了：按凭据 / 图片内容的 md5 都查得到；不再认",
    )
    sd.put_deep("f:S", {**e2, "cap": dc2})
    check(sd.lookup("f:S") is None, "把握不够的词条：不拿来用")
    sd.link("u:zzz", "m:md5a")
    check(sd.lookup("u:zzz") == dc, "内容一样的另一张（别的凭据）：用同一条")
    sd.seen("f:A", "g3", "u9", now=T + 30)
    check(
        sd.get("f:A")["n"] == 4 and sd.lookup("m:md5a") == dc,
        "认好以后再见到：照样记次数、词条还在",
    )
    cnt = sd.count(T)
    check(
        cnt["known"] == 1 and cnt["tracked"] == 3 and cnt["today"] == 2,
        "数：记着几张、认好几张、今天认了几张",
        cnt,
    )
    sd2 = seer.StickerDict(keep=50)
    sd2.load(
        json.loads(
            json.dumps(
                {**sd.dump(), "junk": 1, "alias": {**sd.dump()["alias"], "x": "gone"}}
            )
        )
    )
    check(
        sd2.lookup("m:md5a") == dc
        and sd2.lookup("u:zzz") == dc
        and "x" not in sd2.alias
        and sd2.get("f:A")["n"] == 4,
        "存档读回来：词条、别名都在；指向没了的别名扔掉",
    )
    sd3 = seer.StickerDict(keep=10)
    sd3.put_deep("f:keep", {**e, "cap": dc})
    for i in range(12):
        sd3.seen(f"f:{i}", "g", "u", now=T + i)
    sd3.seen("f:11", "g", "u", now=T + 99)
    check(
        len(sd3.items) == 10
        and sd3.lookup("f:keep") == dc
        and "f:11" in sd3.items
        and "f:0" not in sd3.items,
        "太多了：先扔只见过一次、最久没见的；认好的留着",
        sorted(sd3.items),
    )
    lst = sd.listing()
    check(
        len(lst) == 1 and lst[0]["name"] == "熊猫头裂开" and lst[0]["n"] == 4,
        "面板用的列表：认好的那几条",
        lst,
    )

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
