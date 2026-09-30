from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

from desk.models import OffsetSubmission

# 窗口口径：创建时刻落在 [start, end) 左闭右开区间内的行才属于本窗。
# 压线规则：created_at == start 入窗，created_at == end 不入窗。
# 占比分母：仅已结清（status=done 且有合格/超差结论）的行进桶参与占比；
# 创建时刻在窗内但尚未结清的行只计入「未结清」，绝不算进合格桶或超差桶。
WINDOW_INCLUSIVE_START = True
WINDOW_INCLUSIVE_END = False
DEFAULT_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 24 * 30

BUCKET_PASS = "pass"
BUCKET_FAIL = "fail"
BUCKET_UNSETTLED = "unsettled"


def evaluate_verdict(offset_um: int) -> str:
    if abs(offset_um) <= settings.OFFSET_TOLERANCE_UM:
        return OffsetSubmission.Verdict.PASS
    return OffsetSubmission.Verdict.FAIL


def apply_verdict(submission: OffsetSubmission) -> None:
    submission.verdict = evaluate_verdict(submission.offset_um)
    submission.status = OffsetSubmission.Status.DONE
    submission.reviewed_at = timezone.now()
    submission.save(
        update_fields=["verdict", "status", "reviewed_at"],
    )


def _as_aware(value: datetime) -> datetime:
    """naive 时间按当前时区解释，保证前后端传入的本地时间与库内 UTC 可比较。"""
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, timezone.get_current_timezone())


def resolve_window(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    hours: Optional[float] = None,
) -> tuple[datetime, datetime]:
    """把入参规整成 [start, end) 半开窗（均为 aware datetime）。

    优先级：同时给出 start/end > hours 回溯窗 > 默认最近一小时。
    """
    now = timezone.now().replace(microsecond=0)
    if start is not None and end is not None:
        window_start = _as_aware(start)
        window_end = _as_aware(end)
    else:
        if hours is None:
            hours = DEFAULT_WINDOW_HOURS
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            raise ValueError("窗宽必须是数字（小时）")
        if hours <= 0:
            raise ValueError("窗宽必须大于 0")
        if hours > MAX_WINDOW_HOURS:
            raise ValueError(f"窗宽不能超过 {MAX_WINDOW_HOURS} 小时")
        if start is not None:
            window_start = _as_aware(start)
        elif end is not None:
            window_start = _as_aware(end) - timedelta(hours=hours)
        else:
            window_start = now - timedelta(hours=hours)
        window_end = _as_aware(end) if end is not None else window_start + timedelta(hours=hours)
    if window_end <= window_start:
        raise ValueError("窗的结束时刻必须晚于开始时刻")
    return window_start, window_end


@dataclass
class InventoryRow:
    id: int
    tool_code: str
    offset_um: int
    status: str
    verdict: str
    created_at: datetime
    reviewed_at: Optional[datetime]
    bucket: str


@dataclass
class WindowInventory:
    """一次查询的结果：汇总桶与明细行同源，前端不得再自行加总。"""

    start: datetime
    end: datetime
    pass_count: int = 0
    fail_count: int = 0
    unsettled_count: int = 0
    rows: list[InventoryRow] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return self.pass_count + self.fail_count + self.unsettled_count

    @property
    def settled_total(self) -> int:
        return self.pass_count + self.fail_count

    @property
    def pass_ratio(self) -> Optional[float]:
        return self.pass_count / self.settled_total if self.settled_total else None

    @property
    def fail_ratio(self) -> Optional[float]:
        return self.fail_count / self.settled_total if self.settled_total else None

    def as_payload(self) -> dict:
        return {
            "window": {
                "start": self.start,
                "end": self.end,
                "boundary_rule": "[start, end)：创建时刻压线起点入窗、压线终点不入窗",
                "ratio_basis": "占比分母为窗内已结清总数，未结清不进合格/超差桶",
            },
            "counts": {
                "pass": self.pass_count,
                "fail": self.fail_count,
                "unsettled": self.unsettled_count,
                "total": self.total_count,
                "settled_total": self.settled_total,
            },
            "ratios": {
                "pass": self.pass_ratio,
                "fail": self.fail_ratio,
            },
            "items": [
                {
                    "id": r.id,
                    "tool_code": r.tool_code,
                    "offset_um": r.offset_um,
                    "status": r.status,
                    "verdict": r.verdict,
                    "created_at": r.created_at,
                    "reviewed_at": r.reviewed_at,
                    "bucket": r.bucket,
                }
                for r in self.rows
            ],
        }


def _bucket_for(row: OffsetSubmission) -> str:
    if row.status == OffsetSubmission.Status.DONE:
        if row.verdict == OffsetSubmission.Verdict.PASS:
            return BUCKET_PASS
        if row.verdict == OffsetSubmission.Verdict.FAIL:
            return BUCKET_FAIL
    # pending / processing，或异常的已完成无结论：一律算未结清，不进合格/超差桶。
    return BUCKET_UNSETTLED


def window_inventory(start: datetime, end: datetime) -> WindowInventory:
    """服务端单一数据源：窗内行一次性取出，汇总量与明细都来自这同一批行。"""
    window_rows = list(
        OffsetSubmission.objects.filter(
            created_at__gte=start,
            created_at__lt=end,
        ).order_by("created_at", "id")
    )
    inventory = WindowInventory(start=start, end=end)
    for row in window_rows:
        bucket = _bucket_for(row)
        if bucket == BUCKET_PASS:
            inventory.pass_count += 1
        elif bucket == BUCKET_FAIL:
            inventory.fail_count += 1
        else:
            inventory.unsettled_count += 1
        inventory.rows.append(
            InventoryRow(
                id=row.id,
                tool_code=row.tool_code,
                offset_um=row.offset_um,
                status=row.status,
                verdict=row.verdict or "",
                created_at=row.created_at,
                reviewed_at=row.reviewed_at,
                bucket=bucket,
            )
        )
    return inventory
