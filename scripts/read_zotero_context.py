"""Read one paper's complete annotations and valid LLM-for-Zotero conversations.

Raw branches are preserved; potential inherited duplicates are only flagged.
"""
import argparse
from collections import defaultdict
import json
import sqlite3
import sys

from query_zotero import get_db

MESSAGE_TABLES = (
    'llm_for_zotero_chat_messages',
    'llm_for_zotero_codex_messages',
    'llm_for_zotero_claude_messages',
)
CONTEXT_FIELDS = (
    'selected_text', 'selected_text_contexts_json',
    'selected_texts_json', 'selected_text_paper_contexts_json',
)


def columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def read_context(key):
    db = get_db()
    db.row_factory = sqlite3.Row
    try:
        row = db.execute('SELECT itemID,key FROM items WHERE key=?', (key,)).fetchone()
        if row is None:
            raise ValueError(f'Unknown Zotero key: {key}')
        attachment = db.execute('SELECT parentItemID FROM itemAttachments WHERE itemID=?', (row['itemID'],)).fetchone()
        if attachment is not None:
            if attachment['parentItemID'] is None:
                raise ValueError('Standalone attachment has no parent paper for conversation lookup')
            row = db.execute('SELECT itemID,key FROM items WHERE itemID=?', (attachment['parentItemID'],)).fetchone()
        paper_id = row['itemID']
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {
            'itemKey': row['key'], 'itemID': paper_id,
            'snapshot': 'Verified stable DB/WAL copies, SQLite integrity check and memory backup; source files read only',
            'annotations': [], 'conversations': [], 'messages': [], 'limitations': [],
        }
        if 'itemAnnotations' in tables:
            fields = columns(db, 'itemAnnotations')
            wanted = [f for f in ('text', 'comment', 'pageLabel', 'color', 'type', 'sortIndex') if f in fields]
            if 'parentItemID' in fields and wanted:
                selected = ','.join('a.' + f for f in wanted)
                result['annotations'] = [dict(r) for r in db.execute(
                    f'SELECT i.key AS annotationKey,attItem.key AS attachmentKey,{selected} '
                    'FROM itemAnnotations a JOIN items i ON a.itemID=i.itemID '
                    'JOIN itemAttachments att ON a.parentItemID=att.itemID '
                    'JOIN items attItem ON att.itemID=attItem.itemID '
                    'WHERE att.parentItemID=? ORDER BY a.itemID', (paper_id,))]
            missing = {'text', 'comment', 'pageLabel'} - fields
            if missing:
                result['limitations'].append('Annotation fields unavailable: ' + ', '.join(sorted(missing)))
        else:
            result['limitations'].append('itemAnnotations table unavailable')

        registry = 'llm_for_zotero_conversation_registry'
        required = {'conversation_id', 'paper_item_id', 'valid'}
        if registry not in tables or not required <= columns(db, registry):
            result['limitations'].append('Valid paper conversation registry unavailable; no guessed associations')
        else:
            records = [dict(r) for r in db.execute(
                f'SELECT * FROM {registry} WHERE paper_item_id=? AND valid=1', (paper_id,))]
            result['conversations'] = records
            for table in MESSAGE_TABLES:
                if table not in tables:
                    continue
                available = columns(db, table)
                if not {'conversation_id', 'role', 'text'} <= available:
                    result['limitations'].append(f'{table}: required message fields unavailable')
                    continue
                if not set(CONTEXT_FIELDS) & available:
                    result['limitations'].append(f'{table}: selected-text context fields unavailable')
                wanted = [f for f in ('id', 'conversation_id', 'conversation_instance_id', 'role', 'text', 'timestamp') + CONTEXT_FIELDS if f in available]
                order = ','.join(f for f in ('timestamp', 'id') if f in available) or 'rowid'
                for conversation in records:
                    condition = 'conversation_id=?'
                    params = [conversation['conversation_id']]
                    if 'conversation_instance_id' in available and conversation.get('instance_id'):
                        condition += ' AND (conversation_instance_id=? OR conversation_instance_id IS NULL)'
                        params.append(conversation['instance_id'])
                    for message in db.execute(f'SELECT {",".join(wanted)} FROM {table} WHERE {condition} ORDER BY {order}', params):
                        result['messages'].append({'source': table, **dict(message)})
            if records and not any(t in tables for t in MESSAGE_TABLES):
                result['limitations'].append('No supported message tables available')

        # Same words alone do not prove an inherited Fork message.
        candidates = defaultdict(list)
        for index, message in enumerate(result['messages']):
            if message.get('timestamp') is not None:
                signature = (message['source'], message['role'], message['text'], message['timestamp'],
                             *(message.get(field) for field in CONTEXT_FIELDS))
                candidates[signature].append(index)
        result['possible_inherited_duplicates'] = [
            indexes for indexes in candidates.values()
            if len({result['messages'][i]['conversation_id'] for i in indexes}) > 1
        ]
        result['selected_context_available'] = any(
            message.get(field) not in (None, '', '[]', '{}', 'null')
            for message in result['messages'] for field in CONTEXT_FIELDS
        )
        result['deduplication'] = 'All raw messages retained; cross-branch candidates require inheritance verification'
        result['counts'] = {
            'annotations': len(result['annotations']),
            'written_comments': sum(bool((a.get('comment') or '').strip()) for a in result['annotations']),
            'valid_conversations': len(result['conversations']),
            'messages': len(result['messages']),
            'user_messages': sum(m['role'] == 'user' for m in result['messages']),
        }
        return result
    finally:
        db.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True, help='Parent paper or attachment key')
    args = parser.parse_args()
    try:
        print(json.dumps(read_context(args.key), ensure_ascii=False, indent=2))
    except (ValueError, TimeoutError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
