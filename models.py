import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Literal

from .config import SnoozeConfig

# ---- 主状态常量 ----
STATE_AWAKE = "awake"      # 清醒
STATE_ASLEEP = "asleep"    # 睡觉
STATE_PISSED = "pissed"    # 吵醒

# ---- 副状态常量（仅主状态为 PISSED 时有效） ----
SUB_ANGRY = "angry"        # 生气
SUB_CALM = "calm"          # 消气

# ---- 状态变更类型 ----
TransitionType = Optional[Literal["woke_up", "fell_asleep"]]


@dataclass
class SessionState:
    """单个会话的完整状态快照。"""
    state: str = STATE_AWAKE
    sub_state: Optional[str] = None
    angry_until: Optional[datetime] = None  # 生气到期时间
    ping_count: int = 0                     # 当前窗口@计数
    ping_window_start: Optional[datetime] = None

    def is_asleep(self) -> bool:
        return self.state == STATE_ASLEEP

    def is_awake(self) -> bool:
        return self.state == STATE_AWAKE

    def is_pissed(self) -> bool:
        return self.state == STATE_PISSED

    def is_angry(self) -> bool:
        return self.state == STATE_PISSED and self.sub_state == SUB_ANGRY

    def is_calm(self) -> bool:
        return self.state == STATE_PISSED and self.sub_state == SUB_CALM

    def reset_ping_window(self):
        self.ping_count = 0
        self.ping_window_start = None

    def clear_anger(self):
        self.sub_state = None
        self.angry_until = None


# ---- 辅助函数：判断当前时间是否处于睡觉时间段 ----
def _is_in_time_window(now: datetime, start_time: str, end_time: str) -> bool:
    # 支持跨天
    start = now.replace(hour=int(start_time[:2]), minute=int(start_time[3:]), second=0, microsecond=0)
    end = now.replace(hour=int(end_time[:2]), minute=int(end_time[3:]), second=0, microsecond=0)

    if end <= start:
        end += timedelta(days=1)

    return start <= now <= end


# ---- 状态流转核心函数 ----
def apply_transition(state: SessionState, now: datetime, config: SnoozeConfig):
    """
    纯状态流转函数
    返回：(新状态, 是否发生了变更, 变更类型)
    - 变更类型: "woke_up" (睡醒), "fell_asleep" (入睡), None (无变更)
    """
    new_state = SessionState(
        state=state.state,
        sub_state=state.sub_state,
        angry_until=state.angry_until,
        ping_count=state.ping_count,
        ping_window_start=state.ping_window_start
    )
    changed = False
    transition_type: TransitionType = None

    old_state = state.state

    # ---- 第1步：强制执行（最高优先级） ----
    # 强制起床 在 force_wake_time ~ sleep_start
    if _is_in_time_window(now, config.wake.force_wake_time, config.sleep.sleep_start):
        if new_state.state != STATE_AWAKE or new_state.sub_state is not None:
            new_state.state = STATE_AWAKE
            new_state.clear_anger()
            changed = True
            if old_state != STATE_AWAKE:
                transition_type = "woke_up"
        return new_state, changed, transition_type

    # 强制睡觉 在 force_sleep_time ~ wake_start
    if _is_in_time_window(now, config.sleep.force_sleep_time, config.wake.wake_start):
        if new_state.is_pissed() and new_state.is_angry():
            # 被吵醒还没消气不能强制睡觉
            pass
        else:
            if new_state.state != STATE_ASLEEP or new_state.sub_state is not None:
                new_state.state = STATE_ASLEEP
                new_state.clear_anger()
                changed = True
                if old_state != STATE_ASLEEP:
                    transition_type = "fell_asleep"
            return new_state, changed, transition_type

    # ---- 第2步：清醒 -> 睡觉（熬夜判定） ----
    # 在 sleep_start ~ force_sleep_time
    if new_state.is_awake() and _is_in_time_window(now, config.sleep.sleep_start, config.sleep.force_sleep_time):
        if random.random() >= config.sleep.stay_prob:
            new_state.state = STATE_ASLEEP
            new_state.clear_anger()
            changed = True
            transition_type = "fell_asleep"

    # ---- 第3步：睡觉 -> 清醒（贪睡判定） ----
    # 在 wake_start ~ force_wake_time
    elif new_state.is_asleep() and _is_in_time_window(now, config.wake.wake_start, config.wake.force_wake_time):
        if random.random() >= config.wake.snooze_prob:
            new_state.state = STATE_AWAKE
            new_state.clear_anger()
            changed = True
            transition_type = "woke_up"

    # ---- 第4步：吵醒 -> 睡觉（补觉判定） ----
    elif new_state.is_pissed():
        # 未消气也可以进行补觉
        if random.random() < config.pissed.resleep_prob:
            new_state.state = STATE_ASLEEP
            new_state.clear_anger()
            changed = True
            transition_type = "fell_asleep"
            return new_state, changed, transition_type

        # ---- 第5步：生气 -> 消气 ----
        if new_state.is_angry() and new_state.angry_until and now >= new_state.angry_until:
            new_state.sub_state = SUB_CALM
            new_state.angry_until = None
            changed = True

    return new_state, changed, transition_type