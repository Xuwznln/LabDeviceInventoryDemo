"""库存演示的默认工作流：入库 → 出库成功 → 出库不足（任务在派发前失败）→ 盘点。

出库节点用 ``ctx.run(..., inventory=[...])`` 声明库存需求（``InventoryRequirement`` 是节点输入，
不是设备参数）：调度器在任务启动时对整张任务 all-or-nothing 预留，不足则任务直接 failed
（``plan_not_executable``），设备不会被调用；预留成功后调度器把权威解析出的出库内容按需求 key
注入同名动作参数（这里是 ``dispense(water=...)``），执行面在动作开始前扣减，分液器回报扣减后的 lot。
"""

from unilabos.registry.workflows import WorkflowBuildContext, WorkflowGuide, workflow

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
        "kind": "lot",
        "lot_uuid": WATER_LOT_UUID,
        "quantity": quantity,
        "unit": WATER_UNIT,
    }


@workflow(
    display_name=RESTOCK_WORKFLOW_NAME,
    description="添加试剂：向固定 lot 入库 100 ml 水（total/available +100）",
    tags=["inventory-demo", "inbound"],
    guide=WorkflowGuide(
        preparation=[
            "「设备」页确认试剂库 reagent_store 与分液器 reagent_dispenser 在线。",
            "「物料」页的试剂库存里找「演示试剂：水」：这条模板等价于点「添加试剂」，不需要手动入库。",
        ],
        expected=[
            "任务 succeeded。",
            "「物料」页里水的 lot（固定批号）total / available 各 +100 ml，reserved 为 0。",
        ],
        notes=["库存链路的第一步：先跑它，再跑「出库成功」「出库不足」「库存盘点」。"],
    ),
)
def restock_water(ctx: WorkflowBuildContext) -> None:
    ctx.run(
        "reagent_store/restock",
        {"quantity": RESTOCK_QUANTITY, "unit": WATER_UNIT},
        name="入库 100 ml",
        description="向固定 uuid 的水 lot 入库 100 ml（POST /materials/lots/inbound 的设备侧等价）。",
    )


@workflow(
    display_name=DISPENSE_OK_WORKFLOW_NAME,
    description="库存充足的出库：任务启动预留 40 ml，动作开始扣减，分液后盘点 total 60 / reserved 0",
    tags=["inventory-demo", "outbound"],
    guide=WorkflowGuide(
        preparation=[
            "先跑「试剂入库：水 100 ml」，让水的 lot 至少有 40 ml 可用（「物料」页可看 available）。",
            "不需要在页面上出库：分液节点声明了 40 ml 的库存需求，任务启动时调度器自动预留、动作开始前扣减。",
        ],
        expected=[
            "任务 succeeded；「任务运行时」里分液节点的返回值带扣减后的 lot（quantity_total 60）。",
            "「物料」页里水的 lot total / available 都是 60 ml，reserved 回到 0。",
            "「分液后盘点」返回同一组数字。",
        ],
    ),
)
def dispense_ok(ctx: WorkflowBuildContext) -> None:
    # 参数只给 target；试剂本身来自库存需求 water（调度器预留后注入 dispense(water=...)）
    ctx.run(
        "reagent_dispenser/dispense",
        {"target": "beaker-1"},
        name="分液 40 ml",
        description="按节点库存需求预留的 40 ml 水分到 beaker-1；试剂由调度器注入 water 参数，扣减后回报 lot。",
        inventory=[_water(DISPENSE_OK_VOLUME)],
    )
    ctx.run(
        "reagent_store/stock_report",
        {},
        name="分液后盘点",
        description="读水 lot 的 total / available / reserved，应为 60 / 60 / 0。",
    )


@workflow(
    display_name=DISPENSE_SHORT_WORKFLOW_NAME,
    description="库存不足的出库：需求 500 ml 但只剩 60 ml，任务在派发前失败（plan_not_executable / short by 440 ml），设备不被调用",
    tags=["inventory-demo", "outbound", "insufficient"],
    guide=WorkflowGuide(
        preparation=[
            "先跑「试剂入库」和「出库成功」，让水的 lot 剩 60 ml（少于本模板要的 500 ml）。",
            "这是一条预期失败的模板：用来看库存不足时任务在派发前就被拒，设备不会被调用。",
        ],
        expected=[
            "任务 failed，失败原因 plan_not_executable，消息里有 short by 440 ml。",
            "分液节点 canceled，「任务运行时」里没有设备调用记录。",
            "「物料」页里水的 lot 不变：total / available 仍是 60 ml，reserved 0（失败的预留不留痕迹）。",
        ],
    ),
)
def dispense_short(ctx: WorkflowBuildContext) -> None:
    ctx.run(
        "reagent_dispenser/dispense",
        {"target": "beaker-2"},
        name="分液 500 ml",
        description="声明 500 ml 的库存需求；预留阶段就因不足被拒，动作不会执行。",
        inventory=[_water(DISPENSE_SHORT_VOLUME)],
    )


@workflow(
    display_name=AUDIT_WORKFLOW_NAME,
    description="盘点：失败的预留不能留下任何 reserved，total/available 仍为 60",
    tags=["inventory-demo", "audit"],
    guide=WorkflowGuide(
        preparation=["按顺序跑完「试剂入库」「出库成功」「出库不足」后再跑。"],
        expected=["任务 succeeded；盘点返回 total 60 / available 60 / reserved 0，说明失败的预留没有残留。"],
    ),
)
def audit(ctx: WorkflowBuildContext) -> None:
    ctx.run(
        "reagent_store/stock_report",
        {},
        name="盘点",
        description="读水 lot 的三个数量并返回。",
    )
