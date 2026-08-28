"""作息模拟插件 — MaiBot SDK v2

为 MaiBot 角色增加模拟作息功能，支持睡觉和被吵醒带有起床气。

功能特性：
    - 模拟人类作息，在设定的睡觉时间内自动进入"睡觉"状态，完全静默不回复
    - 在起床时间通过概率"贪睡"或"起床"，模拟赖床行为
    - 在睡觉时间通过概率"熬夜"或"入睡"，模拟熬夜行为
    - 被 @ 多次可概率吵醒，吵醒后进入"生气"状态并注入自定义语气前缀
    - 生气状态持续一定时间后自动"消气"，期间可概率"补觉"重新入睡
    - 群聊和私聊状态完全隔离，不同群聊互不影响
    - 支持黑白名单控制生效范围（群聊和私聊分开设置）
    - 状态变更时自动发送预设消息：睡醒发"早ﾉ☀"，入睡发"Zzz🌙"，被吵醒发"白金吵闹💢"
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

from maibot_sdk import MaiBotPlugin, EventHandler
from maibot_sdk.types import EventType

from .config import SnoozeConfig
from .models import (
    SessionState, STATE_ASLEEP, STATE_PISSED, SUB_ANGRY,
    apply_transition, TransitionType
)


class StateManager:
    """会话状态池管理（内存版）。"""
    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState()
        return self._sessions[session_id]

    def update(self, session_id: str, state: SessionState) -> None:
        self._sessions[session_id] = state

    def get_all_ids(self) -> list:
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
        """处理插件加载。"""
        self.ctx.logger.info("Snooze 插件加载完成，开始作息调度。")
        self._timer_task = asyncio.create_task(self._timer_loop())

    async def on_unload(self) -> None:
        """处理插件卸载。"""
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        self.ctx.logger.info("Snooze 插件已卸载。")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。"""
        self.ctx.logger.info("Snooze 配置已更新。")

    # =========================================================
    # 定时器循环
    # =========================================================

    async def _timer_loop(self):
        while True:
            await asyncio.sleep(self.config.slot_minutes * 60)
            now = datetime.now()
            for sid in self.state_manager.get_all_ids():
                old = self.state_manager.get_or_create(sid)
                new, changed, transition_type = apply_transition(old, now, self.config)
                if changed:
                    self.state_manager.update(sid, new)
                    self.ctx.logger.debug(f"[{sid}] 状态变更: {old.state} -> {new.state}, sub={new.sub_state}")

                    # ---- 发送预设消息（不经过LLM） ----
                    if transition_type == "woke_up":
                        # 睡醒 → 发送 "早ﾉ☀"
                        await self._send_preset_message(sid, self.MSG_WOKE_UP)
                    elif transition_type == "fell_asleep":
                        # 入睡 → 发送 "Zzz🌙"
                        await self._send_preset_message(sid, self.MSG_FELL_ASLEEP)

    # =========================================================
    # 预设消息发送
    # =========================================================

    async def _send_preset_message(self, stream_id: str, text: str) -> None:
        """发送预设消息（不经过LLM，直接发送文本）。"""
        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as e:
            self.ctx.logger.warning(f"[Snooze] 发送预设消息失败: {e}")

    # =========================================================
    # 消息拦截 (EventHandler)
    # =========================================================

    @EventHandler(
        "snooze_message_handler",
        description="作息模拟消息拦截器，根据当前作息状态决定放行或拦截",
        event_type=EventType.ON_MESSAGE_PRE_PROCESS,
    )
    async def on_message(
        self,
        message: Any = None,
        stream_id: str = "",
        **kwargs: Any
    ) -> Tuple[bool, bool, Any, str, Optional[Dict[str, Any]]]:
        """
        处理所有入站消息，根据作息状态决定拦截或放行。

        返回值格式: (handled, success, message, new_stream_id, extra)
        """
        # 1. 提取会话信息
        is_group = False
        session_id = stream_id

        if isinstance(message, dict):
            is_group = message.get("is_group", False)
            session_id = str(message.get("group_id") if is_group else message.get("user_id", stream_id))
        elif hasattr(message, "group_id"):
            is_group = message.group_id is not None
            session_id = str(message.group_id if is_group else message.user_id)

        # 2. 会话过滤（黑白名单）
        if not self._is_allowed(session_id, is_group):
            return True, True, None, stream_id, None

        # 3. 获取当前状态
        state = self.state_manager.get_or_create(session_id)

        # 4. 状态分流
        if state.is_awake():
            return True, True, None, stream_id, None

        if state.is_asleep():
            # ★ 修改：加上 await，因为 _handle_sleeping 现在是异步方法
            return await self._handle_sleeping(message, stream_id, session_id, state)

        if state.is_pissed():
            return self._inject_if_angry(message, stream_id, state)

        return True, True, None, stream_id, None

    # =========================================================
    # 会话过滤
    # =========================================================

    def _is_allowed(self, session_id: str, is_group: bool) -> bool:
        if is_group:
            mode = self.config.group_filter
            ids = self.config.group_ids
        else:
            mode = self.config.pm_filter
            ids = self.config.pm_ids

        if mode == "whitelist":
            return session_id in ids
        else:
            return session_id not in ids

    # =========================================================
    # 睡眠状态处理
    # =========================================================

    async def _handle_sleeping(
        self,
        message: Any,
        stream_id: str,
        session_id: str,
        state: SessionState
    ) -> Tuple[bool, bool, Any, str, Optional[Dict[str, Any]]]:
        """处理睡觉状态下的@计数和吵醒。"""
        now = datetime.now()
        cfg = self.config

        is_mentioned = self._is_mentioned(message)

        if not is_mentioned:
            return True, True, None, stream_id, {"intercept_message": True}

        # 滑动窗口计数
        if state.ping_window_start is None:
            state.ping_window_start = now
            state.ping_count = 0

        elapsed = (now - state.ping_window_start).total_seconds()
        if elapsed > cfg.slot_minutes * 60:
            state.ping_window_start = now
            state.ping_count = 0

        state.ping_count += 1
        self.state_manager.update(session_id, state)

        # 达到阈值 && 概率命中 -> 吵醒
        if state.ping_count >= cfg.ping_threshold and random.random() < cfg.ping_wake_prob:
            new_state = SessionState(
                state=STATE_PISSED,
                sub_state=SUB_ANGRY,
                angry_until=now + timedelta(minutes=cfg.slot_minutes * cfg.anger_slots),
                ping_count=0,
                ping_window_start=None
            )
            self.state_manager.update(session_id, new_state)

            # ---- 被吵醒 → 发送 "白金吵闹💢"（不经过LLM） ----
            await self._send_preset_message(stream_id, self.MSG_WAS_WOKEN)

            return self._inject_if_angry(message, stream_id, new_state)

        return True, True, None, stream_id, {"intercept_message": True}

    # =========================================================
    # 前缀注入
    # =========================================================

    def _inject_if_angry(
        self,
        message: Any,
        stream_id: str,
        state: SessionState
    ) -> Tuple[bool, bool, Any, str, Optional[Dict[str, Any]]]:
        """如果处于生气状态，在用户消息前注入前缀。"""
        if state.is_angry():
            prefix = self.config.angry_prefix
            if isinstance(message, dict):
                original = message.get("plain_text", "") or message.get("content", "")
                message["plain_text"] = f"{prefix}\n{original}"
                if "content" in message:
                    message["content"] = f"{prefix}\n{message.get('content', '')}"
            elif hasattr(message, "content"):
                original = message.content
                message.content = f"{prefix}\n{original}"
        return True, True, None, stream_id, None

    # =========================================================
    # @检测
    # =========================================================

    def _is_mentioned(self, message: Any) -> bool:
        """检测消息中是否@了Bot。"""
        if isinstance(message, dict):
            mentions = message.get("mentions", [])
            if mentions:
                bot_id = getattr(self, "_bot_id", None)
                if bot_id is None:
                    return True
                return bot_id in mentions
            return False

        mentions = getattr(message, "mentions", [])
        if not mentions:
            return False
        bot_id = getattr(self, "_bot_id", None)
        if bot_id is None:
            return True
        return bot_id in mentions


# =========================================================
# 插件入口
# =========================================================

def create_plugin() -> SnoozePlugin:
    """创建打盹儿插件实例。"""
    return SnoozePlugin()