"""
Read MinerU-parsed paper content (llm-for-zotero plugin output).

Pure filesystem access — no Zotero SQLite needed.

Usage:
  python read_mineru.py --list                              # 列出所有已解析论文
  python read_mineru.py --key PAPER001 --toc                # 目录 + 图表清单
  python read_mineru.py --key PAPER001 --read               # 输出 full.md 正文
  python read_mineru.py --key PAPER001 --read --offset 0 --limit 20000
  python read_mineru.py --key PAPER001 --page 7             # 单页原文（含该页图表位置）
  python read_mineru.py --key PAPER001 --figures            # 图清单
  python read_mineru.py --key PAPER001 --img-tags           # 可直接粘贴的 <img> 标签
  python read_mineru.py --key PAPER001 --tables             # 表清单
  python read_mineru.py --key PAPER001 --table 2            # 导出第 2 张表的表格内容
  python read_mineru.py --grep "beam management"            # 跨全部已解析论文全文检索
  python read_mineru.py --key PAPER001 --copy-figures --out <项目>/figures

--key 接受 Zotero itemKey（父条目）或 attachmentKey（附件），也接受 MinerU 目录名。
"""
import argparse
import html
import json
import os
import re
import shutil
import sys

# Windows 控制台默认 GBK，正文里的 ϕ、– 等字符会直接抛 UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ZOTERO_ROOT = os.environ.get("ZOTERO_ROOT") or os.path.join(
    os.path.expanduser("~"), "Zotero"
)
MINERU_BASE = os.environ.get("MINERU_BASE") or os.path.join(
    ZOTERO_ROOT, "llm-for-zotero-mineru"
)

# 正文块类型 -> 是否计入正文文本
BODY_TYPES = {'text', 'ref_text', 'equation', 'page_footnote', 'aside_text'}


# ---------------------------------------------------------------- index

def build_index():
    """扫描 MinerU 根目录，返回 {folder: {...}}。只读一次 _llm_source.json。"""
    index = {}
    if not os.path.isdir(MINERU_BASE):
        return index
    for folder in sorted(os.listdir(MINERU_BASE)):
        path = os.path.join(MINERU_BASE, folder)
        if not os.path.isdir(path):
            continue
        src_path = os.path.join(path, '_llm_source.json')
        if not os.path.isfile(src_path):
            continue
        try:
            with open(src_path, encoding='utf-8') as f:
                src = json.load(f)
        except (OSError, ValueError):
            continue
        index[folder] = {
            'folder': folder,
            'path': path,
            'parentItemKey': src.get('parentItemKey'),
            'attachmentKey': src.get('attachmentKey'),
            'attachmentId': src.get('attachmentId'),
            'filename': src.get('sourceFilename', ''),
            'origin': src.get('origin'),
            'parsedAt': src.get('parsedAt'),
        }
    return index


def resolve(index, key=None, folder=None):
    """按 folder / itemKey / attachmentKey 定位论文。"""
    if folder:
        if folder in index:
            return index[folder]
        raise SystemExit(f"找不到 MinerU 目录: {folder}")
    if not key:
        raise SystemExit("需要 --key 或 --folder（或先 --list 看有哪些）")
    k = key.strip()
    for entry in index.values():
        # attachmentKey 优先：独立附件（如 O-RAN 规范）没有 parentItemKey
        if k in (entry['attachmentKey'], entry['parentItemKey'], entry['folder']):
            return entry
    raise SystemExit(
        f"找不到 {key!r}。用 --list 查看可用的 itemKey / attachmentKey / 目录名。"
    )


# ---------------------------------------------------------------- load

def load_manifest(entry):
    path = os.path.join(entry['path'], 'manifest.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_content_list(entry):
    path = os.path.join(entry['path'], 'content_list.json')
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def load_full_md(entry):
    path = os.path.join(entry['path'], 'full.md')
    if not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def collect_figures(entry, manifest=None, include_unnamed=False):
    """
    汇总图片清单。figureBlocks 有页面/图注/置信度，优先使用；
    sections[].figures 作为补充（注意 allFigures/allTables 恒为空数组，不可用）。

    figureBlocks 里既没有 labelHints 也没有 captionHints 的块，实测都是 2~8 KB 的
    小图（logo/图标/邮箱图标），默认剔除。返回 (figs, dropped_count)。
    """
    manifest = manifest if manifest is not None else load_manifest(entry)
    figs = []
    seen = set()
    dropped = 0

    for fb in manifest.get('figureBlocks', []):
        paths = fb.get('imagePaths') or []
        if not paths:
            continue
        hints = fb.get('labelHints') or []
        captions = fb.get('captionHints') or []
        if not hints and not captions and not include_unnamed:
            dropped += 1
            continue
        label = hints[0] if hints else 'Figure'
        figs.append({
            'label': label,
            'caption': ' '.join(captions).strip(),
            'images': [os.path.join(entry['path'], p) for p in paths],
            'rel_paths': paths,
            'page': fb.get('pageStart'),
            'pageEnd': fb.get('pageEnd'),
            'section': fb.get('sectionHeading'),
            'confidence': fb.get('confidence'),
            'ambiguous': fb.get('ambiguous'),
        })
        seen.update(paths)

    # 补充 sections[].figures 里 figureBlocks 漏掉的
    for section in manifest.get('sections', []):
        for fig in section.get('figures', []):
            rel = fig.get('path')
            if not rel or rel in seen:
                continue
            figs.append({
                'label': fig.get('label') or fig.get('baseLabel') or 'Figure',
                'caption': (fig.get('caption') or '').strip(),
                'images': [os.path.join(entry['path'], rel)],
                'rel_paths': [rel],
                'page': fig.get('page'),
                'pageEnd': None,
                'section': section.get('heading'),
                'confidence': None,
                'ambiguous': None,
            })
            seen.add(rel)

    figs.sort(key=lambda f: (f['page'] if f['page'] is not None else 9999))
    return figs, dropped


def collect_tables(entry, manifest=None, content=None):
    """汇总表格清单，带 caption 与页号，并从 content_list 取表格内容。"""
    manifest = manifest if manifest is not None else load_manifest(entry)
    content = content if content is not None else load_content_list(entry)
    # content_list 里的表块按页序排列，用来补齐 manifest 缺失的表体
    blocks = [b for b in content if b.get('type') == 'table']

    tables = []
    seen = set()
    for section in manifest.get('sections', []):
        for tab in section.get('tables', []):
            rel = tab.get('path')
            if rel and rel in seen:
                continue
            seen.add(rel)
            body = ''
            for b in blocks:
                if b.get('img_path') == rel:
                    body = b.get('table_body') or ''
                    break
            tables.append({
                'label': tab.get('label') or tab.get('baseLabel') or 'Table',
                'caption': (tab.get('caption') or '').strip(),
                'page': tab.get('page'),
                'image': os.path.join(entry['path'], rel) if rel else None,
                'rel_path': rel,
                'body': body,
            })

    # manifest 没登记、但 content_list 里有的表
    known = {t['rel_path'] for t in tables}
    for b in blocks:
        rel = b.get('img_path')
        if rel in known:
            continue
        caption = ' '.join(b.get('table_caption') or []).strip()
        tables.append({
            'label': f'Table {len(tables) + 1}',
            'caption': caption,
            'page': b.get('page_idx'),
            'image': os.path.join(entry['path'], rel) if rel else None,
            'rel_path': rel,
            'body': b.get('table_body') or '',
        })

    tables.sort(key=lambda t: (t['page'] if t['page'] is not None else 9999))
    return tables


def summarize(entry):
    manifest = load_manifest(entry)
    content = load_content_list(entry)
    full_md = os.path.join(entry['path'], 'full.md')
    imgs_dir = os.path.join(entry['path'], 'images')
    figs, _ = collect_figures(entry, manifest)
    return {
        'folder': entry['folder'],
        'parentItemKey': entry['parentItemKey'],
        'attachmentKey': entry['attachmentKey'],
        'filename': entry['filename'],
        'pages': manifest.get('totalPages'),
        'chars': manifest.get('totalChars') or (os.path.getsize(full_md) if os.path.isfile(full_md) else 0),
        'figures': len(figs),
        'tables': len(collect_tables(entry, manifest, content)),
        'images_on_disk': len(os.listdir(imgs_dir)) if os.path.isdir(imgs_dir) else 0,
        'has_full_md': os.path.isfile(full_md),
    }


# ---------------------------------------------------------------- render

def render_page(entry, page_idx):
    """按页输出 content_list 里的块，图表以占位符标出。"""
    content = load_content_list(entry)
    if not content:
        raise SystemExit("该论文没有 content_list.json，无法按页输出")

    blocks = [b for b in content if b.get('page_idx') == page_idx]
    if not blocks:
        maxp = max((b.get('page_idx') or 0) for b in content)
        raise SystemExit(f"第 {page_idx} 页没有内容（该论文共 {maxp + 1} 页，注意页码从 0 开始）")

    lines = [f"# 第 {page_idx} 页", '']
    for b in blocks:
        t = b.get('type')
        if t == 'text':
            lvl = b.get('text_level')
            text = (b.get('text') or '').strip()
            if not text:
                continue
            lines.append(('#' * (lvl + 1) + ' ' + text) if lvl else text)
            lines.append('')
        elif t == 'equation':
            lines.append((b.get('text') or '').strip())
            lines.append('')
        elif t == 'image':
            caps = b.get('image_caption') or []
            lines.append(f"[图] {caps[0][:120] if caps else '(无图注)'}")
            lines.append(f"     路径: {b.get('img_path')}")
            lines.append('')
        elif t == 'table':
            caps = b.get('table_caption') or []
            body = b.get('table_body') or ''
            lines.append(f"[表] {' '.join(caps)[:120]}")
            rows = html_table_to_md(body) if body else ''
            if rows:
                lines.append(rows)
            lines.append('')
        elif t in ('ref_text', 'page_footnote', 'aside_text'):
            text = (b.get('text') or '').strip()
            if text:
                lines.append(text)
                lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def html_table_to_md(body):
    """把 MinerU 的 <table> HTML 转成 markdown 管道表。"""
    if not body:
        return ''
    text = re.sub(r'<t[dh][^>]*colspan="?(\d+)"?[^>]*>', lambda m: '|' * (int(m.group(1)) - 1), body)
    text = re.sub(r'<(td|th)[^>]*>', '| ', text)
    text = re.sub(r'</t[dh]>', ' ', text)
    text = re.sub(r'</tr>', ' |\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip().strip('|').strip()]
    return '\n'.join(lines)


def md_table_from_body(body):
    """表格内容转 markdown，首行后补分隔行。"""
    lines = html_table_to_md(body).splitlines()
    if not lines:
        return ''
    out = [lines[0]]
    if len(lines) > 1:
        cols = max(1, lines[0].count('|') - 1)
        out.append('|' + '---|' * cols)
        out.extend(lines[1:])
    return '\n'.join(out)


def img_tag(entry, fig, index):
    """生成可直接粘贴进精读笔记的 <img> 标签（文件名取自 MinerU，不手写 hash）。"""
    rel = fig['rel_paths'][0]
    name = os.path.basename(rel)
    label = fig['label']
    caption = fig['caption']

    # 图注多数以 "Fig. 1"/"Figure 1." 开头，去掉后取第一句当 alt，
    # 否则 alt 会变成 "Figure 1: Figure 1 An illustration..."
    body = re.sub(r'^\s*(fig(?:ure)?\.?\s*\d+[a-z]?)\s*[.:—-]?\s*',
                  '', caption, flags=re.IGNORECASE).strip()
    short = re.split(r'(?<=[.。])\s', body)[0][:80] if body else ''
    alt = f"{label}: {short}" if short else (caption[:80] or label)
    return (
        f'<img src="figures/{name}" '
        f'alt="{html.escape(alt, quote=True)}" style="zoom:80%;" />'
    )


def grep_all(index, pattern, context=0, limit=40):
    """跨全部已解析论文检索 full.md。"""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise SystemExit(f"正则不合法: {e}")

    hits = []
    for entry in index.values():
        md = load_full_md(entry)
        if not md:
            continue
        lines = md.splitlines()
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            hits.append({
                'folder': entry['folder'],
                'parentItemKey': entry['parentItemKey'],
                'filename': entry['filename'],
                'line': i + 1,
                'text': '\n'.join(lines[lo:hi]).strip(),
            })
            if len(hits) >= limit:
                return hits
    return hits


def detect_type(entry):
    """按 SKILL.md 的规则粗判技术型/综述型，供精读时选模版。"""
    manifest = load_manifest(entry)
    content = load_content_list(entry)
    heads = [b.get('text', '') for b in content
             if b.get('type') == 'text' and b.get('text_level')]
    title = heads[0] if heads else entry['filename']
    joined = ' '.join(heads[:12])

    survey = re.search(r'survey|review|tutorial|state-of-the-art|comprehensive|taxonomy', title, re.I)
    if survey:
        return '综述型', f'标题含 {survey.group(0)!r}'
    if re.search(r'Open Challenges|Future Directions|Comparison of', joined, re.I):
        return '综述型', '章节含综述特征（Open Challenges / Future Directions）'
    return '技术型', '无综述信号，默认技术型'


# ---------------------------------------------------------------- output

def cmd_list(index, as_json):
    rows = [summarize(e) for e in index.values()]
    rows.sort(key=lambda r: (r['filename'] or '').lower())
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print(f"没有找到已解析论文。MinerU 根目录: {MINERU_BASE}")
        return
    print(f"已解析论文 {len(rows)} 篇  ({MINERU_BASE})\n")
    print(f"{'目录':<6} {'itemKey':<9} {'附件Key':<9} {'页':>4} {'图':>3} {'表':>3}  文件")
    for r in rows:
        print(f"{r['folder']:<6} {r['parentItemKey'] or '-':<9} {r['attachmentKey'] or '-':<9} "
              f"{r['pages'] or '-':>4} {r['figures']:>3} {r['tables']:>3}  {(r['filename'] or '')[:58]}")


def cmd_toc(entry, manifest, as_json, include_unnamed=False):
    content = load_content_list(entry)
    figs, dropped = collect_figures(entry, manifest, include_unnamed)
    tabs = collect_tables(entry, manifest, content)
    kind, why = detect_type(entry)

    if as_json:
        print(json.dumps({
            'folder': entry['folder'],
            'parentItemKey': entry['parentItemKey'],
            'attachmentKey': entry['attachmentKey'],
            'filename': entry['filename'],
            'paper_type': kind,
            'pages': manifest.get('totalPages'),
            'chars': manifest.get('totalChars'),
            'headings': [{'level': b.get('text_level'), 'page': b.get('page_idx'), 'text': b.get('text')}
                         for b in content if b.get('type') == 'text' and b.get('text_level')],
            'figures': [{'label': f['label'], 'page': f['page'], 'caption': f['caption']} for f in figs],
            'tables': [{'label': t['label'], 'page': t['page'], 'caption': t['caption']} for t in tabs],
        }, ensure_ascii=False, indent=2))
        return

    print(f"# {entry['filename']}")
    print(f"目录: {entry['folder']}  |  itemKey: {entry['parentItemKey'] or '-'}  |  附件Key: {entry['attachmentKey']}")
    print(f"页数: {manifest.get('totalPages')}  |  字符: {manifest.get('totalChars')}  |  判定: {kind}（{why}）")
    print()

    print("## 目录结构")
    for b in content:
        if b.get('type') == 'text' and b.get('text_level'):
            lvl = b['text_level']
            indent = '  ' * max(0, lvl - 1)
            print(f"{indent}p{b.get('page_idx'):<3} {b.get('text')}")
    print()

    note = f"，另有 {dropped} 个无标注图块已过滤（--all 可显示）" if dropped else ""
    print(f"## 图 ({len(figs)}{note})")
    for i, f in enumerate(figs, 1):
        rel = f['rel_paths'][0]
        warn = '  ⚠ambiguous' if f.get('ambiguous') else ''
        conf = f"  conf={f['confidence']}" if f.get('confidence') is not None else ''
        print(f"[{i}] {f['label']}  p{f['page']}{warn}{conf}")
        print(f"    图注: {f['caption'][:150]}")
        print(f"    文件: {os.path.basename(rel)}")
    print()

    print(f"## 表 ({len(tabs)})")
    for i, t in enumerate(tabs, 1):
        print(f"[{i}] {t['label']}  p{t['page']}")
        print(f"    标题: {t['caption'][:150]}")
        if t['rel_path']:
            print(f"    文件: {os.path.basename(t['rel_path'])}")


def cmd_read(entry, offset, limit, as_json):
    md = load_full_md(entry)
    if not md:
        raise SystemExit("该论文没有 full.md")
    if as_json:
        print(json.dumps({'folder': entry['folder'], 'total_chars': len(md),
                          'offset': offset, 'text': md[offset:offset + limit]},
                         ensure_ascii=False))
        return
    total = len(md)
    chunk = md[offset:offset + limit]
    if offset or limit < total:
        print(f"<!-- full.md 字符 {offset}..{offset + len(chunk)} / 共 {total} -->")
    print(chunk, end='' if chunk.endswith('\n') else '\n')
    if offset + len(chunk) < total:
        print(f"\n<!-- 还有 {total - offset - len(chunk)} 字符未输出，用 --offset {offset + len(chunk)} 继续 -->")


def cmd_figures(entry, manifest, img_tags, as_json, include_unnamed=False):
    figs, dropped = collect_figures(entry, manifest, include_unnamed)
    if as_json:
        print(json.dumps([{**f, 'images': [os.path.basename(p) for p in f['images']]} for f in figs],
                         ensure_ascii=False, indent=2))
        return
    if not figs:
        print("该论文没有提取到图片。" + (f"（{dropped} 个无标注图块被过滤，--all 可显示）" if dropped else ""))
        return
    if dropped:
        print(f"<!-- 已过滤 {dropped} 个无标注图块（多为 logo/图标），--all 可显示 -->\n")
    if img_tags:
        # 直接可粘贴的标签，文件名来自 MinerU，不需要手写 hash
        print("<!-- 复制到精读笔记对应位置即可；图片需先 --copy-figures 到 figures/ -->")
        for i, f in enumerate(figs, 1):
            print(f"<!-- 图{i} | {f['label']} | p{f['page']} | {f['caption'][:100]} -->")
            for rel in f['rel_paths']:
                if rel.lower().endswith(('.jpg', '.jpeg', '.png')):
                    print(img_tag(entry, {**f, 'rel_paths': [rel]}, i))
            print()
        return
    print(f"图 {len(figs)} 张\n")
    for i, f in enumerate(figs, 1):
        print(f"[{i}] {f['label']}  p{f['page']}" + ('  [对齐存疑]' if f.get('ambiguous') else ''))
        print(f"    图注: {f['caption'][:200]}")
        if f.get('section'):
            print(f"    所属章节: {f['section']}")
        for rel in f['rel_paths']:
            exists = os.path.isfile(os.path.join(entry['path'], rel))
            print(f"    文件: {rel}{'' if exists else '  (缺失!)'}")
        print()


def cmd_tables(entry, manifest, table_no, as_json):
    tabs = collect_tables(entry, manifest)
    if table_no is not None:
        if not 1 <= table_no <= len(tabs):
            raise SystemExit(f"表序号超范围：该论文共 {len(tabs)} 张表")
        t = tabs[table_no - 1]
        if as_json:
            print(json.dumps({k: v for k, v in t.items() if k != 'body'} | {'body': t['body']},
                             ensure_ascii=False, indent=2))
            return
        print(f"# {t['label']}  (p{t['page']})")
        print(f"{t['caption']}\n")
        body = md_table_from_body(t['body'])
        print(body if body else "(该表没有抽取到结构化内容，请查看图片)")
        if t['rel_path'] and not body:
            print(f"\n图片: {os.path.basename(t['rel_path'])}")
        return

    if as_json:
        print(json.dumps([{k: v for k, v in t.items() if k != 'body'} for t in tabs],
                         ensure_ascii=False, indent=2))
        return
    if not tabs:
        print("该论文没有提取到表格。")
        return
    print(f"表 {len(tabs)} 张\n")
    for i, t in enumerate(tabs, 1):
        print(f"[{i}] {t['label']}  p{t['page']}")
        print(f"    标题: {t['caption'][:200]}")
        if t['rel_path']:
            print(f"    图片: {os.path.basename(t['rel_path'])}")
        if t['body']:
            print(f"    内容: {len(t['body'])} 字符（用 --table {i} 查看）")
        print()


def cmd_copy_figures(entry, manifest, out_dir, include_unnamed=False):
    figs, _ = collect_figures(entry, manifest, include_unnamed)
    if not figs:
        raise SystemExit("该论文没有提取到图片。")
    os.makedirs(out_dir, exist_ok=True)

    copied = skipped = 0
    missing = []
    for f in figs:
        for src in f['images']:
            name = os.path.basename(src)
            if not os.path.isfile(src):
                missing.append(name)
                continue
            dst = os.path.join(out_dir, name)
            if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
                skipped += 1
                continue
            shutil.copy2(src, dst)
            copied += 1

    print(f"目标目录: {out_dir}")
    print(f"复制 {copied} 张, 已存在跳过 {skipped} 张")
    if missing:
        print(f"缺失 {len(missing)} 张: {', '.join(missing[:5])}")
    print()
    print("<!-- 下面这些标签可直接粘贴到精读笔记，文件名已核对 -->")
    for i, f in enumerate(figs, 1):
        print(f"<!-- 图{i} | {f['label']} | p{f['page']} | {f['caption'][:100]} -->")
        for rel in f['rel_paths']:
            if rel.lower().endswith(('.jpg', '.jpeg', '.png')):
                print(img_tag(entry, {**f, 'rel_paths': [rel]}, i))
        print()


def cmd_grep(index, pattern, context, limit, as_json):
    hits = grep_all(index, pattern, context, limit)
    if as_json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return
    if not hits:
        print(f"没有命中 {pattern!r}（已检索 {len(index)} 篇）")
        return
    print(f"命中 {len(hits)} 处" + (f"（已截断，最多 {limit} 处）" if len(hits) >= limit else '') + "\n")
    for h in hits:
        print(f"[{h['folder']}] {h['parentItemKey'] or h['attachmentKey']}  L{h['line']}  {h['filename'][:50]}")
        for line in h['text'].splitlines():
            print(f"    {line[:160]}")
        print()


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description='读取 MinerU 解析的论文全文与图表')
    p.add_argument('--key', help='Zotero itemKey / attachmentKey / MinerU 目录名')
    p.add_argument('--folder', help='直接指定 MinerU 目录名')
    p.add_argument('--list', action='store_true', help='列出所有已解析论文')
    p.add_argument('--toc', action='store_true', help='目录结构 + 图表清单')
    p.add_argument('--read', action='store_true', help='输出 full.md 正文')
    p.add_argument('--offset', type=int, default=0, help='--read 起始字符位置')
    p.add_argument('--limit', type=int, default=40000, help='--read 最多输出字符数（默认 40000）')
    p.add_argument('--page', type=int, help='输出指定页的原文（页码从 0 开始）')
    p.add_argument('--figures', action='store_true', help='图清单')
    p.add_argument('--img-tags', action='store_true', help='生成可粘贴的 <img> 标签')
    p.add_argument('--tables', action='store_true', help='表清单')
    p.add_argument('--table', type=int, help='导出第 N 张表的内容')
    p.add_argument('--copy-figures', action='store_true', help='复制该论文图片到 --out 目录')
    p.add_argument('--out', help='--copy-figures 的目标目录')
    p.add_argument('--all', action='store_true',
                   help='包含无标注的图块（默认过滤掉 logo/图标类小图）')
    p.add_argument('--grep', help='跨全部已解析论文检索（正则）')
    p.add_argument('--context', type=int, default=0, help='--grep 前后文行数')
    p.add_argument('--grep-limit', type=int, default=40, help='--grep 最多命中数')
    p.add_argument('--json', action='store_true', help='输出 JSON')
    args = p.parse_args()

    index = build_index()

    if args.list:
        cmd_list(index, args.json)
        return
    if args.grep:
        cmd_grep(index, args.grep, args.context, args.grep_limit, args.json)
        return

    entry = resolve(index, args.key, args.folder)
    manifest = load_manifest(entry)

    if args.toc:
        cmd_toc(entry, manifest, args.json, args.all)
    elif args.read:
        cmd_read(entry, args.offset, args.limit, args.json)
    elif args.page is not None:
        print(render_page(entry, args.page), end='')
    elif args.img_tags:
        cmd_figures(entry, manifest, True, args.json, args.all)
    elif args.figures:
        cmd_figures(entry, manifest, False, args.json, args.all)
    elif args.tables or args.table is not None:
        cmd_tables(entry, manifest, args.table, args.json)
    elif args.copy_figures:
        if not args.out:
            raise SystemExit("--copy-figures 需要同时给出 --out <目录>")
        cmd_copy_figures(entry, manifest, args.out, args.all)
    else:
        # 默认给总览，避免什么都不输出
        cmd_toc(entry, manifest, args.json, args.all)


if __name__ == '__main__':
    main()
