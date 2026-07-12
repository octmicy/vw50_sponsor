# Changelog

## [2.0.1] - 2026-07-11

### 修复
- 修复 SDK 2.7 已自动拆包 `chat.get_stream_by_group_id` 返回值，而插件仍按旧版 `{success, stream}` 外层结构解析，导致明明存在的目标群始终被误判为“暂无聊天流”、定时任务永远无法触发的问题。
- 群号解析同时兼容 SDK 2.7 的直接 stream 字典和旧 SDK 的包装结构，并在解析成功时记录明确日志。
- 配置热更新时重新读取持久化状态，确保手动修复或重置 `vw50_state.json` 后无需重启整个 MaiBot。

## [2.0.0] - 2026-07-11

### 新增
- 收款码配置改为 WebUI 多选平台、默认平台下拉框和各平台文件名输入框；保留旧版 `[[payments.codes]]` 回退兼容。
- 调度、触发时间、语录概率和数量配置增加有效范围校验，避免非法值导致插件静默失效。

### 修复
- 调度循环遇到单次异常后会等待 5 秒继续运行，不再永久退出。
- 主动任务入队失败时清理 Hook 武装和检测窗口，并撤销本次重试时间，允许后续尽快重试。
- replyer 提示改为 planner 注入后的单次短窗口，不再污染同一群后续两小时的普通聊天。
- 文案完成判定改用 `send_service.after_send` 且要求 `sent=true`，不再把“消息构建成功但实际发送失败”误判为完成。
- 配置不可用时管理员校验改为拒绝执行，避免管理命令意外失去权限保护。
- 修复语录关键词大小写匹配、损坏状态字段容错和收款码冷却倒计时显示 0 秒的问题。
- 工具平台参数改为 `payment_platform`，避免与 Host 自动注入的聊天平台 `platform="qq"` 冲突；微信/支付宝等旧参数仍兼容。

## [1.9.0] - 2026-07-09

### 新增
- **插件管理员**：`[plugin].admin_qq_ids` 配置管理员 QQ 号列表。
  - 管理命令（`/vw50_test` `/vw50_status` `/vw50_quotes` `/vw50_quote_add` `/vw50_quote_edit` `/vw50_quote_del`）仅管理员可执行。
  - 非管理员调用会提示「你不是插件管理员，无权使用此命令」。
  - `admin_qq_ids` 留空时不校验（兼容旧配置，不推荐生产使用）。
  - 从 Command kwargs 的 `user_id` 取调用者 QQ 号做校验。

## [1.8.0] - 2026-07-09

### 新增
- **语录库管理命令**：可在群里直接查看/添加/修改/删除从群友收集的 vw50 文案。
  - `/vw50_quotes [页码]` — 分页展示语录（每页 10 条，带序号）
  - `/vw50_quote_add <文案>` — 手动添加（自动去重）
  - `/vw50_quote_edit <序号> <新文案>` — 修改指定序号
  - `/vw50_quote_del <序号>` — 删除指定序号
- `/vw50_status` 增加语录库条数展示。
- 抽出 `_save_quotes` / `_format_quotes_list`，方便命令与自动收录共用落盘逻辑。

## [1.7.0] - 2026-07-09

### 变更
- **统一收款码配置**：删除原 `[payment]` 单一收款码节（与 `[[payments.codes]]` 冲突），只保留 `[[payments.codes]]` 分平台列表。旧 `[payment]` 配置需迁移到 `[[payments.codes]]` 条目。
- **默认收款码标记**：`PaymentCode` 新增 `is_default` 字段，列表里某条设 `is_default = true` 即为默认码（工具不传 `platform` 时发这条）。没有标记默认的则用列表第一条。
- **提示词可见平台列表**：planner 提示词新增 `{platforms}` 占位符，运行时替换成已配置的平台列表（如「wechat、alipay」）；replyer Hook 同步把可用平台告诉模型，群友指定某平台时模型可在工具调用里传对应 `platform` 参数。
- 重构收款码查找：抽出 `_find_code(settings, platform)`（按 platform 匹配，空则取 is_default 或第一条）+ `_available_platforms(settings)`；`_load_payment_code_base64` 与 `_caption_for` 都基于它。

### 迁移指南（从 1.6）
- 把原 `[payment]` 节的内容迁到一条 `[[payments.codes]]`，给它加 `platform = "wechat"`（或其他名）和 `is_default = true`。
- 删除 `[payment]` 整节。

## [1.6.0] - 2026-07-09

### 新增
- **多平台收款码**：新增 `[[payments.codes]]` 列表配置，每条对应一个平台（微信/支付宝/自定义），含 `platform`/`source`/`file_path`/`base64_file`/`caption` 五个字段。
- `send_payment_code` 工具新增可选 `platform` 参数（如 `wechat`/`alipay`）：指定时从 `[[payments.codes]]` 匹配对应平台收款码；不传则发 `[payment]` 默认收款码。
- 平台专属 `caption` 留空时自动回退到 `[payment].caption`。

### 变更
- 重构收款码读取：抽出 `_read_code_from_config(code)` 统一处理单个收款码配置；`_load_payment_code_base64(settings, platform)` 按 platform 选配置；新增 `_caption_for(settings, platform)` 取对应文案。
- 工具日志带 platform 标识，便于排查。
- config.toml 加 `[[payments.codes]]` 微信/支付宝示例条目（取消注释配置文件后启用）。

## [1.5.0] - 2026-07-09

### 修复
- **WebUI 加载失败**：把 base64 字符串直接填到 config 或 webui 会撑爆 toml/JSON 并导致 webui 渲染崩溃。改用新方案 `source="base64_file"`：base64 内容存到独立 .txt 文件（如 `payment/code.txt`），config 字段只填短文件名，从根本上避免大字符串进 toml/webui。

### 变更
- 新增 `source="base64_file"` 模式 + 配置项 `[payment].base64_file`（默认 `code.txt`），插件从 `payment/<base64_file>` 读出 base64 内容（自动清洗空白/换行/data URI 前缀）。
- **删除** 原 `[payment].base64` 字段（`input_type: textarea` 是 webui 渲染崩溃的元凶）。已有 base64 内容请改存到 `payment/code.txt`。
- 默认 `source` 改为 `"base64_file"`，新用户开箱即用安全方式。
- `payment/说明.txt` 更新使用指引。
- 未知 source 改为直接返回空串+警告，不再做危险回退。

## [1.4.0] - 2026-07-09

### 变更
- **收款码附带文案改为通用版**：`[payment].caption` 默认值从 `"V我50，请我吃肯德基疯狂星期四~🍔"` 改为 `"这是麦麦的赞助/收款码，感谢你的支持~"`。原因：收款码不只在 vw50 场景发送（群友主动索要、打赏等都可能），文案应保持中性，不绑死 V我50/疯狂星期四 等特定场景。`hint` 也同步调整。

## [1.3.0] - 2026-07-09

### 变更
- **文案与收款码解耦**：用户需求：周四 11 点后只要发过一次「V我50/疯狂星期四」相关文案即视为当天 vw50 完成；收款码由模型/群友通过 `send_payment_code` 工具完全自由调用，**不再受窗口/标记影响**，不会抑制周四自动触发。
- **新增文案检测 hook**：`send_service.after_build_message` Hook(OBSERVE)，在触发后 2h 窗口内，检测该流命中 `prompt.detect_keywords` 关键词的消息，命中即标记 `copy_sent` → 调度循环停止重试。窗口限制避免别人/麦麦普通聊天误判。
- **send_payment_code 工具重构**：移除 `in_flow` 概念与 `copy_sent/code_sent` 联动，改为纯全局节流（`_TOOL_COOLDOWN_SEC` 默认 60s）防刷屏；任何时候调用都直接发收款码，不标记 vw50 自动流程完成。
- **scheduler 判定**：只查 `copy_sent`（文案已发）；不再因 `code_sent` 跳过。
- **新增 `prompt.detect_keywords` 配置项**（默认 9 个关键词，含 V我50/疯狂星期四 等），可在 config 自行调整。
- **planner_prompt 提示词措辞微调**：从「收款码会由系统自动附上」恢复为「调用 send_payment_code 工具发收款码」，回到 v1.2 表述（与用户最新指示一致）。

## [1.2.0] - 2026-07-09

### 变更
- **恢复模型自己调用 send_payment_code 工具发收款码**：上版用插件自动附码绕开弱模型 `[send_payment_code]` 文本 bug，但用户希望模型自己调工具。改回：提示词明确「调用工具而不是把工具名当文本打出来」，由模型在 vw50 流程窗口内自己调 `send_payment_code` 发码。弱模型若调不动需自行升级模型/换强模型，插件不再做兜底附码。
- **planner 提示词换成用户自定义的「角色与任务」模板**：扮演「网络抽象文案大师」生成疯狂星期四整活文案；要求越抽象越好；结尾必须出现「V我50」等类似转账50的核心诉求，转折要极其生硬（反差越大越好）；字数不超过 100 字。`{campaign}` 与 `{attempt}` 占位符仍支持。
- replyer 同步注入「角色与任务」模板 + 工具调用提醒，确保最终输出文案符合规范。
- 移除 `[prompt].auto_attach_code` 字段（不再使用自动附码）；[prompt] 段只剩 `campaign` 与 `planner_prompt` 两项。
- 移除 `send_service.after_build_message` Hook 与附码相关代码（`_ATTACH_AFTER_SPEAK_SEC` / `_FALLBACK_DELAY_SEC` / `_bot_spoke` / `_attach_tasks` / `_schedule_attach` / `_delayed_attach` / `_do_attach`）。
- 调度循环判定条件改为「copy_sent 或 code_sent」任一命中即跳过（`code_sent` 命中通常由群友主动索引起，不影响周四触发）。
- 修复 codex 改坏的中文注释（多处被替换为 `?`）。

## [1.1.0] - 2026-07-08

### 修复
- **严重 bug**：弱模型（如 sensenova-flash-lite）把 `[send_payment_code]` 当文本输出到消息里。根因是 MaiBot 工具为 deferred tool，弱模型调不动。改为：自动流程提示词完全不提工具名（并明确禁工具名/方括号、收款码由系统自动附上），由插件自己发码，不再依赖模型调工具。
- 文案质量：提示词 + replyer 双重要求不少于 50 字、抽象无厘头（先讲离奇段子再反转到 V我50）、不乞讨语气。

### 新增
- 自动附收款码：`send_service.after_build_message` Hook 检测到麦麦发出文案后 3 秒自动附收款码图片；90 秒没说话则兜底发完整「caption+收款码」。
- 历史语录：`chat.receive.before_process` Hook 自动收集群友的疯狂星期四文案到 `vw50_quotes.json`；触发时按 `quotes.use_probability` 概率取一条作为参考给模型（可借鉴改写或原样发出）。新增 `[quotes]` 配置节。
- 收款码专用文件夹 `payment/`（`source="file"` 时 `file_path` 留空自动找第一张图）。

### 变更
- 发送检测信号改为「收款码已发出」（插件附码/工具调用/兜底），彻底移除关键词检测，不会被别人/普通聊天误判。
- `send_payment_code` Tool 保留供群友主动索要（需模型支持工具调用），自动流程不再依赖它。

## [1.0.0] - 2026-07-05

### 新增
- 首个版本：麦麦 vw50 赞助插件（「vw50」= 肯德基疯狂星期四「V我50」梗）。
- 每周四（可配置 `schedule.target_weekday`）11:00 后自动触发 planner 生成「疯狂星期四 V我50」文案并附上收款码。
- 上下文注入双 Hook：`planner.before_request` 向 messages 前置 system 提示词；`replyer.before_request` 向 extra_prompt 追加要求。
- 目标群只需填 QQ 群号（`target.group_ids`），插件用 `chat.get_stream_by_group_id` 自动解析成内部 stream_id，带缓存。
- 收款码双来源：本地文件或 config 内联 base64。
- 未发送重试：每 30 分钟（可配置）重试一次，直至发送成功。
- 上下文感知：每次提醒注入「第 N 次」上下文，状态持久化到 `vw50_state.json`，跨重启不丢失。
- 非目标日静默：每天约两次低频巡检是否到了目标日，非目标日不发消息。
- `send_payment_code` 工具：群友想赞助时可经 AI 调用发送收款码。
- 调试命令：`/vw50_test` 手动触发、`/vw50_status` 查看当日状态。
