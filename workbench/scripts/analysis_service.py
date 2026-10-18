"""Analysis cases over existing dashboard stores. No broker/market/order writes."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlencode, quote

from chart_workspace import Conflict, validate
from analysis_geometry import geometry
from analysis_collaboration import Collaboration, OPS as COLLABORATION_OPS, Forbidden

from price_events import build as price_events, validate_research

from analysis_report import readable, FOLLOWUP

QUALITATIVE_SKILL = 'stock-qualitative-analysis'


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value): return hashlib.sha256(packed(value).encode()).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def ident(prefix): return prefix + uuid.uuid4().hex


def key(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + '[a-f0-9]{32}', value): raise ValueError('invalid_identifier')
    return value


def short(value, limit=4000):
    if not isinstance(value, str) or len(value) > limit: raise ValueError('invalid_text')
    return value


def context(value):
    if not isinstance(value, dict) or set(value)-{'timeframe','range','visible_range','selected_drawing','scenario','price_scale','evaluate_at'}: raise ValueError('invalid_context')
    result = dict(timeframe='1d', range='6', price_scale='linear', scenario='', selected_drawing=None, visible_range=None, **{})
    result.update(value)
    if result['timeframe'] not in ('1d','1w','1mo') or result['range'] not in ('3','6','12','60','all') or result['price_scale'] != 'linear': raise ValueError('unsupported_chart_context')
    short(result['scenario'],200)
    if result['selected_drawing'] is not None: short(result['selected_drawing'],64)
    if result.get('evaluate_at'): datetime.strptime(result['evaluate_at'],'%Y-%m-%d')
    v=result['visible_range']
    if v is not None and (not isinstance(v,dict) or set(v)!={'from','to'} or any(type(x) not in (int,float) or not -100000<x<100000 for x in v.values()) or v['from']>=v['to']): raise ValueError('invalid_visible_range')
    return result


class AnalysisService:
    def __init__(self, store, histories, financials, view, root):
        self.store=store; self.histories=histories; self.financials=financials; self.view=view; self.root=Path(root)
        with closing(store.connect()) as db, db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS analysis_cases (id TEXT PRIMARY KEY, stock_key TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS analysis_snapshots (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS analysis_items (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            ''')
        self.collaboration = Collaboration(self)
        self.collaboration.migrate()

    def rows(self, table, case_id=None):
        if table not in ('analysis_cases','analysis_snapshots','analysis_items'): raise ValueError('table')
        with closing(self.store.connect()) as db:
            sql='SELECT payload FROM '+table; args=()
            if case_id is not None: sql+=' WHERE case_id=?'; args=(case_id,)
            return [json.loads(r[0]) for r in db.execute(sql+' ORDER BY rowid',args)]

    def case(self, case_id):
        key(case_id,'an_')
        with closing(self.store.connect()) as db:
            row=db.execute('SELECT payload FROM analysis_cases WHERE id=?',(case_id,)).fetchone()
        if not row: raise ValueError('analysis_not_found')
        return json.loads(row[0])

    def snapshot(self, case_id, snapshot_id):
        key(snapshot_id,'sn_'); self.case(case_id)
        with closing(self.store.connect()) as db:
            row=db.execute('SELECT payload,sha256 FROM analysis_snapshots WHERE id=? AND case_id=?',(snapshot_id,case_id)).fetchone()
        if not row: raise ValueError('snapshot_not_found_for_analysis')
        value=json.loads(row[0])
        if digest(value)!=row[1]: raise ValueError('snapshot_integrity_failed')
        return value

    def links(self, case, origin):
        cid=case['id']; route='#stock/'+quote(case['stock_key'].split(':',1)[0])+'/'+quote(case['stock_key'].split(':',1)[1])+'?analysis='+cid
        dashboard=origin+'/'+route
        if case['test_record']:
            prompt=(f"투자 워크벤치 연결 검증용 분석건 {cid} ({case['stock_key']})을 확인해줘. "
                    "실제 투자 분석이 아니다. invest_analysis MCP의 analysis_read와 analysis_history로 저장 자료와 연결을 확인하고 "
                    "입력 snapshot_id와 대시보드 복귀 링크를 알려줘. 정성분석 실행·외부 자료 갱신·리포트 저장은 하지 마. "
                    f"작도가 없어도 정상이다. 대시보드: {dashboard}")
        else:
            prompt=(f"${QUALITATIVE_SKILL} 스킬을 사용해 투자 워크벤치 분석건 {cid} ({case['stock_key']})의 정성분석을 수행하고 "
                    "같은 분석건에 리포트를 저장해줘.\n"
                    "스킬의 goal·조사·최대 2회 자체 검증 절차를 따른다. 먼저 analysis_collaboration_read와 analysis_history로 기존 보고서·사용자 답·미정·채택 계획 버전을 확인하고, analysis_read(analysis_key)로 "
                    "현재 저장 입력을 읽고 반환 snapshot_id를 고정해 events·research_groups·news_inputs와 재무 요약·연간/분기 실적·출처를 읽어줘. "
                    "내부 MCP의 계산 재무에서 의미 있는 변화와 사업 맥락을 해석해줘. 코드의 최근5년 주봉 고점·저점 전후 사건만 조사하고 확정·잠정 구분과 우선순위를 유지해줘. 시가 갭·월봉 독립 조사는 이번 범위 밖이다. "
                    "Telegram 검색과 함께 필요한 공시·공식 자료·일반 웹 뉴스를 적극 활용하고, business/risk/new/tsr는 종목에 따라 portfolio/growth 등으로 추가·대체할 수 있다. 발표된 미래 주주환원 계획과 실제 집행을 구분해줘. "
                    "각 news_window부터 실제 뉴스를 검색하고 근거 부족 시 확대하며 근접일을 같은 원인으로 단정하지 마. "
                    "analysis_save_record의 event_research에 group_id, 조사상태(unresearched/failed/cause_unconfirmed/evidence_found), 검색범위·출처를 저장해줘. "
                    "필요한 배열은 next_offset이 null이 될 때까지 같은 snapshot_id로 읽고, analysis_history의 기존 보고서·사용자 의견을 확인해줘. "
                    "저장 자료의 기준일·회계기간·결측·실패와 이전 성공 자료 사용 여부를 보존하고, 스킬에 따라 추가 조사한 공식 공시·IR·뉴스는 "
                    "출처와 공개일을 별도로 기록해줘. 저장 입력을 실시간 자료로 부르거나 과거 입력에 새 근거가 있었던 것처럼 쓰지 마.\n"
                    f"완성한 보고서는 analysis_save_record(analysis_key='{cid}', snapshot_id=고정한_ID, kind='report', author='llm')로 "
                    "해석형 본문과 주장 가까이 연결한 출처를 저장해줘. 첫머리는 정성분석 근거에 기반한 52주 신고가 돌파·추가 상승의 핵심 조건과 촉매를 두괄식으로 설명하고, 확인된 재료·미확인 조건·반증을 구별해줘. 이어 기업의 변화와 판단 쟁점, 핵심 숫자의 사업/이익/현금/주당 가치 의미와 반대 근거를 설명해줘. 주봉 사건은 이전 추세·기대→새 정보→후속 가격→전환/강화/일시반응 해석을 연결하고 전체 국면을 종합해줘. 본문에 담은 해석을 llm_opinion에 중복하지 마. 사용자 발언을 만들지 말고 user_response에는 실제 확인된 발언만 넣어줘. "
                    "저장 성공의 ID와 history 일치를 확인해줘. "
                    "저장 실패를 완료로 표시하지 마. 작도는 없어도 되며 이 정성분석에서 작도 제안·계좌 조회·주문·위험 기준 변경은 하지 마. "
                    + FOLLOWUP + f"분석 키: {cid}\n대시보드: {dashboard}")
        resume = (f"투자 워크벤치 {cid} ({case['stock_key']})의 후속 논의를 이어가줘. "
                  "프로젝트 AGENTS의 후속 협업 규칙을 읽고 analysis_collaboration_read로 현재 판단·채택한 정확한 계획 버전·미정 질문·원문 참조를 먼저 확인해줘. "
                  "analysis_history에서 최신 보고서 본문과 원 출처를 읽고 analysis_report_document로 현재 세션에 열 문서를 얻어줘. 기존 근거를 재사용하며 새로운 사실이 필요한 부분만 조사해줘. 기술적인 처리 내역부터 설명하지 말고 기업의 변화·가격 국면·핵심 쟁점과 반대 근거부터 해석해줘. 7항목을 한꺼번에 묻지 말고 차트·보유 이유·반증·대응에서 이번 판단을 바꾸는 모호함만 자연스럽게 논의해줘. "
                  "새 조사 필요 때만 stock-qualitative-analysis를 사용하고 미정 판단을 대신 채택하지 마. "
                  "확보한 발언 원문과 합의·미정을 analysis_save_discussion으로 저장하고 계획은 analysis_save_plan으로 초안 버전을 남겨줘. "
                  "사용자 직접 작도 또는 analysis_propose의 박스·평행 채널·손익비 초안을 활용하고 저장/채택/실제 체결을 구분해줘. "
                  "가격 경고는 트레일링·MA5와 별도 보유/매수 시나리오의 최신 완료 거래일 종가 판정이다. 접근 기본값은 조정 가능한 0.5×ATR14이며 기울어진 선은 해당 거래일 값으로 비교한다. "
                  "감시 연결은 사용자가 화면에서 확정 시나리오와 저장 작도·허용폭을 확인한다. 저장된 작도 수정은 활성 참조만 갱신하며 당시 snapshot/계획 원본은 보존한다. 폐기한 구 위험 계산 도구는 복구하지 마. "
                  "snapshot_id와 expected_revision을 고정하며 충돌은 재조회해 조정한다. 저장 뒤 ID·버전·재조회 일치와 복귀 링크를 확인해줘. "
                  + FOLLOWUP + f"계획 채택은 사용자가 화면에서 정확한 버전을 확인한다. 대시보드: {dashboard}")
        return {'analysis_key':cid,'dashboard_url':dashboard,'reopen_command':f'.\\analysis.ps1 -AnalysisKey {cid}',
                'resume_prompt':resume, 'resume_codex_url':'codex://new?'+urlencode({'path':str(self.root),'prompt':resume}),
                'copy_text':f'분석건 {cid}\n이 분석건의 현재 차트를 다시 봐줘.\n{dashboard}\n포트 변경/서버 종료 시 워크벤치에서 .\\analysis.ps1 -AnalysisKey {cid}',
                'codex_url':'codex://new?'+urlencode({'path':str(self.root),'prompt':prompt}), 'prompt':prompt,
                'qualitative_skill':QUALITATIVE_SKILL}

    def capture(self, case):
        data,stocks=self.view(); stocks={**self.histories.saved.stocks,**stocks}; k=case['stock_key']; ctx=case['context']
        if k not in stocks: raise ValueError('stock_not_available')
        chart=self.histories.view(stocks[k],ctx['timeframe'])
        workspace=self.store.shared(k,ctx['timeframe']); financial=self.financials.read(k)
        daily=chart if ctx['timeframe']=='1d' else self.histories.view(stocks[k],'1d')
        events=price_events(daily,financial)
        value={'id':ident('sn_'),'case_id':case['id'],'captured_at':now(),'stock_key':k,'context':ctx,
               'instrument':{field:stocks[k].get(field) for field in ('name','symbol','market','currency','isin_code','isin','exchange') if stocks[k].get(field) is not None},
               'price_events':events,'workspace':workspace,'chart':chart,'financials':financial,'accounts':data['accounts'],
               'market':{k:v for k,v in data['market'].items() if k in ('state','session_dates','source','errors')},
               'geometry':geometry(workspace,chart,ctx),'authority':'saved_input_not_trade_confirmation',
               'component_hashes':{'price_events':digest(events),'chart':digest(chart),'workspace':digest(workspace),'financials':digest(financial),'accounts':digest(data['accounts'])}}
        with closing(self.store.connect()) as db,db:
            db.execute('INSERT INTO analysis_snapshots VALUES (?,?,?,?)',(value['id'],case['id'],packed(value),digest(value)))
        return value

    def detail(self, case_id, origin):
        case=self.case(case_id); items=self.rows('analysis_items',case_id)
        with closing(self.store.connect()) as db:
            snapshots=[dict(id=r[0],captured_at=r[1],component_hashes=json.loads(r[2])) for r in db.execute("SELECT id,json_extract(payload,'$.captured_at'),json_extract(payload,'$.component_hashes') FROM analysis_snapshots WHERE case_id=? ORDER BY rowid",(case_id,))]
        return {'case':case,'links':self.links(case,origin),'items':items,
                'snapshots':[{k:s[k] for k in ('id','captured_at','component_hashes')} for s in snapshots],
                'qualitative_skill':QUALITATIVE_SKILL,
                'qualitative_state':'records_available' if any(i['kind']=='report' for i in items) else 'ready'}

    def item(self, case_id, item_id):
        key(item_id,'it_')
        return next((i for i in self.rows('analysis_items',case_id) if i['id']==item_id),None)

    def append(self, case_id, kind, payload, db=None):
        value={**payload,'id':ident('it_'),'case_id':case_id,'kind':kind,'created_at':now()}
        if db is not None: db.execute('INSERT INTO analysis_items VALUES (?,?,?,?)',(value['id'],case_id,kind,packed(value)))
        else:
            with closing(self.store.connect()) as conn,conn: conn.execute('INSERT INTO analysis_items VALUES (?,?,?,?)',(value['id'],case_id,kind,packed(value)))
        return value

    def proposal(self, case, snapshot_id, drawings, rationale):
        snap=self.snapshot(case['id'],snapshot_id)
        validate({'schema':1,'drawings':drawings,'averages':[]},drawing_limit=30)
        if not drawings: raise ValueError('empty_proposal')
        short(rationale)
        return self.append(case['id'],'proposal',{'snapshot_id':snapshot_id,'base_revision':snap['workspace']['revision'],
                    'drawings':drawings,'rationale':rationale,'author':'llm','status':'proposed', 'test_record':case['test_record']})

    def decide(self, case, proposal_id, action, reason, drawings=None):
        if action not in ('adopt','discard'): raise ValueError('invalid_decision')
        short(reason)
        proposal=self.item(case['id'],proposal_id)
        if not proposal or proposal['kind']!='proposal': raise ValueError('proposal_not_found')
        if drawings is not None: validate({'schema':1,'drawings':drawings,'averages':[]},drawing_limit=30)
        with closing(self.store.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            items=[json.loads(r[0]) for r in db.execute('SELECT payload FROM analysis_items WHERE case_id=? AND kind=?',(case['id'],'decision'))]
            if any(i['proposal_id']==proposal_id for i in items): raise Conflict('proposal_already_decided')
            row=db.execute('SELECT revision,payload,updated_at FROM shared_chart_workspaces WHERE identity=?',(case['stock_key'],)).fetchone()
            if not row: raise Conflict('workspace_missing')
            revision,payload,updated=row[0],json.loads(row[1]),row[2]
            added=[]
            if action=='adopt':
                if revision!=proposal['base_revision']: raise Conflict('stale_proposal_read_current_and_resubmit')
                for d in drawings if drawings is not None else proposal['drawings']:
                    added.append(dict(d,id=ident('ai_')))
                if not added: raise ValueError('empty_proposal')
                before=deepcopy(payload); payload['drawings']+=added
                validate({'schema':1,'drawings':payload['drawings'],'averages':payload['averages'][case['context']['timeframe']]},drawing_limit=300)
                db.execute('INSERT OR IGNORE INTO chart_workspace_versions VALUES (?,?,?,?)', (case['stock_key'],revision,packed(before),updated))
                db.execute('INSERT INTO chart_workspace_versions VALUES (?,?,?,?)', (case['stock_key'],revision+1,packed(payload),now()))
                db.execute('UPDATE shared_chart_workspaces SET revision=?,payload=?,updated_at=? WHERE identity=?',(revision+1,packed(payload),now(),case['stock_key']))
            else: before=None
            return self.append(case['id'],'decision',{'proposal_id':proposal_id,'action':action,'reason':reason,'author':'user',
                'snapshot_id':proposal['snapshot_id'],'base_revision':revision,'result_revision':revision+(action=='adopt'),
                'adopted_drawings':added,'original_workspace':before,'edited_proposal':drawings,'authority':'drawing_only_not_trade_or_risk_adoption'},db)

    def record(self, case, a):
        if set(a)-{'analysis_key','snapshot_id','kind','title','body','author','llm_opinion','user_response','sources','event_research'}:
            raise ValueError('invalid_record_fields')
        snap=self.snapshot(case['id'],a['snapshot_id']); kind=a['kind']
        if kind not in ('report','review'): raise ValueError('invalid_record_kind')
        title=short(a['title'],200); body=short(a['body'],50000)
        if a['author'] not in ('llm','user'): raise ValueError('invalid_record_author')
        opinion=short(a.get('llm_opinion',''),12000)
        user=a.get('user_response',{})
        if not isinstance(user,dict) or set(user)-{'agreement','counterexample','change_reason','adoption'}: raise ValueError('invalid_user_response')
        for v in user.values(): short(v,8000)
        sources=a.get('sources',[])
        if not isinstance(sources,list) or len(sources)>50: raise ValueError('invalid_sources')
        for s in sources:
            if not isinstance(s,dict) or set(s)-{'label','url','as_of'}: raise ValueError('invalid_source')
            for v in s.values(): short(v,2000)
        research=validate_research(a.get('event_research',[]),snap.get('price_events'))
        iid=ident('it_'); path=self.root/'judgments'/'analyses'/case['id']/(iid+'.md')
        value={'event_research':research,'id':iid,'case_id':case['id'],'kind':kind,'snapshot_id':snap['id'],'created_at':now(),'title':title,'body':body,'author':a['author'],
               'llm_opinion':opinion,'user_response':user,'sources':sources,'test_record':case['test_record'],
               'authority':'narrative_only_not_verified_identity_or_plan_adoption',
               'markdown_path':str(path.relative_to(self.root)).replace('\\','/'),'reading_format':2}
        text=readable(value,digest(snap),snap['workspace']['revision'])
        value['markdown_sha256']=hashlib.sha256(text.encode()).hexdigest()
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8',newline='\n') as f: f.write(text)
        try:
            with closing(self.store.connect()) as db,db: db.execute('INSERT INTO analysis_items VALUES (?,?,?,?)',(iid,case['id'],kind,packed(value)))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return value

    def report_document(self, cid, record_id=None):
        records=[r for r in self.rows('analysis_items',cid) if r['kind'] in ('report','review')]
        record=next((r for r in records if r['id']==record_id),None) if record_id else (records[-1] if records else None)
        if record is None: raise ValueError('report_not_found')
        original=(self.root/record['markdown_path']).resolve()
        if not original.is_relative_to((self.root/'judgments'/'analyses').resolve()): raise ValueError('invalid_report_path')
        if hashlib.sha256(original.read_bytes()).hexdigest()!=record['markdown_sha256']: raise ValueError('report_integrity_failed')
        path=original
        if record.get('reading_format')!=2:
            path=original.with_suffix('.reading-v2.md')
            rendered=readable(record)
            if not path.exists():
                try:
                    with path.open('x',encoding='utf-8',newline='\n') as f:f.write(rendered)
                except FileExistsError:pass
            if path.read_text(encoding='utf-8')!=rendered: raise ValueError('reading_copy_integrity_failed')
        return dict(record_id=record['id'],snapshot_id=record['snapshot_id'],path=str(path),original_path=str(original),
                    open_in_codex=dict(target=dict(type='file',path=str(path)),placement='right'),
                    next_step=FOLLOWUP)

    def holding(self, case, a):
        snap=self.snapshot(case['id'],a['snapshot_id'])
        if a.get('confirmed_by_user') is not True: raise ValueError('explicit_user_confirmation_required')
        account=next((x for x in snap['accounts']['accounts'] if x['alias']==a['account_alias']),None)
        market,symbol=case['stock_key'].split(':',1)
        pos=next((x for x in (account or {}).get('positions',[]) if x.get('market')==market and (x.get('quote_symbol') or x.get('symbol'))==symbol),None)
        if not pos or Decimal(str(pos.get('quantity',0)))<=0 or pos.get('data_unavailable_reason'): raise ValueError('observed_holding_required')
        scenario=self.snapshot(case['id'],a['adopted_snapshot_id'])
        return self.append(case['id'],'holding_link',{'snapshot_id':snap['id'],'adopted_snapshot_id':scenario['id'],
               'account_alias':a['account_alias'],'observed_position':pos,'as_of':snap['accounts']['as_of'],
               'thesis':short(a['thesis'],12000),'invalidation':short(a['invalidation'],12000),
               'confirmation':short(a['confirmation'],4000),'author':'user','test_record':case['test_record'],'authority':'user_attested_link_to_observed_holding_not_execution_evidence'})

    def dispatch(self, op, a, origin, *, principal='legacy', session=None):
        if op in ('risk_overview','risk_calculate','risk_save','risk_review_save','calculate'):
            raise ValueError('retired_risk_operation')
        if not isinstance(a,dict): raise ValueError('object_required')
        from trade_cycle_api import OPS as CYCLE_OPS, dispatch as cycle_dispatch
        if op in CYCLE_OPS:
            return cycle_dispatch(self, op, a, origin, principal, session)
        from scenario_api import OPS as MONITOR_OPS, dispatch as monitor_dispatch
        if op in MONITOR_OPS:
            return monitor_dispatch(self, op, a, origin, principal, session)
        if op == 'scenario_draft':
            return self.collaboration.scenario_draft(a, self.case(a['analysis_key']), principal, session, origin)
        if op in COLLABORATION_OPS:
            return self.collaboration.dispatch(op, a, origin, principal, session)
        if principal == 'risk' and op not in ('list','detail','holding_links','calculate','read'):
            raise Forbidden('risk_cannot_write_collaboration_or_legacy_records')
        if principal == 'risk' and op == 'read' and not a.get('snapshot_id'):
            raise Forbidden('risk_read_requires_frozen_snapshot')
        if op in ('decide','holding') and principal not in ('browser','legacy'):
            raise Forbidden('local_browser_confirmation_required')
        if op=='list':
            cases=self.rows('analysis_cases')
            if a.get('stock_key'): cases=[c for c in cases if c['stock_key']==a['stock_key']]
            results=[]
            for c in cases:
                items=self.rows('analysis_items', c['id'])
                latest=items[-1] if items else None
                results.append({**c, 'updated_at':latest['created_at'] if latest else c['created_at'],
                    'report_count':sum(i['kind']=='report' for i in items),
                    'latest_record_id':latest['id'] if latest else None, 'links':self.links(c,origin)})
            return {'cases':results}
        if op=='create':
            k=a['stock_key']; _,stocks=self.view()
            if k not in stocks and k not in self.histories.saved.stocks: raise ValueError('unknown_stock')
            if type(a.get('test_record',False)) is not bool: raise ValueError('invalid_test_flag')
            case={'id':ident('an_'),'stock_key':k,'title':short(a.get('title',k),200),'context':context(a.get('context',{})),
                  'created_at':now(),'test_record':a.get('test_record',False),'qualitative_skill':QUALITATIVE_SKILL}
            with closing(self.store.connect()) as db,db: db.execute('INSERT INTO analysis_cases VALUES (?,?,?)',(case['id'],k,packed(case)))
            self.capture(case)
            return self.detail(case['id'],origin)
        if op=='holding_links':
            items=self.rows('analysis_items'); cases={c['id']:c for c in self.rows('analysis_cases')}
            return {'links':[{**i,'stock_key':cases[i['case_id']]['stock_key'],'links':self.links(cases[i['case_id']],origin)} for i in items if i['kind']=='holding_link' and (not a.get('account_alias') or i['account_alias']==a['account_alias'])]}
        case=self.case(a['analysis_key']); cid=case['id']
        if op=='detail': return self.detail(cid,origin)
        if op=='report_document': return self.report_document(cid,a.get('record_id'))
        if op=='context':
            case['context']=context(a['context'])
            with closing(self.store.connect()) as db,db: db.execute('UPDATE analysis_cases SET payload=? WHERE id=?',(packed(case),cid))
            return self.detail(cid,origin)
        if op=='read':
            snap=self.snapshot(cid,a['snapshot_id']) if a.get('snapshot_id') else self.capture(case)
            section=a.get('section','summary'); offset=a.get('offset',0); limit=a.get('limit',200)
            if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=1000: raise ValueError('invalid_pagination')
            base={'analysis_key':cid,'snapshot_id':snap['id'],'captured_at':snap['captured_at'],'context':snap['context'],'component_hashes':snap['component_hashes'],'links':self.links(case,origin),'mode':'historical' if a.get('snapshot_id') else 'current_saved'}
            if section=='summary':
                ev=snap.get('price_events')
                base['price_events']={k:v for k,v in ev.items() if k not in ('events','groups')} if ev else {'state':'not_in_legacy_snapshot'}
                base['price_events'].update(event_count=len((ev or {}).get('events',[])),group_count=len((ev or {}).get('groups',[])))
                base.update(workspace=snap['workspace'],geometry=snap['geometry'],financials={k:v for k,v in snap['financials'].items() if k!='result'},financial_snapshot_id=(snap['financials'].get('result') or {}).get('snapshot_id'),chart={k:v for k,v in snap['chart'].items() if k!='bars'},bar_count=len(snap['chart']['bars']),latest_bars=snap['chart']['bars'][-5:],accounts_as_of=snap['accounts'].get('as_of'))
            elif section in ('events','research_groups','news_inputs'):
                ev=snap.get('price_events')
                base['price_events']={k:v for k,v in (ev or {}).items() if k not in ('events','groups')} if ev else {'state':'not_in_legacy_snapshot'}
                if section=='news_inputs': base['news_inputs']=(ev or {}).get('news_inputs',{})
                else:
                    field='events' if section=='events' else 'groups'
                    rows=(ev or {}).get(field,[]); page=rows[offset:offset+limit]; end=offset+len(page)
                    base.update(field=field,total=len(rows),offset=offset,next_offset=end if end<len(rows) else None)
                    base[field]=page
            elif section=='chart':
                bars=snap['chart']['bars']; page=bars[offset:offset+limit]; end=offset+len(page)
                base.update(chart={**snap['chart'],'bars':page},total=len(bars),offset=offset,
                            next_offset=end if end<len(bars) else None)
            elif section == 'financials' or section in ('financial_annual','financial_quarterly','financial_sources'):
                packet = snap['financials']; result = packet.get('result') or {}
                field = {'financials':'summary','financial_annual':'annual','financial_quarterly':'quarterly','financial_sources':'sources'}[section]
                items = result.get(field, []); page_size = min(limit, 20 if field == 'summary' else 5)
                base['financials'] = {k:v for k,v in packet.items() if k!='result'}
                base['financials']['result'] = {k:v for k,v in result.items() if k not in ('summary','annual','quarterly','sources','ttm')}
                base['financials']['result'][field] = items[offset:offset+page_size]
                base.update(field=field,total=len(items),offset=offset,next_offset=offset+page_size if offset+page_size<len(items) else None)
            elif section in ('accounts','workspace','geometry'): base[section]=snap[section]
            else: raise ValueError('invalid_section')
            return base
        if op=='refresh':
            _, stocks = self.view(); stocks = {**self.histories.saved.stocks, **stocks}
            stock = stocks[case['stock_key']]; mode = a['mode']
            if mode == 'financial_sources':
                source = {**stock, **self.histories.view(stock, '1d')}
                if case['stock_key'] == 'KR:005930.KS':
                    from financial_summary import selected_stock
                    _, source = selected_stock('KR', '005930.KS', self.histories)
                return self.financials.start(case['stock_key'], source, refresh=True)
            if mode == 'price_history':
                value = self.histories.ensure(stock, retry=True)
                return {'history':value['history'], 'scope':'selected_stock_saved_cutoff_only_not_daily_market_refresh'}
            if mode == 'financial_price_recalculate':
                saved = self.financials.read(case['stock_key'])
                sid = (saved.get('result') or {}).get('snapshot_id')
                if not sid or not re.fullmatch('[a-f0-9]{32}', sid): raise ValueError('saved_financial_required')
                seed = self.financials.folder(case['stock_key']) / 'snapshots' / sid
                source = json.loads((seed / 'saved-price.json').read_bytes())
                source.update(self.histories.view(stock, '1d'))
                value = self.financials.import_collected(case['stock_key'], source, seed)
                return {'state':value['state'],'attempt':value['attempt'],'snapshot_id':(value.get('result') or {}).get('snapshot_id'),'scope':'saved_disclosures_and_saved_prices_no_network'}
            raise ValueError('invalid_refresh_mode')
        if op=='propose': return self.proposal(case,a['snapshot_id'],a['drawings'],a['rationale'])
        if op=='decide': return self.decide(case,a['proposal_id'],a['action'],a.get('reason',''),a.get('drawings'))
        if op=='record':
            record=self.record(case,a)
            return {**record,'links':self.links(case,origin),'document':self.report_document(cid,record['id'])}
        if op=='holding': return self.holding(case,a)
        raise ValueError('unknown_analysis_operation')
