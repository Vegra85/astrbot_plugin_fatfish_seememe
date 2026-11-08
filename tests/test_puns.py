#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""puns.py 单独测（不用装 AstrBot；按读音找要装 pypinyin）：python tests/test_puns.py [插件目录 默认上一层]
v0.3.0 新加：图里的字 → 拼音 → 可能的谐音"""

import importlib.util
import sys
import time
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
RES = []


def check(c, m, why=""):
    RES.append((bool(c), m))
    print(
        ("  [过]   " if c else "  [没过] ")
        + m
        + ("" if c or not why else f"\n           {str(why)[:600]}")
    )


if not (d / "puns.py").is_file():
    check(False, "插件里有 puns.py（v0.3.0 新加的；老代码没有）")
    sys.exit(1)
spec = importlib.util.spec_from_file_location("puns", d / "puns.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def cap(text, more="画面：一张图。梗：看不出出处"):
    return f"字：「{text}」。{more}"


print("[1 从看图结果里拿出图里的字]")
check(
    P.image_text("字：「我太南了」。画面：一个人瘫在沙发上") == "我太南了"
    and P.image_text("表情包「熊猫头」：字：「他说「好」了」。画面：x")
    == "他说「好」了"
    and P.image_text("字：给token。画面：一条鱼") == "给token"
    and P.image_text("名字：熊猫头。画面：熊猫头") == ""
    and P.image_text("画面：一只猫。梗：看不出出处") == ""
    and P.image_text("") == "",
    "「字：「…」」原样拿（里面再套引号也行）；没引号的到句号为止；「名字：」不算；没字 → 空",
)
check(
    P.image_text("字：「1、2、3、4、5、6」。画面：一只猫") == ""
    and P.image_text("字：「AI生成」。画面：一个女孩") == ""
    and P.image_text("字：「我太南了；AI生成」。画面：x") == "我太南了",
    "动图拼图的帧号、「AI生成」水印不算图里的字，不拿去查谐音（v0.3.2）",
)

print("\n[2 有谐音：写读音和候选]")
t0 = time.time()
n1 = P.note(cap("我太南了"))
took = time.time() - t0
sound = P.ready()
if sound:
    check(
        n1
        == "谐音（按读音查的）：图里的字读作「wǒ tài nán le」，可能是 太南了 = 太难了（tài nán le）",
        "「我太南了」→ 读音 + 「太南了 = 太难了」",
        n1,
    )
    n2 = P.note(cap("你可真是夺笋啊"))
    check(
        "读作「nǐ kě zhēn shì duó sǔn a」" in n2 and "夺笋 = 多损（duō sǔn）" in n2,
        "「夺笋」= 多损，两边的声调都写上（duó / duō）",
        n2,
    )
    n3 = P.note(cap("蚌埠住了"))
    check(
        "bèng bù zhù le" in n3 and "= 绷不住了" in n3,
        "多音字按对上的那个读音写（蚌埠：bèng，不是最常用的 bàng）",
        n3,
    )
    check(
        "太蓝了 = 太难了" in P.note(cap("我太蓝了"))
        and "压历山大 = 压力山大" in P.note(cap("压历山大"))
        and "鸭梨三大 = 压力山大" in P.note(cap("鸭梨三大")),
        "不在词表写法里的也按读音找得到：「太蓝了」（n/l 近音）、「压历山大」、「鸭梨三大」（s/sh 近音）",
        [P.note(cap(x)) for x in ("我太蓝了", "压历山大", "鸭梨三大")],
    )
else:
    check(False, "装了 pypinyin（按读音找要它；pip install pypinyin）", P.how())
check(
    "蓝瘦，香菇 = 难受想哭" in P.note(cap("蓝瘦，香菇"))
    and "栓Ｑ = thank you（谢谢，常带点无奈）" in P.note(cap("我真的栓Ｑ"))
    and "shuān q」" in P.note(cap("我真的栓Ｑ"))
    and "520 = 我爱你" in P.note(cap("520快乐")),
    "中间夹标点、全角字母、数字谐音都认；外语的写上意思",
    [P.note(cap(x)) for x in ("蓝瘦，香菇", "我真的栓Ｑ", "520快乐")],
)
check(took < 1.0, f"第一次查（要读读音表）也很快：{took:.3f} 秒", took)

print("\n[3 没谐音 / 不该给的：什么都不加]")
none_caps = {
    "没有字": "画面：一只猫。梗：看不出出处",
    "普通的字": cap("周一"),
    "英文": cap("给token"),
    "看图模型自己已经认出来了": "字：「夺笋啊」。画面：一个人在挖笋。梗：「夺笋」谐音多损",
    "原词就写在图上": cap("蚌埠住了 不是 是真的绷不住了"),
    "繁体写的原词": cap("壓力山大"),
    "本身就是词表里的另一个原词": cap("年年有余"),
    "价钱、数量": cap("今天特价 250 克"),
    "期数": cap("第520期"),
    "房间号": cap("房间号 886"),
    "三个字换了两个": cap("我去台南了"),
}
got_none = {k: P.note(v) for k, v in none_caps.items()}
check(
    not any(got_none.values()),
    "没字、普通的字、看图模型已经写出原词、原词写在图上、繁体、价钱期数房间号、地名「台南了」：都不加",
    {k: v for k, v in got_none.items() if v},
)

print("\n[4 自己加的谐音词（配置里的「自己加的谐音词」）]")
extra = ("蒜你狠=算你狠", "鱼塘主")
check(
    "蒜你狠 = 算你狠" in P.note(cap("今年的蒜你狠"), extra)
    and not P.note(cap("今年的蒜你狠")),
    "「谐音写法=原词」：加了才认",
    P.note(cap("今年的蒜你狠"), extra),
)
if sound:
    check(
        "渔塘主 = 鱼塘主" in P.note(cap("我是渔塘主"), extra),
        "只写原词（三个字以上）：按读音找",
        P.note(cap("我是渔塘主"), extra),
    )
check(
    P.parse(["# 注释", "", "a|b c|说明", "夺笋＝多损", "多损"])
    == [("a", ["b", "c"], "说明"), ("多损", ["夺笋"], ""), ("多损", [], "")],
    "词表格式 / 用户写法都拆得开（全角等号也认；# 开头的是注释）",
    P.parse(["# 注释", "", "a|b c|说明", "夺笋＝多损", "多损"]),
)

print("\n[5 词表本身]")
rows = P.parse(P.TABLE.splitlines())
forms = [f for _t, fs, _n in rows for f in fs]
check(
    40 <= len(rows) <= 200
    and all(t not in fs for t, fs, _n in rows)
    and len(forms) == len(set(forms))
    and all(fs for t, fs, _n in rows if not all("一" <= c <= "鿿" for c in t)),
    f"词表 {len(rows)} 条、谐音写法 {len(forms)} 个：不大；写法不重复、不等于原词；原词不是中文的都有写法",
    len(rows),
)
bad_words = ("操", "屌", "逼", "婊", "傻逼")
check(
    not any(b in P.TABLE for b in bad_words),
    "不收脏话",
    [b for b in bad_words if b in P.TABLE],
)

print("\n[6 没装 pypinyin：只按字面写法认，不报错]")
real_find = importlib.util.find_spec
real = P._R["d"]
try:
    importlib.util.find_spec = lambda name, *a: (
        None if name == "pypinyin" else real_find(name, *a)
    )
    P._R["d"] = None
    P._T["key"] = None
    n_lit = P.note(cap("你可真是夺笋啊"))
    n_snd = P.note(cap("压历山大"))
    check(
        not P.ready()
        and P.how() == "没装 pypinyin"
        and n_lit == "谐音（按读音查的）：可能是 夺笋 = 多损"
        and n_snd == "",
        "没装：词表里的写法照样认（不写读音），按读音的不找",
        (n_lit, n_snd),
    )
finally:
    importlib.util.find_spec = real_find
    P._R["d"] = real
    P._T["key"] = None
check(P.ready() == sound, "装回来以后照常")

print("\n[7 自己做的小样本（不是 PunMemeCN：那个数据集要自己下，见 README）]")
# 按读音找的：都不是词表里的现成写法（换了别的同音 / 近音字），看「读音」那一步认不认得出来
held = [
    "压历山大",
    "鸭梨三大",
    "默默无纹",
    "一网情身",
    "其乐无穹",
    "机不可湿",
    "百依百瞬",
    "年年有渔",
    "万事如亿",
    "心想事呈",
    "马到成工",
    "前途无亮",
    "大吉大力",
    "喜气扬扬",
    "太难乐",
    "不知到",
    "我不理姐",
    "加油丫",
    "程序原",
    "工程狮",
    "设计狮",
    "绷不住乐",
    "怎么回四",
    "随心所玉",
]
# 不是谐音的：常见的梗图文字、群聊截图、通知、带数字的
plainset = (
    "我裂开了|我真的会谢|就这？|啊对对对|急了急了|笑死|我直接好家伙|有一说一|别卷了|躺平了|下班了家人们|"
    "你在教我做事？|给爷整笑了|这合理吗|一眼丁真|家人们谁懂啊|格局打开|是在下输了|大可不必|我太难了|"
    "压力山大|与你无关|今天星期几|周五了 放假|明天考试|作业写完了吗|吃了吗|在吗|晚安|早上好|生日快乐|"
    "新年快乐|恭喜发财|万事如意|好好学习 天天向上|一起加油|冲冲冲|期末周|别问 问就是不知道|不知道|"
    "你知道吗|真的假的|怎么回事|管理员同意了|全体禁言|欢迎新人|本群禁止发广告|系统提示：对方已撤回一条消息|"
    "余额不足|2024年5月20日|价格 520 元|第1314期|电话 13800138000|房间号 886|9:30 开会|身高 175cm|"
    "评分 9.4|666号选手|这个视频笑死我了|你说的都对|好的老板|收到|明白|辛苦了|谢谢老板|没关系|对不起|"
    "今天天气真好|风景如画|春暖花开|岁月静好|人间值得|未来可期|前途光明|心想事成|年年有余|大吉大利"
).split("|")
if sound:
    hit = [x for x in held if P.note(cap(x))]
    fp = [x for x in plainset if P.note(cap(x))]
    print(
        f"    按读音认出来：{len(hit)} / {len(held)}；不是谐音却给了候选：{len(fp)} / {len(plainset)}"
    )
    check(
        len(hit) >= 0.8 * len(held),
        f"按读音找：{len(hit)} / {len(held)} 认出来了",
        [x for x in held if x not in hit],
    )
    check(
        len(fp) <= 1,
        f"不是谐音的 {len(plainset)} 句：给了候选的 {len(fp)} 句",
        [(x, P.note(cap(x))) for x in fp],
    )

print("\n[8 只写事实（E1）]")
samples = [P.note(cap(x)) for x in ("我太南了", "栓Q", "520快乐", "鸡不可失")]
check(
    all(s.startswith("谐音（按读音查的）：") for s in samples if s)
    and not any(
        w in "".join(samples)
        for w in ("你可以", "接住", "回复", "建议", "别", "可以说")
    ),
    "只写「怎么读、可能是哪个词」，不写怎么接、不写示范句",
    samples,
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
