# AiRacer 新系统 Demo 测试指南

> 适用版本：含赛区管理 + 多 Controller 槽位 + 多赛场并发重构后的最新系统。
> 所有命令行操作均基于 **Windows PowerShell**。
>
> **重要**：Body 含中文时必须用 `[Text.Encoding]::UTF8.GetBytes(...)` 传字节数组，
> 否则 PowerShell 默认 ASCII 编码会导致中文乱码。

---

## 目录

1. [环境启动](#1-环境启动)
2. [创建赛区](#2-创建赛区)
3. [队伍注册](#3-队伍注册)
4. [上传 Controller 与槽位管理](#4-上传-controller-与槽位管理)
5. [Admin 比赛控制](#5-admin-比赛控制)
6. [多赛区并发验证](#6-多赛区并发验证)
7. [赛制自适应验证](#7-赛制自适应验证)
8. [积分榜与录像](#8-积分榜与录像)
9. [常见问题](#9-常见问题)

---

## 准备：Admin 认证变量

在 PowerShell 会话开头运行一次，后续命令直接复用 `$cred`：

```powershell
$cred = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:admin123"))
```

> 密码在 `server/config/config.yaml` 的 `ADMIN_PASSWORD` 中配置（当前值：`admin123`）。
> Simnode 端口在同文件 `SIMNODE_URL` 中配置（当前值：`http://localhost:5000`）。

---

## 1. 环境启动

### 1.1 启动 Simnode

打开第一个 PowerShell 窗口，切换到项目根目录：

```powershell
conda activate airacer
uvicorn simnode.server:app --host 0.0.0.0 --port 5000
```

确认输出包含：
```
Application startup complete.
```

### 1.2 启动后端

打开第二个 PowerShell 窗口：

```powershell
conda activate airacer
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```

确认输出包含：
```
Application startup complete.
```

### 1.3 打开前端

浏览器访问 `http://localhost:8000`，应看到 AiRacer 首页（深色渐变 Hero + 赛区卡片区域）。

---

## 2. 创建赛区

### 2.1 Admin 登录

进入 `http://localhost:8000/admin/`，在弹出框输入凭据（用户名随意，密码 `admin123`）。

### 2.2 创建两个测试赛区

**推荐方式：Admin 控制台 UI**

在左侧侧边栏底部点击 **＋ 新建赛区**，依次填写：

| 字段 | 赛区 A | 赛区 B |
|------|--------|--------|
| ID | `cs` | `is` |
| 名称 | 计算机科学班 | 信息科学班 |
| 描述 | 任意 | 任意 |
| 总圈数 | 3 | 3 |

**或通过 PowerShell（Body 含中文须用 UTF-8 字节数组）：**

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones" -Method POST `
  -Headers @{ Authorization = "Basic $cred" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes('{"id":"cs","name":"计算机科学班","description":"CS组","total_laps":3}'))

Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones" -Method POST `
  -Headers @{ Authorization = "Basic $cred" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes('{"id":"is","name":"信息科学班","description":"IS组","total_laps":3}'))
```

**验证：**

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones" `
  -Headers @{ Authorization = "Basic $cred" }
```

应返回包含 `cs` 和 `is` 的数组，`name` 字段显示正确中文。前端首页刷新后出现两张赛区卡片。

---

## 3. 队伍注册

### 3.1 注册测试队伍

cs 赛区注册 4 支队伍（队名含中文，使用 UTF-8 字节数组）：

```powershell
foreach ($i in 1..4) {
  $json = "{`"zone_id`":`"cs`",`"team_id`":`"cs_team$i`",`"team_name`":`"CS队$i`",`"password`":`"pass$i`"}"
  Invoke-RestMethod -Uri "http://localhost:8000/api/register" -Method POST `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($json))
}
```

is 赛区注册 6 支队伍：

```powershell
foreach ($i in 1..6) {
  $json = "{`"zone_id`":`"is`",`"team_id`":`"is_team$i`",`"team_name`":`"IS队$i`",`"password`":`"pass$i`"}"
  Invoke-RestMethod -Uri "http://localhost:8000/api/register" -Method POST `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($json))
}
```

**验证：**

```powershell
(Invoke-RestMethod -Uri "http://localhost:8000/api/zones/cs").team_count   # 期望: 4
(Invoke-RestMethod -Uri "http://localhost:8000/api/zones/is").team_count   # 期望: 6
```

---

## 4. 上传 Controller 与槽位管理

以 `cs_team1` 为例，演示三槽位完整流程。

### 4.1 准备测试 Controller 文件

在项目根目录创建两个测试文件：

```powershell
Set-Content -Path "test_driver_fast.py" -Encoding utf8 -Value @"
def control(img_front, img_rear, speed):
    return 0.8, 0.0
"@

Set-Content -Path "test_driver_cautious.py" -Encoding utf8 -Value @"
def control(img_front, img_rear, speed):
    return 0.4, 0.0
"@
```

### 4.2 上传到不同槽位

Controller 代码为纯 ASCII，Body 无需 UTF-8 字节数组：

```powershell
$fast     = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\test_driver_fast.py"))
$cautious = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\test_driver_cautious.py"))

# 上传到 main 槽位
Invoke-RestMethod -Uri "http://localhost:8000/api/submit" -Method POST `
  -ContentType "application/json" `
  -Body "{`"team_id`":`"cs_team1`",`"password`":`"pass1`",`"code`":`"$fast`",`"slot_name`":`"main`"}"

# 上传到 dev 槽位
Invoke-RestMethod -Uri "http://localhost:8000/api/submit" -Method POST `
  -ContentType "application/json" `
  -Body "{`"team_id`":`"cs_team1`",`"password`":`"pass1`",`"code`":`"$cautious`",`"slot_name`":`"dev`"}"
```

**验证三槽位状态：**

```powershell
$teamCred = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("cs_team1:pass1"))
Invoke-RestMethod -Uri "http://localhost:8000/api/test-status/cs_team1" `
  -Headers @{ Authorization = "Basic $teamCred" }
```

期望输出结构：

```json
{
  "team_id": "cs_team1",
  "slots": {
    "main":   { "version": "...", "is_race_active": true,  "queue_status": "...", "test": null },
    "dev":    { "version": "...", "is_race_active": false, "queue_status": "...", "test": null },
    "backup": { "version": null,  "is_race_active": false, "queue_status": "no_run", "test": null }
  }
}
```

### 4.3 切换激活槽位

将 `cs_team1` 的参赛槽位从 `main` 改为 `dev`：

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/activate" -Method POST `
  -ContentType "application/json" `
  -Body '{"team_id":"cs_team1","password":"pass1","slot_name":"dev"}'
```

再次查询，确认 `dev.is_race_active = true`，`main.is_race_active = false`。

### 4.4 前端验证

访问 `http://localhost:8000/submit/`，登录 `cs_team1` / `pass1`，可看到三张槽位卡片，`dev` 卡片顶部有绿色 **ACTIVE** 徽章。

---

## 5. Admin 比赛控制

先为 cs 赛区其余队伍批量上传 controller：

```powershell
$base = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\test_driver_fast.py"))
foreach ($i in 2..4) {
  Invoke-RestMethod -Uri "http://localhost:8000/api/submit" -Method POST `
    -ContentType "application/json" `
    -Body "{`"team_id`":`"cs_team$i`",`"password`":`"pass$i`",`"code`":`"$base`",`"slot_name`":`"main`"}"
}
```

### 5.1 设置并启动 cs 赛区第一场

**推荐方式：Admin 控制台 UI**

1. 侧边栏点击 **cs**（计算机科学班）
2. 切换到**比赛控制** Tab
3. 点击**设置场次** → 选择 `qualifying` 阶段 → 确认
4. 点击**开始比赛**

**或通过 PowerShell（Body 无中文，直接传字符串）：**

```powershell
# 设置场次
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/set-session" `
  -Method POST `
  -Headers @{ Authorization = "Basic $cred" } `
  -ContentType "application/json" `
  -Body '{"session_type":"qualifying","session_id":"cs_q1","team_ids":["cs_team1","cs_team2","cs_team3","cs_team4"],"total_laps":3}'

# 启动仿真
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/start-race" `
  -Method POST -Headers @{ Authorization = "Basic $cred" }
```

**验证：**
- Admin 控制台状态变为 `RUNNING`（绿色闪烁点）
- 车辆实时数据通过 WebSocket 推送更新
- 俯视摄像头图像开始刷新

### 5.2 手动停止比赛

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/stop-race" `
  -Method POST -Headers @{ Authorization = "Basic $cred" }
```

比赛结束后状态变为 `COMPLETED`，成绩写入数据库。

---

## 6. 多赛区并发验证

**核心测试：两个赛区同时运行，互不干扰。**

### 6.1 为 is 赛区批量上传 controller 并启动

```powershell
$base = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\test_driver_fast.py"))
foreach ($i in 1..6) {
  Invoke-RestMethod -Uri "http://localhost:8000/api/submit" -Method POST `
    -ContentType "application/json" `
    -Body "{`"team_id`":`"is_team$i`",`"password`":`"pass$i`",`"code`":`"$base`",`"slot_name`":`"main`"}" | Out-Null
}

# 设置 is 赛区场次
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/is/set-session" `
  -Method POST `
  -Headers @{ Authorization = "Basic $cred" } `
  -ContentType "application/json" `
  -Body '{"session_type":"qualifying","session_id":"is_q1","team_ids":["is_team1","is_team2","is_team3","is_team4"],"total_laps":3}'

# 启动（此时 cs 赛区也在运行）
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/is/start-race" `
  -Method POST -Headers @{ Authorization = "Basic $cred" }
```

### 6.2 检查两个 Webots 进程

```powershell
Get-Process | Where-Object { $_.Name -like "*webots*" }
```

期望：列出**两条**独立的 webots 进程记录。

### 6.3 验证 WebSocket 消息隔离

打开浏览器开发者工具 → Network → WS，连接 `/ws/admin`：

- 切换 Admin 控制台到 **cs** 赛区，`sim_time_approx` 显示 cs 的仿真进度
- 切换到 **is** 赛区，数字独立更新，两者互不覆盖

### 6.4 一场结束，另一场继续

停止 cs 赛区：

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/stop-race" `
  -Method POST -Headers @{ Authorization = "Basic $cred" }
```

检查两个赛区状态互不影响：

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones" `
  -Headers @{ Authorization = "Basic $cred" } |
  ForEach-Object { "$($_.id): $($_.state)" }
```

期望：cs 为 `IDLE`，is 仍为 `RUNNING`。

---

## 7. 赛制自适应验证

### 7.1 通过 API 查询 bracket

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/bracket" `
  -Headers @{ Authorization = "Basic $cred" }
```

| 赛区队伍数 | 期望 stages |
|-----------|-------------|
| ≤ 4       | `qualifying → final` |
| 5–8       | `qualifying → semi → final` |
| ≥ 9       | `qualifying → group_race → semi → final` |

### 7.2 Python 快速验证

```powershell
conda activate airacer
python -c "
from server.race.bracket import compute_bracket
for n in [2, 4, 6, 8, 12, 16, 20]:
    b = compute_bracket(n)
    print(f'n={n:>2}: {b[chr(34)]stages[chr(34)]}')
    print(f'       sessions={b[chr(34)]sessions_per_stage[chr(34)]}')
    print(f'       advancement={b[chr(34)]advancement[chr(34)]}')
"
```

或直接在 Python REPL 里运行：

```python
from server.race.bracket import compute_bracket
for n in [2, 4, 6, 8, 12, 16, 20]:
    b = compute_bracket(n)
    print(f"n={n:>2}: {b['stages']}")
    print(f"       sessions={b['sessions_per_stage']}")
    print(f"       advancement={b['advancement']}")
```

---

## 8. 积分榜与录像

### 8.1 查看赛区积分榜

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/admin/zones/cs/standings" `
  -Headers @{ Authorization = "Basic $cred" }
```

或在 Admin 控制台切换到**积分榜** Tab，前 3 名显示金银铜样式。

公开赛区页面（无需登录）：`http://localhost:8000/zone/?id=cs`

### 8.2 查看录像列表

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/recordings" `
  -Headers @{ Authorization = "Basic $cred" }
```

浏览器访问 `http://localhost:8000/race/`，可按赛区、阶段筛选录像并回放。

---

## 9. 常见问题

### Q: 中文名称显示为问号或乱码
**A:** PowerShell 发送 Body 时默认 ASCII 编码。含中文的请求必须用：
```powershell
-ContentType "application/json; charset=utf-8" `
-Body ([Text.Encoding]::UTF8.GetBytes('{"name":"中文"}'))
```

### Q: 启动后端时报 `zone_id column already exists`
**A:** 正常现象。迁移脚本对已有列执行 `ALTER TABLE` 会静默跳过，不影响运行。

### Q: 并发启动时报 `已达到最大并发仿真数`
**A:** 默认 `MAX_CONCURRENT_RACES = 4`。可在 `simnode/config/config.py` 中调大，或等当前比赛结束。

### Q: `set-session` 后提示某队伍没有可用 controller
**A:** 需先通过 `POST /api/submit` 上传代码。`set-session` 优先使用 `is_race_active=1` 的槽位，无则回退到 `main` 最新版本。

### Q: `Invoke-RestMethod` 报 `Unable to connect`
**A:** 确认后端已在 `:8000` 运行（终端无报错），或检查防火墙是否拦截了本地端口。

### Q: 切换 Admin 赛区后实时数据没更新
**A:** 检查浏览器 Console 是否有 WebSocket 错误。后端 `_heartbeat_loop` 每 10 秒会重播各赛区最后一条状态消息。

### Q: 前端首页赛区卡片为空
**A:** 确认已创建赛区（`GET /api/zones` 返回非空），并硬刷新页面（Ctrl+Shift+R）。

---

*文档维护：与系统代码同步更新，如有 API 变更请同步修改本文件。*
