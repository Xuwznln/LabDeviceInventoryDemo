"""库存演示的有限时 smoke：起真实运行时，经管理 HTTP API 依次提交四条工作流并核对库存数字。

网页上的操作序列在这里逐一复现：

1. ``GET  /api/v1/workflows``                 找到 host 启动时上报的 @workflow 模板；
2. ``POST /api/v1/workflow-tasks``            「试剂入库」→「出库成功」→「出库不足」→「库存盘点」；
3. ``GET  /api/v1/workflow-tasks/{uuid}``      等终态；出库不足的任务应 failed 且
   ``error_info[0] = {code: plan_not_executable, message: ... short by 440 ml}``；
4. ``GET  /api/v1/workflow-tasks/{uuid}/node-runs`` 读节点结果（分液器回报扣减后的 lot）；
5. ``GET  /api/v1/materials/lots/{lot_uuid}``  直接向权威核对 lot 的 total / available / reserved。

预期数字（每次 smoke 用全新数据库）：入库后 100/100/0 → 分液 40 后 60/60/0 →
500 ml 需求被拒绝后仍 60/60/0（失败的预留不留痕）。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Sequence

#: 与 inventory_demo/workflows.py、reagents.py 保持一致（smoke 独立运行，不 import 设备包）。
RESTOCK_WORKFLOW_NAME = "试剂入库：水 100 ml"
DISPENSE_OK_WORKFLOW_NAME = "出库成功：分液 40 ml"
DISPENSE_SHORT_WORKFLOW_NAME = "出库不足：分液 500 ml"
AUDIT_WORKFLOW_NAME = "库存盘点"
WATER_LOT_UUID = "6b1d5f2e-3c84-4a17-8f52-000000004101"

TERMINAL = {"succeeded", "failed"}


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


def _return_value(proof: dict[str, Any], index: int) -> dict[str, Any]:
    return proof["node_runs"][index]["return_info"]["return_value"]


def assert_restock(proof: dict[str, Any]) -> None:
    assert proof["task_status"] == "succeeded", proof
    value = _return_value(proof, 0)
    assert value["added"] == 100.0 and value["unit"] == "ml", value
    lot = value["lot"]
    assert lot["lot_uuid"] == WATER_LOT_UUID
    assert (lot["quantity_total"], lot["quantity_available"], lot["quantity_reserved"]) == (100.0, 100.0, 0.0), lot
    assert proof["lot"]["quantity_total"] == 100.0 and proof["lot"]["quantity_reserved"] == 0.0, proof["lot"]


def assert_dispense_ok(proof: dict[str, Any]) -> None:
    """出库成功：预留 40 → 动作开始扣减 → 分液器读到 total 60；盘点节点确认 reserved 归零。"""

    assert proof["task_status"] == "succeeded", proof
    dispense = _return_value(proof, 0)
    # 体积与 lot 不是工作流参数：来自调度器按需求 key=water 注入的权威分配
    assert dispense["volume"] == 40.0 and dispense["unit"] == "ml" and dispense["target"] == "beaker-1", dispense
    assert dispense["lots"] == [
        {
            "lot_uuid": WATER_LOT_UUID,
            "dispensed": 40.0,
            "quantity_total": 60.0,
            "quantity_available": 60.0,
            "quantity_reserved": 0.0,
        }
    ], dispense
    # 执行面在动作开始前已消耗预留：total 已减 40，reserved 已归零
    assert dispense["lot_total_after"] == 60.0, dispense
    assert dispense["lot_reserved_after"] == 0.0, dispense
    assert dispense["lot_available_after"] == 60.0, dispense
    report = _return_value(proof, 1)
    assert (report["quantity_total"], report["quantity_available"], report["quantity_reserved"]) == (60.0, 60.0, 0.0), report
    assert proof["lot"]["quantity_total"] == 60.0, proof["lot"]


def assert_dispense_short(proof: dict[str, Any]) -> None:
    """出库不足：任务在派发前失败，节点 canceled，设备未被调用，库存不变。"""

    assert proof["task_status"] == "failed", proof
    (error,) = proof["task_error_info"]
    assert error["code"] == "plan_not_executable", error
    assert "short by 440" in error["message"], error
    (node_run,) = proof["node_runs"]
    assert node_run["status"] == "canceled", node_run
    assert node_run["return_info"] == {}, node_run
    assert node_run["attempt_count"] == 1
    # 失败的预留不能留下 reserved，也不能扣减
    lot = proof["lot"]
    assert (lot["quantity_total"], lot["quantity_available"], lot["quantity_reserved"]) == (60.0, 60.0, 0.0), lot


def assert_audit(proof: dict[str, Any]) -> None:
    assert proof["task_status"] == "succeeded", proof
    report = _return_value(proof, 0)
    assert (report["quantity_total"], report["quantity_available"], report["quantity_reserved"]) == (60.0, 60.0, 0.0), report
    assert len(report["lots"]) == 1 and report["lots"][0]["lot_uuid"] == WATER_LOT_UUID, report


# ---------------------------------------------------------------------------
# 进程与 HTTP
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _graph_path(repo_root: Path) -> Path:
    installed = Path(sysconfig.get_path("data")) / "share" / "inventory_demo" / "graph" / "inventory_demo.json"
    if installed.is_file():
        return installed
    source = repo_root / "graph" / "inventory_demo.json"
    if source.is_file():
        return source
    raise FileNotFoundError("Inventory demo graph 未随 distribution 安装")


def _base_command(repo_root: Path, database_root: Path, management_port: int, backend: str) -> list[str]:
    import unilabos

    config_path = Path(unilabos.__file__).resolve().parent / "config" / "example_config.py"
    command = [
        sys.executable,
        "-m",
        "unilabos",
        "--backend",
        backend,
        "--skip_env_check",
        "--devices",
        str(repo_root / "inventory_demo"),
        "--external_devices_only",
        "--visual",
        "disable",
        "--disable_browser",
        "--port",
        str(management_port),
        "--server_database_root",
        str(database_root),
        "--working_dir",
        str(database_root / "work"),
        "--config",
        str(config_path),
        "-g",
        str(_graph_path(repo_root)),
    ]
    if backend == "ros2":
        command.append("--disable_hostlink")
    return command


def _api_request(port: int, path: str, payload: dict[str, Any] | None = None) -> Any:
    """请求管理 API；workflow 风格 {"code":0,"data":...} 自动解包，其余路由原样返回。"""

    url = f"http://127.0.0.1:{port}/api/v1{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if payload is None else "POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.loads(response.read().decode("utf-8"))
    if isinstance(body, dict) and "code" in body:
        if body["code"] != 0:
            raise RuntimeError(f"管理 API {path} 返回错误: {body}")
        return body.get("data")
    return body


def _wait_management_api(port: int, process: subprocess.Popen[Any], deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("runtime process exited before the management API came up")
        try:
            if _api_request(port, "/health").get("status") == "ok":
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    raise RuntimeError("管理 API 未在时限内就绪")


def _find_workflow(port: int, name: str, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        listing = _api_request(port, "/workflows?page=1&page_size=100")
        matches = [item for item in listing["items"] if item["name"] == name]
        if matches:
            return matches[0]
        time.sleep(0.3)
    raise RuntimeError(f"未在管理 API 检索到工作流 {name!r}")


def _lot(port: int) -> dict[str, Any]:
    lot = _api_request(port, f"/materials/lots/{WATER_LOT_UUID}")
    return {
        "lot_uuid": lot["lot_uuid"],
        "quantity_total": float(lot["quantity_total"]),
        "quantity_available": float(lot["quantity_available"]),
        "quantity_reserved": float(lot["quantity_reserved"]),
    }


def run_workflow(port: int, name: str, deadline: float, *, expect_decision_free: bool = True) -> dict[str, Any]:
    """检索工作流 -> 创建任务 -> 等待终态 -> 汇总节点结果与权威 lot 数字。"""

    workflow = _find_workflow(port, name, deadline)
    task = _api_request(port, "/workflow-tasks", {"workflow_uuid": workflow["uuid"], "run_mode": "normal"})
    task_uuid = task["uuid"]
    status = ""
    final: dict[str, Any] = task
    while time.monotonic() < deadline and status not in TERMINAL:
        final = _api_request(port, f"/workflow-tasks/{task_uuid}")
        status = str(final.get("status") or "")
        if status in TERMINAL:
            break
        if expect_decision_free:
            held = [item for item in _api_request(port, "/error-decisions")["items"] if item.get("task_id") == task_uuid]
            if held:
                raise AssertionError(f"任务 {name!r} 的 attempt 进入了错误决策链: {held[0]}")
        time.sleep(0.2)
    if status not in TERMINAL:
        raise RuntimeError(f"工作流任务 {task_uuid} 未在时限内结束: {status}")
    node_runs = _api_request(port, f"/workflow-tasks/{task_uuid}/node-runs")
    return {
        "workflow_uuid": workflow["uuid"],
        "workflow_name": name,
        "task_uuid": task_uuid,
        "task_status": status,
        "task_error_info": list(final.get("error_info") or []),
        "node_runs": [
            {
                "uuid": run["uuid"],
                "status": run["status"],
                "attempt_count": int(run.get("attempt_count") or 0),
                "return_info": dict(run.get("return_info") or {}),
                "error_info": list(run.get("error_info") or []),
            }
            for run in node_runs
        ],
        "lot": _lot(port),
    }


def run_smoke(backend: str = "hostlink", timeout: float = 60.0) -> dict[str, Any]:
    """启动真实图，经管理 API 依次提交四条工作流并核对库存数字，返回可机读证据。"""

    if backend not in {"hostlink", "ros2"}:
        raise ValueError("backend must be hostlink or ros2")
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=f"inventory-demo-{backend}-") as directory:
        root = Path(directory)
        log_path = root / "runtime.log"
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        management_port = _free_port()
        command = _base_command(repo_root, root / "db", management_port, backend)
        if backend == "hostlink":
            command += ["--hostlink_bind", "127.0.0.1", "--hostlink_port", str(_free_port())]
        else:
            domain_id = str(10 + management_port % 190)
            environment["ROS_DOMAIN_ID"] = domain_id
            command += ["--ros_domain_id", domain_id]

        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command, cwd=repo_root, env=environment, stdout=output, stderr=subprocess.STDOUT, text=True
            )
            try:
                deadline = time.monotonic() + timeout
                _wait_management_api(management_port, process, deadline)
                restock = run_workflow(management_port, RESTOCK_WORKFLOW_NAME, deadline)
                assert_restock(restock)
                dispense_ok = run_workflow(management_port, DISPENSE_OK_WORKFLOW_NAME, deadline)
                assert_dispense_ok(dispense_ok)
                dispense_short = run_workflow(management_port, DISPENSE_SHORT_WORKFLOW_NAME, deadline)
                assert_dispense_short(dispense_short)
                audit = run_workflow(management_port, AUDIT_WORKFLOW_NAME, deadline)
                assert_audit(audit)
                return {
                    "success": True,
                    "backend": backend,
                    "restock": restock,
                    "dispense_ok": dispense_ok,
                    "dispense_short": dispense_short,
                    "audit": audit,
                }
            except Exception:
                sys.stderr.write("SMOKE FAILED\n" + log_path.read_text(encoding="utf-8", errors="replace") + "\n")
                raise
            finally:
                _stop(process)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("hostlink", "ros2"), default="hostlink")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    print(json.dumps(run_smoke(args.backend, args.timeout), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
