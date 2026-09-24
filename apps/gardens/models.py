from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone


class Garden(models.Model):
    name = models.CharField("茶园名称", max_length=120)
    altitudeBand = models.CharField("海拔带", max_length=60)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "茶园"
        verbose_name_plural = "茶园"

    def __str__(self):
        return self.name


class Trough(models.Model):
    STATUS_LOADING = "loading"
    STATUS_WITHERING = "withering"
    STATUS_READY = "ready"
    STATUS_CHOICES = [
        (STATUS_LOADING, "装叶中"),
        (STATUS_WITHERING, "萎凋中"),
        (STATUS_READY, "可下槽"),
    ]

    garden = models.ForeignKey(
        Garden,
        on_delete=models.CASCADE,
        related_name="troughs",
        verbose_name="茶园",
    )
    troughCode = models.CharField("槽位编号", max_length=40)
    cultivar = models.CharField("茶树品种", max_length=80)
    loadKg = models.DecimalField("装叶量(kg)", max_digits=10, decimal_places=2)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_LOADING,
    )

    class Meta:
        ordering = ["garden__name", "troughCode"]
        verbose_name = "萎凋槽"
        verbose_name_plural = "萎凋槽"
        constraints = [
            models.UniqueConstraint(
                fields=["garden", "troughCode"],
                name="uniq_trough_code_per_garden",
            ),
        ]

    def __str__(self):
        return f"{self.garden.name}-{self.troughCode}"

    def latest_batch(self):
        return self.batches.order_by("-startedAt", "-id").first()

    def turn_settlement_check(self):
        """翻堆节拍账的统一核对查询。

        销账能否完成、槽状态能否改「可下槽」放行，都走本函数，
        保证两条业务路径读到的是同一组事实：

        返回 (未销账条数, 最新萎凋批次)：
        - 未销账条数 > 0：该槽存在待销账，禁止改「可下槽」；
        - 最新批次为空 / 实测含水率未填：销账与放行一律拒绝。
        """
        open_count = self.turn_ledgers.filter(settled=False).count()
        latest = self.latest_batch()
        return open_count, latest

    def open_turn_count(self):
        """该槽当前未销账的翻堆节拍账条数。"""
        return self.turn_ledgers.filter(settled=False).count()

    def clean(self):
        super().clean()
        if self.status != self.STATUS_READY:
            return
        open_count, latest = 0, None
        if self.pk:
            # 与销账动作共用同一查询函数核对。
            open_count, latest = self.turn_settlement_check()
        if open_count > 0:
            raise ValidationError(
                {
                    "status": (
                        f"无法设为可下槽：该槽尚有 {open_count} 条翻堆节拍账待销账，"
                        "请先完成销账再放行下槽。"
                    )
                }
            )
        if latest is None or latest.actualMoisture is None or latest.actualMoisture > 40:
            raise ValidationError(
                {
                    "status": "无法设为可下槽：最新萎凋批次的实测含水率为空或高于 40%。"
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WitherBatch(models.Model):
    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="batches",
        verbose_name="萎凋槽",
    )
    startedAt = models.DateTimeField("开始时间")
    targetMoisture = models.DecimalField(
        "目标含水率(%)", max_digits=5, decimal_places=2
    )
    actualMoisture = models.DecimalField(
        "实测含水率(%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rollGrade = models.CharField("揉捻等级", max_length=40)

    class Meta:
        ordering = ["-startedAt", "-id"]
        verbose_name = "萎凋批次"
        verbose_name_plural = "萎凋批次"

    def __str__(self):
        return f"{self.trough} @ {self.startedAt:%Y-%m-%d %H:%M}"


class TurnLedger(models.Model):
    """翻堆节拍账：账挂槽位，未销账前该槽不得进入「可下槽」。"""

    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="turn_ledgers",
        verbose_name="所属槽位",
    )
    sequence = models.PositiveIntegerField("翻堆序号")
    plannedAt = models.DateTimeField("计划翻堆时刻")
    doneAt = models.DateTimeField("实做时刻", null=True, blank=True)
    operator = models.CharField("当班人", max_length=80)
    settled = models.BooleanField("是否销账", default=False)

    class Meta:
        ordering = ["trough", "sequence"]
        verbose_name = "翻堆节拍账"
        verbose_name_plural = "翻堆节拍账"
        constraints = [
            models.UniqueConstraint(
                fields=["trough", "sequence"],
                name="uniq_turn_sequence_per_trough",
            ),
        ]
        indexes = [
            models.Index(fields=["trough", "settled"], name="turn_open_idx"),
        ]

    def __str__(self):
        state = "已销账" if self.settled else "待销账"
        return f"{self.trough} 第{self.sequence}次翻堆（{state}）"

    def clean(self):
        super().clean()
        # 新建账：槽必须正处于萎凋中，装叶中/可下槽一律不准建账。
        if not self.pk and self.trough_id:
            if self.trough.status != Trough.STATUS_WITHERING:
                raise ValidationError(
                    {
                        "trough": (
                            "只有「萎凋中」的槽位才能新建翻堆节拍账，"
                            "装叶中、可下槽状态一律不准建账。"
                        )
                    }
                )
        # 同槽序号唯一：在模型层给出中文校验（DB 唯一约束兜底并发）。
        if self.trough_id and self.sequence:
            dup = type(self).objects.filter(
                trough_id=self.trough_id, sequence=self.sequence
            )
            if self.pk:
                dup = dup.exclude(pk=self.pk)
            if dup.exists():
                raise ValidationError(
                    {"sequence": "同槽翻堆序号必须唯一，该序号已存在。"}
                )
        # 已填写销账信息时的一致性校验（销账动作 settle() 同样会校验）。
        if self.doneAt is not None and self.doneAt < self.plannedAt:
            raise ValidationError({"doneAt": "实做时刻不得早于计划翻堆时刻。"})
        if self.settled:
            if self.doneAt is None:
                raise ValidationError({"doneAt": "销账必须写入实做时刻。"})
            latest = self.trough.latest_batch() if self.trough_id else None
            if latest is None or latest.actualMoisture is None:
                raise ValidationError(
                    {"__all__": "无法销账：该槽最新萎凋批次尚未录入实测含水率。"}
                )

    def save(self, *args, **kwargs):
        # 序号在 full_clean 之前补齐，否则非空字段校验会先报错。
        if not self.pk and self.trough_id and not self.sequence:
            last = (
                type(self)
                .objects.filter(trough_id=self.trough_id)
                .aggregate(max_seq=Max("sequence"))["max_seq"]
            )
            self.sequence = (last or 0) + 1
        self.full_clean()
        return super().save(*args, **kwargs)

    def settle(self, done_at=None):
        """销账：写入实做时刻，并在同一事务内核对最新批次实测含水。

        - 实做时刻不得早于计划翻堆时刻；
        - 同事务调用 Trough.turn_settlement_check() 核对最新批次已有实测含水，
          缺失则整笔回滚、拒绝销账；
        - 已销账的账目不得重复销账。
        """
        if done_at is None:
            done_at = timezone.now()
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.settled:
                raise ValidationError({"__all__": "该翻堆节拍账已销账，不能重复销账。"})
            if done_at < locked.plannedAt:
                raise ValidationError({"doneAt": "实做时刻不得早于计划翻堆时刻。"})
            # 与槽状态放行共用同一查询函数。
            _open_count, latest = locked.trough.turn_settlement_check()
            if latest is None or latest.actualMoisture is None:
                raise ValidationError(
                    {"__all__": "无法销账：该槽最新萎凋批次尚未录入实测含水率。"}
                )
            locked.settled = True
            locked.doneAt = done_at
            locked.save()
        self.refresh_from_db()
        return self
