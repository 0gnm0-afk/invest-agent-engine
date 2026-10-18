window.WorkbenchCollaboration = (() => {
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {identity_basis:'종목·자료 기준',business_evidence:'기업·투자 근거',judgment_invalidation:'현재 판단·반증 조건',purpose_plan:'보유 목적·계획',responses:'조건별 대응',scenario_assumptions:'계산 가정',discussion_history:'논의·변경 이유'};
  const names = {daily_close:'완료 일봉 종가',weekly_close:'완료 주봉 종가',intraday:'장중',lt:'미만',lte:'이하',gt:'초과',gte:'이상',reduce:'축소',buy:'매수',add:'추가매수',hold:'유지',holding:'보유 시나리오',review:'재검토',initial_fraction:'최초 배정 물량 비율',remaining_fraction:'잔여 물량 비율',units:'주',initial:'최초 물량(주)',current_remaining:'잔여 물량(주)'};
  const label = x => esc(names[x] || x || '미정');
  function missing(value) {
    const terms={identity:'종목',stable_id:'안정 식별자',currency:'통화',plan:'계획',purpose:'목적',account_alias:'적용 계좌',allocation:'배정 물량',quantity:'수량',basis:'물량 기준',condition:'조건',manual_assessment_required:'정성 판단 필요',evaluation:'판정 시점',operator:'비교 기준',priority:'적용 순서',price_or_drawing:'가격 또는 작도',value:'물량',execution_price:'가정 체결가격',scenarios:'계산 시나리오',scenario:'시나리오',evaluation_at:'평가 시점',assumed_price:'가정 가격',response:'대응 여부',cost:'총비용',fx_to_base:'환율',fx_as_of:'환율 기준일',price_path:'가격 경로'};
    return value.split('.').map(x=>esc(terms[x]||x)).join(' · ');
  }
  const money = (x, currency) => x == null ? '미정' : currency ? esc(WorkbenchFormat.money(x, currency)) + ' ' + esc(currency) : esc(x) + ' (통화 미정)';
  function planHTML(r) {
    const p = r.plan, currency = r.identity.currency;
    if (r.monitoring_only === true) {
      const lineNames = {low:'하단',high:'상단',line:'기준선',other_line:'평행선',entry:'진입',stop:'무효화',target:'목표'};
      const refs = [...new Map(p.conditions.filter(c=>c.drawing_ref).map(c=>[c.drawing_ref.drawing_id,c.drawing_ref])).values()];
      const responses = [...new Set(p.conditions.map(c=>c.statement).filter(Boolean))];
      return `<h4>${esc(p.title)} · v${r.plan_version}</h4><p>목적 ${label(p.purpose)} · ${p.account_alias?'확인 계좌 '+esc(p.account_alias):'전체 계좌에서 실제 보유 확인'}</p>
        ${refs.map(ref=>`<p>저장 작도 <strong>${esc(ref.drawing_id)}</strong> · v${ref.workspace_revision}<br>시간축 ${esc(ref.timeframe)} · 가격 기준 ${esc(ref.price_basis)} · 경계 ${p.conditions.filter(c=>c.drawing_ref?.drawing_id===ref.drawing_id).map(c=>esc(lineNames[c.drawing_ref.line]||c.drawing_ref.line)).join(' / ')}</p>`).join('')}
        <p><strong>확정할 대응</strong><br>${responses.map(esc).join('<br>')||'대응 기준 미정'}</p>
        <p>최신 완료 거래일 종가로 접근·도달·돌파·이탈을 구분합니다. 기울어진 경계는 해당 거래일의 선 값으로 비교합니다.</p>
        <details><summary>계획 ID · 고정 입력</summary><p>자료 ${esc(r.data_as_of.chart_last||'미확인')} · 입력 ${esc(r.snapshot_id)}<br>계획 ${esc(r.plan_id)} · 기록 ${esc(r.id)}<br>계획 SHA-256 ${esc(r.plan_sha256)}<br>입력 SHA-256 ${esc(r.snapshot_sha256)}</p><pre class="analysis-document">${esc(JSON.stringify(p,null,2))}</pre></details>`;
    }
    return `<h4>${esc(p.title)} · v${r.plan_version}</h4><p>목적 ${label(p.purpose)} · 계좌 ${esc(p.account_alias || '미정')} · 배정 ${esc(p.allocation.quantity ?? '미정')} ${label(p.allocation.basis)}</p>
      ${p.conditions.map(c => `<p><strong>${label(c.action)}</strong> · ${esc(c.statement)}<br>${c.kind === 'qualitative' ? '정성 판단 필요' : `${label(c.evaluation)} · ${c.drawing_ref ? `고정 작도 ${esc(c.drawing_ref.drawing_id)} v${c.drawing_ref.workspace_revision} (${esc(c.drawing_ref.timeframe)}, ${esc(c.drawing_ref.line)}, ${esc(c.drawing_ref.price_basis)})` : money(c.price, c.currency)} ${label(c.operator)}`} · 적용 순서 ${esc(c.priority ?? '미정')}<br>수량 ${esc(c.quantity.value ?? '미정')} (${label(c.quantity.basis)}) · 가정 체결 ${money(c.execution_price,c.currency)}</p>`).join('') || '<p>조건 미정</p>'}
      ${p.scenarios.map(s => `<p>시나리오 ${esc(s.id)} · 평가 ${esc(s.evaluation_at || '미정')} · 가정 가격 ${money(s.assumed_price,s.currency)} · ${s.response === 'plan' ? '계획 대응' : s.response === 'none' ? '무대응' : '대응 미정'} · 환율 ${esc(s.fx_to_base ?? '미정')} (${esc(s.fx_as_of || '기준일 미정')}) · 총비용 ${money(s.cost,s.currency)}<br>가격 경로: ${s.price_path?.map(point=>`${esc(point.at)} → ${money(point.price,s.currency)}`).join(" · ") || "미정"}</p>`).join('')}
      <details><summary>고정 입력과 전체 계획</summary><p>계획 SHA-256 ${esc(r.plan_sha256)}<br>입력 SHA-256 ${esc(r.snapshot_sha256)}</p><pre class="analysis-document">${esc(JSON.stringify(r.plan,null,2))}</pre></details><p>자료 ${esc(r.data_as_of.chart_last || '미정')} · 입력 ${esc(r.snapshot_id)}<br>계획 ID ${esc(r.plan_id)} · 기록 ${esc(r.id)}</p>
      <p>${r.blockers.length ? '미정·보류: ' + r.blockers.map(missing).join(' / ') : '입력된 항목 확인됨 · 계좌 계산 검증은 별도'}</p>`;
  }
  let active = null;
  function clear() { if (active) active.host.remove(); active = null; }
  async function show(parent, caseId, rpc, links) {
    if (!active || active.caseId !== caseId || !active.host.isConnected) {
      clear(); const host = document.createElement('section'); host.className = 'panel'; host.id = 'collaboration-panel';
      host.innerHTML = '<div class="panel-body"><h2>함께 정리한 판단 · 계획</h2><p data-status role="status">종목 기록을 읽는 중…</p><div data-content></div><div data-review></div></div>';
      parent.after(host); active = {host, caseId, revision:null, loading:false, pending:null};
    }
    const state = active;
    if (state.loading) return;
    state.loading = true;
    const valid = () => active === state && state.host.isConnected;
    const status = t => { if (valid()) state.host.querySelector('[data-status]').textContent = t; };
    try {
      const v = await rpc('collaboration_read', {analysis_key:caseId});
      if (!valid()) return;
      if (state.revision === v.revision) return;
      if (state.pending) {state.pending = null; state.host.querySelector('[data-review]').replaceChildren(); status('기록이 바뀌었습니다. 최신 버전을 다시 확인하세요.');}
      const oldNote = state.host.querySelector('[name="note"]')?.value || '';
      state.revision = v.revision;
      const current = v.current, plans = v.plans, activePlans = v.active_plans;
      state.host.querySelector('[data-content]').innerHTML = `<p><a class="button" href="${esc(links.resume_codex_url)}">후속 논의 이어가기</a> <button data-copy>이어갈 문맥 복사</button></p>
        <p>기존 근거에서 대화를 이어갑니다. 아직 정하지 않은 내용도 남길 수 있습니다.</p>
        ${(v.unincorporated_notes || []).map(r=>`<p><strong>요약 이후 새 발언 · 다음 논의에서 확인</strong><br>${esc(r.body)}<br><small>${esc(r.id)}</small></p>`).join('')}
        ${current ? `<p>현재 요약 · ${esc(current.id)} · ${esc(current.created_at)}</p>${Object.entries(current.sections).filter(([,v]) => v !== null).map(([k,value]) => `<p><strong>${esc(labels[k])}</strong><br>${esc(value).replaceAll('\n','<br>')}</p>`).join('')}<h3>미정 질문</h3>${current.open_questions.map(q => '<p>' + esc(q) + '</p>').join('') || '<p>기록된 질문 없음</p>'}` : '<p>아직 후속 요약이 없습니다. 아래 기존 보고서와 발언을 바탕으로 논의를 시작할 수 있습니다.</p>'}
        <h3>사용자가 채택한 계획</h3>${activePlans.map(({record:r,adoption}) => `<article>${planHTML(r)}<small>채택 ${esc(adoption.id)} · ${esc(adoption.created_at)}</small><p><button data-review-plan="${r.id}">채택 버전 확인·해제</button></p></article>`).join('') || '<p>채택한 계획 없음. 보고서와 작도 채택은 매매계획 활성화가 아닙니다.</p>'}
        <h3>계획 초안·대안</h3>${plans.map(r => `<details><summary>${esc(r.plan.title)} · v${r.plan_version} · ${activePlans.some(a=>a.record.id===r.id)?'현재 채택됨':'초안'}</summary>${planHTML(r)}<button data-review-plan="${r.id}">이 버전 검토</button></details>`).join('') || '<p>초안 없음</p>'}
        <details><summary>논의·원문·변경 이력 (${v.records.length})</summary>${v.records.map(r => `<details><summary>${esc(r.title || r.plan?.title || r.kind)} · ${esc(r.principal)} · v${r.revision}</summary><pre class="analysis-document">${esc(r.body || r.reason || '')}</pre>${(r.references || []).map(ref=>`<p>${esc(ref.speaker)} · ${esc(ref.reference)}<br>${esc(ref.text || '참조만 보존됨 · 대화 전체 접근은 보장되지 않음')}</p>`).join('')}<small>${esc(r.id)} · 이전 ${esc(r.previous_record_id || '없음')} · ${esc(r.markdown_path)}</small></details>`).join('')}</details>
        <form data-note><label>논의에 남길 말<textarea name="note" maxlength="20000" required placeholder="생각이 바뀐 이유나 아직 결정하지 못한 내용을 자유롭게 남기세요.">${esc(oldNote)}</textarea></label><button ${v.snapshots.length ? '' : 'disabled'}>발언 저장</button><small>저장된 마지막 자료 입력에 연결합니다. 계획은 채택되지 않습니다.</small></form>
        ${v.related_cases.length ? '<p>같은 종목의 다른 분석건: ' + v.related_cases.map(c=>`<a href="#stock/${encodeURIComponent(v.stock_key.split(':')[0])}/${encodeURIComponent(v.stock_key.split(':')[1])}?analysis=${c.id}">${esc(c.title)}</a>`).join(' · ') + '</p>' : ''}`;
      status(`종목 기록 v${v.revision} · 마지막 저장 요약과 채택 버전을 구분합니다.`);
      state.host.querySelector('[data-copy]').onclick = async () => {try {await navigator.clipboard.writeText(links.resume_prompt);status('후속 논의 문맥을 복사했습니다.');} catch(e) {status(e.message);}};
      state.host.querySelector('[data-note]').onsubmit = async e => {
        e.preventDefault(); const button = e.target.querySelector('button'); button.disabled = true;
        try {
          const saved = await rpc('discussion_note_save', {contract_version:v.contract_version, analysis_key:caseId, snapshot_id:v.snapshots.at(-1).id, expected_revision:v.revision, body:new FormData(e.target).get('note')});
          const reread = await rpc('collaboration_read', {analysis_key:caseId});
          if (!saved.verified || !reread.records.some(r=>r.id===saved.record.id)) throw Error('저장 재조회 불일치');
          if (!valid()) return; e.target.reset(); state.revision=null; await show(parent,caseId,rpc,links); status('발언 저장 확인 · ' + saved.record.id);
        } catch(err) {status(err.message);} finally {button.disabled=false;}
      };
      state.host.querySelectorAll('[data-review-plan]').forEach(b=>b.onclick=async()=>{
        try {
          const review = await rpc('plan_review',{analysis_key:caseId,plan_record_id:b.dataset.reviewPlan,expected_revision:v.revision});
          if (!valid()) return; state.pending=review;
          const isActive=activePlans.some(x=>x.record.id===review.plan.id);
          const reviewHost=state.host.querySelector('[data-review]');
          reviewHost.innerHTML=`<h3>확인할 정확한 버전</h3>${planHTML(review.plan)}<p>이 버전의 ${isActive?'채택을 해제':'계획을 채택'}합니다. 실제 주문·체결·위험 기준 승인은 포함되지 않습니다. 미정 항목은 그대로 남습니다.</p><label>결정 이유<input data-reason maxlength="4000"></label><button data-confirm>${isActive?'이 버전 채택 해제':'이 버전으로 채택'}</button> <button data-cancel>닫기</button>`;
          reviewHost.querySelector('[data-cancel]').onclick=()=>{state.pending=null;reviewHost.replaceChildren();};
          reviewHost.querySelector('[data-confirm]').onclick=async e=>{
            e.target.disabled=true;
            try {
              const saved=await rpc('plan_decide',{contract_version:v.contract_version,analysis_key:caseId,expected_revision:review.revision,confirmation:review.confirmation,action:isActive?'revoke':'adopt',reason:reviewHost.querySelector('[data-reason]').value});
              const reread=await rpc('collaboration_read',{analysis_key:caseId});
              if(!saved.verified||!reread.records.some(r=>r.id===saved.record.id))throw Error('채택 기록 재조회 불일치');
              if(!valid())return;state.pending=null;reviewHost.replaceChildren();state.revision=null;await show(parent,caseId,rpc,links);status('결정 저장 확인 · '+saved.record.id);
            }catch(err){state.pending=null;reviewHost.replaceChildren();status(err.message+' · 최신 버전을 다시 검토하세요.');}
          };
        }catch(err){status(err.message);}
      });
    } catch(e) {status('종목 기록 확인 실패 · ' + e.message);} finally {state.loading=false;}
  }
  return {show,clear,planHTML};
})();
