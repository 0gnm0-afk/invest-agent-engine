"""Readable Markdown derived from saved report fields; never edits source evidence."""
import json
import re

FOLLOWUP = ('보고서 저장/재조회 뒤 종료하지 말고 같은 세션에서 보고서를 열고 논의를 시작해줘. '
    '반환 document.open_in_codex 인수로 mcp__codex_app__open_in_codex를 호출해 현재 세션 우측 패널에 Markdown을 열어줘. '
    '기존 보고서는 analysis_report_document(analysis_key, record_id)로 읽기 문서를 얻어줘. '
    '도구 목록이 오래됐으면 저장 markdown_path를 작업 폴더 기준 절대 경로로 열고, 패널 미지원이면 채팅에서 충분히 설명해줘. '
    '사용자가 따로 묻기 전에 최근 주가 움직임의 시작부터 최신 확보 완료봉까지 상승/하락의 사업·시장 재료, 그 가설의 현재 유효성과 반대 근거를 설명해줘. '
    '기존 조사 자료를 재사용하고 필요한 최근 근거만 보완하며, 사업 재료의 지속·뉴스의 신선도·가격 반영 정도를 구별해줘. '
    '이평선·채널은 이 해석과 연결하고 곧바로 지지선/진입가격 양자택일로 논의를 좁히지 마. AGENTS의 최근 차트 상담 규칙에 따라 실제 판단을 바꾸는 질문 하나로 사용자 반응을 기다려줘. '
    '저장 ID/링크 안내나 “원하면 추가 분석”으로 끝내지 마. 사용자 답을 받은 뒤에만 합의/미정과 발언 원문을 기존 논의 기능에 저장하고 '
    '필요한 작도 초안·시나리오로 발전시켜줘. 확정·감시 등록은 기존 사용자 화면 확인을 유지해줘. '
    '보고서만 요청하거나 대화를 원하지 않는다고 명시하면 질문을 강제하지 마.')


def readable(record, snapshot_hash=None, workspace_revision=None):
    body=record['body'].strip()
    # The title is already a structured field; don't render a second H1.
    body=re.sub(r'^# [^\n]+\n+', '', body, count=1)
    parts=['# '+record['title'], body]
    if record.get('test_record'): parts.insert(1,'> 검증용 자료 · 실제 투자 분석 아님')
    opinion=(record.get('llm_opinion') or '').strip()
    if opinion and opinion not in body:
        parts.append('<details>\n<summary>보충 의견</summary>\n\n'+opinion+'\n\n</details>')
    user={k:v for k,v in (record.get('user_response') or {}).items() if v and v.strip()}
    if user:
        parts.append('## 실제 사용자 응답\n\n'+'\n'.join('- '+k+': '+v for k,v in user.items()))
    sources=[s for s in record.get('sources',[]) if s.get('url') and s['url'] not in body]
    if sources:
        parts.append('<details>\n<summary>추가 출처</summary>\n\n'+'\n'.join(
            '- ['+s.get('label',s['url'])+']('+s['url']+') · '+s.get('as_of','기준일 미확인') for s in sources)+'\n\n</details>')
    trace={k:record.get(k) for k in ('case_id','id','snapshot_id','test_record')}
    if snapshot_hash: trace['snapshot_sha256']=snapshot_hash
    if workspace_revision is not None: trace['workspace_revision']=workspace_revision
    # Keep IDs/hash available; long event research stays in the canonical DB record.
    parts.append('<details>\n<summary>기록·근거 추적</summary>\n\n```json\n'+json.dumps(trace,ensure_ascii=False,indent=2)+
        '\n```\n\n사건별 조사 상태·검색 범위·원 출처는 같은 기록의 analysis_history에서 확인할 수 있습니다.\n\n</details>')
    return '\n\n'.join(parts)+'\n'
