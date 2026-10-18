'use strict';
window.WorkbenchReportReader=(()=>{
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function inline(text){
  return esc(text).replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');
 }
 function body(text){
  return String(text||'').replace(/^# [^\n]+\n+/,'').split(/\n\s*\n/).map(block=>{
   const heading=/^(#{1,6}) (.+)$/.exec(block);
   if(heading){const n=Math.min(5,Math.max(3,heading[1].length+1));return `<h${n}>${inline(heading[2])}</h${n}>`;}
   if(block.split('\n').every(line=>/^[-*] /.test(line)))return '<ul>'+block.split('\n').map(line=>'<li>'+inline(line.slice(2))+'</li>').join('')+'</ul>';
   return '<p>'+inline(block).replace(/\n/g,'<br>')+'</p>';
  }).join('');
 }
 function record(r){
  const opinion=(r.llm_opinion||'').trim(), user=Object.entries(r.user_response||{}).filter(([,v])=>v?.trim());
  const extra=(r.sources||[]).filter(s=>s.url&&!r.body.includes(s.url));
  return `<article class="report-reading">${body(r.body)}${opinion&&!r.body.includes(opinion)?`<details><summary>보충 의견</summary><p>${inline(opinion)}</p></details>`:''}${user.length?`<h3>실제 사용자 응답</h3>${user.map(([k,v])=>`<p>${esc(k)}: ${inline(v)}</p>`).join('')}`:''}<details><summary>출처·조사 상태·기록 정보</summary>${extra.map(s=>`<p>${inline('['+(s.label||s.url)+']('+s.url+')')} · ${esc(s.as_of)}</p>`).join('')}${(r.event_research||[]).map(e=>`<p>${esc(e.group_id)} · ${esc(e.status)} · ${esc(e.finding||e.failure_reason||'')}</p>`).join('')}<small>입력 ${esc(r.snapshot_id)} · ${esc(r.markdown_path)}</small><button data-replay="${esc(r.snapshot_id)}">당시 자료 보기</button></details></article>`;
 }
 return {body,record};
})();
