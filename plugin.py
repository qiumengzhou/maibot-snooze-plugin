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
from typing import Optional, Dict, Any

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
    每个会话存储：状态对象 + stream_id（MaiBot内部格式）
    """
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """获取或创建会话状态对象。"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "state": SessionState(),
                "stream_id": ""
            }
        return self._sessions[session_id]["state"]

    def update(self, session_id: str, state: SessionState) -> None:
        """更新会话状态。"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {"state": state, "stream_id": ""}
        else:
            self._sessions[session_id]["state"] = state

    def set_stream_id(self, session_id: str, stream_id: str) -> None:
        """存储会话对应的真实 stream_id（MaiBot内部格式）。"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {"state": SessionState(), "stream_id": stream_id}
        else:
            self._sessions[session_id]["stream_id"] = stream_id

    def get_stream_id(self, session_id: str) -> str:
        """获取会话对应的真实 stream_id。"""
        if session_id in self._sessions:
            return self._sessions[session_id].get("stream_id", "")
        return ""

    def get_all_ids(self) -> list:
        """获取所有会话 ID。"""
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
        self.ctx.logger.info("[Snooze] 插件加载完成，开始作息调度。")

        # ---- 主动初始化配置中的群聊 ----
        for gid in self.config.filter.group_ids:
            self.state_manager.get_or_create(str(gid))
            self.ctx.logger.debug(f"[Snooze] 预创建群聊会话: {gid}")

        self._timer_task = asyncio.create_task(self._timer_loop())

    async def on_unload(self) -> None:
        """处理插件卸载。"""
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        self.ctx.logger.info("[Snooze] 插件已卸载。")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。"""
        self.ctx.logger.info("[Snooze] 配置已更新。")

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
            # 直接使用 MaiBot 内部的 stream_id
            await self.ctx.send.text(text, stream_id)
            self.ctx.logger.debug(f"[Snooze] 发送预设消息: {text} -> {stream_id}")
        except Exception as e:
            self.ctx.logger.warning(f"[Snooze] 发送预设消息失败: {e}")

    # =========================================================
    # 消息拦截 (HookHandler)
    # =========================================================

    @HookHandler(
        "chat.receive.before_process",
        name="snooze_message_handler",
        description="作息模拟消息拦截器，根据当前作息状态决定放行或拦截",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,      # 最高拦截优先级
        timeout_ms=5000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_message(self, message: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        """
        处理所有入站消息，根据作息状态决定拦截或放行

        返回值格式:
            - None: 放行，不影响后续处理
            - {"action": "abort"}: 拦截，中止后续处理
            - {"action": "continue", "modified_kwargs": {"message": modified_message}}: 放行并修改消息
        """
        # 调试日志：确认插件被调用
        self.ctx.logger.debug("[Snooze] handle_message 被调用")

        # 1. 插件开关检查
        if not self.config.plugin.enabled:
            return None

        # 2. 提取会话信息
        if not isinstance(message, dict):
            return None

        # ---- 提取 stream_id（直接使用 MaiBot 内部格式） ----
        real_stream_id = message.get("stream_id") or message.get("session_id") or ""
        if not real_stream_id:
            # 尝试从 message_info 中提取
            info = message.get("message_info") if isinstance(message.get("message_info"), dict) else {}
            real_stream_id = info.get("session_id") or info.get("stream_id") or ""

        if not real_stream_id:
            self.ctx.logger.debug("[Snooze] 无法提取 stream_id，放行")
            return None

        # ---- 提取群号/用户ID（用于黑白名单） ----
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

        # 存储真实的 stream_id（MaiBot 内部格式，如 1fb09f6e9d8dec317f398478f4777c0f）
        self.state_manager.set_stream_id(session_id, real_stream_id)

        # 3. 会话过滤（黑白名单）
        if not self._is_allowed(session_id, is_group):
            self.ctx.logger.debug(f"[{session_id}] 不在黑白名单中，放行")
            return None

        # 4. 获取或创建当前状态
        state = self.state_manager.get_or_create(session_id)

        # 5. 状态分流
        # 分支A：清醒 -> 放行
        if state.is_awake():
            self.ctx.logger.debug(f"[{session_id}] 状态: 清醒，放行")
            return None

        # 分支B：睡觉 -> 拦截或尝试吵醒
        if state.is_asleep():
            self.ctx.logger.debug(f"[{session_id}] 状态: 睡觉，进入处理")
            return await self._handle_sleeping(message, session_id, state)

        # 分支C：吵醒 -> 放行（可能注入前缀）
        if state.is_pissed():
            self.ctx.logger.debug(f"[{session_id}] 状态: 吵醒，检查注入")
            return self._inject_if_angry(message, state)

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
        """处理睡觉状态下的@计数和吵醒"""
        now = datetime.now()
        cfg = self.config

        is_mentioned = self._is_mentioned(message)

        if not is_mentioned:
            # 静默拦截
            self.ctx.logger.info(f"[{session_id}] 🛌 睡觉中，拦截消息（未被@）")
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

        self.ctx.logger.info(f"[{session_id}] 🛌 睡觉中，收到 @，计数 {state.ping_count}/{cfg.pissed.ping_threshold}")

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

            self.ctx.logger.info(f"[{session_id}] 🔥 被@吵醒！")

            # ---- 被吵醒 → 发送 "白金吵闹💢" ----
            stream_id = self.state_manager.get_stream_id(session_id)
            if stream_id:
                await self._send_preset_message(stream_id, self.MSG_WAS_WOKEN)
            else:
                self.ctx.logger.warning(f"[{session_id}] 未找到 stream_id，无法发送吵醒消息")

            return self._inject_if_angry(message, new_state)

        # 未达阈值或概率未命中 -> 拦截
        self.ctx.logger.info(f"[{session_id}] 🛌 睡觉中，@计数 {state.ping_count}/{cfg.pissed.ping_threshold}，拦截")
        return {"action": "abort"}

    # =========================================================
    # 前缀注入
    # =========================================================

    def _inject_if_angry(
        self,
        message: dict[str, Any],
        state: SessionState
    ) -> dict[str, Any] | None:
        """如果处于生气状态，在用户消息前注入前缀"""
        if state.is_angry():
            prefix = self.config.pissed.angry_prefix

            # 修改消息内容
            original = message.get("processed_plain_text", "") or message.get("plain_text", "")
            message["processed_plain_text"] = f"{prefix}\n{original}"
            message["plain_text"] = f"{prefix}\n{original}"

            # 也修改 raw_message 中的文本
            raw = message.get("raw_message")
            if isinstance(raw, list):
                for part in raw:
                    if isinstance(part, dict) and part.get("type") == "text":
                        data = part.get("data", "")
                        part["data"] = f"{prefix}\n{data}"
                        break
                else:
                    # 没有文本节点，插入一个
                    raw.insert(0, {"type": "text", "data": f"{prefix}\n"})

            self.ctx.logger.debug(f"[Snooze] 注入愤怒前缀")
            return {"action": "continue", "modified_kwargs": {"message": message}}

        return None  # 放行

    # =========================================================
    # @检测
    # =========================================================

    def _is_mentioned(self, message: dict[str, Any]) -> bool:
        """检测消息中是否@了Bot。"""
        mentions = message.get("mentions", [])
        if not mentions:
            return False

        bot_id = getattr(self, "_bot_id", None)
        if bot_id is None:
            # 尝试从消息中获取
            bot_id = message.get("bot_id") or message.get("self_id")
        if bot_id is None:
            # 无法获取bot_id，只要有mentions就认为被@
            return True
        return bot_id in mentions


# =========================================================
# 插件入口
# =========================================================

def create_plugin() -> SnoozePlugin:
    """创建打盹儿插件实例"""
    return SnoozePlugin()