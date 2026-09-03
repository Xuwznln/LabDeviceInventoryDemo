"""分液器 — 消耗试剂的设备动作，出库扣减不由设备自己做。

``InventoryRequirement`` 是**节点上的声明**（画布输入或 ``ctx.run(..., inventory=[...])``），
不是设备参数。链路：

1. 任务启动时调度器对整张任务 all-or-nothing 预留（available -= q, reserved += q）；
   任一需求不足，整张任务在派发前失败（``plan_not_executable``，message 含 ``short by``），
   设备根本不会被调用；
2. 权威解析出的**出库内容**（哪些 lot、各多少）由调度器按需求 ``key`` 注入同名动作参数——
   本动作的 ``water`` 参数就是这样拿到 ``{"quantity", "unit", "lots": [...]}`` 的，
   设备不需要知道 lot 是谁、也不需要自己选；
3. 动作开始前执行面消耗预留（出库：total -= q, reserved -= q）；
4. 动作运行时读到的 lot 已经是扣减后的数字——本动作把它作为返回值交还，作为出库证据。

设备不重复扣减：数量事实只有权威一份。
"""

import logging
import time
from typing import Any, Dict, List, Optional

from unilabos.registry.decorators import action, device, not_action, topic_config


@device(
    id="reagent_dispenser_demo",
    display_name="分液器",
    category=["virtual_device"],
    description="消耗试剂的分液动作；试剂数量由工作流节点库存需求预留与扣减，设备只回报扣减后的 lot",
    supported_backends=["hostlink", "ros2"],
)
class ReagentDispenserDemo:
    """按体积分液的虚拟设备。"""

    run_in_test_mode = True

    def __init__(self, device_id: Optional[str] = None, **kwargs: Any) -> None:
        """初始化分液器。

        Args:
            device_id[设备ID]: 设备实例 ID，默认 reagent_dispenser_demo。
        """
        self.device_id = device_id or "reagent_dispenser_demo"
        self.logger = logging.getLogger(f"ReagentDispenser.{self.device_id}")
        self._start_time = time.time()
        self._dispensed_total: float = 0.0
        self._history: List[Dict[str, Any]] = []

    @not_action
    def post_init(self, node: Any) -> None:
        self._device_node = node

    @property
    @topic_config(period=1.0)
    def heartbeat(self) -> int:
        """自启动以来的心跳秒数。"""
        return int(time.time() - self._start_time)

    @property
    @topic_config()
    def dispensed_total(self) -> float:
        """累计分液体积。"""
        return self._dispensed_total

    @action(
        display_name="分液",
        description="把节点库存需求 water 解析出的试剂分到目标容器；预留与扣减由调度器/执行面完成，本动作回报扣减后的 lot",
        feedback_interval=1.0,
    )
    def dispense(
        self,
        water: dict,
        target: str = "beaker",
        duration_s: float = 0.5,
    ) -> Dict[str, Any]:
        """分液。

        Args:
            water[试剂分配]: 由调度器按节点库存需求 key=water 注入：``{"quantity", "unit", "lots": [{"lot_uuid", "quantity"}]}``。
            target[目标容器]: 接收液体的容器名。
            duration_s[持续秒数]: 模拟分液耗时。
        """
        from unilabos.resources.materials import resolve_materials_gateway

        allocation = dict(water or {})
        lots = list(allocation.get("lots") or [])
        if not lots:
            raise ValueError("water 缺少权威分配的 lot：该节点没有声明 water 库存需求")
        volume = float(allocation.get("quantity") or 0.0)
        unit = str(allocation.get("unit") or "")
        time.sleep(max(0.0, float(duration_s)))
        gateway = resolve_materials_gateway()
        lots_after = []
        for item in lots:
            lot = gateway.get_inventory_lot(str(item["lot_uuid"]))
            lots_after.append(
                {
                    "lot_uuid": lot.lot_uuid,
                    "dispensed": float(item["quantity"]),
                    "quantity_total": lot.quantity_total,
                    "quantity_available": lot.quantity_available,
                    "quantity_reserved": lot.quantity_reserved,
                }
            )
        self._dispensed_total += volume
        record = {
            "volume": volume,
            "unit": unit,
            "target": target,
            "lots": lots_after,
            "lot_total_after": lots_after[0]["quantity_total"],
            "lot_available_after": lots_after[0]["quantity_available"],
            "lot_reserved_after": lots_after[0]["quantity_reserved"],
        }
        self._history.append(record)
        self.logger.info(
            f"[ReagentDispenser] 分液 {volume:g}{unit} → {target}（{len(lots)} 个 lot）；"
            f"lot 扣减后 total={record['lot_total_after']} available={record['lot_available_after']} "
            f"reserved={record['lot_reserved_after']}"
        )
        return {"success": True, **record, "dispensed_total": self._dispensed_total}
