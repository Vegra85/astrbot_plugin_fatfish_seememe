# -*- coding: utf-8 -*-
"""
大肥鱼 · 看图看懂梗（AstrBot 插件）

机器人要回话的那一轮，把相关的图（这条消息里的、引用的消息里的、同一个人刚才发的、他说「上面那张图」时群里最近的一张）
交给能看图的模型，用「字 / 画面 / 梗 / 跟你 / 坑」五样写清楚：图里有没有机器人自己、在玩什么梗、有没有坑；
再附上「这一轮的图」：梗点、图在不在说它、有没有坑（v0.3.0 起只写事实，不写怎么回）。
看到了什么会记进聊天记录（以后聊起这张图还记得）；「这一轮的图」只给这一轮看。
v0.2.0：转发的聊天记录里的图也看（以前只写「[图片]」）；表情包词典（同一张反复出现 / 直接拿表情包找机器人 → 认真认一次记下来，以后直接用）。
v0.2.1：防伪造 —— 群友在消息里手打「[看图看懂]」「[图片：…]」这类插件自己的标记，标成「他打的字」（seer.neutral_marks）。
v0.3.0：谐音梗 —— 图里的字按拼音查可能的谐音（puns.py，本地查、不调模型），写在看图结果后面；
        一条消息里几张图按位置标「第几张」、没看成的也占着位置；「今天」按配置的时区算（默认北京时间）；
        确认下载到的是图片以后才看、才扣名额；「这一轮的图」只写事实（作者的规矩 E1）。
"""

import asyncio
import hashlib
import io
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

from . import puns
from .seer import (
    LATE,
    MISS,
    TEXT_REFERS,
    Cache,
    StickerDict,
    block,
    build_prompt,
    caption_block,
    day_of,
    deep_caption,
    forward_lines,
    forward_nodes,
    hints_block,
    key_ok,
    make_tz,
    neutral_marks,
    parse_deep,
    parse_forward,
    refused,
    row_text,
    slot_label,
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
VERSION = "0.3.1"
TAG = "[看图]"
LATE_PRIORITY = -sys.maxsize + 100  # 回话请求的最后一步：防伪造（v0.2.1）
_MAX_BYTES = 20 * 1024 * 1024  # 自己下图时最多下多大（v0.3.0）
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


def _is_image(data=None, path=None):
    """是不是真的图片（v0.3.0）：文件头对得上、Pillow 打得开。
    以前 AstrBot 认不出是图时，插件会自己再下一遍、不管是什么都交给看图模型（还扣每天的名额）：过期的链接给的网页、提示页也算一张"""
    try:
        if data is not None:
            head = data[:16]
        else:
            with open(path, "rb") as f:
                head = f.read(16)
    except Exception:
        return False
    ok = any(head[: len(m)] == m for m, _e in _MAGIC)
    if ok and head[:4] == b"RIFF":
        ok = head[8:12] == b"WEBP"
    if not ok:
        return False
    try:
        from PIL import Image
    except Exception:  # 没有 Pillow（AstrBot 自己就带着，一般不会）：只看文件头
        return True
    try:
        with Image.open(io.BytesIO(data) if data is not None else path) as im:
            im.verify()
        return True
    except Exception:
        return False


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
        # 按哪个时区算「今天」（v0.3.0，默认北京时间；留空 = 跟服务器，以前的做法）
        self.tz, self.tz_label, tz_ok = make_tz(
            self.config.get("day_tz", "Asia/Shanghai")
        )
        if not tz_ok:
            logger.warning(
                f"{TAG} 认不出时区「{self.config.get('day_tz')}」（Windows 上 Asia/Shanghai 以外的名字要装 tzdata；"
                "也可以写 UTC+8 这种）：先按北京时间算「今天」"
            )
        st = self._load()
        self.cache = Cache(self._c("cache_keep", 600))
        self.cache.load(st.get("cache"))
        self.stats = st.get("stats") or {}
        self.recent_caps = deque(st.get("recent") or [], maxlen=60)
        self.errors = deque(st.get("errors") or [], maxlen=20)
        # 表情包词典：单独一个文件（跨群共用、重启不丢；想清空就删掉这个文件）
        self.sticker_file = self.data_dir / "stickers.json"
        self.stickers = StickerDict(
            self._c("sticker_after", 3),
            self._c("sticker_daily", 30),
            self._c("sticker_keep", 2000),
            tz=self.tz,
        )
        self.stickers.load(self._load(self.sticker_file))
        self.stickers.dirty = False
        self._sd_saved = time.time()
        self.chat = defaultdict(
            lambda: deque(maxlen=16)
        )  # 会话 -> [(时间, 名字, 文字)]
        self.imgs = defaultdict(
            lambda: deque(maxlen=30)
        )  # 会话 -> [(时间, QQ, 名字, 图)]
        self.given = defaultdict(dict)  # 会话 -> {图: 记进聊天记录的时间}
        self.fwds = defaultdict(
            lambda: deque(maxlen=10)
        )  # 会话 -> [(时间, QQ, 名字, 转发)]：先发转发、再 @ 机器人
        self.fwd_cache = {}  # 转发的 id -> (时间, 拆好的)：同一段别反复去拿
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

    def _load(self, path=None):
        try:
            d = json.loads((path or self.state_file).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self, force=False, sd=False):
        """AstrBot 被直接关掉（关窗口、结束进程）时不会调 terminate()，所以有变化就尽快存：最多 3 秒存一次
        表情包词典（每条带图的消息都要记一笔，文件也大一些）：最多 30 秒存一次；刚认好一张（sd=True）马上存"""
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
            self._write(self.state_file, d)
            self._dirty = False
            if self.stickers.dirty and (sd or time.time() - self._sd_saved >= 30):
                self._save_stickers()
        except Exception as e:
            logger.warning(f"{TAG} 存档失败：{e!r}")

    def _save_stickers(self):
        self.stickers.dirty = False
        self._sd_saved = time.time()
        try:
            self._write(self.sticker_file, self.stickers.dump())
        except OSError as e:
            self.stickers.dirty = True
            logger.warning(f"{TAG} 表情包词典存档失败：{e!r}")

    @staticmethod
    def _write(path, d):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    async def _save_loop(self):
        while True:
            await asyncio.sleep(5)
            if self._dirty:
                self._save(force=True)
            elif self.stickers.dirty and time.time() - self._sd_saved >= 30:
                self._save_stickers()

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
        self._save(force=True, sd=True)

    def _today(self):
        day = day_of(None, self.tz)  # v0.3.0：按配置的时区（以前按服务器的时间）
        if self.stats.get("day") != day:
            self.stats = {
                "day": day,
                "n": 0,
                "tokens": 0,
                "errors": 0,
                "cached": 0,
                "turns": 0,
            }
        for k in ("turns", "dict", "deep", "fwd"):  # 老存档里没有的
            self.stats.setdefault(k, 0)
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

    def _one(self, c, d, sender, uid, t, quoted=False, said=""):
        """一张图（AstrBot 的 Image 组件 + 对应的原始消息段 d）→ 要看的那张；没有地址 → None
        d 里有原来的文件名（图片内容的哈希）、网址、是不是表情包（QQ 的才有）"""
        ref = c.url or c.file or c.path
        if not ref:
            return None
        alt = str(d.get("url") or "")
        alt = (
            alt if alt.startswith("http") and alt != ref else ""
        )  # 本地那份没了还能再下
        kind = (
            "表情包"
            if str(d.get("sub_type")) == "1" or "表情" in str(d.get("summary") or "")
            else ""
        )
        return {
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

    def _mface(self, d, sender, uid, t, said=""):
        """QQ 商城表情（mface，AstrBot 自己会丢掉它）→ 要看的那张；没有网址 → None"""
        if not str(d.get("url") or "").startswith("http"):
            return None
        name = str(d.get("summary") or "").strip("[]")[:20]
        return {
            "ref": d["url"],
            "key": self._key(d["url"], d.get("emoji_id") or ""),
            "sender": sender,
            "uid": str(uid),
            "t": t,
            "quoted": False,
            "kind": f"QQ 表情「{name}」" if name else "QQ 表情",
            "said": said,
        }

    @staticmethod
    def _mid(event, t):
        """这条消息的 id（同一条消息里的几张图算一组，标「第几张」用）"""
        mid = getattr(getattr(event, "message_obj", None), "message_id", "")
        return str(mid or "") or f"t{t}"

    def _from_comps(
        self, comps, sender, uid, t, quoted=False, raws=None, said="", mid=""
    ):
        """raws：和这些图一一对应的原始消息段（QQ 的才有）；对不上就都不用
        每张带上 pos（第几张）、of（一共几张）、mid（哪条消息）—— v0.3.0 按位置配"""
        imgs = [c for c in comps or [] if isinstance(c, Comp.Image)]
        raws = raws if raws is not None and len(raws) == len(imgs) else [{}] * len(imgs)
        out = []
        for pos, (c, d) in enumerate(zip(imgs, raws), 1):
            im = self._one(c, d, sender, uid, t, quoted, said)
            if im:
                im.update(pos=pos, of=len(imgs), mid=mid)
                out.append(im)
        return out

    def _images(self, event, t=None):
        """这条消息自己带的图（含 QQ 商城表情 mface —— AstrBot 自己会丢掉它），按消息里的顺序排（v0.3.0）
        以前：先排完所有普通图、再排商城表情（「[狗头] + 一张截图」排成截图在前）；一条消息里几张图也不分第几张
        现在：照原始消息段的顺序，每张带上 pos（这条消息里第几张，看不了的也占一个位置）、of（一共几张）、mid（哪条消息）"""
        t = t or time.time()
        sender, uid = (
            event.get_sender_name() or str(event.get_sender_id()),
            str(event.get_sender_id()),
        )
        segs = self._raw_segs(event)  # NapCat：sub_type=1 / 「[动画表情]」是表情包
        said = self._text(event)
        comps = [c for c in event.get_messages() or [] if isinstance(c, Comp.Image)]
        raw_imgs = [s for s in segs if s.get("type") == "image"]
        if segs and len(raw_imgs) == len(comps):
            order, k = [], 0
            for s in segs:
                if s.get("type") == "image":
                    order.append((comps[k], s.get("data") or {}))
                    k += 1
                elif s.get("type") == "mface":
                    order.append((None, s.get("data") or {}))
        else:  # 对不上（AstrBot 丢了哪一张）/ 没有原始消息段：普通图在前、商城表情在后（跟以前一样）
            order = [(c, {}) for c in comps] + [
                (None, s.get("data") or {}) for s in segs if s.get("type") == "mface"
            ]
        mid, out = self._mid(event, t), []
        for pos, (c, d) in enumerate(order, 1):
            if c is not None:
                im = self._one(c, d, sender, uid, t, said=said)
            else:
                im = self._mface(d, sender, uid, t, said)
            if im:
                im.update(pos=pos, of=len(order), mid=mid)
                out.append(im)
        return out

    def _quoted(self, event):
        """引用的那条消息里的图（自己算第几张，不跟这条消息的混在一起数）"""
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
                    mid="q" + str(getattr(c, "id", "") or ""),
                )
        return out

    def _relevant(self, event):
        """这一轮要看的图 → [(图, 哪来的)]：这条消息的 / 引用的 → 都没有就看他刚才发的（图和 @ 常常分开发）
        → 还没有、他又说到「图」了 → 群里最近一张。
        每张带上 poss（这张在它那条消息里占的位置；同一张发了两遍就有两个）—— v0.3.0"""
        umo, now, uid = (
            event.unified_msg_origin,
            time.time(),
            str(event.get_sender_id()),
        )
        out, idx = [], {}
        most = max(1, self._c("max_images", 3))

        def add(im, src):
            k = im["key"]
            if k in idx:  # 同一张图：同一条消息里发了两遍 → 记下它也占了那个位置
                o = out[idx[k]][0]
                if (
                    im.get("pos")
                    and o.get("mid") == im.get("mid")
                    and im["pos"] not in o["poss"]
                ):
                    o["poss"].append(im["pos"])
                return
            idx[k] = len(out)
            out.append((dict(im, poss=[im["pos"]] if im.get("pos") else []), src))

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
            # v0.3.0：留最近的几张，但按发的顺序排（以前最新的排最前：一条消息里的两张图倒着写）
            out = sorted(
                out[:most], key=lambda x: (x[0].get("t") or 0, x[0].get("pos") or 0)
            )
        if not out and TEXT_REFERS.search(event.message_str or ""):
            for t, _u, _n, im in reversed(self.imgs.get(umo) or []):
                if now - t <= self._c("refer_sec", 600):
                    add(im, "refer")
                    break
        return out[:most]

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
            elif isinstance(c, Comp.Forward):
                out.append("[聊天记录]")
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
        uid = str(event.get_sender_id())
        imgs = self._images(event, now)
        before = [x for _t, _n, x in list(self.chat[umo])[-2:]]
        self.chat[umo].append(
            (now, name, (self._plain(event) or ("[表情]" if imgs else ""))[:80])
        )
        for im in imgs:
            self.imgs[umo].append((now, uid, name, im))
        if self._c("forward_images", True):
            for ref in self._fwd_refs(event):
                self.fwds[umo].append((now, uid, name, ref))
        due = self._sticker_seen(event, umo, uid, imgs, before, now)
        self._ensure_saver()
        if not self.config.get("bot_names"):
            gid = str(event.get_group_id() or "")
            if sid not in self.bot_names or (gid and (sid, gid) not in self.cards):
                self._spawn(self._learn_name(event, sid, gid))
        # 「群里的图都先看好」：没人找机器人的图也先看好（以后有人提起就不用等）；
        # 表情包词典该认的那张（第 3 次出现 / 直接拿表情包找机器人）也在这里开始认 —— 机器人要回话的那一轮会等它
        prefetch = self._c("prefetch", False)
        for im in imgs:
            if prefetch or im["key"] in due:
                self._spawn(
                    self._caption(
                        im,
                        self._ctx(
                            umo, im, self._to_bot(event), place=self._place(event)
                        ),
                        umo,
                        sid,
                        background=True,
                        where=self._where(event),
                    )
                )

    @staticmethod
    def _to_bot(event):
        return bool(
            event.is_private_chat() or getattr(event, "is_at_or_wake_command", False)
        )

    def _sd_on(self):
        return bool(self._c("sticker_dict", True))

    def _sticker_seen(self, event, umo, uid, imgs, before, now):
        """表情包词典：这条消息里的图各记一次（只认 QQ 给的文件名 / 表情 id —— 网址每次发都不一样，数不了）；
        没带图的话，看看是不是有人刚发完表情包、这句是接它的。→ 现在该深度认的那几张的凭据"""
        if not self._sd_on():
            return set()
        if not imgs:
            self.stickers.after_msg(umo, uid, self._text(event), now)
            return set()
        to_bot, said, due = self._to_bot(event), self._text(event), set()
        for im in imgs[:3]:
            if im["key"].startswith("f:") and self.stickers.seen(
                im["key"],
                umo,
                uid,
                before=before,
                said=said,
                to_bot=to_bot,
                sticker=bool(im.get("kind")),
                now=now,
            ):
                due.add(im["key"])
        return due

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

    # ───────── 转发的聊天记录（v0.2.0）─────────
    def _fwd_refs(self, event):
        """这条消息自己带的转发 → [{"id", "content"}]（NapCat 有时直接把内容带在消息段里，就不用再去拿了）"""
        out = []
        for s in self._raw_segs(event):
            if s.get("type") != "forward":
                continue
            d = s.get("data") or {}
            content = d.get("content") if isinstance(d.get("content"), list) else None
            fid = str(d.get("id") or d.get("res_id") or "")
            if not fid and content:
                fid = (
                    "h"
                    + hashlib.sha1(
                        json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()[:16]
                )
            if fid:
                out.append({"id": fid, "content": content})
        if not out:  # 别的平台 / 没有原始消息段：AstrBot 的 Forward 组件只留了 id
            for c in event.get_messages() or []:
                if isinstance(c, Comp.Forward) and getattr(c, "id", None):
                    out.append({"id": str(c.id), "content": None})
        return out

    def _forwards(self, event, items):
        """这一轮要看的转发 → [(转发, 哪来的, 谁转发的)]：这条消息的 / 引用的那条 →
        都没有（这条也没带图、没引用图）就看他刚才发的（转发的聊天记录里没法 @ 人，群友都是先转发、再 @ 机器人）"""
        umo, now, uid = (
            event.unified_msg_origin,
            time.time(),
            str(event.get_sender_id()),
        )
        me = event.get_sender_name() or uid
        out = [(ref, "here", me) for ref in self._fwd_refs(event)]
        for c in event.get_messages() or []:
            if isinstance(c, Comp.Reply) and c.chain:
                for x in c.chain:
                    if isinstance(x, Comp.Forward) and getattr(x, "id", None):
                        who = c.sender_nickname or str(c.sender_id or "")
                        out.append(({"id": str(x.id), "content": None}, "quoted", who))
        if not out and not any(src in ("here", "quoted") for _im, src in items):
            for t, u, n, ref in reversed(self.fwds.get(umo) or []):
                if now - t <= self._c("recent_sec", 120) and u == uid:
                    out.append((ref, "recent", n))
                    break
        seen, uniq = set(), []
        for ref, src, who in out:
            if ref["id"] not in seen:
                seen.add(ref["id"])
                uniq.append((ref, src, who))
        return uniq[:2]

    async def _forward_rows(self, event, ref):
        """→ (rows, imgs, 一共几条)；拿不到 → ([], [], 0)"""
        fid, now = ref["id"], time.time()
        hit = self.fwd_cache.get(fid)
        if hit and now - hit[0] < 600:
            return hit[1]
        payload = ref.get("content")
        bot = getattr(event, "bot", None)
        if not forward_nodes(payload) and bot is not None:
            sid = str(event.get_self_id() or "")
            route = {"self_id": int(sid)} if sid.isdigit() else {}
            tries = [{"message_id": fid}, {"id": fid}]
            if fid.isdigit():
                tries.append({"message_id": int(fid)})
            for params in tries:
                got = await asyncio.gather(
                    asyncio.wait_for(
                        bot.call_action("get_forward_msg", **params, **route), 10
                    ),
                    return_exceptions=True,
                )
                payload = got[0]  # 调不通（ActionFailed / 超时…）是个异常 → 当成没拿到
                if forward_nodes(payload):
                    break
        nodes = forward_nodes(payload)
        rows, imgs = parse_forward(payload, max_rows=30)
        if not rows:
            self._warn_once(
                "fwd",
                "读不出转发的聊天记录（QQ 端的 get_forward_msg 没给内容）这一轮里面的图先不看",
                600,
            )
        got = (rows, imgs, len(nodes))
        for k in [k for k, v in self.fwd_cache.items() if now - v[0] > 600]:
            self.fwd_cache.pop(k, None)
        if rows:
            self.fwd_cache[fid] = (now, got)
        return got

    # ───────── 谐音（v0.3.0）─────────
    def _pun_words(self):
        """配置里自己加的谐音词（「夺笋=多损」「多损」）"""
        v = self.config.get("pun_words") or []
        if isinstance(v, str):
            v = v.replace("，", ",").replace("\n", ",").split(",")
        return tuple(str(x).strip() for x in v if str(x).strip())[:200]

    def _with_pun(self, cap):
        """看图结果后面接上谐音：图里的字按读音查到可能的谐音就写上（只写事实：怎么读、可能是哪个词）；
        没字 / 没查到 / 关掉了 → 原样。不调模型"""
        if not cap or cap in (MISS, LATE) or not self._c("puns", True):
            return cap
        try:
            n = puns.note(cap, self._pun_words())
        except Exception as e:
            self._warn_once("pun", f"查谐音出错（不影响看图）：{e!r}"[:200], 3600)
            return cap
        if not puns.ready():
            self._warn_once(
                "nopinyin",
                "没装 pypinyin：谐音只按词表里的写法认（「夺笋」「520」这种），不按读音找。"
                "装插件时 AstrBot 会按 requirements.txt 自动装；没装上可以在 AstrBot 的环境里 pip install pypinyin",
                24 * 3600,
            )
        return f"{cap}。{n}" if n else cap

    # ───────── 要回话了：把看懂的图塞进这一轮 ─────────
    def _neutralize(self, req, skip=()):
        """防伪造（v0.2.1）：他说的话、系统提示、请求里别的段落（AstrBot 群聊上下文、别的插件加的）里手打的
        「[看图看懂]」「[图片：…」→ 标成「他打的字」。skip = 本插件自己加的段落（id），不动。出错不影响看图"""
        try:
            n = 0
            for attr in ("prompt", "system_prompt"):
                v = getattr(req, attr, None)
                if isinstance(v, str) and v:
                    new = neutral_marks(v)
                    if new != v:
                        setattr(req, attr, new)
                        n += 1
            for part in list(getattr(req, "extra_user_content_parts", None) or []):
                if id(part) in skip:
                    continue
                t = getattr(part, "text", None)
                if isinstance(t, str) and t:
                    new = neutral_marks(t)
                    if new != t:
                        part.text = new
                        n += 1
            if n:
                logger.info(
                    f"{TAG} 有人在消息里打了「[看图看懂]」「[图片：…]」这类标记：标成「他打的字」（防伪造）"
                )
        except Exception as e:
            logger.debug(f"{TAG} 防伪造那一步出错（不影响看图）：{e!r}")

    @filter.on_llm_request(priority=LATE_PRIORITY)
    async def on_llm_req_late(self, event: AstrMessageEvent, req: ProviderRequest):
        """回话请求的最后一步：防伪造（v0.2.1；AstrBot 的群聊上下文在默认优先级 0 才加进来，所以放到最后）"""
        if not self._c("enable", True):
            return
        self._neutralize(req, set(event.get_extra("fatfish_seememe_parts") or []))

    @filter.on_llm_request(priority=50)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        before = {id(p) for p in (getattr(req, "extra_user_content_parts", None) or [])}
        try:
            await self._on_llm_req(event, req)
        finally:
            mine = [
                id(p)
                for p in (getattr(req, "extra_user_content_parts", None) or [])
                if id(p) not in before
            ]
            if (
                mine
            ):  # 本插件自己加的段落（看到了什么 / 这一轮的图）：最后防伪造那一步不动
                event.set_extra("fatfish_seememe_parts", mine)

    async def _on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._c("enable", True):
            return
        items = self._relevant(event)
        fwds = self._forwards(event, items) if self._c("forward_images", True) else []
        if any(src in ("here", "quoted") for _r, src, _w in fwds):
            # 他引用的是一段转发：「这图啥意思」说的就是转发里的图，别再去拿群里最近那张
            items = [(im, src) for im, src in items if src != "refer"]
        if not items and not fwds:
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
        fwds = [
            (ref, src, who)
            for ref, src, who in fwds
            if not (remember and "fw:" + ref["id"] in given and src == "recent")
        ]
        if not items and not fwds:
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
        # 主模型自己能看图：这条消息的图 / 引用的图（含引用的转发里的图）AstrBot 会直接给它看；
        # 要不要另外再看一遍（默认看：认梗、认自己、看坑更准）
        main_sees = await self._main_sees(umo)
        skip_seen = main_sees and not self._c("also_when_main_sees", True)
        look = [
            (im, src)
            for im, src in items
            if not (skip_seen and src in ("here", "quoted"))
        ]
        ask = self._text(event)
        place = self._place(event)
        where = self._where(event)
        tasks = {
            im["key"]: self._spawn(
                self._caption(
                    im,
                    self._ctx(umo, im, True, ask=ask, place=place),
                    umo,
                    sid,
                    where=where,
                )
            )
            for im, _src in look
        }
        # 转发的聊天记录：拿到里面的消息 → 最多看 forward_max 张、一起看（和上面的图同时看）
        loaded = []
        if fwds:
            got = await asyncio.gather(
                *[self._forward_rows(event, ref) for ref, _s, _w in fwds],
                return_exceptions=True,
            )
            for (ref, src, who), g in zip(fwds, got):
                if not isinstance(g, Exception) and g[1]:
                    loaded.append((ref, src, who, *g))
        fwd_tasks, budget = {}, max(0, self._c("forward_max", 6))
        for ref, src, who, rows, imgs, _total in loaded:
            if skip_seen and src == "quoted":
                continue
            for fi in imgs:
                if budget <= 0:
                    break
                if not fi["ref"]:
                    continue
                fi["key"] = self._key(fi["ref"], fi.get("file"))
                if fi["key"] in tasks or fi["key"] in fwd_tasks:
                    continue
                budget -= 1
                r = rows[fi["row"]]
                im = {
                    "ref": fi["ref"],
                    "alt": "",
                    "key": fi["key"],
                    "sender": r["who"],
                    "uid": "",
                    "t": now,
                    "quoted": False,
                    "kind": fi.get("kind") or "",
                    "said": "".join(
                        p for p in r["parts"] if isinstance(p, str)
                    ).strip(),  # 那条消息里的字（不带「[图片]」）
                }
                ctx = {
                    "sender": r["who"],
                    "to_bot": True,
                    "chat": [
                        f"{x['who']}: {row_text(x, imgs)}"
                        for x in rows[max(0, fi["row"] - 4) : fi["row"]]
                    ],
                    "said": im["said"],
                    "ask": ask,
                    "place": place,
                    "via": "转发的聊天记录",
                }
                fwd_tasks[fi["key"]] = self._spawn(
                    self._caption(im, ctx, umo, sid, where=where)
                )
        caps, fcaps = await asyncio.gather(
            self._collect(tasks, self._c("wait_sec", 30)),
            self._collect(fwd_tasks, self._c("forward_wait_sec", 25), "转发里的图"),
        )
        late = {k for k, t in tasks.items() if not t.done()}  # 超时、还在看的
        fcaps = {**caps, **fcaps}
        keep_items, temp_items, keep_fw, temp_fw, fw_caps = [], [], [], [], []
        for im, src in items:
            # v0.3.0 按位置配：一条消息里几张图写「3 张图里第 2 张」；看了没看成的也占着位置（写「没看成」，只给这一轮）
            # 以前没看成的那张直接不写：第一张没看成，第二张的描述就被当成了第一张的
            label = slot_label(im.get("of"), im.get("poss"))
            cap = caps.get(im["key"])
            if not cap:
                if im["key"] in tasks:
                    miss = LATE if im["key"] in late else MISS
                    temp_items.append((im.get("sender"), miss, src == "quoted", label))
                continue
            row = (im.get("sender"), self._with_pun(cap), src == "quoted", label)
            if remember and im["key"] not in given:
                keep_items.append(row)
                given[im["key"]] = now
            else:
                temp_items.append(row)
        for ref, src, who, rows, imgs, total in loaded:
            got = {
                j: self._with_pun(fcaps[fi["key"]])
                for j, fi in enumerate(imgs)
                if fi.get("key") in fcaps
            }
            if not got:  # 一张都没看成：这段转发什么都不加（AstrBot 怎么给的就怎么样）
                continue
            fw_caps += list(got.values())
            self._today()["fwd"] += len(got)
            text = forward_lines(
                who, rows, imgs, got, quoted=src == "quoted", total=total
            )
            if remember and "fw:" + ref["id"] not in given:
                keep_fw.append(text)
                given["fw:" + ref["id"]] = now
            else:
                temp_fw.append(text)
        all_caps = list(caps.values()) + fw_caps
        # 一张都没看成、对话模型自己也看不到这些图（它看不了图 / 图不在这条消息里）：别给提示，不然它以为自己看到了
        if not all_caps and not (
            main_sees
            and (
                any(src in ("here", "quoted") for _im, src in items)
                or any(src == "quoted" for _r, src, *_x in loaded)
            )
        ):
            return
        if not all_caps:  # 一张都没看懂（对话模型自己看图）：「没看成」那几行也不写
            temp_items = [r for r in temp_items if r[1] not in (MISS, LATE)]
        if keep_items or keep_fw:
            req.extra_user_content_parts.append(
                _part(caption_block(keep_items, keep_fw), temp=False)
            )
        tail = caption_block(temp_items, temp_fw)
        if self._c("hints", True):
            tail = (tail + "\n\n" if tail else "") + hints_block(
                all_caps, blind=not main_sees
            )
        if tail:
            req.extra_user_content_parts.append(_part(tail, temp=True))
        self._today()["turns"] += 1
        self._save()
        logger.info(
            f"{TAG} 这一轮带上了 {len(caps)} 张图的看懂结果"
            + (f"、转发的聊天记录里 {len(fw_caps)} 张" if loaded else "")
            + ("（主模型自己也看得到图）" if main_sees else "")
        )

    async def _collect(self, tasks, timeout, what="看图"):
        """等这些图看完（最多 timeout 秒）→ {凭据: 看懂的}；没看完的接着看（看完存着 下次直接用）"""
        if not tasks:
            return {}
        done, pending = await asyncio.wait(list(tasks.values()), timeout=timeout)
        if pending:
            self._warn_once(
                "timeout" + what,
                f"{what}超过 {timeout} 秒 这一轮先不带没看完的（{len(pending)} 张）",
                60,
            )
        return {
            k: t.result()
            for k, t in tasks.items()
            if t in done and not t.cancelled() and t.exception() is None and t.result()
        }

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
        """→ (本地图片路径, 是不是动图拼帧, 用完要删的文件)；拿不到 / 不是图片 → (None, False, [])
        v0.3.0：确认是图片（文件头对得上、Pillow 打得开）才交出去 —— 外面拿到了才扣名额、才调看图模型。
        以前 AstrBot 认不出是图时，插件会自己再下一遍、不管下到的是什么都当成图（过期链接给的网页也算一张，名额照扣）；
        图太大 AstrBot 不让交给模型时，也会自己再下一遍绕过去"""
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
                # 太大（ImageInputTooLargeError）：AstrBot 不让交给模型，别自己再下一遍绕过去
                if type(e).__name__ == "ImageInputTooLargeError":
                    self._warn_once(
                        "big", "图太大（AstrBot 不让交给模型）这张不看", 600
                    )
                    return None, False, []
                self._warn_once(
                    "prep", f"准备图片失败（AstrBot 的处理）：{e!r}"[:200], 60
                )
        # AstrBot 没拿到（老版本没有 prepare_model_image / 网络抖了）：自己下一遍，下到的得是图片
        if str(ref).startswith("http"):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as s:
                    async with s.get(ref, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status != 200:
                            return None, False, []
                        data = await r.content.read(_MAX_BYTES + 1)
                if len(data) > _MAX_BYTES or not _is_image(data):
                    return None, False, []  # 太大 / 不是图片（网页、提示页）：不看
                fd, name = tempfile.mkstemp(
                    prefix="img_", suffix=_ext(data), dir=self.tmp_dir
                )
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                return name, False, [name]
            except Exception:
                return None, False, []
        p = str(ref)[7:] if str(ref).startswith("file://") else str(ref)
        if os.path.isfile(p) and _is_image(path=p):
            return p, False, []
        return None, False, []

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
        """看一张图 → 看懂的那句；看不成 → None。
        先查表情包词典（认好的直接用）→ 再查看过的 → 都没有才真的看（该深度认的这次就深度认）"""
        k = im["key"]
        sd = self._sd_on() and not fresh
        if sd:
            hit = self.stickers.lookup(k)
            if hit:
                self._today()["dict"] += 1
                return hit
        deep = sd and self.stickers.due(k)
        fallback = None
        if not fresh:
            hit = self.cache.get(k)
            if hit and not deep:
                self._today()["cached"] += 1
                return hit
            fallback = hit  # 该深度认、但这次认不成（没模型 / 到上限 / 出错）：先用以前看过的那句
            if k in self.inflight:
                return await asyncio.shield(self.inflight[k])
        fut = asyncio.get_running_loop().create_future()
        if not fresh:
            self.inflight[k] = fut
        cap = None
        try:
            cap = await self._look(
                im, ctx, umo, sid, background, fresh, where, info, deep
            )
            if cap is None:
                cap = fallback
            return cap
        finally:
            if self.inflight.get(k) is fut:
                self.inflight.pop(k, None)
            if not fut.done():
                fut.set_result(cap)

    async def _look(self, im, ctx, umo, sid, background, fresh, where, info, deep):
        k = im["key"]
        st, now = self._today(), time.time()
        path, junk = None, []
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
            if not path:  # v0.3.0：下不来 / 不是图片 —— 名额没扣、也没调看图模型
                self._err(f"图拿不到或者不是图片：{str(im['ref'])[:80]}")
                if info is not None:
                    info["error"] = "图拿不到或者不是图片"
                return None
            md5 = "m:" + hashlib.md5(Path(path).read_bytes()).hexdigest()
            if self._sd_on() and not fresh:
                hit = self.stickers.lookup(
                    md5
                )  # 内容一样、换了个文件名 / 网址的：词典里认好过就直接用
                if hit:
                    self.stickers.link(k, md5)
                    st["dict"] += 1
                    return hit
            if not fresh and not deep:
                hit = self.cache.get(md5)
                if hit:
                    self.cache.put([k], hit)
                    st["cached"] += 1
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
                deep=deep,
                usage=self.stickers.usage(k) if deep else (),
            )
            if info is not None:
                info.update(prompt=prompt, model=pid, avatar=bool(ref))
            self.hour.append(now)
            st["n"] += 1
            if deep:
                st["deep"] += 1
                self.stickers.started(now)
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
            entry = None
            if deep:
                cap, entry = self._deep_cap(text, im, montage)
            else:
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
            if entry:
                entry = dict(entry, cap=cap, t=int(time.time()))
                self.stickers.put_deep(k, entry, also=[md5])
                if not self.stickers.usable({"deep": entry}):
                    logger.info(
                        f"{TAG} [表情包词典] 把握不够（{entry.get('conf')}），这次照用、不拿来当词条"
                    )
            if not fresh:
                self.cache.put([k, md5], cap)
            self.recent_caps.append(
                {
                    "t": int(time.time()),
                    "who": im.get("sender") or "",
                    "cap": cap,
                    "bg": bool(background),
                    "deep": bool(deep),
                    "where": where,
                    "model": pid,
                    "tokens": used,
                }
            )
            self._save(
                force=True, sd=bool(entry)
            )  # 看一张图要花钱：看完马上存（AstrBot 被直接关掉也不丢）
            logger.info(f"{TAG} {'[表情包词典] ' if deep else ''}{cap[:120]}")
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
            for j in junk:
                Path(j).unlink(missing_ok=True)

    def _deep_cap(self, text, im, montage):
        """深度认的回复 → (给机器人看的那句, 词条)；模型拒绝了 → (它说的, None) 留给外面判；拆不出 → ("", None)"""
        if refused(trim(text)):
            return trim(text), None
        entry = parse_deep(text)
        if not entry:
            return "", None
        cap = trim(
            deep_caption(entry, kind=im.get("kind") or "", anim=montage),
            self._c("max_chars", 180) + 140,
        )
        return cap, entry

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
            "stickers": {
                "on": self._sd_on(),
                **self.stickers.count(),
                "list": self.stickers.listing(10),
            },
            "puns": {  # v0.3.0
                "on": bool(self._c("puns", True)),
                "sound": puns.ready(),
                "how": puns.how(),
            },
            "tz": self.tz_label,
            "version": VERSION,
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
                    "forward_images",
                    "forward_max",
                    "forward_wait_sec",
                    "sticker_dict",
                    "sticker_after",
                    "sticker_daily",
                    "puns",
                    "pun_words",
                    "day_tz",
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
                    "block": block(
                        [("面板测试", self._with_pun(cap), False)],
                        self._c("hints", True),
                    ),
                    "prompt": info.get("prompt", ""),
                    "model": info.get("model", ""),
                    "tokens": info.get("tokens", 0),
                    "avatar": info.get("avatar", False),
                    "state": await self.snapshot(),
                },
            }
        finally:
            Path(name).unlink(missing_ok=True)
