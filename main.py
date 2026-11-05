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
from astrbot.api.message_components import Image as CompImage, Plain, Reply
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
        self.command_name = str(cmd_cfg.get("command_name") or "睦睦搜图").strip()
        raw_aliases = str(cmd_cfg.get("command_aliases") or "搜图,找coser,查出处,睦睦找鸡,黄瓜搜图,黄瓜找鸡").strip()
        self.aliases = [a.strip() for a in re.split(r"[,，\s]+", raw_aliases) if a.strip()]
        self.enable_group = bool(cmd_cfg.get("enable_group", True))
        self.enable_private = bool(cmd_cfg.get("enable_private", True))
        self.cooldown_sec = max(0, int(cmd_cfg.get("cooldown_sec", 30) or 0))

        # 2. 交互与回复配置
        interactive_cfg = self.config.get("interactive") or {}
        self.enable_start_hint = bool(interactive_cfg.get("enable_start_hint", True))
        self.start_hint_text = str(
            interactive_cfg.get("start_hint_text") or "🔍 收到图片，正在调用多引擎反查出处..."
        ).strip()
        self.enable_persona_reply = bool(interactive_cfg.get("enable_persona_reply", False))
        self.enable_conversation_history = bool(interactive_cfg.get("enable_conversation_history", True))

        # 3. 搜索与引擎配置
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

        # 4. 表情包配置
        meme_cfg = self.config.get("meme") or {}
        self.enable_found_image = bool(meme_cfg.get("enable_found_image", True))
        self.found_image = str(meme_cfg.get("found_image") or "").strip()
        self.enable_miss_image = bool(meme_cfg.get("enable_miss_image", False))
        self.miss_image = str(meme_cfg.get("miss_image") or "").strip()
        self.meme_max_size = max(0, int(meme_cfg.get("meme_max_size", 240) or 0))

        # 5. 环境配置
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

    def _match_trigger_command(self, raw_text: str) -> Optional[str]:
        """检查文本是否匹配主指令或别名，匹配成功返回具体的触发词"""
        text = raw_text.strip()
        if not text:
            return None

        clean_text = re.sub(r"^[/／#!！.。]+", "", text).strip()
        first_token = clean_text.split()[0] if clean_text else ""

        all_cmds = [self.command_name] + self.aliases
        for target in all_cmds:
            t_clean = re.sub(r"^[/／#!！.。]+", "", target).strip()
            if first_token == t_clean or clean_text == t_clean:
                return t_clean
        return None

    async def _extract_image_url(self, event: AstrMessageEvent) -> Optional[str]:
        """从当前消息或被引用的消息中提取图片 URL 或路径"""
        message_chain = getattr(event.message_obj, "message", []) or []

        # 1. 优先检查当前发送的消息中是否附带图片
        for comp in message_chain:
            if isinstance(comp, CompImage) or comp.__class__.__name__ == "Image":
                url = getattr(comp, "url", None) or getattr(comp, "file", None)
                if url:
                    return str(url)

        # 2. 检查是否有引用消息（Reply），优先从 AstrBot 已经解析好的 Reply.chain 中极速提取（0 延迟）
        reply_id = None
        for comp in message_chain:
            if isinstance(comp, Reply) or comp.__class__.__name__ == "Reply":
                reply_chain = getattr(comp, "chain", []) or []
                for sub_comp in reply_chain:
                    if isinstance(sub_comp, CompImage) or sub_comp.__class__.__name__ == "Image":
                        url = getattr(sub_comp, "url", None) or getattr(sub_comp, "file", None)
                        if url:
                            return str(url)
                c_id = getattr(comp, "id", None)
                if c_id:
                    reply_id = c_id
                    break

        if not reply_id:
            reply_obj = getattr(event.message_obj, "reply", None)
            if reply_obj:
                reply_chain = getattr(reply_obj, "chain", []) or []
                for sub_comp in reply_chain:
                    if isinstance(sub_comp, CompImage) or sub_comp.__class__.__name__ == "Image":
                        url = getattr(sub_comp, "url", None) or getattr(sub_comp, "file", None)
                        if url:
                            return str(url)
                if hasattr(reply_obj, "id"):
                    reply_id = reply_obj.id

        if not reply_id:
            return None

        # 3. 兜底路径：若 chain 为空，再调用 bot API (get_msg) 向 NapCat/OneBot 拉取
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

            # CQ 码格式
            elif isinstance(msg_data, str):
                m = re.search(r"\[CQ:image,[^\]]*url=([^,\]]+)", msg_data)
                if m:
                    return m.group(1)
                m2 = re.search(r"\[CQ:image,[^\]]*file=([^,\]]+)", msg_data)
                if m2 and m2.group(1).startswith("http"):
                    return m2.group(1)

        except Exception as e:
            logger.warning(f"[{self.command_name}] 尝试拉取被引用消息 ({reply_id}) 失败: {e}")

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
            logger.error(f"[{self.command_name}] 下载图片异常: {e}")
            return None

    async def _prepare_meme_component(self, path_or_url: str) -> Optional[Any]:
        """根据配置处理表情包：支持等比例缩放，并返回 CompImage 组件"""
        if not path_or_url:
            return None

        local_path = None
        if path_or_url.startswith(("http://", "https://")):
            # 如果不缩放，直接使用网络 URL 发送
            if self.meme_max_size <= 0:
                return CompImage.fromURL(path_or_url)
            # 若配置了缩放，先下载到本地临时文件进行处理
            local_path = await self._download_image_to_temp(path_or_url)
        elif os.path.exists(path_or_url):
            local_path = path_or_url

        if not local_path or not os.path.exists(local_path):
            return None

        # 不需要缩放
        if self.meme_max_size <= 0:
            return CompImage.fromFileSystem(local_path)

        # 执行 PIL 高质量等比例缩放
        try:
            from PIL import Image as PILImage

            with PILImage.open(local_path) as im:
                w, h = im.size
                if max(w, h) <= self.meme_max_size:
                    return CompImage.fromFileSystem(local_path)

                cache_dir = PLUGIN_DIR / "temp_meme"
                cache_dir.mkdir(parents=True, exist_ok=True)
                mtime = int(os.path.getmtime(local_path))
                file_hash = abs(hash(f"{local_path}:{mtime}:{self.meme_max_size}"))
                ext = Path(local_path).suffix or ".jpg"
                scaled_path = cache_dir / f"meme_{file_hash}_{self.meme_max_size}{ext}"

                if not scaled_path.exists():
                    im_copy = im.copy()
                    im_copy.thumbnail((self.meme_max_size, self.meme_max_size), PILImage.Resampling.LANCZOS)
                    if ext.lower() in [".jpg", ".jpeg"] and im_copy.mode in ("RGBA", "P"):
                        im_copy = im_copy.convert("RGB")
                    im_copy.save(str(scaled_path), quality=92)

                return CompImage.fromFileSystem(str(scaled_path))
        except Exception as e:
            logger.warning(f"[{self.command_name}] 表情包缩放处理失败，回退使用原图: {e}")
            return CompImage.fromFileSystem(local_path)

    async def _llm_persona_reply(
        self, event: AstrMessageEvent, search_result: str, triggered_cmd: str = "搜图"
    ) -> Optional[str]:
        """使用当前会话的模型与人格润色搜图结果"""
        try:
            umo = event.unified_msg_origin
            provider = None
            if hasattr(self.context, "get_using_provider_async"):
                provider = await self.context.get_using_provider_async(umo)
            if not provider:
                provider = self.context.get_using_provider(umo)
            if not provider:
                return None

            persona_prompt = ""
            conv_mgr = getattr(self.context, "conversation_manager", None)
            curr_cid = None
            if conv_mgr:
                curr_cid = await conv_mgr.get_curr_conversation_id(umo)
                if curr_cid:
                    conv = await conv_mgr.get_conversation(umo, curr_cid)
                    if conv and hasattr(self.context, "persona_manager"):
                        persona = await self.context.persona_manager.get_persona(conv.persona_id)
                        if persona:
                            persona_prompt = getattr(persona, "system_prompt", "") or getattr(persona, "prompt", "")

            user_prompt = (
                f"【系统信息】：用户发送了一张图片请求搜图/找出处（使用的指令为：{triggered_cmd}），多引擎反查结果如下：\n"
                f"{search_result}\n\n"
                f"【任务要求】：请完全代入你当前的人格人设、性格特征和语言风格，把上面的搜图结果告知用户。\n"
                f"要求：\n"
                f"1. 如果查到了推特/微博等账号，请务必保留真实的名字与链接，不要遗漏关键链接，也不要编造不存在的信息。\n"
                f"2. 如果未找到，请用符合性格的语气委婉告知用户。\n"
                f"3. 保持自然、生动，具有角色温度。"
            )

            resp = await provider.text_chat(
                prompt=user_prompt,
                system_prompt=persona_prompt or None,
                session_id=curr_cid or "cucumber_search",
                persist=False,
            )
            if resp and getattr(resp, "completion_text", None):
                return resp.completion_text.strip()
        except Exception as e:
            logger.warning(f"[黄瓜搜图] 人格包装生成失败，降级使用原生排版: {e}")
        return None

    async def _record_conversation(
        self, event: AstrMessageEvent, user_text: str, reply_text: str, triggered_cmd: str = "搜图"
    ):
        """将搜图结果写入当前 LLM 会话上下文，供后续对话参考"""
        try:
            manager = getattr(self.context, "conversation_manager", None)
            if manager is None:
                return
            conversation_id = await manager.get_curr_conversation_id(event.unified_msg_origin)
            if not conversation_id:
                conversation_id = await manager.new_conversation(
                    event.unified_msg_origin, platform_id=event.get_platform_id()
                )
            await manager.add_message_pair(
                conversation_id,
                {"role": "user", "content": user_text or f"[发送了一张图片请求{triggered_cmd}]"},
                {"role": "assistant", "content": f"[{triggered_cmd}结果]\n{reply_text}"},
            )
            logger.info(f"[{triggered_cmd}] 已将搜图结果记录至 LLM 对话上下文")
        except Exception as e:
            logger.warning(f"[{triggered_cmd}] 写入会话上下文失败: {e}")

    @filter.event_message_type(EventMessageType.ALL, priority=1000)
    async def on_message(self, event: AstrMessageEvent):
        """统一监听消息并处理指令"""
        is_group = bool(event.get_group_id())
        if is_group and not self.enable_group:
            return
        if not is_group and not self.enable_private:
            return

        text = (event.message_str or "").strip()
        triggered_cmd = self._match_trigger_command(text)
        if not triggered_cmd:
            return

        # 冷却时间检查
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")
        user_key = f"{group_id}:{sender_id}" if group_id else sender_id
        now = time.time()
        last_time = self._user_cooldown.get(user_key, 0)
        if self.cooldown_sec > 0 and now - last_time < self.cooldown_sec:
            remain = int(self.cooldown_sec - (now - last_time))
            await event.send(event.plain_result(f"⏱️ 搜图冷却中，请 {remain} 秒后再试~"))
            event.stop_event()
            return

        # 提取图片
        image_url = await self._extract_image_url(event)
        if not image_url:
            await event.send(
                event.plain_result(f"💡 请【引用一张带有图片的消息】或【发送图片时附带文字】再发送 /{triggered_cmd}")
            )
            event.stop_event()
            return

        # 更新冷却时间
        self._user_cooldown[user_key] = now

        # 前置提示语（使用 await event.send 主动推送，不依赖 generator）
        if self.enable_start_hint and self.start_hint_text:
            await event.send(event.plain_result(self.start_hint_text))

        # 探测 Node.js 环境
        node_bin = self._detect_node()
        if not node_bin:
            await event.send(
                event.plain_result("❌ 未检测到可用的 Node.js (>=20) 环境！请在插件设置中指定 node_path。")
            )
            event.stop_event()
            return

        browser_bin = self._detect_browser()

        # 下载图片到临时文件
        temp_img_path = await self._download_image_to_temp(image_url)
        if not temp_img_path:
            await event.send(event.plain_result("❌ 获取图片失败，请检查图片是否有效或稍后再试。"))
            event.stop_event()
            return

        try:
            # 准备引擎参数
            engines = []
            if self.enable_lens:
                if browser_bin:
                    engines.append("lens")
                else:
                    logger.warning(f"[{triggered_cmd}] 未找到 Chromium/Edge 浏览器，跳过 Google Lens 引擎")
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
                logger.error(f"[{triggered_cmd}] Node 引擎退出异常 (code {proc.returncode}): {err_msg}")
                await event.send(event.plain_result(f"❌ 搜图服务执行异常：{err_msg[:120] if err_msg else '未知错误'}"))
                return

            out_text = stdout.decode("utf-8", errors="ignore").strip()
            try:
                res_data = json.loads(out_text)
            except Exception:
                await event.send(event.plain_result(out_text or "未找到相关结果。"))
                return

            accounts = res_data.get("accounts") or []
            raw_reply_text = res_data.get("text") or f"🎯 未检索到相关出处。"

            # 动态替换标题为当前触发的指令名称（如：睦睦找鸡 -> 🎯 **睦睦找鸡结果**）
            cmd_title = triggered_cmd[:-2] if triggered_cmd.endswith("结果") and len(triggered_cmd) > 2 else triggered_cmd
            raw_reply_text = re.sub(r"🎯 \*\*[^\*]+结果\*\*", f"🎯 **{cmd_title}结果**", raw_reply_text)
            raw_reply_text = raw_reply_text.replace("黄瓜搜图结果", f"{cmd_title}结果")

            # 1. 人格回复处理
            final_reply_text = raw_reply_text
            if self.enable_persona_reply:
                persona_text = await self._llm_persona_reply(event, raw_reply_text, triggered_cmd)
                if persona_text:
                    final_reply_text = persona_text

            # 2. 合并单条消息发送：表情包（在前）+ 搜图结果文本（在后）
            msg_chain: list[Any] = []

            # 命中且配置了表情包（支持等比例缩放防霸屏）
            if accounts and (self.enable_found_image or self.found_image) and self.found_image:
                meme_comp = await self._prepare_meme_component(self.found_image)
                if meme_comp:
                    msg_chain.append(meme_comp)

            # 未命中且配置了未命中表情包（支持等比例缩放防霸屏）
            elif not accounts and (self.enable_miss_image or self.miss_image) and self.miss_image:
                meme_comp = await self._prepare_meme_component(self.miss_image)
                if meme_comp:
                    msg_chain.append(meme_comp)

            # 拼接文本结果
            msg_chain.append(Plain(final_reply_text))

            # 一条消息合并发送（图片 + 文本）
            await event.send(event.chain_result(msg_chain))

            # 3. 写入会话上下文（供后续追问）
            if self.enable_conversation_history:
                await self._record_conversation(event, text, final_reply_text, triggered_cmd)

        except asyncio.TimeoutError:
            await event.send(event.plain_result("⏱️ 搜图请求超时，可能是代理未连接或目标图库正在维护。"))
        except Exception as e:
            logger.error(f"[{triggered_cmd}] 运行异常: {e}", exc_info=True)
            await event.send(event.plain_result(f"❌ 搜图处理失败: {e}"))
        finally:
            if temp_img_path and os.path.exists(temp_img_path) and "cucumber_search_" in temp_img_path:
                try:
                    os.remove(temp_img_path)
                except Exception:
                    pass
            # 最终阻断普通 LLM 对话，避免搜完图后又触发常规 AI 对话
            event.stop_event()
