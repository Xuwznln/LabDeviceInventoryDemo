"""试剂库 — 面向 Materials Authority 的库存入库与盘点。

入库（``restock``）就是网页"添加试剂"按钮背后的 ``POST /api/v1/materials/lots/inbound``：
按模板名找到权威模板，向固定 lot 追加数量（同一 lot 重复入库累加）。盘点
（``stock_report``）读该模板下全部 lot 的 总量 / 可用 / 已预留 三个数字——它们分别在
入库、调度器预留、动作开始扣减三个时刻变化：

- 入库：total += q, available += q；
- 任务启动预留：available -= q, reserved += q（all-or-nothing，不足则整张任务不启动）；
- 动作开始扣减（出库）：total -= q, reserved -= q；
- 任务未消耗就结束：reserved -= q, available += q（释放）。
"""

import logging
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from unilabos.protocol.materials import InventoryLotInbound, InventoryMutation
from unilabos.registry.decorators import action, device, not_action, topic_config

from .reagents import WATER_LOT_UUID, WATER_TEMPLATE_NAME, WATER_UNIT


def _lot_view(lot: Any) -> Dict[str, Any]:
    return {
        "lot_uuid": lot.lot_uuid,
        "template_uuid": lot.template_uuid,
        "batch_no": lot.batch_no,
        "unit": lot.unit,
        "quantity_total": lot.quantity_total,
        "quantity_available": lot.quantity_available,
        "quantity_reserved": lot.quantity_reserved,
        "version": lot.version,
    }


@device(
    id="reagent_store_demo",
    display_name="试剂库",
    category=["virtual_device"],
    description="试剂入库（添加试剂）与库存盘点：直接操作 Materials Authority 的可计量库存 lot",
    supported_backends=["hostlink", "ros2"],
)
class ReagentStoreDemo:
    """试剂库：入库与盘点。"""

    run_in_test_mode = True

    def __init__(self, device_id: Optional[str] = None, **kwargs: Any) -> None:
        """初始化试剂库。

        Args:
            device_id[设备ID]: 设备实例 ID，默认 reagent_store_demo。
        """
        self.device_id = device_id or "reagent_store_demo"
        self.logger = logging.getLogger(f"ReagentStore.{self.device_id}")
        self._start_time = time.time()
        self._restocks: int = 0
        self._last_report: str = ""

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
    def restocks(self) -> int:
        """已执行的入库次数。"""
        return self._restocks

    @property
    @topic_config()
    def last_report(self) -> str:
        """最近一次盘点摘要（total/available/reserved）。"""
        return self._last_report

    # ── 权威访问 ────────────────────────────────────────────

    @staticmethod
    @not_action
    def _gateway() -> Any:
        from unilabos.resources.materials import resolve_materials_gateway

        return resolve_materials_gateway()

    @not_action
    def _template_uuid(self, template_name: str) -> str:
        """注册表 @resource 在 host 启动时已同步为权威模板；按 name 取运行时分配的 uuid。"""

        for template in self._gateway().list_templates():
            if template.name == template_name:
                return str(template.template_uuid)
        raise LookupError(f"权威中没有模板 {template_name!r}（registry 资源未同步）")

    @not_action
    def _mutation(self, operation: str, effect_key: str) -> InventoryMutation:
        return InventoryMutation(
            command_uuid=str(uuid4()),
            effect_key=effect_key,
            operation=operation,
            actor_type="device",
            actor_uuid=self.device_id,
        )

    # ── 动作 ────────────────────────────────────────────────

    @action(
        display_name="试剂入库",
        description="向固定 lot 追加数量（同一 lot 重复入库累加）：total/available 各 +quantity",
        always_free=True,
        feedback_interval=1.0,
    )
    def restock(
        self,
        reagent: str = WATER_TEMPLATE_NAME,
        quantity: float = 100.0,
        unit: str = WATER_UNIT,
        lot_uuid: str = WATER_LOT_UUID,
        batch_no: str = "demo-water",
    ) -> Dict[str, Any]:
        """添加试剂：登记或补充一批可按数量扣减的试剂。

        Args:
            reagent[试剂模板名]: 注册表 @resource 的 id，默认 demo_reagent_water。
            quantity[数量]: 本次入库数量（>0）。
            unit[单位]: 计量单位，需与该 lot 已有单位一致。
            lot_uuid[批次uuid]: 固定的批次身份；工作流的库存需求按它指向这一瓶试剂。
            batch_no[批号]: 批号，同一 lot 再次入库时必须一致。
        """
        template_uuid = self._template_uuid(reagent)
        result = self._gateway().inbound_inventory_lot(
            self._mutation("inbound_inventory_lot", f"restock:{lot_uuid}:{uuid4()}"),
            InventoryLotInbound(
                lot_uuid=lot_uuid,
                template_uuid=template_uuid,
                batch_no=batch_no,
                unit=unit,
                quantity=float(quantity),
            ),
        )
        lot = result.data
        self._restocks += 1
        self.logger.info(
            f"[ReagentStore] 入库 {quantity}{unit} → lot {lot_uuid[-4:]} total={lot.quantity_total} "
            f"available={lot.quantity_available} reserved={lot.quantity_reserved}"
        )
        return {"success": True, "added": float(quantity), "unit": unit, "lot": _lot_view(lot)}

    @action(
        display_name="库存盘点",
        description="读取试剂模板下全部 lot 的 总量/可用/已预留",
        always_free=True,
        feedback_interval=1.0,
    )
    def stock_report(self, reagent: str = WATER_TEMPLATE_NAME) -> Dict[str, Any]:
        """盘点一个试剂模板的库存。

        Args:
            reagent[试剂模板名]: 注册表 @resource 的 id。
        """
        template_uuid = self._template_uuid(reagent)
        lots = [
            _lot_view(lot)
            for lot in self._gateway().list_inventory_lots(template_uuid=template_uuid)
        ]
        totals = {
            "quantity_total": sum(lot["quantity_total"] for lot in lots),
            "quantity_available": sum(lot["quantity_available"] for lot in lots),
            "quantity_reserved": sum(lot["quantity_reserved"] for lot in lots),
        }
        self._last_report = (
            f"total={totals['quantity_total']:g} available={totals['quantity_available']:g} "
            f"reserved={totals['quantity_reserved']:g}"
        )
        self.logger.info(f"[ReagentStore] 盘点 {reagent}: {self._last_report}")
        return {"success": True, "reagent": reagent, "template_uuid": template_uuid, "lots": lots, **totals}
