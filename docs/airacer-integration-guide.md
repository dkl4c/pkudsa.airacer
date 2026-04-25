# AI Racer 系统联调指南

> 版本 v1.0 | 2026-04-25  
> 本文档面向负责集成测试与联调的人员，描述当前系统状态、待完成工作，以及从前后端到 Webots 的完整联调步骤。

---

## 一、当前系统状态

### 1.1 已完成

| 模块 | 状态 | 说明 |
|------|------|------|
| FastAPI 后端 | ✅ | 全部 REST 端点 + Admin WebSocket 实现并测试 |
| SQLite 数据库 | ✅ | 队伍/提交/测试/赛次/积分 5 张表 |
| 赛事状态机 | ✅ | IDLE→各阶段 RUNNING→FINISHED/ABORTED→DONE 全链路 |
| Webots 进程管理 | ✅ | 后端可启动/停止 Webots，监控进程退出 |
| Supervisor 控制器 | ✅ | 计圈、碰撞检测（车 vs 车）、遥测写入、metadata 写入 |
| 车辆控制器框架 | ✅ | 双摄像头读取、沙箱通信、超时处理、崩溃重启 |
| 代码沙箱 | ✅ | import hook 模块级隔离，禁止危险模块 |
| 代码提交 API | ✅ | 语法检查 + 接口合规检查 + 入队 |
| 录像服务 API | ✅ | list/metadata/telemetry 三个端点 |
| 前端三个页面 | ✅ | /race/ /submit/ /admin/ 基础页面存在 |
| API 端到端测试 | ✅ | set-session→start-race→stop-race→录像 API 验证完毕 |

### 1.2 待完成

| 任务 | 负责方 | 优先级 | 说明 |
|------|--------|--------|------|
| `server/race/test_runner.py` | 后端开发 | **P0** | 测试队列 worker，学生自测依赖此模块 |
| Car PROTO 建模 | 技术组 | **P0** | 替换占位 Robot 节点，car 控制器才能运行 |
| 真实赛道建模 | 技术组 | **P0** | 替换平面占位，检查点/碰撞才有意义 |
| 更新 CHECKPOINTS | 技术组/开发 | P1 | 建模后同步 supervisor.py 第 37-42 行 |
| 更新 minimap WORLD 边界 | 技术组/开发 | P1 | 建模后同步 frontend/race/minimap.js |
| Supervisor 动态障碍 | 后端/控制器开发 | P1 | 当前 supervisor.py 仅有 TODO 占位 |
| Supervisor 加速包 | 后端/控制器开发 | P1 | 当前未实现 |
| 前端页面功能完善 | 前端开发 | P2 | Admin 控制台交互、回放播放器完整功能 |

---

## 二、待实现：测试队列 Worker

**影响：** 在此实现之前，学生提交代码后 `/api/test-status/{team_id}` 永远返回 `waiting`，测试报告不会出现。

### 2.1 任务描述

新建 `server/race/test_runner.py`，实现一个后台 async worker：

```
每 2 秒检查 submission.py 中的 _test_queue
  │
  ├── 若 state_machine.is_running() → 跳过（比赛期间不跑测试）
  ├── 若队列为空 → 等待
  └── 若有条目：取出 submission_id
        │
        ├── 更新 test_runs.status = 'running'
        ├── 调用 start_webots(minimize=True)，等待进程退出
        ├── 调用 scoring.extract_test_results() 读取报告
        └── 更新 test_runs 各字段，status = 'done'
```

**关键参考文件：**
- `server/race/session.py` — `start_webots()`, `monitor_webots()`
- `server/race/scoring.py` — `extract_test_results()`
- `server/db/models.py` — `test_runs` 表字段
- `server/api/submission.py` — `_test_queue`, `enqueue_test()`, `queue_position()`

**注册到 lifespan（server/main.py）：**

```python
# 在 lifespan 的 task = asyncio.create_task(heartbeat_loop()) 之后加：
from race.test_runner import run_test_worker
test_task = asyncio.create_task(run_test_worker())
# yield 之后取消：
test_task.cancel()
try:
    await test_task
except asyncio.CancelledError:
    pass
```

**用 Claude Code 实现（推荐 prompt）：**

```
参考 docs/airacer-agent-guide.md 示例 B，实现 server/race/test_runner.py：
- async def run_test_worker() 无限循环，每 2 秒轮询 submission.py 中的 _test_queue
- 比赛进行中（state_machine.is_running()）时跳过不消费
- 取出 submission_id，查 DB 得到 code_path 和 recording_path（格式：recordings/test_{submission_id[:8]}）
- async 方式（asyncio.to_thread）启动 Webots（minimize=True），等待退出
- 退出后调用 scoring.extract_test_results() 读结果，更新 test_runs 表
- 在 server/main.py lifespan 中注册和取消
完成后运行：conda run -n airacer python -m py_compile server/race/test_runner.py server/main.py
```

---

## 三、联调阶段划分

| 阶段 | 前置条件 | 目标 |
|------|----------|------|
| **阶段 A** | 现在即可 | 后端 + 前端基础联调（无 Webots） |
| **阶段 B** | 现在即可 | Supervisor 独立测试（占位 Robot 节点，car 控制器关闭） |
| **阶段 C** | test_runner.py 实现后 | 学生自测流程端到端 |
| **阶段 D** | 技术组建模完成后 | 完整赛次仿真（Car PROTO + 真实赛道） |
| **阶段 E** | 阶段 D 通过后 | 回归测试 + 压力测试 |

---

## 四、阶段 A：后端 + 前端基础联调

**目标：** 验证前端三个页面与后端 API/WebSocket 的基础连通。

### 4.1 启动后端

```powershell
cd D:\Documents\postgraduate\courses\26Spring\pkudsa.airacer\server
conda run -n airacer uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

确认输出中有：`Application startup complete.`

### 4.2 测试学生提交页（/submit/）

浏览器访问 `http://localhost:8000/submit/`

**测试流程：**
1. 用 team_01 / 12345 登录
2. 上传 `template/team_controller.py`（此文件应在项目根目录）
3. 页面应显示"提交成功"并显示队列位置
4. 刷新页面，`/api/test-status/team_01` 应返回 `queue_status: "waiting"`

**用 PowerShell 直接验证 API：**

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("team_01:12345"))

# 提交代码（base64编码文件内容）
$code = [Convert]::ToBase64String([IO.File]::ReadAllBytes("template\team_controller.py"))
$body = @{ team_id = "team_01"; password = "12345"; code = $code } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/submit" `
  -ContentType "application/json" -Body $body

# 查看测试状态
Invoke-RestMethod -Uri "http://localhost:8000/api/test-status/team_01" `
  -Headers @{Authorization = "Basic $auth"}
```

**预期结果：** 提交返回 `{status: "queued", version: "...", queue_position: 1}`，状态返回 `queue_status: "waiting"`。

### 4.3 测试管理控制台（/admin/）

浏览器访问 `http://localhost:8000/admin/`

**测试流程：**
1. 用管理员密码（`AIRACER_ADMIN_PASSWORD` 环境变量或默认 `12345`）登录
2. 确认 WebSocket 连接成功（应显示当前状态 `idle`）
3. 执行一次 set-session：

```powershell
$adminAuth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:12345"))
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
  -Headers @{Authorization = "Basic $adminAuth"} `
  -ContentType "application/json" `
  -Body '{"session_type":"qualifying","session_id":"qual_test","team_ids":["team_01"],"total_laps":2}'
```

**预期：** 管理控制台 WebSocket 不需要收到新消息（set-session 本身不触发广播），但 DB 中应有 `phase=waiting` 的记录。

### 4.4 测试回放页（/race/）

浏览器访问 `http://localhost:8000/race/`

**测试流程：**
1. 从录像列表加载已有录像（`test_001`）
2. 确认小地图能渲染车辆轨迹
3. 测试播放/暂停/速度控制
4. 确认排行榜随时间轴变化

---

## 五、阶段 B：Supervisor 独立测试

**目标：** 验证 Supervisor 能完整跑完一场，写出 telemetry + metadata，后端能识别完成并广播。

**前置：** 暂时把世界文件里四辆 car 的 `controller` 字段改为 `<none>`（Webots GUI 操作），避免 car 控制器报错干扰。

### 5.1 准备配置文件

后端已运行时，通过 API 自动写 `race_config.json`：

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:12345"))

# 配置一场单车排位赛（qualifying 会在5分钟后自动结束）
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
  -Headers @{Authorization = "Basic $auth"} `
  -ContentType "application/json" `
  -Body '{"session_type":"qualifying","session_id":"b_test_001","team_ids":["team_01"],"total_laps":2}'

# 启动（连带启动 Webots）
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/start-race" `
  -Headers @{Authorization = "Basic $auth"}
```

### 5.2 等待 Supervisor 自然结束

排位赛 qualifying 的结束逻辑：
- 所有车辆跑完规定圈数 → 立即结束 **（car 控制器关闭时车辆不动，永远不会跑完）**
- **或** 仿真时间达到 300 秒（5分钟）→ 超时结束

**建议：** 等待约 5 分钟，Supervisor 超时后自动写 metadata.json，Webots 退出，后端检测到退出并广播 `recording_ready`。

### 5.3 验证结果

```powershell
# 查看录像列表（应有 b_test_001）
Invoke-RestMethod -Uri "http://localhost:8000/api/recordings"

# 查看元数据
Invoke-RestMethod -Uri "http://localhost:8000/api/recordings/b_test_001/metadata"
```

**AdminWebSocket 应收到（可在浏览器控制台或 wscat 观察）：**
```json
{"type": "sim_status", "state": "recording_ready", "session_id": "b_test_001"}
```

**验收标准：**
- `metadata.json` 存在，`finish_reason: "timeout"`
- `telemetry.jsonl` 已生成，内容约 4687 行（300s / 0.064 ≈ 4687 帧）
- 后端广播 `recording_ready` 而不是 `aborted`
- `/admin/` 控制台状态变为 `recording_ready`

### 5.4 测试强制停止流程

另开一次测试，start-race 后 30 秒内执行 stop-race：

```powershell
# start-race 后等 30 秒
Start-Sleep 30
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/stop-race" `
  -Headers @{Authorization = "Basic $auth"}
```

**预期：** WebSocket 广播 `aborted`，录像列表中不出现该 session（无 metadata.json）。

之后需要 reset-track 才能继续：

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/reset-track" `
  -Headers @{Authorization = "Basic $auth"}
```

---

## 六、阶段 C：学生自测流程端到端

**前置：** `server/race/test_runner.py` 已实现并注册到 lifespan。

### 6.1 验证 worker 启动

重启后端，服务器日志应有类似输出：
```
INFO: Application startup complete.
[test_runner] worker started, polling every 2 seconds
```

### 6.2 提交代码并等待测试完成

```powershell
# 提交 template/team_controller.py
$code = [Convert]::ToBase64String([IO.File]::ReadAllBytes("template\team_controller.py"))
$body = @{ team_id = "team_01"; password = "12345"; code = $code } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/submit" `
  -ContentType "application/json" -Body $body

# 等待（最多5分钟），轮询状态
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("team_01:12345"))
do {
    $status = Invoke-RestMethod -Uri "http://localhost:8000/api/test-status/team_01" `
      -Headers @{Authorization = "Basic $auth"}
    Write-Host "status: $($status.queue_status)"
    Start-Sleep 10
} until ($status.queue_status -eq "done")

$status.report
```

**预期：** 约 5 分钟后（qualifying 300s 超时），report 出现，`finish_reason: "timeout"`，`laps_completed: 0`（因为占位节点车辆不移动）。

---

## 七、阶段 D：完整赛次仿真（技术组建模完成后）

**前置条件：**
- `webots/worlds/airacer.wbt` 已替换为真实赛道 + Car PROTO 节点
- `supervisor.py` 第 37-42 行 CHECKPOINTS 已更新为真实坐标
- `frontend/race/minimap.js` WORLD 边界已更新

### 7.1 验证 Car PROTO 节点

启动 Webots 打开 `airacer.wbt`，确认 Console 无报错：

```
✅ supervisor: [INFO] config loaded, session_id=...
✅ car_1:      [INFO] car library initialized  （不再出现 "Only nodes based on Car"）
✅ car_2~4:    正常 idle（config 中未分配则安静等待）
```

关键错误排查：
- 仍出现 `Only nodes based on Car` → 车辆节点类型不是 Car PROTO，检查 `.wbt` 中节点定义
- `left_camera / right_camera not found` → 摄像头节点未正确挂到 sensorsSlotFront
- supervisor 报 `getFromDef returned None` → DEF 名称不是 `car_1`~`car_4`

### 7.2 单车直行测试（验证基础联通）

用官方模板代码（`template/team_controller.py`，直行 speed=0.5）提交 team_01，手动启动一场排位赛：

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:12345"))
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
  -Headers @{Authorization = "Basic $auth"} `
  -ContentType "application/json" `
  -Body '{"session_type":"qualifying","session_id":"d_qual_001","team_ids":["team_01"],"total_laps":2}'

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/start-race" `
  -Headers @{Authorization = "Basic $auth"}
```

**观察 Webots 窗口：** car_1 应开始向前行驶。

**等待结束后检查录像元数据：**

```powershell
$meta = Invoke-RestMethod -Uri "http://localhost:8000/api/recordings/d_qual_001/metadata"
$meta | ConvertTo-Json
```

**验收标准：**
- `finish_reason:` 是 `"all_cars_done"` 或 `"timeout"`（完成2圈则前者）
- `final_rankings` 中有 team_01 的条目，`best_lap` 有值
- 若直行代码无法完成圈数（撞墙等），会 timeout — 属正常，说明联通

### 7.3 回放验证

```powershell
# 确认录像可用
Invoke-RestMethod -Uri "http://localhost:8000/api/recordings"
```

在浏览器 `/race/` 页面加载 `d_qual_001`，验证：
- 小地图中 team_01 的轨迹能渲染
- 时间轴拖动时位置同步更新
- 排行榜条目显示正确

### 7.4 多车竞速测试

两支队伍分别上传不同代码，配置一场分组赛：

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
  -Headers @{Authorization = "Basic $auth"} `
  -ContentType "application/json" `
  -Body '{"session_type":"group_race","session_id":"d_group_G1","team_ids":["team_01","team_02"],"total_laps":3}'
```

**验收标准：**
- 两辆车同时运动，Supervisor 正确检测碰撞
- 第一辆完赛后60秒宽限，宽限到期后 race_end 事件触发
- metadata 中 final_rankings 有完整排名

---

## 八、阶段 E：回归与压力测试

### 8.1 回归检查清单

每次代码变更后必须验证：

```
□ server/ 目录所有 .py 文件语法无误
  conda run -n airacer python -m py_compile server/main.py server/api/admin.py server/api/submission.py server/race/session.py server/race/state_machine.py server/race/scoring.py server/ws/admin.py server/db/models.py

□ FastAPI 应用能正常导入
  conda run -n airacer python -c "from main import app; print(app.title)"

□ 数据库初始化正常（无已有 DB 时）
  conda run -n airacer python inittest.py

□ 提交 API 能接受合法代码，拒绝非法代码（语法错误/接口不符）

□ set-session → start-race → stop-race 流程无异常

□ 录像 API 在有/无 metadata 两种情况下均返回正确 HTTP 状态码
```

### 8.2 状态机边界测试

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:12345"))

# 测试1：重复 start-race 应返回 409
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/start-race" `
  -Headers @{Authorization = "Basic $auth"}
# 期待：409 Conflict

# 测试2：非法状态跳转（qualifying 还未 finalize 就 set-session group_race）
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
  -Headers @{Authorization = "Basic $auth"} -ContentType "application/json" `
  -Body '{"session_type":"group_race","session_id":"illegal","team_ids":["team_01"],"total_laps":3}'
# 期待：409 Conflict，detail 说明非法转换

# 重置
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/reset-track" `
  -Headers @{Authorization = "Basic $auth"}
```

### 8.3 沙箱隔离验证

测试危险 import 是否被正确拒绝（exit code 应为 2）：

```powershell
$badCode = @"
import os
def control(l, r, t):
    return 0.0, 0.5
"@
$badCode | Out-File -Encoding utf8 bad_ctrl.py

conda run -n airacer python `
  webots/controllers/car/sandbox_runner.py `
  --team-id test --code-path bad_ctrl.py
Write-Host "Exit code: $LASTEXITCODE"  # 期待: 2

Remove-Item bad_ctrl.py
```

### 8.4 连续场次压力测试

模拟一整场排位赛（7批次）：

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:12345"))
for ($i = 1; $i -le 3; $i++) {
    Write-Host "=== 批次 $i ==="
    Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/set-session" `
      -Headers @{Authorization = "Basic $auth"} -ContentType "application/json" `
      -Body "{`"session_type`":`"qualifying`",`"session_id`":`"qual_$i`",`"team_ids`":[`"team_01`"],`"total_laps`":2}"
    Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/start-race" `
      -Headers @{Authorization = "Basic $auth"}
    
    # 等待 Webots 启动（5秒）后立即 stop 测试 aborted 流程
    Start-Sleep 5
    Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/stop-race" `
      -Headers @{Authorization = "Basic $auth"}
    Start-Sleep 2
    Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/admin/reset-track" `
      -Headers @{Authorization = "Basic $auth"}
    Start-Sleep 2
}
Write-Host "连续3次启动/停止完成，服务器应仍正常响应"
Invoke-RestMethod -Uri "http://localhost:8000/api/recordings"
```

---

## 九、关键联调问题速查

| 现象 | 排查方向 |
|------|----------|
| set-session 返回 404（team not found） | `inittest.py` 是否已运行初始化 DB；队伍 ID 是否拼写正确 |
| set-session 返回 409（非法转换） | 上一场未 reset-track；调 `POST /api/admin/reset-track` 重置 |
| start-race 返回 404（Webots 启动失败） | `WEBOTS_BINARY` 环境变量是否设置；路径是否指向 `webotsw.exe`（不是根目录快捷方式）|
| Webots 启动但 supervisor 报 `config not found` | `RACE_CONFIG_PATH` 环境变量未传入；检查 `session.py` 的 `start_webots()` 是否在 `env` 里设置了该变量 |
| supervisor 报 `getFromDef("car_1") returned None` | `.wbt` 车辆节点缺少 `DEF car_1` 关键字（只有 `name` 字段不够）|
| car 控制器报 `Only nodes based on Car` | 车辆节点类型是 Robot，不是 Car PROTO；建模后会解决 |
| car 控制器报 `left_camera not found` | 摄像头节点未正确添加到 Car PROTO 的 sensorsSlotFront |
| WebSocket 不推送状态 | 检查 `main.py` lifespan 中 heartbeat_loop 是否正常运行；检查浏览器 WebSocket 连接是否建立 |
| stop-race 后 WebSocket 广播 `aborted` 而不是 `recording_ready` | 正常 — stop-race 强杀进程，supervisor 来不及写 metadata.json |
| 录像 API 返回空列表 | `recordings/{session_id}/metadata.json` 是否存在；强杀产生的录像不会出现在列表中 |
| 前端回放看不到轨迹 | minimap.js 的 WORLD 边界是否包住了赛道坐标范围 |

---

## 十、验收总检查清单

建模完成、所有代码实现完毕后，按顺序执行以下验收：

```
□ [语法] server/ 所有 .py 文件无语法错误
□ [基础] 后端启动，三个前端页面能打开
□ [提交] team_01 能提交直行代码，返回 queue_position
□ [测试] test_runner worker 消费队列，约5分钟内 test-status 变为 done
□ [建模] Webots 启动无 "Only nodes based on Car" 报错
□ [建模] car_1 能获取 left_camera/right_camera 图像（无 NoneType 错误）
□ [仿真] team_01 用直行代码能在赛道上移动（不卡在起点）
□ [计圈] supervisor 能记录到 lap_complete 事件（需要代码能完成圈数）
□ [录像] 赛次结束后 api/recordings 出现该 session，metadata 完整
□ [回放] /race/ 页面能加载该 session，小地图显示轨迹，时间轴可拖动
□ [多车] 两队同场竞速，Webots 窗口能看到两辆车运动
□ [抢占] 第一辆完赛后 60 秒宽限倒计时，宽限到期后 race_end 事件触发
□ [积分] race_points 表有正确的 rank 和 points 写入
□ [状态] reset-track → qualifying → finalize → group_race 全链路不报错
```
