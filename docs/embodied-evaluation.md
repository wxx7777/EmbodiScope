# 具身策略数据就绪度

EmbodiScope 的具身评估页回答一个比“字段是否完整”更接近机器人学习的问题：一段轨迹是否保留了

`observation -> action -> next state -> contact -> task outcome`

这条闭环证据链。它是训练前的数据筛查，不是策略成功率预测器。

## 五个维度

- **观测覆盖**：检查本体状态、动作、视觉、接触信号和任务结果是否存在。
- **动作-状态响应**：计算当前动作与下一时刻状态变化的互相关，并报告响应延迟。没有显式 `action_*` 时只使用状态差分代理，并标记为风险。
- **时序与阶段**：检查时间轴单调性、采样抖动、阶段数量和阶段切换，避免把 reset、approach、grasp、transport、place 混成一条无语义序列。
- **接触语义**：用夹爪-目标距离和力觉信号确认抓取或接触是否能被物理证据解释。
- **行为多样性**：用动作幅值的变化程度筛查“只有一条几乎静止的演示”或动作分布过窄的问题。

分数采用固定透明权重：观测覆盖 22%、动作-状态响应 25%、时序与阶段 18%、接触语义 20%、行为多样性 15%。动作缺失、结果标签缺失和时间轴异常会生成阻断项；因此高平均分不能掩盖闭环断裂。

## 开源依据

本功能借鉴公开数据约定和工程组织方式，不复制第三方实现：

- [Hugging Face LeRobot](https://github.com/huggingface/lerobot)：统一 `observation` / `action`、Episode 元数据与 Parquet + 视频布局。
- [robomimic](https://github.com/ARISE-Initiative/robomimic)：trajectory 中 `states`、`actions`、`rewards`、`dones`、`obs` 和 `next_obs` 的结构化契约，并强调动作归一化。
- [RLDS](https://github.com/google-research/rlds)：Episode/Step 语义、`is_last`、`is_terminal`、reward 与截断状态的区分。
- [ManiSkill](https://github.com/haosulab/ManiSkill)：将仿真状态、控制动作、接触信号、任务结果与可回放轨迹放在同一实验闭环内。

## 解读限制

1. 就绪度高只表示数据更适合进入策略训练，不代表策略在真实机器人上一定成功。
2. 状态差分代理只能说明“运动发生了”，不能证明控制器发出了什么动作。
3. 接触评分依赖数据中至少存在 `gripper`、`object_distance` 或 `force/wrench` 信号。
4. 真正的策略评测仍需要留出验证集、仿真回放和真实硬件闭环测试。
