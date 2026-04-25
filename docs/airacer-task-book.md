# AI Racer 平台开发任务书

> 2026年春季数算B大作业——AI赛车竞速平台
> 版本 v1.4 | 2026-04-24

---

## 一、项目概述

### 1.1 背景

本平台为课程大作业竞赛系统。参赛学生团队（约25组）各自编写纯视觉自动驾驶算法，算法以 Python 文件形式提交，由平台加载到 Webots 仿真车辆上，在同一台中心机器上进行仿真竞速比赛。仿真过程以录制文件形式保存，比赛结束后可通过前端回放播放器进行回放。

### 1.2 系统功能要求

1. **输入限制**：所有参赛车辆的控制算法唯一数据来源为双目摄像头图像（BGR格式 numpy 数组），平台不向算法提供位置坐标、速度、地图或任何其他传感器数据
2. **统一参数**：所有参赛车辆的物理参数、摄像头参数、起跑位置间距完全一致
3. **录制回放**：仿真过程持续写入遥测数据文件（JSONL格式），仿真结束后可通过前端回放播放器加载并播放
4. **自助模拟测试**：参赛队伍在赛前提交代码后，可在平台上申请单车模拟测试，查看算法运行结果，并可多次更新提交
5. **代码隔离执行**：每支队伍的代码运行在独立沙箱进程中，不能访问文件系统、网络或其他队伍的数据
6. **赛程管理**：助教通过管理控制台控制赛程推进，包括开始/停止比赛、锁定代码提交、调整分组对阵

### 1.3 技术栈

| 层次 | 技术 |
|------|------|
| 仿真平台 | Webots R2023b（Windows 版）|
| 仿真控制器 | Python 3.10+ |
| 后端 | Python / FastAPI / SQLite / WebSocket |
| 前端 | HTML + CSS + JavaScript（原生，不依赖前端框架）|
| 运行平台 | Windows 11（中心机器），局域网内浏览器访问 |

### 1.4 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                    中心机器 (Windows 11)                       │
│                                                              │
│  ┌──────────────────────┐    ┌──────────────────────────┐   │
│  │      模块一           │    │        模块二              │   │
│  │  Webots 仿真          │录制 │    赛事管理后端            │   │
│  │  赛道 + 控制器 + 沙箱  │文件 │    FastAPI + WebSocket   │   │
│  │  （写入 telemetry.jsonl）│◄──►│    （提供录制文件服务）     │   │
│  └──────────────────────┘    └────────────┬─────────────┘   │
│                                           │ WebSocket / REST  │
└───────────────────────────────────────────┼──────────────────┘
                                            │
                                            ▼
                               ┌────────────────────────────┐
                               │      模块三：前端（浏览器访问）│
                               │  回放播放页 / 提交页 / 控制台 │
                               └────────────────────────────┘
```

---

## 二、赛事规则说明（开发参考）

### 2.1 赛制结构

参赛队伍数量约25组，每场仿真同时运行2~4辆车。赛制按以下顺序执行：

```
排位赛（全员，共7批次，每批3~4队，各自独立计时）
    │
    ├─ 按排位成绩进行蛇形分组
    ▼
分组赛（共7场，每场3~4队，同场竞速）
    │
    ├─ 各场第1名（共7队）+ 所有第2名中排位成绩最快者（1队）= 8队晋级
    ▼
半决赛（共2场，每场4队）
    │
    ├─ 每场前2名晋级，共4队进入决赛
    ▼
决赛（1场，4队）
```

**阶段说明：**

| 阶段 | 场次数 | 每场车辆数 | 固定圈数 | 预计耗时 |
|------|--------|------------|----------|----------|
| 排位赛 | 7批 | 3~4辆 | 2圈（计时） | 约35分钟 |
| 分组赛 | 7场 | 3~4辆 | 3圈 | 约35分钟 |
| 半决赛 | 2场 | 4辆/场 | 3圈 | 约12分钟 |
| 决赛 | 1场 | 4辆 | 5圈 | 约8分钟 |
| 合计 | — | — | — | 约1.5小时（含场间 Webots 重启和代码加载时间）|

### 2.2 竞速赛制与排名规则

**赛制类型（分组赛 / 半决赛 / 决赛）：竞速赛制（固定圈数）**

各阶段规定圈数：分组赛3圈、半决赛3圈、决赛5圈。

**比赛结束条件：**
1. 本场第一辆车完成规定圈数并经过终点线时，Supervisor 启动60秒宽限计时
2. 宽限期内完成规定圈数的车辆均记录完赛时间（从比赛开始到经过终点线的仿真时间）
3. 60秒宽限期结束后，比赛正式结束

**排名规则（优先级从高到低）：**
1. 宽限期内完成规定圈数的车辆：按完赛时间升序排列
2. 宽限期结束时未完成规定圈数的车辆：按已完成圈数降序排列；圈数相同时按 `lap_progress` 降序排列；所有未完赛车辆排在已完赛车辆之后

**排位赛成绩：** 计时赛，每辆车最多完成2圈。取2圈内最快单圈时间。未完成任意一圈者记为 DNF，排在已完成队伍之后，DNF 内部按 `lap_progress` 降序排列。

### 2.3 晋级规则

- 分组赛各场第1名自动晋级半决赛（共7名）
- 剩余1个半决赛席位由所有分组赛第2名中、排位赛成绩最快者获得
- 半决赛各场前2名晋级决赛（共4名）

### 2.4 学生代码接口（强制约定，所有模块开发必须遵守）

学生提交唯一文件 `team_controller.py`，该文件必须包含以下函数，签名不可修改：

```python
import numpy as np

def control(left_img: np.ndarray,
            right_img: np.ndarray,
            timestamp: float) -> tuple[float, float]:
    """
    参数：
        left_img:  左目摄像头图像，shape=(480, 640, 3)，dtype=uint8，通道顺序 BGR
        right_img: 右目摄像头图像，shape=(480, 640, 3)，dtype=uint8，通道顺序 BGR
        timestamp: 当前仿真时间，单位秒，只读，禁止基于此参数实现帧间计时逻辑

    返回值：
        steering: float，范围 [-1.0, 1.0]，负值左转，正值右转，0.0 直行
        speed:    float，范围 [0.0, 1.0]，目标速度相对于当前允许最大速度的比例

    执行时限：每次调用必须在 20ms 内返回
    """
    steering = 0.0
    speed = 0.5
    return steering, speed
```

---

## 三、模块一：Webots 仿真（赛道 + 控制器 + 沙箱）

### 3.1 负责范围

本模块涵盖两类工作：

1. **赛道建模**：构建完整的 Webots 世界文件（`.wbt`），包含赛道几何、车辆模型、障碍物初始布置、加速包模型、光照与物理参数配置
2. **仿真控制器**：编写 Supervisor 控制器和车辆控制器框架，负责比赛裁判逻辑、学生代码加载与沙箱执行，并将仿真遥测数据写入录制文件

### 3.2 交付物

```
webots/
├── worlds/
│   └── airacer.wbt              # 主世界文件
├── protos/
│   ├── RaceCar.proto            # 赛车模型（含双目摄像头节点）
│   ├── TrafficCone.proto        # 锥桶（颜色通过参数配置，用于红色/橙色）
│   ├── Barrier.proto            # 黄黑路障
│   └── Powerup.proto            # 加速包（蓝色扁圆柱 + 旋转光环）
└── controllers/
    ├── supervisor/
    │   └── supervisor.py        # Supervisor 控制器（负责裁判逻辑 + 写入录制文件）
    └── car/
        ├── car_controller.py    # 车辆控制器框架（运行于 Webots 进程内）
        └── sandbox_runner.py    # 学生代码沙箱执行器（作为独立子进程运行）
```

### 3.3 赛道几何规格

| 参数 | 要求 |
|------|------|
| 形状 | 闭合环形，行驶方向为顺时针 |
| 参考周长 | 150~200m |
| 主赛道宽度 | 6~8m |
| 窄道区宽度 | 4~5m |
| 最小弯道内径 | 8m |
| 地面颜色 | 深灰色，RGB 约 (60, 60, 60)，使用 asphalt 材质 |

**必须包含的路段（各路段顺序由建模人员决定，需合理连接成闭合环形）：**

| 路段 | 数量 | 具体要求 |
|------|------|----------|
| 主直道 | 1段 | 长40~50m，宽8m；起终点线设于此路段 |
| 高速弯道 | 2处 | 弯道内径15m以上 |
| 发夹弯 | 1~2处 | 弯道内径8~10m |
| S型连续弯 | 1处 | 由3~4个连续反向弯道组成，每个子弯道内径不小于10m |
| 窄道区 | 1段 | 宽4~5m，长20m |

### 3.4 车道标线规范

所有标线颜色须与地面底色 RGB(60,60,60) 形成明显对比，保证在640×480摄像头图像中可通过颜色阈值方法分割。

| 标线 | 颜色 | 宽度 | 样式 |
|------|------|------|------|
| 左侧边线 | 白色 RGB(255,255,255) | 0.10m | 连续实线 |
| 中心线 | 白色 RGB(255,255,255) | 0.08m | 虚线（1m 实 / 1m 虚）|
| 右侧边线 | 黄色 RGB(255,220,0) | 0.10m | 连续实线 |
| 路肩纹 | 红白相间 | 0.5m（总宽）| 锯齿纹，每段0.25m |
| 起终点线 | 红白横纹 | 覆盖全赛道宽度 | 连续横条 |

**光照要求：**
- 使用固定方向光，光源方向与强度在世界文件中写死，不随仿真时间变化
- 所有节点关闭 `castShadows`，禁止场景中出现动态阴影

### 3.5 车辆模型规格

| 参数 | 值 |
|------|-----|
| 质量 | 1.5 kg |
| 轴距 | 0.25m |
| 最大转向角 | ±0.5 rad |
| 最大速度（无加速包） | 10 m/s |
| 0到最大速的加速时间 | 约1.5秒 |

**双目摄像头参数：**

| 参数 | 值 |
|------|-----|
| 左右摄像头数量 | 各1个，同步输出 |
| 图像分辨率 | 640 × 480 像素 |
| 水平视场角 | 60° |
| 基线距离 | 0.12m |
| 安装位置 | 车头前方0.10m，离地0.30m，光轴水平朝前，左右摄像头水平排列 |
| 输出通道顺序 | Webots 默认输出 RGB，车辆控制器框架负责转换为 BGR 后传入学生代码 |

### 3.6 障碍物规格

**静态障碍（在世界文件中预置，位置固定，Supervisor 不在运行时移动）：**

| 类型 | 颜色 RGB | 几何尺寸 | 默认布置位置 |
|------|---------|---------|------------|
| 红色锥桶 | (220, 30, 30) | 高0.30m，底部直径0.20m，圆锥形 | 发夹弯入口两侧，间隔2m排列 |
| 黄黑路障 | 主体 (255,220,0)，条纹 (0,0,0) | 高0.25m，宽0.40m，长0.20m | 窄道区两侧边界，每隔3m一个 |
| 灰色石块 | (100, 100, 100) | 不规则多边形体，外接球半径0.15~0.20m | 主直道外侧路肩，每条直道不超过2个 |

所有静态障碍的底面与赛道地面贴合，不可嵌入地面或悬空。障碍物需在摄像头图像中完整可见，不可被地形或其他物体遮挡。

**动态障碍（Supervisor 在比赛运行中生成和删除，使用 TrafficCone.proto）：**

| 参数 | 规格 |
|------|------|
| 颜色 | 橙色，RGB(255, 140, 0)，与红色静态锥桶形状相同 |
| 生成规则 | 每隔30秒，从世界文件中预标注的候选坐标集合中随机选取一个位置，生成一个动态障碍节点 |
| 删除规则 | 车辆与其发生碰撞后立即删除；10秒后重新随机生成 |
| 同时存在上限 | 3个 |
| 禁止生成位置 | 弯道顶点前后各5m范围内；起终点线前后各20m范围内；当前已有车辆位置半径3m以内 |
| 候选坐标 | 由建模人员在世界文件注释中标注不少于10个候选坐标点，供 Supervisor 读取 |

**加速包（使用 Powerup.proto）：**

| 参数 | 规格 |
|------|------|
| 几何形状 | 扁平圆柱体，半径0.20m，高0.10m，底面悬浮于地面0.05m |
| 颜色 | 亮蓝色 RGB(0, 150, 255) |
| 动态效果 | 顶部附加白色半透明圆环，每仿真步旋转5°（Powerup.proto 内部实现旋转动画）|
| 候选位置 | 建模人员预设不少于6个固定候选坐标（建议位于主直道中段及高速弯出口处）|
| 激活数量 | 每场比赛开始时由 Supervisor 从候选位置中随机选取2~3个激活 |

### 3.7 隐形检查点

赛道上设置4个隐形检查区域（以坐标范围或 `TouchSensor` 实现），供 Supervisor 判断车辆是否按顺序通过。位置要求：

| 检查点 | 建议位置 |
|--------|----------|
| CP0 | 起终点线处（计圈触发点）|
| CP1 | 主直道末端，弯道入口前 |
| CP2 | 发夹弯出口 |
| CP3 | S型弯中部 |

4个检查点需均匀分布于全圈，不可集中在半圈以内。具体坐标由建模人员根据赛道实际几何确定后，以注释方式写入世界文件，供 Supervisor 读取。

### 3.8 Supervisor 控制器

**职责列表：**
- 在比赛开始时读取 `race_config.json`，初始化参赛车辆列表、场次类型、规定圈数和录制路径
- 维护每辆车的检查点通过序列，判断是否完成有效一圈及对应圈时
- 按竞速赛制执行比赛结束逻辑（见下方"比赛结束流程"）
- 通过 `TouchSensor` 或坐标距离检测碰撞事件，按碰撞规则执行处理
- 检测车辆是否进入加速包碰撞区域，触发加速包拾取逻辑
- 按照加速包生成规则动态创建和删除加速包节点
- 按照动态障碍生成规则随机创建和删除障碍锥桶节点
- 每仿真步（64ms）将状态数据追加写入 `telemetry.jsonl`
- 仿真结束后写入 `metadata.json`

**比赛结束流程（竞速赛制）：**

```
初始状态：grace_period_started = False，grace_start_time = None

每仿真步检查：
    if 某辆车本步完成了第 total_laps 圈（检测到 lap_complete 且 lap == total_laps）:
        if not grace_period_started:
            grace_period_started = True
            grace_start_time = sim_time
            写入事件：{"type": "leader_finished", "team_id": ..., "finish_time": sim_time}

    if grace_period_started:
        if sim_time - grace_start_time >= 60.0:
            写入事件：{"type": "race_end", "sim_time": sim_time}
            停止所有车辆（Driver速度设为0），结束仿真步循环
```

排位赛（session_type = "qualifying"）不执行上述流程，每辆车完成2圈后由车辆控制器停止该车。

**录制文件写入（telemetry.jsonl）：**

每64ms追加写入一行 JSON，格式如下（每行一个完整 JSON 对象，以 `\n` 结尾）：

```
{"t":0.064,"cars":[{"team_id":"A01","x":12.4,"y":-3.1,"heading":1.57,"speed":8.3,"lap":0,"lap_progress":0.0,"status":"normal","boost_remaining":0.0}],"events":[]}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `t` | float | 当前仿真时间（秒） |
| `cars` | array | 当前所有参赛车辆的状态快照 |
| `cars[].team_id` | string | 队伍ID |
| `cars[].x`, `cars[].y` | float | 车辆在世界坐标系中的位置（米） |
| `cars[].heading` | float | 车头朝向（弧度，范围 [−π, π]） |
| `cars[].speed` | float | 当前速度（m/s） |
| `cars[].lap` | int | 已完成圈数 |
| `cars[].lap_progress` | float | 当前圈进度（0.0~1.0） |
| `cars[].status` | string | 车辆状态：`normal` / `boosted` / `penalized` / `disqualified` |
| `cars[].boost_remaining` | float | 加速包剩余效果时间（秒），未激活时为 0.0 |
| `events` | array | 本帧发生的事件列表（结构与原 IPC 事件相同）|

**仿真结束后写入 metadata.json：**

```json
{
  "session_id": "group_race_G1",
  "session_type": "group_race",
  "total_laps": 3,
  "recording_path": "D:/airacer/recordings/group_race_G1",
  "recorded_at": "2026-04-10T15:30:21",
  "duration_sim": 326.4,
  "total_frames": 5100,
  "teams": [{"team_id": "A01", "team_name": "队伍A"}],
  "finish_reason": "race_end",
  "final_rankings": [{"rank": 1, "team_id": "A01", "total_time": 321.4}]
}
```

`recording_path` 字段从 `race_config.json` 读取，`recorded_at` 为仿真结束时的系统本地时间（ISO 8601 格式），`total_frames` 为实际写入的 JSONL 行数。

**碰撞判定规则：**

| 级别 | 判定条件 | 处理操作 |
|------|----------|----------|
| 轻微碰撞 | 接触时相对速度 < 3m/s | 被碰车辆速度降至当前速度的70%，持续1秒；写入事件 `collision(severity=minor)` |
| 严重碰撞 | 接触时相对速度 ≥ 3m/s | 被碰车辆停止运动2秒；写入事件 `collision(severity=major)` |
| 判负 | 同一场次同一队伍累计3次严重碰撞 | 该车标记为 `disqualified`，停止行驶至本场结束；不计入本场排名 |

**加速包拾取规则：**
- 拾取判定：车辆中心点进入加速包圆柱碰撞框
- 拾取效果：该车允许最大速度提升至基础最大速度的130%，持续2秒
- 冷却规则：同一车辆拾取后5秒内，再次进入加速包区域不触发效果
- 加速包被拾取后立即删除节点，3秒后在另一候选位置重新创建

**计圈规则：**
- Supervisor 为每辆车维护检查点通过序列，初始状态为等待 CP0
- 车辆依次经过 CP0 → CP1 → CP2 → CP3，且每段行驶时车辆 heading 方向与赛道顺时针方向夹角小于90°
- 在满足上述条件下经过 CP0 时，`lap` 计数+1，记录本圈用时，序列重置为等待 CP1
- 跳过任意检查点或逆向通过时，当前圈序列不推进，不计圈

### 3.9 车辆控制器框架（car_controller.py）

**执行流程（每仿真步64ms）：**
1. 从 Webots Camera 节点读取左右摄像头图像，转换为 BGR uint8 numpy array
2. 将图像序列化为二进制，写入对应队伍沙箱子进程的 stdin
3. 设置20ms读超时，等待子进程 stdout 返回一行 JSON：`{"steering": x, "speed": y}`
4. 解析返回值，通过 Webots `Driver` API 施加转向和速度控制

**超时处理：**

| 情况 | 处理方式 |
|------|----------|
| 单次调用超时（超过20ms未返回） | 沿用上一帧的 steering/speed 值；记录1次警告；`timeout_warn` 事件写入下一帧 JSONL |
| 同一队伍累计3次警告 | 通知 Supervisor 执行 `lap_void`：重置该车至最近检查点，当前圈成绩取消 |
| 子进程退出（stdout关闭或退出码非零）| 记录崩溃日志；自动重新启动沙箱子进程；期间该车停止运动2秒 |
| 子进程触发非法 import（ImportError 被捕获）| 子进程终止后不重启；通知 Supervisor 将该车标记为 `disqualified` |

**比赛配置读取：**
控制器启动时读取 `race_config.json`（由后端在比赛开始前写入），获取本场参赛队伍列表及各队代码路径。

**父子进程通信协议：**
- 父进程 → 子进程（stdin）：每帧发送一个二进制消息，格式为 `[4字节小端整数：左图数据长度][左图BGR bytes][4字节小端整数：右图数据长度][右图BGR bytes][8字节double：timestamp]`
- 子进程 → 父进程（stdout）：每帧返回一行 JSON 字符串，格式为 `{"steering": float, "speed": float}\n`

**父进程启动沙箱子进程（Windows 版本）：**

```python
proc = subprocess.Popen(
    ["python", "sandbox_runner.py", "--team-id", team_id, "--code-path", code_path],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NO_WINDOW
)
```

注意：Windows 不支持 `preexec_fn`，网络和进程隔离通过沙箱内部的 import hook 实现（见3.10节）。

### 3.10 沙箱执行器（sandbox_runner.py）

作为独立子进程运行，每辆参赛车对应一个实例。在加载学生代码前，通过 Python import hook 阻断对危险模块的访问：

```python
BLOCKED_PREFIXES = frozenset([
    'os', 'sys', 'socket', 'subprocess', 'multiprocessing',
    'threading', 'time', 'datetime', 'io', 'builtins',
    'ctypes', 'winreg', 'nt', '_winapi', 'pathlib',
    'shutil', 'tempfile', 'glob', 'fnmatch',
    'requests', 'urllib', 'http', 'ftplib', 'smtplib',
    'signal', 'gc', 'inspect', 'importlib',
])

class SandboxImportHook(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        base = fullname.split('.')[0]
        if base in BLOCKED_PREFIXES:
            raise ImportError(f"[Sandbox] Module '{fullname}' is not allowed.")
        return None

sys.meta_path.insert(0, SandboxImportHook())
```

沙箱执行器在安装 hook 后，再执行 `importlib.util.spec_from_file_location` 加载学生代码文件。父进程若检测到子进程因 ImportError 退出，记录违规日志并通知 Supervisor 将该车标记为 `disqualified`，不重启子进程。

**注意：** Windows 下无法使用 `resource.setrlimit`，内存和进程数量限制依赖 Windows Job Object（可选实现，P2优先级）。当前版本以 import hook 作为核心安全机制。

### 3.11 验收标准

**赛道建模部分：**
- [ ] Webots 可正常加载 `airacer.wbt`，物理仿真启动后无崩溃或异常
- [ ] 车辆可通过 Webots `Driver` API 接受转向角和速度指令并产生对应物理运动
- [ ] 双目摄像头节点可正常输出图像数据，可转换为 numpy ndarray
- [ ] 在640×480分辨率摄像头图像中，所有车道标线和障碍物可见、无遮挡、无极端光照导致的不可见区域
- [ ] 4个检查点坐标覆盖全圈，相邻检查点之间无法通过倒车绕过
- [ ] 所有 Proto 节点可由 Supervisor 在运行时动态创建和删除，不引发世界文件状态损坏
- [ ] 世界文件中包含不少于10个动态障碍候选坐标注释和不少于6个加速包候选坐标注释

**控制器与沙箱部分：**
- [ ] Supervisor 计圈判定正确：检查点必须按序通过；倒车/跳过检查点时当前圈不计入
- [ ] 碰撞三级判定规则全部可触发，处理动作与规则一致
- [ ] 加速包拾取后效果持续2秒，5秒冷却期内对同一车辆不再触发
- [ ] 动态障碍每30秒生成一次，同时存在数量不超过3个，生成位置不违反禁止区域规则
- [ ] Supervisor 每64ms写入一行 telemetry JSONL，仿真结束后 metadata.json 写入完整
- [ ] 车辆控制器单次调用超时（20ms）行为正确：沿用上帧值，记录警告
- [ ] 累计3次警告后触发 `lap_void`，车辆正确重置
- [ ] 沙箱子进程尝试 `import os` 等被阻断模块时抛出 ImportError，子进程被标记为违规
- [ ] 子进程崩溃后车辆控制器自动重启，不影响同场其他车辆的正常运行

---

## 四、模块二：赛事管理后端

### 4.1 负责范围

后端服务的全部功能：读取和提供录制文件、维护比赛状态机、管理 Webots 进程生命周期、提供代码提交的 HTTP API、测试队列管理，以及向前端推送仿真状态。

### 4.2 交付物

```
server/
├── main.py                      # FastAPI 应用入口，挂载所有路由
├── race/
│   ├── state_machine.py         # 比赛状态机
│   ├── session.py               # Webots 进程启动/停止管理
│   └── scoring.py               # 比赛排名计算（从录制文件提取）
├── api/
│   ├── submission.py            # 代码提交与即时检查 API
│   ├── testqueue.py             # 测试队列管理 API
│   ├── admin.py                 # 助教控制 API（需密码验证）
│   └── recording.py             # 录制文件服务 API（新增）
├── ws/
│   └── admin.py                 # Admin WebSocket（推送仿真状态）
└── db/
    └── models.py                # SQLite 数据模型
```

### 4.3 录制文件服务 API

**录制文件 REST API（无需鉴权，前端回放播放器使用）：**

```
GET /api/recordings/{session_id}/metadata
  → 返回该场次的 metadata.json 内容（JSON 格式）

GET /api/recordings/{session_id}/telemetry
  → 流式返回 telemetry.jsonl 文件内容
  → Content-Type: application/x-ndjson
  → 逐行返回，每行为一个 JSON 对象

GET /api/recordings
  → 返回所有已完成录制的摘要列表
  → 每条摘要包含：session_id, session_type, recorded_at, duration_sim, total_frames, teams, final_rankings
```

**说明：**
- `GET /api/recordings/{session_id}/telemetry` 使用 StreamingResponse 返回，前端逐行解析 JSONL 构建帧数组
- 录制文件存储路径来自 `race_config.json` 中的 `recording_path` 字段
- 若录制文件尚未生成（仿真未结束），返回 HTTP 404

### 4.4 REST API 接口定义

**代码提交接口（学生使用，队伍ID + 密码鉴权）：**

```
POST /api/submit
Content-Type: application/json
Body:
{
  "team_id": "A01",
  "password": "xxxx",
  "code": "<文件内容的 base64 编码字符串>"
}

Response 200（通过检查，已入队）：
{
  "status": "queued",
  "version": "20260410_153021",
  "queue_position": 3
}

Response 400（检查失败）：
{
  "status": "error",
  "stage": "syntax_check",
  "detail": "SyntaxError at line 14: invalid syntax"
}

Response 403（提交已锁定）：
{
  "status": "locked",
  "detail": "Submission is closed. Race is about to begin."
}
```

**测试状态查询（学生使用）：**

```
GET /api/test-status/{team_id}
Headers: Authorization: Basic base64(team_id:password)

Response 200：
{
  "team_id": "A01",
  "latest_version": "20260410_153021",
  "queue_status": "waiting",      // waiting | running | done | no_submission
  "queue_position": 2,            // 当前在队列中的位置，running时为0
  "report": {                     // 仅 done 状态时有此字段
    "laps_completed": 2,
    "best_lap_time": 43.21,
    "collisions_minor": 1,
    "collisions_major": 0,
    "timeout_warnings": 0,
    "finish_reason": "completed"  // completed | timeout | crashed | disqualified
  }
}
```

**公开数据接口（无需鉴权）：**

```
GET /api/teams
  → 返回队伍列表（team_id, team_name），不含密码等敏感信息

GET /api/results
  → 返回所有已完成场次的结果

GET /api/schedule
  → 返回赛程安排
```

### 4.5 助教 REST API

**助教控制接口（需密码验证）：**

```
POST /api/admin/lock-submissions
  → 锁定所有队伍的代码提交入口，操作不可逆

POST /api/admin/set-session
Body: {"session_type": "qualifying|group_race|semi|final", "session_id": "G1", "team_ids": [...], "total_laps": 3}
  → 配置下一场比赛的参数，写入 race_config.json（含 recording_path 字段）

POST /api/admin/start-race
  → 启动 Webots 进程，开始当前场比赛

POST /api/admin/stop-race
  → 强制终止 Webots 进程，将当前场标记为 aborted

POST /api/admin/reset-track
  → 终止当前 Webots 进程（如有），清空 race_config.json，状态机回退至 IDLE

GET  /api/admin/standings
  → 返回所有队伍的当前场次积分和排位赛成绩

GET  /api/admin/schedule
  → 返回当前赛程安排（分组赛对阵表）

POST /api/admin/override-schedule
Body: {"group_id": "G1", "team_ids": ["A01", "C03", "B05", "D02"]}
  → 助教手动修改某场分组赛的参赛队伍
```

### 4.6 Admin WebSocket

**监听地址：** `ws://0.0.0.0:8000/ws/admin`

此 WebSocket 仅供助教控制台使用，推送仿真运行状态（不推送逐帧遥测数据，遥测数据通过录制文件服务获取）。

**推送数据格式：**

```json
{
  "type": "sim_status",
  "state": "running",
  "session_id": "group_race_G1",
  "webots_pid": 12345,
  "sim_time_approx": 45.3,
  "recording_path": "D:/airacer/recordings/group_race_G1"
}
```

**`state` 字段取值：**

| 取值 | 含义 |
|------|------|
| `"idle"` | 无仿真运行，等待助教配置 |
| `"running"` | Webots 进程运行中，仿真进行中 |
| `"recording_ready"` | 仿真已结束，录制文件写入完成，可供回放 |
| `"aborted"` | 仿真被强制终止，录制文件可能不完整 |

**推送触发机制：**
- 状态变化时立即推送（如从 `running` 变为 `recording_ready`）
- 仿真运行中每10秒推送一次心跳（更新 `sim_time_approx`）

### 4.7 比赛状态机

状态机维护全局赛事状态，所有转换须由助教通过管理 API 主动触发，**禁止自动跳转**（唯一例外：Webots 进程自然结束时，状态从 `*_RUNNING` 自动转为 `*_FINISHED`）。

```
IDLE
  │ POST /api/admin/set-session + start-race
  ▼
QUALIFYING_RUNNING   ── Webots 运行中，写入录制文件
  │ Webots 进程退出 或 stop-race
  ▼
QUALIFYING_FINISHED  ── 本批成绩从录制文件提取后写入数据库
  │ 所有批次完成后：POST /api/admin/finalize-qualifying
  ▼
QUALIFYING_DONE      ── 排位成绩排序完毕，分组赛对阵计算完毕
  │ set-session(group_race) + start-race（共循环7次）
  ▼
GROUP_RACE_RUNNING → GROUP_RACE_FINISHED（每场结束后写入场次结果）
  │ 7场全部完成后：POST /api/admin/finalize-group
  ▼
GROUP_DONE           ── 8强名单确定
  │ set-session(semi) + start-race（共循环2次）
  ▼
SEMI_RUNNING → SEMI_FINISHED
  │ 2场全部完成后：POST /api/admin/finalize-semi
  ▼
SEMI_DONE            ── 4强名单确定
  │ set-session(final) + start-race
  ▼
FINAL_RUNNING → FINAL_FINISHED
  │ POST /api/admin/close-event
  ▼
CLOSED               ── 所有结果已持久化，前端展示最终排名
```

**状态机约束：**
- 非合法顺序的状态跳转请求（如从 `QUALIFYING_RUNNING` 直接跳至 `FINAL_RUNNING`）返回 HTTP 400
- 所有 `*_RUNNING` 状态下，测试队列暂停消费，比赛结束后自动恢复

### 4.8 测试队列

- 代码通过提交检查后自动加入 FIFO 队列尾部
- 队列为单线程串行消费，同一时刻最多运行一个测试 Webots 实例
- 若同一队伍在队列中已存在一条**尚未开始执行**的任务，新提交入队时替换旧任务
- 若旧任务已开始执行，则不中断，新提交作为新条目追加到队列尾部
- 所有 `*_RUNNING` 状态下暂停消费，`*_FINISHED` 或 `IDLE` 状态下自动恢复
- 每次测试：启动单车 Webots 实例 → 运行至完成2圈或超过5分钟 → 关闭 Webots → 读取录制文件提取测试指标 → 写入测试报告
- 测试报告字段：`laps_completed`, `best_lap_time`, `collisions_minor`, `collisions_major`, `timeout_warnings`, `finish_reason`
- 测试报告仅该队伍通过鉴权访问，其他队伍无法查询

### 4.9 数据库表结构

```sql
-- 队伍信息
teams (
    id          TEXT PRIMARY KEY,  -- 队伍ID，如 "A01"
    name        TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL
)

-- 代码提交版本
submissions (
    id          TEXT PRIMARY KEY,  -- 时间戳字符串，如 "20260410_153021"
    team_id     TEXT NOT NULL,
    code_path   TEXT NOT NULL,     -- 文件系统存储路径（Windows 正斜杠）
    submitted_at TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1  -- 0: 被后续版本替代; 1: 当前有效版本
)

-- 测试记录
test_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id   TEXT NOT NULL,
    status          TEXT NOT NULL,    -- queued | running | done | skipped
    queued_at       TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    laps_completed  INTEGER,
    best_lap_time   REAL,
    collisions_minor INTEGER,
    collisions_major INTEGER,
    timeout_warnings INTEGER,
    finish_reason   TEXT              -- completed | timeout | crashed | disqualified
)

-- 比赛场次记录
race_sessions (
    id          TEXT PRIMARY KEY,     -- 如 "qualifying_batch_3"、"group_race_G2"
    type        TEXT NOT NULL,        -- qualifying | group_race | semi | final
    team_ids    TEXT NOT NULL,        -- JSON 数组字符串
    total_laps  INTEGER NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    phase       TEXT NOT NULL,        -- waiting | running | finished | aborted
    result      TEXT                  -- JSON 对象，比赛结束后写入
)

-- 分组赛场内积分（用于确定晋级名单）
race_points (
    team_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    rank        INTEGER,
    points      INTEGER,
    PRIMARY KEY (team_id, session_id)
)
```

### 4.10 验收标准

- [ ] `GET /api/recordings/{session_id}/metadata` 正确返回对应 metadata.json 内容
- [ ] `GET /api/recordings/{session_id}/telemetry` 以 NDJSON 格式流式返回 telemetry.jsonl，Content-Type 为 `application/x-ndjson`
- [ ] `GET /api/recordings` 返回所有已完成录制的摘要列表，字段完整
- [ ] 所有其他 REST API 端点返回正确的 HTTP 状态码和响应体，包含必要的错误信息
- [ ] Admin WebSocket 在仿真状态变化时立即推送，`state` 字段值与实际仿真状态一致
- [ ] 状态机拒绝非法顺序跳转，返回 HTTP 400 并描述当前状态
- [ ] 测试队列在比赛期间暂停，比赛结束后自动恢复消费，队列顺序严格遵循 FIFO（含替换规则）
- [ ] 测试结束后从录制文件正确提取测试指标并写入测试报告
- [ ] 代码锁定后提交接口返回 HTTP 403
- [ ] Webots 进程意外退出时，后端检测到退出码，将当前场次标记为 `aborted`，Admin WebSocket 推送 state=aborted
- [ ] 数据库在后端重启后仍保留所有历史比赛结果（使用 SQLite 持久化，非内存数据库）

---

## 五、模块三：前端与回放播放器

### 5.1 负责范围

三个独立页面，使用原生 HTML + CSS + JavaScript 实现，不依赖任何前端框架。所有动态数据通过 WebSocket 或 HTTP 请求从后端获取。核心功能为录制回放播放器，替代原实时串流展示。

### 5.2 交付物

```
frontend/
├── race/
│   ├── index.html               # 回放播放页（加载录制文件并播放）
│   ├── replay.js                # 回放控制逻辑（帧管理、时间轴、速度控制）
│   ├── minimap.js               # 2D 小地图渲染模块（Canvas 2D）
│   └── leaderboard.js           # 排行榜与事件提示模块
├── submit/
│   └── index.html               # 学生代码提交页
└── admin/
    └── index.html               # 助教控制台
```

### 5.3 回放播放页（/race/）

该页面用于在比赛结束后播放录制文件，可通过 URL 参数指定 session_id。

**核心流程：**
1. 读取 URL 参数获取 `session_id`（如 `?session=group_race_G1`）
2. `GET /api/recordings/{session_id}/metadata` → 获取队伍信息、总时长、最终排名
3. `GET /api/recordings/{session_id}/telemetry` → 逐行解析 JSONL，构建帧数组（每行一帧）
4. 数据加载完成后展示时间轴 slider，允许跳转到任意时刻
5. 播放时使用 `requestAnimationFrame`，根据播放速度和经过的实际时间计算目标帧，通过二分查找 `t` 字段定位对应帧
6. 渲染 Canvas 2D 小地图：赛道轮廓为静态背景层，车辆位置/heading 为动态层
7. 同步更新排行榜和事件提示

**播放控制：**
- 播放/暂停按钮
- 时间轴 slider（拖动可跳转到指定时刻）
- 播放速度选择：1×、2×、4×
- 当前时间显示（格式：`MM:SS.ss`）/ 总时长显示

**2D 小地图实现要求：**
- 赛道轮廓为预绘制的固定背景（SVG 或 Canvas 静态层），根据 Webots 世界文件的赛道几何绘制
- 车辆位置来自当前帧数据的 `(x, y, heading)` 字段，每帧渲染时更新
- 每辆车以不同颜色实心圆点表示，附带指向 heading 方向的箭头指示行驶方向
- 当前帧中的 `events` 字段若包含障碍物生成/删除事件，更新小地图上的障碍物显示

**布局结构（参考）：**

```
┌───────────────────────────────────────────────────────────┐
│  场次名称: 分组赛 第1场      播放时间: 02:34.51  [REPLAY]  │  ← 顶部信息栏
├────────────────────────────┬──────────────────────────────┤
│                            │  排名  队伍名   圈数  用时    │
│  2D 小地图（Canvas）        │   1    队伍A     3   321.4s  │
│  赛道轮廓 + 车辆位置         │   2    队伍C     3   328.7s  │
│  + 障碍物位置               │   3    队伍B     2   186.3s  │
│                            │                              │
├────────────────────────────┴──────────────────────────────┤
│  ▶  [==================●==============]  02:34 / 05:26  │  ← 时间轴
│     1×  2×  4×                                           │
├───────────────────────────────────────────────────────────┤
│  事件记录（当前时刻附近事件）                                │
│  [02:34] 队伍A 完成第3圈，圈速 107.3s                      │
└───────────────────────────────────────────────────────────┘
```

**最终排名展示：**
回放结束（时间轴到达末尾）后，自动展示来自 `metadata.json` 的 `final_rankings` 数据。

### 5.4 代码提交页（/submit/index.html）

**功能列表：**
1. 队伍 ID + 密码登录（局部状态，不需要 session/cookie，页面刷新后重新输入）
2. 代码文件上传：支持文件拖拽和点击选择，限制文件类型为 `.py`，文件大小限制 1MB
3. 提交后即时展示后端返回的检查结果：
   - 通过：显示版本号、当前队列位置
   - 失败：显示失败阶段（语法检查/接口检查）和具体错误信息（包含行号）
4. 轮询 `/api/test-status/{team_id}`（每5秒一次），展示当前测试状态：
   - 等待中：显示队列位置和预计等待条目数
   - 运行中：显示"测试进行中"
   - 已完成：展示测试报告（完成圈数、最快圈时、碰撞次数、超时警告次数、结束原因）
5. 历史提交记录列表：展示本队所有历史版本的提交时间、版本号和对应的测试结果摘要
6. 代码提交入口锁定后：文件上传控件和提交按钮变为不可交互状态，并显示说明文字

**数据访问范围约束：**
- 提交页不展示其他队伍的任何信息（代码、测试结果、队伍名称等）
- 鉴权失败时，所有接口返回 HTTP 401，页面仅显示登录表单

### 5.5 助教控制台（/admin/index.html）

通过页面内密码输入框鉴权，密码正确后显示控制台内容，密码通过 HTTP Basic Auth 方式传递给后端 `/api/admin/*` 接口。

**功能列表：**
1. 所有队伍代码提交状态总览：队伍ID、队伍名、最新提交时间、是否已有通过检查的版本
2. **锁定提交**按钮：点击时显示二次确认对话框，确认后调用 `/api/admin/lock-submissions`；锁定后按钮变灰并显示"已锁定"
3. 比赛场次配置：
   - 下拉选择场次类型（排位赛批次N / 分组赛场次X / 半决赛N / 决赛）
   - 根据蛇形分组算法自动填充参赛队伍列表，可手动修改
   - 设置本场总圈数
   - 确认后调用 `set-session`，页面显示当前配置内容
4. **开始比赛** / **停止比赛** / **重置赛道** 按钮，每个操作前显示二次确认对话框
5. 仿真录制状态监控：连接 Admin WebSocket，实时显示当前仿真状态（`state` 字段）、录制文件路径和预估仿真时间；`recording_ready` 时显示"录制完成，可回放"并附带回放链接
6. 实时积分总表：展示所有队伍的排位赛成绩、各场分组赛积分、当前总积分，按总积分降序排列
7. 测试队列视图：显示当前队列中的所有条目（队伍ID、提交版本、排队时间）及正在执行的测试；支持手动移除某条队列条目

### 5.6 验收标准

- [ ] 回放播放页可通过 URL 参数加载指定场次的录制文件，数据加载完成后时间轴可正常操作
- [ ] 播放/暂停、速度切换（1×/2×/4×）、时间轴拖动均正常工作，帧渲染无明显跳帧或卡顿
- [ ] 小地图车辆位置与帧数据中的 `(x, y, heading)` 一致，不同队伍颜色区分明确
- [ ] 回放结束时正确展示 `metadata.json` 中的最终排名
- [ ] 助教控制台的 Admin WebSocket 状态监控正常更新，`recording_ready` 时正确显示回放链接
- [ ] 代码提交页：上传 `.py` 文件后，在2秒内展示后端返回的检查结果
- [ ] 代码提交页：测试报告数据正确展示（与后端 `/api/test-status` 返回一致）
- [ ] 代码提交页：锁定后提交控件不可用，历史记录仍可查看
- [ ] 助教控制台：所有操作均有二次确认步骤，且操作完成后有明确的成功/失败反馈
- [ ] 三个页面在 Chrome、Firefox、Edge 最新稳定版本均可正常使用

---

## 六、模块间接口约定

各模块独立开发时必须遵守以下接口规范，以保证集成时不需要修改对接代码。

### 接口①：录制文件（模块一输出 → 模块二读取）

模块一（Supervisor）在仿真过程中向 `recording_path` 目录持续写入 `telemetry.jsonl`，仿真结束后写入 `metadata.json`。模块二通过文件系统直接读取，通过 REST API 提供给模块三。

**文件结构：**
```
D:/airacer/recordings/{session_id}/
├── telemetry.jsonl    # 仿真过程逐帧写入，每行一个 JSON 对象
└── metadata.json      # 仿真结束后一次性写入
```

**telemetry.jsonl 单行格式：**
```
{"t":0.064,"cars":[{"team_id":"A01","x":12.4,"y":-3.1,"heading":1.57,"speed":8.3,"lap":0,"lap_progress":0.0,"status":"normal","boost_remaining":0.0}],"events":[]}
```

**metadata.json 格式：**
```json
{
  "session_id": "group_race_G1",
  "session_type": "group_race",
  "total_laps": 3,
  "recording_path": "D:/airacer/recordings/group_race_G1",
  "recorded_at": "2026-04-10T15:30:21",
  "duration_sim": 326.4,
  "total_frames": 5100,
  "teams": [{"team_id": "A01", "team_name": "队伍A"}],
  "finish_reason": "race_end",
  "final_rankings": [{"rank": 1, "team_id": "A01", "total_time": 321.4}]
}
```

### 接口②：录制文件服务 API（模块二 → 模块三）

```
GET /api/recordings/{session_id}/metadata   → 返回 metadata.json 内容
GET /api/recordings/{session_id}/telemetry  → 流式返回 telemetry.jsonl（Content-Type: application/x-ndjson）
GET /api/recordings                         → 返回所有已完成录制的摘要列表
```

详细字段定义见第四章 4.3 节。

### 接口③：Admin WebSocket（模块二 → 模块三）

- 监听地址：`ws://0.0.0.0:8000/ws/admin`
- 推送格式：

```json
{
  "type": "sim_status",
  "state": "running",
  "session_id": "group_race_G1",
  "webots_pid": 12345,
  "sim_time_approx": 45.3,
  "recording_path": "D:/airacer/recordings/group_race_G1"
}
```

`state` 取值：`idle` / `running` / `recording_ready` / `aborted`

### 接口④：后端启动 Webots（模块二 → 模块一，Windows 命令）

后端通过 `subprocess.Popen` 在 Windows 11 上启动 Webots 进程：

```python
# 正式比赛
proc = subprocess.Popen(
    [
        r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe",
        # 注意：实际路径取决于 Webots 安装位置，建议通过环境变量或配置文件指定
        "D:/airacer/webots/worlds/airacer.wbt"
    ],
    creationflags=subprocess.CREATE_NO_WINDOW
)

# 单车测试（不需要 3D 串流时，关闭渲染以节省资源）
proc = subprocess.Popen(
    [
        r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe",
        "--minimize",
        "--no-rendering",
        "D:/airacer/webots/worlds/airacer.wbt"
    ],
    creationflags=subprocess.CREATE_NO_WINDOW
)
```

后端须监控 Webots 子进程状态，进程退出时记录退出码并触发状态机转换。

### 接口⑤：比赛配置文件 race_config.json（模块二 → 模块一）

每场比赛开始（`start-race`）前，后端写入以下配置文件，Supervisor 和车辆控制器在启动时读取。v1.4 新增 `recording_path` 字段（去除原 `ipc_port` 字段）：

```json
{
  "session_id": "group_race_G3",
  "session_type": "group_race",
  "total_laps": 3,
  "recording_path": "D:/airacer/recordings/group_race_G3",
  "cars": [
    {
      "car_node_id": "car_1",
      "team_id": "A01",
      "team_name": "队伍A",
      "code_path": "D:/airacer/submissions/A01/20260410_153021/team_controller.py",
      "start_position": 1
    }
  ]
}
```

配置文件路径固定为 `D:/airacer/race_config.json`（或由环境变量 `AIRACER_CONFIG_PATH` 指定），路径分隔符使用正斜杠（Python Windows 兼容）。

---

## 七、开发优先级与注意事项

### 7.1 开发优先级

**P0（平台基本可运行所必须完成的功能）：**

- 赛道建模 + 车辆模型（模块一）
- Supervisor 计圈逻辑 + 录制文件写入（模块一）
- 车辆控制器框架 + 沙箱子进程（模块一）
- 后端录制文件服务 API（模块二）
- 后端状态机基础流转（非全部状态，能运行单场即可）（模块二）
- 前端回放播放页（小地图 + 排行榜 + 时间轴）（模块三）

**P1（比赛完整流程所需功能）：**
- 动态障碍 + 加速包生成逻辑（模块一）
- 后端完整状态机（所有阶段）（模块二）
- 代码提交 API + 即时检查（模块二）
- 测试队列系统（模块二）
- 学生代码提交页（模块三）
- 助教控制台（含录制状态监控）（模块三）

**P2（可选功能，在 P1 完成后有余力时实现）：**
- Windows Job Object 实现沙箱内存限制
- 比赛全程数据导出（JSON 格式）
- 助教控制台中的积分历史图表展示

### 7.2 端到端联调建议

建议各模块完成 P0 后，尽早按以下顺序进行端到端联调：

1. **模块一联调**：使用官方模板代码（附录A）作为测试输入，跑一圈，确认 `telemetry.jsonl` 和 `metadata.json` 正确生成，文件格式符合接口①规范
2. **模块二联调**：启动后端服务，调用 `GET /api/recordings/{session_id}/telemetry`，确认可正确流式返回步骤1生成的 JSONL 文件，每行格式合法
3. **模块三联调**：打开回放播放页，加载步骤1生成的录制，确认时间轴正常、小地图车辆位置与 JSONL 数据一致、排行榜与 metadata.json 最终排名一致
4. **完整联调**：助教控制台触发比赛开始 → Webots 仿真运行 → 录制文件生成 → Admin WebSocket 推送 `recording_ready` → 控制台显示回放链接 → 回放播放页正常播放

### 7.3 已知注意事项（Windows 版本）

- Webots 的 Camera 节点默认输出 RGB 通道顺序，车辆控制器框架在传入学生代码前须转换为 BGR（`image = image[:, :, ::-1]` 或 `cv2.cvtColor`）
- Webots 仿真时间与墙钟时间不严格一致，`t` 字段（仿真时间）可能快于或慢于真实时间，前端回放播放器须使用帧的 `t` 字段驱动时间轴，不可使用 JavaScript 的 `Date.now()`
- Windows 下不支持 `resource.setrlimit` 和 `preexec_fn`，沙箱以 import hook 为核心安全机制，`CREATE_NO_WINDOW` flag 防止子进程弹出控制台窗口
- Webots 可执行文件路径取决于安装位置，建议通过环境变量 `WEBOTS_PATH` 或配置文件指定，不要在代码中硬编码
- 录制文件路径使用正斜杠（`D:/airacer/recordings/...`），Python 在 Windows 下可正确识别，避免反斜杠转义问题
- WebSocket 端点存在跨域场景，后端须在 FastAPI 中正确配置 CORS（允许局域网内任意来源）
- 沙箱子进程中需预装 `numpy` 和 `opencv-python`，联调前须确认 Python 环境中已安装
- `telemetry.jsonl` 在仿真过程中持续追加写入，后端读取时需等待 `metadata.json` 生成后再提供回放服务，以确保文件写入已完成

---

## 附录A：学生官方模板代码

```python
# team_controller.py
# 只需提交本文件，不要修改 control() 的函数签名

import numpy as np

def control(left_img: np.ndarray,
            right_img: np.ndarray,
            timestamp: float) -> tuple[float, float]:
    """
    参数：
        left_img:  左目图像，shape=(480, 640, 3)，dtype=uint8，BGR 通道顺序
        right_img: 右目图像，shape=(480, 640, 3)，dtype=uint8，BGR 通道顺序
        timestamp: 仿真时间（秒），只读

    返回值：
        steering: float，范围 [-1.0, 1.0]，负值左转，正值右转
        speed:    float，范围 [0.0, 1.0]，0.0 停止，1.0 最大速度

    每次调用时限：20ms
    """

    # 在此实现视觉控制算法

    steering = 0.0
    speed = 0.5

    return steering, speed
```

**运行环境预装库（可直接 import）：**
`numpy`, `cv2`（OpenCV）, `math`, `collections`, `heapq`, `functools`, `itertools`

**禁止 import 的模块：**
`os`, `sys`, `socket`, `subprocess`, `threading`, `multiprocessing`, `time`, `datetime`，及所有网络请求相关库（`requests`, `urllib`, `http` 等）

---

## 附录B：参考文档

- 完整架构设计文档：`docs/airacer-architecture.md`（第三节为接口数据字典，定义所有枚举值、录制文件格式和计算字段）
- Webots 官方参考手册：https://cyberbotics.com/doc/reference/index
- Webots Python API 文档：https://cyberbotics.com/doc/reference/python-api
- Webots Web Streaming 文档：https://cyberbotics.com/doc/guide/web-simulation

---
