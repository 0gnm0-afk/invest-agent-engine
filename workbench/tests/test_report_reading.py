import hashlib
import json
from contextlib import closing
from pathlib import Path
import unittest
import test_analysis


class ReportReadingTests(unittest.TestCase):
    setUp = test_analysis.AnalysisTests.setUp
    tearDown = test_analysis.AnalysisTests.tearDown
    # Reuse only the isolated setup, not the parent regression suite.
    def test_readable_save_and_legacy_copy_preserve_evidence(self):
        args=dict(snapshot_id=self.sid,kind='report',title='합성 보고서',body='# 합성 보고서\n\n사업 변화 해석',author='llm',llm_opinion='사업 변화 해석',event_research=[])
        record=self.s.record(self.case,args)
        path=self.root/record['markdown_path']
        original=path.read_bytes()
        doc=self.s.report_document(self.cid,record['id'])
        self.assertEqual(Path(doc['path']),path)
        self.assertEqual(path.read_text(encoding='utf-8').count('사업 변화 해석'),1)
        self.assertNotIn('실제 사용자 응답',path.read_text(encoding='utf-8'))
        # Model an existing format without changing its saved body or snapshot.
        record.pop('reading_format')
        with closing(self.store.connect()) as db, db:
            db.execute('UPDATE analysis_items SET payload=? WHERE id=?',(json.dumps(record),record['id']))
        before=self.s.snapshot(self.cid,self.sid)
        legacy=self.s.report_document(self.cid,record['id'])
        self.assertNotEqual(legacy['path'],str(path))
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(hashlib.sha256(original).hexdigest(),record['markdown_sha256'])
        self.assertEqual(self.s.snapshot(self.cid,self.sid),before)
        self.assertEqual(len(self.s.rows('analysis_items',self.cid)),1)
