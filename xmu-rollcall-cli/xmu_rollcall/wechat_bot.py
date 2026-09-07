from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Union
from zoneinfo import ZoneInfo

from .config import CONFIG_DIR, ensure_config_dir
from .rollcall_service import AnswerBatchResult, AnswerOutcome, RollcallService
from .utils import save_session
from .wechat_storage import (
    add_or_update_user_account,
    add_user_cron_schedule,
    build_user_session_cache_key,
    clear_user_cron_schedules,
    delete_user_cron_schedule,
    get_current_user_account,
    get_user_cron_schedules,
    get_user_accounts,
    list_user_cron_jobs,
    load_all_user_context_tokens,
    mark_user_cron_triggered,
    save_user_context_token,
    set_current_user_account,
)

MAX_LINES_PER_MESSAGE = 12
MAX_CHARS_PER_MESSAGE = 380
SUMMARY_ROWS_PER_MESSAGE = 4
DETAIL_BLOCKS_PER_MESSAGE = 2
CRON_ROWS_PER_MESSAGE = 4
CRON_POLL_INTERVAL_SECONDS = 20
CRON_GRACE_SECONDS = 300
CHINA_TZ = ZoneInfo("Asia/Shanghai")
CRON_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")

ReplyPayload = Union[str, List[str], None]


@dataclass
class PendingConfigState:
    stage: str
    username: str = ""


def _escape_markdown(text: Any) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if not value:
        return "-"
    return value.replace("`", "'").replace("|", "｜")


def _shorten(text: Any, max_len: int = 12) -> str:
    value = _escape_markdown(text)
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 1]}…"


def _account_id_text(account: Optional[dict]) -> str:
    if not account:
        return "-"
    return f"`{account.get('id')}`"


def _cron_id_text(schedule: Optional[dict]) -> str:
    if not schedule:
        return "-"
    return f"`{int(schedule.get('id', 0))}`"


def _compact_time(value: str) -> str:
    if len(value) >= 16:
        return value[5:16]
    return value


def _china_now() -> datetime:
    return datetime.now(CHINA_TZ)


def _to_china_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TZ)
    return value.astimezone(CHINA_TZ)


def _format_datetime(value: datetime) -> str:
    return _to_china_time(value).strftime("%Y-%m-%d %H:%M:%S")


def _weekday_text(weekday: int) -> str:
    labels = {
        1: "周一",
        2: "周二",
        3: "周三",
        4: "周四",
        5: "周五",
        6: "周六",
        7: "周日",
    }
    return labels.get(int(weekday), f"周{weekday}")


def _format_cron_label(schedule: Optional[dict]) -> str:
    if not schedule:
        return "未设置"
    weekday = int(schedule.get("weekday", 0))
    hour = int(schedule.get("hour", 0))
    minute = int(schedule.get("minute", 0))
    return f"{_weekday_text(weekday)} {hour:02d}:{minute:02d}"


def _format_cron_table(schedules: Sequence[dict]) -> str:
    if not schedules:
        return _build_table(
            ["ID", "计划", "状态"],
            [["-", "未设置", "发送 `/cron 4 8:00`"]],
        )

    rows = []
    for schedule in schedules:
        rows.append([
            _cron_id_text(schedule),
            _format_cron_label(schedule),
            "已启用",
        ])
    return _build_table(["ID", "计划", "状态"], rows)


def _parse_cron_time(value: str) -> Optional[tuple[int, int]]:
    match = CRON_TIME_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _resolve_due_cron_slot(now: datetime, schedule: Optional[dict]) -> Optional[str]:
    if not isinstance(schedule, dict):
        return None

    try:
        weekday = int(schedule.get("weekday", 0))
        hour = int(schedule.get("hour", 0))
        minute = int(schedule.get("minute", 0))
    except (TypeError, ValueError):
        return None

    if weekday != now.isoweekday():
        return None

    try:
        scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    delta_seconds = (now - scheduled_at).total_seconds()
    if delta_seconds < 0 or delta_seconds > CRON_GRACE_SECONDS:
        return None

    slot_key = scheduled_at.strftime("%Y-%m-%dT%H:%M")
    if schedule.get("last_triggered_key") == slot_key:
        return None
    return slot_key


def _build_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    row_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, separator_line, *row_lines])


def _chunked(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _split_long_markdown_message(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if (
        stripped.count("\n") + 1 <= MAX_LINES_PER_MESSAGE
        and len(stripped) <= MAX_CHARS_PER_MESSAGE
    ):
        return [stripped]

    blocks = stripped.split("\n\n")
    messages: List[str] = []
    current_blocks: List[str] = []
    current_lines = 0
    current_chars = 0

    for block in blocks:
        block_lines = block.count("\n") + 1
        block_chars = len(block)
        needs_flush = current_blocks and (
            current_lines + 1 + block_lines > MAX_LINES_PER_MESSAGE
            or current_chars + 2 + block_chars > MAX_CHARS_PER_MESSAGE
        )
        if needs_flush:
            messages.append("\n\n".join(current_blocks).strip())
            current_blocks = [block]
            current_lines = block_lines
            current_chars = block_chars
            continue
        current_blocks.append(block)
        if len(current_blocks) == 1:
            current_lines = block_lines
            current_chars = block_chars
        else:
            current_lines += 1 + block_lines
            current_chars += 2 + block_chars

    if current_blocks:
        messages.append("\n\n".join(current_blocks).strip())

    return messages


def _normalize_messages(payload: ReplyPayload) -> List[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        payload = [payload]

    messages: List[str] = []
    for item in payload:
        messages.extend(_split_long_markdown_message(item))
    return [message for message in messages if message.strip()]


def _format_accounts_table(accounts: List[dict], current_account_id: Optional[int]) -> str:
    if not accounts:
        return _build_table(
            ["ID", "姓名", "状态"],
            [["-", "-", "未配置"]],
        )

    rows = []
    for account in accounts:
        account_id = int(account.get("id", 0))
        display_name = _shorten(account.get("name") or account.get("username") or "-", 8)
        status = "当前" if account_id == int(current_account_id or 0) else "-"
        rows.append([f"`{account_id}`", display_name, status])
    return _build_table(["ID", "姓名", "状态"], rows)


def _format_help_markdown() -> str:
    return "\n".join(
        [
            "# 命令",
            "",
            _build_table(
                ["指令", "说明"],
                [
                    ["`/conf`", "分步配置账号（学号、密码）"],
                    ["`/accounts`", "查看账号 ID"],
                    ["`/switch 1`", "切换账号"],
                    ["`/answer`", "查询并自动签到（数字/雷达）"],
                    ["`/qr`", "等二维码照片签到；`/qr 内容` 直接按内容签"],
                    ["`/cron`", "定时签到，用法见下方示例"],
                    ["`/refresh`", "重新登录（会话失效时用）"],
                    ["`/cancel`", "取消当前操作"],
                ],
            ),
            "",
            "> 直接把“二维码点名”的照片发给机器人也会自动签到，用 `/qr` 待命后再发最快。",
            "",
            "## /cron 定时任务示例",
            "",
            "- `/cron` — 查看当前全部定时",
            "- `/cron add 4 8:00` — 新增：每周四 08:00 自动签到",
            "- `/cron 4 8:00` — 简写，与上一条等价",
            "- `/cron del 2` — 删除编号为 2 的定时",
            "- `/cron off` — 清空全部定时",
            "",
            "> 星期用 1-7，分别代表周一至周日。",
        ]
    )


def _format_no_account_markdown() -> str:
    return "\n".join(
        [
            "# 尚未配置",
            "",
            _build_table(
                ["步骤", "操作"],
                [
                    ["1", "`/conf`"],
                    ["2", "发送学号"],
                    ["3", "发送密码"],
                    ["4", "`/answer`"],
                ],
            ),
        ]
    )


def _format_accounts_markdown(user_id: str) -> str:
    accounts = get_user_accounts(user_id)
    current_account = get_current_user_account(user_id)
    return "\n".join(
        [
            "# 账号",
            "",
            f"当前 ID：{_account_id_text(current_account)}",
            "",
            _format_accounts_table(accounts, (current_account or {}).get("id")),
        ]
    )


def _format_conf_start_markdown() -> str:
    return "\n".join(
        [
            "# 配置账号",
            "",
            "请发送学号。",
            "",
            "> 密码会作为普通消息发送，完成后可自行撤回。",
        ]
    )


def _format_conf_password_markdown(username: str) -> str:
    return "\n".join(
        [
            "# 配置账号",
            "",
            f"学号已记录：`{_shorten(username, 18)}`",
            "",
            "请发送密码。",
            "",
            "> 可发送 `/cancel` 取消。",
        ]
    )


def _format_conf_retry_markdown(message: str) -> str:
    return "\n".join(
        [
            "# 登录失败",
            "",
            f"- {_shorten(message, 40)}",
            "- 请重新发送密码。",
            "- 或发送 `/cancel` 取消。",
        ]
    )


def _format_conf_cancelled_markdown() -> str:
    return "\n".join(
        [
            "# 已取消",
            "",
            "账号配置已中止。",
        ]
    )


def _format_conf_success_messages(account: dict, created: bool, accounts: List[dict]) -> List[str]:
    action_title = "# 账号已添加" if created else "# 账号已更新"
    return [
        "\n".join(
            [
                action_title,
                "",
                f"当前 ID：{_account_id_text(account)}",
                "",
                _format_accounts_table(accounts, account.get("id")),
            ]
        ),
        "\n".join(
            [
                "# 下一步",
                "",
                _build_table(
                    ["指令", "作用"],
                    [
                        ["`/answer`", "查一次签到"],
                        ["`/switch ID`", "切换账号"],
                    ],
                ),
            ]
        ),
    ]


def _format_switch_markdown(account: dict, accounts: List[dict]) -> str:
    return "\n".join(
        [
            "# 已切换",
            "",
            f"当前 ID：{_account_id_text(account)}",
            "",
            _format_accounts_table(accounts, account.get("id")),
        ]
    )


def _format_refresh_markdown(account: dict, removed: bool) -> str:
    status = "已清理，下次会重新登录。" if removed else "当前没有可清理的缓存。"
    return "\n".join(
        [
            "# 缓存处理完成",
            "",
            _build_table(
                ["当前 ID", "结果"],
                [[_account_id_text(account), status]],
            ),
        ]
    )


def _format_answer_status(outcome: AnswerOutcome) -> str:
    if outcome.action == "already_answered":
        return "已完成"
    if outcome.action == "unsupported":
        return "暂不支持"
    if outcome.action == "expired":
        return "已过期"
    if outcome.action == "qr_answered":
        return "二维码签到成功"
    return "成功" if outcome.success else "失败"


def _format_no_rollcall_markdown(account: dict, queried_at: str) -> str:
    return "\n".join(
        [
            "# 没有签到",
            "",
            _build_table(
                ["当前 ID", "时间"],
                [[_account_id_text(account), f"`{_compact_time(queried_at)}`"]],
            ),
        ]
    )


def _format_cron_status_messages(schedules: Sequence[dict]) -> List[str]:
    if not schedules:
        return [
            "\n".join(
                [
                    "# 定时签到",
                    "",
                    _format_cron_table([]),
                    "",
                    "> 用 `/cron add 4 8:00` 或 `/cron 4 8:00` 新增。",
                    "> 时间支持 `8:00` 和 `08:00`。",
                ]
            )
        ]

    messages = [
        "\n".join(
            [
                "# 定时签到",
                "",
                _build_table(
                    ["数量", "时区"],
                    [[f"`{len(schedules)}`", "UTC+8"]],
                ),
                "",
                "> 新增：`/cron add 4 8:00`  删除：`/cron del 2`",
            ]
        )
    ]
    for chunk in _chunked(list(schedules), CRON_ROWS_PER_MESSAGE):
        messages.append(
            "\n".join(
                [
                    "# 列表",
                    "",
                    _format_cron_table(chunk),
                ]
            )
        )
    return messages


def _format_cron_set_markdown(schedule: dict, created: bool) -> str:
    title = "# 定时签到已新增" if created else "# 定时签到已存在"
    status = "已添加" if created else "未重复添加"
    return "\n".join(
        [
            title,
            "",
            _build_table(
                ["ID", "计划", "结果"],
                [[_cron_id_text(schedule), _format_cron_label(schedule), status]],
            ),
        ]
    )


def _format_cron_deleted_markdown(schedule: dict) -> str:
    return "\n".join(
        [
            "# 定时签到已删除",
            "",
            _build_table(
                ["ID", "计划"],
                [[_cron_id_text(schedule), _format_cron_label(schedule)]],
            ),
        ]
    )


def _format_cron_cleared_markdown(cleared_count: int) -> str:
    status = f"已清空 `{cleared_count}` 条任务。" if cleared_count else "当前没有已设置的定时任务。"
    return "\n".join(
        [
            "# 定时签到",
            "",
            f"- {status}",
        ]
    )


def _format_cron_run_markdown(schedule: dict, executed_at: datetime) -> str:
    return "\n".join(
        [
            "# 定时签到执行",
            "",
            _build_table(
                ["ID", "计划", "执行时间"],
                [[
                    _cron_id_text(schedule),
                    _format_cron_label(schedule),
                    f"`{_compact_time(_format_datetime(executed_at))}`",
                ]],
            ),
        ]
    )


def _detail_text(outcome: AnswerOutcome) -> str:
    if outcome.number_code:
        return f"码 `{_escape_markdown(outcome.number_code)}`"
    if outcome.latitude is not None and outcome.longitude is not None:
        return f"`{outcome.latitude:.5f}, {outcome.longitude:.5f}`"
    if outcome.response_status and not outcome.success:
        return f"HTTP `{outcome.response_status}`"
    if outcome.action in {"unsupported", "failed", "expired", "skipped"}:
        return _shorten(outcome.message, 18)
    return "-"


def _format_answer_messages(batch_result: AnswerBatchResult) -> List[str]:
    account = batch_result.account
    queried_at = _format_datetime(batch_result.queried_at)
    messages: List[str] = [
        "\n".join(
            [
                "# 签到结果",
                "",
                _build_table(
                    ["当前 ID", "时间", "数量"],
                    [[
                        _account_id_text(account),
                        f"`{_compact_time(queried_at)}`",
                        f"`{len(batch_result.rollcalls)}`",
                    ]],
                ),
            ]
        )
    ]

    summary_rows = []
    detail_blocks = []

    for index, outcome in enumerate(batch_result.outcomes, start=1):
        rollcall = outcome.rollcall
        summary_rows.append(
            [
                f"`{index}`",
                _shorten(rollcall.course_title, 10),
                _shorten(rollcall.type_label, 4),
                _format_answer_status(outcome),
            ]
        )

        detail_value = _detail_text(outcome)
        if detail_value == "-":
            continue
        detail_blocks.append(
            "\n".join(
                [
                    f"## 详情 `{index}`",
                    "",
                    _build_table(
                        ["项目", "内容"],
                        [
                            ["课程", _shorten(rollcall.course_title, 16)],
                            ["类型", _shorten(rollcall.type_label, 8)],
                            ["结果", _format_answer_status(outcome)],
                            ["详情", detail_value],
                        ],
                    ),
                ]
            )
        )

    for summary_chunk in _chunked(summary_rows, SUMMARY_ROWS_PER_MESSAGE):
        messages.append(
            "\n".join(
                [
                    "# 列表",
                    "",
                    _build_table(
                        ["#", "课程", "类型", "结果"],
                        list(summary_chunk),
                    ),
                ]
            )
        )

    for block_chunk in _chunked(detail_blocks, DETAIL_BLOCKS_PER_MESSAGE):
        messages.append("\n\n".join(block_chunk))

    return messages


def _format_error_markdown(title: str, message: str) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            f"- {_shorten(message, 40)}",
        ]
    )


class XMUWeChatBotApp:
    def __init__(self, bot: Any):
        self.bot = bot
        self.command_lock = asyncio.Lock()
        self.pending_configs: Dict[str, PendingConfigState] = {}
        # 用户输入 /qr 后等待二维码照片：user_id -> {account, service, session}
        self.qr_ready: Dict[str, Dict[str, Any]] = {}
        self.cron_task: Optional[asyncio.Task[None]] = None

    def restore_context_tokens(self) -> None:
        context_tokens = getattr(self.bot, "_context_tokens", None)
        if not isinstance(context_tokens, dict):
            return
        context_tokens.update(load_all_user_context_tokens())

    async def start_background_tasks(self) -> None:
        self.restore_context_tokens()
        if self.cron_task is None:
            self.cron_task = asyncio.create_task(self._cron_loop())

    async def stop_background_tasks(self) -> None:
        if self.cron_task is None:
            return
        self.cron_task.cancel()
        try:
            await self.cron_task
        except asyncio.CancelledError:
            pass
        self.cron_task = None

    async def handle_message(self, msg: Any) -> None:
        text = (getattr(msg, "text", "") or "").strip()

        context_token = getattr(msg, "_context_token", "") or ""
        if context_token:
            await asyncio.to_thread(save_user_context_token, msg.user_id, context_token)
            context_tokens = getattr(self.bot, "_context_tokens", None)
            if isinstance(context_tokens, dict):
                context_tokens[msg.user_id] = context_token

        async with self.command_lock:
            try:
                await self.bot.send_typing(msg.user_id)
            except Exception:
                pass

            try:
                # 检查是否为图片消息（二维码签到）
                msg_type = getattr(msg, "type", "") or ""
                is_image = getattr(msg, "is_image", False) or msg_type == "Image"

                if is_image:
                    reply_payload = await self._handle_image(msg)
                elif text:
                    reply_payload = await self._route_message(msg, text)
                else:
                    return
            except Exception as exc:
                reply_payload = _format_error_markdown("执行失败", str(exc))

            await self._reply_messages(msg, reply_payload)

    async def _handle_image(self, msg: Any) -> ReplyPayload:
        """处理图片消息：若用户已用 /qr 进入“待命”状态则走快速通道，否则通用解码。"""
        ready = self.qr_ready.get(msg.user_id)
        if ready is not None:
            return await self._handle_qr_image(msg, ready)
        return await self._handle_image_generic(msg)

    async def _handle_qr_image(self, msg: Any, ready: Dict[str, Any]) -> ReplyPayload:
        """/qr 之后收到的照片：用预取好的会话快速解码并签到。"""
        image_bytes = None
        if hasattr(msg, "image") and callable(msg.image):
            image_bytes = await msg.image()
        elif hasattr(msg, "image_data"):
            image_bytes = msg.image_data
        elif hasattr(msg, "image"):
            image_bytes = msg.image

        if not image_bytes:
            return _format_error_markdown("图片处理失败", "无法获取图片数据。")

        service: RollcallService = ready["service"]
        session = ready["session"]

        try:
            qr_content = await asyncio.to_thread(
                RollcallService.decode_qr_image, image_bytes
            )
        except RuntimeError as exc:
            return _format_error_markdown("二维码解码失败", str(exc))

        if not qr_content:
            return (
                "没有在照片里找到二维码。\n"
                "请重新发送清晰的二维码照片（机器人保持待命，无需再发 /qr）。"
            )

        parsed = RollcallService.parse_qr_content(qr_content)
        if not (isinstance(parsed, dict) and parsed.get("rollcallId") is not None):
            return (
                "已识别到二维码，但内容不是畅课“二维码点名”的签到码：\n"
                f"`{_escape_markdown(qr_content)}`\n\n"
                "机器人保持待命，请重新发送正确的签到二维码照片。"
            )

        rollcall_id = int(parsed["rollcallId"])
        course_id = parsed.get("courseId")
        outcome = await asyncio.to_thread(service.answer_qr_content, session, qr_content)
        if outcome.success:
            self.qr_ready.pop(msg.user_id, None)
            lines = [
                "# 二维码签到成功",
                "",
                f"- 签到编号：`{rollcall_id}`",
            ]
            if course_id is not None:
                lines.append(f"- 课程编号：`{course_id}`")
            lines.append("> 提示：动态二维码会定期刷新，下次签到请重新发 /qr 再拍照。")
            return "\n".join(lines)

        # 失败保持待命，便于重拍
        lines = [
            "# 二维码签到失败",
            "",
            f"- 签到编号：`{rollcall_id}`",
            f"- 原因：{outcome.message}",
            "",
            "机器人保持待命，可重新发送二维码照片重试；或发送 /cancel 退出。",
        ]
        return "\n".join(lines)

    async def _handle_image_generic(self, msg: Any) -> ReplyPayload:
        """通用图片处理：尝试解码二维码并自动签到"""
        try:
            image_bytes = None
            if hasattr(msg, "image") and callable(msg.image):
                image_bytes = await msg.image()
            elif hasattr(msg, "image_data"):
                image_bytes = msg.image_data
            elif hasattr(msg, "image"):
                image_bytes = msg.image

            if not image_bytes:
                return _format_error_markdown("图片处理失败", "无法获取图片数据。")

            # 解码二维码
            try:
                qr_content = await asyncio.to_thread(
                    RollcallService.decode_qr_image, image_bytes
                )
            except RuntimeError as exc:
                return _format_error_markdown("二维码解码失败", str(exc))

            if not qr_content:
                return "未在图片中找到二维码。"

            # 获取用户账号，尝试自动签到
            account = await asyncio.to_thread(get_current_user_account, msg.user_id)
            if not account:
                return f"检测到二维码内容：\n`{qr_content}`\n\n未配置 TronClass 账号，请先 /conf 配置。"

            cache_key = build_user_session_cache_key(msg.user_id, int(account["id"]))
            service = RollcallService(account, session_cache_key=cache_key)

            try:
                session = await asyncio.to_thread(service.get_session)
            except Exception as exc:
                return f"检测到二维码内容：\n`{qr_content}`\n\n登录失败：{exc}"

            parsed = RollcallService.parse_qr_content(qr_content)

            # 内容自带 rollcallId：直接按内容签到（更快，减少二维码过期的风险）
            if isinstance(parsed, dict) and parsed.get("rollcallId") is not None:
                outcome = await asyncio.to_thread(
                    service.answer_qr_content, session, qr_content
                )
                rollcall_id = int(parsed["rollcallId"])
                course_id = parsed.get("courseId")
                if outcome.success:
                    lines = [
                        "# 二维码签到成功",
                        "",
                        f"- 签到编号：`{rollcall_id}`",
                    ]
                    if course_id is not None:
                        lines.append(f"- 课程编号：`{course_id}`")
                    lines.append("> 提示：动态二维码会定期刷新，若下次提示失败，请及时重新拍照。")
                else:
                    lines = [
                        "# 二维码签到失败",
                        "",
                        f"- 签到编号：`{rollcall_id}`",
                        f"- 原因：{outcome.message}",
                        "",
                        "> 动态二维码通常几分钟内就会过期。若提示“时间不一致/已过期”，",
                        "> 请让老师重新展示二维码后立即把照片发给本机器人。",
                    ]
                return "\n".join(lines)

            # 解析不出 rollcallId（例如内容是纯数字口令的二维码）：回退到遍历活跃签到
            try:
                rollcalls = await asyncio.to_thread(service.fetch_rollcalls, session)
                qr_rollcalls = [r for r in rollcalls
                                if not r.is_number and not r.is_radar and not r.is_expired]
            except Exception:
                qr_rollcalls = []

            if not qr_rollcalls:
                return (
                    f"检测到二维码内容：\n`{qr_content}`\n\n"
                    "当前没有活跃的二维码签到。\n"
                    f"可用 /qrcode 二维码内容 手动提交。"
                )

            # 对每个活跃的二维码签到尝试提交
            results = []
            for rc in qr_rollcalls:
                outcome = await asyncio.to_thread(
                    service.answer_qr_rollcall_with_code,
                    session, rc.rollcall_id, qr_content,
                )
                results.append(f"- {rc.course_title}：{'✅ 成功' if outcome.success else '❌ ' + outcome.message}")

            return "\n".join([
                "## 二维码签到结果",
                "",
                f"解码内容：`{qr_content}`",
                "",
                *results,
            ])

        except Exception as exc:
            return _format_error_markdown("图片处理异常", str(exc))

    async def _reply_messages(self, msg: Any, payload: ReplyPayload) -> None:
        messages = _normalize_messages(payload)
        for index, message in enumerate(messages):
            await self.bot.reply(msg, message)
            if index < len(messages) - 1:
                await asyncio.sleep(0.2)

    async def _send_user_messages(self, user_id: str, payload: ReplyPayload) -> None:
        messages = _normalize_messages(payload)
        for index, message in enumerate(messages):
            await self.bot.send(user_id, message)
            if index < len(messages) - 1:
                await asyncio.sleep(0.2)

    async def _route_message(self, msg: Any, text: str) -> ReplyPayload:
        if msg.user_id in self.pending_configs:
            return await self._handle_pending_conf(msg.user_id, text)

        if not text.startswith("/"):
            return None

        return await self._dispatch(msg, text)

    async def _dispatch(self, msg: Any, text: str) -> ReplyPayload:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return _format_error_markdown("命令解析失败", str(exc))

        if not parts:
            return None

        command = parts[0].lower()
        if command == "/help":
            return _format_help_markdown()
        if command == "/accounts":
            return await asyncio.to_thread(_format_accounts_markdown, msg.user_id)
        if command == "/conf":
            return self._start_conf(msg.user_id)
        if command == "/switch":
            return await self._handle_switch(msg.user_id, parts)
        if command == "/answer":
            return await self._handle_answer(msg.user_id)
        if command in ("/qr", "/qrcode"):
            return await self._handle_qr_command(msg.user_id, command, parts)
        if command == "/cron":
            return await self._handle_cron(msg.user_id, parts)
        if command == "/refresh":
            return await self._handle_refresh(msg.user_id)
        if command == "/cancel":
            cancelled = []
            if self.qr_ready.pop(msg.user_id, None) is not None:
                cancelled.append("已退出二维码照片待命模式。")
            if msg.user_id in self.pending_configs:
                self.pending_configs.pop(msg.user_id, None)
                cancelled.append("已取消进行中的配置。")
            if not cancelled:
                return _format_error_markdown("没有进行中的操作", "当前无需取消。")
            return "\n".join(cancelled)

        return [
            _format_error_markdown("未知命令", parts[0]),
            _format_help_markdown(),
        ]

    def _start_conf(self, user_id: str) -> str:
        self.pending_configs[user_id] = PendingConfigState(stage="username")
        return _format_conf_start_markdown()

    async def _handle_pending_conf(self, user_id: str, text: str) -> ReplyPayload:
        if text.lower() == "/cancel":
            self.pending_configs.pop(user_id, None)
            return _format_conf_cancelled_markdown()

        if text.startswith("/"):
            if text.lower() == "/conf":
                return self._start_conf(user_id)
            return _format_error_markdown("配置进行中", "请继续输入，或发送 `/cancel`。")

        state = self.pending_configs[user_id]
        if state.stage == "username":
            username = text.strip()
            if not username:
                return _format_error_markdown("学号为空", "请重新发送学号。")
            state.username = username
            state.stage = "password"
            return _format_conf_password_markdown(username)

        password = text.strip()
        if not password:
            return _format_error_markdown("密码为空", "请重新发送密码。")

        try:
            validation = await asyncio.to_thread(
                RollcallService.validate_credentials,
                state.username,
                password,
            )
        except Exception as exc:
            return _format_conf_retry_markdown(str(exc))

        account_name = validation.name
        account, created = await asyncio.to_thread(
            add_or_update_user_account,
            user_id,
            state.username,
            password,
            account_name,
        )

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        await asyncio.to_thread(save_session, validation.session, service.session_cache_path)
        accounts = await asyncio.to_thread(get_user_accounts, user_id)
        self.pending_configs.pop(user_id, None)
        return _format_conf_success_messages(account, created, accounts)

    async def _handle_switch(self, user_id: str, parts: Sequence[str]) -> ReplyPayload:
        if len(parts) != 2:
            return _format_error_markdown("参数错误", "用法：/switch 账号ID")

        try:
            account_id = int(parts[1])
        except ValueError:
            return _format_error_markdown("参数错误", "账号ID 必须是数字。")

        account = await asyncio.to_thread(set_current_user_account, user_id, account_id)
        if not account:
            accounts = await asyncio.to_thread(get_user_accounts, user_id)
            return [
                _format_error_markdown("切换失败", f"没有找到账号 ID {account_id}。"),
                "\n".join(
                    [
                        "# 账号",
                        "",
                        _format_accounts_table(accounts, None),
                    ]
                ),
            ]

        accounts = await asyncio.to_thread(get_user_accounts, user_id)
        return _format_switch_markdown(account, accounts)

    async def _handle_answer(self, user_id: str) -> ReplyPayload:
        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        batch_result = await asyncio.to_thread(service.answer_active_rollcalls)
        queried_at = _format_datetime(batch_result.queried_at)
        if not batch_result.rollcalls:
            return _format_no_rollcall_markdown(account, queried_at)
        return _format_answer_messages(batch_result)

    async def _handle_qr_command(self, user_id: str, command: str, parts: Sequence[str]) -> ReplyPayload:
        """统一入口：/qr 与 /qrcode。

        - `/qr`：进入照片待命
        - `/qr 二维码内容`：直接按内容签到
        - `/qrcode ...`：历史别名，同样可用
        """
        content = " ".join(parts[1:]).strip()
        if not content:
            if command == "/qrcode":
                return _format_error_markdown("参数不足", "用法：/qr 二维码内容，或 /qr 后直接发送照片")
            return await self._handle_qr_ready(user_id)
        return await self._handle_qrcode(user_id, parts)

    async def _handle_qrcode(self, user_id: str, parts: Sequence[str]) -> ReplyPayload:
        """按二维码内容直接签到。

        两种用法：
          /qr 二维码内容            （rollcallId 由内容自动解析）
          /qr rollcall_id 内容      （显式指定签到编号）
        """
        if len(parts) < 2:
            return _format_error_markdown("参数不足", "用法：/qr [rollcall_id] 二维码内容文本")

        rollcall_id = 0
        content_start = 1
        if len(parts) >= 3 and parts[1].isdigit():
            rollcall_id = int(parts[1])
            content_start = 2

        qr_content = " ".join(parts[content_start:]).strip()
        if not qr_content:
            return _format_error_markdown("参数错误", "二维码内容不能为空。")

        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)

        try:
            session = await asyncio.to_thread(service.get_session)
            outcome = await asyncio.to_thread(
                service.answer_qr_rollcall_with_code,
                session, rollcall_id, qr_content,
            )
        except Exception as exc:
            return _format_error_markdown("二维码签到失败", str(exc))

        status_text = "成功" if outcome.success else "失败"
        detail = outcome.number_code or "-"
        return "\n".join([
            "# 二维码签到结果",
            "",
            f"- 状态：**{status_text}**",
            f"- 内容：`{_escape_markdown(detail)}`",
            f"- 详情：{outcome.message}",
        ])

    async def _handle_qr_ready(self, user_id: str) -> ReplyPayload:
        """进入“二维码照片待命”模式：预取会话，等用户发照片后秒级签到。"""
        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        try:
            session = await asyncio.to_thread(service.get_session)
        except Exception as exc:
            return _format_error_markdown("登录失败", str(exc))

        self.qr_ready[user_id] = {
            "account": account,
            "service": service,
            "session": session,
        }
        return "\n".join([
            "# 二维码照片待命",
            "",
            f"- 账号：{_escape_markdown(service.display_name)}",
            "",
            "请直接发送二维码点名的二维码照片，我会立即识别并签到。",
            "发送 /cancel 可退出待命。",
        ])

    async def _handle_cron(self, user_id: str, parts: Sequence[str]) -> ReplyPayload:
        if len(parts) == 1:
            schedules = await asyncio.to_thread(get_user_cron_schedules, user_id)
            return _format_cron_status_messages(schedules)

        action = parts[1].lower()
        if len(parts) == 2 and action in {"off", "clear", "disable"}:
            cleared_count = await asyncio.to_thread(clear_user_cron_schedules, user_id)
            return _format_cron_cleared_markdown(cleared_count)

        if len(parts) == 3 and action in {"del", "delete", "rm", "remove"}:
            try:
                cron_id = int(parts[2])
            except ValueError:
                return _format_error_markdown("参数错误", "任务 ID 必须是数字。")
            schedule = await asyncio.to_thread(delete_user_cron_schedule, user_id, cron_id)
            if not schedule:
                schedules = await asyncio.to_thread(get_user_cron_schedules, user_id)
                return [
                    _format_error_markdown("删除失败", f"没有找到任务 ID {cron_id}。"),
                    *_format_cron_status_messages(schedules),
                ]
            return _format_cron_deleted_markdown(schedule)

        if len(parts) == 4 and action in {"add", "new", "set"}:
            weekday_value = parts[2]
            time_value = parts[3]
        elif len(parts) == 3:
            weekday_value = parts[1]
            time_value = parts[2]
        else:
            return _format_error_markdown(
                "参数错误",
                "用法：/cron add 4 8:00、/cron del 2、/cron off",
            )

        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        try:
            weekday = int(weekday_value)
        except ValueError:
            return _format_error_markdown("参数错误", "星期必须是 1-7，1=周一，4=周四。")

        if not 1 <= weekday <= 7:
            return _format_error_markdown("参数错误", "星期必须是 1-7，1=周一，4=周四。")

        parsed_time = _parse_cron_time(time_value)
        if not parsed_time:
            return _format_error_markdown("参数错误", "时间格式请用 `8:00` 或 `08:00`。")

        hour, minute = parsed_time
        schedule, created = await asyncio.to_thread(add_user_cron_schedule, user_id, weekday, hour, minute)
        return _format_cron_set_markdown(schedule, created)

    async def _handle_refresh(self, user_id: str) -> ReplyPayload:
        account = await asyncio.to_thread(get_current_user_account, user_id)
        if not account:
            return _format_no_account_markdown()

        cache_key = build_user_session_cache_key(user_id, int(account["id"]))
        service = RollcallService(account, session_cache_key=cache_key)
        removed = await asyncio.to_thread(service.clear_session_cache)
        return _format_refresh_markdown(account, removed)

    async def _cron_loop(self) -> None:
        while True:
            try:
                await self._run_due_crons()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[wechatbot] 定时任务异常：{exc}", file=sys.stderr, flush=True)
            await asyncio.sleep(CRON_POLL_INTERVAL_SECONDS)

    async def _run_due_crons(self) -> None:
        now = _china_now()
        jobs = await asyncio.to_thread(list_user_cron_jobs)
        for job in jobs:
            schedule = job.get("cron")
            slot_key = _resolve_due_cron_slot(now, schedule)
            if not slot_key:
                continue
            user_id = str(job.get("user_id") or "")
            if not user_id:
                continue
            cron_id = int((schedule or {}).get("id", 0))
            if cron_id <= 0:
                continue
            await asyncio.to_thread(mark_user_cron_triggered, user_id, cron_id, slot_key)
            await self._execute_cron(user_id, schedule, now)

    async def _execute_cron(self, user_id: str, schedule: dict, executed_at: datetime) -> None:
        payload: ReplyPayload
        async with self.command_lock:
            account = await asyncio.to_thread(get_current_user_account, user_id)
            if not account:
                payload = [
                    _format_cron_run_markdown(schedule, executed_at),
                    _format_error_markdown("定时任务未执行", "当前没有可用账号。"),
                ]
            else:
                try:
                    cache_key = build_user_session_cache_key(user_id, int(account["id"]))
                    service = RollcallService(account, session_cache_key=cache_key)
                    batch_result = await asyncio.to_thread(service.answer_active_rollcalls)
                    if not batch_result.rollcalls:
                        payload = [
                            _format_cron_run_markdown(schedule, batch_result.queried_at),
                            _format_no_rollcall_markdown(
                                account,
                                _format_datetime(batch_result.queried_at),
                            ),
                        ]
                    else:
                        payload = [
                            _format_cron_run_markdown(schedule, batch_result.queried_at),
                            *_format_answer_messages(batch_result),
                        ]
                except Exception as exc:
                    payload = [
                        _format_cron_run_markdown(schedule, executed_at),
                        _format_error_markdown("定时任务失败", str(exc)),
                    ]

            try:
                await self._send_user_messages(user_id, payload)
            except Exception as exc:
                print(
                    f"[wechatbot] 定时消息发送失败 user_id={user_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )


def _resolve_cred_path(cli_cred_path: Optional[str]) -> str:
    if cli_cred_path:
        return cli_cred_path
    if env_cred_path := os.environ.get("XMU_WECHAT_BOT_CRED_PATH"):
        return env_cred_path
    return str(CONFIG_DIR / "wechatbot-credentials.json")


def _load_wechatbot_class():
    try:
        from wechatbot import WeChatBot
    except ImportError as exc:
        raise SystemExit(
            '未安装依赖。请先在项目目录执行: pip install -e .'
        ) from exc
    return WeChatBot


async def _run_bot(args: argparse.Namespace) -> None:
    WeChatBot = _load_wechatbot_class()
    cred_path = _resolve_cred_path(args.cred_path)

    bot = WeChatBot(
        cred_path=cred_path,
        on_qr_url=lambda url: print(f"[wechatbot] 请扫码登录：{url}", flush=True),
        on_scanned=lambda: print("[wechatbot] 已扫码，等待确认。", flush=True),
        on_expired=lambda: print("[wechatbot] 二维码已过期，请重新生成。", flush=True),
        on_error=lambda err: print(f"[wechatbot] 错误：{err}", file=sys.stderr, flush=True),
    )

    app = XMUWeChatBotApp(bot)

    @bot.on_message
    async def handle_message(msg: Any) -> None:
        await app.handle_message(msg)

    await bot.login(force=args.force_login)
    print(f"[wechatbot] 登录成功，凭证位置：{cred_path}", flush=True)

    if args.login_only:
        print("[wechatbot] 已完成登录，按需启动 systemd 服务即可。", flush=True)
        return

    await app.start_background_tasks()
    print("[wechatbot] 机器人已启动，等待微信命令消息。", flush=True)
    try:
        await bot.start()
    finally:
        await app.stop_background_tasks()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XMU Rollcall WeChat Bot")
    parser.add_argument(
        "--cred-path",
        help="wechatbot 凭证文件路径，默认使用 XMU_WECHAT_BOT_CRED_PATH 或配置目录。",
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="忽略现有 wechatbot 凭证，强制重新扫码登录。",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="只完成扫码登录并写入凭证，不启动消息监听。",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_config_dir()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_bot(args))
    except KeyboardInterrupt:
        print("\n[wechatbot] 已停止。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
