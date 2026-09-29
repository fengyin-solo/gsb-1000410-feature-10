"""车辆调度业务规则：状态流转、字段校验与筛选口径都收在这里。

车辆上的「制冷机组状态」不允许在本模块私自维护：统一从制冷机组模块投影过来，
保证机组列表、车辆详情、调度画面看到的同一机组状态完全一致。
"""
from __future__ import annotations

from typing import Any

from app.services.reefer_unit import vehicle_reefer_status
from app.store import store

MODULE = "vehicle"
REQUIRED_FIELDS = ["车辆编号", "车牌号", "车型类别"]
STATUS_ORDER = ["空闲", "已派单", "执行中", "维修中", "停运"]
ACTION_RULES = {"派发出车": "已派单", "收车归队": "空闲", "报修车辆": "维修中"}
NEGATIVE_ACTIONS = []


class VehicleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("车辆编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        projected = [self._with_reefer(row) for row in rows]
        total = len(projected)
        start = max(page - 1, 0) * size
        return projected[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_reefer(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_reefer(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"冷藏车辆 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于车辆调度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_reefer(entry), f"冷藏车辆已{action}"

    def _with_reefer(self, vehicle: dict[str, Any]) -> dict[str, Any]:
        """以制冷机组模块为唯一事实来源，覆盖车辆上缓存的机组状态。"""
        view = dict(vehicle)
        reefer = vehicle_reefer_status(str(vehicle.get("车辆编号", "")))
        view["制冷机组状态"] = reefer["制冷机组状态"]
        view["最近保养结论"] = reefer["最近保养结论"]
        return view
