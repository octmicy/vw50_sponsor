# 麦麦 vw50 插件

作者：octmicy

「vw50」= 肯德基疯狂星期四「V我50」梗。每周四 11:00 后，自动让麦麦生成一条抽象无厘头的「疯狂星期四 V我50」文案，并要求它自己调用 `send_payment_code` 工具把收款码发给群友；若没发，每 30 分钟提醒一次直至发送成功。另外支持管理员用指令录入群友赞助，让麦麦公开感谢。

注意：若收款码被受限此插件概不负责

## 功能

- **定时触发**：每周四（疯狂星期四，可配置）11:00 后，自动给 planner 注入「网络抽象文案大师」提示词，生成「V我50」风格文案，并要求模型调用 `send_payment_code` 工具发收款码。
- **角色化提示词**：planner + replyer 双 Hook 注入「网络抽象文案大师」角色与任务；要求越抽象越好、结尾必须出现「V我50」、转折要极其生硬（反差越大越好）、字数不超过 100 字。
- **模型自调工具发码**：自动流程完全依赖模型自己调 `send_payment_code`（需模型支持工具调用）。
- **发送检测与重试**：触发后 2h 窗口内，出站消息命中 `detect_keywords` 即视为当天 vw50 文案已发；收款码工具调用不作为完成信号。未发送则每 30 分钟（可配置）重试一次。
- **赞助录入**：管理员发送 `/vw50 金额 @成员` 记录一次赞助（同一人多次累加），麦麦会在当前会话公开感谢该赞助者。
- **上下文感知**：每次提醒带上「这是今天第 N 次」的上下文；状态持久化到 `vw50_state.json`，重启不丢失。
- **非目标日静默**：其余时间每天低频巡检两次是否到了目标日，非周四插件静默不发消息。
- **只需填群号**：目标群填 QQ 群号即可，插件自动解析成内部 stream_id，无需手动查 stream_id。
- **收款码两种来源**：推荐把本地图片直接放进 `payment/`，也兼容把 Base64 存到独立 `.txt` 文件；不要把大字符串填进 config 或 WebUI。
- **多平台收款码**：支持微信/支付宝/QQ/自定义平台，`send_payment_code` 工具可选 `payment_platform` 参数指定要哪种。
- **收款码工具**：`send_payment_code` 既供自动流程调用，也可供群友经 AI 主动索要；文案完成判定与工具调用相互独立。
- **赞助查询工具**：`query_sponsor_amount` 让 planner 按昵称/QQ 号查某人的累计赞助金额与次数，不传参数则返回总览与金额排行榜。

## 安装

把整个 `vw50_sponsor` 文件夹放到 MaiBot 的插件目录 `<模块根>/plugins/vw50_sponsor/`（Windows OneKey 版为
`%APPDATA%\MaiBotOneKeyDesktop\<实例哈希>\modules\MaiBot\plugins\`），重启 MaiBot 或在 WebUI 重载插件即可。

> `config.toml` 不在仓库里（它是运行时的本地配置）。首次加载时 Runner 会按 `config_model`
> 自动生成；也可以先把 `config.example.toml` 复制成 `config.toml` 再改。

## 配置

配置由 MaiBot 的 Runner 依据 `plugin.py` 的 `config_model` 自动生成与维护，实际生效的是插件目录下的
`config.toml`（**不在版本控制内**，见 `.gitignore`）。仓库里的 `config.example.toml` 只是字段说明模板，
可复制一份改名为 `config.toml` 后再改，也可以直接在 WebUI 的插件配置页编辑。

核心三步：

1. **目标群**：在 `[target]` 的 `group_ids` 填上 QQ 群号，如 `group_ids = ["123456789", "987654321"]`。
2. **管理员**：在 `[plugin]` 的 `admin_qq_ids` 填上你的 QQ 号，如 `admin_qq_ids = ["123456789"]`。
   **留空则所有管理命令一律拒绝**（fail-closed）。
3. **收款码（WebUI 直接配置）**：把图片放进 `payment/` 目录，在“启用的收款平台”中多选平台，再用“默认收款平台”下拉框选择默认项。默认文件名为 `wechat.png`、`alipay.png`、`qq.png`，通常无需修改。
   - planner/replyer 会看到已配置的平台列表，群友指定某个平台时模型可传对应 `payment_platform` 参数。
4. **总开关**：`[plugin] enabled = true`。

其余参数（目标星期、触发时辰、重试间隔、提示词模板、赞助录入等）均有合理默认值，按需调整。

### 关键配置项说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `plugin.admin_qq_ids` | `[]` | 插件管理员 QQ 号列表；**留空则所有管理命令一律拒绝**，必须填 |
| `target.group_ids` | `[]` | 目标 QQ 群号列表，直接填群号 |
| `schedule.target_weekday` | `3`（周四） | 0=周一 … 6=周日 |
| `schedule.trigger_hour` | `11` | 到达该小时后才触发（系统本地时间） |
| `schedule.retry_interval_min` | `30` | 未发送时重试间隔 |
| `prompt.planner_prompt` | 见 config.example.toml | 注入给 planner 的「网络抽象文案大师」提示词模板（支持 `{campaign}`/`{attempt}`/`{platforms}`） |
| `payments.enabled_platforms` | `[]` | WebUI 多选框：启用微信、支付宝、QQ 或自定义平台 |
| `payments.default_platform` | `wechat` | WebUI 下拉框：未指定平台时发送哪个收款码 |
| `payments.*_file` | `*.png` | 对应平台的图片或 Base64 文本文件名，默认从 `payment/` 目录读取 |
| `payments.caption` | 见 config.example.toml | 所有平台共用的收款码附带文案，留空则只发图片 |
| `sponsor.enabled` | `true` | 是否启用 `/vw50 金额 @成员` 赞助录入 |
| `sponsor.max_amount` | `100000` | 单次赞助金额上限（元） |
| `sponsor.group_only` | `false` | 是否仅允许群聊录入 |
| `sponsor.thanks_ttl_sec` | `120` | 感谢注入有效期（秒） |
| `sponsor.repeat_trigger_interval_sec` | `60` | 同一人重复录入的最小发言间隔（秒） |
| `sponsor.query_tool_enabled` | `true` | 是否给 planner 提供 `query_sponsor_amount` 查询工具 |
| `tool.enabled` | `true` | 收款码工具开关（需模型支持工具调用） |

### 关于群号解析

插件用 `chat.get_stream_by_group_id` 把群号解析成内部 stream_id。**前提**：麦麦必须是该群成员，且该群在 MaiBot 里已有聊天流（群里收到过消息）。若解析不到（群不存在/未入群/无聊天流），插件会跳过该群并在 debug 日志记录，不会报错。

> 提示：群号填数字字符串即可，如 `"123456789"`。多个群都填进数组。

## 多平台收款码

无需编辑对象列表。打开插件 WebUI 的“收款码”区域，按以下步骤配置：

1. 在“启用的收款平台”多选框中选择微信、支付宝、QQ 或自定义平台。
2. 在“默认收款平台”下拉框中选择工具未指定平台时使用的收款码。
3. 把收款码图片放进插件的 `payment/` 目录；使用默认文件名时无需再改路径。
4. 如需使用其他文件名，在对应平台的文件输入框中修改即可。

等价的简化 TOML 配置如下：

```toml
[payments]
enabled_platforms = ["wechat", "alipay"]
default_platform = "wechat"
wechat_file = "wechat.png"
alipay_file = "alipay.png"
qq_file = "qq.png"
custom_platform = "custom"
custom_file = "custom.png"
caption = "这是麦麦的赞助/收款码，感谢你的支持~"
```

- `send_payment_code` 工具不传 `payment_platform` 时发送默认平台；传 `wechat`、`alipay`、`qq` 时发送对应平台，也识别“微信”“支付宝”等常见别名。
- 推荐直接使用图片文件。若使用 Base64，请放进独立 `.txt` 文件并在对应文件输入框填写文件名，不要把大段 Base64 直接粘贴到 WebUI。
- 旧版 `[[payments.codes]]` 配置仍可读取；当“启用的收款平台”为空时，插件会自动回退到旧配置。
- planner/replyer 提示词会看到已配置的平台列表（`{platforms}` 占位符），群友指定某个平台时模型可在工具调用里传对应 `payment_platform` 参数。

## 运行机制

```
后台调度循环（自适应睡眠）
  ├─ 非目标日 -> 静默，每天约检测两次是否到了目标日
  └─ 目标日 ≥ 11:00 -> 对每个目标群：
       1.群号 -> stream_id 解析（带缓存）
       2.武装 + maisaka.proactive.trigger(stream_id, intent="vw50_sponsor", reason)
       3.planner.before_request Hook：session_id 匹配且在窗口内 -> 注入 system 提示词
         （MaiBot 1.2.0+ 走 Context Items；旧版主程序回退为 messages 前置）
         （「网络抽象文案大师」角色与任务 + 调工具发码要求）
       4.replyer.before_request Hook：仅对紧随其后的首个 replyer 请求追加文案要求与工具提醒
       5.麦麦生成文案 + 调用 send_payment_code 工具发送收款码
       6.send_service.after_send 确认消息发送成功且命中文案关键词后，标记当天完成
       7.文案未成功发送则 30 分钟后重试；收款码工具调用不影响文案完成判定
```

## 赞助录入

管理员在群里（或私聊）发送：

```
/vw50 50 @群友甲
```

执行过程：

1. 校验管理员身份、金额（`0 < 金额 ≤ sponsor.max_amount`）与 @ 目标；
2. 追加一条赞助明细（同一人多次赞助会累加，不覆盖）；
3. **拦截该消息**，不进入 AI 处理链；
4. 挂一个有效期为 `sponsor.thanks_ttl_sec` 的"待感谢"状态；
5. 调 `maisaka.proactive.trigger` 把这一轮推到 planner 发言；
6. 麦麦生成回复时，`replyer.before_request` 把**强硬的感谢要求**追加进 `extra_prompt`；
7. 消息发出成功后清理待感谢状态（replyer 重试期间会一直保留要求）。

说明：

- 感谢话术由 replyer 生成，模板见 `[sponsor] prompt_template`，支持 `{name}`/`{amount}`/`{times}`/`{total}`。
- 不要求模型写 @：replyer 的输出会被主程序当作纯文本发送，写 @ 也不会变成真实提及。
- 同一人 `sponsor.repeat_trigger_interval_sec` 秒内重复录入，只记明细不再触发发言，避免连刷。
- 赞助明细保存在统一持久化目录的 `vw50_sponsors.json`（见下）。

## 赞助查询工具

插件向 planner 暴露一个工具 `query_sponsor_amount`（可用 `sponsor.query_tool_enabled` 关闭）：

| 调用方式 | 返回 |
|---|---|
| `query_sponsor_amount(sponsor="群友甲")` | 该人的累计次数、累计金额、最近一次时间、按金额排名 |
| `query_sponsor_amount(sponsor="123456789")` | 按 QQ 号精确查询 |
| `query_sponsor_amount()` | 赞助总览（人数/人次/总额）+ 金额最高的前 5 位 |

匹配顺序：QQ 号精确 → 昵称精确 → 昵称/QQ 号子串。子串命中多人时会列出候选，让模型用更精确的昵称或 QQ 号再查。

数据来自 `vw50_sponsors.json` 的明细按 `user_id` 聚合，所以同一人多次赞助会被正确累加。

## 调试命令

| 命令 | 作用 |
|------|------|
| `/vw50 <金额> @成员` | 录入一次赞助并让麦麦公开感谢 |
| `/vw50_test` | 在当前群/会话手动触发一次周四流程（忽略日期/时间限制） |
| `/vw50_status` | 查看插件当日状态（含赞助记录条数） |

> 以上管理命令均需在 `plugin.admin_qq_ids` 里配置的 QQ 号才能使用；非管理员会提示无权。
> **管理员列表为空时所有管理命令一律拒绝**（fail-closed），不会退化成"不校验"。
>
> `/vw50_test` 在命令所在的会话里触发，不需要该群在 `group_ids` 里，方便随时测试。

## 注意事项

- 插件使用系统本地时间判断星期与时辰，请确保 MaiBot 运行机器时区正确（中国用户一般为 UTC+8）。
- **不要把 Base64 直接填进 config 或 WebUI**。推荐把图片放进 `payment/`；若使用 Base64，则放在独立 `.txt` 文件中，插件会自动清洗内容。
- 自动流程完全依赖模型调工具发码；若模型调不动（如弱模型把工具名当文本输出），需升级模型或换强模型，插件不做兜底。
- `send_payment_code` 工具可在任何时候调用：不传 `payment_platform` 发默认码，传 `wechat`/`alipay` 等发对应平台码。
- 状态文件 `vw50_state.json`、赞助明细 `vw50_sponsors.json` 保存在统一持久化目录
  `<模块根>/data/plugins/<插件 id>/`（运行时即 `ctx.paths.data_dir`）；旧版本放在插件目录的文件会在首次读写时自动复制过去。

## 依赖

- MaiBot Plugin SDK ≥ 2.5.0
- 无第三方 Python 依赖（仅标准库 + SDK）

## 许可证

MIT
