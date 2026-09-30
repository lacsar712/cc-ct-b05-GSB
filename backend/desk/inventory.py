"""超差盘点：小时窗服务端聚合。

专页四类量、占比与「按窗重查」明细全部由 :func:`compute_inventory` 同一个函数
在服务端产出，前端只渲染，不允许自行加总。

口径（前后端一致，CRITERION_TEXT 原样下发给专页展示）：

* 窗口按整点对齐，左闭右开 ``[窗起, 窗止)``；创建时刻压线窗起计入，压线窗止不计入。
* 仅「已结清」（status=done 且结论为合格/超差）的行可入合格桶或超差桶。
* 创建时刻落在窗内但尚未结清（待复核/复核中/结论缺失）的行，不计入合格桶或
  超差桶，单独计入「未结清」桶。
* 占比分母为四类合计（含未结清）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from desk.models import OffsetSubmission

# 窗宽允许范围（整数小时）
MIN_WIDTH_HOURS = 1
MAX_WIDTH_HOURS = 24
# 专页窗宽档位
WIDTH_PRESETS = (1, 2, 4, 8, 12, 24)

CRITERION_TEXT = (
    "窗口按整点对齐、左闭右开 [窗起, 窗止)：创建时刻压线窗起计入，压线窗止不计入；"
    "仅已结清（已完成且结论明确）的行可入合格/超差桶；"
    "创建时刻在窗内但尚未结清的行不计入合格桶或超差桶，单列「未结清」；"
    "占比分母为四类合计（含未结清）。"
    "四类量、占比与按窗重查明细由同一服务端聚合函数产出，对账误差为零。"
)


def parse_end_at(raw: Optional[str]) -> datetime:
    """解析前端传入的窗止时刻；缺省取当前时刻。

    无时区标注的字符串按 TIME_ZONE（Asia/Shanghai）解释，保证与服务端口径一致。
    """
    if raw is None or raw == "":
        return timezone.now()
    text = raw.strip()
    parsed: Optional[datetime] = None
    for candidate in (text, text.replace(" ", "T"), text.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("end_at 时刻格式无法识别，请使用 YYYY-MM-DDTHH:MM 格式")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, ZoneInfo(settings.TIME_ZONE))
    return parsed


def normalize_width(width_hours: Any) -> int:
    try:
        width = int(width_hours)
    except (TypeError, ValueError):
        raise ValueError("窗宽必须是 1~24 的整数小时")
    if width < MIN_WIDTH_HOURS or width > MAX_WIDTH_HOURS:
        raise ValueError(f"窗宽仅支持 {MIN_WIDTH_HOURS}~{MAX_WIDTH_HOURS} 小时")
    return width


def window_bounds(width_hours: int, end_at: datetime) -> tuple[datetime, datetime]:
    """窗止向整点（本地时区）取整，窗起 = 窗止 - 窗宽。"""
    tz = ZoneInfo(settings.TIME_ZONE)
    local_end = end_at.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    local_start = local_end - timedelta(hours=width_hours)
    return local_start, local_end


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _percent_text(count: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{count / total * 100:.1f}%"


def _row_payload(row: OffsetSubmission, bucket: str) -> dict:
    return {
        "id": row.id,
        "tool_code": row.tool_code,
        "offset_um": row.offset_um,
        "status": row.status,
        "verdict": row.verdict or "",
        "bucket": bucket,
        "created_at": _isoformat(row.created_at),
        "reviewed_at": _isoformat(row.reviewed_at),
    }


def compute_inventory(width_hours: Any, raw_end_at: Optional[str] = None) -> dict:
    """单一聚合入口：同时产出四类量、占比与按窗明细，并做内部对账。

    专页 summary 接口与 rows 接口都调用本函数，相同入参必得相同结果——
    聚合数字、专页卡片、明细列表三路同源。
    """
    width = normalize_width(width_hours)
    end_input = parse_end_at(raw_end_at)
    start, end = window_bounds(width, end_input)

    # 明细查询就是聚合的唯一数据源：先取窗内全部行，再在 Python 侧分桶，
    # 聚合与明细天然同源；窗边界走 ORM 的 >= / <，即左闭右开。
    window_rows = list(
        OffsetSubmission.objects.filter(
            created_at__gte=start,
            created_at__lt=end,
        ).order_by("created_at", "id")
    )

    pass_rows: list[OffsetSubmission] = []
    fail_rows: list[OffsetSubmission] = []
    unsettled_rows: list[OffsetSubmission] = []
    for row in window_rows:
        if row.status == OffsetSubmission.Status.DONE and (
            row.verdict == OffsetSubmission.Verdict.PASS
            or row.verdict == OffsetSubmission.Verdict.FAIL
        ):
            if row.verdict == OffsetSubmission.Verdict.PASS:
                pass_rows.append(row)
            else:
                fail_rows.append(row)
        else:
            # 创建时刻在窗内但尚未结清：不得进入合格/超差桶
            unsettled_rows.append(row)

    counts = {
        "pass": len(pass_rows),
        "fail": len(fail_rows),
        "unsettled": len(unsettled_rows),
        "total": len(window_rows),
    }

    buckets = [
        {"key": "pass", "label": "合格", "count": counts["pass"],
         "ratio_text": _percent_text(counts["pass"], counts["total"])},
        {"key": "fail", "label": "超差", "count": counts["fail"],
         "ratio_text": _percent_text(counts["fail"], counts["total"])},
        {"key": "unsettled", "label": "未结清", "count": counts["unsettled"],
         "ratio_text": _percent_text(counts["unsettled"], counts["total"])},
        # 合计行的百分比仅占位（100%），不作为任何桶的占比参与对账
        {"key": "total", "label": "合计", "count": counts["total"],
         "ratio_text": _percent_text(counts["total"], counts["total"])},
    ]

    rows = (
        [_row_payload(r, "pass") for r in pass_rows]
        + [_row_payload(r, "fail") for r in fail_rows]
        + [_row_payload(r, "unsettled") for r in unsettled_rows]
    )

    # 内部对账：桶量必须与明细逐行分类结果一致，误差恒为 0
    recheck = {"pass": 0, "fail": 0, "unsettled": 0}
    for item in rows:
        recheck[item["bucket"]] += 1
    bucket_diffs = {key: counts[key] - recheck[key] for key in recheck}
    total_diff = counts["total"] - len(rows)

    payload = {
        "window": {
            "width_hours": width,
            "start": _isoformat(start),
            "end": _isoformat(end),
            "end_input": _isoformat(end_input),
        },
        "criterion": CRITERION_TEXT,
        "counts": counts,
        "buckets": buckets,
        "rows": rows,
        "reconciliation": {
            "bucket_diffs": bucket_diffs,
            "total_diff": total_diff,
            "difference": sum(abs(v) for v in bucket_diffs.values()) + abs(total_diff),
            "statement": (
                f"本窗共 {counts['total']} 行：合格 {counts['pass']}、"
                f"超差 {counts['fail']}、未结清 {counts['unsettled']}；"
                f"四类量与按窗重查明细逐桶对账误差 "
                f"{sum(abs(v) for v in bucket_diffs.values()) + abs(total_diff)}。"
            ),
        },
    }
    return payload
