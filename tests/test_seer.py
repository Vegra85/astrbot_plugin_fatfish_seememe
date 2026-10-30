#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seer.py 单独测（不用装 AstrBot）：python tests/test_seer.py [插件目录 默认上一层]"""

import importlib.util
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
