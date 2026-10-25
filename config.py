"""麦麦 vw50 插件配置模型。

配置结构：
  [plugin]    插件总开关与配置版本
  [schedule]  调度参数（目标星期、触发时辰、重试间隔、巡检节奏）
  [target]    目标 QQ 群号列表
  [payments]  WebUI 多平台收款码选择、文件名与附带文案
  [prompt]    注入给 planner 的提示词模板
  [sponsor]   赞助录入（/vw50 金额 @成员）与感谢注入
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


# 注入给 replyer 的"感谢赞助"要求。支持 {name}/{amount}/{times}/{total} 占位符。
# 说明：replyer 的输出会被主程序整体当作纯文本发送，写 @ 不会变成真实提及，
# 所以这里不要求模型输出 @，只要求"公开感谢"。
DEFAULT_SPONSOR_PROMPT = (
    "【本轮最高优先级 · 必须执行 · 不得省略】\n"
    "群友「{name}」刚刚真实赞助了你 {amount} 元（累计第 {times} 次 / 共 {total} 元）。\n"
    "号主已确认到账，并要求你在这条回复里公开感谢他。\n\n"
    "硬性要求：\n"
    "1. 这条回复必须是感谢，不许答非所问、不许敷衍、不许只发表情；\n"
    "2. 语气真诚、符合你平时的人设，不要客服腔；可以带一点「V我50」的梗，但感谢是主体；\n"
    "3. 只发一次，不要重复刷屏；不要输出括号说明、动作描写或解释。"
)


# proactive.trigger 的 intent：会作为 <plugin_proactive_task> 进入 chat_history，
# planner 与 replyer 都能看到，用来把这一轮"推到发言"。
DEFAULT_SPONSOR_INTENT = (
    "号主刚刚用管理指令录入了群友「{name}」赞助 {amount} 元（累计第 {times} 次）。"
    "这是一次真实的赞助，需要你现在公开感谢他。请立刻决定发言并调用 reply，"
    "不要 no_reply，不要等待更合适的时机。"
)


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
        description="插件管理员 QQ 号列表。只有这些人能执行管理命令（赞助录入、测试、状态）；留空则全部拒绝。",
        json_schema_extra={
            "label": "管理员 QQ 号",
            "hint": "必填，填 QQ 号，如 [\"123456789\"]。留空则所有管理命令一律拒绝（fail-closed）。",
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


class SponsorConfig(PluginConfigBase):
    """赞助录入：/vw50 金额 @成员，把感谢要求注入 replyer。"""

    __ui_label__: ClassVar[str] = "赞助录入"
    __ui_order__: ClassVar[int] = 5

    enabled: bool = Field(
        default=True,
        description="是否启用 /vw50 赞助录入指令。",
        json_schema_extra={"label": "启用赞助录入", "order": 0},
    )
    max_amount: float = Field(
        default=100000.0,
        gt=0,
        description="单次赞助金额上限（元），超过则拒绝录入。",
        json_schema_extra={"label": "金额上限（元）", "order": 1, "step": 1},
    )
    group_only: bool = Field(
        default=False,
        description="是否仅允许在群聊中录入。开启后私聊会被拒绝。",
        json_schema_extra={"label": "仅限群聊", "order": 2},
    )
    thanks_ttl_sec: float = Field(
        default=120.0,
        gt=0,
        description="感谢注入的有效期（秒）。超时未发出则作废。",
        json_schema_extra={"label": "感谢注入有效期（秒）", "order": 3, "step": 1},
    )
    repeat_trigger_interval_sec: float = Field(
        default=60.0,
        ge=0,
        description="同一人重复录入时，距离上次触发发言不足该秒数则只记明细、不再触发发言。",
        json_schema_extra={"label": "重复录入发言间隔（秒）", "hint": "避免同一人连刷导致连续感谢刷屏；0=每次都触发。", "order": 4, "step": 1},
    )
    query_tool_enabled: bool = Field(
        default=True,
        description="是否向 planner 提供 query_sponsor_amount 工具（查询赞助人累计金额与排行榜）。",
        json_schema_extra={"label": "启用赞助查询工具", "order": 5},
    )
    prompt_template: str = Field(
        default=DEFAULT_SPONSOR_PROMPT,
        description="注入给 replyer 的感谢要求模板，支持 {name}/{amount}/{times}/{total} 占位符。",
        json_schema_extra={"label": "Replyer 感谢提示词", "input_type": "textarea", "order": 6},
    )
    intent_template: str = Field(
        default=DEFAULT_SPONSOR_INTENT,
        description="proactive.trigger 的 intent 模板，支持 {name}/{amount}/{times}/{total} 占位符。",
        json_schema_extra={"label": "主动触发 intent", "input_type": "textarea", "order": 7},
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
    sponsor: SponsorConfig = Field(default_factory=SponsorConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)
