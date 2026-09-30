"""超差盘点：时间窗聚合、压线边界、未结清口径、汇总-明细同源对账。"""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import TestCase
from django.utils import timezone
from ninja.testing import TestClient

from desk.api import api
from desk.auth_utils import create_access_token
from desk.models import OffsetSubmission, User
from desk.services import resolve_window, window_inventory


def make_row(offset_um, *, status, verdict="", created_at=None, reviewed=True):
    row = OffsetSubmission.objects.create(
        tool_code=f"T{offset_um}",
        offset_um=offset_um,
        status=status,
        verdict=verdict,
    )
    if created_at is not None:
        OffsetSubmission.objects.filter(pk=row.pk).update(created_at=created_at)
    if reviewed and status == OffsetSubmission.Status.DONE:
        OffsetSubmission.objects.filter(pk=row.pk).update(reviewed_at=created_at or timezone.now())
    row.refresh_from_db()
    return row


def settled_pass(created_at):
    return make_row(
        5,
        status=OffsetSubmission.Status.DONE,
        verdict=OffsetSubmission.Verdict.PASS,
        created_at=created_at,
    )


def settled_fail(created_at):
    return make_row(
        20,
        status=OffsetSubmission.Status.DONE,
        verdict=OffsetSubmission.Verdict.FAIL,
        created_at=created_at,
    )


class WindowInventoryTests(TestCase):
    def setUp(self):
        self.t0 = timezone.now().replace(microsecond=0) - timedelta(hours=1)

    def test_reconciliation_counts_match_items_zero_error(self):
        # 同一响应里的汇总四类量必须等于按明细行逐桶计数，误差为零（三路同源的服务端口径）。
        settled_pass(self.t0 + timedelta(minutes=5))
        settled_fail(self.t0 + timedelta(minutes=6))
        make_row(50, status=OffsetSubmission.Status.PENDING, created_at=self.t0 + timedelta(minutes=7))
        make_row(50, status=OffsetSubmission.Status.PROCESSING, created_at=self.t0 + timedelta(minutes=8))
        inv = window_inventory(self.t0, self.t0 + timedelta(hours=1))
        payload = inv.as_payload()

        items = payload["items"]
        bucket_counts = {"pass": 0, "fail": 0, "unsettled": 0}
        for item in items:
            bucket_counts[item["bucket"]] += 1

        self.assertEqual(payload["counts"]["pass"], bucket_counts["pass"])
        self.assertEqual(payload["counts"]["fail"], bucket_counts["fail"])
        self.assertEqual(payload["counts"]["unsettled"], bucket_counts["unsettled"])
        self.assertEqual(payload["counts"]["total"], len(items))
        self.assertEqual(
            payload["counts"]["settled_total"],
            bucket_counts["pass"] + bucket_counts["fail"],
        )
        # 占比也必须与汇总量自洽，而不是另算一套。
        self.assertAlmostEqual(payload["ratios"]["fail"], 1 / 2)
        self.assertAlmostEqual(payload["ratios"]["pass"], 1 / 2)

    def test_unsettled_rows_never_enter_pass_or_fail_buckets(self):
        # 哪怕刀补绝对值远超 12µm，只要创建时刻未结清就不许进超差桶。
        make_row(99, status=OffsetSubmission.Status.PENDING, created_at=self.t0)
        make_row(99, status=OffsetSubmission.Status.PROCESSING, created_at=self.t0)
        make_row(
            99,
            status=OffsetSubmission.Status.DONE,
            verdict="",
            created_at=self.t0 + timedelta(minutes=1),
        )
        inv = window_inventory(self.t0, self.t0 + timedelta(hours=1))
        self.assertEqual(inv.pass_count, 0)
        self.assertEqual(inv.fail_count, 0)
        self.assertEqual(inv.unsettled_count, 3)
        self.assertIsNone(inv.pass_ratio)
        self.assertIsNone(inv.fail_ratio)

    def test_boundary_start_inclusive_end_exclusive(self):
        # 压线点：created_at == start 入桶；created_at == end 不入桶。
        on_start = settled_pass(self.t0)
        on_end = settled_fail(self.t0 + timedelta(hours=1))
        settled_pass(self.t0 - timedelta(microseconds=1))
        inv = window_inventory(self.t0, self.t0 + timedelta(hours=1))
        ids = {r.id for r in inv.rows}
        self.assertIn(on_start.id, ids)
        self.assertNotIn(on_end.id, ids)
        self.assertEqual(inv.pass_count, 1)
        self.assertEqual(inv.fail_count, 0)

    def test_fail_batch_plus_boundary_pass_raises_fail_ratio(self):
        # 基线窗：一笔合格，超差占比 0。
        settled_pass(self.t0 - timedelta(minutes=90))
        baseline = window_inventory(self.t0 - timedelta(hours=2), self.t0 - timedelta(hours=1))
        self.assertEqual(baseline.fail_count, 0)
        self.assertEqual(baseline.pass_count, 1)
        self.assertEqual(baseline.fail_ratio, 0)

        # 故意超差三笔，再在窗起点压线上合格一笔。
        batch_start = self.t0
        settled_pass(batch_start)  # 压线合格
        settled_fail(batch_start + timedelta(minutes=1))
        settled_fail(batch_start + timedelta(minutes=2))
        settled_fail(batch_start + timedelta(minutes=3))

        current = window_inventory(batch_start, batch_start + timedelta(hours=1))
        self.assertGreaterEqual(current.pass_count, 1)
        self.assertEqual(current.fail_count, 3)
        self.assertAlmostEqual(current.fail_ratio, 0.75)
        # 本窗超差占比相对基线窗升高。
        self.assertGreater(current.fail_ratio, baseline.fail_ratio)

    def test_narrowed_window_excluding_batch_is_empty(self):
        batch_start = self.t0
        settled_pass(batch_start)
        for minute in (1, 2, 3):
            settled_fail(batch_start + timedelta(minutes=minute))

        # 窗收窄到完全不含这批时刻：总量为空，占比为空（下降或空）。
        narrow = window_inventory(
            batch_start + timedelta(hours=1),
            batch_start + timedelta(hours=2),
        )
        self.assertEqual(narrow.total_count, 0)
        self.assertEqual(narrow.pass_count, 0)
        self.assertEqual(narrow.fail_count, 0)
        self.assertIsNone(narrow.fail_ratio)
        self.assertIsNone(narrow.pass_ratio)

    def test_resolve_window_validation(self):
        with self.assertRaises(ValueError):
            resolve_window(hours=0)
        with self.assertRaises(ValueError):
            resolve_window(start=self.t0, end=self.t0)
        start, end = resolve_window(hours=2)
        self.assertEqual(end - start, timedelta(hours=2))


class InventoryApiTests(TestCase):
    def setUp(self):
        self.client = TestClient(api)
        self.machinist = User.objects.create_user("m", password="x", role=User.Role.MACHINIST)
        self.auditor = User.objects.create_user("a", password="x", role=User.Role.AUDITOR)
        self.t0 = timezone.now().replace(microsecond=0) - timedelta(hours=1)

    def auth(self, user):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}

    def test_endpoint_requires_auth(self):
        resp = self.client.get("/inventory/window")
        self.assertEqual(resp.status_code, 401)

    def test_auditor_read_only_window_query(self):
        settled_fail(self.t0 + timedelta(minutes=10))
        qs = urlencode(
            {
                "start": (self.t0 - timedelta(minutes=5)).isoformat(),
                "end": (self.t0 + timedelta(minutes=20)).isoformat(),
            }
        )
        resp = self.client.get(f"/inventory/window?{qs}", headers=self.auth(self.auditor))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["fail"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertIn("[start, end)", body["window"]["boundary_rule"])

    def test_explicit_window_boundary_consistent_via_api(self):
        # 前端只把窗边界 ISO 时刻传给服务端，由同一套 gte/lt 判定：压线起点入窗。
        settled_pass(self.t0)
        settled_fail(self.t0 + timedelta(hours=1))
        other = settled_pass(self.t0 - timedelta(hours=3))
        qs = urlencode(
            {
                "start": self.t0.isoformat(),
                "end": (self.t0 + timedelta(hours=1)).isoformat(),
            }
        )
        resp = self.client.get(f"/inventory/window?{qs}", headers=self.auth(self.machinist))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["pass"], 1)
        self.assertEqual(body["counts"]["fail"], 0)
        self.assertEqual(body["counts"]["total"], len(body["items"]))
        ids = {item["id"] for item in body["items"]}
        self.assertNotIn(other.id, ids)

    def test_bad_window_returns_400(self):
        resp = self.client.get(
            "/inventory/window?hours=-1",
            headers=self.auth(self.machinist),
        )
        self.assertEqual(resp.status_code, 400)
