from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as CompImage, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.event_message_type import EventMessageType

PLUGIN_DIR = Path(__file__).resolve().parent
ENGINE_DIR = PLUGIN_DIR / "engine"
CUCUMBER_MJS = ENGINE_DIR / "bin" / "cucumber.mjs"


@register(
    "astrbot_plugin_cucumber_search",
    "AkinoSa",
    "黄瓜搜图：多引擎反查 Coser 社媒账号与二次元图源（Google Lens / SauceNAO / trace.moe / IQDB）",
    "1.0.0",
    "https://github.com/AkinoSa/astrbot_plugin_cucumber_search",
)
class CucumberSearchPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 1. 指令配置
        cmd_cfg = self.config.get("command") or {}
        self.command_name = str(cmd_cfg.get("command_name") or "黄瓜搜图").strip()
        raw_aliases = str(cmd_cfg.get("command_aliases") or "搜图,找coser,查出处,黄瓜找鸡").strip()
        self.aliases = [a.strip() for a in re.split(r"[,，\s]+", raw_aliases) if a.strip()]
        self.enable_group = bool(cmd_cfg.get("enable_group", True))
        self.enable_private = bool(cmd_cfg.get("enable_private", True))
        self.cooldown_sec = max(0, int(cmd_cfg.get("cooldown_sec", 30) or 0))

        # 2. 搜索与引擎配置
        search_cfg = self.config.get("search") or {}
        self.enable_lens = bool(search_cfg.get("enable_lens", True))
        self.enable_saucenao = bool(search_cfg.get("enable_saucenao", True))
        self.enable_tracemoe = bool(search_cfg.get("enable_tracemoe", True))
        self.enable_iqdb = bool(search_cfg.get("enable_iqdb", False))
        self.enable_baidu = bool(search_cfg.get("enable_baidu", False))
        self.proxy = str(search_cfg.get("proxy") or "").strip()
        self.timeout_sec = max(10, int(search_cfg.get("timeout_sec", 45) or 45))
        self.max_accounts = max(1, int(search_cfg.get("max_accounts", 3) or 3))
        self.strict_consensus = bool(search_cfg.get("strict_consensus", True))

        # 3. 表情包配置
        meme_cfg = self.config.get("meme") or {}
        self.enable_found_image = bool(meme_cfg.get("enable_found_image", False))
        self.found_image = str(meme_cfg.get("found_image") or "").strip()
        self.enable_miss_image = bool(meme_cfg.get("enable_miss_image", False))
        self.miss_image = str(meme_cfg.get("miss_image") or "").strip()

        # 4. 环境配置
        env_cfg = self.config.get("env") or {}
        self.custom_node_path = str(env_cfg.get("node_path") or "").strip()
        self.custom_browser_path = str(env_cfg.get("browser_path") or "").strip()

        # 用户冷却记录：{user_key: timestamp}
        self._user_cooldown: Dict[str, float] = {}

    def _detect_node(self) -> Optional[str]:
        """探测可用的 Node.js (>=20) 路径"""
        if self.custom_node_path and os.path.exists(self.custom_node_path):
            return self.custom_node_path

        which_node = shutil.which("node")
        if which_node:
            return which_node

        candidates = [
            "/vol1/@appcenter/nodejs_v22/bin/node",
            "/usr/local/bin/node",
            "/usr/bin/node",
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Program Files (x86)\nodejs\node.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _detect_browser(self) -> Optional[str]:
        """探测可用的 Chromium / Edge / Chrome 浏览器路径"""
        if self.custom_browser_path and os.path.exists(self.custom_browser_path):
            return self.custom_browser_path

        candidates = [
            "/vol1/@appcenter/fygo-browser/app/vendor/runtime/usr/lib/chromium/chromium",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/local/bin/chromium",
            "/usr/bin/msedge",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

        for name in ("chromium", "google-chrome", "chromium-browser", "msedge"):
            w = shutil.which(name)
            if w:
                return w
        return None

    def _is_trigger_command(self, raw_text: str) -> bool:
        """检查文本是否匹配主指令或别名"""
        text = raw_text.strip()
        if not text:
            return False

        # 去除开头的常用指令前缀（/、#、!、。）
        clean_text = re.sub(r"^[/／#!！.。]+", "", text).strip()
        first_token = clean_text.split()[0] if clean_text else ""

        all_cmds = [self.command_name] + self.aliases
        for target in all_cmds:
            t_clean = re.sub(r"^[/／#!！.。]+", "", target).strip()
            if first_token == t_clean or clean_text == t_clean:
                return True
        return False

    async def _extract_image_url(self, event: AstrMessageEvent) -> Optional[str]:
        """从当前消息或被引用的消息中提取图片 URL 或路径"""
        message_chain = getattr(event.message_obj, "message", []) or []

        # 1. 优先检查当前发送的消息中是否附带图片
        for comp in message_chain:
            if isinstance(comp, CompImage) or comp.__class__.__name__ == "Image":
                url = getattr(comp, "url", None) or getattr(comp, "file", None)
                if url:
                    return str(url)

        # 2. 检查是否有引用消息（Reply）
        reply_id = None
        for comp in message_chain:
            if comp.__class__.__name__ == "Reply" or hasattr(comp, "id"):
                c_id = getattr(comp, "id", None)
                if c_id:
                    reply_id = c_id
                    break

        if not reply_id:
            # 兼容某些平台在 raw_message 或 reply 字段
            reply_obj = getattr(event.message_obj, "reply", None)
            if reply_obj and hasattr(reply_obj, "id"):
                reply_id = reply_obj.id

        if not reply_id:
            return None

        # 3. 调用 bot API 拉取被引用的历史消息
        bot = getattr(event, "bot", None)
        if not bot:
            return None

        try:
            resp = None
            if hasattr(bot, "call_action"):
                resp = await bot.call_action("get_msg", message_id=int(reply_id))
            elif hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                resp = await bot.api.call_action("get_msg", message_id=int(reply_id))

            if not resp:
                return None

            payload = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp
            if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                payload = payload["result"]

            msg_data = payload.get("message") or payload.get("raw_message") if isinstance(payload, dict) else None

            # OneBot 数组格式
            if isinstance(msg_data, list):
                for seg in msg_data:
                    if isinstance(seg, dict) and seg.get("type") == "image":
                        d = seg.get("data", {})
                        u = d.get("url") or d.get("file")
                        if u:
                            return str(u)

            # CQ 码字符串格式
            elif isinstance(msg_data, str):
                m = re.search(r"\[CQ:image,[^\]]*url=([^,\]]+)", msg_data)
                if m:
                    return m.group(1)
                m2 = re.search(r"\[CQ:image,[^\]]*file=([^,\]]+)", msg_data)
                if m2 and m2.group(1).startswith("http"):
                    return m2.group(1)

        except Exception as e:
            logger.warning(f"[黄瓜搜图] 尝试拉取被引用消息 ({reply_id}) 失败: {e}")

        return None

    async def _download_image_to_temp(self, url_or_path: str) -> Optional[str]:
        """将网络图片下载到本地临时文件以供 Node 引擎读取"""
        if not url_or_path:
            return None

        if os.path.exists(url_or_path):
            return url_or_path

        if not url_or_path.startswith(("http://", "https://")):
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url_or_path, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[黄瓜搜图] 图片下载失败 HTTP {resp.status}: {url_or_path}")
                        return None
                    content = await resp.read()

            ext = ".jpg"
            if content.startswith(b"\x89PNG"):
                ext = ".png"
            elif content.startswith(b"GIF"):
                ext = ".gif"
            elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
                ext = ".webp"

            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="cucumber_search_")
            tmp_file.write(content)
            tmp_file.close()
            return tmp_file.name
        except Exception as e:
            logger.error(f"[黄瓜搜图] 下载图片异常: {e}")
            return None

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """统一监听消息并处理指令"""
        is_group = bool(event.get_group_id())
        if is_group and not self.enable_group:
            return
        if not is_group and not self.enable_private:
            return

        text = (event.message_str or "").strip()
        if not self._is_trigger_command(text):
            return

        # 匹配到指令，立即阻断后续 LLM 响应，避免大模型把识图指令当做聊天
        event.stop_event()

        # 冷却时间检查
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")
        user_key = f"{group_id}:{sender_id}" if group_id else sender_id
        now = time.time()
        last_time = self._user_cooldown.get(user_key, 0)
        if self.cooldown_sec > 0 and now - last_time < self.cooldown_sec:
            remain = int(self.cooldown_sec - (now - last_time))
            yield event.plain_result(f"⏱️ 搜图冷却中，请 {remain} 秒后再试~")
            return

        # 提取图片
        image_url = await self._extract_image_url(event)
        if not image_url:
            yield event.plain_result(f"💡 请【引用一张带有图片的消息】或【发送图片时附带文字】再发送 /{self.command_name}")
            return

        # 更新冷却时间
        self._user_cooldown[user_key] = now

        # 先提示已收到，开始搜图
        yield event.plain_result("🔍 收到图片，正在调用多引擎反查出处...")

        # 探测环境
        node_bin = self._detect_node()
        if not node_bin:
            yield event.plain_result("❌ 未检测到可用的 Node.js (>=20) 环境！请在插件设置中指定 node_path。")
            return

        browser_bin = self._detect_browser()

        # 下载图片到临时文件
        temp_img_path = await self._download_image_to_temp(image_url)
        if not temp_img_path:
            yield event.plain_result("❌ 获取图片失败，请检查图片是否有效或稍后再试。")
            return

        try:
            # 准备引擎参数
            engines = []
            if self.enable_lens:
                if browser_bin:
                    engines.append("lens")
                else:
                    logger.warning("[黄瓜搜图] 未找到 Chromium/Edge 浏览器，跳过 Google Lens 引擎")
            if self.enable_saucenao:
                engines.append("saucenao")
            if self.enable_tracemoe:
                engines.append("tracemoe")
            if self.enable_iqdb:
                engines.append("iqdb")
            if self.enable_baidu and browser_bin:
                engines.append("baidu")

            if not engines:
                engines = ["saucenao", "tracemoe"]

            cmd = [
                node_bin,
                str(CUCUMBER_MJS),
                temp_img_path,
                "--json",
                "--engines",
                ",".join(engines),
                "--timeout",
                str(self.timeout_sec),
                "--max",
                str(self.max_accounts),
            ]
            if self.proxy:
                cmd.extend(["--proxy", self.proxy])
            if browser_bin:
                cmd.extend(["--browser", browser_bin])
            if not self.strict_consensus:
                cmd.append("--no-strict")

            # 注入飞牛 Linux Chromium 所需的 LD_LIBRARY_PATH
            env = os.environ.copy()
            if browser_bin and "fygo-browser" in browser_bin:
                fygo_ld = "/vol1/@appcenter/fygo-browser/app/vendor/runtime/usr/lib/x86_64-linux-gnu:/vol1/@appcenter/fygo-browser/app/vendor/runtime/lib/x86_64-linux-gnu:/vol1/@appcenter/fygo-browser/app/vendor/runtime/usr/lib:/vol1/@appcenter/fygo-browser/app/vendor/runtime/usr/lib/chromium"
                cur_ld = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = f"{fygo_ld}:{cur_ld}" if cur_ld else fygo_ld

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            total_wait = max(60, self.timeout_sec * len(engines) + 20)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=total_wait)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="ignore").strip()
                logger.error(f"[黄瓜搜图] Node 引擎退出异常 (code {proc.returncode}): {err_msg}")
                yield event.plain_result(f"❌ 搜图服务执行异常：{err_msg[:120] if err_msg else '未知错误'}")
                return

            out_text = stdout.decode("utf-8", errors="ignore").strip()
            try:
                res_data = json.loads(out_text)
            except Exception:
                # 兼容直接输出普通文本
                yield event.plain_result(out_text or "未找到相关结果。")
                return

            accounts = res_data.get("accounts") or []
            reply_text = res_data.get("text") or "🎯 未检索到相关出处。"

            # 1. 命中账号且配置了表情包
            if accounts and (self.enable_found_image or self.found_image) and self.found_image:
                if self.found_image.startswith(("http://", "https://")):
                    yield event.image_result(self.found_image)
                elif os.path.exists(self.found_image):
                    yield event.image_result(self.found_image)

            # 2. 未命中且配置了未命中表情包
            elif not accounts and (self.enable_miss_image or self.miss_image) and self.miss_image:
                if self.miss_image.startswith(("http://", "https://")):
                    yield event.image_result(self.miss_image)
                elif os.path.exists(self.miss_image):
                    yield event.image_result(self.miss_image)

            yield event.plain_result(reply_text)

        except asyncio.TimeoutError:
            yield event.plain_result("⏱️ 搜图请求超时，可能是代理未连接或目标图库正在维护。")
        except Exception as e:
            logger.error(f"[黄瓜搜图] 运行异常: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜图处理失败: {e}")
        finally:
            if temp_img_path and os.path.exists(temp_img_path) and "cucumber_search_" in temp_img_path:
                try:
                    os.remove(temp_img_path)
                except Exception:
                    pass
