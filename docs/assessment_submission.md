# EmbodiScope v2.3 考核交付清单

## 一句话主张

EmbodiScope 将具身实验失败从分散日志转化为可复现、可诊断、可恢复验证的证据闭环。

## 考核要求映射

| 考核内容 | 主要材料 |
|---|---|
| 问题背景 | PPT 第 2 页；README 考核快速入口 |
| 功能设计 | PPT 第 3-4 页；`docs/technical_report.md` |
| 系统实现 | PPT 第 5-7 页；项目代码与 Web demo |
| 原创点 | PPT 第 4、8、10 页；`THIRD_PARTY_NOTICES.md` |
| 运行演示 | 3 分钟现场路径；备用 MP4 |
| 效果验证 | PPT 第 8-9、11 页；三套 benchmark JSON |
| 不足与扩展 | PPT 第 12 页；RecoveryLab 解释边界 |

## 正式交付物

- 代码：`embodiscope/`、`static/`、`scripts/`、`tests/`
- README：`README.md`
- 答辩 PPT：`docs/EmbodiScope_Assessment_Deck_v2.3.pptx`
- 答辩讲稿与现场演示：`docs/demo_script.md`
- 备用演示视频：`docs/EmbodiScope_Assessment_Demo_v2.3.mp4`
- 高频问题：`docs/assessment_qa.md`
- 开源与原创边界：`THIRD_PARTY_NOTICES.md`
- 版本说明：`CHANGELOG.md`
- 软件引用信息：`CITATION.cff`
- 正式 RecoveryBench：`output/recovery-benchmark/recovery-bench-20260723-002311-2c10fb/result.json`

## 启动与验收

```powershell
powershell -ExecutionPolicy Bypass -File scripts\assessment_start.ps1
```

完整自动化验收：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\assessment_start.ps1 -RunTests
```

浏览器地址：`http://127.0.0.1:8876/`

## 正式结果

| 指标 | 结果 |
|---|---:|
| FaultBench Macro F1 | 98.4% |
| RepairBench 修复动作成功率 | 100.0% |
| RecoveryBench 严格任务恢复 | 8/9，88.9% |
| RecoveryBench Wilson 95% CI | 56.5%-98.0% |
| 完整 Episode 安全率 | 66.7% |
| 干预后安全率 | 100.0% |
| 在线触发覆盖 / 配对完整率 | 100.0% / 100.0% |
| 自动化测试 | 58/58，且 `static/app.js` 语法检查通过 |

## 结论边界

当前 RecoveryBench 验证的是 `PickCube-v1`、Franka Panda 控制器和三个故障场景中的局部恢复协议，不代表多任务、跨机器人或真机安全认证。seed 8 因受控故障签名缺失而在分析前排除；collision / seed 10 因没有产生任务成功的反事实增益而保留为严格失败。
