# -*- coding: utf-8 -*-
"""笔记体检：按模版与写作约定逐条核对精读笔记，只报可执行的问题。

用法：
    py scripts/lint_note.py --note "<库根>/paper-note.md"
    py scripts/lint_note.py --vault "<库根>"
    py scripts/lint_note.py --vault "<库根>" --selftest
    py scripts/lint_note.py --note <笔记.md> --force      # 强制检查非论文笔记

判读要点（重要）：
    1. **FAIL 才需要改**；WARN 是"看一眼"。退出码 1 当且仅当出现 FAIL。
    2. 本脚本只认结构化信号，**不判断内容对错**——"有解读段"不等于"解读对"。
    3. **误报是本工具最大的失败模式。** 每条检查的注释里都写明"故意不报什么"。
       宁可 WARN 不可错杀：把好笔记判死的检查器会被忽略，然后整个机制就失效。
    4. `--selftest` 不通过（检测器失效 ✘）时结果一律不可信，先修检测器再读数。
    5. **优先按 zotflow_type 判产物，旧论文笔记兼容 Zotero Key。** 无 Key 的计划、代码比较、学习记录跳过 1~8 项风格检查——它们本来就允许用"你/我"。
       但第 9~12 项（死链、链接目标）对所有笔记都跑，那是客观缺陷。
    6. 首次跑全库会有一批 FAIL，是**存量笔记的旧习惯**（图后接标题、引用了式号
       却没写 \\tag、正文用"你/我"），不是脚本坏了。存量不回改，报告仅供参考；
       1~8 项真正的用途是给**新生成的笔记**当交付闸门（--note 模式）。
"""
import argparse
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BAR = "=" * 78

# ---------------------------------------------------------------- 图引用
# 三种语法都要认。![[ ]] 必须排在 ![]( ) 之前试，否则图片嵌入会被当成链接。
IMAGE_ANY = re.compile(
    r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]"          # Obsidian 图片嵌入
    r"|<img\b[^>]*?src\s*=\s*[\"']([^\"']+)[\"']"  # HTML img
    r"|!\[[^\]]*\]\(([^)\s]+)\)",                 # markdown 图片
    re.I)
# 整行只有一张图（用于"连排图"识别）
IMAGE_LINE = re.compile(
    r"^\s*(?:!\[\[[^\]]+\]\]|<img\b[^>]*>|!\[[^\]]*\]\([^)]*\))\s*$", re.I)
# 图注：**图2: …** / 图2：… / *Fig.1 …* / Table 3 …。只对"图后首个非空行"试匹配。
CAPTION = re.compile(
    r"^\s*(?:[*_]{1,2})?\s*(?:图|表)\s*(?:[0-9]|[一二三四五六七八九十]|[:：])"
    r"|^\s*(?:[*_]{1,2})?\s*(?:Fig(?:ure)?|Table|Eq(?:uation)?)\b", re.I)

# ---------------------------------------------------------------- 结构
H2 = re.compile(r"^##\s+(.*?)\s*$")
H_ANY = re.compile(r"^\s{0,3}#{1,6}\s")
HRULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
ABSTRACT = re.compile(r"^\s*>\s*\[!\s*(?:abstract|summary)\s*\][^\n]*", re.I | re.M)

# ---------------------------------------------------------------- 公式
# 式13 / 式 13 / 式(13) / 式（13） / 式1,2 / Eq. 13 / Eq.(13) / 公式 13
# 负向后顾挡住"方式 3 / 模式 2 / 格式 5 / 形式 4 / 样款程"。
CITE = re.compile(
    r"(?<![方模格形样款程])式\s*[（(]?\s*(\d{1,3}(?:\s*[,，、]\s*\d{1,3})*)(?!\d)"
    r"|(?<![A-Za-z])Eq(?:uation)?s?\.?\s*[（(]?\s*(\d{1,3}(?:\s*[,，、]\s*\d{1,3})*)(?!\d)",
    re.I)
TAG = re.compile(r"\\tag\s*\{([^}]*)\}")
MATH_LINE = re.compile(r"^\s*\$\$")
MATH_SKIP = re.compile(r"<!--\s*lint:\s*skip-math-intuition\s*-->")

# ---------------------------------------------------------------- 元数据 / 标注 / 人称词
KEY = re.compile(r"Zotero\s*Key\*{0,2}\s*[:：]\s*\**\s*([^\s*|）)]+)", re.I)
KEY_OK = re.compile(r"^[A-Z0-9]{8}$")
ANCHOR = re.compile(r"\[论文[^\]]*\]|\[推断\]|\[论文未涉及\]"
                    r"|（\s*§|\(\s*§|Table\s*[IVX0-9]|表\s*[IVX0-9]")
PERSON = re.compile(r"你|我|咱")
# 豁免词：自我/我国/我方/忘我/无我/迷你 里的"我""你"不是人称指代
PERSON_EXEMPT = re.compile(r"自我|我国|我方|忘我|无我|迷你")
# 跨笔记链接：负向后顾排除 ![[图片]]
LINK_OUT = re.compile(r"(?<!!)\[\[([^\]|#]+)")

# ---------------------------------------------------------------- 去噪（等长空白替换，保住行号）
FENCE = re.compile(r"^[ \t]*(?:```|~~~)[\s\S]*?^[ \t]*(?:```|~~~)"
                   r"|^[ \t]*(?:```|~~~)[\s\S]*\Z", re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")
HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
FRONTMATTER = re.compile(r"\A---\n[\s\S]*?\n---\n")

# ---------------------------------------------------------------- 模版章节
# 别名用 re.search 匹配任一 H2 标题，不是一一对应——所以"一、研究背景与动机"
# 同时满足"研究背景"和"研究动机"两个槽位。
TECH_SLOTS = [
    ("研究背景",       r"研究背景|背景",                  "WARN"),
    ("研究动机",       r"研究动机|动机",                  "WARN"),
    ("拟解决的问题",   r"拟解决的问题|问题定义|要解决的问题", "WARN"),
    ("方法与架构",     r"方法与架构|方法|架构|模型设计",     "FAIL"),
    ("实验结果",       r"实验结果|实验|评估",              "FAIL"),
    ("关键发现与讨论", r"关键发现|发现与讨论|讨论",          "WARN"),
    ("与本人研究的关联", r"与本人研究|与.*关联|关联",        "WARN"),
    ("后续方向",       r"后续方向|未来方向|展望",           "WARN"),
    ("附录：个人思考", r"^附录",                          "FAIL"),
]
SURVEY_SLOTS = [
    ("综述范围与定位",   r"范围|定位",                     "FAIL"),
    ("领域全景与分类体系", r"全景|分类|体系|概览",           "FAIL"),
    ("各分支核心技术梳理", r"核心技术|技术|方法|路线|应用|一览", "FAIL"),
    ("技术演进脉络",     r"演进|脉络|时间线|历程|发展",       "WARN"),
    ("开放问题与未来方向", r"开放问题|未来方向|挑战|局限|展望",  "FAIL"),
    ("与本人研究的关联",  r"与本人研究|与.*关联|关联",         "FAIL"),
    ("文献阅读清单",     r"文献|阅读清单|清单",              "WARN"),
    ("附录：个人思考",   r"^附录",                          "FAIL"),
]
SURVEY_MARK = r"范围|定位|全景|分类体系|技术演进|开放问题|文献阅读清单|阅读清单|综述"


# ================================================================ 基础设施
def blank(m):
    """把匹配到的内容换成等长空白，行号列号全部保持不变。"""
    return re.sub(r"[^\n]", " ", m.group(0))


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def strip_noise(lines):
    """剥掉代码块 / 行内代码 / HTML 注释 / frontmatter，避免误触发。

    正文检查（式引用、人称词）必须走这一步：笔记里贴的代码样本会带"式 9"
    这类字样，模板残留的 <!-- 对我的研究方向 … --> 里更是直接有"我"。
    """
    txt = "\n".join(lines)
    txt = FRONTMATTER.sub(blank, txt)
    txt = FENCE.sub(blank, txt)
    txt = INLINE_CODE.sub(blank, txt)
    txt = HTML_COMMENT.sub(blank, txt)
    return txt.split("\n")


def mask_appendix(lines):
    """从 `## 附录：个人思考` 起到下一个 `## ` 或 EOF，整段抹白。

    附录是留给用户手写的空间，人称词豁免必须覆盖它。注意要遮到下一个 `## `
    而不是第一个 `#`——附录内部可以有三级标题，遮到 `#` 会漏掉它们。
    只豁免匹配附录名称的 H2，不豁免名称中偶然出现「附录」的其他章节。
    """
    out, masking = [], False
    for line in lines:
        if not masking and re.match(r"^##\s*附录", line):
            masking = True
        elif masking and re.match(r"^##\s", line):
            masking = False
        out.append(" " * len(line) if masking else line)
    return out


def prepare(lines, drop_appendix=True):
    out = strip_noise(lines)
    return mask_appendix(out) if drop_appendix else out


def resolves(ref, vault, note_path):
    """逐字复用 audit_figures.py 的解析顺序：库根 → 笔记同级 → figures/。

    第三条分支兼容粘贴后集中保存在 vault/figures/ 下的图片。
    """
    ref = ref.split("|")[0].strip().replace("\\", "/")
    for cand in (os.path.join(vault, ref),
                 os.path.join(os.path.dirname(note_path), ref),
                 os.path.join(vault, "figures", os.path.basename(ref))):
        if os.path.isfile(cand):
            return True
    return False


def find_vault(note_path):
    """`--note` 模式下推断库根。

    不能取 `os.path.dirname(note)`——重组前笔记都在库根，那恰好等价；
    笔记移入子目录之后，
    笔记同级就不再是库根，`resolves()` 的 `vault/figures/` 分支会去找
    `<子目录>/figures/`，把每一张图都误报成死链。

    改为向上找**库的标记**，优先级：
      1. 含 `.obsidian/` 的最近祖先——Obsidian 库根的无歧义判据
      2. 否则含 `figures/` 的最近祖先——没装 `.obsidian` 的裸目录也能用
      3. 都没有则退回笔记同级（旧行为，至少不会比修前更差）
    """
    start = os.path.dirname(os.path.abspath(note_path))
    for marker in (".obsidian", "figures"):
        cur = start
        while True:
            if os.path.isdir(os.path.join(cur, marker)):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return start


def h2_list(lines):
    out = []
    for i, line in enumerate(lines):
        m = H2.match(line)
        if m:
            out.append((i + 1, m.group(1)))
    return out


def note_type(lines):
    """判类型只看结构，**绝不看文件名**。

    文件名带"综述"也可能使用技术型模板，不能据文件名判断。
    """
    titles = [t for _, t in h2_list(lines)]
    hits = sum(1 for t in titles if re.search(SURVEY_MARK, t))
    if hits >= 2:
        return "survey", f"命中 {hits} 个综述型章节标记"
    return "technical", f"综述型标记仅 {hits} 个"


def is_paper_note(lines, txt):
    """Explicit artifact type takes precedence; legacy reading notes use Key."""
    if lines and lines[0].strip() == '---':
        for line in lines[1:]:
            if line.strip() == '---':
                break
            match = re.match(r'^zotflow_type:\s*[\'"]?([\w-]+)', line)
            if match:
                if match.group(1) in {'resource-search', 'research-review', 'paper-comparison', 'experiment-review'}:
                    return False
                if match.group(1) == 'close-reading':
                    return True
    return bool(KEY.search(txt))


# ================================================================ 检查 1：每图必解读
def find_interpretation(lines, start, window=6):
    """从 start 之后找第一段"人写的解读"。

    返回 ("text", 行号) / ("heading", 行号) / (None, None)。

    不计入 window 也不终止搜索的行：空行、--- 分隔线、另一个图引用行。
    连排图时每张图各自独立找解读，互不借光；若一整簇图共用一段解读，
    由调用方追加一条 WARN（**故意不判 FAIL**——既有笔记里连排图确实是一组
    子图，要求每张各写一段会制造大量噪声）。

    **`>` 引用行一律算解读。** 既有笔记里三种真实解读形态就是引用块
    （如「📐 上图依据 §… 重绘」）、表格、列表。
    把 `>` 判成图注会连错三处。

    故意不报的：表格单元内/列表项里的图、无扩展名的引用（那是 ![[笔记名]]
    笔记嵌入不是图）、http(s):// 与 data: 图源。
    """
    skipped = 0
    for j in range(start + 1, min(start + 1 + window + skipped, len(lines))):
        s = lines[j].strip()
        if not s or HRULE.match(s):
            skipped += 1
            continue
        if IMAGE_LINE.match(lines[j]):
            skipped += 1
            continue
        if H_ANY.match(lines[j]):
            return "heading", j + 1
        # 图后首个非空行若是图注，跳过它，继续找解读段
        if skipped == 0 and CAPTION.match(lines[j]):
            continue
        return "text", j + 1
    return None, None


def find_images(lines):
    """返回 [(行号, 引用路径)]；跳过网络图源和无扩展名的笔记嵌入。"""
    out = []
    for i, line in enumerate(lines):
        for m in IMAGE_ANY.finditer(line):
            ref = next((g for g in m.groups() if g), "")
            ref = ref.strip()
            if re.match(r"https?://|data:", ref, re.I):
                continue
            if "." not in os.path.basename(ref):
                continue          # ![[笔记名]] 是笔记嵌入，不是图
            out.append((i + 1, ref))
            break
    return out


def check_figures(lines, vault, path):
    """每张图后面必须有解读段；图后直接接标题 = FAIL。"""
    body = mask_appendix(strip_noise(lines))
    findings = []
    images = find_images(body)
    for ln, ref in images:
        kind, at = find_interpretation(body, ln - 1)
        if kind == "heading":
            findings.append(("FAIL", "每图必解读", ln,
                             f"图后未见解读段（图注后紧接标题，见第 {at} 行）"))
        elif kind is None:
            findings.append(("FAIL", "每图必解读", ln, "图后未见解读段（到文末/窗口结束都没有正文）"))
    if not findings:
        findings.append(("PASS", "每图必解读", None, f"{len(images)} 张图均有解读段"))
    return findings


# ================================================================ 检查 2/3：公式
def tags_in(txt):
    """收集全文中 \\tag{} 里的编号。

    **扫全文而不是只扫 $$ 块**：`\\tag{4}` 可能出现在行内 `$…$` 中，
    只扫 `$$` 会误判为缺失。一个 \\tag 也可能有多个号：`\\tag{26,27}`。
    """
    out = set()
    for raw in TAG.findall(txt):
        for token in re.split(r"[,，、\s]+", raw):
            m = re.match(r"(\d+)", token.strip())
            if m:
                out.add(int(m.group(1)))
    return out


def citations_in(txt):
    """返回 {式号: 首次引用行号}。"""
    out = {}
    for i, line in enumerate(txt.split("\n"), 1):
        for m in CITE.finditer(line):
            raw = m.group(1) or m.group(2) or ""
            for token in re.split(r"[,，、\s]+", raw):
                token = token.strip()
                if token.isdigit():
                    out.setdefault(int(token), i)
    return out


def check_equations(txt, raw_lines):
    """引用了「式 N」却全文没有 \\tag{N} → FAIL。

    措辞要把两种情况分开，避免误读成"公式根本没写"——实际大多数是
    "公式写了但没编号"（例如将内容写成行内 $…$）。
    """
    body = "\n".join(mask_appendix(strip_noise(raw_lines)))
    tagged = tags_in(body)
    cited = citations_in(body)
    missing = sorted(set(cited) - tagged)
    findings = [("FAIL", "公式必重现", cited[n],
                 f"式{n} 被引用但全文无 \\tag{{{n}}} —— 公式未以带编号的公式块重现")
                for n in missing]
    if not findings:
        extra = sorted(tagged - set(cited))
        note = f"，另有 {len(extra)} 个 \\tag 未被正文引用" if extra else ""
        findings.append(("PASS", "公式必重现", None,
                         f"引用的 {len(cited)} 个式号均有对应 \\tag{note}"))
    return findings


def math_blocks(lines):
    """返回 [(起始行, 结束行)]。单行 `$$..$$` 与多行块都要认。"""
    out, start = [], None
    for i, line in enumerate(lines):
        if not MATH_LINE.match(line):
            continue
        if start is None:
            if len(re.findall(r"\$\$", line)) >= 2:
                out.append((i + 1, i + 1))      # 单行块
            else:
                start = i + 1
        else:
            out.append((start, i + 1))
            start = None
    if start is not None:
        out.append((start, len(lines)))
    return out


def check_math_intuition(raw_lines):
    """$$ 块后应当有解释段。**只对 $$ 块生效，行内 $…$ 故意不查**——
    行内公式可能与段落共用解释，不逐个检查。
    豁免标记：<!-- lint:skip-math-intuition -->（Obsidian 阅读视图里不可见）
    """
    body = mask_appendix(strip_noise(raw_lines))
    findings = []
    for start, end in math_blocks(body):
        if MATH_SKIP.search(body[start - 1]) or (start >= 2 and MATH_SKIP.search(body[start - 2])):
            continue
        nxt = None
        for j in range(end, min(end + 3, len(body))):
            s = body[j].strip()
            if not s or MATH_LINE.match(s):
                continue
            nxt = s
            break
        if nxt is None or H_ANY.match(nxt):
            findings.append(("WARN", "公式必直觉", start,
                             f"$$ 公式块（第 {start}-{end} 行）后未见解释段"))
    if not findings:
        findings.append(("PASS", "公式必直觉", None, f"{len(math_blocks(body))} 个公式块均有解释"))
    return findings


# ================================================================ 检查 4~8
def check_anchors(txt):
    """统计源头标注。**只要出现就 PASS**，不要求逐句标注——那会变成噪声。"""
    hits = ANCHOR.findall(txt)
    bracketed = len(re.findall(r"\[论文[^\]]*\]|\[推断\]|\[论文未涉及\]", txt))
    n = len(hits)
    if n == 0:
        return [("WARN", "论断锚点", None,
                 "0 处源头标注（括注 图N/§ 0，方括号 [论文…]/[推断] 0）")]
    return [("PASS", "论断锚点", None, f"{n} 处源头标注（其中方括号标记 {bracketed}）")]


def person_hits(line):
    """返回该行"你/我/咱"的列号，已剔除 自我/我国/迷你 这类豁免词。"""
    exempt = set()
    for m in PERSON_EXEMPT.finditer(line):
        exempt.update(range(m.start(), m.end()))
    return [m.start() for m in PERSON.finditer(line) if m.start() not in exempt]


def check_person(lines, raw_lines):
    """正文禁人称词（本人 放行，附录整段豁免）。

    附录及其三级标题用于用户个人记录，不应用正文的人称限制。
    """
    body = prepare(raw_lines)
    findings = []
    for i, line in enumerate(body, 1):
        hits = person_hits(line)
        if hits:
            findings.append(("FAIL", "禁人称词", i,
                             f"正文含人称词「{line[hits[0]]}」：{line.strip()[:52]}"))
    if not findings:
        findings.append(("PASS", "禁人称词", None, "正文无人称词（附录已豁免）"))
    return findings


def chapter_misses(lines, kind):
    """返回 (FAIL 缺失, WARN 缺失)。别名用 re.search，允许多对一。"""
    slots = SURVEY_SLOTS if kind == "survey" else TECH_SLOTS
    titles = [re.sub(r"^[一二三四五六七八九十]+、", "", t).strip() for _, t in h2_list(lines)]
    hard, soft = [], []
    for name, alias, level in slots:
        if not any(re.search(alias, t) for t in titles):
            (hard if level == "FAIL" else soft).append(name)
    return hard, soft


def check_chapters(lines, kind):
    hard, soft = chapter_misses(lines, kind)
    findings = [("FAIL", "章节完整性", None, f"缺核心章节：{name}") for name in hard]
    findings += [("WARN", "章节完整性", None, f"缺章节：{name}") for name in soft]
    if not findings:
        findings.append(("PASS", "章节完整性", None, "模版章节齐全"))
    return findings


def check_abstract(txt):
    if ABSTRACT.search(txt):
        return [("PASS", "速览块", None, "有速览块")]
    return [("WARN", "速览块", None,
             "缺 > [!abstract] 速览（新约定，存量笔记普遍没有）")]


def check_key(txt):
    m = KEY.search(txt)
    if not m:
        return [("FAIL", "Zotero Key", None, "未找到 Zotero Key")]
    val = m.group(1).strip()
    if not KEY_OK.match(val):
        return [("FAIL", "Zotero Key", None,
                 f"格式不对：'{val}'（应为 8 位大写字母/数字）")]
    return [("PASS", "Zotero Key", None, val)]


# ================================================================ 检查 9~12
def check_dead_links(lines, vault, path):
    """图引用必须能解析。复用 audit_figures.py 的解析顺序。"""
    findings = []
    for ln, ref in find_images(lines):
        if not resolves(ref, vault, path):
            findings.append(("FAIL", "图引用可解析", ln, f"死链：{ref}"))
    if not findings:
        findings.append(("PASS", "图引用可解析", None, "图引用均可解析"))
    return findings


def check_link_targets(lines, vault, path, existing):
    """所有 [[…]] 目标必须存在为文件。

    能抓到畸形 `[[[Paper]]]`（多一个左括号，目标成了 `[Paper`），以及索引里的
    `[[链接]]` / `[[双向链接]]`——把链接语法词当成了笔记名。
    """
    body = prepare(lines, drop_appendix=False)
    findings = []
    for i, line in enumerate(body, 1):
        for target in LINK_OUT.findall(line):
            t = target.strip().split("|")[0].strip()
            if not t:
                continue
            if t in existing:
                continue
            if t.startswith("[") or t.endswith("]"):
                findings.append(("FAIL", "链接目标有效", i,
                                 f"畸形链接 [[{t}]]（多半是方括号写多了）"))
            else:
                findings.append(("WARN", "链接目标有效", i,
                                 f"链接目标不存在：[[{t}]]"))
    return findings


def check_moc_coverage(path, vault, existing, moc_links, moc_path=None):
    """只检查用户显式指定的索引，不假设所有笔记库使用相同文件名。"""
    if not moc_path:
        return []
    name = os.path.splitext(os.path.basename(path))[0]
    if name in moc_links or (moc_path and os.path.abspath(path) == os.path.abspath(moc_path)):
        return []
    return [("WARN", "MOC 收录", None, "未被指定 MOC 链接收录")]


# ================================================================ 单篇体检
def lint_note(path, vault, kind="auto", force=False):
    lines = read_lines(path)
    txt = "\n".join(lines)
    findings, info = [], {}

    if kind == "auto":
        kind, why = note_type(lines)
        info["type_why"] = why
    info["type"] = kind

    paper = force or is_paper_note(lines, txt)
    info["paper"] = paper

    # 1~8 只对论文笔记跑；9~12 对所有笔记跑（客观缺陷与写作风格无关）
    if paper:
        findings += check_figures(lines, vault, path)
        findings += check_equations(txt, lines)
        findings += check_math_intuition(lines)
        findings += check_anchors("\n".join(mask_appendix(strip_noise(lines))))
        findings += check_person(lines, lines)
        findings += check_chapters(lines, kind)
        findings += check_abstract(txt)
        findings += check_key(txt)
    findings += check_dead_links(lines, vault, path)
    return findings, info


def worst_level(findings):
    for lv in ("FAIL", "WARN"):
        if any(f[0] == lv for f in findings):
            return lv
    return "PASS"


# ================================================================ 输出
MARK = {"FAIL": "✘ FAIL", "WARN": "⚠ WARN", "PASS": "✔ PASS"}


def emit(findings, rel, max_hits=8):
    rel = rel.replace("\\", "/")
    by_check, order = {}, []
    for lv, check, ln, msg in findings:
        if check not in by_check:
            by_check[check] = []
            order.append(check)
        by_check[check].append((lv, ln, msg))
    for check in order:
        items = by_check[check]
        lv = worst_level([(i[0], "", 0, "") for i in items])
        shown = items[:max_hits]
        for l, ln, msg in shown:
            loc = f"{rel}:{ln}" if ln else f"{rel}:—"
            print(f"  {MARK[l]}  {check:<12} {loc:<40} {msg}")
        if len(items) > max_hits:
            print(f"         … 共 {len(items)} 处（已列前 {max_hits}）")


# ================================================================ 自测
BAD = """# 自测样本 · 必错
**作者**: Self Test

![[deadbeef00ffff.jpg]]
**Fig.1: 图注。**
### 小节标题

式13、式 19 与 Eq.(7) 都引用了，全文没有任何 tag。

$$
a=b
$$
## 五、实验结果

我建议你直接看，咱不用管。
"""

GOOD = """# 自测样本 · 必对
**Zotero Key**: ABCD1234

> [!abstract] 速览
> 一句话说清。

## 一、研究背景
## 二、研究动机
## 三、拟解决的问题

![[ok.jpg]]
**Fig.1: 图注。**
这是对图的解读：可以看出 X 与 Y 的关系 [论文 §III]。

式13 引用，下面重现：

$$E = mc^2 \\tag{13}$$

这公式的直觉是：质量与能量等价 [推断]。

自我批评、我国、迷你、忘我 —— 均不得误报。
## 四、方法与架构
## 五、实验结果
## 六、关键发现与讨论
## 七、与本人研究的关联
## 八、后续方向
## 附录：个人思考

我在附录里随便写，你也可以。
"""


def selftest():
    """植入诱饵验证检测器本身有效，**在临时目录里跑**。

    在 --vault 模式下往库里写探针会被当成真笔记体检，所以用退出时自动清理的 TemporaryDirectory。

    第二半（误报诱饵 + 活链阳性对照）不能省：一个"什么都报"的检测器
    同样能通过第一半。`ok.jpg` 是活链的阳性对照——如果 resolves() 恒为
    False，死链断言反而会"通过"。
    """
    with tempfile.TemporaryDirectory(prefix="zotflow_lint_") as tmp:
        os.makedirs(os.path.join(tmp, "figures"), exist_ok=True)
        open(os.path.join(tmp, "figures", "ok.jpg"), "wb").close()

        def write(name, body):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(body)
            return p

        bad = write("bad.md", BAD)
        good = write("good.md", GOOD)

        # bad 无 Zotero Key，默认会被判定为"非论文笔记"而跳过 1~8 项；
        # 这里用 force=True 把全部检查打开，才能验证检测器本身。
        bf, _ = lint_note(bad, tmp, "technical", force=True)
        gf, _ = lint_note(good, tmp, "technical")   # good 有 Key，走正常路径
        bfail = [f for f in bf if f[0] == "FAIL"]
        gfail = [f for f in gf if f[0] == "FAIL"]
        gwarn = [f for f in gf if f[0] == "WARN"]

        def has(fs, check, level="FAIL"):
            return any(f[0] == level and f[1] == check for f in fs)

        checks = [
            ("每图必解读   图后接标题", has(bfail, "每图必解读")),
            ("公式必重现   式13/19/7 全缺", len([f for f in bfail if f[1] == "公式必重现"]) == 3),
            ("公式必直觉   $$ 块无解释", has(bf, "公式必直觉", "WARN")),
            ("禁人称词     正文有我/你/咱", has(bfail, "禁人称词")),
            ("章节完整性   缺核心章节", has(bfail, "章节完整性")),
            ("Zotero Key   缺失", has(bfail, "Zotero Key")),
            ("图引用可解析 死链", has(bfail, "图引用可解析")),
            ("误报-豁免词 自我/我国/迷你/忘我", not has(gf, "禁人称词")),
            ("误报-附录内人称词", not has(gf, "禁人称词")),
            ("误报-活链 ![[ok.jpg]] 不得判死", not has(gfail, "图引用可解析")),
            ("误报-良构笔记 零 FAIL", not gfail),
            ("误报-良构笔记 零 WARN", not gwarn),
        ]
        print("[自测] 植入必错样本 7 类 + 误报诱饵 4 类 …")
        ok = True
        for name, passed in checks:
            print(f"  {'✔' if passed else '✘'} {name}")
            ok &= passed
        print()
        if ok:
            print("检测器有效 ✔")
        else:
            print("检测器失效 ✘ 结果不可信，先修检测器")
        return 0 if ok else 1


# ================================================================ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", help="体检单篇笔记")
    ap.add_argument("--vault", help="体检整个库（含跨笔记链接）")
    ap.add_argument("--moc", help="全库检查时可选的已有索引文件，相对库根或绝对路径")
    ap.add_argument("--type", default="auto", choices=["auto", "technical", "survey"])
    ap.add_argument("--force", action="store_true", help="强制检查非论文笔记")
    ap.add_argument("--max-hits", type=int, default=8, help="每项检查最多列几行")
    ap.add_argument("--selftest", action="store_true", help="先验证检测器有效再体检")
    args = ap.parse_args()

    if args.selftest:
        rc = selftest()
        if rc:
            return rc
        print()
        if not args.note and not args.vault:
            return 0

    if not args.note and not args.vault:
        ap.error("需要 --note 或 --vault")

    vault = find_vault(args.note) if (args.note and not args.vault) else os.path.abspath(args.vault)

    if args.note:
        path = os.path.abspath(args.note)
        findings, info = lint_note(path, vault, args.type, args.force)
        rel = os.path.relpath(path, vault)
        print(BAR)
        print(f"zotflow 笔记体检 · {rel}    [{info['type']}] {info.get('type_why','')}")
        print(BAR)
        if not info["paper"]:
            print("  ⚠ 非精读产物或无 Zotero Key，仅跑链接类检查。加 --force 强制全查。\n")
        emit(findings, rel, args.max_hits)
        n_fail = len([f for f in findings if f[0] == "FAIL"])
        n_warn = len([f for f in findings if f[0] == "WARN"])
        n_pass = len({f[1] for f in findings if f[0] == "PASS"})
        print("-" * 78)
        print(f"{n_fail} FAIL, {n_warn} WARN, {n_pass} PASS   ·  退出码 {1 if n_fail else 0}")
        return 1 if n_fail else 0

    # ---- vault 模式 ----
    notes = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for fn in sorted(files):
            if fn.endswith(".md"):
                notes.append(os.path.join(root, fn))
    notes.sort()

    existing = {os.path.splitext(os.path.basename(p))[0] for p in notes}
    moc_path = os.path.join(vault, args.moc) if args.moc else None
    if moc_path and not os.path.isfile(moc_path):
        ap.error(f"MOC 文件不存在: {moc_path}")
    moc_links = set()
    if moc_path:
        with open(moc_path, encoding="utf-8", errors="replace") as fh:
            moc_links = {t.strip().split("|")[0].strip()
                         for t in LINK_OUT.findall(fh.read())}

    print(BAR)
    print(f"全库概览 · {vault}")
    print(BAR)

    rows, total_fail, total_warn = [], 0, 0
    for path in notes:
        findings, info = lint_note(path, vault, args.type, args.force)
        rel = os.path.relpath(path, vault)
        # 链接目标对所有笔记都查——坏链接恰恰长在非论文笔记里
        # （例如索引中的语法示例或畸形三层括号），
        # 放进 paper 闸门里就正好漏掉它们。
        findings += check_link_targets(read_lines(path), vault, path, existing)
        if info["paper"]:
            findings += check_moc_coverage(path, vault, existing, moc_links, moc_path)
        nf = len([f for f in findings if f[0] == "FAIL"])
        nw = len([f for f in findings if f[0] == "WARN"])
        total_fail += nf
        total_warn += nw
        rows.append((rel, info["type"], nf, nw, findings))

    print(f"\n{'笔记':<42}{'类型':<8}{'FAIL':>5}{'WARN':>6}")
    print("-" * 78)
    for rel, kind, nf, nw, _ in rows:
        flag = "" if nf == 0 else "  ←"
        print(f"{rel.replace(chr(92), '/'):<42}{kind:<8}{nf:>5}{nw:>6}{flag}")

    print()
    print(BAR)
    print("明细（只列有 FAIL/WARN 的笔记；存量笔记的 FAIL 多为旧习惯，不回改）")
    print(BAR)
    for rel, kind, nf, nw, findings in rows:
        if nf or nw:
            print(f"\n▌{rel.replace(chr(92), '/')}   [{kind}]")
            emit([f for f in findings if f[0] != "PASS"], rel, args.max_hits)

    print()
    print(f"合计 {total_fail} FAIL, {total_warn} WARN · 退出码 {1 if total_fail else 0}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
