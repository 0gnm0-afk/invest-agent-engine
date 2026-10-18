"""Immutable selected-stock snapshots, explicit refresh, cached reads and cross-process locking."""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from financial_sources import Collector, SourceError, settings
from financial_model import dart_normalize, sec_normalize
from financial_focus import VERSION, summarize

ROOT = Path(__file__).resolve().parents[1]

def packed(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')

def save(path,v,atomic=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if atomic:
        tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_bytes(packed(v))
        for attempt in range(6):
            try:os.replace(tmp,path);break
            except PermissionError:
                if attempt==5:raise
                time.sleep(.02*(attempt+1))
    else:
        with path.open('xb') as f:f.write(packed(v))

def now(): return datetime.now(timezone.utc).isoformat()

def lock_file(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return f
    except OSError:
        f.close();return None

def unlock(f):
    f.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(f,fcntl.LOCK_UN)
    f.close()

class FinancialStore:
    def __init__(self, root=None):
        self.root=Path(root or ROOT/'runtime/financials')
        self.mutex=threading.Lock()

    def folder(self,key):
        if not re.fullmatch(r'(KR:[0-9]{6}(?:\.KS|\.KQ)?|US:[A-Z0-9][A-Z0-9.\-]{0,14})',key):
            raise ValueError('invalid_financial_symbol')
        return self.root/key.replace(':','-')

    def read(self,key):
        folder=self.folder(key);status={};result=None
        try:
            status=json.loads((folder/'attempt.json').read_bytes())
        except FileNotFoundError: pass
        except (ValueError,OSError):status=dict(state='failed',error='status_unreadable')
        try:
            pointer=json.loads((folder/'latest.json').read_bytes());sid=pointer['snapshot']
            if not re.fullmatch('[a-f0-9]{32}',sid):raise ValueError('invalid_snapshot')
            base=folder/'snapshots'/sid
            manifest=json.loads((base/'manifest.json').read_bytes())
            if hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest()!=pointer['manifest_sha256']:raise ValueError('manifest_hash_mismatch')
            raw=(base/'result.json').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest['files']['result.json']:raise ValueError('result_hash_mismatch')
            result=json.loads(raw)
        except FileNotFoundError: pass
        except (OSError,ValueError,KeyError):status=dict(state='failed',error='cached_snapshot_integrity_failed')
        if status.get('state')=='running':
            lease=lock_file(folder/'request.lock')
            if lease:
                unlock(lease)
                # A worker may finish between our first read and lock probe.
                latest=json.loads((folder/'attempt.json').read_bytes())
                status=latest if latest.get('state')!='running' else {**status,'state':'interrupted','error':'previous_collection_interrupted'}
        conf=settings()
        return dict(key=key,state=status.get('state','saved' if result else 'not_requested'),attempt=status,result=result,
                    config=dict(dart_ready=bool(conf.get('DART_API_KEY')),sec_ready=bool(conf.get('SEC_USER_AGENT'))),
                    reused=result is not None, calculation_version=VERSION,
                    result_stale=result is not None and result.get('calculation_version') != VERSION)

    def start(self,key,stock,refresh=False):
        folder=self.folder(key)
        with self.mutex:
            cached=self.read(key)
            if cached['result'] and not cached['result_stale'] and not refresh:return cached
            lease=lock_file(folder/'request.lock')
            if lease is None:return {**cached,'state':'running'}
            save(folder/'attempt.json',dict(state='running',started_at=now(),stage='공식 자료 요청 준비'),atomic=True)
            thread=threading.Thread(target=self._work,args=(key,stock,lease),daemon=True)
            thread.start()
        return self.read(key)

    def _work(self,key,stock,lease,seed=None,supplement=False):
        folder=self.folder(key);sid=uuid.uuid4().hex;base=folder/'snapshots'/sid;base.mkdir(parents=True)
        started=now()
        def progress(stage):save(folder/'attempt.json',dict(state='running',started_at=started,stage=stage),atomic=True)
        try:
            inherited = None
            if seed:
                import shutil
                seed=Path(seed);shutil.copytree(seed/'raw',base/'raw',dirs_exist_ok=True)
                source=json.loads((seed/'source.json').read_bytes())
                if (seed/'result.json').exists():
                    inherited=json.loads((seed/'result.json').read_bytes())
                sources=json.loads((seed/'sources.json').read_bytes()) if (seed/'sources.json').exists() else inherited['sources']
                if supplement and source.get('provider')=='DART':
                    from financial_dart_supplement import collect_annual_supplements
                    collector=Collector(base/'raw',progress)
                    source=collect_annual_supplements(collector,source)
                    sources=[*sources,*collector.sources]
                    source['supplemented_at']=now()
            else:
                collector=Collector(base/'raw',progress)
                source=collector.korea(stock['symbol'].split('.')[0]) if stock['market']=='KR' else collector.usa(stock['symbol'])
                sources=collector.sources
            sources = list({s['id']:s for s in sources}.values())
            source['symbol']=stock['symbol']
            progress('공시 기간 정규화 · Python 재무 계산')
            from financial_business import unsupported,coverage
            if source.get('eligibility',{}).get('status')=='unsupported':
                normal=unsupported(source);result=dict(normal,calculation_version=VERSION)
            else:
                normal=dart_normalize(source) if source['provider']=='DART' else sec_normalize(source)
                result=coverage(summarize(normal,stock))
                result['format_issues']=source.get('format_issues',[])
                result['enrichment_errors']=source.get('enrichment_errors',[])
                result['enrichment_plan']=source.get('enrichment_plan',{})
            result.update(key=key,snapshot_id=sid,collected_at=now(),sources=sources)
            if inherited is not None:
                result.update(collected_at=inherited['collected_at'],recalculated_at=now(),source_snapshot_id=inherited['snapshot_id'])
            if source.get('supplemented_at'):
                result['supplemented_at']=source['supplemented_at']
            save(base/'source.json',source);save(base/'normalized.json',normal);save(base/'saved-price.json',stock)
            save(base/'result.json',result)
            files={str(p.relative_to(base)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()}
            code_files=['financial_focus.py','financial_sources.py','financial_model.py','financial_revenue.py','financial_dart_supplement.py','financial_store.py','financial_supplements.py','financial_summary.py']
            code_files+=['financial_business.py','financial_dart_notes.py','financial_sec_filing.py','financial_cohort.py','financial_returns.py','financial_sec_tables.py','financial_sec_distributions.py','financial_sec_ifrs.py']
            save(base/'manifest.json',dict(schema=1,key=key,version=VERSION,files=files,
                                          code={name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest() for name in code_files}))
            save(folder/'latest.json',dict(snapshot=sid,manifest_sha256=hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest()),atomic=True)
            state='unsupported' if result.get('eligibility',{}).get('status')=='unsupported' else 'complete'
            save(folder/'attempt.json',dict(state=state,quality=result.get('data_quality',{}).get('state'),started_at=started,finished_at=now(),snapshot=sid,stage='저장 완료'),atomic=True)
        except Exception as exc:
            # Do not expose raw exceptions: urllib errors may contain API keys.
            known_format={'SEC_IFRS_TAXONOMY_REQUIRES_MAPPING','SEC_IFRS_REPORTING_CURRENCY_UNVERIFIED','SEC_REPORTING_CURRENCY_AMBIGUOUS','DART_NON_DECEMBER_YEAR_REQUIRES_PERIOD_MAPPING','OFFICIAL_PERIODS_UNAVAILABLE'}
            error='UNSUPPORTED_FINANCIAL_FORMAT' if str(exc) in known_format else str(exc) if isinstance(exc,SourceError) else type(exc).__name__
            # Retain official sources for a future parser fix even when no table
            # can be produced. Never turn transport failure into format failure.
            if error=='UNSUPPORTED_FINANCIAL_FORMAT':
                if 'source' in locals() and not (base/'source.json').exists():save(base/'source.json',source)
                if 'sources' in locals() and not (base/'sources.json').exists():save(base/'sources.json',sources)
            save(base/'failure.json',dict(state='failed',error=error,at=now()))
            save(folder/'attempt.json',dict(state='failed',started_at=started,finished_at=now(),error=error,stage='갱신 실패 · 이전 성공 보존'),atomic=True)
        finally:unlock(lease)

    def import_collected(self,key,stock,seed):
        folder=self.folder(key);lease=lock_file(folder/'request.lock')
        if lease is None:raise ValueError('busy')
        self._work(key,stock,lease,seed=seed)
        return self.read(key)

    def recalculate_saved(self,key,supplement=False):
        """Verify and retain saved inputs; fetch missing annual disclosures only if requested."""
        folder=self.folder(key);lease=lock_file(folder/'request.lock')
        if lease is None:raise ValueError('busy')
        try:
            pointer=json.loads((folder/'latest.json').read_bytes());sid=pointer['snapshot']
            if not re.fullmatch('[a-f0-9]{32}',sid):raise ValueError('invalid_snapshot')
            seed=folder/'snapshots'/sid
            manifest_raw=(seed/'manifest.json').read_bytes()
            if hashlib.sha256(manifest_raw).hexdigest()!=pointer['manifest_sha256']:
                raise ValueError('manifest_hash_mismatch')
            manifest=json.loads(manifest_raw)
            required={'source.json','saved-price.json','result.json'}
            if not required.issubset(manifest['files']):raise ValueError('saved_inputs_missing')
            for name,digest in manifest['files'].items():
                path=(seed/name).resolve()
                if not path.is_relative_to(seed.resolve()):raise ValueError('invalid_manifest_path')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                    raise ValueError('saved_input_hash_mismatch')
            prior=json.loads((seed/'result.json').read_bytes())
            if prior.get('key')!=key or prior.get('snapshot_id')!=sid:
                raise ValueError('saved_identity_mismatch')
            stock=json.loads((seed/'saved-price.json').read_bytes())
        except Exception:
            unlock(lease)
            raise
        self._work(key,stock,lease,seed=seed,supplement=supplement)
        return self.read(key)
