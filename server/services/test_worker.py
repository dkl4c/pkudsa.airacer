"""
services/test_worker.py — 测试队列消费者

启动后轮询内存测试队列，逐个调用 Sim Node 执行单车辆测试赛，
完成后通过 race_service.on_test_run_ended() 写入结果。
"""

import asyncio
import base64
import datetime
import logging
import pathlib

from server.config.config import DB_PATH
from server.database.action import (
    db_get_submission_by_id,
    db_get_team_secure,
    update_test_run,
)
from server.database.models import get_db
from server.services.race_service import on_test_run_ended
from server.utils.simnode_client import (
    get_race_result as simnode_get_result,
    get_race_status as simnode_get_status,
    start_race as simnode_start_race,
)

logger = logging.getLogger(__name__)


async def _test_worker_loop() -> None:
    """主循环：每 2 秒取队列头部任务，串行处理。"""
    from server.blueprints.submission import dequeue_test

    while True:
        await asyncio.sleep(2)

        task = dequeue_test()
        if task is None:
            continue

        try:
            await _run_single_test(task)
        except Exception:
            logger.exception(f"测试 worker 异常: {task}")
            try:
                with get_db(DB_PATH) as conn:
                    update_test_run(
                        conn, task["test_run_id"],
                        status="error",
                        finish_reason="worker_exception",
                    )
            except Exception:
                pass


async def _run_single_test(task: dict) -> None:
    """执行单个测试：读代码 → 调 Sim Node → 轮询结果 → 写库。"""
    submission_id = task["submission_id"]
    test_run_id = task["test_run_id"]
    team_id = task["team_id"]

    # 1. 查 DB 获取提交和队伍信息
    with get_db(DB_PATH) as conn:
        sub = db_get_submission_by_id(conn, submission_id)
        team = db_get_team_secure(conn, team_id)

    if sub is None:
        _mark_error(test_run_id, "submission_not_found")
        return
    if team is None:
        _mark_error(test_run_id, "team_not_found")
        return

    code_path = pathlib.Path(sub["code_path"])
    if not code_path.exists():
        _mark_error(test_run_id, "code_file_missing")
        return

    # 2. 构建单车 cars 列表
    code_b64 = base64.b64encode(code_path.read_bytes()).decode()
    cars = [{
        "car_slot":  "car_1",
        "team_id":   team["id"],
        "team_name": team["name"],
        "code_b64":  code_b64,
    }]

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    race_id = f"test_{team_id}_{task['slot_name']}_{timestamp}"

    # 3. 标记 test_run 为 running
    now = datetime.datetime.now().isoformat()
    with get_db(DB_PATH) as conn:
        update_test_run(conn, test_run_id, status="running", started_at=now)

    # 4. 调用 Sim Node
    try:
        await asyncio.to_thread(simnode_start_race, race_id, "test", 3, cars)
    except RuntimeError as exc:
        _mark_error(test_run_id, f"simnode_unreachable: {exc}")
        return

    logger.info(f"测试赛已启动: {race_id}")

    # 5. 轮询等待完成
    none_strikes = 0
    while True:
        await asyncio.sleep(5)
        status = await asyncio.to_thread(simnode_get_status, race_id)

        if status is None:
            none_strikes += 1
            if none_strikes >= 3:
                _mark_error(test_run_id, "simnode_lost")
                return
            continue

        none_strikes = 0

        if status == "completed":
            result = await asyncio.to_thread(simnode_get_result, race_id)
            if result:
                on_test_run_ended(test_run_id, result)
                logger.info(f"测试赛完成: {race_id}")
            else:
                _mark_error(test_run_id, "no_result_from_simnode")
            return

        if status in ("error", "cancelled"):
            _mark_error(test_run_id, f"simnode_{status}")
            return


def _mark_error(test_run_id: int, reason: str) -> None:
    logger.warning(f"测试赛失败 (test_run_id={test_run_id}): {reason}")
    try:
        with get_db(DB_PATH) as conn:
            update_test_run(conn, test_run_id, status="error", finish_reason=reason)
    except Exception:
        logger.exception(f"写入错误状态失败 (test_run_id={test_run_id})")
