# AI Racer GitHub 协作工作流

版本 v1.0 | 2026-04-24

---

## 一、团队与模块分工

| 成员 | 负责模块 | 主要目录 |
|------|----------|----------|
| 成员 A | 模块一：Webots 仿真 | `webots/` |
| 成员 B | 模块二：赛事管理后端 | `server/` |
| 成员 C | 模块三：前端与回放 | `frontend/` |

**共享目录（需要跨模块协调）：**
- 根目录的 `race_config.json`（字段变更须知会领头人）
- `docs/`（架构文档修改须走 Issue 流程）
- `recordings/`（录制文件输出目录，不纳入版本控制）
- `submissions/`（学生代码提交目录，不纳入版本控制）

**组长负责：**

- 整体集成与端到端联调
- 所有涉及接口的 PR 审核
- 接口冲突裁决

---

## 二、分支策略

- **`main`**：受保护分支，只能通过 PR 合并，不允许直接 push
- **功能分支命名规则**：`feat/{模块简称}/{功能描述}`

  | 示例分支名 | 对应功能 |
  |-----------|---------|
  | `feat/webots/supervisor-recording` | Supervisor 录制文件写入 |
  | `feat/backend/recording-api` | 录制文件服务 REST API |
  | `feat/frontend/replay-player` | 回放播放页核心逻辑 |

- 不使用长生命周期的 `dev/模块` 独立分支。所有功能分支直接从 `main` 创建，完成后合回 `main`。

---

## 三、PR 规范

**基本要求：**
- 每个 PR 聚焦单一功能，目标变更量不超过300行
- PR 标题格式：`[模块] 功能描述`
  - 示例：`[Webots] 实现Supervisor录制写入`、`[Backend] 添加录制文件服务API`、`[Frontend] 完成回放时间轴控制`

**PR 描述必须包含：**
1. 接口变更说明（若涉及 race_config 字段、录制文件格式、REST API 路径或参数，须明确列出变更前后的差异）
2. 至少3项自测结果（格式：操作步骤 → 期望结果 → 实际结果）
3. 若触碰接口文件，须在描述中标注"已知会领头人"

**审核规则：**
- 触碰接口文件的 PR（`race_config.json` 字段、录制文件格式、REST API）必须由领头人审核后才能合并
- 其他 PR 可由任意成员互相审核，领头人不强制参与

---

## 四、接口文件保护

**受保护文件：**
- `docs/airacer-architecture.md` 中第3.2~3.5节（接口数据格式规范）：只有领头人可以直接修改

**修改接口的流程：**
1. 需要修改接口的成员在 GitHub Issue 中描述变更意图（变更的字段/端点、变更原因、影响的模块）
2. 领头人在 Issue 中确认，或提出替代方案
3. 领头人确认后，成员才可在分支中实现变更并开 PR
4. `race_config.json` 的字段变更必须在同一 PR 中同步更新 `docs/airacer-architecture.md`

---

## 五、Vibe Coding 规范

**使用 AI 生成代码前：**
- 确认当前工作目录的 `CLAUDE.md` 内容准确，特别是模块职责和接口约定部分
- 向 AI 提供当前模块的接口规范（来自 `docs/airacer-task-book.md` 对应章节）

**生成后必须做的事：**
- 在本地实际运行验证，确认功能符合预期后再提交
- AI 生成的代码不可直接 push，须经过本地验证

**Commit message 格式：**
- 简短中文描述，说明做了什么
- 使用 Claude Code 生成时，commit message 会自动附带 Co-Authored-By 署名，无需手动添加

**Commit 频率建议：**
- 每完成一个可独立测试的小步骤就 commit，不要攒大 commit
- 示例节奏：实现路由骨架 → commit；添加文件读取逻辑 → commit；添加错误处理 → commit

---

## 六、集成检查点

建议按以下顺序完成集成验证，每个检查点均需实际运行，不可仅凭代码审查跳过：

| 步骤 | 操作 | 验收条件 |
|------|------|----------|
| 1 | 模块一完成后：使用官方模板代码（附录A）跑一圈 | `recordings/{session_id}/telemetry.jsonl` 和 `metadata.json` 正确生成，文件格式符合接口①规范 |
| 2 | 模块二完成后：启动后端服务，调用录制文件 API | `GET /api/recordings/{session_id}/telemetry` 可流式返回步骤1的 JSONL，每行格式合法 |
| 3 | 模块三完成后：打开回放播放页加载步骤1的录制 | 时间轴可操作，小地图车辆位置与 JSONL 数据一致，最终排名与 `metadata.json` 一致 |

---

## 七、日常工作流（快速参考）

```bash
# 开始新功能
git checkout main && git pull
git checkout -b feat/backend/recording-api

# 开发过程中频繁 commit（每完成一个可测试的小步骤）
git add server/api/recording.py
git commit -m "实现录制文件服务API基础路由"

git add server/api/recording.py
git commit -m "添加telemetry流式返回逻辑"

# 功能完成后推送并开 PR
git push -u origin feat/backend/recording-api
# 在 GitHub 上开 PR：
# - 标题：[Backend] 添加录制文件服务API
# - 描述：填写接口变更说明 + 自测结果
# - 若涉及接口文件，请求领头人审核

# 合并后清理本地分支
git checkout main && git pull
git branch -d feat/backend/recording-api
```

---
