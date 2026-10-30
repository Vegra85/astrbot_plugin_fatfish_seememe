#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试：真的 AstrBot（4.29.3）+ 这个插件 + 假 QQ 端（假 NapCat 反向 WebSocket）+ 假模型（OpenAI 兼容；「看图」靠认颜色）
第一轮：对话模型只认字，看图用另一个模型，带自定义头像对照
  A 带图 @ 机器人：看一次（两张图：头像 + 这张）；回话请求里有看懂的结果和「图在说你」
  B 先发图、再 @：看他刚才发的那张（发图的时候不看）
  C 引用别人的图 @：看引用的那张
  D 同一张图换个群再发：不再看（记住了）
  E 「上面那张图什么意思」：看群里最近一张
  F 动图：拼帧看、结果是「动图：…」
  G QQ 商城表情（mface，AstrBot 自己会丢掉）、表情包（sub_type=1）
  H 记进聊天记录：下一轮的历史里有「看到了什么」、没有「这一轮怎么接」；紧接着再 @ 不重复给
  J 看图太慢（超过「回话前最多等」）：先回话不带，看完存着，下一轮直接用
  K 看图模型拒绝了（「抱歉 我无法查看」）：不当成看懂了、不记住
  I 面板接口：状态、试一张图（带一句话）、没登录读不到、面板页认得出
第二轮（重启）：没有能看图的模型 → 带图 @ 照样回、不报错、不乱加；记住的图重启以后还在
第三轮（重启）：对话模型自己能看图 + 群里的图都先看好 → 没人 @ 的图先看好、之后提起直接用；带图 @ 时对话模型也拿到图本身
第四轮（重启）：关掉「对话模型能看图时也另外看」→ 带图 @ 不另外看、只给通用提示；先发图再 @ 照样看
用法：python tests/e2e_astrbot.py [插件目录 默认上一层] [--keep] [--dump]（--dump：打印 A 里发给两个模型的原话）
  需要装了 astrbot（测过 4.29.3）的 Python，astrbot 命令在 PATH 里；另外要 aiohttp、pyjwt、websockets、Pillow（装 astrbot 时都会带上）
  只在临时文件夹里跑：模型是假的、QQ 端是假的，不连外网
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp
from aiohttp import web
import jwt
import websockets
from PIL import Image

PLUGIN_SRC = Path(
    sys.argv[1]
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
    else Path(__file__).resolve().parent.parent
).resolve()
KEEP = "--keep" in sys.argv
PNAME = PLUGIN_SRC.name
SELF, HUMAN, HUMAN2, HUMAN3, OTHER = 99999, 30001, 30002, 30003, 50005
G1, G2, G3 = 7001, 7002, 7003
HEAD = "[看图看懂]"
HINT = "# 这一轮怎么接"
RES = []


def check(cond, msg, why=""):
    RES.append((bool(cond), msg))
    print(
        ("  [过]   " if cond else "  [没过] ")
        + msg
        + ("" if cond or not why else f"\n           {str(why)[:900]}"),
        flush=True,
    )
    return bool(cond)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


LLM_PORT, WS_PORT, DASH_PORT = free_port(), free_port(), free_port()
ROOT = Path(tempfile.mkdtemp(prefix="astrbot-e2e-seememe-"))
IMG = ROOT / "img"
SECRET = "e2e-secret-0123456789abcdef0123456789"
vision_calls, chat_calls, img_hits = [], [], []

# ───────────── 测试用的图：纯色方块（假模型认颜色）─────────────
COLORS = {
    "red": (230, 20, 20),
    "blue": (20, 40, 230),
    "green": (20, 200, 40),
    "yellow": (240, 220, 20),
    "purple": (140, 30, 200),
    "orange": (250, 140, 0),
    "pink": (250, 120, 200),
    "teal": (0, 160, 160),
    "cyan": (0, 230, 250),
    "brown": (120, 70, 20),
    "gray": (128, 128, 128),
    "magenta": (255, 0, 255),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "navy": (0, 0, 110),
    "olive": (110, 110, 0),
}
CAPS = {
    "red": "字：「给token」。画面：一条胖鱼抱着一堆金币。梗：「给X什么都做」网络模板。跟你：图里的胖鱼就是大肥鱼的 Q 版。坑：没有，纯属玩梗",
    "blue": "画面：一车鲸鱼开过来。梗：网络模板「找一车X来弄你」。跟你：没关系。坑：问它要不要上车",
    "green": "字：「周一」。画面：一只猫瘫在桌上。梗：周一综合征",
    "yellow": "画面：一只柴犬歪头看镜头。梗：看不出出处",
    "magenta": "画面：一开始一条鱼在游然后跳起来最后落回水里。梗：看不出出处",
    "brown": "画面：一个狗头。梗：狗头保命",
    "gray": "字：「我裂开了」。画面：一个裂开的表情。梗：「我裂开了」表情包",
    "purple": "画面：紫色方块。梗：看不出出处",
    "orange": "画面：橙色方块",
    "pink": "字：「夺笋啊」。画面：一个人在挖笋。梗：「夺笋」谐音多损",
    "teal": "画面：一条蓝绿色的鱼在吐泡泡。梗：看不出出处",
    "navy": "画面：深夜加班的工位。梗：「福报」",
    "white": "抱歉，我无法查看这张图片。",
}
SLOW = {"navy": 6}  # 这张图假模型要看 6 秒（回话最多等 3 秒）


def make_images():
    IMG.mkdir(parents=True, exist_ok=True)
    for name, rgb in COLORS.items():
        Image.new("RGB", (96, 96), rgb).save(IMG / f"{name}.png")
    frames = [
        Image.new("RGB", (96, 96), COLORS[c]) for c in ("magenta", "black", "white")
    ]
    frames[0].save(
        IMG / "anim.gif", save_all=True, append_images=frames[1:], duration=120, loop=0
    )


def file_hash(name):
    return (
        hashlib.md5((IMG / name).read_bytes()).hexdigest().upper() + Path(name).suffix
    )


def color_of(url):
    try:
        raw = (
            base64.b64decode(url.split(",", 1)[1])
            if url.startswith("data:")
            else Path(url).read_bytes()
        )
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        px = im.getpixel((im.width // 6, im.height // 6))
    except Exception:
        return "?"
    return min(COLORS, key=lambda n: sum((a - b) ** 2 for a, b in zip(COLORS[n], px)))


def text_of(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            p.get("text", "")
            for p in c
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def images_in(m):
    c = m.get("content")
    return (
        [
            p["image_url"]["url"]
            for p in c
            if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        if isinstance(c, list)
        else []
    )


# ───────────── 假模型 + 假图床 ─────────────
async def chat_completions(request):
    body = await request.json()
    msgs = body.get("messages") or []
    last = msgs[-1] if msgs else {}
    prompt = text_of(last)
    if "让看不到图的机器人也能接话" in prompt:
        colors = [color_of(u) for u in images_in(last)]
        if colors and colors[-1] in SLOW:
            await asyncio.sleep(SLOW[colors[-1]])
        vision_calls.append(
            {
                "model": body.get("model"),
                "prompt": prompt,
                "colors": colors,
                "t": time.time(),
            }
        )
        text = CAPS.get(colors[-1] if colors else "", "画面：看不清")
    else:
        chat_calls.append(
            {"model": body.get("model"), "messages": msgs, "t": time.time()}
        )
        text = f"收到第{len(chat_calls)}句"
    return web.json_response(
        {
            "id": "x",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 300,
                "completion_tokens": 60,
                "total_tokens": 360,
            },
        }
    )


async def models(request):
    return web.json_response(
        {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}
    )


async def serve_img(request):
    name = request.match_info["name"]
    img_hits.append(name)
    p = IMG / name
    if not p.exists():
        return web.Response(status=404)
    ctype = "image/gif" if name.endswith(".gif") else "image/png"
    return web.Response(body=p.read_bytes(), content_type=ctype)


# ───────────── 假 QQ 端（NapCat 反向 WebSocket） ─────────────
class FakeQQ:
    def __init__(self):
        self.ws = None
        self.mid = 1000 + int(time.time()) % 1000 * 10
        self.sent = []
        self.store = {}
        self.names = {
            SELF: "大肥鱼",
            HUMAN: "老王",
            HUMAN2: "小李",
            HUMAN3: "阿珍",
            OTHER: "路人",
        }
        self.new_sent = asyncio.Event()

    async def connect(self):
        for _ in range(120):
            try:
                self.ws = await websockets.connect(
                    f"ws://127.0.0.1:{WS_PORT}/ws/",
                    additional_headers={
                        "X-Self-ID": str(SELF),
                        "X-Client-Role": "Universal",
                    },
                )
                break
            except Exception:
                await asyncio.sleep(0.5)
        if not self.ws:
            raise RuntimeError("连不上 AstrBot 的反向 WebSocket")
        asyncio.create_task(self.loop())
        await self.ws.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "self_id": SELF,
                    "time": int(time.time()),
                }
            )
        )

    async def loop(self):
        try:
            async for raw in self.ws:
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if "action" not in d:
                    continue
                act, p, echo = d["action"], d.get("params") or {}, d.get("echo")
                data = {}
                if act in ("send_group_msg", "send_msg"):
                    self.mid += 1
                    msg = p.get("message")
                    text = (
                        "".join(
                            (s.get("data") or {}).get("text", "")
                            for s in msg
                            if isinstance(s, dict) and s.get("type") == "text"
                        )
                        if isinstance(msg, list)
                        else str(msg)
                    )
                    self.sent.append((int(p.get("group_id") or 0), text, time.time()))
                    data = {"message_id": self.mid}
                    self.new_sent.set()
                elif act == "get_group_member_info":
                    uid = int(p.get("user_id") or 0)
                    data = {
                        "user_id": uid,
                        "nickname": self.names.get(uid, str(uid)),
                        "card": self.names.get(uid, ""),
                        "role": "member",
                    }
                elif act == "get_stranger_info":
                    uid = int(p.get("user_id") or 0)
                    data = {"user_id": uid, "nickname": self.names.get(uid, str(uid))}
                elif act == "get_msg":
                    data = self.store.get(int(p.get("message_id") or 0)) or {}
                elif act == "get_login_info":
                    data = {"user_id": SELF, "nickname": "大肥鱼"}
                await self.ws.send(
                    json.dumps(
                        {"status": "ok", "retcode": 0, "data": data, "echo": echo}
                    )
                )
        except Exception:
            pass

    async def say(
        self,
        gid,
        uid,
        text="",
        at_bot=False,
        quote=None,
        image=None,
        sticker=False,
        mface=None,
    ):
        self.mid += 1
        segs = []
        if quote:
            segs.append({"type": "reply", "data": {"id": str(quote)}})
        if at_bot:
            segs.append({"type": "at", "data": {"qq": str(SELF)}})
        if text:
            segs.append(
                {"type": "text", "data": {"text": (" " if at_bot else "") + text}}
            )
        if image:
            segs.append(
                {
                    "type": "image",
                    "data": {
                        "file": file_hash(image),
                        "url": f"http://127.0.0.1:{LLM_PORT}/img/{image}",
                        "summary": "[动画表情]" if sticker else "",
                        "sub_type": 1 if sticker else 0,
                        "file_size": str((IMG / image).stat().st_size),
                    },
                }
            )
        if mface:
            segs.append(
                {
                    "type": "mface",
                    "data": {
                        "emoji_id": "a3c5e8b0d2f4a6c8e0b2d4f6a8c0e2b4",
                        "emoji_package_id": 230012,
                        "key": "k123",
                        "summary": f"[{mface[1]}]",
                        "url": f"http://127.0.0.1:{LLM_PORT}/img/{mface[0]}",
                    },
                }
            )
        ev = {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": self.mid,
            "group_id": gid,
            "user_id": uid,
            "self_id": SELF,
            "time": int(time.time()),
            "message": segs,
            "raw_message": text,
            "font": 0,
            "sender": {
                "user_id": uid,
                "nickname": self.names.get(uid, str(uid)),
                "card": self.names.get(uid, ""),
                "role": "member",
            },
        }
        self.store[self.mid] = dict(ev)
        await self.ws.send(json.dumps(ev, ensure_ascii=False))
        return self.mid

    async def wait_reply(self, gid, since, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            got = [s for s in self.sent if s[0] == gid and s[2] >= since]
            if got:
                return got
            self.new_sent.clear()
            try:
                await asyncio.wait_for(
                    self.new_sent.wait(), max(0.05, end - time.time())
                )
            except asyncio.TimeoutError:
                pass
        return []


def setup_root():
    env = dict(os.environ, ASTRBOT_ROOT=str(ROOT))
    subprocess.run(
        ["astrbot", "init", "-y"], cwd=ROOT, env=env, check=True, capture_output=True
    )
    shutil.copytree(
        PLUGIN_SRC,
        ROOT / "data" / "plugins" / PNAME,
        ignore=shutil.ignore_patterns("__pycache__", "dev", "*.pyc", ".git"),
    )
    code = (
        "from astrbot.core.config.astrbot_config import AstrBotConfig;"
        f"AstrBotConfig(config_path=r'{ROOT / 'data' / 'cmd_config.json'}')"
    )
    subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True
    )
    set_main(["text"])
    p = ROOT / "data" / "cmd_config.json"
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    cfg["platform"] = [
        {
            "id": "napcat",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": WS_PORT,
            "ws_reverse_token": "",
        }
    ]
    cfg["platform_settings"]["rate_limit"]["count"] = 1000
    cfg.setdefault("dashboard", {})["port"] = DASH_PORT
    cfg["dashboard"]["host"] = "127.0.0.1"
    cfg["dashboard"]["jwt_secret"] = SECRET
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def set_main(modalities):
    p = ROOT / "data" / "cmd_config.json"
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    base = {
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "enable": True,
        "key": ["sk-fake"],
        "api_base": f"http://127.0.0.1:{LLM_PORT}/v1",
        "timeout": 30,
    }
    cfg["provider"] = [
        {"id": "fake", **base, "model": "fake-model", "modalities": modalities},
        {
            "id": "fakevision",
            **base,
            "model": "fake-vision",
            "modalities": ["text", "image"],
        },
    ]
    cfg.setdefault("provider_settings", {})["default_provider_id"] = "fake"
    cfg["provider_settings"]["default_image_caption_provider_id"] = ""
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def set_plugin(**kw):
    p = ROOT / "data" / "config" / f"{PNAME}_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
    cfg.update(kw)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def log_text():
    return (ROOT / "astrbot_stdout.log").read_text(encoding="utf-8", errors="replace")


async def turn(qq, gid, uid, text="", wait=True, **kw):
    """说一句 → 等机器人回 → 返回（回了没有, 这一轮新增的看图调用, 这一轮的回话请求）"""
    nv, nc, t0 = len(vision_calls), len(chat_calls), time.time()
    await qq.say(gid, uid, text, **kw)
    got = await qq.wait_reply(gid, t0, timeout=10 if wait else 2.5)
    if not wait:
        await asyncio.sleep(0.5)
    chat = chat_calls[nc] if len(chat_calls) > nc else None
    return bool(got), vision_calls[nv:], chat


def last_user(chat):
    return chat["messages"][-1] if chat else {}


def history_text(chat):
    return "\n".join(text_of(m) for m in (chat["messages"][:-1] if chat else []))


async def main():
    make_images()
    setup_root()
    set_plugin(
        vision_provider_id="fakevision",
        avatar_mode="自定义图片",
        avatar_url=f"http://127.0.0.1:{LLM_PORT}/img/cyan.png",
        bot_look="蓝头发、戴鱼形发卡的女孩",
        wait_sec=3,
    )
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/img/{name}", serve_img)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", LLM_PORT).start()

    env = dict(
        os.environ,
        ASTRBOT_ROOT=str(ROOT),
        DASHBOARD_PORT=str(DASH_PORT),
        PYTHONUNBUFFERED="1",
    )
    log = open(ROOT / "astrbot_stdout.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["astrbot", "run"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    token = jwt.encode(
        {"username": "astrbot", "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )
    hdr = {"Authorization": f"Bearer {token}"}
    api = f"http://127.0.0.1:{DASH_PORT}/api/v1/plugins/extensions/{PNAME}"
    try:
        qq = FakeQQ()
        await qq.connect()
        await asyncio.sleep(4)
        text = log_text()
        check(
            PNAME in text and "Traceback" not in text,
            "AstrBot 起来了、插件加载了、没有报错",
            text[-1500:],
        )

        # ═════════ A 带图 @ ═════════
        print("\n[A 带图 @ 机器人]")
        hits = len(img_hits)
        ok, vc, chat = await turn(
            qq, G1, HUMAN, "这不就是你吗", at_bot=True, image="red.png"
        )
        check(ok, "回了")
        check(
            img_hits[hits:].count("red.png") == 1,
            "图只下载了一次（插件用的是 AstrBot 收消息时下好的那份）",
            img_hits[hits:],
        )
        check(
            len(vc) == 1
            and vc[0]["colors"] == ["cyan", "red"]
            and vc[0]["model"] == "fake-vision",
            "用配置的看图模型看了一次：两张图（第一张头像、第二张这张）",
            vc,
        )
        vp = vc[0]["prompt"] if vc else ""
        check(
            "这是 QQ 群里「老王」发的一张图" in vp
            and "大肥鱼" in vp
            and "蓝头发" in vp
            and "第一张是机器人自己的头像" in vp
            and "发图的时候还说了：「这不就是你吗」" in vp,
            "给看图模型的话：谁发的、机器人的名字长相、头像对照、发图时说的话",
            vp[:700],
        )
        lu = text_of(last_user(chat))
        check(
            HEAD in lu and "「老王」发的图：字：「给token」" in lu,
            "回话请求里有看懂的结果",
            lu[-900:],
        )
        check(
            HINT in lu
            and "图在说你（图里的胖鱼就是大肥鱼的 Q 版）" in lu
            and "梗点：「给X什么都做」网络模板" in lu
            and "有坑" not in lu,
            "「这一轮怎么接」：梗点、图在说你（「坑：没有，纯属玩梗」不算坑）",
            lu[-900:],
        )
        check(
            "原图没直接发给你" in lu and "not included in this request" in lu,
            "对话模型看不了图：AstrBot 注了「原图没给它」，提示里说清楚这不碍事",
            lu[-600:],
        )
        check(
            chat and chat["model"] == "fake-model",
            "回话还是用对话模型",
            chat and chat["model"],
        )
        if "--dump" in sys.argv:
            print(
                "\n----- 发给看图模型的话 -----\n"
                + vp
                + "\n----- 回话请求里最后一条（用户这条）-----\n"
                + json.dumps(last_user(chat), ensure_ascii=False, indent=1)[:3000]
                + "\n-----"
            )

        # ═════════ B 先发图 再 @ ═════════
        print("\n[B 先发图、再 @]")
        ok, vc, chat = await turn(qq, G1, HUMAN2, image="blue.png", wait=False)
        check(
            not ok and not vc,
            "光发图（没 @）：不回、也不看（没开「群里的图都先看好」）",
            vc,
        )
        ok, vc, chat = await turn(qq, G1, HUMAN2, "你看我刚发的", at_bot=True)
        check(
            ok and len(vc) == 1 and vc[0]["colors"][-1] == "blue",
            "再 @：看他刚才发的那张",
            vc,
        )
        check(
            vc and "后来有人找机器人说：「你看我刚发的」" in vc[0]["prompt"],
            "看图模型知道后来 @ 机器人说了什么",
            vc and vc[0]["prompt"][:600],
        )
        lu = text_of(last_user(chat))
        check(
            HEAD in lu
            and "「小李」发的图：画面：一车鲸鱼" in lu
            and "图里有坑（问它要不要上车）" in lu
            and "图在说你" not in lu,
            "回话请求里有那张图、有坑（「跟你：没关系」不算在说它）",
            lu[-900:],
        )

        print("\n[H 记进聊天记录]")
        hist = history_text(chat)
        check(
            "「老王」发的图：字：「给token」" in hist,
            "上一轮看懂的结果进了聊天记录（这一轮的历史里有）",
            hist[-1200:],
        )
        check(HINT not in hist, "「这一轮怎么接」没进聊天记录", hist[-1200:])
        ok, vc, chat = await turn(qq, G1, HUMAN2, "然后呢", at_bot=True)
        lu = text_of(last_user(chat))
        check(
            ok and not vc and HEAD not in lu and "一车鲸鱼" in history_text(chat),
            "紧接着再 @（没带图）：刚才那张不再重复给（聊天记录里已经有了）",
            (vc, lu[-400:]),
        )

        # ═════════ C 引用别人的图 ═════════
        print("\n[C 引用别人的图]")
        await turn(qq, G1, OTHER, image="green.png", wait=False)
        mid = qq.mid
        ok, vc, chat = await turn(qq, G1, HUMAN, "这啥意思", at_bot=True, quote=mid)
        check(
            ok and len(vc) == 1 and vc[0]["colors"][-1] == "green", "看了引用的那张", vc
        )
        lu = text_of(last_user(chat))
        check(
            "引用的消息里「路人」发的图：字：「周一」" in lu,
            "回话请求里标明是引用的消息里的图",
            lu[-700:],
        )

        # ═════════ D 同一张图换个群再发 ═════════
        print("\n[D 同一张图换个群再发]")
        ok, vc, chat = await turn(
            qq, G2, HUMAN3, "哈哈哈", at_bot=True, image="red.png"
        )
        lu = text_of(last_user(chat))
        check(
            ok and not vc and "「阿珍」发的图：字：「给token」" in lu,
            "没再看（同一张图记住了），回话请求里照样有",
            (vc, lu[-500:]),
        )

        # ═════════ E 上面那张图 ═════════
        print("\n[E 说「上面那张图」]")
        await turn(qq, G3, OTHER, image="yellow.png", wait=False)
        ok, vc, chat = await turn(qq, G3, HUMAN, "上面那张图什么意思", at_bot=True)
        lu = text_of(last_user(chat))
        check(
            ok
            and len(vc) == 1
            and vc[0]["colors"][-1] == "yellow"
            and "「路人」发的图：画面：一只柴犬" in lu,
            "看了群里最近一张（别人发的）",
            (vc, lu[-500:]),
        )
        ok, vc, chat = await turn(qq, G3, HUMAN2, "今天吃啥", at_bot=True)
        check(
            ok and not vc and HEAD not in text_of(last_user(chat)),
            "没说到图、没带图：不看、不加",
            vc,
        )

        # ═════════ F 动图 ═════════
        print("\n[F 动图]")
        ok, vc, chat = await turn(
            qq, G3, HUMAN3, "看我做的", at_bot=True, image="anim.gif"
        )
        lu = text_of(last_user(chat))
        check(
            ok and len(vc) == 1 and "当成一个动作过程" in vc[0]["prompt"],
            "动图：AstrBot 拼好帧、提示词里说「当成一个动作过程」",
            vc and vc[0]["prompt"][-300:],
        )
        check(
            "发的图：动图：画面：一开始一条鱼在游" in lu,
            "结果标成「动图：…」",
            lu[-500:],
        )

        # ═════════ G 商城表情 / 表情包 ═════════
        print("\n[G QQ 商城表情、表情包]")
        ok, vc, chat = await turn(
            qq, G2, HUMAN, "送你", at_bot=True, mface=("brown.png", "狗头")
        )
        check(
            ok
            and len(vc) == 1
            and vc[0]["colors"][-1] == "brown"
            and "发的一张QQ 表情「狗头」" in vc[0]["prompt"],
            "商城表情（AstrBot 自己会丢掉）也看了、知道叫「狗头」",
            vc and vc[0]["prompt"][:300],
        )
        check(
            "狗头保命" in text_of(last_user(chat)),
            "回话请求里有",
            text_of(last_user(chat))[-300:],
        )
        ok, vc, chat = await turn(
            qq, G2, HUMAN2, image="gray.png", sticker=True, at_bot=True
        )
        check(
            ok and len(vc) == 1 and "发的一张表情包" in vc[0]["prompt"],
            "sub_type=1 的图：告诉看图模型这是表情包",
            vc and vc[0]["prompt"][:200],
        )

        # ═════════ J 看得太慢 ═════════
        print("\n[J 看图太慢（超过「回话前最多等」）]")
        t0 = time.time()
        ok, vc, chat = await turn(
            qq, G3, HUMAN2, "加班中", at_bot=True, image="navy.png"
        )
        check(
            ok and time.time() - t0 < 6 and HEAD not in text_of(last_user(chat)),
            f"等了 3 秒就先回了（{time.time() - t0:.1f} 秒），这一轮不带",
            text_of(last_user(chat))[-300:],
        )
        await asyncio.sleep(4.5)
        check(
            [v for v in vision_calls if v["colors"][-1:] == ["navy"]],
            "没看完的接着看完了",
        )
        ok, vc, chat = await turn(qq, G3, HUMAN2, "你倒是说说", at_bot=True)
        check(
            ok and not vc and "深夜加班的工位" in text_of(last_user(chat)),
            "下一轮直接用看完的（不再花钱看）",
            text_of(last_user(chat))[-300:],
        )

        # ═════════ K 看图模型拒绝了 ═════════
        print("\n[K 看图模型拒绝了]")
        ok, vc, chat = await turn(
            qq, G3, HUMAN3, "这是啥", at_bot=True, image="white.png"
        )
        check(
            ok
            and len(vc) == 1
            and HEAD not in text_of(last_user(chat))
            and HINT not in text_of(last_user(chat)),
            "看图模型说「抱歉 我无法查看」：不当成看懂了、不给提示",
            text_of(last_user(chat))[-300:],
        )
        ok, vc, chat = await turn(
            qq, G3, HUMAN3, "再看看", at_bot=True, image="white.png"
        )
        check(ok and len(vc) == 1, "也没记住（下次还会再试）", vc)

        # ═════════ I 面板接口 ═════════
        print("\n[I 面板接口]")
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{api}/state", headers=hdr) as r:
                d = await r.json(content_type=None)
            st = (d or {}).get("data") or {}
            today = st.get("today") or {}
            check(
                r.status == 200
                and today.get("n") == len(vision_calls)
                and today.get("cached", 0) >= 1
                and today.get("turns", 0) >= 7,
                f"状态：今天看了 {today.get('n')} 张（假模型收到 {len(vision_calls)} 次）、直接用记住的 {today.get('cached')}、"
                f"带进回话 {today.get('turns')} 轮",
                str(d)[:500],
            )
            check(
                st.get("vision") == "fakevision"
                and st.get("main_sees") is False
                and "大肥鱼" in (st.get("names") or []),
                "状态：看图模型、对话模型看不了图、认自己用的名字",
                {k: st.get(k) for k in ("vision", "main_sees", "names")},
            )
            check(
                any(
                    "给token" in (x.get("cap") or "") and x.get("where") == f"群 {G1}"
                    for x in st.get("recent") or []
                ),
                "状态：最近看懂的图（在哪个群、看到什么）",
                (st.get("recent") or [])[:2],
            )
            check(
                any(
                    "看图模型没看" in (e.get("text") or "")
                    for e in st.get("errors") or []
                )
                and today.get("errors", 0) >= 2,
                "状态：出错记录里有「看图模型没看」",
                st.get("errors"),
            )
            async with s.post(
                f"{api}/prep", headers=hdr, json={"said": "测试一句"}
            ) as r:
                d = await r.json(content_type=None)
            check(
                r.status == 200 and (d or {}).get("status") == "ok", "试图前先说一句", d
            )
            nv = len(vision_calls)
            form = aiohttp.FormData()
            form.add_field(
                "file",
                (IMG / "purple.png").read_bytes(),
                filename="purple.png",
                content_type="image/png",
            )
            async with s.post(f"{api}/test", headers=hdr, data=form) as r:
                d = await r.json(content_type=None)
            dd = (d or {}).get("data") or {}
            check(
                r.status == 200
                and dd.get("caption") == CAPS["purple"]
                and len(vision_calls) == nv + 1,
                "试一张图：真的看了、结果对",
                str(d)[:500],
            )
            check(
                HEAD in (dd.get("block") or "")
                and HINT in (dd.get("block") or "")
                and "发图的时候还说了：「测试一句」" in (dd.get("prompt") or "")
                and dd.get("avatar") is True
                and dd.get("model") == "fakevision",
                "试一张图：机器人会看到的、发给看图模型的话、带了头像、用的哪个模型",
                {k: dd.get(k) for k in ("block", "prompt", "model", "avatar")},
            )
            form = aiohttp.FormData()
            form.add_field(
                "file",
                (IMG / "purple.png").read_bytes(),
                filename="purple.png",
                content_type="image/png",
            )
            async with s.post(f"{api}/test", headers=hdr, data=form) as r:
                d = await r.json(content_type=None)
            check(
                (d or {}).get("status") == "ok" and len(vision_calls) == nv + 2,
                "同一张图再试：不走缓存（改了配置能马上看到效果）",
                len(vision_calls) - nv,
            )
            form = aiohttp.FormData()
            form.add_field("other", "x")
            async with s.post(f"{api}/test", headers=hdr, data=form) as r:
                d = await r.json(content_type=None)
            check(
                (d or {}).get("status") == "error"
                and "没收到图" in (d or {}).get("message", ""),
                "没传图：说没收到",
                d,
            )
            async with s.get(f"{api}/state") as r:
                check(r.status in (401, 403), f"没登录读不到（{r.status}）")
            async with s.get(
                f"http://127.0.0.1:{DASH_PORT}/api/v1/plugins/{PNAME}/views/panel",
                headers=hdr,
            ) as r:
                d = await r.json(content_type=None)
            url = None

            def _find(o):
                nonlocal url
                if isinstance(o, dict):
                    for v in o.values():
                        if isinstance(v, str) and "/_t/" in v and url is None:
                            url = v
                        _find(v)
                elif isinstance(o, list):
                    for v in o:
                        _find(v)

            _find(d)
            check(
                r.status == 200 and url,
                "面板页 panel 认出来了",
                json.dumps(d, ensure_ascii=False)[:400],
            )
            if url:
                full = (
                    url
                    if url.startswith("http")
                    else f"http://127.0.0.1:{DASH_PORT}{url}"
                )
                async with s.get(full, headers=hdr) as r2:
                    html = await r2.text()
                check(
                    r2.status == 200
                    and "看图看懂梗" in html
                    and "bridge-sdk.js" in html,
                    "面板页 HTML 拿得到、AstrBot 插上了桥接脚本",
                    (r2.status, html[:200]),
                )
        text = log_text()
        check(
            "Traceback" not in text,
            "第一轮 AstrBot 日志里没有 Traceback",
            text[text.find("Traceback") - 300 :][:1500],
        )

        # ═════════ 第二轮：没有能看图的模型 ═════════
        print("\n[第二轮 重启：没有能看图的模型]")
        proc.terminate()
        proc.wait(15)
        await qq.ws.close()
        state = json.loads(
            (ROOT / "data" / "plugin_data" / PNAME / "state.json").read_text(
                encoding="utf-8"
            )
        )
        check(
            len(state.get("cache") or {}) >= 8
            and (state.get("stats") or {}).get("n", 0) >= 9,
            "关掉的时候存了档（记住的图、今天的数）",
            (len(state.get("cache") or {}), state.get("stats")),
        )
        set_plugin(vision_provider_id="")
        proc = subprocess.Popen(
            ["astrbot", "run"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        qq = FakeQQ()
        await qq.connect()
        await asyncio.sleep(4)
        ok, vc, chat = await turn(
            qq, G1, HUMAN, "这个呢", at_bot=True, image="orange.png"
        )
        lu = text_of(last_user(chat))
        check(
            ok and not vc and HEAD not in lu and HINT not in lu,
            "带图 @：照样回，不乱加（没看成就不给提示，免得它以为自己看到了）",
            (vc, lu[-300:]),
        )
        check(
            "没有能看图的模型" in log_text(),
            "日志里说清楚了：去插件配置里选「看图用哪个模型」",
        )
        ok, vc, chat = await turn(
            qq, G2, HUMAN, "再看看这个", at_bot=True, image="red.png"
        )
        check(
            ok and not vc and "字：「给token」" in text_of(last_user(chat)),
            "记住的图重启以后还在（看过的照样用）",
            text_of(last_user(chat))[-300:],
        )

        # ═════════ 第三轮：对话模型自己能看图 + 群里的图都先看好 ═════════
        print("\n[第三轮 重启：对话模型自己能看图 + 群里的图都先看好]")
        proc.terminate()
        proc.wait(15)
        await qq.ws.close()
        set_main(["text", "image"])
        set_plugin(vision_provider_id="", prefetch=True)
        proc = subprocess.Popen(
            ["astrbot", "run"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        qq = FakeQQ()
        await qq.connect()
        await asyncio.sleep(4)
        ok, vc, chat = await turn(qq, G2, OTHER, image="pink.png", wait=False)
        await asyncio.sleep(1)
        vc = [v for v in vision_calls if v["colors"] and v["colors"][-1] == "pink"]
        check(
            not ok and len(vc) == 1 and vc[0]["model"] == "fake-model",
            "没人 @ 的图也先看好了（用对话模型看，它能看图）",
            vc,
        )
        ok, vc, chat = await turn(qq, G2, HUMAN3, "楼上那张图啥意思", at_bot=True)
        lu = text_of(last_user(chat))
        check(
            ok and not vc and "「路人」发的图：字：「夺笋啊」" in lu,
            "之后有人提起：直接用先看好的，不用等",
            (vc, lu[-400:]),
        )
        ok, vc, chat = await turn(qq, G2, HUMAN, "你看", at_bot=True, image="teal.png")
        lu = last_user(chat)
        check(
            ok and len(vc) == 1 and HEAD in text_of(lu) and "蓝绿色的鱼" in text_of(lu),
            "带图 @：插件照样专门看一遍（默认开着）",
            vc,
        )
        check(
            any(color_of(u) == "teal" for u in images_in(lu)),
            "对话模型也拿到了图本身（AstrBot 直接给的）",
            len(images_in(lu)),
        )
        check(
            "原图没直接发给你" not in text_of(lu),
            "对话模型能看图：不说「原图没给你」那句",
            text_of(lu)[-400:],
        )

        # ═════════ 第四轮：对话模型能看图时不另外看 ═════════
        print("\n[第四轮 重启：关掉「对话模型自己能看图时 也另外看一遍」]")
        proc.terminate()
        proc.wait(15)
        await qq.ws.close()
        set_plugin(also_when_main_sees=False, prefetch=False)
        proc = subprocess.Popen(
            ["astrbot", "run"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        qq = FakeQQ()
        await qq.connect()
        await asyncio.sleep(4)
        ok, vc, chat = await turn(
            qq, G1, HUMAN, "看这个", at_bot=True, image="olive.png"
        )
        lu = last_user(chat)
        check(
            ok and not vc and any(color_of(u) == "olive" for u in images_in(lu)),
            "带图 @：插件不另外看（省一次），对话模型自己看图",
            vc,
        )
        check(
            HINT in text_of(lu)
            and HEAD not in text_of(lu)
            and "- 梗点：" not in text_of(lu),
            "只附上通用的「这一轮怎么接」",
            text_of(lu)[-500:],
        )
        await turn(qq, G1, HUMAN3, image="black.png", wait=False)
        ok, vc, chat = await turn(qq, G1, HUMAN3, "我刚发的那张呢", at_bot=True)
        check(
            ok and len(vc) == 1 and vc[0]["colors"][-1] == "black",
            "先发图再 @：AstrBot 不会把那张给对话模型，插件照样看",
            vc,
        )
        text = log_text()
        check(
            "Traceback" not in text,
            "整个过程 AstrBot 日志里没有 Traceback",
            text[text.find("Traceback") - 300 :][:1500],
        )
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        await runner.cleanup()
        log.close()

    ok = sum(1 for c, _ in RES if c)
    bad = [m for c, m in RES if not c]
    print("\n" + "=" * 60)
    print(f"  结果：{ok} 项过了" + (f"，{len(bad)} 项没过" if bad else "，全过"))
    for m in bad:
        print("    -", m)
    print("=" * 60)
    if KEEP or bad:
        print("AstrBot 目录留着：", ROOT)
    else:
        shutil.rmtree(ROOT, ignore_errors=True)
    sys.exit(1 if bad else 0)


asyncio.run(main())
