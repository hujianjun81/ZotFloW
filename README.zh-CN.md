# ZotFlow

[English](README.md) | [简体中文](README.zh-CN.md) · [MIT 开源协议](LICENSE)

**以 Zotero 文献库、论文批注和实际研究进展为依据的模块化科研助手。**

ZotFlow 帮助理解论文为什么这样设计、比较跨论文证据、寻找适合当前条件的资源，并将已有结果整理成有用的科研讨论。它起源于无线基础模型（WFM）与模型压缩学习；实际建议应依据使用者自己的领域、目标和约束。

本仓库提供的是 **供智能体使用的本地 skill**。工作流由宿主智能体结合 Python 脚本执行，不能作为独立应用或 Zotero 插件启动。当前版本：**2.0.0**。

## 能做什么

保留 ZotFlow 统一入口，只加载本次任务需要的模块。

| 工作流 | 适用任务 |
| --- | --- |
| `library` | 检索本地 Zotero 元数据，读取可用全文、批注及论文关联问答。 |
| `close-reading` | 解释问题、设计原因、实现方式和消融证据，生成有重点的精读笔记。 |
| `resource-search` | 根据已有基础与现实约束推荐论文、代码、权重、数据集或教程。 |
| `research-review` | 梳理学习与研究计划，区分已完成证据、已确认决定和 AI 候选建议。 |
| `paper-comparison` | 比较机制与证据，检查缺失对照，分析迁移到自身任务的条件。 |
| `experiment-review` | 解读已有实验，核对比较条件，准备导师讨论中的结论与问题。 |
| `figures` | 绘制可编辑方法关系图，将关键实验表格转为合适的图表。 |
| `note-quality` | 检查精读结构、公式引用，以及支持的图片和链接格式。 |

核心标准是：**压缩重复叙述和常规配置，保留“问题 → 设计原因 → 如何实现 → 消融证据”的完整链条。** 公式解释要联系数学操作的实际作用；批注和反复提问决定哪些地方需要多讲一些。篇幅服从理解需要，不设硬性字数上限。

## 使用条件

- 能加载 `SKILL.md`、读取本地文件并执行 Python 的智能体。下方安装方式面向 **Codex**；其他宿主需要自行配置技能加载位置和权限。
- **Python 3.10+**；随附脚本仅使用标准库。本次本地验证环境为 Windows / Python 3.12，其他平台尚未独立验证。
- 本地 Zotero 数据库，用于文献库和批注读取。默认路径是 `~/Zotero/zotero.sqlite`，可修改。
- 可选：使用兼容表结构的 **LLM-for-Zotero 会话注册表**，用于读取论文关联对话；以及兼容的 **MinerU 缓存**，用于结构化全文和图表读取。本仓库不包含这两个插件或 PDF 解析器。
- 可选：外部检索工具、绘图工具，以及兼容的 `nature-academic-search`、`nature-figure` 技能。它们并未随仓库分发，也不会因为安装 ZotFlow 就自动连接。

研究复盘也可以直接使用用户提供的文件和上下文。批注对话或全文缓存不可访问时，应明确说明缺项，不能编造内容，也不能据此断言没有记录。

## 在 Codex 中安装

将仓库安装为名为 `zotflow` 的技能目录。以下位置依据 [Codex 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

**Windows / PowerShell**

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.agents\skills') | Out-Null
git clone https://github.com/hujianjun81/ZotFloW.git (Join-Path $HOME '.agents\skills\zotflow')
```

**macOS / Linux**

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/hujianjun81/ZotFloW.git ~/.agents/skills/zotflow
```

若只在某个项目中使用，可改为克隆到 `<项目>/.agents/skills/zotflow`。选择一种安装范围，避免重复技能入口。若目标位置已有个人版本，请先备份或比较差异；`git clone` 不会覆盖非空目录。

Codex 通常会自动发现技能变更；未出现时可重启。直接在请求中提及 ZotFlow，或在支持的 Codex 输入框中使用 `$zotflow`。建议在自己的研究项目中发起任务，便于读取项目记录并保存到正确目录。

## 配置本地数据

随附脚本不需要 API Key。环境变量要设置在智能体实际运行命令的环境中；以下脚本命令均从已安装的技能目录执行。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ZOTERO_ROOT` | `~/Zotero` | 包含 `zotero.sqlite`、通常也包含 `storage/` 的目录。 |
| `MINERU_BASE` | `$ZOTERO_ROOT/llm-for-zotero-mineru` | 兼容的论文解析缓存根目录。 |
| `ZOTFLOW_TEMP_DIR` | 系统临时目录 | 可选的、已存在且可写的数据库快照临时目录。 |

PowerShell 示例；请将示例路径换成自己的：

```powershell
$env:ZOTERO_ROOT = 'D:\Research\Zotero'
$env:MINERU_BASE = 'D:\Research\parsed-papers'
python scripts/query_zotero.py --list-collections
```

Bash 对应写法：

```bash
export ZOTERO_ROOT="$HOME/Zotero"
export MINERU_BASE="$ZOTERO_ROOT/llm-for-zotero-mineru"
python scripts/query_zotero.py --list-collections
```

默认位置正确时无需设置。若使用 `ZOTFLOW_TEMP_DIR`，请先创建对应目录。桌面智能体需要继承这些变量；在无关终端中设置变量可能不会影响已运行的应用。

要获得个性化建议，可指定项目已有记录，或说明研究领域、当前问题、导师要求、可用数据/模型/设备和已经确认的决定。不强制使用某个研究档案文件名。私人研究记录留在自己的项目中，不放入本公开技能仓库。

## 使用示例

直接描述任务，不需要记住模块名。

```text
用 ZotFlow 为《论文题目》生成精读笔记，结合原文批注和可读取的关联问答。
重点解释核心设计为什么必要、如何实现、消融究竟证明了什么，保存到［目录］。

结合我当前的 WFM 压缩问题找资源。先查已读记录，再找值得补充的新论文
和官方代码，说明每项资源在我现有条件下能帮助解决什么问题。

根据［项目记录］复盘学习进展，区分已经展示理解的部分和仅阅读过的部分，
给出一个可以验证完成情况的下一步。

比较［论文 A］和［论文 B］对［目标任务］的价值，核对假设、监督信号、
成本、证据和迁移所需条件。

分析［实验结果文件］，整理导师讨论材料：已支持的结论、尚不确定的地方，
以及哪个对照实验能帮助消除不确定性。
```

内部指令、模板和精读风格检查目前主要面向**中文**。可以要求英文输出，但内置 lint 尚不提供等效的英文风格覆盖。双语 README 不代表内部模板已完成全面双语化。

## 随附脚本

将 `PAPER001` 替换为真实的八位 Zotero 父条目或 PDF 附件 Key。

```bash
python scripts/query_zotero.py --search "quantization" --json
python scripts/query_zotero.py --key PAPER001 --annotations --json
python scripts/read_zotero_context.py --key PAPER001
python scripts/read_mineru.py --key PAPER001 --toc
python scripts/read_mineru.py --key PAPER001 --table 2
python scripts/lint_note.py --note /path/to/paper-note.md
python scripts/lint_note.py --vault /path/to/notes --moc index.md
python scripts/audit_figures.py --vault /path/to/notes --selftest
```

- **数据库读取**：仅读源文件，验证主库及存在的 WAL 副本稳定一致，经 SQLite 完整性检查后查询内存快照。连续变化时有界重试并报错；临时副本会清理，不向原 Zotero 库写入内容。
- **对话读取**：只查询有效论文注册表关联的受支持消息表，保留分支与重复提问，仅标记可能的继承重复。插件表结构不兼容、选中文本不可用时明确报告。它不是适配所有 Zotero AI 插件的通用导出工具。
- **MinerU 读取**：每篇缓存需要 `_llm_source.json`、`full.md`；结构化操作还使用 `manifest.json`、`content_list.json`、`images/`。任意原始 MinerU 输出可能需要适配，详见[文献读取模块](references/library.md)。
- **笔记检查**：新精读可用 `zotflow_type: close-reading`；其他科研产物使用主要工作流名，可另附 `zotflow_workflows` 列表，避免仅因包含 Zotero Key 就被套用精读风格。`--moc` 显式启用已有索引收录检查。图片体检支持 Obsidian 图片嵌入和 HTML `<img>` 引用，并非通用 Markdown 校验器。

各脚本的完整参数可通过 `--help` 查看。若 Python 3 在本机对应 `python3` 或 `py`，请用该命令替换示例中的 `python`。输出可能含私人批注和对话，分享前请自行检查。

## 图表与证据

方法关系图通常采用可编辑 `.drawio.svg`，实验数据图采用 `.svg`。每张图只保留一种最终格式，嵌入相关说明附近；临时预览放研究项目的 `.build/`。绘制和渲染依赖由所选后端决定，仓库不负责安装。

图表保留来源数值、单位、对照条件与表号/页码。不得虚构误差条，也不能将理论 FLOPs 降低写成实测时延提升。没有消融就是证据缺口；生成笔记和 lint 通过也不代表科学结论正确或已经掌握论文。

## 隐私与使用边界

公开内容包括技能指令、模板、脚本源码和合成测试，不包含个人文献库、PDF、批注、聊天导出、凭证或私人研究记录。Python 脚本没有网络上传逻辑；使用云端模型、搜索或其他外部工具时，数据处理与权限由宿主环境决定。“本地读取文件”不意味着整个 AI 流程离线运行。

科研建议仍是建议。讨论方案本身不授权启动训练、修改数据划分、发送外部消息或建立定期督学。ZotFlow 应尊重已确认的用户决定，并区分来源、推断、证据缺口和实施约束。

## 目录与验证

```text
SKILL.md                     统一入口与路由规则
manifest.yaml                工作流及辅助脚本映射
references/                  精读、上下文、配图、模板与质量规则
references/workflows/        资源检索、研究复盘、论文比较和实验讨论
scripts/                     本地读取、检查脚本和合成回归测试
```

在仓库根运行，无需真实 Zotero 文献库：

```bash
python -m unittest discover -s scripts -p "test_*.py" -v
python scripts/lint_note.py --selftest
```

测试覆盖快照一致性与只读行为、批注保真、论文会话关联、分支保留、产物路由、可配置索引和配图自测安全性；不代表验证过所有插件版本或所有科学结论。反馈问题时请提供可复现描述与脱敏样本，避免上传私人数据库或聊天导出。

通过 Git 安装且工作区干净时，可在技能目录执行 `git pull --ff-only` 更新；有本地定制时先保存或提交修改。

## 开源协议

采用 [MIT](LICENSE) 协议，仅覆盖本仓库内容。引用的论文、第三方工具、模型、数据集和用户材料仍遵循各自的授权协议。
