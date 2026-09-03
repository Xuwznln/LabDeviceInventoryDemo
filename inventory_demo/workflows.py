"""库存演示的默认工作流：入库 → 出库成功 → 出库不足（任务在派发前失败）→ 盘点。

出库节点用 ``ctx.run(..., inventory=[...])`` 声明库存需求：调度器在任务启动时对整张任务
all-or-nothing 预留，不足则任务直接 failed（``plan_not_executable``），设备不会被调用；
预留成功的任务在动作开始前由执行面扣减（出库），分液器回报扣减后的 lot 数量。
"""

from unilabos.registry.workflows import WorkflowBuildContext, workflow

from .reagents import WATER_LOT_UUID, WATER_UNIT

RESTOCK_WORKFLOW_NAME = "试剂入库：水 100 ml"
DISPENSE_OK_WORKFLOW_NAME = "出库成功：分液 40 ml"
DISPENSE_SHORT_WORKFLOW_NAME = "出库不足：分液 500 ml"
AUDIT_WORKFLOW_NAME = "库存盘点"

RESTOCK_QUANTITY = 100.0
DISPENSE_OK_VOLUME = 40.0
DISPENSE_SHORT_VOLUME = 500.0


def _water(quantity: float) -> dict:
    """指向固定 lot 的试剂需求（reagent 类需求必须带 quantity + unit）。"""

    return {
        "key": "water",
        "kind": "reagent",
        "lot_uuid": WATER_LOT_UUID,
        "quantity": quantity,
        "unit": WATER_UNIT,
    }


@workflow(
    display_name=RESTOCK_WORKFLOW_NAME,
    description="添加试剂：向固定 lot 入库 100 ml 水（total/available +100）",
    tags=["inventory-demo", "inbound"],
)
def restock_water(ctx: WorkflowBuildContext) -> None:
    ctx.run("reagent_store/restock", {"quantity": RESTOCK_QUANTITY, "unit": WATER_UNIT}, name="入库 100 ml")


@workflow(
    display_name=DISPENSE_OK_WORKFLOW_NAME,
    description="库存充足的出库：任务启动预留 40 ml，动作开始扣减，分液后盘点 total 60 / reserved 0",
    tags=["inventory-demo", "outbound"],
)
def dispense_ok(ctx: WorkflowBuildContext) -> None:
    ctx.run(
        "reagent_dispenser/dispense",
        {"volume": DISPENSE_OK_VOLUME, "unit": WATER_UNIT, "target": "beaker-1"},
        name="分液 40 ml",
        inventory=[_water(DISPENSE_OK_VOLUME)],
    )
    ctx.run("reagent_store/stock_report", {}, name="分液后盘点")


@workflow(
    display_name=DISPENSE_SHORT_WORKFLOW_NAME,
    description="库存不足的出库：需求 500 ml 但只剩 60 ml，任务在派发前失败（plan_not_executable / short by 440 ml），设备不被调用",
    tags=["inventory-demo", "outbound", "insufficient"],
)
def dispense_short(ctx: WorkflowBuildContext) -> None:
    ctx.run(
        "reagent_dispenser/dispense",
        {"volume": DISPENSE_SHORT_VOLUME, "unit": WATER_UNIT, "target": "beaker-2"},
        name="分液 500 ml",
        inventory=[_water(DISPENSE_SHORT_VOLUME)],
    )


@workflow(
    display_name=AUDIT_WORKFLOW_NAME,
    description="盘点：失败的预留不能留下任何 reserved，total/available 仍为 60",
    tags=["inventory-demo", "audit"],
)
def audit(ctx: WorkflowBuildContext) -> None:
    ctx.run("reagent_store/stock_report", {}, name="盘点")
