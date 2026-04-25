# Webots 安装与首次测试指南

本指南适用于 Windows 11，目标是从零完成 Webots R2023b 安装、项目接入和端到端冒烟测试。

---

## 第一步：安装 Webots R2023b

1. 前往 [https://cyberbotics.com/](https://cyberbotics.com/) → Downloads → 选择 **R2023b** 版本（不要用最新版，API 有变动）
2. 下载 Windows 安装包（`webots-R2023b_setup.exe`，约 1.5 GB）
3. 双击安装，路径可自定义（如 `D:\Webots`）
4. 安装完成后确认以下文件存在（路径以实际安装目录为准）：

```
<WEBOTS_HOME>\msys64\mingw64\bin\webotsw.exe   ← 实际可执行文件（bin 目录下）
<WEBOTS_HOME>\webots                        ← 快捷方式/链接，不能直接用于脚本调用
<WEBOTS_HOME>\lib\controller\python\
```

> **注意：** 根目录下的 `webots` 是快捷方式，`server/config.py` 和环境变量需要指向 `msys64\mingw64\bin\webotsw.exe`（真正的可执行文件）。

---

## 第二步：设置系统环境变量

项目通过两个环境变量消除硬编码路径，**永久设置一次即可**（PowerShell 以管理员身份运行，或在系统属性 → 环境变量 中手动添加）：

```powershell
# 控制器使用的 Python（conda airacer 环境）
[System.Environment]::SetEnvironmentVariable("AIRACER_PYTHON", "D:\Development\ANACONDA\envs\airacer\python.exe", "User")

# Webots 可执行文件路径（注意是 msys64\mingw64\bin\webotsw.exe，不是根目录的 webots.exe）
[System.Environment]::SetEnvironmentVariable("WEBOTS_BINARY", "D:\Webots\msys64\mingw64\bin\webotsw.exe", "User")
```

设置后**重新打开终端**使其生效，验证：

```powershell
echo $env:AIRACER_PYTHON   # 应输出 conda python 路径
echo $env:WEBOTS_BINARY    # 应输出 webots.exe 路径
```

> **Python 解释器设置（一次性）：** Webots R2023b 不支持在 `runtime.ini` 中通过 `[python] command` 键指定解释器（该键会被忽略并打印 WARNING）。正确方式是在 Webots GUI 中设置：
> **Edit → Preferences → Python command** → 填入 conda airacer 环境的 Python 路径（如 `D:\Development\ANACONDA\envs\airacer\python.exe`）。
> 设置后 Webots 会自动把 `%WEBOTS_HOME%\lib\controller\python\` 追加到 PYTHONPATH，所以 `from controller import Supervisor` / `from vehicle import Driver` 无需额外配置。

---

## 第三步：确认 config.py

`server/config.py` 中所有路径均从项目根目录自动推导，无需手动修改。
`WEBOTS_BINARY` 优先读取环境变量 `WEBOTS_BINARY`，第二步已设置则自动生效。

```python
# 无需改动，已自动推导；默认 fallback 指向常见安装位置（可能不准确，建议通过环境变量覆盖）
WEBOTS_BINARY = os.environ.get("WEBOTS_BINARY", r"D:\Webots\msys64\mingw64\bin\webotsw.exe")
```

---

## 第四步：准备测试用 race_config.json

后端在启动 Webots 之前会自动生成 `race_config.json`，但手动测试时需要自己造一份。
在项目根目录创建 `race_config.json`：

```json
{
  "session_id": "test_001",
  "session_type": "qualifying",
  "total_laps": 2,
  "recording_path": "D:/Documents/postgraduate/courses/26Spring/pkudsa.airacer/recordings/test_001",
  "cars": [
    {
      "car_node_id": "car_1",
      "team_id":     "team_01",
      "team_name":   "Alpha 队",
      "code_path":   "D:/Documents/postgraduate/courses/26Spring/pkudsa.airacer/template/team_controller.py",
      "start_position": 0
    }
  ],
  "created_at": "2026-04-25T12:00:00"
}
```

同时创建录制目录：

```bash
mkdir -p /d/Documents/postgraduate/courses/26Spring/pkudsa.airacer/recordings/test_001
```

---

## 第五步：第一次手动打开 Webots 测试

### 5.1 设置环境变量并启动 Webots

在启动 Webots **之前**，先在 PowerShell 中设置 `RACE_CONFIG_PATH`（控制器运行时从此路径读取配置）：

```powershell
$env:RACE_CONFIG_PATH = "D:\Documents\postgraduate\courses\26Spring\pkudsa.airacer\race_config.json"
& $env:WEBOTS_BINARY "D:\Documents\postgraduate\courses\26Spring\pkudsa.airacer\webots\worlds\airacer.wbt"
```

> PowerShell 调用存储在变量中的可执行文件必须加 `&`（调用运算符），否则会被当成普通表达式报错。

### 5.2 预期行为

| 控制器 | 预期结果 | 说明 |
|--------|----------|------|
| supervisor | 正常启动，开始写 telemetry.jsonl | 读取 race_config.json 成功 |
| car_1 | 可能启动失败（见下） | 占位车型没有摄像头节点 |
| car_2~4 | 进入 idle 循环 | config 里不存在这些 ID，安静等待 |

### 5.3 当前世界文件的限制

当前 `airacer.wbt` 使用的是占位 `Robot` 节点，而非正式的 Car/Vehicle 节点，存在以下问题：

- `car_controller.py` 用 `Driver` API，`Robot` 节点上调用会报错
- 没有摄像头节点，`driver.getDevice('left_camera')` 返回 None

**临时绕过方案：把 car 控制器改为 `<none>`**，让 Webots 不启动 car 控制器，只跑 supervisor：

在 Webots GUI 中：
1. 打开世界后，双击 `car_1` 节点
2. 找到 `controller` 字段，改为 `<none>`（或留空）
3. 对 car_2、car_3、car_4 同样操作
4. 保存世界（Ctrl+S）

这样 supervisor 可以独立测试，验证检查点检测、遥测写入、metadata 生成等核心逻辑。

### 5.4 验证 supervisor 正常工作

仿真运行约 10 秒后，检查录制目录：

```bash
ls /d/Documents/postgraduate/courses/26Spring/pkudsa.airacer/recordings/test_001/
# 应该看到 telemetry.jsonl 正在增长
```

查看遥测内容：

```bash
head -3 /d/Documents/postgraduate/courses/26Spring/pkudsa.airacer/recordings/test_001/telemetry.jsonl
```

正常输出示例：
```json
{"t": 0.064, "cars": [{"team_id": "team_01", "x": 0.0, "y": 0.0, ...}], "events": []}
{"t": 0.128, "cars": [...], "events": []}
```

---

## 第六步：通过后端 API 测试完整流程

服务器运行中时，可以通过 Admin API 来让后端自动启动 Webots，无需手动设置环境变量。

### 6.1 配置一场比赛

```bash
curl -X POST http://localhost:8000/api/admin/set-session \
  -u admin:12345 \
  -H "Content-Type: application/json" \
  -d '{
    "session_type": "qualifying",
    "session_id":   "qual_001",
    "team_ids":     ["team_01"],
    "total_laps":   2
  }'
```

预期返回：`{"status": "ready", "session_id": "qual_001"}`

### 6.2 启动比赛（后端自动启动 Webots）

```bash
curl -X POST http://localhost:8000/api/admin/start-race \
  -u admin:12345
```

预期返回：`{"status": "running", "session_id": "qual_001", "pid": <webots_pid>}`

此时管理后台 WebSocket 应收到 `{"type": "sim_status", "state": "running", ...}`。

### 6.3 停止比赛

```bash
curl -X POST http://localhost:8000/api/admin/stop-race \
  -u admin:12345
```

### 6.4 查看录制结果

```bash
# 列出所有已完成录制
curl http://localhost:8000/api/recordings

# 查看指定场次元数据（注意：该结束后才有 metadata.json，stop-race 强杀则无）
curl http://localhost:8000/api/recordings/qual_001/metadata
```

---

## 常见问题

### Q: Webots 提示找不到控制器 `supervisor` / `car`

Webots 根据世界文件路径自动确定控制器目录（`worlds/../controllers/`）。确认路径结构是：

```
webots/
  worlds/airacer.wbt
  controllers/
    supervisor/supervisor.py
    car/car.py              ← Webots 要求入口文件与目录同名
    car/car_controller.py   ← 实际逻辑在此，car.py 用 runpy 委派过来
```

如果不在这个结构下，在 Webots → Tools → Preferences → Extra project path 中添加 `webots/` 目录。

### Q: 控制器报 `ModuleNotFoundError: No module named 'controller'`

说明 Webots 没有正确把库路径注入 PYTHONPATH。检查：
1. `runtime.ini` 文件是否存在且格式正确
2. Webots 版本是否确为 R2023b

手动排查：在控制器顶部临时加一行：
```python
import sys; print(sys.path)  # 检查 Webots lib 路径是否在其中
```

### Q: `from vehicle import Driver` 报 ImportError

当前占位 `.wbt` 里的 `car` 节点类型不是 Vehicle/Car 节点，`Driver` 类无法绑定。**在正式赛道建模完成前，这个错误是预期的**，先用临时方案（参见第五步）只跑 supervisor。

### Q: telemetry.jsonl 没有生成

检查 race_config.json 中的 `recording_path`：
- 路径必须用正斜杠（`/`）或 `\\`
- 目录必须已存在（supervisor 会尝试 `os.makedirs`，但如果路径有误会静默失败）

---

## 快速检查清单

```
□ Webots R2023b 安装完成
□ config.py 中 WEBOTS_BINARY 路径正确
□ runtime.ini 存在于 supervisor/ 和 car/ 目录
□ 测试用 race_config.json 已创建，recording_path 目录已存在
□ 手动测试：supervisor 能启动并写出 telemetry.jsonl
□ API 测试：set-session + start-race 流程正常
□ 管理后台 WebSocket 能收到状态变化
```
