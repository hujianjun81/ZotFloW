"""Regression checks for read-only snapshots, annotation identity and branch scope."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import lint_note
import query_zotero
import read_zotero_context


class ZoteroReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zotflow_test_')
        self.root = Path(self.temp.name)
        self.path = self.root / 'zotero.sqlite'
        self.writer = sqlite3.connect(self.path)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('PRAGMA wal_autocheckpoint=0')
        self.writer.executescript('''
            CREATE TABLE items(itemID INTEGER PRIMARY KEY,key TEXT,itemTypeID INTEGER,dateAdded TEXT);
            CREATE TABLE itemAttachments(itemID INTEGER,parentItemID INTEGER,contentType TEXT);
            CREATE TABLE itemAnnotations(itemID INTEGER,parentItemID INTEGER,text TEXT,comment TEXT,pageLabel TEXT,color TEXT,type INTEGER,sortIndex TEXT);
            CREATE TABLE fields(fieldID INTEGER,fieldName TEXT);
            CREATE TABLE itemData(itemID INTEGER,fieldID INTEGER,valueID INTEGER);
            CREATE TABLE itemDataValues(valueID INTEGER,value TEXT);
            CREATE TABLE creators(creatorID INTEGER,lastName TEXT,firstName TEXT);
            CREATE TABLE itemCreators(itemID INTEGER,creatorID INTEGER);
            CREATE TABLE tags(tagID INTEGER,name TEXT);
            CREATE TABLE itemTags(itemID INTEGER,tagID INTEGER);
            CREATE TABLE collections(collectionID INTEGER,collectionName TEXT);
            CREATE TABLE collectionItems(itemID INTEGER,collectionID INTEGER);
            CREATE TABLE llm_for_zotero_conversation_registry(conversation_id TEXT PRIMARY KEY,instance_id TEXT,paper_item_id INTEGER,valid INTEGER);
            CREATE TABLE llm_for_zotero_chat_messages(id INTEGER PRIMARY KEY,conversation_id TEXT,conversation_instance_id TEXT,role TEXT,text TEXT,timestamp INTEGER,selected_text TEXT);
        ''')
        self.writer.executemany('INSERT INTO items VALUES(?,?,?,?)', [
            (1,'PAPER001',11,'2026-10-20'),(2,'PDF00001',3,'2026-10-20'),
            (3,'ANNO0001',1,'2026-10-20'),(4,'OTHER001',11,'2026-10-20')])
        self.writer.execute("INSERT INTO itemAttachments VALUES(2,1,'application/pdf')")
        self.long_comment = '用户真正的批注' * 30
        self.writer.execute('INSERT INTO itemAnnotations VALUES(?,?,?,?,?,?,?,?)',
                            (3,2,'高亮原文',self.long_comment,'1005','#ffff00',1,'00001'))
        self.writer.execute("INSERT INTO fields VALUES(1,'title')")
        self.writer.execute("INSERT INTO itemData VALUES(1,1,1)")
        self.writer.execute("INSERT INTO itemDataValues VALUES(1,'Target paper')")
        self.writer.executemany('INSERT INTO llm_for_zotero_conversation_registry VALUES(?,?,?,?)', [
            ('a','ia',1,1),('fork','if',1,1),('bad','ib',1,0),('other','io',4,1)])
        self.writer.executemany('INSERT INTO llm_for_zotero_chat_messages VALUES(?,?,?,?,?,?,?)', [
            (1,'a','ia','user','再次问同一句',100,None),
            (2,'a','ia','user','再次问同一句',200,None),
            (3,'a','WRONG','user','错实例',300,None),
            (4,'fork','if','user','再次问同一句',100,None),
            (5,'bad','ib','user','失效会话',100,None),
            (6,'other','io','user','其他论文',100,None)])
        self.writer.commit()
        self.patch_db = patch.object(query_zotero,'DB_PATH',str(self.path))
        self.patch_db.start()

    def tearDown(self):
        self.patch_db.stop()
        self.writer.close()
        self.temp.cleanup()

    def test_committed_wal_visible_without_source_writes(self):
        paths = [self.path, Path(str(self.path)+'-wal')]
        before = [hashlib.sha256(p.read_bytes()).digest() for p in paths]
        db = query_zotero.get_db()
        self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0],4)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM items')
        db.close()
        self.assertEqual(before,[hashlib.sha256(p.read_bytes()).digest() for p in paths])

    def test_real_comment_and_page_not_highlight_alias(self):
        paper = query_zotero.get_paper_by_key('PDF00001',with_annotations=True)
        annotation = paper['annotations'][0]
        self.assertEqual(annotation['text'],'高亮原文')
        self.assertEqual(annotation['comment'],self.long_comment)
        self.assertEqual(annotation['pageLabel'],'1005')
        self.assertEqual(annotation['attachmentKey'],'PDF00001')
        self.assertIn(self.long_comment,query_zotero.format_output(paper))

    def test_paper_validity_instance_and_fork_scope(self):
        context = read_zotero_context.read_context('PDF00001')
        self.assertEqual(context['itemKey'],'PAPER001')
        self.assertEqual({m['id'] for m in context['messages']},{1,2,4})
        self.assertEqual(context['counts']['written_comments'],1)
        self.assertFalse(context['selected_context_available'])
        self.assertEqual(context['counts']['user_messages'],3)
        self.assertEqual(len(context['possible_inherited_duplicates']),1)
        # The later repeated question remains; even Fork candidates are not deleted.
        self.assertIn(2,[m['id'] for m in context['messages']])

    def test_missing_registry_is_reported_not_guessed(self):
        self.writer.execute('DROP TABLE llm_for_zotero_conversation_registry')
        self.writer.commit()
        context = read_zotero_context.read_context('PAPER001')
        self.assertEqual(context['messages'],[])
        self.assertTrue(any('registry unavailable' in s for s in context['limitations']))

    def test_unknown_key_does_not_return_other_records(self):
        with self.assertRaises(ValueError):
            read_zotero_context.read_context('UNKNOWN1')

    def test_changing_source_never_returns_inconsistent_snapshot(self):
        copyfile = query_zotero.shutil.copyfile
        counter = 100

        def copy_while_writer_changes(source, destination):
            nonlocal counter
            result = copyfile(source, destination)
            counter += 1
            self.writer.execute('INSERT INTO items VALUES(?,?,?,?)',
                                (counter,str(counter),11,'2026-10-20'))
            self.writer.commit()
            return result

        with patch.object(query_zotero.shutil,'copyfile',side_effect=copy_while_writer_changes):
            with self.assertRaises(TimeoutError):
                query_zotero.get_db()

    def test_research_artifact_with_key_keeps_link_checks_only(self):
        note = self.root/'comparison.md'
        note.write_text('---\nzotflow_type: paper-comparison\nzotflow_workflows: [paper-comparison, research-review]\n---\n我比较两篇。\n**Zotero Key**: PAPER001\n![[missing.svg]]\n',encoding='utf-8')
        findings,info = lint_note.lint_note(str(note),str(self.root))
        self.assertFalse(info['paper'])
        self.assertTrue(any(f[0]=='FAIL' and f[1]=='图引用可解析' for f in findings))
        self.assertFalse(any(f[1] in {'禁人称词','章节完整性'} for f in findings))

    def test_declared_reading_note_still_requires_key(self):
        note = self.root/'reading.md'
        note.write_text('---\nzotflow_type: close-reading\n---\n# Reading\n',encoding='utf-8')
        findings,info = lint_note.lint_note(str(note),str(self.root))
        self.assertTrue(info['paper'])
        self.assertTrue(any(f[0]=='FAIL' and f[1]=='Zotero Key' for f in findings))


if __name__ == '__main__':
    unittest.main(verbosity=2)
