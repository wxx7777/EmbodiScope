# 任务图与失败恢复规划

## 为什么需要这一层

普通日志诊断回答“哪个信号异常”，但任务与运动规划还需要回答三个更接近机器人决策的问题：

1. 异常发生时机器人正在执行哪个技能。
2. 哪个前置条件、技能效果或安全不变量最先失效。
3. 应从哪个局部状态恢复，而不是把整个任务从头执行。

EmbodiScope v1.9 将连续轨迹映射为可审计的符号任务图。它不训练策略，也不把规则建议描述为真实执行结果；它的目标是在数据、诊断和规划器之间建立明确接口。

## Pick-and-Place Operator Graph

内置模板包含五个技能算子：

| Operator | 主要前置条件 | 预期效果 |
|---|---|---|
| `ApproachObject` | `observation_fresh`、`collision_free` | `target_localized` |
| `ReachPregrasp` | `target_localized`、`gripper_open`、`collision_free` | `near_object` |
| `SecureGrasp` | `near_object`、`gripper_open`、`observation_fresh` | `gripper_closed`、`object_attached` |
| `TransportObject` | `object_attached`、`collision_free`、`motion_progress` | `at_goal` |
| `ReleaseAtGoal` | `at_goal`、`object_attached`、`collision_free` | `object_released`、`task_complete` |

这些算子表达的是任务语义约束，不替代底层运动规划器或控制器。

## 连续信号如何落到谓词

- `object_attached`：夹爪闭合、目标距离小于阈值，并且没有抓取滑脱事件。
- `collision_free`：阶段接触力没有超过 Profile 安全门限，也没有工作空间越界。
- `motion_progress`：位姿或状态速度持续超过卡滞阈值。
- `observation_fresh`：时间戳、帧有效性和多模态同步没有触发相应诊断事件。
- `at_goal`：当前版本可由 `transport -> place` 阶段转换弱推断；有目标位姿时应使用几何约束替代。
- `task_complete`：只使用明确的 success、reward 或 terminal 结果，不从阶段名称伪造成功。

每个谓词都返回 `true / false / unknown`、证据文本和置信度。缺少传感器时保持 `unknown`，不会自动当作成功。

## 首因与恢复规划

系统优先使用带时间定位的诊断事件，找到最早失效谓词和对应技能。例如：

- 碰撞：`collision_free=false`，停止运动、撤退、重新观测、无碰撞重规划、带力阈值重试。
- 卡滞：`motion_progress=false`，停止控制器、回退到最近有进展状态、重新规划、使用 watchdog 恢复。
- 时序缺口：`observation_fresh=false`，保持位置、重新同步、验证新鲜状态、恢复中断技能。
- 抓取滑脱：`object_attached=false`，停止运输、张开夹爪、重定位目标、重规划预抓取、闭合并验证附着。

恢复算子的目标是恢复缺失谓词，而不是盲目重做完整任务。实际执行仍需接入 MoveIt、PDDLStream、行为树或具体机器人的控制栈。

## 开源方法依据

- [PDDLStream](https://github.com/caelan/pddlstream)：符号规划与连续采样约束结合。
- [MoveIt Task Constructor](https://github.com/moveit/moveit_task_constructor)：分阶段操作任务以及前向、后向状态传播。
- [BehaviorTree.CPP](https://github.com/BehaviorTree/BehaviorTree.CPP)：可恢复、可监控的机器人技能执行树。
- [py_trees](https://github.com/splintered-reality/py_trees)：任务行为状态与运行时可视化。

项目自身实现连续信号到谓词的映射、首个约束失效定位、失败传播解释和恢复算子生成，没有复制上述项目的规划器实现。

## API

- `GET /api/task-reasoning`：返回当前数据集的任务图推理结果。
- `GET /api/task-reasoning.json`：下载同一结果的 JSON 文件。

核心实现位于 `embodiscope/task_reasoning.py`。
