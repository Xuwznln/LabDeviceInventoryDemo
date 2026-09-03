"""分液器 — 消耗试剂的设备动作，出库扣减不由设备自己做。

``dispense`` 本身只是"把 volume 的液体分到 target"。它消耗多少试剂由**工作流节点的库存需求**
（``ctx.run(..., inventory=[{"kind": "reagent", "lot_uuid": ..., "quantity": ..., "unit": ...}])``）
声明：

1. 任务启动时调度器对整张任务 all-or-nothing 预留（available -= q, reserved += q）；
   任一需求不足，整张任务在派发前失败（``plan_not_executable``，message 含 ``short by``），
   设备根本不会被调用；
2. 动作开始前执行面消耗预留（出库：total -= q, reserved -= q）；
3. 动作运行时读到的 lot 已经是扣减后的数字——本动作把它作为返回值交还，作为出库证据。

设备不重复扣减：数量事实只有权威一份。
"""

import logging
import time
from typing import Any, Dict, List, Optional

from unilabos.registry.decorators import action, device, not_action, topic_config

from .reagents import WATER_LOT_UUID


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
        description="向目标分出 volume 的试剂；对应节点的库存需求已由调度器预留、执行面扣减，本动作回报扣减后的 lot",
        feedback_interval=1.0,
    )
    def dispense(
        self,
        volume: float = 40.0,
        unit: str = "ml",
        target: str = "beaker",
        lot_uuid: str = WATER_LOT_UUID,
        duration_s: float = 0.5,
    ) -> Dict[str, Any]:
        """分液。

        Args:
            volume[体积]: 分液体积，应与节点库存需求的 quantity 一致。
            unit[单位]: 计量单位。
            target[目标容器]: 接收液体的容器名。
            lot_uuid[批次uuid]: 回报哪一瓶试剂扣减后的数量。
            duration_s[持续秒数]: 模拟分液耗时。
        """
        from unilabos.resources.materials import resolve_materials_gateway

        time.sleep(max(0.0, float(duration_s)))
        lot = resolve_materials_gateway().get_inventory_lot(lot_uuid)
        self._dispensed_total += float(volume)
        record = {
            "volume": float(volume),
            "unit": unit,
            "target": target,
            "lot_total_after": lot.quantity_total,
            "lot_available_after": lot.quantity_available,
            "lot_reserved_after": lot.quantity_reserved,
        }
        self._history.append(record)
        self.logger.info(
            f"[ReagentDispenser] 分液 {volume}{unit} → {target}；lot 扣减后 total={lot.quantity_total} "
            f"available={lot.quantity_available} reserved={lot.quantity_reserved}"
        )
        return {"success": True, **record, "dispensed_total": self._dispensed_total}
