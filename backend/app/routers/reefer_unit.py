"""制冷机组接口：维护制冷设备，覆盖停机检查、安排保养、复位故障、保养登记等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.reefer_unit import ReeferUnitService

router = APIRouter(prefix="/api/reefer_unit", tags=["制冷机组"])

service = ReeferUnitService()

LIST_FIELDS = ["机组编号", "所属车辆", "机组型号", "设定温度", "回风温度", "运转时长", "上次保养日", "机组状态"]
STATUSES = ["运行", "怠速", "故障", "保养中"]


class MaintenancePayload(BaseModel):
    """保养登记入参：保养结论必填，其余可空。"""

    保养结论: str | None = None
    保养人员: str | None = None
    保养日期: str | None = None
    备注: str | None = None


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按机组编号检索"),
    status: str | None = Query(default=None, description="运行、怠速、故障、保养中"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按机组编号与状态过滤制冷机组列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出制冷机组清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "reefer_unit", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条制冷设备明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"制冷设备 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/maintenance", response_model=dict)
def get_maintenance(entry_id: int) -> dict[str, Any]:
    """保养详情：查询报错或保养结论缺失时不允许继续占用设备（服务层自愈并释放）。"""
    detail, message = service.get_maintenance_detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=409, detail=message)
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条制冷设备，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="制冷设备已登记", entry=entry)


@router.post("/{entry_id}/maintenance", response_model=ActionResult)
def register_maintenance(entry_id: int, payload: MaintenancePayload) -> ActionResult:
    """登记保养结论：结论缺失/并发抢占/写入失败都会回滚并释放设备。"""
    values = payload.model_dump()
    entry, message = service.register_maintenance(entry_id, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: dict[str, Any]) -> ActionResult:
    """对单条制冷设备执行停机检查、安排保养、复位故障；不允许的动作会被拦下并说明原因。"""
    # 兼容两种入参：{action} 直传，或 {values: {action}}
    if "action" in payload:
        action = str(payload.get("action") or "").strip()
    else:
        action = str((payload.get("values") or {}).get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
