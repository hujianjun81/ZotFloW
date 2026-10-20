# -*- coding: utf-8 -*-
"""配图体检：找出笔记里真正损坏的图片引用，并与 MinerU 图清单比对。

用法：
    py scripts/audit_figures.py --vault "<notes-directory>"
    py scripts/audit_figures.py --vault <vault> --selftest

判读要点（重要）：
    「阶段二 · 未引用 N」**不等于**丢了 N 张图。figures/ 里的图是早期导入流程
    放进来的，文件名不是内容哈希，且常被重新编码（sha256 变、像素尺寸不变），
    综述笔记也不会嵌入全部图。真正的损坏只有一种：**死链**。
    要确认某张"未引用"的图是否真的缺，得比像素尺寸或肉眼比对，不能只看哈希。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REF_EMBED = re.compile(r"!\[\[([^]]+?)\]\]")
REF_IMG = re.compile(r'<img\s[^>]*?src="([^"]+)"')
KEY = re.compile(r"Zotero Key\*{0,2}\s*[:：]\s*([A-Z0-9]{8})")


def mineru_base():
    root = os.environ.get("ZOTERO_ROOT") or os.path.join(os.path.expanduser("~"), "Zotero")
    return os.environ.get("MINERU_BASE") or os.path.join(root, "llm-for-zotero-mineru")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def mineru_index(base):
    """itemKey -> (dirname, [figure filenames]) — unlabelled blocks are logos/icons."""
    idx = {}
    if not os.path.isdir(base):
        return idx
    for sub in os.listdir(base):
        src = os.path.join(base, sub, "_llm_source.json")
        if not os.path.isfile(src):
            continue
        try:
            key = json.load(open(src, encoding="utf-8")).get("parentItemKey")
        except Exception:
            continue
        if not key:
            continue
        figs = []
        man = os.path.join(base, sub, "manifest.json")
        if os.path.isfile(man):
            try:
                blocks = json.load(open(man, encoding="utf-8")).get("figureBlocks", [])
            except Exception:
                blocks = []
            for b in blocks:
                if not b.get("labelHints") and not b.get("captionHints"):
                    continue
                for p in b.get("imagePaths") or []:
                    figs.append(os.path.basename(p))
        idx[key] = (sub, figs)
    return idx


def resolves(ref, vault, note_path):
    ref = ref.split("|")[0].strip()
    for cand in (os.path.join(vault, ref),
                 os.path.join(os.path.dirname(note_path), ref),
                 os.path.join(vault, "figures", os.path.basename(ref))):
        if os.path.isfile(cand):
            return True
    return False


def notes_of(vault):
    out = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for fn in sorted(files):
            if fn.endswith(".md"):
                out.append(os.path.join(root, fn))
    return sorted(out)


def collect(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        txt = fh.read()
    return txt, REF_EMBED.findall(txt) + REF_IMG.findall(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="Obsidian 库根目录")
    ap.add_argument("--selftest", action="store_true",
                    help="先在临时目录验证检测器有效，再体检")
    args = ap.parse_args()
    vault = os.path.abspath(args.vault)
    base = mineru_base()

    if args.selftest:
        with tempfile.TemporaryDirectory(prefix="zotflow_figures_") as folder:
            probe = os.path.join(folder, "_selftest_figures.md")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write('# selftest\n\n![[deadbeef00ffff.jpg]]\n'
                         '<img src="figures/notexist123.jpg" />\n')
            _, refs = collect(probe)
            caught = sum(1 for r in refs if not resolves(r, folder, probe))
        print(f"[自测] 植入 2 个死链，检出 {caught} 个 -> "
              + ("检测器有效 ✔" if caught == 2 else "检测器失效 ✘ 结果不可信"))
        if caught != 2:
            return 1
        print()

    idx = mineru_index(base)
    print("=" * 78)
    print("阶段一 · 死链检测（唯一能判定「真损坏」的指标）")
    print("=" * 78)
    total = 0
    rows = []
    for path in notes_of(vault):
        txt, refs = collect(path)
        dead = [r for r in refs if not resolves(r, vault, path)]
        total += len(dead)
        m = KEY.search(txt)
        key = m.group(1) if m else None
        rows.append((os.path.relpath(path, vault).replace("\\", "/"),
                     len(refs), dead, txt, key))
        if dead:
            print(f"\n  {os.path.relpath(path, vault)}   ({len(dead)}/{len(refs)} 死链)")
            for d in dead:
                print(f"      ✘ {d}")
    print("\n  全部引用均可解析 ✔（无损坏）" if total == 0 else f"\n  合计死链: {total}")

    print()
    print("=" * 78)
    print("阶段二 · 与 MinerU 比对「源里有图但本笔记未引用」")
    print("        注意：这不是损坏指标，多为重编码/编辑取舍 —— 见文件头说明")
    print("=" * 78)
    vault_md5 = {}
    figdir = os.path.join(vault, "figures")
    if os.path.isdir(figdir):
        for fn in os.listdir(figdir):
            fp = os.path.join(figdir, fn)
            if os.path.isfile(fp):
                vault_md5.setdefault(md5(fp), []).append(fn)

    for name, nrefs, _dead, txt, key in rows:
        if not key or key not in idx:
            continue
        sub, figs = idx[key]
        if not figs:
            continue
        identical = sum(1 for f in figs
                        if os.path.isfile(os.path.join(base, sub, "images", f))
                        and md5(os.path.join(base, sub, "images", f)) in vault_md5)
        print(f"  {name:<40} MinerU {sub}: {len(figs)} 图, "
              f"字节级同源 {identical}, 其余 {len(figs) - identical} 需人工确认")
    print("\n  提示：'需人工确认'包含重新编码过的同一张图与编辑取舍，不是缺图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
