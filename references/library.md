# 本地文献库与全文读取

## 功能一：文献查找

```bash
python scripts/query_zotero.py --search "<关键词>"           # 元数据关键词搜索（含题名等字段）
python scripts/query_zotero.py --author "<作者>"             # 按作者搜索
python scripts/query_zotero.py --tag "<标签>"                # 按标签搜索
python scripts/query_zotero.py --collection "<分类>"         # 按分类搜索
python scripts/query_zotero.py --key "<key>"                 # 论文详情
python scripts/query_zotero.py --key "<key>" --annotations   # 详情 + Zotero 标注
python scripts/query_zotero.py --list-collections            # 列出所有分类
python scripts/query_zotero.py --list-mineru                 # 列出已解析论文
python scripts/query_zotero.py --unparsed                    # 列出有 PDF 但还没解析的
```

`--key` 同时接受 **itemKey**（父条目，如 `PAPER001`，精读笔记里记的就是它）和
**附件 key**（如 `PDF00001`）。输出带 `[MinerU]` 标记表示已关联解析目录，读取前仍检查 `full.md` 实际可用。

搜索结果显示 `has_mineru: true` 时，直接转功能二；否则检查全文是否可用，报告实际缺项。

## 功能二：读取论文正文与图表

```bash
python scripts/read_mineru.py --list                          # 所有已解析论文（目录/itemKey/页数/图表数）
python scripts/read_mineru.py --key <KEY> --toc               # 目录结构 + 图表清单 + 自动判定论文类型
python scripts/read_mineru.py --key <KEY> --read              # 输出 full.md 正文
python scripts/read_mineru.py --key <KEY> --read --offset 40000 --limit 40000   # 分段读长文
python scripts/read_mineru.py --key <KEY> --page 7            # 单页原文（页码从 0 开始）
python scripts/read_mineru.py --key <KEY> --figures           # 图清单（编号/图注/页/文件名/所属章节）
python scripts/read_mineru.py --key <KEY> --img-tags          # 可直接粘贴的 <img> 标签
python scripts/read_mineru.py --key <KEY> --tables            # 表清单
python scripts/read_mineru.py --key <KEY> --table 2           # 第 2 张表的内容（HTML 转 markdown）
python scripts/read_mineru.py --grep "<正则>" --context 2      # 跨全部已解析论文检索
python scripts/read_mineru.py --key <KEY> --copy-figures --out <项目>/figures   # 复制图片并打印标签
```

**用 `--copy-figures` 获取已核对的图片文件名与标签，不手抄哈希。**
生成笔记时可先输出到项目 `.build/<论文简称>/source-figures/`，仅将选用的原图复制到
最终 `figures/`，保留原 sha256 文件名并调整标签的相对路径。图片是否选用取决于
其对核心机制或证据的帮助，不要求全文原图全部嵌入。

### 兼容的 MinerU 缓存结构

脚本适配的是 LLM-for-Zotero 风格的缓存，不是任意 MinerU 输出。预期目录 `<Zotero>/llm-for-zotero-mineru/<attachmentId>/` 含以下文件；插件版本可能改变字段，使用时检查实际内容：

| 文件 | 实际内容 |
|------|---------|
| `full.md` | 正文。兼容缓存通常将表格以内嵌 HTML 保存；图片可能与正文分离，图注可能只以文本保留 |
| `manifest.json` | `figureBlocks`（图的权威来源）、`sections[]`、`totalPages`、`totalChars` |
| `content_list.json` | 块级结构，带页码和坐标：`page_idx`、`bbox`、`table_body`(HTML)、`image_caption` |
| `_llm_source.json` | Zotero 关联：`attachmentKey`、`parentItemKey`、`attachmentId`、`sourceFilename` |
| `images/` | 提取的图片，文件名为 sha256 |

**三个坑：**

1. **图和正文是分离的** —— `full.md` 不保证有图片引用。图的编号、图注、页号、文件名优先从
   `manifest.json` 的 `figureBlocks` 或 `content_list.json` 取，不能靠读 `full.md` 推断。
2. **部分缓存的 `manifest.allFigures` / `allTables` 为空数组**，不能仅凭它们判断没有图表。图和表分别取
   `figureBlocks` 与 `sections[].figures/tables`。
3. **`manifest.sections` 可能只有单节**（`noSections: true`），
   此时章节结构要从 `content_list.json` 里 `text_level` 非空的 text 块取，不是从 sections 取。

`figureBlocks` 里既无 `labelHints` 也无 `captionHints` 的块，脚本默认视为可能的 logo/图标并过滤，报告过滤数量。无图注的有效图也可能被过滤，核对或查看全部时加 `--all`。

## 可信读取与提问上下文

- `query_zotero.py` 仅读源库文件，复制主库及存在的 WAL；复制前后与副本哈希一致才交给 SQLite，验证完整性后建立内存快照。避免直接加锁运行中的源库，也不只复制主库。连续变化时有界重试并报告，不读不一致副本。
- `--key KEY --annotations --json` 返回完整高亮 `text`、真正文字批注 `comment`、页码和附件身份；两者分别解读，不把高亮原文称作用户观点。
- 读取关联问答用 `python scripts/read_zotero_context.py --key KEY`，默认输出 JSON。只查询该论文有效 registry 对应的会话；缺表、缺字段或身份不明时输出具体限制，不把不可读误称为没有记录。
- 原始消息按会话分支保留。跨分支同内容、同时间戳仅列为可能继承的重复候选；确认共享消息身份／Fork 继承再去重。同一用户在不同时间的再次追问仍是理解卡点，不能仅按文本删掉。
- selected_text／选中文本上下文为空时，依据问题本身定位，不声称已读到选择的原文。插件回答是待核线索，科学结论回原文验证。
- `has_pdf` 指附件记录，`has_mineru` 指目录关联，不等于文件存在或全文可读；实际使用前检查目标文件。
- `--read` 的 full.md 可能遗漏 PDF 脚注。关键公式、表注、页脚用 `--page N`（含 page_footnote）和原 PDF 按需核验；缺失的说明写“未核实”，不自行补为作者实现。
- 来源库位置默认 `~/Zotero`，可由 `ZOTERO_ROOT` 覆盖；MinerU 由 `MINERU_BASE` 覆盖。Windows 脚本输出 UTF-8。
- 临时副本自动清理；系统临时目录不可写时可设 `ZOTFLOW_TEMP_DIR` 为项目 `.build/` 下已存在的可写临时目录，不把数据库副本放文献库或仓库根。
