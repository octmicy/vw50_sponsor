"""麦麦 vw50 插件入口。

「vw50」= 肯德基疯狂星期四「V我50」梗：每周四让群友V你50去吃肯德基。

功能：
  - 每周四（可配置）11:00 后，自动触发 planner 生成「疯狂星期四 V我50」抽象文案，
    并要求模型自己调用 send_payment_code 工具发送收款码。
  - 文案完成判定：触发后 2h 窗口内，出站消息命中 detect_keywords 即视为当天完成；
    收款码工具可随时调用，不作为完成判定、不抑制周四自动触发。
  - 未发送则每 30 分钟（可配置）重试一次，直至文案已发。
  - 非目标日每天低频巡检两次是否到了目标日，非目标日静默。
  - 赞助录入：管理员发送「/vw50 金额 @成员」记录一次赞助（同一人多次累加），
    并让麦麦在当前会话公开感谢该赞助者。
  - 提供 send_payment_code 工具：支持多平台（wechat/alipay/qq 等），可选 payment_platform 参数；
    不传则发送 WebUI 中选择的默认平台收款码。
  - 收款码通过 WebUI 多选平台、下拉默认项并填写文件名；旧版 [[payments.codes]] 仍兼容。
  - 目标群只需填 QQ 群号，插件用 chat.get_stream_by_group_id 自动解析成 stream_id。

调度采用后台 asyncio 任务；周四流程的上下文注入通过 maisaka.planner.before_request
注入 Context Items（MaiBot 1.2.0+ 的 Item-first 契约；旧版主程序回退为向
messages 前置 system 消息），文案要求走 replyer.before_request 的 extra_prompt。
赞助感谢的注入点在 replyer.before_request（真正写文案的组件），配合
maisaka.proactive.trigger 把这一轮推到 planner 发言。
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import shutil
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, ClassVar, Iterable, Optional

from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import HookMode

from .config import Vw50Settings


def _plugin_dir() -> str:
    """获取插件目录（ctx 没有 plugin_dir 属性，用 __file__）。"""
    return os.path.dirname(os.path.abspath(__file__))


class Vw50SponsorPlugin(MaiBotPlugin):
    """vw50 赞助插件主类。"""

    config_model = Vw50Settings
    # 本插件只关心自身配置热重载，不订阅 bot/model 全局配置变更
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()

    STATE_FILE = "vw50_state.json"
    SPONSORS_FILE = "vw50_sponsors.json"
    # planner.before_request 注入的有效窗口（秒）：仅在此窗口内、且 session_id 匹配才注入
    _ARM_WINDOW_SEC = 120.0
    # 触发后文案检测的有效窗口（秒）：仅在此窗口内、且群内命中「V我50/疯狂星期四」相关关键词，
    # 才视为 vw50 自动流程完成（避免被别人/麦麦普通聊天误判）
    _DETECT_WINDOW_SEC = 7200.0
    # 收款码工具调用的全局节流（秒）：同流连续调用间隔，避免群友反复点导致刷屏
    _TOOL_COOLDOWN_SEC = 60.0

    def __init__(self) -> None:
        super().__init__()
        # 后台调度任务句柄
        self._scheduler_task: Optional[asyncio.Task[None]] = None
        # planner 注入用的「已武装」状态：stream_id -> (触发时间戳, attempt)
        # 本次主动触发后，下一个匹配该流的 planner 请求才注入；多流可同时武装
        self._armed: dict[str, tuple[float, int]] = {}
        # 文案检测窗口：stream_id -> 检测有效截止时间戳。仅在触发后一段时间内，
        # 该流命中「V我50/疯狂星期四」相关关键词才视为 vw50 自动流程完成
        self._detect_until: dict[str, float] = {}
        # replyer 注入是一次性的，只允许紧随 planner 注入后的首个匹配请求使用
        self._replyer_armed: dict[str, float] = {}
        # 赞助感谢：stream_id -> 本轮待感谢的赞助信息（发出成功后清理）
        self._sponsor_pending: dict[str, dict[str, Any]] = {}
        # 收款码工具节流：stream_id -> 上次发送时间戳（避免群友反复点导致刷屏）
        self._tool_last_sent: dict[str, float] = {}
        # group_id -> stream_id 解析缓存，避免每个巡检周期都发起 RPC 查询
        self._stream_cache: dict[str, str] = {}
        # 持久化状态（跨重启，意识到上下文）
        self._state: dict[str, Any] = self._load_state()

    # ── 生命周期 ──────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：启动后台调度任务。"""
        self.ctx.logger.info("[vw50] 插件已加载")
        self._start_scheduler()

    async def on_unload(self) -> None:
        """插件卸载：停止后台调度任务。"""
        self._stop_scheduler()
        self.ctx.logger.info("[vw50] 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        """配置热重载：重启调度任务以应用新配置。"""
        self.ctx.logger.info(f"[vw50] 配置更新: scope={scope}, version={version}")
        if scope == "self":
            self._stop_scheduler()
            self._state = self._load_state()
            self._start_scheduler()

    # ── 状态持久化 ────────────────────────────────────────────────────

    def _data_dir(self) -> str:
        """返回插件统一持久化目录。

        优先用 SDK 2.6.0+ 的 `ctx.paths.data_dir`（`<模块根>/data/plugins/<插件id>/`）；
        拿不到时回落到插件源码目录，兼容旧环境。`__init__` 阶段 ctx 尚未注入，
        所以这里直接读私有字段而不是 `self.ctx` 属性。
        """
        ctx = getattr(self, "_ctx", None)
        paths = getattr(ctx, "paths", None) if ctx is not None else None
        data_dir = getattr(paths, "data_dir", None) if paths is not None else None
        if data_dir:
            try:
                os.makedirs(str(data_dir), exist_ok=True)
                return str(data_dir)
            except Exception:
                pass
        return _plugin_dir()

    def _persist_path(self, filename: str) -> str:
        """返回数据文件的持久化路径；首次使用时把插件目录下的旧文件搬过来。"""
        target_dir = self._data_dir()
        target = os.path.join(target_dir, filename)
        if target_dir != _plugin_dir() and not os.path.exists(target):
            legacy = os.path.join(_plugin_dir(), filename)
            if os.path.exists(legacy):
                try:
                    shutil.copy2(legacy, target)
                except Exception:
                    pass
        return target

    def _state_path(self) -> str:
        return self._persist_path(self.STATE_FILE)

    def _load_state(self) -> dict[str, Any]:
        """读取持久化状态。__init__ 阶段调用，不能用 ctx.logger。"""
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:
        try:
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.ctx.logger.warning(f"[vw50] 状态保存失败: {exc}")

    @staticmethod
    def _today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _ensure_today_state(self) -> None:
        """跨日重置：当系统日期与状态文件中的 date 不一致时，清空当日发送/触发记录。"""
        today = self._today_str()
        if self._state.get("date") != today:
            self._state = {
                "date": today,
                "copy_sent": {},
                "code_sent": {},
                "last_trigger": {},
                "attempt": {},
            }
            self._save_state()
            return
        changed = False
        for key in ("copy_sent", "code_sent", "last_trigger", "attempt"):
            if not isinstance(self._state.get(key), dict):
                self._state[key] = {}
                changed = True
        if changed:
            self._save_state()

    def _is_copy_sent(self, stream_id: str) -> bool:
        return bool(self._state.get("copy_sent", {}).get(stream_id, False))

    def _is_admin(self, user_id: str) -> bool:
        """判断 user_id 是否为插件管理员。

        规则：
          - admin_qq_ids 为空 → 不校验（所有人放行，兼容旧配置）
          - 否则 user_id 必须在 admin_qq_ids 列表中
        """
        try:
            settings = self.config
            admins = [str(x).strip() for x in (settings.plugin.admin_qq_ids or []) if str(x).strip()]
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(f"[vw50] 管理员配置读取失败，拒绝执行管理命令: {exc}")
            return False
        if not admins:
            return True
        return str(user_id or "").strip() in admins

    def _is_admin_strict(self, user_id: str) -> bool:
        """严格管理员判定：admin_qq_ids 为空时拒绝所有人（fail-closed）。

        赞助录入涉及真实金额，不能沿用"留空放行"的宽松策略。
        """
        try:
            settings = self.config
            admins = [str(x).strip() for x in (settings.plugin.admin_qq_ids or []) if str(x).strip()]
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(f"[vw50] 管理员配置读取失败，拒绝执行: {exc}")
            return False
        if not admins:
            return False
        return str(user_id or "").strip() in admins

    async def _deny_if_not_admin(self, kwargs: dict[str, Any]) -> Optional[tuple[bool, str, int]]:
        """非管理员时发送提示并返回 Command 三元组；管理员返回 None。"""
        user_id = str(kwargs.get("user_id") or "")
        if self._is_admin(user_id):
            return None
        stream_id = str(kwargs.get("stream_id") or "")
        if stream_id:
            await self.ctx.send.text("你不是插件管理员，无权使用此命令。", stream_id)
        return False, "非管理员", 2

    def _is_code_sent(self, stream_id: str) -> bool:
        """保留：仅在调试/手动查询场景使用。tool 不再调用此方法。"""
        return bool(self._state.get("code_sent", {}).get(stream_id, False))

    def _mark_sent(
        self,
        stream_id: str,
        *,
        copy_sent: bool = False,
        code_sent: bool = False,
        source: str = "",
    ) -> None:
        """标记某流的发送状态并落盘。"""
        self._ensure_today_state()
        changed = False
        if copy_sent:
            bucket = self._state.setdefault("copy_sent", {})
            if not bucket.get(stream_id):
                bucket[stream_id] = True
                changed = True
        if code_sent:
            bucket = self._state.setdefault("code_sent", {})
            if not bucket.get(stream_id):
                bucket[stream_id] = True
                changed = True
        if changed:
            self._save_state()
            self.ctx.logger.info(f"[vw50] 标记发送状态 stream={stream_id} source={source}")
        if copy_sent:
            self._armed.pop(stream_id, None)
            self._replyer_armed.pop(stream_id, None)
            self._detect_until.pop(stream_id, None)

    # ── 调度任务管理 ──────────────────────────────────────────────────

    def _start_scheduler(self) -> None:
        """启动后台调度循环（若配置启用且当前未运行）。"""
        try:
            settings = self.config
        except RuntimeError:
            self.ctx.logger.warning("[vw50] 配置尚未就绪，跳过启动调度")
            return

        if not settings.plugin.enabled:
            self.ctx.logger.info("[vw50] 插件已关闭（plugin.enabled=false）")
            return

        if self._scheduler_task is not None and not self._scheduler_task.done():
            self.ctx.logger.debug("[vw50] 调度任务已在运行，跳过重复启动")
            return

        self._scheduler_task = asyncio.create_task(self._scheduler_loop(), name="vw50-scheduler")
        self.ctx.logger.info(
            f"[vw50] 调度已启动：目标星期={settings.schedule.target_weekday}，"
            f"触发时辰={settings.schedule.trigger_hour}，重试间隔={settings.schedule.retry_interval_min}min"
        )

    def _stop_scheduler(self) -> None:
        """停止后台调度循环。"""
        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None and not task.done():
            task.cancel()
        self._armed.clear()
        self._replyer_armed.clear()
        self._detect_until.clear()
        self._stream_cache.clear()

    async def _scheduler_loop(self) -> None:
        """调度主循环：自适应睡眠，按日期/时辰触发 vw50 流程。被取消时安静退出。"""
        while True:
            try:
                try:
                    settings = self.config
                except RuntimeError:
                    await asyncio.sleep(5.0)
                    continue

                if not settings.plugin.enabled:
                    self.ctx.logger.info("[vw50] 运行中检测到 enabled=false，停止调度")
                    return

                self._ensure_today_state()
                now = datetime.now()
                target_weekday = settings.schedule.target_weekday

                if now.weekday() != target_weekday:
                    # 非目标日：静默，低频巡检；但不超过「到次日零点后半小时」，确保能及时察觉跨日
                    hours_to_midnight = 24.0 - now.hour - now.minute / 60.0 - now.second / 3600.0
                    sleep_hours = min(
                        max(1.0, float(settings.schedule.non_thursday_check_interval_hours)),
                        hours_to_midnight + 0.5,
                    )
                    self.ctx.logger.debug(
                        f"[vw50] 今天非目标日(weekday={now.weekday()})，静默 {sleep_hours:.1f}h"
                    )
                    await asyncio.sleep(sleep_hours * 3600.0)
                    continue

                # 目标日
                if now.hour < settings.schedule.trigger_hour:
                    # 还没到触发时辰，短睡等待
                    await asyncio.sleep(
                        max(30.0, float(settings.schedule.thursday_poll_interval_sec))
                    )
                    continue

                # 已过触发时辰：逐个目标群解析 stream_id 并检查是否需要触发
                retry_sec = max(60.0, float(settings.schedule.retry_interval_min) * 60.0)
                poll_sec = max(30.0, float(settings.schedule.thursday_poll_interval_sec))
                for group_id in settings.target.group_ids:
                    gid = str(group_id).strip()
                    if not gid:
                        continue
                    sid = await self._resolve_stream_id(gid, settings)
                    if not sid:
                        continue
                    # 已发出文案 = 当天 vw50 完成，跳过。收款码独立，不影响判定。
                    if self._is_copy_sent(sid):
                        continue
                    try:
                        last_trigger = float(
                            self._state.get("last_trigger", {}).get(sid, 0.0) or 0.0
                        )
                    except (TypeError, ValueError):
                        last_trigger = 0.0
                    if last_trigger and (time.time() - last_trigger) < retry_sec:
                        continue
                    await self._trigger_vw50(sid, settings)

                await asyncio.sleep(poll_sec)
            except asyncio.CancelledError:
                self.ctx.logger.debug("[vw50] 调度任务被取消")
                raise
            except Exception:  # noqa: BLE001
                self.ctx.logger.exception("[vw50] 调度循环异常，5 秒后继续运行")
                await asyncio.sleep(5.0)

    # ── vw50 触发流程 ────────────────────────────────────────────────

    async def _resolve_stream_id(self, group_id: str, settings: Vw50Settings) -> str:
        """把 QQ 群号解析成内部 stream_id。命中缓存直接返回；失败返回空串。"""
        cached = self._stream_cache.get(group_id)
        if cached:
            return cached
        platform = (settings.target.platform or "qq").strip() or "qq"
        try:
            result = await self.ctx.chat.get_stream_by_group_id(
                group_id=group_id, platform=platform
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error(f"[vw50] 查询群 {group_id} 的 stream_id 失败: {exc}")
            return ""
        if not isinstance(result, dict):
            self.ctx.logger.debug(
                f"[vw50] 群 {group_id} 暂无聊天流（可能麦麦未入群或未收到消息）"
            )
            return ""
        # SDK 2.7 会把 Host 的 {success, stream} 自动拆包，直接返回 stream 字典；
        # 旧 SDK/测试环境可能仍返回外层包装，因此两种结构都兼容。
        if "success" in result:
            if not result.get("success"):
                self.ctx.logger.warning(
                    f"[vw50] 查询群 {group_id} 的聊天流失败: {result.get('error') or '未知错误'}"
                )
                return ""
            stream = result.get("stream")
        else:
            stream = result
        if not isinstance(stream, dict):
            self.ctx.logger.debug(
                f"[vw50] 群 {group_id} 暂无聊天流（可能麦麦未入群或未收到消息）"
            )
            return ""
        sid = str(stream.get("stream_id") or stream.get("session_id") or "").strip()
        if sid:
            self._stream_cache[group_id] = sid
            self.ctx.logger.info(f"[vw50] 群 {group_id} 已解析 stream_id={sid}")
        return sid

    async def _trigger_vw50(self, stream_id: str, settings: Vw50Settings) -> bool:
        """对单个目标流触发一次 vw50 赞助流程，成功入队返回 True。"""
        self._ensure_today_state()
        attempts = self._state.setdefault("attempt", {})
        try:
            attempt = int(attempts.get(stream_id, 0) or 0) + 1
        except (TypeError, ValueError):
            attempt = 1

        # 武装 planner 注入：仅下一个匹配该 stream 的 planner 请求会被注入
        self._armed[stream_id] = (time.time(), attempt)
        # 打开文案检测窗口：接下来一段时间内，该流命中「V我50/疯狂星期四」相关关键词
        # 即视为 vw50 自动流程完成（只判定文案，不依赖收款码）
        self._detect_until[stream_id] = time.time() + self._DETECT_WINDOW_SEC

        reason = self._build_reason(settings, attempt)
        try:
            result = await self.ctx.maisaka.proactive.trigger(
                stream_id=stream_id,
                intent="vw50_sponsor",
                reason=reason,
            )
            if isinstance(result, dict) and not result.get("success", True):
                raise RuntimeError(str(result.get("error") or "主动任务入队失败"))
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error(f"[vw50] proactive.trigger 失败: {exc}")
            self._armed.pop(stream_id, None)
            self._replyer_armed.pop(stream_id, None)
            self._detect_until.pop(stream_id, None)
            return False
        attempts[stream_id] = attempt
        self._state.setdefault("last_trigger", {})[stream_id] = time.time()
        self._save_state()
        self.ctx.logger.info(
            f"[vw50] 已触发 vw50 流程 stream={stream_id} attempt={attempt}"
        )
        return True

    def _build_reason(self, settings: Vw50Settings, attempt: int) -> str:
        campaign = settings.prompt.campaign or "vw50"
        return (
            f"今天是肯德基疯狂星期四（{campaign}）。请按你的人设和「网络抽象文案大师」角色，"
            f"主动在群里发一条「V我50」疯狂星期四文案，并调用 send_payment_code 工具发收款码"
            f"（不是把工具名当文本打出来）。这是号主希望你今天主动做的事，请直接生成并发送"
            f"（第{attempt}次提醒），不要判断合不合适、不要沉默。"
        )

    def _build_planner_prompt(self, settings: Vw50Settings, attempt: int) -> str:
        campaign = settings.prompt.campaign or "vw50"
        template = settings.prompt.planner_prompt or ""
        # 可用平台列表，供提示词 {platforms} 占位符使用
        platforms = self._available_platforms(settings)
        platforms_str = "、".join(platforms) if platforms else "（未配置任何平台）"
        # 用 replace 而非 str.format，避免提示词中存在的花括号导致解析异常
        return (
            template.replace("{campaign}", str(campaign))
            .replace("{attempt}", str(attempt))
            .replace("{platforms}", platforms_str)
        )

    # ── 赞助记录 ──────────────────────────────────────────────────────

    def _sponsors_path(self) -> str:
        return self._persist_path(self.SPONSORS_FILE)

    def _load_sponsors(self) -> dict[str, Any]:
        """读取赞助明细。结构：{"version": 1, "records": [...]}。"""
        try:
            with open(self._sponsors_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                return data
        except Exception:
            pass
        return {"version": 1, "records": []}

    def _save_sponsors(self, data: dict[str, Any]) -> bool:
        """落盘赞助明细。成功返回 True。"""
        try:
            with open(self._sponsors_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(f"[vw50] 赞助记录保存失败: {exc}")
            return False

    def _add_sponsor(
        self,
        record: dict[str, Any],
        repeat_trigger_interval_sec: float = 0.0,
    ) -> tuple[int, float, bool]:
        """追加一条赞助明细。

        同一人多次赞助按明细累加，不覆盖。

        Returns:
            (该用户累计次数, 该用户累计金额, 是否因间隔过短而跳过本次发言触发)
        """

        data = self._load_sponsors()
        records = data.setdefault("records", [])
        user_id = str(record.get("user_id") or "")

        # 先算"距上次录入"的间隔，再入库，否则会和本次自己比较
        previous_ts = [
            float(item.get("ts") or 0)
            for item in records
            if isinstance(item, dict) and str(item.get("user_id") or "") == user_id
        ]
        last_ts = max(previous_ts) if previous_ts else 0.0
        too_soon = (
            repeat_trigger_interval_sec > 0
            and last_ts > 0
            and (time.time() - last_ts) < repeat_trigger_interval_sec
        )

        records.append(record)
        self._save_sponsors(data)

        same_user = [
            item
            for item in records
            if isinstance(item, dict) and str(item.get("user_id") or "") == user_id
        ]
        times = len(same_user)
        total = round(sum(float(item.get("amount") or 0) for item in same_user), 2)
        return times, total, too_soon

    @staticmethod
    def _render_sponsor_template(
        template: str,
        *,
        name: str,
        amount: float,
        times: int,
        total: float,
    ) -> str:
        """渲染赞助提示词模板（用 replace 而非 format，避免花括号问题）。"""

        return (
            str(template or "")
            .replace("{name}", str(name))
            .replace("{amount}", f"{amount:g}")
            .replace("{times}", str(times))
            .replace("{total}", f"{total:g}")
        )

    # ── 收款码读取 ────────────────────────────────────────────────────

    @staticmethod
    def _payment_dir() -> str:
        """收款码图片专用目录：插件目录下的 payment/。"""
        return os.path.join(_plugin_dir(), "payment")

    def _auto_find_payment_image(self) -> str:
        """在 payment/ 目录里找第一张图片，返回其绝对路径；找不到返回空串。"""
        payment_dir = self._payment_dir()
        if not os.path.isdir(payment_dir):
            return ""
        exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
        try:
            candidates = [
                os.path.join(payment_dir, name)
                for name in os.listdir(payment_dir)
                if os.path.splitext(name)[1].lower() in exts
                and os.path.isfile(os.path.join(payment_dir, name))
            ]
        except Exception:  # noqa: BLE001
            return ""
        if not candidates:
            return ""
        candidates.sort()
        return candidates[0]

    def _read_code_from_config(self, code: Any, fallback_name: str = "默认") -> str:
        """从单个 PaymentCode 配置读出裸 base64。

        source=file 时：
          - file_path 为绝对路径 → 直接用；
          - file_path 为相对路径 → 相对 payment/ 目录解析；
          - file_path 留空 → 自动取 payment/ 目录下第一张图片。

        source=base64_file 时（推荐）：
          - base64_file 为绝对路径 → 直接用；
          - base64_file 为相对路径 → 相对 payment/ 目录解析；
          - 文件内容是裸 base64 字符串（自动去除空白/换行/data URI 前缀）。
        """
        source = (getattr(code, "source", "") or "").strip().lower()
        if source == "auto":
            raw_path = (getattr(code, "code_file", "") or "").strip()
            if not raw_path:
                self.ctx.logger.warning(f"[vw50] {fallback_name}收款码文件名为空")
                return ""
            path = raw_path if os.path.isabs(raw_path) else os.path.join(self._payment_dir(), raw_path)
            if os.path.splitext(path)[1].lower() == ".txt":
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                except Exception as exc:  # noqa: BLE001
                    self.ctx.logger.error(f"[vw50] {fallback_name}收款码 Base64 文件读取失败: {path} ({exc})")
                    return ""
                raw = "".join(raw.split())
                if raw.startswith("data:") and "," in raw:
                    raw = raw.split(",", 1)[1]
                if not raw:
                    self.ctx.logger.warning(f"[vw50] {fallback_name}收款码 Base64 文件为空: {path}")
                return raw
            try:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error(f"[vw50] {fallback_name}收款码图片读取失败: {path} ({exc})")
                return ""
        if source == "file":
            raw_path = (getattr(code, "file_path", "") or "").strip()
            if raw_path and os.path.isabs(raw_path):
                path = raw_path
            elif raw_path:
                path = os.path.join(self._payment_dir(), raw_path)
            else:
                path = self._auto_find_payment_image()
            if not path:
                self.ctx.logger.warning(
                    f"[vw50] {fallback_name}收款码：未找到图片，请放进 payment/ 目录或在 file_path 填文件名"
                )
                return ""
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error(f"[vw50] {fallback_name}收款码读取失败: {path} ({exc})")
                return ""
            return base64.b64encode(data).decode("ascii")
        if source == "base64_file":
            raw_name = (getattr(code, "base64_file", "") or "").strip()
            if not raw_name:
                raw_name = "code.txt"
            if os.path.isabs(raw_name):
                path = raw_name
            else:
                path = os.path.join(self._payment_dir(), raw_name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except FileNotFoundError:
                self.ctx.logger.warning(
                    f"[vw50] {fallback_name}收款码 base64 文件不存在: {path}"
                )
                return ""
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error(f"[vw50] {fallback_name}收款码 base64 文件读取失败: {path} ({exc})")
                return ""
            # 清洗：去除所有空白与换行
            raw = "".join(raw.split())
            if raw.startswith("data:") and "," in raw:
                raw = raw.split(",", 1)[1]
            if not raw:
                self.ctx.logger.warning(f"[vw50] {fallback_name}收款码 base64 文件为空: {path}")
            return raw
        self.ctx.logger.warning(
            f"[vw50] {fallback_name}收款码未知 source={source!r}（应为 file 或 base64_file）"
        )
        return ""

    @staticmethod
    def _normalize_payment_platform(platform: str) -> str:
        """统一常见平台别名，便于模型传中文或缩写。"""
        normalized = (platform or "").strip().lower()
        aliases = {
            "微信": "wechat",
            "微信支付": "wechat",
            "wx": "wechat",
            "wechatpay": "wechat",
            "支付宝": "alipay",
            "zfb": "alipay",
            "qq支付": "qq",
            "qqpay": "qq",
        }
        return aliases.get(normalized, normalized)

    def _configured_payment_codes(self, settings: Vw50Settings) -> list[Any]:
        """优先生成新版简化配置；未配置时回退旧版 codes 列表。"""
        payments = settings.payments
        enabled = [
            self._normalize_payment_platform(str(platform))
            for platform in (payments.enabled_platforms or [])
        ]
        default_platform = self._normalize_payment_platform(payments.default_platform)
        file_fields = {
            "wechat": payments.wechat_file,
            "alipay": payments.alipay_file,
            "qq": payments.qq_file,
            "custom": payments.custom_file,
        }
        simple_codes: list[Any] = []
        for selected in enabled:
            if selected not in file_fields:
                continue
            platform = selected
            if selected == "custom":
                platform = self._normalize_payment_platform(payments.custom_platform) or "custom"
            code_file = (file_fields[selected] or "").strip()
            if not code_file:
                continue
            simple_codes.append(
                SimpleNamespace(
                    platform=platform,
                    is_default=selected == default_platform,
                    source="auto",
                    code_file=code_file,
                    caption=payments.caption,
                )
            )
        return simple_codes or list(payments.codes or [])

    def _find_code(self, settings: Vw50Settings, platform: str = "") -> Any:
        """按 platform 找收款码配置条目。platform 空时返回 is_default=true 的那条；
        没有标记默认的则返回列表第一条；列表空返回 None。"""
        codes = self._configured_payment_codes(settings)
        if not codes:
            return None
        platform = self._normalize_payment_platform(platform)
        if platform:
            for code in codes:
                code_platform = self._normalize_payment_platform(
                    getattr(code, "platform", "") or ""
                )
                if code_platform == platform:
                    return code
            return None
        # 无 platform：优先 is_default=true 的条目
        for code in codes:
            if getattr(code, "is_default", False):
                return code
        return codes[0]

    def _available_platforms(self, settings: Vw50Settings) -> list[str]:
        """返回所有已配置的平台标识列表（供提示词展示给模型）。"""
        return [
            (getattr(c, "platform", "") or "").strip()
            for c in self._configured_payment_codes(settings)
            if (getattr(c, "platform", "") or "").strip()
        ]

    def _load_payment_code_base64(self, settings: Vw50Settings, platform: str = "") -> str:
        """读取收款码，返回裸 base64。platform 非空时按 platform 匹配；空则用默认条目。"""
        code = self._find_code(settings, platform)
        if code is None:
            if platform:
                self.ctx.logger.warning(
                    f"[vw50] 未配置 platform={platform!r} 的收款码"
                )
            else:
                self.ctx.logger.warning(
                    "[vw50] 未配置任何收款码，请在 WebUI 的「收款码」配置中选择平台并填写文件"
                )
            return ""
        tag = (getattr(code, "platform", "") or "默认").strip() or "默认"
        return self._read_code_from_config(code, fallback_name=f"{tag} ")

    def _caption_for(self, settings: Vw50Settings, platform: str = "") -> str:
        """取对应平台（或默认）的附带文案。条目 caption 留空则只发图片。"""
        code = self._find_code(settings, platform)
        if code is None:
            return ""
        return (getattr(code, "caption", "") or "").strip()

    # ── Hook：向 planner 注入 vw50 提示词 ────────────────────────────

    @staticmethod
    def _build_planner_system_item(text: str) -> dict[str, Any]:
        """构造一个最小合法的主程序 SystemMessageItem 快照（Item-first 契约）。

        字段必须与主程序 `deserialize_context_item_snapshot` 的校验一致：
          - `item_type` 固定为 SystemMessageItem；
          - `meta.item_id` 非空，且不得与本次请求已有 Item 重复；
          - `meta.logical_turn_id` 键必须存在（取值可为 null）；
          - `meta.timestamp` 必须是合法 ISO 时间字符串；
          - `parts` 为内容片段列表，文本片段形如 {"type": "text", "text": ...}。
        """
        return {
            "item_type": "SystemMessageItem",
            "meta": {
                "item_id": f"vw50-{uuid.uuid4().hex}",
                "logical_turn_id": None,
                "timestamp": datetime.now().isoformat(),
            },
            "parts": [{"type": "text", "text": text}],
        }

    def _inject_planner_prompt_into_items(self, items: list[Any], prompt: str) -> None:
        """把 planner 提示词注入主程序下发的 Context Items（就地修改）。

        优先把文本并入最后一条 SystemMessageItem 的最后一个文本片段：这样不会新增
        system 消息、也不会让单条消息从「字符串 content」变成「多段 content 列表」，
        对只接受字符串 system content 的 Provider 更安全。没有 SystemMessageItem 时
        在列表最前面插入一个新的。
        """
        for item in reversed(items):
            if not isinstance(item, dict) or item.get("item_type") != "SystemMessageItem":
                continue
            parts = item.get("parts")
            if not isinstance(parts, list) or not parts:
                continue
            last_part = parts[-1]
            if isinstance(last_part, dict) and str(last_part.get("type") or "") == "text":
                last_part["text"] = f"{last_part.get('text') or ''}\n\n{prompt}"
            else:
                parts.append({"type": "text", "text": prompt})
            return
        items.insert(0, self._build_planner_system_item(prompt))

    @HookHandler("maisaka.planner.before_request", mode=HookMode.BLOCKING)
    async def on_planner_before_request(self, **kwargs: Any) -> Optional[dict[str, Any]]:
        """当本插件已武装、且 planner 请求属于目标流且在窗口内时，注入一条 system 提示词。

        注入一次后解除武装，避免对同一流的后续普通 planner 请求重复注入；
        下一次 30 分钟重试会重新武装。

        Hook 契约随主程序版本变化，这里按 kwargs 里实际存在的键双兼容：
          - MaiBot 1.2.0+（Item-first）：提供 `items`（Context Item 列表）+
            `item_schema_version` + `session_id`，不再有 `messages`；
          - 旧版主程序：提供 `messages`（{"role", "content"} 列表）。
        若按固定键取值，主程序升级后注入会静默失效。
        """
        session_id = str(kwargs.get("session_id") or "")
        armed = self._armed.get(session_id)
        if not armed:
            # 该流未武装，放行不修改
            return {"action": "continue"}
        armed_ts, armed_attempt = armed
        if (time.time() - armed_ts) > self._ARM_WINDOW_SEC:
            # 超出窗口，丢弃此次武装
            self._armed.pop(session_id, None)
            return {"action": "continue"}
        try:
            settings = self.config
        except RuntimeError:
            return {"action": "continue"}

        prompt = self._build_planner_prompt(settings, armed_attempt)

        items = kwargs.get("items")
        if isinstance(items, list):
            # 新版主程序（MaiBot 1.2.0+）：注入 Context Items
            self._inject_planner_prompt_into_items(items, prompt)
        elif isinstance(kwargs.get("messages"), list):
            # 旧版主程序：前置一条 system 消息
            kwargs["messages"] = [
                {"role": "system", "content": prompt},
                *kwargs["messages"],
            ]
        else:
            # 既无 items 也无 messages：不消费武装，保留到下一次请求
            self.ctx.logger.warning(
                f"[vw50] planner Hook 未提供 items/messages，保留武装等待下一次 stream={session_id}"
            )
            return {"action": "continue"}

        # 解除武装：本次注入只生效一次
        self._armed.pop(session_id, None)
        self._replyer_armed[session_id] = time.time() + self._ARM_WINDOW_SEC
        self.ctx.logger.debug(
            f"[vw50] 已注入 planner 提示词 stream={session_id} attempt={armed_attempt}"
        )
        return {"action": "continue", "modified_kwargs": kwargs}

    # ── Hook：向 replyer 注入写作要求（vw50 流程 / 赞助感谢）──────────

    @HookHandler("maisaka.replyer.before_request", mode=HookMode.BLOCKING)
    async def on_replyer_before_request(self, **kwargs: Any) -> dict[str, Any]:
        """向 replyer 的 extra_prompt 追加本轮必须满足的写作要求。

        两条互不干扰的注入源：
          1. 赞助感谢：命中本流待感谢记录时注入感谢脚本，保留到发送成功或超时
             （replyer 内部重试时仍能拿到要求）；
          2. vw50 周四流程：planner 注入后的一次性文案要求，用过即清。

        replyer 才是真正生成消息文本的组件，所以两条要求都注入到这里；
        extra_prompt 是追加语义，不覆盖其它插件的内容。
        """
        session_id = str(kwargs.get("session_id") or "")
        try:
            settings = self.config
        except RuntimeError:
            return {"action": "continue"}

        suffixes: list[str] = []

        # ① 赞助感谢（独立于 vw50 流程）
        sponsor_suffix = self._build_sponsor_replyer_suffix(session_id, settings)
        if sponsor_suffix:
            suffixes.append(sponsor_suffix)

        # ② vw50 周四流程（一次性）
        replyer_until = self._replyer_armed.get(session_id, 0.0)
        if replyer_until:
            if time.time() > replyer_until or self._is_copy_sent(session_id):
                self._replyer_armed.pop(session_id, None)
            else:
                suffixes.append(self._build_vw50_replyer_suffix(settings))
                self._replyer_armed.pop(session_id, None)

        if not suffixes:
            return {"action": "continue"}
        kwargs["extra_prompt"] = (kwargs.get("extra_prompt") or "") + "".join(suffixes)
        return {"action": "continue", "modified_kwargs": kwargs}

    def _build_sponsor_replyer_suffix(self, session_id: str, settings: Vw50Settings) -> str:
        """构造赞助感谢要求；没有待感谢记录或已超时返回空串。"""
        pending = self._sponsor_pending.get(session_id)
        if not pending:
            return ""
        if time.time() > float(pending.get("until") or 0):
            self._sponsor_pending.pop(session_id, None)
            return ""
        # 标记已注入：真正发出（after_send sent=true）后才清理，保证 replyer 重试仍带要求
        pending["injected"] = True
        rendered = self._render_sponsor_template(
            settings.sponsor.prompt_template,
            name=str(pending.get("name") or ""),
            amount=float(pending.get("amount") or 0),
            times=int(pending.get("times") or 1),
            total=float(pending.get("total") or 0),
        )
        return "\n" + rendered

    def _build_vw50_replyer_suffix(self, settings: Vw50Settings) -> str:
        """构造 vw50 周四流程的文案要求。"""
        platforms = self._available_platforms(settings)
        platforms_hint = (
            f"可选平台：{('、'.join(platforms))}（不传 payment_platform 则发默认码）。"
            if platforms
            else "（未配置任何收款码平台）"
        )
        return (
            "\n【vw50 流程 · 文案要求】请你扮演「网络抽象文案大师」，"
            "生成一段「疯狂星期四」整活文案：越抽象越好；"
            "结尾必须出现「V我50」等类似转账50的核心诉求，转折要极其生硬（反差越大越好）；"
            "字数不超过 100 字。\n"
            "【发送方式】文案生成后，调用 send_payment_code 工具把你的收款码图片一并发出去"
            "（让群友能V你50）。注意是「调用工具」，不是把工具名当成文本打出来。"
            f"{platforms_hint} 群友若指定要某个平台的码（如「微信」「支付宝」），"
            "请在工具调用里传对应 payment_platform 参数。\n"
            "务必发送，不要判断合不合适、不要沉默。"
        )

    # ── Hook：文案检测（触发窗口内命中关键词 = vw50 完成）────────────

    @HookHandler("send_service.after_send", mode=HookMode.OBSERVE)
    async def on_after_send(self, **kwargs: Any) -> None:
        """成功发送后：触发窗口内命中关键词，才视为 vw50 文案已发。

        OBSERVE 只读。仅在 Host 明确返回 sent=true 后判定，避免消息构建成功但发送失败时误停止重试。
        """
        if not bool(kwargs.get("sent", False)):
            return
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return
        stream_id = str(message.get("session_id") or "")
        if not stream_id:
            return

        # 赞助感谢已成功发出：清理待感谢记录（replyer 重试期间会一直保留要求）
        sponsor_pending = self._sponsor_pending.get(stream_id)
        if sponsor_pending and sponsor_pending.get("injected"):
            self._sponsor_pending.pop(stream_id, None)
            self.ctx.logger.info(
                f"[vw50] 赞助感谢已发出 stream={stream_id} name={sponsor_pending.get('name')} "
                f"amount={sponsor_pending.get('amount')}"
            )

        detect_until = self._detect_until.get(stream_id, 0.0)
        if not detect_until:
            return
        if time.time() > detect_until:
            self._detect_until.pop(stream_id, None)
            return
        if self._is_copy_sent(stream_id):
            return  # 已完成
        try:
            settings = self.config
        except RuntimeError:
            return
        text = str(message.get("processed_plain_text") or "")
        if not text:
            return
        keywords = settings.prompt.detect_keywords or []
        if not keywords:
            return
        text_lower = text.lower()
        for kw in keywords:
            kw_str = str(kw).strip()
            if kw_str and kw_str.lower() in text_lower:
                self._mark_sent(stream_id, copy_sent=True, source=f"copy:{kw_str}")
                self.ctx.logger.info(
                    f"[vw50] 文案检测命中 stream={stream_id} keyword={kw_str}"
                )
                break

    # ── Tool：模型 / 群友经 AI 调用发送收款码 ──────────────────────────

    @Tool(
        name="send_payment_code",
        description=(
            "发送麦麦的赞助/收款码图片。当群友表示想给麦麦赞助、打赏、支持麦麦，"
            "或需要在「V我50 / 疯狂星期四」等场景下附上收款码时调用此工具。"
            "可选 payment_platform 参数指定平台，如 wechat（微信）、alipay（支付宝）；"
            "不传则发默认收款码。"
        ),
        parameters={
            "payment_platform": {
                "type": "string",
                "description": (
                    "要发送哪个平台的收款码，如 wechat（微信支付）、alipay（支付宝）。"
                    "不传或传空则发默认收款码。"
                ),
            },
        },
    )
    async def tool_send_payment_code(self, **kwargs: Any) -> dict[str, Any]:
        """LLM 工具：发送收款码图片到当前会话。可选 payment_platform 指定平台。"""
        try:
            settings = self.config
        except RuntimeError:
            return {"name": "send_payment_code", "content": "插件配置未就绪，无法发送收款码"}

        if not settings.tool.enabled:
            return {"name": "send_payment_code", "content": "收款码工具已被禁用"}

        stream_id = str(kwargs.get("stream_id") or kwargs.get("chat_id") or "")
        if not stream_id:
            return {"name": "send_payment_code", "content": "未获取到会话 stream_id，无法发送"}

        platform = str(kwargs.get("payment_platform") or "").strip()
        if not platform:
            legacy_platform = str(kwargs.get("platform") or "").strip()
            configured_chat_platform = (settings.target.platform or "qq").strip() or "qq"
            chat_platform = self._normalize_payment_platform(configured_chat_platform)
            if self._normalize_payment_platform(legacy_platform) != chat_platform:
                platform = legacy_platform

        # 全局节流：同流连续调用间隔，避免群友反复点导致刷屏。
        # 注意：工具调用**不**作为 vw50 自动流程完成的信号，文案才算。
        last = float(self._tool_last_sent.get(stream_id, 0.0) or 0.0)
        if last and (time.time() - last) < self._TOOL_COOLDOWN_SEC:
            remain = max(1, math.ceil(self._TOOL_COOLDOWN_SEC - (time.time() - last)))
            return {
                "name": "send_payment_code",
                "content": f"收款码刚发过，请 {remain}s 后再试",
            }

        image_b64 = self._load_payment_code_base64(settings, platform=platform)
        if not image_b64:
            tip = f" platform={platform!r}" if platform else ""
            self.ctx.logger.warning(f"[vw50] 收款码{tip}未配置，工具无法发送")
            return {"name": "send_payment_code", "content": "收款码尚未配置"}

        caption = self._caption_for(settings, platform=platform)
        try:
            if caption:
                segments = [
                    {"type": "text", "content": caption},
                    {"type": "image", "content": image_b64},
                ]
                ok = await self.ctx.send.hybrid(segments, stream_id)
            else:
                ok = await self.ctx.send.image(image_b64, stream_id)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error(f"[vw50] 工具发送收款码失败: {exc}")
            return {"name": "send_payment_code", "content": f"发送失败: {exc}"}

        if ok:
            # 仅记录节流时间戳 + 工具调用日志，不标记 vw50 自动流程完成。
            self._tool_last_sent[stream_id] = time.time()
            tag = f" platform={platform}" if platform else " 默认"
            self.ctx.logger.info(f"[vw50] 工具已发送收款码 stream={stream_id}{tag}")
            return {"name": "send_payment_code", "content": "已发送麦麦的收款码"}
        return {"name": "send_payment_code", "content": "收款码发送未成功"}

    # ── Command：手动测试 / 查看状态 ─────────────────────────────────

    @Command(
        name="vw50_test",
        pattern=r"^/vw50_test\b",
        description="手动触发一次 vw50 赞助流程（忽略日期/时间限制，便于验证）。",
    )
    async def cmd_vw50_test(self, **kwargs: Any) -> tuple[bool, str, int]:
        denied = await self._deny_if_not_admin(kwargs)
        if denied is not None:
            return denied
        stream_id = str(kwargs.get("stream_id", ""))
        try:
            settings = self.config
        except RuntimeError:
            if stream_id:
                await self.ctx.send.text("插件配置未就绪", stream_id)
            return False, "配置未就绪", 2

        if not stream_id:
            return False, "未获取到 stream_id", 2

        await self.ctx.send.text("已手动触发，请观察麦麦后续是否主动发言。", stream_id)
        if not await self._trigger_vw50(stream_id, settings):
            await self.ctx.send.text("主动任务入队失败，请查看日志后重试。", stream_id)
            return False, "主动任务入队失败", 2
        return True, "已手动触发流程", 2

    @Command(
        name="vw50_status",
        pattern=r"^/vw50_status\b",
        description="查看 vw50 插件当日状态。",
    )
    async def cmd_vw50_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        denied = await self._deny_if_not_admin(kwargs)
        if denied is not None:
            return denied
        stream_id = str(kwargs.get("stream_id", ""))
        self._ensure_today_state()
        sponsor_count = len(self._load_sponsors().get("records") or [])
        msg = (
            f"[插件状态]\n"
            f"日期: {self._state.get('date')}\n"
            f"copy_sent: {self._state.get('copy_sent', {})}\n"
            f"code_sent: {self._state.get('code_sent', {})}\n"
            f"attempt: {self._state.get('attempt', {})}\n"
            f"last_trigger: {self._state.get('last_trigger', {})}\n"
            f"赞助记录: {sponsor_count} 条"
        )
        if stream_id:
            await self.ctx.send.text(msg, stream_id)
        return True, "已发送状态", 2

    # ── Command：赞助录入 ────────────────────────────────────────────

    @Command(
        name="vw50",
        pattern=r"^/vw50(?=\s|$)(?:\s+(?P<amount>\d+(?:\.\d{1,2})?))?(?:\s+@\S+)?\s*$",
        description="录入群友赞助（格式：/vw50 金额 @成员），并让麦麦公开感谢。",
    )
    async def cmd_vw50_sponsor(self, **kwargs: Any) -> tuple[bool, str, int]:
        """管理员录入赞助：/vw50 金额 @成员。

        消息被拦截（不进 AI 链），随后挂一次性感谢要求并主动触发一轮发言；
        感谢话术由 replyer 在生成回复时拿到（见 on_replyer_before_request）。
        """
        stream_id = str(kwargs.get("stream_id") or "")
        user_id = str(kwargs.get("user_id") or "")
        group_id = str(kwargs.get("group_id") or "")

        async def deny(message: str) -> tuple[bool, str, int]:
            if stream_id:
                await self.ctx.send.text(message, stream_id)
            return False, message, 2

        # 赞助涉及真实金额：管理员列表为空时直接拒绝（fail-closed）
        if not self._is_admin_strict(user_id):
            return await deny("你不是插件管理员，无权使用此命令（请先在配置里填 admin_qq_ids）。")

        try:
            settings = self.config
        except RuntimeError:
            return await deny("插件配置未就绪。")

        if not settings.sponsor.enabled:
            return await deny("赞助录入功能已禁用。")
        if settings.sponsor.group_only and not group_id:
            return await deny("该指令只能在群聊中使用。")
        if not stream_id:
            return await deny("未获取到会话信息，无法录入。")

        matched = kwargs.get("matched_groups") if isinstance(kwargs.get("matched_groups"), dict) else {}
        amount_raw = str(matched.get("amount") or "").strip()
        if not amount_raw:
            return await deny("格式：/vw50 金额 @成员（例如 /vw50 50 @某人）")
        try:
            amount = round(float(amount_raw), 2)
        except ValueError:
            return await deny("金额格式不正确。")
        max_amount = float(settings.sponsor.max_amount)
        if not (0 < amount <= max_amount):
            return await deny(f"金额必须在 0 到 {max_amount:g} 元之间。")

        target = self._extract_at_target(kwargs.get("message"))
        if not target:
            return await deny("请 @ 要录入的赞助人。")

        record = {
            "ts": time.time(),
            "amount": amount,
            "user_id": target["user_id"],
            "name": target["name"],
            "operator_id": user_id,
            "group_id": group_id,
            "stream_id": stream_id,
            "message_id": str((kwargs.get("message") or {}).get("message_id") or ""),
        }
        times, total, too_soon = self._add_sponsor(
            record,
            repeat_trigger_interval_sec=float(settings.sponsor.repeat_trigger_interval_sec),
        )

        if too_soon:
            return (
                True,
                f"已记录赞助：{target['name']} {amount:g} 元（第 {times} 次）；间隔过短，未重复触发发言",
                2,
            )

        ttl = float(settings.sponsor.thanks_ttl_sec)
        self._sponsor_pending[stream_id] = {
            "name": target["name"],
            "amount": amount,
            "times": times,
            "total": total,
            "until": time.time() + ttl,
            "injected": False,
        }

        intent = self._render_sponsor_template(
            settings.sponsor.intent_template,
            name=target["name"],
            amount=amount,
            times=times,
            total=total,
        )
        try:
            result = await self.ctx.maisaka.proactive.trigger(
                stream_id=stream_id,
                intent=intent,
                reason=f"赞助录入：{target['name']} {amount:g} 元",
            )
            if isinstance(result, dict) and not result.get("success", True):
                raise RuntimeError(str(result.get("error") or "主动任务入队失败"))
        except Exception as exc:  # noqa: BLE001
            self._sponsor_pending.pop(stream_id, None)
            self.ctx.logger.error(f"[vw50] 赞助感谢主动触发失败: {exc}")
            if stream_id:
                await self.ctx.send.text(f"已记录赞助，但主动发言入队失败：{exc}", stream_id)
            return False, "主动发言入队失败", 2

        self.ctx.logger.info(
            f"[vw50] 已录入赞助 stream={stream_id} user={target['user_id']} "
            f"name={target['name']} amount={amount:g} times={times} total={total:g}"
        )
        return True, f"已记录赞助：{target['name']} {amount:g} 元（第 {times} 次）", 2

    @staticmethod
    def _extract_at_target(message: Any) -> Optional[dict[str, str]]:
        """从序列化消息的 at 段取出被 @ 的用户。

        MaiBot 1.3.8 的 at 段结构：
        {"type": "at", "data": {"target_user_id", "target_user_nickname", "target_user_cardname"}}
        显示名优先群名片，其次昵称，最后 QQ 号。
        """
        if not isinstance(message, dict):
            return None
        for seg in message.get("raw_message") or []:
            if not isinstance(seg, dict) or seg.get("type") != "at":
                continue
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            target_id = str(data.get("target_user_id") or "").strip()
            if not target_id:
                continue
            name = (
                data.get("target_user_cardname")
                or data.get("target_user_nickname")
                or target_id
            )
            return {"user_id": target_id, "name": str(name).strip() or target_id}
        return None


def create_plugin() -> MaiBotPlugin:
    """MaiBot 插件工厂函数。"""
    return Vw50SponsorPlugin()
