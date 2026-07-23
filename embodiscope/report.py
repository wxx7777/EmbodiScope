from __future__ import annotations

from datetime import datetime
from typing import Any


def build_markdown_report(dataset_name: str, analysis: dict[str, Any], source: dict[str, Any] | None = None) -> str:
    status = "成功" if analysis["success"] else "失败"
    source_label = (source or {}).get("adapter_name", "通用数据适配器")
    lines = [
        "# EmbodiScope 具身数据质量诊断报告",
        "",
        f"- 数据集：`{dataset_name}`",
        f"- 数据来源：{source_label}",
        f"- Episode：`{analysis['episode_id']}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 任务结果：{status}",
        f"- 综合质量分：**{analysis['quality_score']} / 100（{analysis['grade']}）**",
        "",
        "## 质量维度",
        "",
        "| 维度 | 得分 |",
        "|---|---:|",
        f"| 完整性 | {analysis['scores']['completeness']} |",
        f"| 时序质量 | {analysis['scores']['temporal']} |",
        f"| 运动质量 | {analysis['scores']['motion']} |",
        f"| 多模态同步 | {analysis['scores']['sync']} |",
        f"| 安全性 | {analysis['scores']['safety']} |",
        "",
        "## 关键指标",
        "",
        f"- 数据点：{analysis['rows']}",
        f"- 时长：{analysis['duration']:.2f} s",
        f"- 采样率：{analysis['sample_rate']:.2f} Hz",
        f"- 缺失率：{analysis['metrics']['missing_rate']:.3f}%",
        f"- 视觉同步偏移：{analysis['metrics']['sync_offset_ms']:.1f} ms",
        f"- 关节突跳：{analysis['metrics']['joint_jumps']} 次",
        f"- 力峰值异常：{analysis['metrics']['force_spikes']} 次",
        "",
        "## 诊断问题",
        "",
    ]
    if not analysis["issues"]:
        lines.append("未发现超过当前阈值的质量问题。")
    for index, issue in enumerate(analysis["issues"], start=1):
        severity = {"critical": "严重", "warning": "警告", "info": "提示"}.get(issue["severity"], issue["severity"])
        lines.extend([
            f"### {index}. [{severity}] {issue['title']}",
            "",
            issue["description"],
            "",
            f"**证据：** {issue['evidence'] or '见对应时间段信号'}",
            "",
            f"**建议：** {issue['recommendation']}",
            "",
        ])
    lines.extend(["## 失败根因排序", ""])
    if analysis["root_causes"]:
        lines.extend(["| 排名 | 根因 | 置信度 | 判断依据 |", "|---:|---|---|---|"])
        for index, cause in enumerate(analysis["root_causes"], start=1):
            lines.append(f"| {index} | {cause['label']} | {cause['confidence']} | {cause['reason']} |")
    else:
        lines.append("该 episode 成功完成，未发现明确失败根因。")
    lines.extend([
        "",
        "## 使用说明",
        "",
        "本报告由统计规则与时序信号分析自动生成，用于加速数据筛查和实验调试。严重问题仍应结合原始视频、机器人控制日志和硬件状态复核。",
        "",
    ])
    return "\n".join(lines)
