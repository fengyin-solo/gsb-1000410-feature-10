"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

制冷机组保养涉及多表联动（机组、车辆、保养单、保养记录），因此这里额外提供：
- aux_rows：业务模块之外的辅助表（保养单、保养登记记录），不计入概览统计；
- next_id：辅助表的发号；
- lock_for：按机组粒度的互斥锁，保证并发保养登记只有一个能落结论；
- snapshot/restore：多表写入失败时整体回滚，绝不留下半条记录。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS

# 保养单：每台机组同时最多只有一张未完成（有效）保养单
REEFER_MAINTENANCE_TICKETS = "reefer_maintenance_ticket"
# 保养登记记录：每次保养结论落成一条有效记录
REEFER_MAINTENANCE_RECORDS = "reefer_maintenance_record"
AUX_TABLES = (REEFER_MAINTENANCE_TICKETS, REEFER_MAINTENANCE_RECORDS)


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._aux: dict[str, list[dict[str, Any]]] = {name: [] for name in AUX_TABLES}
        self._locks: dict[str, threading.RLock] = {}
        self._gate = threading.RLock()

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def aux_rows(self, name: str) -> list[dict[str, Any]]:
        return self._aux.setdefault(name, [])

    def next_id(self, name: str) -> int:
        rows = self.aux_rows(name)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def lock_for(self, key: str) -> threading.RLock:
        """按业务键取锁（同一台机组串行化保养登记）。"""
        with self._gate:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        for row in self.rows(module):
            if str(row.get(field, "")) == str(value):
                return row
        return None

    def snapshot(self, tables: list[str]) -> dict[str, list[dict[str, Any]]]:
        """对指定表做深拷贝快照，用于多表写入失败时回滚。"""
        snap: dict[str, list[dict[str, Any]]] = {}
        for name in tables:
            source = self._aux[name] if name in self._aux else self.rows(name)
            snap[name] = [dict(row) for row in source]
        return snap

    def restore(self, snap: dict[str, list[dict[str, Any]]]) -> None:
        for name, rows in snap.items():
            if name in self._aux:
                self._aux[name] = [dict(row) for row in rows]
            else:
                self._tables[name] = [dict(row) for row in rows]

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
