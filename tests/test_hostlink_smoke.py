from __future__ import annotations

from inventory_demo.smoke import (
    assert_audit,
    assert_dispense_ok,
    assert_dispense_short,
    assert_restock,
    run_smoke,
)


def test_real_inventory_hostlink_smoke() -> None:
    proof = run_smoke("hostlink", timeout=90.0)
    # 添加试剂：固定 lot 入库 100 ml
    assert_restock(proof["restock"])
    # 出库成功：任务启动预留、动作开始扣减，分液器回报 total 60 / reserved 0
    assert_dispense_ok(proof["dispense_ok"])
    # 出库不足：任务在派发前 failed（plan_not_executable / short by 440 ml），库存不变
    assert_dispense_short(proof["dispense_short"])
    # 盘点：失败的预留不留痕
    assert_audit(proof["audit"])
