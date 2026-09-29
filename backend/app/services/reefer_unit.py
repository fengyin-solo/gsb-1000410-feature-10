"""制冷机组业务规则：状态流转、字段校验、保养登记与跨模块一致性都收在这里。

保养流程（机组状态始终以本模块为唯一事实来源）：
1. 安排保养：机组置「保养中」并建立一张未完成保养单，设备开始被占用；
2. 保养登记：登记保养结论，机组回到「运行」，写一条有效保养记录，结论与状态
   同步回写到车辆调度；
3. 任何失败（并发抢占、缺少保养结论、车辆对不上、写入异常）都会整体回滚，
   并释放设备，绝不留下半条记录或让设备继续被占用。

并发：按机组加互斥锁，同一台机组并发保养登记只有一个能落成有效结论。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import (
    REEFER_MAINTENANCE_RECORDS,
    REEFER_MAINTENANCE_TICKETS,
    store,
)

MODULE = "reefer_unit"
VEHICLE_MODULE = "vehicle"
REQUIRED_FIELDS = ["机组编号", "所属车辆", "机组型号"]
STATUS_ORDER = ["运行", "怠速", "故障", "保养中"]
MAINTENANCE_STATUS = "保养中"
RUNNING_STATUS = "运行"
ACTION_RULES = {"停机检查": "故障", "复位故障": "运行"}
NEGATIVE_ACTIONS = []

# 多表联动涉及的全部表，登记时对它们一起快照，失败整体回滚
JOINT_TABLES = [MODULE, VEHICLE_MODULE, REEFER_MAINTENANCE_TICKETS, REEFER_MAINTENANCE_RECORDS]


class ReeferUnitService:
    # ---------- 基础查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        # 列表口径也要自愈：占用记录与结论不一致时不允许继续占用设备
        for row in rows:
            self._heal_unit(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("机组编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        projected = [self._project(row) for row in rows]
        total = len(projected)
        start = max(page - 1, 0) * size
        return projected[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        self._heal_unit(entry)
        return self._project(entry)

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
        return self._project(entry), []

    # ---------- 动作 ----------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"制冷设备 {entry_id} 不存在或已归档"

        with store.lock_for(f"{MODULE}:{entry_id}"):
            if action == "安排保养":
                return self._schedule_maintenance(entry)

            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于制冷机组可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            # 保养中的设备必须先登记保养结论，不能被其他动作抢走
            if entry.get("status") == MAINTENANCE_STATUS:
                return None, "机组保养中，需先登记保养结论后再执行该动作"
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return self._project(entry), f"制冷设备已{action}"

    # ---------- 保养 ----------
    def schedule_maintenance(self, entry_id: int, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"制冷设备 {entry_id} 不存在或已归档"
        with store.lock_for(f"{MODULE}:{entry_id}"):
            result, message = self._schedule_maintenance(entry, remark)
        return result, message

    def get_maintenance_detail(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """读取保养详情。查询报错或保养结论缺失时，不允许继续占用设备。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"制冷设备 {entry_id} 不存在或已归档"
        try:
            with store.lock_for(f"{MODULE}:{entry_id}"):
                self._heal_unit(entry)
                ticket = self._open_ticket(entry_id)
                if entry.get("status") == MAINTENANCE_STATUS:
                    if ticket is None:
                        # 占用中却查不到保养单：半条记录，自愈已释放，明确报错
                        return None, "保养详情不完整（保养单丢失），已释放该设备，请重新安排保养"
                    detail = self._detail(entry, ticket, None)
                    if not str(detail.get("保养结论") or "").strip():
                        # 保养详情丢失保养结论：不能继续占用设备
                        self._release_unit(entry, ticket, reason="保养详情丢失保养结论")
                        return None, "保养详情缺少保养结论，不能继续占用设备，已释放该设备"
                    return detail, "ok"
                # 非保养中：返回最近一次有效保养记录
                record = self._latest_record(entry_id)
                if record is None:
                    return None, "该机组暂无已登记的保养记录"
                return self._detail(entry, ticket, record), "ok"
        except Exception as exc:  # 查询过程报错也要保证设备不被占用
            self._safe_release(entry)
            return None, f"保养详情读取失败，已释放设备占用：{exc}"

    def register_maintenance(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """登记保养结论：多表事务，并发下只保留一个有效结论，失败整体回滚。"""
        conclusion = str(values.get("保养结论") or "").strip()
        maintainer = str(values.get("保养人员") or "").strip()
        maintenance_date = str(values.get("保养日期") or "").strip() or date.today().isoformat()
        remark = str(values.get("备注") or "").strip()

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"制冷设备 {entry_id} 不存在或已归档"

        # 保养结论是登记的核心，缺失时不能落成一条“半成品”记录
        if not conclusion:
            with store.lock_for(f"{MODULE}:{entry_id}"):
                ticket = self._open_ticket(entry_id)
                if ticket is not None or entry.get("status") == MAINTENANCE_STATUS:
                    self._release_unit(
                        entry,
                        ticket,
                        reason="保养登记缺少保养结论",
                    )
            return None, "缺少保养结论，不能继续占用设备，已释放该机组"

        with store.lock_for(f"{MODULE}:{entry_id}"):
            # 并发：只有持保养单且仍在保养中的机组能登记，抢占者直接失败
            ticket = self._open_ticket(entry_id)
            if entry.get("status") != MAINTENANCE_STATUS or ticket is None:
                return None, "该机组当前不在保养中或已有保养结论登记，请勿重复提交"

            # 多表快照：机组 / 车辆 / 保养单 / 保养记录 一起回滚
            snap = store.snapshot(JOINT_TABLES)
            record_id = None
            try:
                vehicle_code = str(entry.get("所属车辆") or "").strip()
                vehicle = store.find_by_field(VEHICLE_MODULE, "车辆编号", vehicle_code) if vehicle_code else None
                if vehicle is None:
                    raise ValueError(f"机组所属车辆「{vehicle_code}」在车辆调度中不存在，无法回写状态")

                # 1) 落一条有效保养记录
                records = store.aux_rows(REEFER_MAINTENANCE_RECORDS)
                record_id = store.next_id(REEFER_MAINTENANCE_RECORDS)
                record = {
                    "id": record_id,
                    "机组id": entry_id,
                    "机组编号": entry.get("机组编号"),
                    "所属车辆": vehicle_code,
                    "保养单id": ticket["id"],
                    "保养日期": maintenance_date,
                    "保养人员": maintainer,
                    "保养结论": conclusion,
                    "备注": remark,
                    "有效": True,
                }
                records.append(record)

                # 2) 保养单收尾（同一机组只保留这一张有效保养单对应一个有效结论）
                ticket["状态"] = "已完成"
                ticket["有效"] = False
                ticket["保养记录id"] = record_id
                ticket["完成时间"] = maintenance_date

                # 3) 机组释放，回到运行
                entry["status"] = RUNNING_STATUS
                entry["pending"] = True
                entry["abnormal"] = False
                entry["上次保养日"] = maintenance_date

                # 4) 回写车辆调度：列表与详情读到的机组状态都来自这里的同步结果
                self._write_back_vehicle(vehicle, entry, maintenance_date, conclusion, record_id)
            except Exception as exc:
                # 失败整体回滚，随后释放设备，绝不留下半条记录
                store.restore(snap)
                fresh = store.find(MODULE, entry_id)
                fresh_ticket = self._open_ticket(entry_id)
                if fresh is not None and (fresh.get("status") == MAINTENANCE_STATUS or fresh_ticket is not None):
                    self._release_unit(fresh, fresh_ticket, reason=f"保养登记失败回滚：{exc}")
                return None, f"保养登记失败，已回滚且未保留任何记录：{exc}"

            return self._project(entry), "保养结论已登记，机组状态已同步回车辆调度"

    # ---------- 内部：保养单生命周期 ----------
    def _schedule_maintenance(
        self,
        entry: dict[str, Any],
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry_id = int(entry.get("id", 0))
        # 进入保养前先自愈掉脏占用
        self._heal_unit(entry)
        existing = self._open_ticket(entry_id)
        if entry.get("status") == MAINTENANCE_STATUS or existing is not None:
            return None, "该机组已在保养中，未完成保养结论登记前不能重复安排"

        tickets = store.aux_rows(REEFER_MAINTENANCE_TICKETS)
        snap = store.snapshot([MODULE, VEHICLE_MODULE, REEFER_MAINTENANCE_TICKETS])
        try:
            ticket = {
                "id": store.next_id(REEFER_MAINTENANCE_TICKETS),
                "机组id": entry_id,
                "机组编号": entry.get("机组编号"),
                "所属车辆": entry.get("所属车辆"),
                "开始时间": date.today().isoformat(),
                "备注": remark or "",
                "状态": "保养中",
                "有效": True,
                "保养记录id": None,
                "完成时间": None,
            }
            tickets.append(ticket)
            entry["status"] = MAINTENANCE_STATUS
            entry["pending"] = False
            entry["abnormal"] = False
            self._mark_vehicle_occupied(entry)
        except Exception as exc:
            # 安排保养同样不允许留下半张保养单或半截占用状态
            store.restore(snap)
            return None, f"安排保养失败，已回滚：{exc}"
        return self._project(entry), "制冷设备已安排保养，请登记保养结论"

    def _release_unit(
        self,
        entry: dict[str, Any] | None,
        ticket: dict[str, Any] | None,
        *,
        reason: str,
    ) -> None:
        """释放设备占用并作废保养单（不生成保养记录）。"""
        if ticket is not None:
            ticket["状态"] = "已作废"
            ticket["有效"] = False
            ticket["作废原因"] = reason
        if entry is not None:
            entry["status"] = RUNNING_STATUS
            entry["pending"] = True
            entry["abnormal"] = False
            vehicle = self._vehicle_of(entry)
            if vehicle is not None:
                self._write_back_vehicle(
                    vehicle,
                    entry,
                    str(entry.get("上次保养日") or ""),
                    f"保养未完成（{reason}）",
                    None,
                )

    def _safe_release(self, entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        try:
            with store.lock_for(f"{MODULE}:{entry.get('id')}"):
                fresh = store.find(MODULE, int(entry.get("id", 0)))
                if fresh is not None:
                    self._release_unit(fresh, self._open_ticket(int(fresh.get("id", 0))), reason="查询异常")
        except Exception:
            pass

    def _heal_unit(self, entry: dict[str, Any]) -> None:
        """读时自愈：占用状态与保养单/结论对不上时，不允许继续占用设备。"""
        entry_id = int(entry.get("id", 0))
        ticket = self._open_ticket(entry_id)
        status = entry.get("status")
        if status == MAINTENANCE_STATUS and ticket is None:
            # 设备显示占用，但保养单丢失（半条记录）
            self._release_unit(entry, None, reason="保养单丢失，列表校验发现占用记录不完整")
            return
        if status != MAINTENANCE_STATUS and ticket is not None:
            # 保养单还挂着，设备却已不在保养中
            record = self._latest_record(entry_id)
            if record is not None and str(record.get("保养结论") or "").strip():
                ticket["状态"] = "已完成"
                ticket["有效"] = False
            else:
                self._release_unit(entry, ticket, reason="设备未处于保养中却存在未完成保养单")

    # ---------- 内部：车辆一致性 ----------
    def _vehicle_of(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        code = str(entry.get("所属车辆") or "").strip()
        if not code:
            return None
        return store.find_by_field(VEHICLE_MODULE, "车辆编号", code)

    def _mark_vehicle_occupied(self, entry: dict[str, Any]) -> None:
        vehicle = self._vehicle_of(entry)
        if vehicle is None:
            return
        vehicle["制冷机组状态"] = MAINTENANCE_STATUS

    def _write_back_vehicle(
        self,
        vehicle: dict[str, Any],
        entry: dict[str, Any],
        maintenance_date: str,
        conclusion: str,
        record_id: int | None,
    ) -> None:
        """把保养结果回写到车辆调度，保证机组列表、车辆详情、调度画面状态一致。"""
        vehicle["制冷机组状态"] = entry.get("status")
        if maintenance_date:
            vehicle["上次维保日"] = maintenance_date
        model = entry.get("机组型号")
        if model:
            vehicle["制冷机组型号"] = model
        vehicle["最近保养结论"] = conclusion
        vehicle["最近保养记录id"] = record_id

    # ---------- 内部：投影与组装 ----------
    def _project(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对外视图：机组状态列以内部 status 为准，并附带最近保养结论。"""
        view = dict(entry)
        view["机组状态"] = entry.get("status")
        record = self._latest_record(int(entry.get("id", 0)))
        if record is not None:
            view["最近保养结论"] = record.get("保养结论")
            view["上次保养日"] = record.get("保养日期") or view.get("上次保养日")
        else:
            view.setdefault("最近保养结论", "")
        return view

    def _open_ticket(self, entry_id: int) -> dict[str, Any] | None:
        for ticket in store.aux_rows(REEFER_MAINTENANCE_TICKETS):
            if int(ticket.get("机组id", 0)) == entry_id and ticket.get("有效"):
                return ticket
        return None

    def _latest_record(self, entry_id: int) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        for record in store.aux_rows(REEFER_MAINTENANCE_RECORDS):
            if int(record.get("机组id", 0)) == entry_id and record.get("有效"):
                if latest is None or int(record.get("id", 0)) > int(latest.get("id", 0)):
                    latest = record
        return latest

    def _detail(
        self,
        entry: dict[str, Any],
        ticket: dict[str, Any] | None,
        record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record = record or self._latest_record(int(entry.get("id", 0)))
        return {
            "机组id": entry.get("id"),
            "机组编号": entry.get("机组编号"),
            "所属车辆": entry.get("所属车辆"),
            "机组状态": entry.get("status"),
            "保养单id": ticket.get("id") if ticket else (record or {}).get("保养单id"),
            "保养开始时间": ticket.get("开始时间") if ticket else None,
            "保养日期": (record or {}).get("保养日期"),
            "保养人员": (record or {}).get("保养人员"),
            "保养结论": (record or {}).get("保养结论"),
            "备注": (record or {}).get("备注") if record else (ticket or {}).get("备注"),
        }


# 车辆调度侧读取机组状态的统一入口：保证两个画面状态口径一致
def vehicle_reefer_status(vehicle_code: str) -> dict[str, Any]:
    reefer = None
    for row in store.rows(MODULE):
        if str(row.get("所属车辆") or "").strip() == str(vehicle_code):
            reefer = row
            break
    if reefer is None:
        return {"制冷机组状态": "", "最近保养结论": ""}
    svc = ReeferUnitService()
    projected = svc._project(reefer)
    return {
        "制冷机组状态": projected.get("机组状态", ""),
        "最近保养结论": projected.get("最近保养结论", ""),
    }
