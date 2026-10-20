"""
Query Zotero SQLite database to find papers.
Usage:
  python query_zotero.py --search "quantization"                    # search metadata keyword
  python query_zotero.py --author "Example Author"              # search by author
  python query_zotero.py --collection "Research"               # list papers in a collection
  python query_zotero.py --tag "foundation-model"            # search by tag
  python query_zotero.py --list-collections                  # list all collections
  python query_zotero.py --key "PAPER001"                      # get paper by item key
  python query_zotero.py --key "PAPER001" --annotations        # get paper + annotations/notes
"""
import sqlite3
import sys
import json
import argparse
import os
from pathlib import Path
import hashlib
import shutil
import tempfile

# Windows 控制台默认 GBK，标题/摘要里的 ϕ、– 等字符会直接抛 UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ZOTERO_ROOT = os.environ.get("ZOTERO_ROOT") or os.path.join(
    os.path.expanduser("~"), "Zotero"
)
DB_PATH = os.path.join(ZOTERO_ROOT, "zotero.sqlite")
MINERU_BASE = os.environ.get("MINERU_BASE") or os.path.join(
    ZOTERO_ROOT, "llm-for-zotero-mineru"
)

_MINERU_INDEX = None


def mineru_index():
    """
    扫描 MinerU 目录一次并缓存，返回 ({key: folder}, {itemKey: folder})。

    按 attachmentKey（PDF 附件）和 parentItemKey（父条目）双键索引：
    独立附件（如 O-RAN 规范）没有 parentItemKey，只有 attachmentKey 能匹配到。
    """
    global _MINERU_INDEX
    if _MINERU_INDEX is not None:
        return _MINERU_INDEX

    by_attachment, by_parent = {}, {}
    if os.path.isdir(MINERU_BASE):
        for folder in os.listdir(MINERU_BASE):
            src_path = os.path.join(MINERU_BASE, folder, '_llm_source.json')
            if not os.path.isfile(src_path):
                continue
            try:
                with open(src_path, encoding='utf-8') as f:
                    src = json.load(f)
            except (OSError, ValueError):
                continue
            if src.get('attachmentKey'):
                by_attachment[src['attachmentKey']] = folder
            if src.get('parentItemKey'):
                by_parent.setdefault(src['parentItemKey'], folder)

    _MINERU_INDEX = (by_attachment, by_parent)
    return _MINERU_INDEX


def find_mineru(item_key=None, attachment_keys=()):
    """按 itemKey / 附件 key 找 MinerU 目录，找不到返回 None。"""
    by_attachment, by_parent = mineru_index()
    for ak in attachment_keys:
        if ak and ak in by_attachment:
            return os.path.join(MINERU_BASE, by_attachment[ak])
    if item_key and item_key in by_parent:
        return os.path.join(MINERU_BASE, by_parent[item_key])
    return None


def get_db():
    """Stable DB/WAL copies avoid live locks; SQLite interprets committed frames."""
    source_path = Path(DB_PATH)
    wal_path = Path(DB_PATH + '-wal')
    paths = [source_path, wal_path]

    def fingerprints():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in paths}

    db = sqlite3.connect(':memory:')
    try:
        with tempfile.TemporaryDirectory(prefix='zotflow_', dir=os.environ.get('ZOTFLOW_TEMP_DIR')) as folder:
            copied = Path(folder)
            for attempt in range(3):
                try:
                    before = fingerprints()
                    if before[source_path.name] is None:
                        raise FileNotFoundError(DB_PATH)
                    for path in paths:
                        target = copied / path.name
                        if before[path.name] is not None:
                            shutil.copyfile(path, target)
                        elif target.exists():
                            target.unlink()
                    after = fingerprints()
                    actual = {p.name: hashlib.sha256((copied/p.name).read_bytes()).hexdigest() if (copied/p.name).exists() else None for p in paths}
                    if before == after == actual:
                        break
                except FileNotFoundError:
                    if not source_path.exists():
                        raise
            else:
                raise TimeoutError('Zotero changed during snapshot; retry after the current write finishes')
            local = sqlite3.connect(copied / source_path.name)
            try:
                if local.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise sqlite3.DatabaseError('Zotero snapshot integrity check failed')
                local.backup(db)
            finally:
                local.close()
        db.execute('PRAGMA query_only=ON')
    except Exception:
        db.close()
        raise
    return db


def search_papers(keyword=None, author=None, tag=None, collection=None, limit=20):
    """Search papers by keyword, author, tag, or collection."""
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    # Common paper types: journalArticle(22), conferencePaper(11), preprint(31), bookSection(8), thesis(37), report(34)
    paper_types = [22, 11, 31, 8, 37, 34]
    placeholders = ','.join(['?'] * len(paper_types))
    conditions = [f"items.itemTypeID IN ({placeholders})"]
    params = paper_types.copy()

    if keyword:
        conditions.append("(itemDataValues.value LIKE ? OR items.key LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if author:
        conditions.append("creators.lastName LIKE ?")
        params.append(f"%{author}%")
    if tag:
        conditions.append("tags.name LIKE ?")
        params.append(f"%{tag}%")
    if collection:
        conditions.append("collections.collectionName LIKE ?")
        params.append(f"%{collection}%")

    where = " AND ".join(conditions)

    query = f"""
        SELECT DISTINCT
            items.key AS itemKey,
            items.itemID,
            (SELECT idv.value FROM itemData d
             JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'title'
             LIMIT 1) AS title,
            COALESCE(
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='publicationTitle' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='proceedingsTitle' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='conferenceName' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='bookTitle' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='repository' LIMIT 1)
            ) AS venue,
            (SELECT idv.value FROM itemData d
             JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'date'
             LIMIT 1) AS date,
            COALESCE(
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='DOI' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='url' LIMIT 1)
            ) AS doiOrUrl,
            GROUP_CONCAT(DISTINCT creators.lastName || ', ' || creators.firstName) AS authors,
            GROUP_CONCAT(DISTINCT tags.name) AS tags,
            GROUP_CONCAT(DISTINCT collections.collectionName) AS collections,
            (SELECT GROUP_CONCAT(ai.key) FROM itemAttachments a
             JOIN items ai ON a.itemID = ai.itemID
             WHERE a.parentItemID = items.itemID AND a.contentType = 'application/pdf') AS attachmentKeys
        FROM items
        LEFT JOIN itemCreators ON items.itemID = itemCreators.itemID
        LEFT JOIN creators ON itemCreators.creatorID = creators.creatorID
        LEFT JOIN itemData ON items.itemID = itemData.itemID
        LEFT JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
        LEFT JOIN itemTags ON items.itemID = itemTags.itemID
        LEFT JOIN tags ON itemTags.tagID = tags.tagID
        LEFT JOIN collectionItems ON items.itemID = collectionItems.itemID
        LEFT JOIN collections ON collectionItems.collectionID = collections.collectionID
        WHERE {where}
        GROUP BY items.itemID
        ORDER BY items.dateAdded DESC
        LIMIT ?
    """
    params.append(limit)
    cursor.execute(query, params)
    results = [dict(r) for r in cursor.fetchall()]

    # 标注 MinerU 状态（索引只建一次，避免每个结果都重扫整个目录）
    for r in results:
        attachments = (r.pop('attachmentKeys', None) or '').split(',')
        folder = find_mineru(r.get('itemKey'), attachments)
        r['has_mineru'] = bool(folder)
        r['mineru_folder'] = folder
        r['has_pdf'] = any(attachments)

    db.close()
    return results


def get_paper_by_key(key, with_annotations=False):
    """Get full paper details by Zotero item key."""
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()

    query = """
        SELECT
            items.key AS itemKey,
            items.itemID,
            (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'title' LIMIT 1) AS title,
            (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'abstractNote' LIMIT 1) AS abstract,
            COALESCE(
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='publicationTitle' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='proceedingsTitle' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='conferenceName' LIMIT 1),
                (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID=f.fieldID JOIN itemDataValues idv ON d.valueID=idv.valueID
                 WHERE d.itemID=items.itemID AND f.fieldName='repository' LIMIT 1)
            ) AS venue,
            (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'date' LIMIT 1) AS date,
            (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'DOI' LIMIT 1) AS doi,
            (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
             JOIN itemDataValues idv ON d.valueID = idv.valueID
             WHERE d.itemID = items.itemID AND f.fieldName = 'url' LIMIT 1) AS url,
            GROUP_CONCAT(DISTINCT creators.lastName || ', ' || creators.firstName) AS authors,
            GROUP_CONCAT(DISTINCT tags.name) AS tags,
            GROUP_CONCAT(DISTINCT collections.collectionName) AS collections,
            (SELECT GROUP_CONCAT(ai.key) FROM itemAttachments a
             JOIN items ai ON a.itemID = ai.itemID
             WHERE a.parentItemID = items.itemID AND a.contentType = 'application/pdf') AS attachmentKeys
        FROM items
        LEFT JOIN itemCreators ON items.itemID = itemCreators.itemID
        LEFT JOIN creators ON itemCreators.creatorID = creators.creatorID
        LEFT JOIN itemData ON items.itemID = itemData.itemID
        LEFT JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
        LEFT JOIN itemTags ON items.itemID = itemTags.itemID
        LEFT JOIN tags ON itemTags.tagID = tags.tagID
        LEFT JOIN collectionItems ON items.itemID = collectionItems.itemID
        LEFT JOIN collections ON collectionItems.collectionID = collections.collectionID
        WHERE items.key = ?
          AND items.itemID NOT IN (SELECT itemID FROM itemAttachments)
        GROUP BY items.itemID
    """
    cursor.execute(query, [key])
    row = cursor.fetchone()

    # 传进来的是附件 key（如 PDF 附件的 PDF00001）时，改用它的父条目
    if not row:
        cursor.execute(
            "SELECT pi.key FROM itemAttachments a "
            "JOIN items pi ON a.parentItemID = pi.itemID "
            "JOIN items ai ON a.itemID = ai.itemID "
            "WHERE ai.key = ?", [key])
        parent = cursor.fetchone()
        if not parent:
            db.close()
            return None
        key = parent['key']
        cursor.execute(query, [key])
        row = cursor.fetchone()
        if not row:
            db.close()
            return None

    result = dict(row)
    attachments = (result.pop('attachmentKeys', None) or '').split(',')

    # MinerU 目录：先按附件 key 匹配，再退回 itemKey
    folder = find_mineru(key, attachments)
    result['mineru_folder'] = folder
    result['has_mineru'] = bool(folder)
    result['has_pdf'] = any(attachments)

    # Get annotations if requested
    if with_annotations:
        ann_query = """
            SELECT
                itemAnnotations.text AS text,
                itemAnnotations.comment AS comment,
                itemAnnotations.pageLabel AS pageLabel,
                parentItems.key AS attachmentKey,
                itemAnnotations.color AS color,
                itemAnnotations.sortIndex AS sort_order
            FROM itemAnnotations
            JOIN items AS parentItems ON itemAnnotations.parentItemID = parentItems.itemID
            WHERE parentItems.itemID IN (
                SELECT itemID FROM itemAttachments WHERE parentItemID = ?
            )
            ORDER BY itemAnnotations.sortIndex
        """
        cursor.execute(ann_query, [row['itemID']])
        result['annotations'] = [dict(r) for r in cursor.fetchall()]

    db.close()
    return result


def list_collections():
    """List all collections."""
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    cursor.execute("""
        SELECT collections.collectionName, COUNT(collectionItems.itemID) AS item_count
        FROM collections
        LEFT JOIN collectionItems ON collections.collectionID = collectionItems.collectionID
        GROUP BY collections.collectionID
        ORDER BY collections.collectionName
    """)
    results = [dict(r) for r in cursor.fetchall()]
    db.close()
    return results


def list_mineru_papers():
    """List all papers that have MinerU processed content."""
    results = []
    if not os.path.isdir(MINERU_BASE):
        return results
    for d in sorted(os.listdir(MINERU_BASE), key=lambda x: (len(x), x)):
        src_path = os.path.join(MINERU_BASE, d, '_llm_source.json')
        if not os.path.isfile(src_path):
            continue
        try:
            with open(src_path, encoding='utf-8') as f:
                src = json.load(f)
        except (OSError, ValueError):
            continue
        full_md = os.path.join(MINERU_BASE, d, 'full.md')
        results.append({
            'folder': d,
            'parentItemKey': src.get('parentItemKey'),
            'attachmentKey': src.get('attachmentKey'),
            'filename': src.get('sourceFilename', ''),
            'has_full_md': os.path.isfile(full_md),
            'full_md_size': os.path.getsize(full_md) if os.path.isfile(full_md) else 0,
        })
    return results


def list_unparsed_papers():
    """列出有 PDF 附件但 MinerU 还没解析的条目。"""
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    cursor.execute("""
        SELECT pi.key AS itemKey,
               ai.key AS attachmentKey,
               (SELECT idv.value FROM itemData d JOIN fields f ON d.fieldID = f.fieldID
                JOIN itemDataValues idv ON d.valueID = idv.valueID
                WHERE d.itemID = pi.itemID AND f.fieldName = 'title' LIMIT 1) AS title
        FROM itemAttachments a
        JOIN items ai ON a.itemID = ai.itemID
        LEFT JOIN items pi ON a.parentItemID = pi.itemID
        WHERE a.contentType = 'application/pdf'
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    db.close()

    by_attachment, by_parent = mineru_index()
    unparsed = []
    for r in rows:
        if r['attachmentKey'] in by_attachment:
            continue
        if r['itemKey'] and r['itemKey'] in by_parent:
            continue
        unparsed.append({
            'unparsed': True,
            'itemKey': r['itemKey'],
            'attachmentKey': r['attachmentKey'],
            'title': r['title'] or '(独立附件，无父条目)',
        })
    return unparsed


def format_output(results, fmt='text'):
    """Format results as text or JSON."""
    if fmt == 'json':
        return json.dumps(results, ensure_ascii=False, indent=2)

    lines = []
    if isinstance(results, list):
        for i, r in enumerate(results):
            if 'collectionName' in r:
                # Collection listing
                lines.append(f"[{i+1}] {r['collectionName']} ({r['item_count']} papers)")
            elif 'folder' in r and 'has_full_md' in r:
                # MinerU listing
                tag = "[MD]" if r['has_full_md'] else "[no MD]"
                pkey = r.get('parentItemKey') or '-'
                lines.append(f"[{r['folder']}] {tag} key={pkey:<9} {r['filename'][:70]}")
            elif r.get('unparsed'):
                # 有 PDF 但 MinerU 尚未解析
                lines.append(f"[{r['attachmentKey']}] {r['title'][:70]}")
                lines.append(f"    itemKey: {r['itemKey'] or '-'}  |  尚未 MinerU 解析")
            else:
                # Paper search result
                title = (r.get('title') or 'Unknown')[:100]
                authors = (r.get('authors') or 'Unknown')[:60]
                venue = (r.get('venue') or '?')
                date = (r.get('date') or '?')
                key = r.get('itemKey', '?')
                mu = " [MinerU]" if r.get('has_mineru') else ""
                lines.append(f"[{i+1}] {title}")
                lines.append(f"    作者: {authors}")
                lines.append(f"    发表于: {venue}, {date}  |  key: {key}{mu}")
                if r.get('tags'):
                    lines.append(f"    标签: {r['tags']}")
                if r.get('collections'):
                    lines.append(f"    分类: {r['collections']}")
                lines.append("")
    elif isinstance(results, dict):
        title = (results.get('title') or 'Unknown')[:100]
        authors = (results.get('authors') or 'Unknown')
        lines.append(f"标题: {title}")
        lines.append(f"作者: {authors}")
        lines.append(f"发表: {results.get('venue','?')}, {results.get('date','?')}")
        if results.get('doi'):
            lines.append(f"DOI: {results['doi']}")
        if results.get('tags'):
            lines.append(f"标签: {results['tags']}")
        if results.get('collections'):
            lines.append(f"分类: {results['collections']}")
        if results.get('has_mineru'):
            lines.append(f"MinerU: {results['mineru_folder']}")
        if results.get('annotations'):
            lines.append(f"\n标注与注释 ({len(results['annotations'])} 条):")
            for ann in results['annotations']:
                tag = f"[p{ann.get('pageLabel') or '?'} {ann.get('color','')}]"
                highlighted = ann.get('text') or ''
                comment = ann.get('comment') or ''
                if highlighted.strip():
                    lines.append(f"  {tag} 高亮: {highlighted}")
                if comment.strip():
                    lines.append(f"  {tag} 批注: {comment}")
                if not highlighted.strip() and not comment.strip():
                    lines.append(f"  {tag} (无文字内容的标注)")
        if results.get('abstract'):
            lines.append(f"\n摘要: {results['abstract'][:300]}")

    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Query Zotero library')
    parser.add_argument('--search', type=str, help='Search metadata keyword (including title)')
    parser.add_argument('--author', type=str, help='Search by author name')
    parser.add_argument('--tag', type=str, help='Search by tag')
    parser.add_argument('--collection', type=str, help='Search by collection name')
    parser.add_argument('--key', type=str, help='Get paper by Zotero item key')
    parser.add_argument('--annotations', action='store_true', help='Include annotations when using --key')
    parser.add_argument('--list-collections', action='store_true', help='List all collections')
    parser.add_argument('--list-mineru', action='store_true', help='List all MinerU processed papers')
    parser.add_argument('--unparsed', action='store_true', help='List PDFs not yet parsed by MinerU')
    parser.add_argument('--limit', type=int, default=20, help='Max results (default 20)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    if args.list_collections:
        result = list_collections()
    elif args.list_mineru:
        result = list_mineru_papers()
    elif args.unparsed:
        result = list_unparsed_papers()
    elif args.key:
        result = get_paper_by_key(args.key, with_annotations=args.annotations)
        if result is None:
            raise SystemExit(
                f"找不到 key={args.key}（itemKey 与附件 key 都未匹配）。"
                f"用 --search 或 --list-mineru 确认正确的 key。"
            )
    else:
        result = search_papers(
            keyword=args.search,
            author=args.author,
            tag=args.tag,
            collection=args.collection,
            limit=args.limit
        )

    fmt = 'json' if args.json else 'text'
    print(format_output(result, fmt))
