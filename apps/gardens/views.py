from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    UpdateView,
)

from .forms import (
    GardenForm,
    TroughForm,
    TurnLedgerCreateForm,
    TurnLedgerSettleForm,
    WitherBatchForm,
)
from .models import Garden, Trough, TurnLedger, WitherBatch


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


@login_required
def home(request):
    context = {
        "garden_count": Garden.objects.count(),
        "trough_count": Trough.objects.count(),
        "batch_count": WitherBatch.objects.count(),
        "ready_count": Trough.objects.filter(status=Trough.STATUS_READY).count(),
        "withering_count": Trough.objects.filter(
            status=Trough.STATUS_WITHERING
        ).count(),
        "loading_count": Trough.objects.filter(
            status=Trough.STATUS_LOADING
        ).count(),
    }
    return render(request, "home.html", context)


# ---- Garden ----


class GardenListView(LoginRequiredMixin, ListView):
    model = Garden
    template_name = "gardens/list.html"
    context_object_name = "gardens"

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "gardens/_table.html",
                {"gardens": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class GardenCreateView(LoginRequiredMixin, CreateView):
    model = Garden
    form_class = GardenForm
    template_name = "gardens/form.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已创建")
        response = super().form_valid(form)
        if _wants_htmx(self.request):
            return redirect("garden_list")
        return response


class GardenUpdateView(LoginRequiredMixin, UpdateView):
    model = Garden
    form_class = GardenForm
    template_name = "gardens/form.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已更新")
        return super().form_valid(form)


class GardenDeleteView(LoginRequiredMixin, DeleteView):
    model = Garden
    template_name = "gardens/confirm_delete.html"
    success_url = reverse_lazy("garden_list")

    def form_valid(self, form):
        messages.success(self.request, "茶园已删除")
        return super().form_valid(form)


# ---- Trough ----


class TroughListView(LoginRequiredMixin, ListView):
    model = Trough
    template_name = "troughs/list.html"
    context_object_name = "troughs"

    def get_queryset(self):
        # 一次性带未销账条数，避免槽列表逐条查询。
        return (
            Trough.objects.select_related("garden")
            .annotate(
                open_turn_count_=Count(
                    "turn_ledgers", filter=Q(turn_ledgers__settled=False)
                )
            )
        )

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "troughs/_table.html",
                {"troughs": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class TroughCreateView(LoginRequiredMixin, CreateView):
    model = Trough
    form_class = TroughForm
    template_name = "troughs/form.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已创建")
        return super().form_valid(form)


class TroughUpdateView(LoginRequiredMixin, UpdateView):
    model = Trough
    form_class = TroughForm
    template_name = "troughs/form.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已更新")
        return super().form_valid(form)


class TroughDeleteView(LoginRequiredMixin, DeleteView):
    model = Trough
    template_name = "troughs/confirm_delete.html"
    success_url = reverse_lazy("trough_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋槽已删除")
        return super().form_valid(form)


# ---- WitherBatch ----


class BatchListView(LoginRequiredMixin, ListView):
    model = WitherBatch
    template_name = "batches/list.html"
    context_object_name = "batches"

    def get_queryset(self):
        return WitherBatch.objects.select_related("trough", "trough__garden").all()

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "batches/_table.html",
                {"batches": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class BatchCreateView(LoginRequiredMixin, CreateView):
    model = WitherBatch
    form_class = WitherBatchForm
    template_name = "batches/form.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋批次已创建")
        return super().form_valid(form)


class BatchUpdateView(LoginRequiredMixin, UpdateView):
    model = WitherBatch
    form_class = WitherBatchForm
    template_name = "batches/form.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋批次已更新")
        return super().form_valid(form)


class BatchDeleteView(LoginRequiredMixin, DeleteView):
    model = WitherBatch
    template_name = "batches/confirm_delete.html"
    success_url = reverse_lazy("batch_list")

    def form_valid(self, form):
        messages.success(self.request, "萎凋批次已删除")
        return super().form_valid(form)


# ---- TurnLedger 翻堆节拍账 ----


class TurnLedgerListView(LoginRequiredMixin, ListView):
    model = TurnLedger
    template_name = "turns/list.html"
    context_object_name = "ledgers"

    def get_queryset(self):
        return TurnLedger.objects.select_related(
            "trough", "trough__garden"
        ).all()

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        if _wants_htmx(request):
            html = render_to_string(
                "turns/_table.html",
                {"ledgers": self.object_list},
                request=request,
            )
            return HttpResponse(html)
        return super().get(request, *args, **kwargs)


class TurnLedgerCreateView(LoginRequiredMixin, CreateView):
    model = TurnLedger
    form_class = TurnLedgerCreateForm
    template_name = "turns/form.html"
    success_url = reverse_lazy("turn_list")

    def form_valid(self, form):
        # 模型层 clean 强制：仅萎凋中的槽可建账，序号在同槽自动递增。
        # full_clean 已在表单校验阶段跑过；此处兜底并发建账导致的序号冲突。
        try:
            response = super().form_valid(form)
        except IntegrityError:
            form.add_error(
                None,
                "翻堆序号冲突（可能有并发建账），同槽序号必须唯一，请重试。",
            )
            return self.form_invalid(form)
        messages.success(self.request, "翻堆节拍账已建立")
        if _wants_htmx(self.request):
            return redirect("turn_list")
        return response


@login_required
def turn_settle(request, pk):
    """销账动作：写入实做时刻，并在同一事务内核对最新批次实测含水。"""
    ledger = get_object_or_404(TurnLedger, pk=pk)
    if request.method == "POST":
        form = TurnLedgerSettleForm(request.POST)
        if form.is_valid():
            try:
                ledger.settle(form.cleaned_data["doneAt"])
            except ValidationError as exc:
                for field, errs in getattr(exc, "message_dict", {}).items():
                    messages.error(request, "；".join(errs))
                return redirect("turn_list")
            messages.success(request, "翻堆节拍账已销账")
            return redirect("turn_list")
        for field, errs in form.errors.items():
            messages.error(request, "；".join(errs))
        return redirect("turn_list")
    # GET：展示销账确认页。
    form = TurnLedgerSettleForm()
    return render(
        request,
        "turns/settle.html",
        {"ledger": ledger, "form": form},
    )
