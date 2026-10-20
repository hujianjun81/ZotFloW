"""Publication regressions: no personal vault assumptions or self-test writes."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import audit_figures
import lint_note


class PublicPortabilityTests(unittest.TestCase):
    def test_figure_selftest_preserves_existing_same_named_note(self):
        with tempfile.TemporaryDirectory(prefix='zotflow_public_test_') as folder:
            vault = Path(folder)
            note = vault / '_selftest_figures.md'
            note.write_text('# Existing personal note\n', encoding='utf-8')
            before = note.read_bytes()
            args = ['audit_figures.py', '--vault', folder, '--selftest']
            with patch.object(sys, 'argv', args), \
                 patch.object(audit_figures, 'mineru_base', return_value=str(vault / 'absent')), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(audit_figures.main(), 0)
            self.assertEqual(note.read_bytes(), before)
            self.assertEqual({p.name for p in vault.iterdir()}, {note.name})

    def test_moc_is_opt_in_and_empty_index_reports_missing_note(self):
        with tempfile.TemporaryDirectory(prefix='zotflow_public_test_') as folder:
            note = str(Path(folder) / 'paper.md')
            index = str(Path(folder) / 'custom-index.md')
            self.assertEqual(lint_note.check_moc_coverage(note, folder, set(), {'other'}), [])
            findings = lint_note.check_moc_coverage(note, folder, set(), set(), index)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][0], 'WARN')
            self.assertEqual(lint_note.check_moc_coverage(note, folder, set(), {'paper'}, index), [])
            self.assertEqual(lint_note.check_moc_coverage(index, folder, set(), set(), index), [])

    def test_cli_uses_custom_index_instead_of_fixed_vault_name(self):
        with tempfile.TemporaryDirectory(prefix='zotflow_public_test_') as folder:
            vault = Path(folder)
            (vault / 'paper.md').write_text('**Zotero Key**: PAPER001\n', encoding='utf-8')
            index = vault / 'custom-index.md'
            index.write_text('[[paper]]\n', encoding='utf-8')

            def run():
                output = io.StringIO()
                with patch.object(sys, 'argv', ['lint_note.py', '--vault', folder, '--moc', index.name]), \
                     redirect_stdout(output):
                    lint_note.main()
                return output.getvalue()

            self.assertNotIn('未被指定 MOC 链接收录', run())
            index.write_text('# Empty index\n', encoding='utf-8')
            self.assertIn('未被指定 MOC 链接收录', run())


if __name__ == '__main__':
    unittest.main(verbosity=2)
