"""作息模拟插件 — MaiBot SDK v2

为 MaiBot 角色增加模拟作息功能，支持睡觉和被吵醒带有起床气。

功能特性：
    - 模拟人类作息，在设定的睡觉时间内自动进入"睡觉"状态，完全静默不回复
    - 在起床时间通过概率"贪睡"或"起床"，模拟赖床行为
    - 在睡觉时间通过概率"熬夜"或"入睡"，模拟熬夜行为
    - 被 @/戳 多次可概率吵醒，吵醒后进入"生气"状态并注入自定义语气前缀
    - 生气状态持续一定时间后自动"消气"，期间可概率"补觉"重新入睡
    - 群聊和私聊状态完全隔离，不同群聊互不影响
    - 支持黑白名单控制生效范围（群聊和私聊分开设置）
    - 状态变更时自动发送预设消息：睡醒发"早ﾉ☀"，入睡发"Zzz🌙"，被吵醒发"白金吵闹💢"
    - 戳一戳（Poke）与 @ 同等对待，累计计数可吵醒
"""

import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from maibot_sdk import HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .config import SnoozeConfig
from .models import (
    SessionState, STATE_ASLEEP, STATE_PISSED, SUB_ANGRY,
    apply_transition, TransitionType
)


class StateManager:
    """
    会话状态池管理
    每个会话存储：状态对象 + stream_id
    """
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """获取或创建会话状态对象"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "state": SessionState(),
                "stream_id": ""
            }
        return self._sessions[session_id]["state"]

    def update(self, session_id: str, state: SessionState) -> None:
        """更新会话状态"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {"state": state, "stream_id": ""}
        else:
            self._sessions[session_id]["state"] = state

    def set_stream_id(self, session_id: str, stream_id: str) -> None:
        """存储会话对应的真实 stream_id（MaiBot内部格式）"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {"state": SessionState(), "stream_id": stream_id}
        else:
            self._sessions[session_id]["stream_id"] = stream_id

    def get_stream_id(self, session_id: str) -> str:
        """获取会话对应的真实 stream_id"""
        if session_id in self._sessions:
            return self._sessions[session_id].get("stream_id", "")
        return ""

    def get_all_ids(self) -> list:
        """获取所有会话 ID"""
        return list(self._sessions.keys())


class SnoozePlugin(MaiBotPlugin):
    """打盹儿作息模拟插件"""

    config_model = SnoozeConfig

    # ---- 预设消息 ----
    MSG_WOKE_UP = "早ﾉ☀"
    MSG_FELL_ASLEEP = "Zzz🌙"
    MSG_WAS_WOKEN = "白金吵闹💢"

    def __init__(self):
        super().__init__()
        self.state_manager = StateManager()
        self._timer_task: Optional[asyncio.Task] = None

    # =========================================================
    # 生命周期
    # =========================================================

    async def on_load(self) -> None:
        """处理插件加载"""
        self.ctx.logger.info("[Snooze] 插件加载完成，开始作息调度")

        # ---- 主动初始化配置中的群聊 ----
        for gid in self.config.filter.group_ids:
            self.state_manager.get_or_create(str(gid))
            self.ctx.logger.debug(f"[Snooze] 预创建群聊会话: {gid}")

        self._timer_task = asyncio.create_task(self._timer_loop())

    async def on_unload(self) -> None:
        """处理插件卸载"""
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        self.ctx.logger.info("[Snooze] 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。"""
        self.ctx.logger.info("[Snooze] 配置已更新")

    # =========================================================
    # 定时器循环
    # =========================================================

    async def _timer_loop(self):
        while True:
            await asyncio.sleep(self.config.wake.slot_minutes * 60)
            now = datetime.now()
            active_sessions = self.state_manager.get_all_ids()
            if not active_sessions:
                self.ctx.logger.debug("[Snooze] 当前无活跃会话，跳过状态检查")
                continue

            for sid in active_sessions:
                old = self.state_manager.get_or_create(sid)
                new, changed, transition_type = apply_transition(old, now, self.config)
                if changed:
                    self.state_manager.update(sid, new)
                    self.ctx.logger.info(f"[{sid}] 状态变更: {old.state} -> {new.state}, sub={new.sub_state}")

                    # ---- 发送预设消息 ----
                    stream_id = self.state_manager.get_stream_id(sid)
                    if not stream_id:
                        self.ctx.logger.warning(f"[{sid}] 未找到 stream_id，无法发送预设消息")
                        continue

                    if transition_type == "woke_up":
                        await self._send_preset_message(stream_id, self.MSG_WOKE_UP)
                    elif transition_type == "fell_asleep":
                        await self._send_preset_message(stream_id, self.MSG_FELL_ASLEEP)

    # =========================================================
    # 预设消息发送
    # =========================================================

    async def _send_preset_message(self, stream_id: str, text: str) -> None:
        """发送预设消息（不经过LLM，直接发送文本）"""
        try:
            await self.ctx.send.text(text, stream_id)
            self.ctx.logger.debug(f"[Snooze] 发送预设消息: {text} -> {stream_id}")
        except Exception as e:
            self.ctx.logger.warning(f"[Snooze] 发送预设消息失败: {e}")

    # =========================================================
    # Hook 1: 消息拦截（状态管理 + 预设消息发送）
    # =========================================================

    @HookHandler(
        "chat.receive.before_process",
        name="snooze_message_handler",
        description="作息模拟消息拦截器，根据当前作息状态决定放行或拦截",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=5000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_message(self, message: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        """
        处理所有入站消息，根据作息状态决定拦截或放行。
        """
        self.ctx.logger.debug("[Snooze] handle_message 被调用")

        if not self.config.plugin.enabled:
            return None

        if not isinstance(message, dict):
            return None

        # ---- 提取 stream_id ----
        real_stream_id = message.get("stream_id") or message.get("session_id") or ""
        if not real_stream_id:
            info = message.get("message_info") if isinstance(message.get("message_info"), dict) else {}
            real_stream_id = info.get("session_id") or info.get("stream_id") or ""

        if not real_stream_id:
            self.ctx.logger.debug("[Snooze] 无法提取 stream_id，放行")
            return None

        # ---- 提取群号/用户ID ----
        info = message.get("message_info") if isinstance(message.get("message_info"), dict) else {}
        group_info = info.get("group_info") if isinstance(info.get("group_info"), dict) else {}
        user_info = info.get("user_info") if isinstance(info.get("user_info"), dict) else {}
        group_id = str(group_info.get("group_id") or "")
        user_id = str(user_info.get("user_id") or "")

        if group_id:
            is_group = True
            session_id = group_id
            self.ctx.logger.debug(f"[Snooze] 群聊消息 -> 群号: {group_id}, stream_id: {real_stream_id}")
        elif user_id:
            is_group = False
            session_id = user_id
            self.ctx.logger.debug(f"[Snooze] 私聊消息 -> 用户ID: {user_id}, stream_id: {real_stream_id}")
        else:
            self.ctx.logger.debug("[Snooze] 无法提取群号/用户ID，放行")
            return None

        # 存储 stream_id
        self.state_manager.set_stream_id(session_id, real_stream_id)

        # ---- 会话过滤 ----
        if not self._is_allowed(session_id, is_group):
            self.ctx.logger.debug(f"[{session_id}] 不在黑白名单中，放行")
            return None

        # ---- 获取状态 ----
        state = self.state_manager.get_or_create(session_id)

        # ---- 状态分流 ----
        if state.is_awake():
            self.ctx.logger.debug(f"[{session_id}] 状态: 清醒，放行")
            return None

        if state.is_asleep():
            self.ctx.logger.debug(f"[{session_id}] 状态: 睡觉，进入处理")
            return await self._handle_sleeping(message, session_id, state)

        if state.is_pissed():
            self.ctx.logger.debug(f"[{session_id}] 状态: 吵醒，放行")
            # 不再修改用户消息，直接放行，情绪由另一个 Hook 注入
            return None

        return None

    # =========================================================
    # 会话过滤
    # =========================================================

    def _is_allowed(self, session_id: str, is_group: bool) -> bool:
        if is_group:
            mode = self.config.filter.group_filter
            ids = self.config.filter.group_ids
        else:
            mode = self.config.filter.pm_filter
            ids = self.config.filter.pm_ids

        if mode == "whitelist":
            return session_id in ids
        else:
            return session_id not in ids

    # =========================================================
    # 睡眠状态处理
    # =========================================================

    async def _handle_sleeping(
        self,
        message: dict[str, Any],
        session_id: str,
        state: SessionState
    ) -> dict[str, Any] | None:
        """处理睡觉状态下的 @/戳 计数和吵醒"""
        now = datetime.now()
        cfg = self.config

        # 检测 @/戳
        is_triggered = self._is_mentioned_or_poked(message)

        if not is_triggered:
            self.ctx.logger.info(f"[{session_id}] 睡觉中，拦截消息")
            return {"action": "abort"}

        # 滑动窗口计数
        if state.ping_window_start is None:
            state.ping_window_start = now
            state.ping_count = 0

        elapsed = (now - state.ping_window_start).total_seconds()
        if elapsed > cfg.wake.slot_minutes * 60:
            state.ping_window_start = now
            state.ping_count = 0

        state.ping_count += 1
        self.state_manager.update(session_id, state)

        # [修改] 日志改为 @/戳
        self.ctx.logger.info(f"[{session_id}] 睡觉中，收到 @/戳，计数 {state.ping_count}/{cfg.pissed.ping_threshold}")

        # 达到阈值 && 概率命中 -> 吵醒
        if state.ping_count >= cfg.pissed.ping_threshold and random.random() < cfg.pissed.ping_wake_prob:
            new_state = SessionState(
                state=STATE_PISSED,
                sub_state=SUB_ANGRY,
                angry_until=now + timedelta(minutes=cfg.wake.slot_minutes * cfg.pissed.anger_slots),
                ping_count=0,
                ping_window_start=None
            )
            self.state_manager.update(session_id, new_state)

            # [修改] 日志改为 @/戳
            self.ctx.logger.info(f"[{session_id}] 被@/戳吵醒")

            # ---- ★ 只发送预设消息，不修改用户消息 ----
            stream_id = self.state_manager.get_stream_id(session_id)
            if stream_id:
                await self._send_preset_message(stream_id, self.MSG_WAS_WOKEN)
            else:
                self.ctx.logger.warning(f"[{session_id}] 未找到 stream_id，无法发送吵醒消息")

            # ★ 拦截当前消息，不交给LLM
            return {"action": "abort"}

        # 未达阈值或概率未命中 -> 拦截
        self.ctx.logger.info(f"[{session_id}] 睡觉中，@/戳计数 {state.ping_count}/{cfg.pissed.ping_threshold}，拦截")
        return {"action": "abort"}

    # =========================================================
    # Hook 2: 在 Planner 请求前注入生气系统提示
    # =========================================================

    @HookHandler(
        "maisaka.planner.before_request",
        name="snooze_mood_injector",
        description="在 LLM 请求前注入生气状态提示",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=3000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_mood_before_request(
        self,
        messages: List[dict[str, Any]] | None = None,
        session_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        在 LLM 规划请求前，如果当前会话处于生气状态，注入系统提示。

        参考好感度插件的实现，通过插入 system 消息来传递情绪状态，
        不修改用户消息，避免提示词泄漏到聊天端。
        """
        del kwargs

        if not self.config.plugin.enabled:
            return {"action": "continue"}

        if not isinstance(messages, list) or not messages:
            return {"action": "continue"}

        # 从 session_id 反查群号/用户ID
        session_key = self._resolve_session_key(session_id)
        if not session_key:
            return {"action": "continue"}

        # 获取状态
        state = self.state_manager.get_or_create(session_key)

        # 只有处于生气状态才注入
        if not state.is_angry():
            return {"action": "continue"}

        # 构造系统提示（使用配置中的 angry_prefix）
        hint_text = self.config.pissed.angry_prefix
        if not hint_text:
            return {"action": "continue"}

        self.ctx.logger.info(f"[{session_id}] 💢 注入生气系统提示")

        # 插入 system 消息
        hint_msg = {"role": "system", "content": hint_text}

        # 找到最后一个 system 消息的位置，插入其后
        last_system_idx = -1
        for idx, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                last_system_idx = idx

        if last_system_idx < 0:
            modified_messages = [hint_msg] + list(messages)
        else:
            modified_messages = (
                list(messages[:last_system_idx + 1])
                + [hint_msg]
                + list(messages[last_system_idx + 1:])
            )

        return {"action": "continue", "modified_kwargs": {"messages": modified_messages}}

    def _resolve_session_key(self, session_id: str) -> str:
        """
        从 MaiBot 的 session_id 反查群号/用户ID（用于状态存储）。
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return ""

        # 尝试直接匹配已存储的会话
        for sid in self.state_manager.get_all_ids():
            if sid in session_id or session_id.endswith(sid):
                return sid

        # 尝试从 session_id 中提取数字（群号/用户号）
        match = re.search(r'\d+', session_id)
        if match:
            extracted = match.group()
            # 检查是否已存在
            if extracted in self.state_manager.get_all_ids():
                return extracted
            # 否则直接使用提取的值
            return extracted

        return ""

    # =========================================================
    # @/戳 检测方法
    # =========================================================

    def _is_mentioned_or_poked(self, message: dict[str, Any]) -> bool:
        """
        检测消息是否@了Bot，或者戳了Bot。

        返回 True 表示触发了（被@或被戳且目标为Bot）。
        """
        # 先检测 @
        if self._is_mentioned(message):
            return True

        # 再检测戳一戳
        if self._is_poked(message):
            return True

        return False

    def _is_mentioned(self, message: dict[str, Any]) -> bool:
        """检测消息中是否@了Bot。"""
        mentions = message.get("mentions", [])
        if not mentions:
            return False

        bot_id = self._get_bot_id(message)
        if bot_id is None:
            # 无法获取bot_id，宁滥勿缺
            return True
        return bot_id in mentions

    def _is_poked(self, message: dict[str, Any]) -> bool:
        """检测消息是否为戳一戳事件，且目标是Bot。"""
        # 1. 必须为 notify 事件
        if not message.get("is_notify"):
            return False

        # 2. 检查 additional_config
        msg_info = message.get("message_info")
        if not isinstance(msg_info, dict):
            return False
        additional = msg_info.get("additional_config")
        if not isinstance(additional, dict):
            return False

        # 3. 必须为 poke 子类型
        if additional.get("napcat_notice_type") != "notify":
            return False
        if additional.get("napcat_notice_sub_type") != "poke":
            return False

        # 4. 提取 payload
        payload = additional.get("napcat_notice_payload")
        if not isinstance(payload, dict):
            return False

        self_id = str(payload.get("self_id") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()

        # 必须包含 self_id 和 target_id
        if not self_id or not target_id:
            return False

        # 目标必须是 bot 自己
        if target_id != self_id:
            return False

        return True

    def _get_bot_id(self, message: dict[str, Any]) -> Optional[str]:
        """获取Bot自身的ID（优先从缓存或消息中获取）。"""
        bot_id = getattr(self, "_bot_id", None)
        if bot_id is None:
            bot_id = message.get("bot_id") or message.get("self_id")
        if bot_id is None:
            # 尝试从 self.ctx 获取（如果有）
            if hasattr(self, "ctx") and hasattr(self.ctx, "bot"):
                try:
                    # 可能通过 bot.get_login_info 获取
                    info = self.ctx.bot.get_login_info()
                    if info and isinstance(info, dict):
                        bot_id = info.get("user_id")
                except Exception:
                    pass
        return str(bot_id) if bot_id else None


# =========================================================
# 插件入口
# =========================================================

def create_plugin() -> SnoozePlugin:
    """创建打盹儿插件实例"""
    return SnoozePlugin()