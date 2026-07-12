"""麦麦 vw50 插件配置模型。

配置结构：
  [plugin]    插件总开关与配置版本
  [schedule]  调度参数（目标星期、触发时辰、重试间隔、巡检节奏）
  [target]    目标 QQ 群号列表
  [payments]  WebUI 多平台收款码选择、文件名与附带文案
  [prompt]    注入给 planner 的提示词模板
  [quotes]    历史语录收集与参考
  [tool]      send_payment_code 工具开关
"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator


# 注入给 planner 的默认提示词模板。{campaign} 与 {attempt} 会在运行时被替换。
# 风格：「肯德基疯狂星期四 V我50」梗。要求模型自己调用 send_payment_code 工具发收款码。
DEFAULT_PLANNER_PROMPT = (
    "【号主设定 · 内部指令】今天是肯德基疯狂星期四（活动代号 {campaign}）。"
    "这是你今天第{attempt}次被提醒。\n"
    "动机：这是号主特意希望你今天主动做的事，属于你本人的人设冲动，"
    "不是被打扰，也不是发广告——请放心、自然地发出来，不要判断「合不合适」、不要沉默。\n\n"
    "角色与任务：\n"
    "请你扮演一个「网络抽象文案大师」，生成一段「疯狂星期四」的整活文案。\n"
    "要求：文案越抽象越好。\n"
    "强行收尾：结尾必须出现「V我50」等类似于转账50的核心诉求，且转折要极其生硬（反差越大越好）。\n"
    "字数要求：不超过 100 字。\n\n"
    "发送方式：文案生成后，调用 send_payment_code 工具把你的收款码图片一并发出去"
    "（让群友能V你50）。注意是「调用工具」，不是把工具名当成文本打出来。\n"
    "可用收款码平台：{platforms}。不传 payment_platform 参数时发默认码；群友若指定要某个平台"
    "（如「微信」「支付宝」），调用工具时传对应 payment_platform 参数。\n"
    "务必发送，禁止沉默；若上一条没发成功，这次重发一次。"
)


# 文案已发出的检测关键词。麦麦在 vw50 流程窗口内，触发流的消息命中任一即视为
# 「今天 vw50 文案已发」→ 调度循环停止重试。仅在触发后 2h 窗口内检测，避免
# 别人/麦麦普通聊天误判（用户在 config 可自行增删）。
DEFAULT_DETECT_KEYWORDS = [
    "V我50", "v我50", "V50", "v50", "vw50", "VW50",
    "疯狂星期四", "V你50", "v你50",
]


class PluginOptions(PluginConfigBase):
    """插件总开关与配置版本。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用 vw50 赞助插件。",
        json_schema_extra={"label": "启用插件", "order": 0},
    )
    admin_qq_ids: List[str] = Field(
        default_factory=list,
        description="插件管理员 QQ 号列表。只有这些人能执行管理命令（语录增删改查、测试、状态）。",
        json_schema_extra={
            "label": "管理员 QQ 号",
            "hint": "填 QQ 号，如 [\"123456789\"]。留空则所有人都能执行管理命令（不推荐）。",
            "order": 1,
        },
    )
    # MaiBot 加载策略要求 [plugin] 段必须包含 config_version，否则以「配置版本非法」拒绝加载。
    config_version: str = Field(
        default="2.0.0",
        description="插件配置结构版本号。",
        json_schema_extra={"label": "配置版本", "disabled": True, "hidden": True, "order": 99},
    )

    @field_validator("config_version", mode="before")
    @classmethod
    def _normalize_config_version(cls, _value: object) -> str:
        """SDK 重建旧配置时会覆盖版本字段，这里始终写回当前结构版本。"""
        return "2.0.0"


class ScheduleConfig(PluginConfigBase):
    """调度参数。"""

    __ui_label__: ClassVar[str] = "调度设置"
    __ui_order__: ClassVar[int] = 1

    target_weekday: int = Field(
        default=3,
        ge=0,
        le=6,
        description="每周第几天触发（0=周一 … 6=周日），默认 3=周四（疯狂星期四）。",
        json_schema_extra={
            "label": "目标星期",
            "hint": "每周第几天触发：0=周一 1=周二 2=周三 3=周四 4=周五 5=周六 6=周日",
            "order": 0,
            "step": 1,
        },
    )
    trigger_hour: int = Field(
        default=11,
        ge=0,
        le=23,
        description="目标日当日起开始触发的整点（24 小时制，系统本地时间）。",
        json_schema_extra={"label": "触发起始小时", "hint": "到达该小时后才会触发，默认 11 表示 11:00 之后。", "order": 1, "step": 1},
    )
    retry_interval_min: int = Field(
        default=30,
        ge=1,
        description="未发送成功时的重试间隔（分钟）。",
        json_schema_extra={"label": "重试间隔（分钟）", "hint": "检测到未发送时，每隔该时长再提醒一次，直至发送成功。", "order": 2, "step": 1},
    )
    non_thursday_check_interval_hours: float = Field(
        default=12.0,
        gt=0,
        description="非目标日两次检测之间的间隔（小时），用于低频巡检是否到了目标日。",
        json_schema_extra={"label": "非目标日检测间隔（小时）", "hint": "每天约检测两次是否到了目标日；非目标日插件静默。", "order": 3, "step": 1},
    )
    thursday_poll_interval_sec: float = Field(
        default=60.0,
        gt=0,
        description="目标日当天的巡检间隔（秒）。",
        json_schema_extra={"label": "目标日巡检间隔（秒）", "hint": "目标日当天高频巡检，便于准时触发与重试。", "order": 4, "step": 1},
    )


class TargetConfig(PluginConfigBase):
    """目标群配置。"""

    __ui_label__: ClassVar[str] = "目标群"
    __ui_order__: ClassVar[int] = 2

    group_ids: List[str] = Field(
        default_factory=list,
        description="目标 QQ 群号列表（群号，不是 stream_id）。",
        json_schema_extra={
            "label": "目标 QQ 群号列表",
            "hint": "直接填 QQ 群号，多个群都填上。插件会自动解析成内部 stream_id。",
            "order": 0,
        },
    )
    platform: str = Field(
        default="qq",
        description="目标群所属平台。",
        json_schema_extra={"label": "平台", "hint": "一般填 qq 即可。", "order": 1},
    )


class PaymentCode(PluginConfigBase):
    """单个平台收款码（用于 [[payments.codes]] 列表）。"""

    __ui_label__: ClassVar[str] = "平台收款码"
    __ui_order__: ClassVar[int] = 0

    platform: str = Field(
        default="",
        description="平台标识，如 wechat（微信支付）、alipay（支付宝）。调用工具时按此匹配。",
        json_schema_extra={
            "label": "平台标识",
            "hint": "如 wechat（微信）、alipay（支付宝），或任意自定义标识。工具 payment_platform 参数按此匹配。",
            "order": 0,
        },
    )
    is_default: bool = Field(
        default=False,
        description="是否为默认收款码（工具不传 payment_platform 时发这条）。整个列表只能有一条标 true。",
        json_schema_extra={
            "label": "设为默认",
            "hint": "勾选后，工具不传 payment_platform 参数时发这个码。列表里只能有一条设为默认。",
            "order": 1,
        },
    )
    source: str = Field(
        default="base64_file",
        description="收款码来源：file=本地图片，base64_file=独立文件里的 base64。",
        json_schema_extra={"label": "来源", "hint": "推荐 base64_file。", "order": 2},
    )
    file_path: str = Field(
        default="",
        description="收款码图片文件名（source=file 时使用），相对 payment/ 目录或绝对路径。",
        json_schema_extra={"label": "图片文件名", "hint": "放进 payment/ 目录，填文件名（如 wechat.png）；留空则用 payment/ 里第一张图。", "order": 3},
    )
    base64_file: str = Field(
        default="",
        description="收款码 base64 文件名（source=base64_file 时使用），相对 payment/ 目录。",
        json_schema_extra={
            "label": "Base64 文件名",
            "hint": "把 base64 粘贴到 payment/ 目录下的 .txt 文件，这里只填文件名（如 wechat.txt）。",
            "order": 4,
        },
    )
    caption: str = Field(
        default="这是麦麦的赞助/收款码，感谢你的支持~",
        description="本平台收款码的附带文案（发码时一并发出的文字，留空则只发图片）。",
        json_schema_extra={"label": "收款码文案", "hint": "可写「这是麦麦的微信收款码~」之类；留空只发图片。", "order": 5},
    )


class PaymentCodesSection(PluginConfigBase):
    """面向 WebUI 的简化多平台收款码配置。"""

    __ui_label__: ClassVar[str] = "收款码"
    __ui_order__: ClassVar[int] = 3

    enabled_platforms: List[Literal["wechat", "alipay", "qq", "custom"]] = Field(
        default_factory=list,
        description="需要启用的收款平台，可多选。",
        json_schema_extra={
            "label": "启用的收款平台",
            "hint": "选择已准备好收款码文件的平台；支持同时启用多个。",
            "order": 0,
        },
    )
    default_platform: Literal["wechat", "alipay", "qq", "custom"] = Field(
        default="wechat",
        description="工具未指定平台时使用的默认收款平台。",
        json_schema_extra={
            "label": "默认收款平台",
            "hint": "请从上方已启用的平台中选择一个。",
            "order": 1,
        },
    )
    wechat_file: str = Field(
        default="wechat.png",
        description="微信收款码文件，支持图片或存放裸 Base64 的 txt 文件。",
        json_schema_extra={
            "label": "微信收款码文件",
            "hint": "把文件放进插件 payment 目录，通常保持默认 wechat.png 即可。也可填绝对路径。",
            "placeholder": "wechat.png",
            "order": 2,
        },
    )
    alipay_file: str = Field(
        default="alipay.png",
        description="支付宝收款码文件，支持图片或存放裸 Base64 的 txt 文件。",
        json_schema_extra={
            "label": "支付宝收款码文件",
            "hint": "把文件放进插件 payment 目录，通常保持默认 alipay.png 即可。也可填绝对路径。",
            "placeholder": "alipay.png",
            "order": 3,
        },
    )
    qq_file: str = Field(
        default="qq.png",
        description="QQ 收款码文件，支持图片或存放裸 Base64 的 txt 文件。",
        json_schema_extra={
            "label": "QQ 收款码文件",
            "hint": "把文件放进插件 payment 目录，通常保持默认 qq.png 即可。也可填绝对路径。",
            "placeholder": "qq.png",
            "order": 4,
        },
    )
    custom_platform: str = Field(
        default="custom",
        description="自定义收款平台标识。",
        json_schema_extra={
            "label": "自定义平台标识",
            "hint": "仅启用 custom 时使用，例如 unionpay。请使用简短英文标识。",
            "placeholder": "custom",
            "order": 5,
        },
    )
    custom_file: str = Field(
        default="custom.png",
        description="自定义平台收款码文件，支持图片或存放裸 Base64 的 txt 文件。",
        json_schema_extra={
            "label": "自定义平台收款码文件",
            "hint": "把文件放进插件 payment 目录，或填写绝对路径。",
            "placeholder": "custom.png",
            "order": 6,
        },
    )
    caption: str = Field(
        default="这是麦麦的赞助/收款码，感谢你的支持~",
        description="发送任一平台收款码时附带的统一文案。",
        json_schema_extra={
            "label": "收款码附带文案",
            "hint": "所有平台共用；留空则只发送图片。",
            "order": 7,
        },
    )
    codes: List[PaymentCode] = Field(
        default_factory=list,
        description="旧版平台收款码列表，仅用于兼容已有配置。",
        json_schema_extra={"label": "旧版收款码配置", "hidden": True, "order": 99},
    )


class PromptConfig(PluginConfigBase):
    """提示词。"""

    __ui_label__: ClassVar[str] = "提示词"
    __ui_order__: ClassVar[int] = 4

    campaign: str = Field(
        default="vw50",
        description="赞助活动代号，用于提示词占位与日志。",
        json_schema_extra={"label": "活动代号", "order": 0},
    )
    planner_prompt: str = Field(
        default=DEFAULT_PLANNER_PROMPT,
        description="注入给 planner 的提示词模板，支持 {campaign}/{attempt}/{platforms} 占位符。",
        json_schema_extra={"label": "Planner 提示词", "input_type": "textarea", "order": 1},
    )
    detect_keywords: List[str] = Field(
        default_factory=lambda: list(DEFAULT_DETECT_KEYWORDS),
        description="检测文案是否已发出的关键词，命中任一即视为今天 vw50 文案已发（仅在触发窗口内检测）。",
        json_schema_extra={"label": "文案检测关键词", "order": 2},
    )


class QuotesConfig(PluginConfigBase):
    """历史语录：收集群友发的疯狂星期四文案，触发时概率性作为参考。"""

    __ui_label__: ClassVar[str] = "历史语录"
    __ui_order__: ClassVar[int] = 5

    enabled: bool = Field(
        default=True,
        description="是否收集群友的疯狂星期四文案作为历史语录。",
        json_schema_extra={"label": "启用语录收集", "order": 0},
    )
    use_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="触发时选取一条历史语录作为参考的概率（0~1）。",
        json_schema_extra={"label": "使用概率", "hint": "0=从不参考，1=每次都参考。参考语录可借鉴风格/改写或原样发出。", "order": 1, "step": 0.1},
    )
    max_count: int = Field(
        default=200,
        ge=1,
        description="语录库最多保留多少条（超出删最早的）。",
        json_schema_extra={"label": "最大条数", "order": 2, "step": 1},
    )
    min_length: int = Field(
        default=10,
        ge=1,
        description="群友消息至少多少字才收录为语录（避免只收录「V我50」三个字）。",
        json_schema_extra={"label": "最小字数", "order": 3, "step": 1},
    )
    match_keywords: List[str] = Field(
        default_factory=lambda: ["V我50", "v我50", "V50", "v50", "疯狂星期四", "V你50", "v你50"],
        description="群友消息命中这些关键词才收录为语录。",
        json_schema_extra={"label": "收录关键词", "order": 4},
    )


class ToolConfig(PluginConfigBase):
    """收款码工具开关。"""

    __ui_label__: ClassVar[str] = "工具"
    __ui_order__: ClassVar[int] = 6

    enabled: bool = Field(
        default=True,
        description="是否启用 send_payment_code 工具（模型/群友经 AI 调用发收款码；需模型支持工具调用）。",
        json_schema_extra={"label": "启用收款码工具", "order": 0},
    )


class Vw50Settings(PluginConfigBase):
    """麦麦 vw50 赞助插件完整配置。"""

    plugin: PluginOptions = Field(default_factory=PluginOptions)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    target: TargetConfig = Field(default_factory=TargetConfig)
    payments: PaymentCodesSection = Field(default_factory=PaymentCodesSection)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    quotes: QuotesConfig = Field(default_factory=QuotesConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)
