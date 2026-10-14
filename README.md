# cognitive-distiller（认知蒸馏器）

**从非结构化文本中提取结构化认知条目** —— 把一个人/一套理论的公开语料，变成可检索、可审计、可复用的本地知识资产。

```
语料文本(.txt) → 切块 → 逐块 LLM 提取 → 结构化 JSON（信念/价值/规条/洞察）→ 本地知识库
```

不是让模型"模仿大师"，而是把方法论从语料中**提取出来**——每条条目强制附带原文引用（evidence），不许编造。这是整套工具的核心设计：**防幻觉靠结构，不靠祈祷。**

## 为什么用它

大模型的"专家能力"来自训练参数里的世界知识——不可审计、不可修正、且质量参差。「提示词人设 / 蒸馏技能包」类玩法本质上都是让模型 COSPLAY，解决不了根子上的问题。

本工具走另一条路：**不依赖模型的世界知识，只依赖模型的推理与提取能力**，把专家的方法论提取成本地资产。提取出的条目可以：检索增强（分析有本地依据）、行为约束（配合工程钩子变成硬纪律）、复现校验（拿历史案例验证提取忠实度）。

完整方法论见 **[METHODOLOGY.md](METHODOLOGY.md)**。

## 快速开始

```bash
# 1. 配置模型通道（任何兼容端点；也可用本机既有路由，见下）
export DISTILL_API_URL="https://your-endpoint/v1/messages"   # Anthropic 协议(默认)
export DISTILL_API_KEY="sk-..."
export DISTILL_MODEL="claude-sonnet-5"
# OpenAI 兼容端点加一行: export DISTILL_PROTOCOL=openai

# 2. 放语料到 corpus/（.txt 文件，目录递归），先小样本验证
python tools/distill.py --corpus corpus --limit 1 --dry-run   # 只切块预览，不调模型
python tools/distill.py --corpus corpus --limit 1             # 真跑一个文件

# 3. 全量跑（随时可中断；重跑自动断点续切）
python tools/distill.py --corpus corpus

# 4. 有失败块时补跑（账目在 distill_progress.json 的 retry_chunks）
python tools/distill.py --corpus corpus --retry-json distill_progress.json
```

输出到 `corpus/distilled/`：`beliefs.json / values.json / rules.json / insights.json`
每条条目：`{"text": "...", "evidence": "原文引用", "source": "文件#chunkN"}`

## 特性（每条都是实战事故缝出来的）

| 特性 | 说明 |
|---|---|
| **证据强制** | 每条条目必须带原文真实片段（evidence），提取不许编造 |
| **块级断点** | kill 掉最多丢一块；大书跑断不用从头 |
| **失败记账** | 失败块自动进 `retry_chunks` 清单，事后一条命令补跑——不静默丢失 |
| **异常隔离** | 单块空响应/截断/脏 JSON 全部隔离在块内，不杀进程 |
| **原子落盘** | 每块完成即写（临时文件+replace），断电不毁数据 |
| **零第三方依赖** | 纯 Python 标准库 |
| **领域可定制** | 提示词经 `--system` 外挂；分类框架可改（默认四类：信念/价值/规条/洞察） |

## 常用参数

```bash
--corpus <路径>        # 语料目录或单个文件（默认 ./corpus）
--out <路径>           # 输出目录（默认 corpus/distilled）
--limit N              # 只处理 N 个文件（小样本验证）
--chunk N              # 每块字符数（默认 2500，越小越精细、调用越多）
--sleep N              # 块间节流秒数（默认 3，免费通道建议 5+）
--system <提示词.txt>  # 自定义蒸馏提示词（领域适配）
--dry-run              # 只切块预览
--retry-json <文件>    # 块级重试清单
--model <模型名>       # 强制模型（可跳过路由自带的选择逻辑）
```

## 模型通道

`tools/llm_client.py` 按优先级选择：

1. **显式端点**：`DISTILL_API_URL` + `DISTILL_API_KEY` + `DISTILL_MODEL`（`DISTILL_PROTOCOL=anthropic|openai`）
2. **本机既有路由**：若存在 `D:/kimi-workspace/tools/layer.py`（原作者的免费模型智能路由），自动使用——蒸馏器诞生于这套路由之上，保留兼容
3. 都没有 → **显式报错**（不静默）

## 无人值守跑法

用系统计划任务/Cron 定期调用即可；`distill.py` 自身续跑语义安全（重跑=续跑）：

```bash
# Linux crontab 示例：每小时续跑一轮
0 * * * * cd /path/to/cognitive-distiller && python tools/distill.py --corpus corpus >> distill.log 2>&1
```

## 来源与许可证

**Copyright (C) 2026 jerrypps3-glitch — GNU AGPL-3.0-or-later**（与同作者的开源缠论引擎同款协议）

自由使用、修改、分发；衍生作品（含网络服务化）需以 AGPL-3.0 开源。此项目没有商业目的，只希望它被取用、被完善。

拆解永远比囤积有价值——把东西拉下来拆零碎了，比收藏一百个仓库都强。
