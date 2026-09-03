"""演示试剂的注册表模板与固定的批次（lot）身份。

``@resource`` 让 ``demo_reagent_water`` 在 host 启动时同步成 Materials Authority 的资源模板
（``register_resource_definitions``，template_uuid 由权威分配）；可计量库存以 **lot** 为单位
挂在模板下（``InventoryLotInbound``），工作流节点的 ``InventoryRequirement`` 用固定的 lot uuid
指向"这一瓶水"，这样声明式工作流不必知道运行时分配的 template_uuid。
"""

from pylabrobot.resources import Container

from unilabos.registry.decorators import resource

#: 注册表模板名（= 权威模板 name）。装饰器里必须写字面量：注册表按 AST 扫描，不求值变量。
WATER_TEMPLATE_NAME = "demo_reagent_water"
#: 固定的批次身份：入库、预留、出库都指向这一瓶水。
WATER_LOT_UUID = "6b1d5f2e-3c84-4a17-8f52-000000004101"
WATER_UNIT = "ml"


@resource(
    id="demo_reagent_water",
    category=["reagent"],
    description="演示试剂：水。可计量库存的模板，数量以 ml 计，按 lot 入库/预留/扣减",
    display_name="演示试剂：水",
)
def demo_reagent_water(name: str) -> Container:
    return Container(
        name=name,
        size_x=60.0,
        size_y=60.0,
        size_z=120.0,
        max_volume=1000.0,
        category="reagent",
        model=WATER_TEMPLATE_NAME,
    )
