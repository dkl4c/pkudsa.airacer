# Webots 建模指南 — 赛道 & 赛车 PROTO

本文档面向负责 3D 建模的技术组成员。完成建模后需要同步更新两个代码文件，本文均有说明。

---

## 概览

| 任务 | 文件 | 预计工作量 |
|------|------|-----------|
| 赛车 PROTO | `webots/protos/AiRacerCar.proto` | 2–4 小时 |
| 赛道几何 | `webots/worlds/airacer.wbt` | 4–8 小时 |
| 同步代码（检查点坐标） | `webots/controllers/supervisor/supervisor.py` | 30 分钟 |
| 同步代码（小地图边界） | `frontend/race/minimap.js` | 15 分钟 |

---

## 一、赛车 PROTO

### 1.1 目标

替换 `airacer.wbt` 中当前的占位 `Robot` 节点，改为真正的车辆节点，使 `car_controller.py` 的 `Driver` API 可以正常工作。

### 1.2 使用 Webots 内置车辆 PROTO（推荐路线）

Webots R2023b 内置了多款可直接使用的车辆模型，位于：

```
D:\Webots\msys64\mingw64\share\webots\projects\vehicles\protos\
```

推荐使用 `BmwX5` 或 `ToyotaPrius`（物理参数合理，已有转向/驱动配置）。

**步骤：**

1. 在 Webots 场景树中，右键 `car_1` 节点 → Delete
2. 右键根节点 → Add new node → 搜索 `BmwX5`（或其他车型）
3. 将节点的 `DEF` 设为 `car_1`，`name` 设为 `car_1`
4. 设置 `controller` 为 `car`
5. 在车辆节点的 `sensorsSlotFront`（或对应插槽）添加双目摄像头（见 1.3）
6. 重复以上步骤完成 `car_2`、`car_3`、`car_4`
7. 保存世界文件

### 1.3 添加双目摄像头

在每辆车的摄像头插槽下添加两个 `Camera` 节点，**名称必须与代码一致**：

```vrml
Camera {
  name "left_camera"
  width 640
  height 480
  fieldOfView 1.047
  translation -0.3 0.3 0.8
}
Camera {
  name "right_camera"
  width 640
  height 480
  fieldOfView 1.047
  translation 0.3 0.3 0.8
}
```

> `translation` 值为相对车体的偏移，根据实际车型调整，确保视野朝前。

### 1.4 如需创建自定义 PROTO

若团队需要统一车辆外形，可创建 `webots/protos/AiRacerCar.proto`，继承 Webots 内置 `Car`：

```vrml
PROTO AiRacerCar [
  field SFVec3f    translation  0 0 0
  field SFRotation rotation     0 1 0 0
  field SFString   name         "car"
  field SFString   controller   "car"
  field SFString   customData   ""
]
{
  Car {
    translation  IS translation
    rotation     IS rotation
    name         IS name
    controller   IS controller
    customData   IS customData
    # 在此配置车辆参数（引擎、悬挂、轮距等）
    # 参考 Webots 文档 Car PROTO 字段说明
    sensorsSlotFront [
      Camera {
        name "left_camera"
        width 640
        height 480
        fieldOfView 1.047
        translation -0.3 0.3 0
      }
      Camera {
        name "right_camera"
        width 640
        height 480
        fieldOfView 1.047
        translation 0.3 0.3 0
      }
    ]
  }
}
```

然后在 `airacer.wbt` 中：

```vrml
DEF car_1 AiRacerCar {
  translation 0 0 5
  name "car_1"
  controller "car"
}
```

---

## 二、赛道几何

### 2.1 规格要求

| 参数 | 要求 |
|------|------|
| 形状 | 闭合环形，无交叉 |
| 周长 | 150–200 m |
| 路面宽度 | 主直道 ≥ 8 m，窄道区 ≥ 4 m |
| 必含路段 | 主直道 / 发夹弯 / S 型弯 / 窄道区 |
| 路面材质 | 深灰色沥青（PBRAppearance，roughness ≥ 0.8） |
| 边界 | 路肩（白色）+ 护栏或路锥 |

### 2.2 建模步骤（Webots 场景编辑器）

1. 删除 `track_placeholder` Solid 节点
2. 用 `Solid` + `Shape` 组合拼出赛道（推荐用 `ElevationGrid` 或拼接 `Box` 段）
3. 每段需要 `boundingObject` 和 `physics` 才能与车辆发生碰撞
4. 在起终点线位置放置明显标志（颜色条纹或 3D 标志物）
5. 在发夹弯出口、S 型弯中段各放置检查点标志（可用半透明平面，运行时不可见）

### 2.3 坐标系说明

Webots 使用 NUE 坐标系（`coordinateSystem "NUE"` 已在世界文件中设置）：

- X 轴：右
- Y 轴：上
- Z 轴：朝向屏幕外（即摄像机方向）

`supervisor.py` 中读取的坐标为 `(pos[0], pos[2])`，即 **X-Z 平面**代表地面。

---

## 三、完成建模后必须更新的代码

### 3.1 更新检查点坐标

文件：`webots/controllers/supervisor/supervisor.py`，第 37–42 行附近：

```python
CHECKPOINTS = [
    {"id": 0, "cx":  X0, "cy":  Z0, "half_w": W0, "half_h": H0, "track_heading": A0},  # 起终点线
    {"id": 1, "cx":  X1, "cy":  Z1, "half_w": W1, "half_h": H1, "track_heading": A1},  # 主直道末端
    {"id": 2, "cx":  X2, "cy":  Z2, "half_w": W2, "half_h": H2, "track_heading": A2},  # 发夹弯出口
    {"id": 3, "cx":  X3, "cy":  Z3, "half_w": W3, "half_h": H3, "track_heading": A3},  # S 型弯中部
]
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `cx`, `cy` | 检查点中心的 X、Z 坐标（从 Webots 场景树读取） |
| `half_w`, `half_h` | 检测区域半宽、半高（建议比路宽略小，约 3–4 m） |
| `track_heading` | 赛道在该点的前进方向（弧度，X-Z 平面内，X 轴正方向为 0） |

**如何获取坐标：** 在 Webots 中选中检查点标志物节点，场景树中直接读取 `translation` 字段的 x、z 值。

**track_heading 速查：**

| 朝向 | 角度 |
|------|------|
| +X 方向（向右）| 0.0 |
| +Z 方向（向外）| −π/2 ≈ −1.57 |
| −X 方向（向左）| ±π ≈ ±3.14 |
| −Z 方向（向内）| +π/2 ≈ 1.57 |

### 3.2 更新小地图世界边界

文件：`frontend/race/minimap.js`，顶部 `WORLD` 常量：

```javascript
const WORLD = {
  xMin: -XX,   // 赛道最左侧 X 坐标（留 10m 余量）
  xMax:  XX,   // 赛道最右侧 X 坐标
  zMin: -ZZ,   // 赛道最前侧 Z 坐标
  zMax:  ZZ,   // 赛道最后侧 Z 坐标
};
```

确保整条赛道都在这个矩形范围内，小地图才能正确缩放。

---

## 四、验收检查清单

建模完成后，按以下步骤验收：

```
□ 四辆车节点 DEF 名为 car_1 ~ car_4，controller 均为 "car"
□ 每辆车包含 left_camera 和 right_camera 两个摄像头（宽640高480）
□ 启动 Webots + 后端，car 控制器无报错（不再出现 "Only nodes based on Car"）
□ 赛道无穿模（用 Webots 物理检查：View → Optional Rendering → Physics）
□ supervisor.py 的 CHECKPOINTS 坐标已更新
□ 让 team_01 用模板代码（直行）跑一圈，supervisor 能记录到 lap_complete 事件
□ frontend/race/minimap.js 的 WORLD 边界已更新，小地图能渲染出赛道轮廓
```

---

## 参考资料

- Webots R2023b 车辆文档：`D:\Webots\msys64\mingw64\share\webots\docs\` 或官网
- 内置车辆 PROTO 示例：`D:\Webots\msys64\mingw64\share\webots\projects\vehicles\protos\`
- 内置障碍物/锥桶 PROTO：`D:\Webots\msys64\mingw64\share\webots\projects\objects\`
