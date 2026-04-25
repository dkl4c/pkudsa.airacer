# AI Racer — Claude Code Agent 开发与测试指南

本文档面向参与 AI Racer 平台开发的同学，说明如何利用 Claude Code 的多 Agent 并行能力高效完成开发、调试与测试任务。

---

## 目录

1. [为什么用 Agent](#为什么用-agent)
2. [项目模块与 Agent 分工](#项目模块与-agent-分工)
3. [常用 Agent 调用模式](#常用-agent-调用模式)
   - [并行初探代码库](#1-并行初探代码库)
   - [并行编写多个模块](#2-并行编写多个模块)
   - [提交-测试闭环](#3-提交测试闭环)
   - [Bug 排查](#4-bug-排查)
   - [文档更新](#5-文档更新)
4. [具体任务示例](#具体任务示例)
   - [实现赛道 Proto 文件](#示例-a-实现赛道-proto-文件)
   - [补全测试队列 Worker](#示例-b-补全测试队列-worker)
   - [修改积分规则](#示例-c-修改积分规则)
   - [新增 API 端点](#示例-d-新增-api-端点)
5. [测试策略](#测试策略)
6. [工作流检查清单](#工作流检查清单)

---

## 为什么用 Agent

Claude Code 支持在同一会话中并发启动多个子 Agent，每个子 Agent 拥有独立上下文，可以并行阅读、编写、运行不同模块的代码。对于 AI Racer 这种**多层栈项目**（Webots 控制器 + FastAPI 后端 + 前端 + 数据库），并行 Agent 可以：

- **缩短迭代时间**：前端、后端、控制器三部分互不依赖时，一次对话即可同步推进
- **隔离上下文**：每个 Agent 只看自己负责的文件，避免上下文污染和越权修改
- **专业化分工**：用 `coder` Agent 写代码，用 `paper-specialist` Agent 读协议文档，用 `Explore` Agent 快速定位文件
- **自动并行验证**：一个 Agent 跑语法检查，另一个同时搜索相关调用点

---

## 项目模块与 Agent 分工

```
pkudsa.airacer/
├── server/          ← 后端 Agent 负责
├── webots/          ← 控制器 Agent 负责
├── frontend/        ← 前端 Agent 负责
└── docs/            ← 文档 Agent 负责（可选）
```

| 子任务 | 推荐 Agent 类型 | 说明 |
|--------|----------------|------|
| 实现/修改 Python 后端逻辑 | `coder` | 有 Bash 工具，可运行 `conda run -n airacer` 验证 |
| 实现/修改 Webots 控制器 | `coder` | 注意 Webots API 只能在仿真内运行，Agent 只负责代码正确性 |
| 实现/修改前端 HTML/JS | `coder` | 纯文件操作，无需运行时验证 |
| 快速定位文件/符号 | `Explore` | 速度快，适合搜索任务 |
| 阅读 Webots/FastAPI 文档 | `paper-specialist` | 带 WebFetch，适合读在线文档 |
| 跨多文件分析 + 改动规划 | `general-purpose` | 适合需要多轮 grep/read 的调查 |

---

## 常用 Agent 调用模式

### 1. 并行初探代码库

当你刚接手某个模块，需要快速了解结构时，启动多个 `Explore` Agent 并行读取：

```
你：请并行用三个 Explore Agent 分别探索：
    ① server/race/ 目录，搞清楚状态机和会话管理的逻辑
    ② webots/controllers/supervisor/supervisor.py，搞清楚检查点和遥测写入
    ③ frontend/race/ 目录，搞清楚回放播放器的工作流程
```

Claude 会同时在三个 Agent 里运行，几秒内返回三份摘要，你可以快速决定下一步改哪里。

---

### 2. 并行编写多个模块

当多个模块之间接口已定（见 `docs/airacer-task-book.md`），可以并行开发：

```
你：请并行启动三个代码专员：
    ① 实现 server/api/recording.py 中的 /api/recordings 列表端点
    ② 在 frontend/race/replay.js 中实现 loadTelemetry() 函数，使用 ReadableStream 流式加载 NDJSON
    ③ 在 server/race/scoring.py 中实现 extract_test_results()，解析 telemetry.jsonl 的事件计数
    
    接口约定见 docs/airacer-task-book.md 第 5 节。
    每个 Agent 完成后运行语法检查：conda run -n airacer python -m py_compile <file>
```

**关键**：要在 prompt 里告诉每个 Agent 接口约定来自哪里，让 Agent 自己去读文档，不要替它总结。

---

### 3. 提交-测试闭环

```
你：帮我验证代码提交流程是否端到端通畅：
    1. 用 Explore Agent 读 server/api/submission.py，确认 POST /api/submit 的完整处理步骤
    2. 用 coder Agent 写一个测试脚本 scripts/test_submit.py：
       - 用 requests 库向 http://localhost:8000/api/submit 提交 template/team_controller.py
       - 轮询 /api/test-status/{team_id} 直到状态变为 done 或超时 60s
       - 打印最终报告
    3. 告诉我如何运行这个脚本
```

---

### 4. Bug 排查

发现 bug 时，用 `general-purpose` Agent 做跨文件调查，避免占用主上下文：

```
你：/api/admin/start-race 调用后 WebSocket 客户端收不到 running 状态，
    请帮我调查：
    - server/api/admin.py 中 start_race() 的广播调用链
    - server/ws/admin.py 中 broadcast_state() 的实现
    - main.py 中心跳循环是否干扰了状态
    给出根因分析和修复建议，不要直接改文件。
```

---

### 5. 文档更新

改完代码后让 Agent 同步文档，避免文档腐烂：

```
你：我刚修改了 server/race/session.py，新增了 get_current_session_id() 函数，
    并修改了 server/api/admin.py 的 _running_state_for 映射（"group"→"group_race"）。
    请更新 docs/airacer-architecture.md 中涉及这两处的描述，只改动实际过时的内容。
```

---

## 具体任务示例

### 示例 A：实现赛道 Proto 文件

赛道 Proto 文件目前是空目录（`webots/protos/`），需要创建 `RaceCar.proto`：

```
你：请帮我在 webots/protos/ 目录下创建 RaceCar.proto。
    要求：
    - 继承 Webots 标准 Car proto（vehicle/Car）
    - 包含两个 Camera 设备：left_camera 和 right_camera，分辨率 640×480
    - 包含 customData 字段（VRML SFString，用于 Supervisor IPC）
    - 参考 Webots R2023b 文档中 Car proto 的写法
    
    完成后检查 VRML 语法（确保字段名和缩进正确）。
    不需要测试运行，只写文件。
```

---

### 示例 B：补全测试队列 Worker

目前 `submission.py` 有测试队列但没有 worker 消费：

```
你：server/api/submission.py 中有 _test_queue（list）和 enqueue_test() 函数，
    但没有实际执行测试的 worker。
    
    请在 server/race/ 目录下新建 test_runner.py，实现：
    1. 一个 async def run_test_worker() 无限循环，每 2 秒检查队列
    2. 取出队列头部的 submission_id，在 airacer conda 环境下通过 asyncio.to_thread 
       启动 Webots（minimize=True）运行单圈测试
    3. Webots 退出后调用 scoring.extract_test_results() 读取结果
    4. 将结果写入 DB（test_runs 表的 status/laps_completed/best_lap_time 等字段）
    5. 在 main.py 的 lifespan 中注册这个 worker（像 heartbeat_loop 一样）
    
    参考文件：
    - server/race/session.py（start_webots, monitor_webots）
    - server/race/scoring.py（extract_test_results）
    - server/db/models.py（test_runs 表结构）
    - server/api/submission.py（_test_queue 操作）
    
    完成后运行：conda run -n airacer python -m py_compile server/race/test_runner.py server/main.py
```

---

### 示例 C：修改积分规则

```
你：当前积分规则在 server/api/admin.py 的 _rank_to_points() 函数中。
    我想改为：
      1st=12, 2nd=9, 3rd=6, 4th=4, 5th=2, 其余=1
    
    另外 docs/airacer-architecture.md 里也提到了积分规则，请同步更新。
    
    请用两个并行 Agent：
    ① 修改 server/api/admin.py 中的 _rank_to_points()
    ② 修改 docs/airacer-architecture.md 中积分规则那一节
```

---

### 示例 D：新增 API 端点

```
你：请在 server/api/admin.py 中新增一个端点：
    GET /api/admin/session-history
    
    功能：返回最近 20 场比赛的摘要列表，每条包含：
      session_id, type, phase, team_ids（解析为数组）, started_at, finished_at
    
    要求：
    - 同样需要 HTTP Basic Auth（复用已有的 require_admin 依赖）
    - 结果按 rowid DESC 排序
    - 参考同文件中 get_standings() 的写法
    - 完成后语法检查：conda run -n airacer python -m py_compile server/api/admin.py
```

---

## 测试策略

由于 Webots 需要 GUI 且只能在赛场机器上运行，测试分两层：

### 层一：静态验证（任何机器，无需 Webots）

```bash
# 语法检查所有 Python 文件
cd server
conda run -n airacer python -m py_compile \
    main.py \
    api/submission.py api/admin.py api/recording.py \
    race/state_machine.py race/session.py race/scoring.py \
    ws/admin.py db/models.py

# 验证 FastAPI 应用能正常导入
conda run -n airacer python -c "from main import app; print(app.title)"
```

**让 Agent 做这件事：**
```
你：请对 server/ 目录下所有 Python 文件运行语法检查，
    使用 conda run -n airacer 环境，汇报任何失败。
```

### 层二：集成验证（需要运行服务器）

```bash
# 终端 1：启动服务器
cd server && conda run -n airacer uvicorn main:app --reload

# 终端 2：运行集成测试脚本
conda run -n airacer python scripts/test_submit.py
```

**让 Agent 做这件事（需要服务器已在运行）：**
```
你：服务器已在 localhost:8000 运行。
    请用 requests 库测试以下流程并报告结果：
    1. GET /api/teams — 应返回队伍列表
    2. POST /api/submit — 用 template/team_controller.py 提交，team_id=team_01
    3. GET /api/test-status/team_01 — 验证提交已入队
    不需要写文件，直接在 Bash 里用 curl 或 python -c 运行。
```

### 层三：沙盒验证（验证学生代码隔离）

```bash
# 测试正常代码
conda run -n airacer python webots/controllers/car/sandbox_runner.py \
    --team-id test --code-path template/team_controller.py
# 应该等待 stdin 输入（正常）

# 测试危险 import（应退出码 2）
echo "import os" > /tmp/bad_ctrl.py
echo "def control(a,b,t): return 0.0, 0.5" >> /tmp/bad_ctrl.py
conda run -n airacer python webots/controllers/car/sandbox_runner.py \
    --team-id test --code-path /tmp/bad_ctrl.py
echo "Exit code: $?"  # 期待: 2
```

---

## 工作流检查清单

每次开发迭代后，让 Agent 依次确认以下项目：

```
你：请帮我做一次提交前检查：
    □ server/ 下所有 .py 文件语法无误
    □ car_node_id 使用 1-indexed（car_1…car_4），不存在 car_0
    □ session_type 为 "qualifying"/"group_race"/"semi"/"final"，无 "group" 拼写
    □ 所有 DB 操作使用 get_db(DB_PATH) 上下文管理器
    □ 没有绕过 state_machine.transition() 直接修改状态
    □ 新增的 API 端点已在 main.py 的 include_router 中注册
    □ 新增的 async 任务已在 lifespan() 中启动和取消
    
    如发现任何问题，直接修复并报告。
```

---

## 重要约束提醒

给 Agent 写 prompt 时，请始终注明以下背景，避免 Agent 做出错误假设：

1. **Python 环境**：所有命令使用 `conda run -n airacer python`，不用系统 python
2. **Webots API**：`controller`、`vehicle` 模块只在 Webots 进程内可用，Agent 不能运行包含这些 import 的文件进行测试
3. **无 ORM**：不引入 SQLAlchemy 或任何 ORM，所有 DB 操作用原生 `sqlite3`
4. **无前端框架**：不引入 React/Vue/npm，前端只用原生 HTML/CSS/JS
5. **Windows 路径**：config.py 中路径使用原始字符串 `r"..."` 或正斜杠
6. **car_node_id**：世界文件 DEF 名是 `car_1`…`car_4`，后端生成时必须 1-indexed
