"""增量采集接口：游标管理 + 双轨区间认领、批次回执确认、观察窗口总览。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, ClaimPayload, EntryPayload, PageResult, ReceiptPayload, WatermarkPayload
from app.services.delta import DeltaService

router = APIRouter(prefix="/api/delta", tags=["增量采集"])

service = DeltaService()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出增量采集清单：返回当前过滤条件下的全量数据。

    必须声明在 /{entry_id} 之前，否则 "export" 会被当成游标 id 解析。
    """
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "delta", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按游标编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、运行中、已暂停"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按游标编号与状态过滤增量采集列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条增量游标明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"增量游标 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条增量游标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="增量游标已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条增量游标执行提交校验、启动增量、暂停增量、续做增量。

    暂停增量会把在途认领一并挂起（保留认领及候选终点），续做增量从原段接着取数。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/intervals")
def get_intervals(entry_id: int) -> dict[str, Any]:
    """双轨区间总览：已确认段、在途/暂停认领、回扫覆盖、最近样本与坐标轴。"""
    intervals = service.get_intervals(entry_id)
    if intervals is None:
        raise HTTPException(status_code=404, detail=f"增量游标 {entry_id} 不存在或已归档")
    return intervals


@router.post("/{entry_id}/watermark", response_model=ActionResult)
def set_watermark(entry_id: int, payload: WatermarkPayload) -> ActionResult:
    """明确水位类型与顺序：旧样例水位是不可比较文字时的前置步骤。"""
    entry, message, comparable = service.set_watermark(
        entry_id, payload.水位类型, payload.当前水位, payload.水位顺序表
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=comparable, message=message, entry=entry)


@router.post("/{entry_id}/claims", response_model=ActionResult)
def create_claim(entry_id: int, payload: ClaimPayload) -> ActionResult:
    """认领一个起止水位区间；不可比较水位、间隙、重叠、穿越都会被拦下并说明原因。"""
    claim, message = service.create_claim(entry_id, payload.track, payload.start, payload.end, payload.job)
    if claim is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=claim)


@router.post("/{entry_id}/claims/{claim_id}/pause", response_model=ActionResult)
def pause_claim(entry_id: int, claim_id: int) -> ActionResult:
    """暂停在途认领：保留认领及候选终点。"""
    entry, message = service.pause_claim(entry_id, claim_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/claims/{claim_id}/resume", response_model=ActionResult)
def resume_claim(entry_id: int, claim_id: int) -> ActionResult:
    """续做已暂停认领：从原段接着取数。"""
    entry, message = service.resume_claim(entry_id, claim_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/claims/{claim_id}/confirm", response_model=ActionResult)
def confirm_claim(entry_id: int, claim_id: int, payload: ReceiptPayload) -> ActionResult:
    """凭批次回执确认认领区间；重复回执幂等，不额外前移水位。"""
    entry, message, _stats = service.confirm_claim(
        entry_id, claim_id, payload.model_dump()
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
