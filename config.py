from typing import List
from pydantic import Field
from maibot_sdk.config import PluginConfigBase


class PluginSectionConfig(PluginConfigBase):

    __ui_label__ = "插件设置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={"label": "启用插件"},
    )
    config_version: str = Field(
        default="1.2.0",
        description="配置版本号",
        json_schema_extra={"label": "配置版本"},
    )

    timezone: str = Field(
        default="UTC+8",
        description="插件使用的时区。可选: 'UTC+8'(北京时间) 或 'UTC'(协调世界时)",
        json_schema_extra={
            "label": "时区",
            "placeholder": "UTC+8 or UTC",
        }
    )


class FilterConfig(PluginConfigBase):

    __ui_label__ = "过滤"
    __ui_icon__ = "filter"
    __ui_order__ = 1

    group_filter: str = Field(
        default="whitelist",
        description="群聊过滤模式，可选 whitelist / blacklist",
        json_schema_extra={
            "label": "群聊过滤模式",
            "placeholder": "whitelist or blacklist",
        }
    )
    group_ids: List[str] = Field(
        default=[],
        description="群聊列表",
        json_schema_extra={
            "label": "群聊列表",
            "placeholder": "123456789",
        }
    )
    pm_filter: str = Field(
        default="whitelist",
        description="私聊过滤模式，可选 whitelist / blacklist",
        json_schema_extra={
            "label": "私聊过滤模式",
            "placeholder": "whitelist or blacklist",
        }
    )
    pm_ids: List[str] = Field(
        default=[],
        description="用户列表",
        json_schema_extra={
            "label": "用户列表",
            "placeholder": "123456789",
        }
    )


class WakeConfig(PluginConfigBase):

    __ui_label__ = "起床"
    __ui_icon__ = "sunrise"
    __ui_order__ = 2

    slot_minutes: int = Field(
        default=30,
        description="时间片段大小（分钟）",
        json_schema_extra={
            "label": "时间片段（分钟）",
            "placeholder": "30",
        }
    )
    wake_start: str = Field(
        default="07:00",
        description="起床时间",
        json_schema_extra={
            "label": "起床时间",
            "placeholder": "07:00",
        }
    )
    snooze_prob: float = Field(
        default=0.3,
        description="贪睡概率（0~1）",
        json_schema_extra={
            "label": "贪睡概率",
            "placeholder": "0 ~ 1",
        }
    )
    force_wake_time: str = Field(
        default="11:00",
        description="强制起床时间点",
        json_schema_extra={
            "label": "强制起床时间",
            "placeholder": "11:00",
        }
    )


class SleepConfig(PluginConfigBase):

    __ui_label__ = "睡觉"
    __ui_icon__ = "moon"
    __ui_order__ = 3

    sleep_start: str = Field(
        default="21:00",
        description="睡觉时间",
        json_schema_extra={
            "label": "睡觉时间",
            "placeholder": "21:00",
        }
    )
    stay_prob: float = Field(
        default=0.2,
        description="熬夜概率（0~1）",
        json_schema_extra={
            "label": "熬夜概率",
            "placeholder": "0 ~ 1",
        }
    )
    force_sleep_time: str = Field(
        default="04:00",
        description="强制睡觉时间点",
        json_schema_extra={
            "label": "强制睡觉时间",
            "placeholder": "04:00",
        }
    )


class PissedConfig(PluginConfigBase):

    __ui_label__ = "吵醒"
    __ui_icon__ = "alert-triangle"
    __ui_order__ = 4

    ping_threshold: int = Field(
        default=3,
        description="吵醒阈值（@计数）",
        json_schema_extra={
            "label": "吵醒阈值（@计数）",
            "placeholder": "4",
        }
    )
    ping_wake_prob: float = Field(
        default=0.6,
        description="吵醒概率（0~1）",
        json_schema_extra={
            "label": "吵醒概率",
            "placeholder": "0 ~ 1",
        }
    )
    angry_prefix: str = Field(
        default="你被吵醒了，现在极度愤怒，语气必须非常不善！",
        description="生气状态下注入LLM的前缀文本",
        json_schema_extra={
            "label": "起床气描述",
            "placeholder": "你被吵醒了...",
        }
    )
    anger_slots: int = Field(
        default=1,
        description="消气时间（片段数）",
        json_schema_extra={
            "label": "消气时间（片段数）",
            "placeholder": "1",
        }
    )
    resleep_prob: float = Field(
        default=0.2,
        description="补觉概率（0~1）",
        json_schema_extra={
            "label": "补觉概率",
            "placeholder": "0 ~ 1",
        }
    )


class SnoozeConfig(PluginConfigBase):

    __ui_label__ = "打盹儿"
    __ui_icon__ = "package"
    __ui_order__ = 0

    plugin: PluginSectionConfig = Field(
        default_factory=PluginSectionConfig,
        description="插件基础设置"
    )
    filter: FilterConfig = Field(
        default_factory=FilterConfig,
        description="过滤配置"
    )
    wake: WakeConfig = Field(
        default_factory=WakeConfig,
        description="起床配置"
    )
    sleep: SleepConfig = Field(
        default_factory=SleepConfig,
        description="睡觉配置"
    )
    pissed: PissedConfig = Field(
        default_factory=PissedConfig,
        description="吵醒配置"
    )