# -*- coding: utf-8 -*-
"""
大肥鱼 · 看图看懂梗（AstrBot 插件）

机器人要回话的那一轮，把相关的图（这条消息里的、引用的消息里的、同一个人刚才发的、他说「上面那张图」时群里最近的一张）
交给能看图的模型，用「字 / 画面 / 梗 / 跟你 / 坑」五样写清楚：图里有没有机器人自己、在玩什么梗、有没有坑；
再附上「这一轮怎么接」：别复述画面、对梗点做反应、认出图在说自己、看穿坑反挖回去。
看到了什么会记进聊天记录（以后聊起这张图还记得）；「这一轮怎么接」只给这一轮看。
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
import astrbot.api.message_components as Comp

from .seer import (
    TEXT_REFERS,
    Cache,
    block,
    build_prompt,
    caption_block,
    hints_block,
    key_ok,
    refused,
    trim,
)

try:
    from astrbot.core.agent.message import TextPart
except Exception:  # 老版本 / 以后挪了地方：退回 dict
    TextPart = None
try:
    from astrbot.core.utils.media_utils import prepare_model_image
except Exception:
    prepare_model_image = None

PLUGIN_NAME = "astrbot_plugin_fatfish_seememe"
TAG = "[看图]"
_MAGIC = (
    (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"RIFF", ".webp"),
    (b"BM", ".bmp"),
)


def _num(v, d):
    try:
        return type(d)(v)
    except (TypeError, ValueError):
        return d


def _ext(data):
    for magic, ext in _MAGIC:
        if data[: len(magic)] == magic:
            return ext
    return ".jpg"


def _part(text, temp):
    if TextPart is not None:
        p = TextPart(text=text)
        return p.mark_as_temp() if temp else p
    return {"type": "text", "text": text, **({"_no_save": True} if temp else {})}


class FatfishSeeMeme(Star):
    """看图看懂梗：回话前帮机器人看懂图里的梗、认出图在说它、看出有没有坑"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        try:
            self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.data_dir = Path(os.getcwd()) / "data" / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.data_dir / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        for old in self.tmp_dir.glob("*"):  # 上次没删掉的临时图
            try:
                old.unlink()
            except Exception:
                pass
        self.state_file = self.data_dir / "state.json"
        st = self._load()
        self.cache = Cache(self._c("cache_keep", 600))
        self.cache.load(st.get("cache"))
        self.stats = st.get("stats") or {}
        self.recent_caps = deque(st.get("recent") or [], maxlen=60)
        self.errors = deque(st.get("errors") or [], maxlen=20)
        self.chat = defaultdict(
            lambda: deque(maxlen=16)
        )  # 会话 -> [(时间, 名字, 文字)]
        self.imgs = defaultdict(
            lambda: deque(maxlen=30)
        )  # 会话 -> [(时间, QQ, 名字, 图)]
        self.given = defaultdict(dict)  # 会话 -> {图: 记进聊天记录的时间}
        self.hour = deque(maxlen=1000)
        self.inflight = {}
        self.bot_names = {}  # self_id -> 昵称（没在配置里填名字时用）
        self.cards = {}  # (self_id, 群) -> 机器人在这个群的群名片
        self.avatar = {}  # self_id -> (本地路径, 拿到的时间)
        self.last_sid = ""
        self.test_ctx = {}
        self._warned = {}
        self._dirty = False
        self._saved_at = 0.0
        self._tasks = set()
        self._saver = None
        self._register_web()

    # ───────── 配置 / 存档 ─────────
    def _c(self, key, default):
        return _num(self.config.get(key, default), default)

    def _load(self):
        try:
            d = json.loads(self.state_file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self, force=False):
        """AstrBot 被直接关掉（关窗口、结束进程）时不会调 terminate()，所以有变化就尽快存：最多 3 秒存一次"""
        self._dirty = True
        if not force and time.time() - self._saved_at < 3:
            return
        self._saved_at = time.time()
        try:
            d = {
                "cache": self.cache.dump(),
                "stats": self.stats,
                "recent": list(self.recent_caps),
                "errors": list(self.errors),
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_file)
            self._dirty = False
        except Exception as e:
            logger.warning(f"{TAG} 存档失败：{e!r}")

    async def _save_loop(self):
        while True:
            await asyncio.sleep(5)
            if self._dirty:
                self._save(force=True)

    def _ensure_saver(self):
        if self._saver is None or self._saver.done():
            self._saver = asyncio.create_task(self._save_loop())

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        self._ensure_saver()
        return t

    async def terminate(self):
        for t in list(self._tasks):
            t.cancel()
        if self._saver:
            self._saver.cancel()
        self._save(force=True)

    def _today(self):
        day = time.strftime("%Y-%m-%d")
        if self.stats.get("day") != day:
            self.stats = {
                "day": day,
                "n": 0,
                "tokens": 0,
                "errors": 0,
                "cached": 0,
                "turns": 0,
            }
        self.stats.setdefault("turns", 0)
        return self.stats

    def _err(self, text):
        self._today()["errors"] += 1
        self.errors.append({"t": int(time.time()), "text": str(text)[:200]})
        self._save()

    def _warn_once(self, key, text, every=600):
        now = time.time()
        if now - self._warned.get(key, 0) > every:
            self._warned[key] = now
            logger.warning(f"{TAG} {text}")

    # ───────── 认图 ─────────
    @staticmethod
    def _key(ref, file=""):
        if key_ok(file):
            return "f:" + str(file)[:100]
        return "u:" + hashlib.sha1(str(ref).encode("utf-8", "ignore")).hexdigest()[:20]

    @staticmethod
    def _raw_segs(event):
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        try:
            segs = raw.get("message") if hasattr(raw, "get") else None
        except Exception:
            segs = None
        return (
            [s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []
        )

    def _from_comps(self, comps, sender, uid, t, quoted=False, raws=None, said=""):
        """raws：和这些图一一对应的原始消息段（QQ 的才有）—— 里面有原来的文件名（图片内容的哈希）、网址、是不是表情包"""
        imgs = [c for c in comps or [] if isinstance(c, Comp.Image)]
        raws = raws if raws is not None and len(raws) == len(imgs) else [{}] * len(imgs)
        out = []
        for c, d in zip(imgs, raws):
            ref = c.url or c.file or c.path
            if not ref:
                continue
            alt = str(d.get("url") or "")
            alt = (
                alt if alt.startswith("http") and alt != ref else ""
            )  # 本地那份没了还能再下
            kind = (
                "表情包"
                if str(d.get("sub_type")) == "1"
                or "表情" in str(d.get("summary") or "")
                else ""
            )
            out.append(
                {
                    "ref": ref,
                    "alt": alt,
                    "key": self._key(alt or ref, d.get("file") or c.file),
                    "sender": sender,
                    "uid": str(uid),
                    "t": t,
                    "quoted": quoted,
                    "kind": kind,
                    "said": said,
                }
            )
        return out

    def _images(self, event, t=None):
        """这条消息自己带的图（含 QQ 商城表情 mface —— AstrBot 自己会丢掉它）"""
        t = t or time.time()
        sender, uid = (
            event.get_sender_name() or str(event.get_sender_id()),
            str(event.get_sender_id()),
        )
        segs = self._raw_segs(event)
        raws = [
            s.get("data") or {} for s in segs if s.get("type") == "image"
        ]  # NapCat：sub_type=1 / 「[动画表情]」是表情包
        said = self._text(event)
        out = self._from_comps(
            event.get_messages(), sender, uid, t, raws=raws if segs else None, said=said
        )
        for s in segs:
            d = s.get("data") or {}
            if s.get("type") == "mface" and str(d.get("url") or "").startswith("http"):
                name = str(d.get("summary") or "").strip("[]")[:20]
                out.append(
                    {
                        "ref": d["url"],
                        "key": self._key(d["url"], d.get("emoji_id") or ""),
                        "sender": sender,
                        "uid": uid,
                        "t": t,
                        "quoted": False,
                        "kind": f"QQ 表情「{name}」" if name else "QQ 表情",
                        "said": said,
                    }
                )
        return out

    def _quoted(self, event):
        out = []
        for c in event.get_messages() or []:
            if isinstance(c, Comp.Reply) and c.chain:
                said = "".join(
                    x.text for x in c.chain if isinstance(x, Comp.Plain)
                ).strip()
                out += self._from_comps(
                    c.chain,
                    c.sender_nickname or str(c.sender_id or ""),
                    c.sender_id or "",
                    time.time(),
                    quoted=True,
                    said=said,
                )
        return out

    def _relevant(self, event):
        """这一轮要看的图 → [(图, 哪来的)]：这条消息的 / 引用的 → 都没有就看他刚才发的（图和 @ 常常分开发）
        → 还没有、他又说到「图」了 → 群里最近一张"""
        umo, now, uid = (
            event.unified_msg_origin,
            time.time(),
            str(event.get_sender_id()),
        )
        out, seen = [], set()

        def add(im, src):
            if im["key"] not in seen:
                seen.add(im["key"])
                out.append((im, src))

        try:
            t0 = (
                event.get_extra("seememe_t") or None
            )  # on_message 记下这条消息的时间（算「发图之前在聊什么」要用）
        except Exception:
            t0 = None
        for im in self._images(event, t0):
            add(im, "here")
        for im in self._quoted(event):
            add(im, "quoted")
        if not out:
            for t, u, _n, im in reversed(self.imgs.get(umo) or []):
                if now - t <= self._c("recent_sec", 120) and u == uid:
                    add(im, "recent")
        if not out and TEXT_REFERS.search(event.message_str or ""):
            for t, _u, _n, im in reversed(self.imgs.get(umo) or []):
                if now - t <= self._c("refer_sec", 600):
                    add(im, "refer")
                    break
        return out[: max(1, self._c("max_images", 3))]

    def _ctx(self, umo, im, to_bot, ask="", place=""):
        chat = [
            f"{n}: {x}"
            for t, n, x in (self.chat.get(umo) or [])
            if t < im["t"] - 0.01 and x
        ][-4:]
        return {
            "sender": im.get("sender") or "",
            "to_bot": bool(to_bot),
            "chat": chat,
            "said": im.get("said") or "",
            "ask": ask,
            "place": place,
        }

    @staticmethod
    def _place(event):
        try:
            qq = event.get_platform_name() == "aiocqhttp"
            private = event.is_private_chat()
        except Exception:
            qq, private = False, False
        return ("QQ " if qq else "") + ("私聊" if private else "群" if qq else "群聊")

    # ───────── 每条消息：记一下 ─────────
    @staticmethod
    def _text(event):
        out = [c.text for c in event.get_messages() or [] if isinstance(c, Comp.Plain)]
        return "".join(out).strip() or (event.message_str or "").strip()

    @staticmethod
    def _plain(event):
        out = []
        for c in event.get_messages() or []:
            if isinstance(c, Comp.Plain):
                out.append(c.text)
            elif isinstance(c, Comp.Image):
                out.append("[图片]")
        return "".join(out).strip() or (event.message_str or "").strip()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize - 10)
    async def on_message(self, event: AstrMessageEvent):
        if not self._c("enable", True):
            return
        umo, now = event.unified_msg_origin, time.time()
        sid = str(event.get_self_id() or "")
        if str(event.get_sender_id()) == sid:
            return
        self.last_sid = sid or self.last_sid
        try:
            event.set_extra("seememe_t", now)
        except Exception:
            pass
        name = event.get_sender_name() or str(event.get_sender_id())
        imgs = self._images(event, now)
        self.chat[umo].append(
            (now, name, (self._plain(event) or ("[表情]" if imgs else ""))[:80])
        )
        for im in imgs:
            self.imgs[umo].append((now, str(event.get_sender_id()), name, im))
        self._ensure_saver()
        if not self.config.get("bot_names"):
            gid = str(event.get_group_id() or "")
            if sid not in self.bot_names or (gid and (sid, gid) not in self.cards):
                self._spawn(self._learn_name(event, sid, gid))
        if imgs and self._c("prefetch", False):
            for im in (
                imgs
            ):  # 「群里的图都先看」：没人找机器人的图也先看好（以后有人提起就不用等）
                self._spawn(
                    self._caption(
                        im,
                        self._ctx(umo, im, False, place=self._place(event)),
                        umo,
                        sid,
                        background=True,
                        where=self._where(event),
                    )
                )

    async def _learn_name(self, event, sid, gid):
        bot = getattr(event, "bot", None)
        if bot is None:
            self.bot_names.setdefault(sid, "")
            return
        if sid not in self.bot_names:
            self.bot_names[sid] = ""
            try:
                info = await bot.call_action("get_login_info")
                self.bot_names[sid] = str((info or {}).get("nickname") or "")
            except Exception:
                pass
        if gid and (sid, gid) not in self.cards and gid.isdigit() and sid.isdigit():
            self.cards[(sid, gid)] = ""
            try:
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(gid),
                    user_id=int(sid),
                    no_cache=False,
                )
                self.cards[(sid, gid)] = str((info or {}).get("card") or "")
            except Exception:
                pass

    def _names(self, sid, gid=""):
        v = self.config.get("bot_names") or []
        if isinstance(v, str):
            v = v.replace("，", ",").split(",")
        names = [str(x).strip() for x in v if str(x).strip()]
        return names or [
            n
            for n in (
                self.bot_names.get(str(sid)),
                self.cards.get((str(sid), str(gid))),
            )
            if n
        ]

    @staticmethod
    def _where(event):
        try:
            gid = event.get_group_id()
        except Exception:
            gid = ""
        return f"群 {gid}" if gid else "私聊"

    # ───────── 要回话了：把看懂的图塞进这一轮 ─────────
    @filter.on_llm_request(priority=50)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._c("enable", True):
            return
        items = self._relevant(event)
        if not items:
            return
        umo, sid, now = (
            event.unified_msg_origin,
            str(event.get_self_id() or ""),
            time.time(),
        )
        remember, keep = self._c("remember", True), self._c("refer_sec", 600)
        given = self.given[umo]
        for k in [k for k, t in given.items() if now - t > keep]:
            given.pop(k, None)
        # 前一轮已经记进聊天记录的图：「他刚才发的」「上面那张」不再重复给（聊天记录里有）
        items = [
            (im, src)
            for im, src in items
            if not (remember and im["key"] in given and src in ("recent", "refer"))
        ]
        if not items:
            return
        for p in req.extra_user_content_parts or []:
            t = getattr(p, "text", None) if not isinstance(p, dict) else p.get("text")
            if isinstance(t, str) and t.startswith("<image_caption>"):
                self._warn_once(
                    "dup",
                    "AstrBot 自己的「图片转述模型」也在看这张图（同一张图看了两遍、花两份钱）。"
                    "用这个插件的话可以把它清空：AstrBot 配置 → AI → 模型 → 图片转述模型。",
                    6 * 3600,
                )
                break
        # 主模型自己能看图：这条消息的图 / 引用的图 AstrBot 会直接给它看；要不要另外再看一遍（默认看：认梗、认自己、看坑更准）
        main_sees = await self._main_sees(umo)
        look = [
            (im, src)
            for im, src in items
            if not (
                main_sees
                and not self._c("also_when_main_sees", True)
                and src in ("here", "quoted")
            )
        ]
        ask = self._text(event)
        place = self._place(event)
        tasks = {
            im["key"]: self._spawn(
                self._caption(
                    im,
                    self._ctx(umo, im, True, ask=ask, place=place),
                    umo,
                    sid,
                    where=self._where(event),
                )
            )
            for im, _src in look
        }
        caps = {}
        if tasks:
            done, pending = await asyncio.wait(
                list(tasks.values()), timeout=self._c("wait_sec", 30)
            )
            if pending:  # 没看完的接着看（看完存着 下次直接用）
                self._warn_once(
                    "timeout",
                    f"看图超过 {self._c('wait_sec', 30)} 秒 这一轮先不带没看完的",
                    60,
                )
            for k, t in tasks.items():
                if (
                    t in done
                    and not t.cancelled()
                    and t.exception() is None
                    and t.result()
                ):
                    caps[k] = t.result()
        keep_items, temp_items = [], []
        for im, src in items:
            cap = caps.get(im["key"])
            if not cap:
                continue
            row = (im.get("sender"), cap, src == "quoted")
            if remember and im["key"] not in given:
                keep_items.append(row)
                given[im["key"]] = now
            else:
                temp_items.append(row)
        # 一张都没看成、对话模型自己也看不到这些图（它看不了图 / 图不在这条消息里）：别给提示，不然它以为自己看到了
        if not caps and not (
            main_sees and any(src in ("here", "quoted") for _im, src in items)
        ):
            return
        if keep_items:
            req.extra_user_content_parts.append(
                _part(caption_block(keep_items), temp=False)
            )
        tail = caption_block(temp_items)
        if self._c("hints", True):
            tail = (tail + "\n\n" if tail else "") + hints_block(
                list(caps.values()), blind=not main_sees
            )
        if tail:
            req.extra_user_content_parts.append(_part(tail, temp=True))
        self._today()["turns"] += 1
        self._save()
        logger.info(
            f"{TAG} 这一轮带上了 {len(caps)} 张图的看懂结果"
            + ("（主模型自己也看得到图）" if main_sees else "")
        )

    # ───────── 看一张图 ─────────
    @staticmethod
    def _supports_image(prov):
        m = (getattr(prov, "provider_config", None) or {}).get("modalities", None)
        return m == [] or (isinstance(m, list) and "image" in m)

    async def _main_sees(self, umo):
        try:
            prov = await self.context.get_using_provider_async(umo)
        except Exception:
            return False
        return prov is not None and self._supports_image(prov)

    async def _vision_pid(self, umo):
        pid = str(self.config.get("vision_provider_id") or "").strip()
        if pid:
            return pid
        try:
            prov = await self.context.get_using_provider_async(umo)
        except Exception:
            prov = None
        if prov is not None and self._supports_image(prov):
            return prov.meta().id
        try:
            cap = (self.context.get_config(umo=umo).get("provider_settings") or {}).get(
                "default_image_caption_provider_id"
            )
        except Exception:
            cap = ""
        return cap or None

    async def _prepare(self, ref, max_size=1280):
        """→ (本地图片路径, 是不是动图拼帧, 用完要删的文件)；拿不到 → (None, False, [])"""
        if prepare_model_image is not None:
            try:
                got = await prepare_model_image(
                    ref, max_size=max_size, output_dir=self.tmp_dir
                )
                if got:
                    path, montage, cleanup, orig = got
                    junk = [path] if cleanup else []
                    if (
                        str(ref).startswith(("http://", "https://", "base64://"))
                        and orig
                        and orig not in junk
                    ):
                        junk.append(orig)  # 下载下来的原图也是这次的临时文件
                    return path, bool(montage), junk
            except Exception as e:
                self._warn_once(
                    "prep", f"准备图片失败（AstrBot 的处理）：{e!r}"[:200], 60
                )
        if str(ref).startswith("http"):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as s:
                    async with s.get(ref, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status != 200:
                            return None, False, []
                        data = await r.read()
                fd, name = tempfile.mkstemp(
                    prefix="img_", suffix=_ext(data), dir=self.tmp_dir
                )
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                return name, False, [name]
            except Exception:
                return None, False, []
        p = str(ref)[7:] if str(ref).startswith("file://") else str(ref)
        return (p, False, []) if os.path.isfile(p) else (None, False, [])

    async def _avatar_ref(self, sid):
        """机器人自己的头像（给看图的模型对照「图里是不是它」）"""
        mode = str(self.config.get("avatar_mode") or "QQ 头像").strip()
        if mode in ("不用", "none"):
            return None
        custom = mode in ("自定义图片", "custom")
        url = (
            str(self.config.get("avatar_url") or "").strip()
            if custom
            else (
                f"https://q1.qlogo.cn/g?b=qq&nk={sid}&s=640"
                if sid and sid.isdigit()
                else ""
            )
        )
        if not url:
            return None
        slot = "custom" if custom else sid
        hit = self.avatar.get(slot)
        if (
            hit
            and time.time() - hit[1] < 86400
            and (not hit[0] or os.path.exists(hit[0]))
        ):
            return hit[0] or None
        path, _m, junk = await self._prepare(url, max_size=320)
        if not path:
            self._warn_once(
                "avatar", f"拿不到机器人的头像（{url[:60]}）这次看图不带头像对照"
            )
            self.avatar[slot] = ("", time.time())
            return None
        try:
            data = Path(path).read_bytes()
            dst = self.data_dir / f"avatar_{slot}{_ext(data)}"
            dst.write_bytes(data)
        finally:
            for j in junk:
                Path(j).unlink(missing_ok=True)
        self.avatar[slot] = (str(dst), time.time())
        return str(dst)

    async def _caption(
        self, im, ctx, umo, sid, background=False, fresh=False, where="", info=None
    ):
        k = im["key"]
        if not fresh:
            hit = self.cache.get(k)
            if hit:
                self._today()["cached"] += 1
                return hit
            if k in self.inflight:
                return await asyncio.shield(self.inflight[k])
        st, now = self._today(), time.time()
        fut = asyncio.get_running_loop().create_future()
        if not fresh:
            self.inflight[k] = fut
        path, junk, cap = None, [], None
        try:
            pid = await self._vision_pid(umo)
            if not pid:
                self._warn_once(
                    "nopid",
                    "没有能看图的模型：去插件配置里选「看图用哪个模型」，或者在 AstrBot 的模型设置里给对话模型的「模型能力」勾上「图像」",
                )
                if info is not None:
                    info["error"] = "没有能看图的模型：插件配置里选「看图用哪个模型」"
                return None
            path, montage, junk = await self._prepare(im["ref"])
            if not path and im.get("alt"):
                path, montage, junk = await self._prepare(im["alt"])
            if not path:
                self._err(f"图拿不到：{str(im['ref'])[:80]}")
                return None
            md5 = "m:" + hashlib.md5(Path(path).read_bytes()).hexdigest()
            if not fresh:
                hit = self.cache.get(md5)
                if hit:
                    self.cache.put([k], hit)
                    st["cached"] += 1
                    cap = hit
                    return hit
            while self.hour and now - self.hour[0] > 3600:
                self.hour.popleft()
            if st["n"] >= self._c("daily_max", 300) or len(self.hour) >= self._c(
                "hourly_max", 60
            ):
                self._warn_once(
                    "limit",
                    f"看图到上限了（每小时 {self._c('hourly_max', 60)} 张 / 每天 {self._c('daily_max', 300)} 张）先不看",
                )
                if info is not None:
                    info["error"] = "到看图上限了（插件配置里能调）"
                return None
            ref = await self._avatar_ref(sid)
            note = "动图（按顺序抽了几帧拼成一张）" if montage else ""
            gid = str(where[2:]) if str(where).startswith("群 ") else ""
            prompt = build_prompt(
                self._names(sid, gid),
                str(self.config.get("bot_look") or ""),
                ctx,
                ref=bool(ref),
                note=note,
                kind=im.get("kind") or "",
                max_chars=self._c("max_chars", 180),
            )
            if info is not None:
                info.update(prompt=prompt, model=pid, avatar=bool(ref))
            self.hour.append(now)
            st["n"] += 1
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=pid,
                    prompt=prompt,
                    image_urls=[ref, path] if ref else [path],
                ),
                self._c("timeout_sec", 40),
            )
            text = getattr(resp, "completion_text", "") or ""
            u = getattr(resp, "usage", None)
            used = int(getattr(u, "total", 0) or 0) if u is not None else 0
            st["tokens"] += used
            if info is not None:
                info["tokens"] = used
            cap = trim(text, self._c("max_chars", 180) + 80)
            if cap and note and not cap.startswith("动图"):
                cap = "动图：" + cap
            if not cap:
                self._err("模型没回描述")
                return None
            if refused(cap):
                self._err(f"看图模型没看（它说：{cap[:60]}）—— 换一个能看图的模型试试")
                if info is not None:
                    info["error"] = f"看图模型没看，它说：{cap[:80]}"
                return None
            if not fresh:
                self.cache.put([k, md5], cap)
            self.recent_caps.append(
                {
                    "t": int(time.time()),
                    "who": im.get("sender") or "",
                    "cap": cap,
                    "bg": bool(background),
                    "where": where,
                    "model": pid,
                    "tokens": used,
                }
            )
            self._save(
                force=True
            )  # 看一张图要花钱：看完马上存（AstrBot 被直接关掉也不丢）
            logger.info(f"{TAG} {cap[:120]}")
            return cap
        except asyncio.TimeoutError:
            self._err("看图超时")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._err(f"看图失败：{e!r}")
            self._warn_once("fail", f"看图失败：{e!r}"[:200], 60)
            return None
        finally:
            if self.inflight.get(k) is fut:
                self.inflight.pop(k, None)
            if not fut.done():
                fut.set_result(cap)
            for j in junk:
                Path(j).unlink(missing_ok=True)

    # ───────── 面板 ─────────
    def _register_web(self):
        import importlib.util

        try:
            if importlib.util.find_spec("astrbot.api.web") is None:
                return
        except Exception:
            return
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/state", self.web_state, ["GET"], "看图：状态"
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/prep", self.web_prep, ["POST"], "看图：试图前先说一句"
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/test", self.web_test, ["POST"], "看图：试一张图"
        )

    async def snapshot(self):
        try:
            pid = await self._vision_pid(None)
        except Exception:
            pid = None
        return {
            "today": dict(self._today()),
            "cache": len(self.cache.d),
            "recent": list(self.recent_caps)[-30:][::-1],
            "errors": list(self.errors)[-10:][::-1],
            "hour": len(self.hour),
            "vision": pid or "",
            "main_sees": await self._main_sees(None),
            "names": self._names(self.last_sid),
            "limits": {
                "daily": self._c("daily_max", 300),
                "hourly": self._c("hourly_max", 60),
            },
            "config": {
                k: self.config.get(k)
                for k in (
                    "vision_provider_id",
                    "bot_names",
                    "bot_look",
                    "avatar_mode",
                    "prefetch",
                    "hints",
                    "remember",
                    "also_when_main_sees",
                )
            },
        }

    async def web_state(self):
        return {"status": "ok", "data": await self.snapshot()}

    async def web_prep(self):
        from astrbot.api.web import request

        body = await request.json({}) or {}
        self.test_ctx = {"said": str(body.get("said") or "")[:120], "t": time.time()}
        return {"status": "ok", "data": {"ok": True}}

    async def web_test(self):
        from astrbot.api.web import request

        files = await request.files()
        f = files.get("file") if files else None
        if f is None:
            return {"status": "error", "message": "没收到图"}
        data = await f.read(10 * 1024 * 1024 + 1)
        if not data or len(data) > 10 * 1024 * 1024:
            return {"status": "error", "message": "图是空的或者太大（10MB 以内）"}
        said = (
            self.test_ctx.get("said", "")
            if time.time() - self.test_ctx.get("t", 0) < 120
            else ""
        )
        self.test_ctx = {}
        fd, name = tempfile.mkstemp(prefix="test_", suffix=_ext(data), dir=self.tmp_dir)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        try:
            im = {
                "ref": name,
                "key": "t:" + hashlib.md5(data).hexdigest(),
                "sender": "面板测试",
                "uid": "",
                "t": time.time(),
                "quoted": False,
                "kind": "",
                "said": said,
            }
            info = {}
            cap = await self._caption(
                im,
                {"sender": "面板测试", "to_bot": True, "chat": [], "said": said},
                None,
                self.last_sid,
                fresh=True,
                where="面板测试",
                info=info,
            )
            if not cap:
                last = info.get("error") or (
                    list(self.errors)[-1]["text"]
                    if self.errors
                    else "看不了（看 AstrBot 日志）"
                )
                return {"status": "error", "message": f"没看成：{last}"}
            return {
                "status": "ok",
                "data": {
                    "caption": cap,
                    "block": block([("面板测试", cap, False)], True),
                    "prompt": info.get("prompt", ""),
                    "model": info.get("model", ""),
                    "tokens": info.get("tokens", 0),
                    "avatar": info.get("avatar", False),
                    "state": await self.snapshot(),
                },
            }
        finally:
            Path(name).unlink(missing_ok=True)
