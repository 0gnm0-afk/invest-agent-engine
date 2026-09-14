"""Consistent local archives and verified restore to a new directory only."""
import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from .contracts import digest, encode
from .store import instance_lock, now


def relative(name):
    if not isinstance(name,str): raise ValueError('Invalid archive path')  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    name=name.replace('\\','/')
    parts=name.split('/')
    reserved={'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    if any(not p or p in ('.','..') or ':' in p or p.endswith(('.', ' ')) or p.split('.')[0].upper() in reserved for p in parts):
        raise ValueError('Unsafe archive path')
    if name!='state.sqlite3' and parts[0] not in ('runs','reports'):
        raise ValueError('Archive path outside instance artifacts')
    return PurePosixPath(name).as_posix()


def file_hash(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def database_refs(path):
    refs={}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (1,2,3) or db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Database integrity/schema check failed')
        for row in db.execute('SELECT * FROM runs'):
            expected=digest({'report_date':row['report_date'],'config':json.loads(row['config_json']),
                'input':json.loads(row['input_json']),'code':row['code_version']})
            if expected!=row['run_key']: raise ValueError('Frozen run integrity check failed')
        for row in db.execute('SELECT * FROM artifacts'):
            payload=json.loads(row['payload_json'])
            if digest(payload)!=row['payload_hash']: raise ValueError('Artifact integrity check failed')
            for ref in payload.get('files',[]):
                name=relative(ref['path'])
                if name=='state.sqlite3': raise ValueError('Database cannot be an artifact reference')
                if name in refs and refs[name]!=ref['sha256']: raise ValueError('Conflicting artifact references')
                refs[name]=ref['sha256']
        if version>=2:
            tables = ('adoptions', 'plan_events') + (('risk_entities', 'risk_events') if version >= 3 else ())
            for table in tables:
                for row in db.execute(f'SELECT payload_json,payload_hash FROM {table}'):
                    if digest(json.loads(row[0]))!=row[1]: raise ValueError('Plan/event integrity check failed')
    return refs


def create(instance, run_id=None):
    root=Path(instance).resolve()
    database=root/'state.sqlite3'
    if not database.is_file() or database.is_symlink(): raise ValueError('Instance database missing or linked')
    with instance_lock(root):
        folder=root/'backups';folder.mkdir(exist_ok=True)
        if folder.resolve().parent!=root: raise ValueError('Backup directory must stay inside instance')
        with tempfile.TemporaryDirectory(prefix='backup-stage-',dir=folder) as temp:
            stage=Path(temp).resolve()
            if not stage.is_relative_to(folder.resolve()): raise ValueError('Unsafe staging directory')
            copied=stage/'state.sqlite3'
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(copied)) as target:
                source.backup(target)
            refs=database_refs(copied)
            paths={'state.sqlite3':copied}
            for name,expected in refs.items():
                source=(root/name).resolve()
                if not source.is_relative_to(root) or not source.is_file() or file_hash(source)!=expected:
                    raise ValueError('Referenced artifact missing, escaped or changed')
                paths[name]=source
            entries={name:{'sha256':file_hash(path),'size':path.stat().st_size} for name,path in paths.items()}
            manifest={'format_version':1,'created_at':now(),'run_id':run_id,'files':entries}
            archive=stage/'archive.zip'
            with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr('manifest.json',encode(manifest))
                for name,path in paths.items(): zipped.write(path,name)
            # Verify the actual ZIP bytes, not only files read before compression.
            verify(archive)
            final=folder/f"instance-{uuid.uuid4().hex}.zip"
            if final.exists(): raise ValueError('Backup destination already exists')
            archive.rename(final)
            return {'state':'available','archive':str(final),'sha256':file_hash(final),
                    'created_at':manifest['created_at'],'file_count':len(entries),'run_id':run_id}


def _unpack(archive, stage):
    with zipfile.ZipFile(archive) as zipped:
        infos=zipped.infolist()
        if len({i.filename for i in infos})!=len(infos): raise ValueError('Duplicate ZIP entry')
        manifest_info=zipped.getinfo('manifest.json')
        if manifest_info.file_size>16*1024*1024: raise ValueError('Manifest too large')
        manifest=json.loads(zipped.read(manifest_info))
        if manifest.get('format_version')!=1 or not isinstance(manifest.get('files'),dict): raise ValueError('Unsupported backup format')
        files=manifest['files']
        if sum(i.file_size for i in infos)>shutil.disk_usage(stage).free:
            raise ValueError('Insufficient space to verify backup')
        if set(files)|{'manifest.json'}!={i.filename for i in infos} or 'state.sqlite3' not in files:
            raise ValueError('Archive inventory mismatch')
        for name,ref in files.items():
            if relative(name)!=name: raise ValueError('Noncanonical archive path')
            info=zipped.getinfo(name)
            if info.is_dir() or stat.S_ISLNK(info.external_attr>>16) or info.file_size!=ref['size']:
                raise ValueError('Invalid archive member')
            target=stage/name
            if not target.resolve().is_relative_to(stage): raise ValueError('Archive escapes staging')
            target.parent.mkdir(parents=True,exist_ok=True)
            with zipped.open(info) as source,target.open('xb') as output: shutil.copyfileobj(source,output,1024*1024)
            if file_hash(target)!=ref['sha256']: raise ValueError('Backup checksum mismatch')
        refs=database_refs(stage/'state.sqlite3')
        if set(refs)|{'state.sqlite3'}!=set(files): raise ValueError('DB/archive references differ')
        if any(files[name]['sha256']!=expected for name,expected in refs.items()):
            raise ValueError('Artifact checksum differs from DB')
        return manifest


def verify(archive):
    archive=Path(archive).resolve()
    with tempfile.TemporaryDirectory(prefix='backup-verify-',dir=archive.parent) as temp:
        stage=Path(temp).resolve()
        if not stage.is_relative_to(archive.parent): raise ValueError('Unsafe verification directory')
        manifest=_unpack(archive,stage)
    return {'state':'verified','archive':str(archive),'file_count':len(manifest['files'])}


def restore(archive, destination):
    archive=Path(archive).resolve();destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink(): raise ValueError('Restore requires a new destination')
    parent=destination.parent.resolve()
    if not parent.is_dir(): raise ValueError('Restore parent must already exist')
    destination=parent/destination.name
    with tempfile.TemporaryDirectory(prefix='restore-stage-',dir=parent) as temp:
        stage=Path(temp).resolve()
        if not stage.is_relative_to(parent): raise ValueError('Unsafe restore staging directory')
        manifest=_unpack(archive,stage)
        destination.mkdir(exist_ok=False)  # Atomically claim a new directory; never replace one.
        # Install DB last, so interrupted copies cannot be used as a complete instance.
        for name in sorted(manifest['files'],key=lambda n:n=='state.sqlite3'):
            target=destination/name
            if not target.resolve().is_relative_to(destination.resolve()): raise ValueError('Restore path escaped')
            target.parent.mkdir(parents=True,exist_ok=True)
            if name=='state.sqlite3':
                partial=destination/'state.sqlite3.partial'
                with (stage/name).open('rb') as source,partial.open('xb') as output: shutil.copyfileobj(source,output)
                partial.rename(target)
            else:
                with (stage/name).open('rb') as source,target.open('xb') as output: shutil.copyfileobj(source,output)
        return {'state':'restored','instance':str(destination),'file_count':len(manifest['files']),
                'note':'No credentials, external source files or schedules are restored. Resume requires the matching engine code.'}
