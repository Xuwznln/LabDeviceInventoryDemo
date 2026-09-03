# LabDeviceInventoryDemo

[中文说明](README_zh.md)

A minimal Uni-Lab-OS device package for the **quantity-based inventory** chain:
adding a reagent (inbound), consuming it from a workflow (outbound with
reservation and deduction) and being refused when the stock is insufficient.
Two virtual devices share one process: `reagent_store` (`reagent_store_demo`)
talks to the Materials Authority's inventory lots, `reagent_dispenser`
(`reagent_dispenser_demo`) is the action that consumes reagent.

What it demonstrates (all through the web-style workflow submission path):

- **Reagent template from the registry**: `@resource(id="demo_reagent_water")`
  is synced into the Materials Authority as a resource template at host startup;
  measurable stock hangs under it as **lots** (`InventoryLotInbound`).
- **Adding reagent** (`reagent_store.restock`): the same call as the web UI's
  `POST /api/v1/materials/lots/inbound` — 100 ml water into a fixed lot
  (`total/available += 100`).
- **Outbound with reservation** (`reagent_dispenser.dispense` + node
  `inventory` declaration): the workflow step declares
  `{"key": "water", "kind": "reagent", "lot_uuid": …, "quantity": 40, "unit": "ml"}`.
  `InventoryRequirement` is a node-level input, not a device parameter: the
  scheduler reserves the whole task all-or-nothing when it starts
  (`available -= 40`, `reserved += 40`), injects the authority's resolved
  allocation into the action argument named by the requirement `key`
  (`dispense(water={"quantity": 40, "unit": "ml", "lots": [{"lot_uuid": …,
  "quantity": 40}]})`), the execution plane consumes the reservation right
  before the action starts (`total -= 40`, `reserved -= 40`) and the dispenser
  reports the lot *after* deduction: `60 / 60 / 0`. The workflow never tells
  the device which lot or how much — the backend does.
- **Insufficient stock**: a 500 ml requirement against a 60 ml lot fails at
  reservation time — the task ends `failed` with
  `error_info[0] = {code: "plan_not_executable", message: "requirement 'water'
  is short by 440 ml"}`, its node run is `canceled`, the device is never
  called and the lot is untouched (a failed reservation leaves nothing
  reserved).
- **Audit**: `stock_report` reads all lots of the template and shows
  `total / available / reserved`.

The device never deducts quantities itself: there is exactly one copy of the
inventory facts, in the Materials Authority.

## Install from GitHub

```bash
unilab package install https://github.com/Xuwznln/LabDeviceInventoryDemo --ref <commit-sha>
```

For local development:

```bash
git clone https://github.com/Xuwznln/LabDeviceInventoryDemo.git
cd LabDeviceInventoryDemo
python -m pip install -e .
```

No AK/SK and no cloud lab required.

## Terminating dual-runtime smoke

```bash
python -m inventory_demo.smoke --backend hostlink --timeout 90
python -m inventory_demo.smoke --backend ros2 --timeout 120
```

The smoke boots the real runtime (`unilab -g graph/inventory_demo.json`, which
also reports the `@workflow` templates to the local Workflow Authority) and
replays the web UI's calls (`POST /api/v1/workflow-tasks`, `GET
/api/v1/workflow-tasks/{uuid}/node-runs`, `GET /api/v1/materials/lots/{lot}`)
against a fresh database:

1. **"试剂入库：水 100 ml"** — `restock(quantity=100)`: lot `100 / 100 / 0`.
2. **"出库成功：分液 40 ml"** — `dispense(volume=40)` with a 40 ml requirement,
   then `stock_report`: the dispenser sees `lot_total_after = 60`,
   `lot_reserved_after = 0`; the report confirms `60 / 60 / 0`.
3. **"出库不足：分液 500 ml"** — a 500 ml requirement: task `failed`,
   `plan_not_executable` / `short by 440 ml`, node run `canceled`, lot still
   `60 / 60 / 0`.
4. **"库存盘点"** — `stock_report`: `60 / 60 / 0`, one lot.

## Manual start

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

Then open the management UI, run "试剂入库：水 100 ml" once, run "出库成功：分液 40 ml"
and "出库不足：分液 500 ml", and watch the lot at `/api/v1/materials/lots`.
Numbers above assume a fresh database; every extra `restock` adds another
100 ml to the same lot.

## Declaring inventory on a workflow step

```python
ctx.run(
    "reagent_dispenser/dispense",
    {"target": "beaker-1"},
    inventory=[{"key": "water", "kind": "reagent", "lot_uuid": WATER_LOT_UUID,
                "quantity": 40.0, "unit": "ml"}],
)
```

`inventory` is validated as `InventoryRequirement` at declaration time and
lands in the node's `meta_data.inventory_requirements`; `lot_uuid` pins one
lot, `template_uuid` lets the authority pick lots FIFO. At dispatch time the
scheduler puts the resolved allocation into the action argument named by
`key` — a `material` requirement arrives as a ResourceSlot reference
(`{"uuid": material_uuid, ...}`), a `reagent` requirement as
`{"quantity", "unit", "lots": [...]}`.

## Layout

```text
graph/inventory_demo.json          one graph shared by both backends (store + dispenser)
inventory_demo/
  reagents.py                      demo_reagent_water resource + fixed lot uuid / unit
  reagent_store.py                 restock (inbound) / stock_report
  dispenser.py                     dispense(water=<allocation injected by the scheduler>): reports the lot after deduction
  workflows.py                     @workflow templates (restock, dispense ok, dispense short, audit)
  smoke.py                         terminating real-runtime proof driven through the management API
tests/test_hostlink_smoke.py       HostLink integration assertions
```
