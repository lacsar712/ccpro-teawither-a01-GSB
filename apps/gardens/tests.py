from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Garden, Trough, TurnLedger, WitherBatch


def make_garden(name="测试园"):
    return Garden.objects.create(name=name, altitudeBand="600-800m")


def make_trough(status=Trough.STATUS_WITHERING, code="T-01", moisture=None):
    t = Trough.objects.create(
        garden=make_garden(code),
        troughCode=code,
        cultivar="福鼎大白",
        loadKg=Decimal("100.00"),
        status=status,
    )
    WitherBatch.objects.create(
        trough=t,
        startedAt=timezone.now() - timezone.timedelta(hours=6),
        targetMoisture=Decimal("38.00"),
        actualMoisture=moisture,
        rollGrade="一级",
    )
    return t


class BuildLedgerTests(TestCase):
    def test_sequence_starts_at_one_and_increments(self):
        t = make_trough()
        l1 = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        l2 = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        self.assertEqual(l1.sequence, 1)
        self.assertEqual(l2.sequence, 2)

    def test_create_allowed_only_when_withering(self):
        planned = timezone.now()

        loading = make_trough(status=Trough.STATUS_LOADING, code="L-1")
        with self.assertRaises(ValidationError):
            TurnLedger.objects.create(
                trough=loading, plannedAt=planned, operator="张三"
            )

        ready = make_trough(
            status=Trough.STATUS_WITHERING, code="R-1", moisture=Decimal("35.00")
        )
        ready.status = Trough.STATUS_READY
        ready.save()
        with self.assertRaises(ValidationError):
            TurnLedger.objects.create(
                trough=ready, plannedAt=planned, operator="张三"
            )

    def test_sequence_unique_per_trough(self):
        t = make_trough()
        TurnLedger.objects.create(
            trough=t, sequence=1, plannedAt=timezone.now(), operator="张三"
        )
        # 序号从 1 起，不同槽各自计数。
        other = make_trough(code="T-02")
        other_first = TurnLedger.objects.create(
            trough=other, plannedAt=timezone.now(), operator="李四"
        )
        self.assertEqual(other_first.sequence, 1)
        # 同槽强制唯一。
        with self.assertRaises(ValidationError):
            TurnLedger.objects.create(
                trough=t, sequence=1, plannedAt=timezone.now(), operator="王五"
            )

    def test_default_unsettled_and_done_null(self):
        t = make_trough()
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        self.assertFalse(led.settled)
        self.assertIsNone(led.doneAt)


class SettleTests(TestCase):
    def test_settle_writes_done_time(self):
        t = make_trough(moisture=Decimal("36.00"))
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="张三",
        )
        done = timezone.now()
        led.settle(done)
        led.refresh_from_db()
        self.assertTrue(led.settled)
        self.assertEqual(led.doneAt, done)

    def test_settle_rejects_done_before_planned(self):
        t = make_trough(moisture=Decimal("36.00"))
        planned = timezone.now()
        led = TurnLedger.objects.create(trough=t, plannedAt=planned, operator="张三")
        with self.assertRaises(ValidationError):
            led.settle(planned - timezone.timedelta(minutes=1))
        led.refresh_from_db()
        self.assertFalse(led.settled)
        self.assertIsNone(led.doneAt)

    def test_settle_rejects_when_moisture_missing(self):
        # 最新批次实测含水为空 -> 拒绝销账（核心防漏点）。
        t = make_trough(moisture=None)
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="张三",
        )
        with self.assertRaises(ValidationError):
            led.settle()
        led.refresh_from_db()
        self.assertFalse(led.settled)

    def test_settle_rejects_when_no_batch(self):
        t = Trough.objects.create(
            garden=make_garden("无批次"), troughCode="X-1",
            cultivar="x", loadKg=Decimal("10"), status=Trough.STATUS_WITHERING,
        )
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        with self.assertRaises(ValidationError):
            led.settle()

    def test_settle_accepts_present_moisture_even_if_high(self):
        # 销账只要求“已有实测含水”，不卡 40% 门槛（40% 是放行可下槽的规则）。
        t = make_trough(moisture=Decimal("42.00"))
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="张三",
        )
        led.settle()
        self.assertTrue(led.settled)

    def test_settle_twice_rejected(self):
        t = make_trough(moisture=Decimal("36.00"))
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="张三",
        )
        led.settle()
        with self.assertRaises(ValidationError):
            led.settle()


class TroughReadyGateTests(TestCase):
    def test_empty_ledger_allows_ready_when_moisture_ok(self):
        # 空账表 + 含水率合格 -> 可改可下槽（明确防漏点）。
        t = make_trough(moisture=Decimal("37.50"))
        t.status = Trough.STATUS_READY
        t.save()  # 不应抛异常
        self.assertEqual(t.status, Trough.STATUS_READY)

    def test_open_ledger_blocks_ready_with_chinese_message(self):
        t = make_trough(moisture=Decimal("37.50"))
        TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        t.status = Trough.STATUS_READY
        with self.assertRaises(ValidationError) as ctx:
            t.save()
        message = str(ctx.exception.message_dict.get("status", ""))
        self.assertIn("待销账", message)
        t.refresh_from_db()
        self.assertEqual(t.status, Trough.STATUS_WITHERING)

    def test_all_settled_allows_ready(self):
        t = make_trough(moisture=Decimal("37.50"))
        l1 = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=2),
            operator="张三",
        )
        l2 = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="李四",
        )
        l1.settle()
        l2.settle()
        t.status = Trough.STATUS_READY
        t.save()
        self.assertEqual(t.status, Trough.STATUS_READY)

    def test_moisture_rule_still_enforced(self):
        t = make_trough(moisture=None)
        t.status = Trough.STATUS_READY
        with self.assertRaises(ValidationError):
            t.save()

        t2 = make_trough(moisture=Decimal("42.00"), code="T-HI")
        t2.status = Trough.STATUS_READY
        with self.assertRaises(ValidationError):
            t2.save()

    def test_settlement_check_is_shared(self):
        t = make_trough(moisture=Decimal("37.50"))
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        open_count, latest = t.turn_settlement_check()
        self.assertEqual(open_count, 1)
        self.assertIsNotNone(latest)
        led.settle()
        open_count, _ = t.turn_settlement_check()
        self.assertEqual(open_count, 0)


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="witherer", password="123456"
        )
        self.client.force_login(self.user)

    def test_settle_post_blocks_without_moisture(self):
        t = make_trough(moisture=None)
        led = TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now() - timezone.timedelta(hours=1),
            operator="张三",
        )
        resp = self.client.post(reverse("turn_settle", args=[led.pk]))
        self.assertEqual(resp.status_code, 302)
        led.refresh_from_db()
        self.assertFalse(led.settled)

    def test_trough_list_shows_open_count(self):
        t = make_trough(moisture=Decimal("37.50"))
        TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        resp = self.client.get(reverse("trough_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2 条待销账")

    def test_turn_list_page_renders(self):
        t = make_trough(moisture=Decimal("37.50"))
        TurnLedger.objects.create(
            trough=t, plannedAt=timezone.now(), operator="张三"
        )
        resp = self.client.get(reverse("turn_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "待销账")
