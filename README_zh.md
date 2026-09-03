# LabDeviceInventoryDemo

[English](README.md)

一个最小的 Uni-Lab-OS 设备包，覆盖**按数量计量的库存**链路：添加试剂（入库）、工作流消耗试剂
（预留 + 出库扣减）、数量不足被拒绝。两台虚拟设备同进程：`reagent_store`（`reagent_store_demo`）
直接操作 Materials Authority 的库存 lot，`reagent_dispenser`（`reagent_dispenser_demo`）是消耗试剂的动作。

演示内容（全部走网页式工作流提交路径）：

- **注册表试剂模板**：`@resource(id="demo_reagent_water")` 在 host 启动时同步成 Materials Authority
  的资源模板；可计量库存以 **lot** 为单位挂在模板下（`InventoryLotInbound`）。
- **添加试剂**（`reagent_store.restock`）：与网页"添加试剂"背后的 `POST /api/v1/materials/lots/inbound`
  同一调用——向固定 lot 入库 100 ml 水（`total/available += 100`）。
- **带预留的出库**（`reagent_dispenser.dispense` + 节点 `inventory` 声明）：工作流步骤声明
  `{"kind": "reagent", "lot_uuid": …, "quantity": 40, "unit": "ml"}`。任务启动时调度器对整张任务
  all-or-nothing 预留（`available -= 40, reserved += 40`），动作开始前执行面消耗预留
  （`total -= 40, reserved -= 40`），分液器回报**扣减后**的 lot：`60 / 60 / 0`。
- **数量不足**：对 60 ml 的 lot 提 500 ml 需求，在预留阶段失败——任务 `failed`，
  `error_info[0] = {code: "plan_not_executable", message: "requirement 'water' is short by 440 ml"}`，
  节点运行 `canceled`，设备根本没被调用，lot 原样不动（失败的预留不留任何 reserved）。
- **盘点**：`stock_report` 读模板下全部 lot 的 `total / available / reserved`。

设备自己从不扣数量：库存事实只有 Materials Authority 一份。

## 从 GitHub 安装

```bash
unilab package install https://github.com/Xuwznln/LabDeviceInventoryDemo --ref <commit-sha>
```

本地开发可使用：

```bash
git clone https://github.com/Xuwznln/LabDeviceInventoryDemo.git
cd LabDeviceInventoryDemo
python -m pip install -e .
```

本地演示不需要 AK/SK，也不依赖云端实验室。

## 有终止条件的双运行时 smoke

```bash
python -m inventory_demo.smoke --backend hostlink --timeout 90
python -m inventory_demo.smoke --backend ros2 --timeout 120
```

smoke 启动真实运行时（`unilab -g graph/inventory_demo.json`，启动时把 `@workflow` 模板上报到本机
Workflow Authority），在全新数据库上复现网页的调用（`POST /api/v1/workflow-tasks`、
`GET /api/v1/workflow-tasks/{uuid}/node-runs`、`GET /api/v1/materials/lots/{lot}`）：

1. **「试剂入库：水 100 ml」**——`restock(quantity=100)`：lot `100 / 100 / 0`。
2. **「出库成功：分液 40 ml」**——`dispense(volume=40)` 带 40 ml 需求，再 `stock_report`：
   分液器读到 `lot_total_after = 60`、`lot_reserved_after = 0`；盘点确认 `60 / 60 / 0`。
3. **「出库不足：分液 500 ml」**——500 ml 需求：任务 `failed`，`plan_not_executable` / `short by 440 ml`，
   节点运行 `canceled`，lot 仍是 `60 / 60 / 0`。
4. **「库存盘点」**——`stock_report`：`60 / 60 / 0`，只有一个 lot。

## 手动启动

```bash
python -m unilabos --backend hostlink --skip_env_check \
  --devices ./inventory_demo --external_devices_only \
  --visual disable --disable_browser \
  -g ./graph/inventory_demo.json

python -m unilabos --backend ros2 --disable_hostlink --skip_env_check \
  --devices ./inventory_demo --external_devices_only \
  --visual disable --disable_browser \
  -g ./graph/inventory_demo.json
```

然后打开管理页面：先运行一次「试剂入库：水 100 ml」，再运行「出库成功：分液 40 ml」和
「出库不足：分液 500 ml」，在 `/api/v1/materials/lots` 观察 lot 数字。上面的数字以全新数据库为前提，
每多跑一次 `restock` 同一 lot 就多 100 ml。

## 在工作流步骤上声明库存需求

```python
ctx.run(
    "reagent_dispenser/dispense",
    {"volume": 40.0, "unit": "ml"},
    inventory=[{"key": "water", "kind": "reagent", "lot_uuid": WATER_LOT_UUID,
                "quantity": 40.0, "unit": "ml"}],
)
```

`inventory` 在声明时按 `InventoryRequirement` 校验，落到节点的 `meta_data.inventory_requirements`；
`lot_uuid` 指定一瓶，`template_uuid` 则由权威按 FIFO 选 lot。

## 目录

```text
graph/inventory_demo.json          两种 backend 共用的一份图（试剂库 + 分液器）
inventory_demo/
  reagents.py                      demo_reagent_water 资源 + 固定 lot uuid / 单位
  reagent_store.py                 restock（入库）/ stock_report（盘点）
  dispenser.py                     dispense：消耗节点预留，回报扣减后的 lot
  workflows.py                     @workflow 模板（入库、出库成功、出库不足、盘点）
  smoke.py                         经管理 API 驱动的有终止条件真实运行时证明
tests/test_hostlink_smoke.py       HostLink 集成断言
```
