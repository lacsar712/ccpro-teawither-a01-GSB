from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
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

    def unsettled_turn_count(self):
        """未销账条数。

        「销账是否完成」与「槽态改可下槽是否放行」走这同一查询函数。
        """
        if not self.pk:
            return 0
        return self.turn_ledgers.filter(isSettled=False).count()

    def clean(self):
        super().clean()
        if self.status != self.STATUS_READY:
            return
        pending = self.unsettled_turn_count()
        if pending > 0:
            raise ValidationError(
                {
                    "status": f"无法设为可下槽：尚有 {pending} 条翻堆节拍账待销账。"
                }
            )
        latest = None
        if self.pk:
            latest = (
                WitherBatch.objects.filter(trough_id=self.pk)
                .order_by("-startedAt", "-id")
                .first()
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
    """翻堆节拍账：账挂槽位，未销账前该槽不得进入可下槽。"""

    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="turn_ledgers",
        verbose_name="所属槽位",
    )
    seq = models.PositiveIntegerField(
        "翻堆序号",
        validators=[MinValueValidator(1)],
    )
    plannedAt = models.DateTimeField("计划翻堆时刻")
    actualAt = models.DateTimeField("实做时刻", null=True, blank=True)
    operator = models.CharField("当班人", max_length=60)
    isSettled = models.BooleanField("是否销账", default=False)

    class Meta:
        ordering = ["trough__garden__name", "trough__troughCode", "seq"]
        verbose_name = "翻堆节拍账"
        verbose_name_plural = "翻堆节拍账"
        constraints = [
            models.UniqueConstraint(
                fields=["trough", "seq"],
                name="uniq_turn_seq_per_trough",
            ),
        ]

    def __str__(self):
        return f"{self.trough} 第{self.seq}翻"

    def clean(self):
        super().clean()
        # 新建账时槽须正处于萎凋中，其它两种槽态一律不准建账
        if self._state.adding and self.trough_id is not None:
            if self.trough.status != Trough.STATUS_WITHERING:
                raise ValidationError(
                    {
                        "trough": "仅「萎凋中」的槽位可新建翻堆节拍账，装叶中、可下槽一律不准建账。"
                    }
                )
        # 实做不得早于计划
        if (
            self.actualAt is not None
            and self.plannedAt is not None
            and self.actualAt < self.plannedAt
        ):
            raise ValidationError(
                {"actualAt": "实做时刻不得早于计划翻堆时刻。"}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @transaction.atomic
    def settle(self):
        """销账：写入实做时刻，同事务核对最新批次已有实测含水，缺则拒绝。"""
        entry = TurnLedger.objects.select_for_update().get(pk=self.pk)
        if entry.isSettled:
            raise ValidationError("该条翻堆节拍账已销账，无需重复销账。")
        latest = entry.trough.latest_batch()
        if latest is None or latest.actualMoisture is None:
            raise ValidationError(
                "拒绝销账：最新萎凋批次尚无实测含水率，请先补录实测。"
            )
        entry.actualAt = timezone.now()
        if entry.actualAt < entry.plannedAt:
            raise ValidationError("拒绝销账：实做时刻不得早于计划翻堆时刻。")
        entry.isSettled = True
        entry.save()
