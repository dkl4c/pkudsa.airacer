# AI Racer — PKU DSA-B 赛车大赛平台

> 2026 年春季《数据结构与算法 B》大作业  
> 学生提交纯视觉自动驾驶代码，在 Webots 仿真中进行多车实时比赛。

---

## 目录

1. [项目简介](#项目简介)
2. [系统架构](#系统架构)
3. [快速开始](#快速开始)
4. [目录结构](#目录结构)
5. [比赛规则](#比赛规则)
6. [API 参考](#api-参考)
7. [学生提交指南](#学生提交指南)
8. [开发说明](#开发说明)

---

## 项目简介

AI Racer 将仿真赛车与算法竞赛结合：每队提交一个 Python 函数 `control(left_img, right_img, t)`，在 20 ms 内根据左右双目摄像头图像返回转向和油门，驱动赛车在 Webots 赛道上与其他队伍同场竞技。

**平台功能：**

- 代码在线提交 + 自动语法/接口检查
- 沙盒隔离执行（`subprocess` + 导入黑名单）
- 比赛过程全量遥测录制（JSONL）
- 网页端实时回放 + 小地图 + 排行榜
- 管理员后台一键控制赛事流程

---

## 系统架构

```
┌─────────────────────────────────────────────────┐
│                    浏览器                        │
│  /submit/  /race/ (回放)  /admin/ (管理后台)     │
└────────────┬────────────────────────┬────────────┘
             │ HTTP / WebSocket        │
┌────────────▼────────────────────────▼────────────┐
│               FastAPI 后端  :8000                 │
│  POST /api/submit           GET /api/recordings  │
│  GET  /api/test-status      POST /api/admin/*    │
│  WS   /ws/admin                                  │
│                                                  │
│  SQLite DB  ──  race/state_machine.py            │
│  race/session.py  ──  race/scoring.py            │
└────────────────────┬─────────────────────────────┘
                     │ subprocess (Webots)
┌────────────────────▼─────────────────────────────┐
│                  Webots R2023b                    │
│                                                  │
│  supervisor.py          car_controller.py (×4)   │
│  · 检查点检测            · 摄像头采集              │
│  · 碰撞判定              · sandbox_runner.py      │
│  · 遥测写入               · 学生代码 (隔离子进程)  │
│  · metadata.json         · 转向/油门输出          │
└──────────────────────────────────────────────────┘
         ↕ 文件系统
  recordings/{session_id}/
    ├── telemetry.jsonl   (每帧 64 ms，前端回放用)
    └── metadata.json     (排名、成绩、原因)
```

---

## 快速开始

### 环境要求

| 组件 | 版本 |
|------|------|
| Windows | 10/11（Webots 在 Windows 上运行） |
| [Webots](https://cyberbotics.com/) | R2023b |
| conda/mamba | 任意 |
| Python | 3.11（见下方） |

### 1. 克隆仓库

```bash
git clone <repo-url> pkudsa.airacer
cd pkudsa.airacer
```

### 2. 创建 Python 环境

```bash
conda create -n airacer python=3.11 -y
conda activate airacer
pip install fastapi "uvicorn[standard]" passlib bcrypt numpy opencv-python pydantic
```

### 3. 配置后端

```bash
cp server/config.example.py server/config.py
# 用编辑器修改 config.py：
#   WEBOTS_BINARY  → Webots 安装路径
#   AIRACER_ROOT   → 本仓库根目录的绝对路径
#   ADMIN_PASSWORD → 自定义管理员密码
```

### 4. 初始化数据库 & 注册队伍

```bash
cd server
conda run -n airacer python - <<'EOF'
from config import DB_PATH
from db.models import init_db
import pathlib, sqlite3
from passlib.context import CryptContext

pathlib.Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
init_db(DB_PATH)

ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
teams = [
    ("team_01", "Team Alpha",  "password01"),
    ("team_02", "Team Beta",   "password02"),
    # ... 按需添加
]
with sqlite3.connect(DB_PATH) as conn:
    for tid, name, pwd in teams:
        conn.execute(
            "INSERT OR IGNORE INTO teams (id, name, password_hash) VALUES (?,?,?)",
            (tid, name, ctx.hash(pwd))
        )
print("Done.")
EOF
```

### 5. 启动服务器

```bash
cd server
conda run -n airacer uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

浏览器打开 `http://localhost:8000` 即可看到导航首页。

### 6. 配置 Webots

在 Webots 中打开 `webots/worlds/airacer.wbt`，确认：

- 四辆赛车节点 DEF 名分别为 `car_1` … `car_4`
- Supervisor 控制器指向 `webots/controllers/supervisor/`
- 每辆车控制器指向 `webots/controllers/car/`
- 系统环境变量 `RACE_CONFIG_PATH` 会由后端在启动 Webots 时自动注入

---

## 目录结构

```
pkudsa.airacer/
├── server/                    # FastAPI 后端
│   ├── main.py                # 应用入口：CORS、路由、心跳
│   ├── config.example.py      # 配置模板（复制为 config.py）
│   ├── api/
│   │   ├── submission.py      # 提交 & 测试状态 API
│   │   ├── admin.py           # 赛事管理 API（Basic Auth）
│   │   └── recording.py       # 回放数据 API
│   ├── db/models.py           # SQLite schema，无 ORM
│   ├── race/
│   │   ├── state_machine.py   # 线程安全赛事状态机
│   │   ├── session.py         # Webots 进程生命周期
│   │   └── scoring.py         # 从录制文件提取成绩
│   └── ws/admin.py            # WebSocket 实时状态推送
│
├── webots/
│   ├── worlds/airacer.wbt     # 赛道世界文件
│   ├── protos/                # 自定义 Proto（待补充）
│   └── controllers/
│       ├── supervisor/        # 裁判控制器
│       └── car/               # 车辆控制器 + 沙盒
│
├── frontend/
│   ├── index.html             # 导航首页
│   ├── submit/                # 代码提交页
│   ├── race/                  # 回放播放器
│   └── admin/                 # 赛事管理后台
│
├── template/
│   └── team_controller.py     # 学生代码模板
│
├── docs/                      # 详细设计文档
│   ├── airacer-architecture.md
│   └── airacer-task-book.md
│
├── recordings/                # 比赛录制输出（自动创建）
└── submissions/               # 学生代码存储（自动创建）
```

---

## 比赛规则

### 赛程

| 阶段 | 场次 | 每场车数 | 圈数 | 目的 |
|------|------|----------|------|------|
| 资格赛 | 7 | 3–4 | 2 | 确定排名，按成绩蛇形分组 |
| 小组赛 | 7 | 3–4 | 3 | 积分，前两名晋级 |
| 半决赛 | 2 | 4 | 3 | 各组冠军 + 最佳第二 |
| 决赛 | 1 | 4 | 5 | 最终排名 |

### 学生代码接口

```python
# template/team_controller.py
import numpy as np

def control(left_img: np.ndarray,   # shape (480, 640, 3) BGR uint8
            right_img: np.ndarray,  # shape (480, 640, 3) BGR uint8
            timestamp: float        # 仿真时间 (秒)
           ) -> tuple[float, float]:
    """
    返回 (steering, speed)
      steering: [-1.0, 1.0]   左负右正
      speed:    [ 0.0, 1.0]   0=停 1=最大速度
    必须在 20 ms 内返回，超时使用上一帧结果。
    """
    return 0.0, 0.5
```

### 可用库

`numpy`、`cv2`（opencv）、`math`、`collections`、`heapq`、`functools`、`itertools`

文件系统、网络、`os`、`sys` 等均被沙盒拦截，违规 import 导致直接取消资格。

### 积分规则

| 名次 | 积分 |
|------|------|
| 1 | 10 |
| 2 | 7 |
| 3 | 5 |
| 4 | 3 |
| 其余 | 1 |

---

## API 参考

### 公开接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/submit` | 提交代码（JSON body，见下） |
| `GET` | `/api/teams` | 获取所有队伍列表 |
| `GET` | `/api/test-status/{team_id}` | 查询测试状态（Basic Auth） |
| `GET` | `/api/recordings` | 列出所有录制场次 |
| `GET` | `/api/recordings/{session_id}` | 获取场次 metadata |
| `GET` | `/api/recordings/{session_id}/telemetry` | 流式返回 NDJSON 遥测 |

**提交请求体：**

```json
{
  "team_id": "team_01",
  "password": "password01",
  "code": "<base64 编码的 Python 源码>"
}
```

### 管理接口（Basic Auth，密码见 config.py）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/admin/set-session` | 配置下一场比赛 |
| `POST` | `/api/admin/start-race` | 启动 Webots + 开赛 |
| `POST` | `/api/admin/stop-race` | 中止当前比赛 |
| `POST` | `/api/admin/reset-track` | 重置状态机为 IDLE |
| `POST` | `/api/admin/lock-submissions` | 锁定代码提交 |
| `GET` | `/api/admin/standings` | 获取当前积分榜 |
| `POST` | `/api/admin/finalize-qualifying` | 资格赛阶段结束 |
| `POST` | `/api/admin/finalize-group` | 小组赛阶段结束 |
| `POST` | `/api/admin/finalize-semi` | 半决赛阶段结束 |
| `POST` | `/api/admin/close-event` | 整个赛事关闭 |
| `WS` | `/ws/admin` | 实时状态推送 |

---

## 学生提交指南

1. 打开 `http://<服务器地址>:8000/submit/`
2. 输入队伍 ID 和密码（由助教分配）
3. 将 `team_controller.py` 拖入上传区或点击选择文件
4. 点击"提交"，页面会显示语法检查结果和队列位置
5. 等待测试完成后可查看圈速、碰撞数等报告
6. 比赛开始前可多次提交，以最后一次提交参赛

---

## 开发说明

### 运行单元测试

```bash
cd server
conda run -n airacer python -m py_compile main.py api/admin.py api/submission.py race/scoring.py race/session.py race/state_machine.py
```

### Webots 控制器路径说明

Webots 启动时后端会将 `RACE_CONFIG_PATH` 注入环境变量，Supervisor 和 Car Controller 均从该路径读取 `race_config.json`。本地调试时可手动设置：

```bash
set RACE_CONFIG_PATH=D:\path\to\race_config.json
```

### 关键设计约束

- **不使用 ORM**：所有 DB 操作均为原生 `sqlite3`，`get_db()` 返回 `row_factory=sqlite3.Row` 的连接
- **状态机是核心同步点**：任何启动/停止操作前必须确认 `state_machine.state` 允许该转换
- **car_node_id 命名**：世界文件中 DEF 名为 `car_1`…`car_4`（1-indexed），后端 `set_session` 按此生成
- **遥测格式**：每行一个 JSON 对象 `{"t": float, "cars": [...], "events": [...]}`，事件嵌套在 `events` 数组中

### 文档

- [`docs/airacer-architecture.md`](docs/airacer-architecture.md)：完整平台设计，含数据流图
- [`docs/airacer-task-book.md`](docs/airacer-task-book.md)：各模块开发任务书，含接口定义
