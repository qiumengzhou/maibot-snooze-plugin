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
        return self.sub_state == SUB_ANGRY

    def is_calm(self) -> bool:
        return self.sub_state == SUB_CALM

    def reset_ping_window(self):
        self.ping_count = 0
        self.ping_window_start = None

    def clear_anger(self):
        self.sub_state = None
        self.angry_until = None


# ---- 时间工具函数 ----
def parse_time(time_str: str):
    """将 'HH:MM' 解析为 datetime.time 对象。"""
    hour, minute = map(int, time_str.split(":"))
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

def is_time_past(time_str: str, now: datetime) -> bool:
    """判断当前时间是否已超过指定时间点（今天）。"""
    target = parse_time(time_str)
    return now >= target


# ---- 状态流转核心函数 ----
def apply_transition(state: SessionState, now: datetime, config: SnoozeConfig):
    """
    纯状态流转函数（无副作用）。
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
    if config.force_wake and is_time_past(config.force_wake_time, now):
        if new_state.state != STATE_AWAKE or new_state.sub_state is not None:
            new_state.state = STATE_AWAKE
            new_state.clear_anger()
            changed = True
            # 强制起床也是"睡醒"
            if old_state != STATE_AWAKE:
                transition_type = "woke_up"
        return new_state, changed, transition_type

    if config.force_sleep and is_time_past(config.force_sleep_time, now):
        if new_state.state != STATE_ASLEEP or new_state.sub_state is not None:
            new_state.state = STATE_ASLEEP
            new_state.clear_anger()
            changed = True
            # 强制睡觉也是"入睡"
            if old_state != STATE_ASLEEP:
                transition_type = "fell_asleep"
        return new_state, changed, transition_type

    # ---- 第2步：清醒 -> 睡觉（熬夜判定） ----
    if new_state.is_awake() and is_time_past(config.sleep_start, now):
        if random.random() >= config.stay_prob:  # 未命中熬夜 -> 入睡
            new_state.state = STATE_ASLEEP
            new_state.clear_anger()
            changed = True
            transition_type = "fell_asleep"

    # ---- 第3步：睡觉 -> 清醒（贪睡判定） ----
    elif new_state.is_asleep() and is_time_past(config.wake_start, now):
        if random.random() >= config.snooze_prob:  # 未命中贪睡 -> 起床
            new_state.state = STATE_AWAKE
            new_state.clear_anger()
            changed = True
            transition_type = "woke_up"

    # ---- 第4步：吵醒 -> 睡觉（补觉判定） ----
    elif new_state.is_pissed():
        if random.random() < config.resleep_prob:
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
            # 消气不是入睡或睡醒，transition_type 保持 None

    return new_state, changed, transition_type