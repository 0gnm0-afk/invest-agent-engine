import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.backup import create, relative, restore, verify
from invest_agent.trading.runner import PipelineRunner
from invest_agent.trading.store import BusyError, instance_lock


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.instance=self.root/'instance'
        self.fixture=Path(__file__).resolve().parents[1]/'examples/morning_fixture.json'
        self.runner=PipelineRunner(self.instance)

    def run_fixture(self,stop=None):
        return self.runner.run(self.fixture,'2026-09-12',['KR','US'],stop)

    def test_restore_interrupted_run_and_resume_in_new_directory(self):
        stopped=self.run_fixture('summary')
        backup=create(self.instance)
        self.assertEqual(verify(backup['archive'])['state'],'verified')
        target=self.root/'restored';restore(backup['archive'],target)
        self.assertEqual(PipelineRunner(target).status(stopped['run_id'])['state'],'interrupted')
        resumed=PipelineRunner(target).resume(stopped['run_id'])
        self.assertEqual(resumed['state'],'succeeded')
        self.assertTrue(all(s['attempt_no']==1 for s in resumed['steps']))
        self.assertEqual(self.runner.status(stopped['run_id'])['state'],'interrupted')

    def test_completed_report_restores_and_archive_does_not_include_backups_or_secrets(self):
        run=self.run_fixture();(self.instance/'.env').write_text('DO_NOT_ARCHIVE=synthetic',encoding='utf-8')
        first=create(self.instance);second=create(self.instance)
        with zipfile.ZipFile(second['archive']) as zipped:
            names=zipped.namelist()
            self.assertFalse(any(n.startswith('backups/') or n=='.env' for n in names))
            self.assertTrue(any(n.endswith('report.html') for n in names))
        target=self.root/'restored';restore(second['archive'],target)
        resumed=PipelineRunner(target).resume(run['run_id'])
        self.assertEqual(resumed['state'],'succeeded')
        self.assertTrue(Path(first['archive']).is_file())

    def test_existing_destination_is_never_overwritten(self):
        self.run_fixture();backup=create(self.instance)
        target=self.root/'existing';target.mkdir();marker=target/'keep';marker.write_text('original')
        with self.assertRaisesRegex(ValueError,'new destination'):restore(backup['archive'],target)
        self.assertEqual(marker.read_text(),'original')

    def test_corrupt_archive_and_missing_artifact_are_rejected(self):
        self.run_fixture();backup=create(self.instance)
        bad=self.root/'bad.zip'
        with zipfile.ZipFile(backup['archive']) as source,zipfile.ZipFile(bad,'w') as dest:
            for info in source.infolist():
                value=source.read(info)
                if info.filename.endswith('report.md'):value=b'corrupt'
                dest.writestr(info.filename,value)
        with self.assertRaises(ValueError):restore(bad,self.root/'rejected')
        self.assertFalse((self.root/'rejected').exists())
        next((self.instance/'reports').glob('*/*/report.md')).unlink()
        with self.assertRaisesRegex(ValueError,'artifact missing'):create(self.instance)

    def test_path_escape_and_duplicate_zip_members_are_rejected(self):
        for name in ('../outside','reports/../../outside','C:/outside','reports/x:stream','reports/CON','/absolute','reports/x.'): 
            with self.subTest(name=name),self.assertRaises(ValueError):relative(name)
        self.run_fixture();backup=create(self.instance);bad=self.root/'duplicate.zip'
        with zipfile.ZipFile(backup['archive']) as source,zipfile.ZipFile(bad,'w') as dest:
            for info in source.infolist(): dest.writestr(info.filename,source.read(info))
            with self.assertWarns(UserWarning):dest.writestr('manifest.json','{}')
        with self.assertRaisesRegex(ValueError,'Duplicate ZIP'):restore(bad,self.root/'rejected')

    def test_active_writer_prevents_backup(self):
        self.run_fixture()
        with instance_lock(self.instance), self.assertRaises(BusyError):
            create(self.instance)

    def test_morning_automatic_backup_and_failure_do_not_destroy_report(self):
        config=self.root/'config.json';config.write_text(json.dumps({'schema_version':1}),encoding='utf-8')
        with patch('invest_agent.trading.charts.generate',return_value=[]):
            result=self.runner.run_morning(config,'2026-09-12')
        self.assertEqual(result['backup']['state'],'available')
        self.assertTrue(Path(result['backup']['archive']).exists())
        with patch('invest_agent.trading.backup.create',side_effect=OSError('simulated full disk')):
            repeated=self.runner.run_morning(config,'2026-09-12')
        self.assertEqual(repeated['run_id'],result['run_id'])
        self.assertEqual(repeated['backup']['state'],'unavailable')
        self.assertTrue(next((self.instance/'reports').glob('*/*/report.md')).is_file())

    def test_cli_backup_restore_and_status(self):
        run=self.run_fixture()
        def call(*args):
            result=subprocess.run([sys.executable,'-m','invest_agent.cli',*map(str,args)],capture_output=True,encoding='utf-8', check=False)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        backup=call('backup','--instance',self.instance)
        target=self.root/'cli-restored';call('restore','--archive',backup['archive'],'--destination',target)
        result=call('status',run['run_id'],'--instance',target)
        self.assertEqual(result['state'],'succeeded')
