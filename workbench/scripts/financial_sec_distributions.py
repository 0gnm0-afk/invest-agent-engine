"""Cash distributions verified against complete financing rows and payment evidence."""
import re
from datetime import date,timedelta
from bs4 import BeautifulSoup
from financial_sec_tables import text


def financing_zeros(raw,parsed,filing,source_id,currency):
    """Zero residual only for a complete, exactly reconciled financing section.

    No inference from an absent tag, no-program statement, or an 'other' row.
    Employee tax withholding is a separate cash-flow category, not repurchases.
    """
    soup=BeautifulSoup(raw,'html.parser');out=[]
    anchors={r.get('context'):r for r in parsed if r['tag']=='NetCashProvidedByUsedInFinancingActivities' and r['unit']==currency and not r.get('common_only')}
    allowed=re.compile(r'^(?:debt issue costs|drawdown of credit lines and loan facilities|repayment of credit lines and loan facilities|proceeds from exercise of equity compensation|share issue costs|repurchase of ordinary shares|share repurchase costs|non-interest-bearing deposits|interest-bearing deposits|short-term borrowings|repayments of long-term debt|issuance for stock options exercised|forward sale common stock issuance|dividends paid|vesting of restricted stock, net of shares held for taxes)$',re.I)
    for ti,table in enumerate(soup.find_all('table')):
        section=[];active=False
        for tr in table.find_all('tr',recursive=False) or table.find_all('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            if not cells:continue
            label=next((text(c) for c in cells if re.search('[A-Za-z]',text(c)) and not c.find('ix:nonfraction')), '')
            if re.fullmatch(r'(?:cash flows (?:used in|provided by) )?financing activities:',label,re.I):active=True;section=[];continue
            if not active:continue
            if re.search(r'net cash.*financing activities',label,re.I):
                for totalnode in tr.find_all('ix:nonfraction'):
                    ctx=totalnode.get('contextref');anchor=anchors.get(ctx)
                    if not anchor or not anchor.get('start'):continue
                    amounts=[];labels=[];valid=True
                    for line_label,line in section:
                        nodes=line.find_all('ix:nonfraction',attrs={'contextref':ctx})
                        if not nodes:
                            if not line.find('ix:nonfraction') and not re.search(r'\d',text(line)):continue
                            valid=False;break
                        if not allowed.fullmatch(line_label) or len(nodes)!=1:valid=False;break
                        node=nodes[0]
                        if node.get('unitref')!=totalnode.get('unitref') or node.get('xsi:nil')=='true':valid=False;break
                        value=re.sub(r'[\s,\u200b]','',node.get_text())
                        if value in ('—','–','-') and node.get('format','').lower().split(':')[-1] in ('zerodash','numdash','fixed-zero'):value='0'
                        if not re.fullmatch(r'\d+(?:\.\d+)?',value):valid=False;break
                        n=float(value)*10**int(node.get('scale','0'))
                        td=node.find_parent(['td','th']);visual=text(td)
                        # XBRL custom debit/credit signs are not cash-flow
                        # presentation signs. Reconcile the displayed table.
                        if '(' in visual or re.match(r'^\s*[-−]\s*\d',visual):n=-n
                        amounts.append(n);labels.append(line_label)
                    if not valid or not amounts or abs(sum(amounts)-anchor['val'])>0.001:continue
                    fields=[]
                    if not any('dividend' in l.lower() for l in labels):fields+=['cash_dividends','dividend']
                    if not any(re.search(r'repurchase|buyback',l,re.I) for l in labels):fields+=['buybacks']
                    if fields:out.append(dict(start=anchor['start'],end=anchor['end'],fields=fields,filed=filing['filingDate'],accession=filing['accessionNumber'],source_id=source_id,
                        formula='전체 재무활동 현금흐름 행과 합계 대사 · 해당 지급 항목 잔액 0',
                        evidence=dict(rule='complete_financing_zero_residual',table=ti,context=ctx,labels=labels,amounts=amounts,total=anchor['val'],currency=currency)))
                active=False
            else:section.append((label,tr))
    return out


def preferred_paid(raw,facts):
    """A declared preferred amount is usable only with same-period payment text."""
    whole=text(BeautifulSoup(raw,'html.parser'));out=[]
    for r in facts:
        if r['tag']!='DividendsPreferredStockCash' or not r.get('start'):continue
        y=r['end'][:4];period=None
        if (r['start'],r['end'])==(y+'-01-01',y+'-12-31'):period=r'During\s+'+y
        for q,word in enumerate(['first','second','third','fourth'],1):
            if (r['start'],r['end'])==(f'{y}-{q*3-2:02d}-01',f'{y}-{q*3:02d}-'+('31' if q in (1,4) else '30')):period=r'During the '+word+r' quarter of '+y
        if not period:continue
        m=re.search(period+r',?\s+we declared and paid (?:a quarterly dividend|dividends) on our outstanding shares of[^.]{0,100}?Preferred Stock of',whole,re.I)
        if m:out.append(dict(r,tag='PaymentsOfDividendsPreferredStockAndPreferenceStock',evidence=dict(rule='explicit_preferred_declared_and_paid',statement=m[0])))
    return out


def common_paid_from_total(facts):
    """Actual total cash paid minus verified preferred cash paid, exact periods."""
    totals=[r for r in facts if r['tag']=='PaymentsOfOrdinaryDividends' and r.get('start')]
    preferred=[r for r in facts if r['tag']=='PaymentsOfDividendsPreferredStockAndPreferenceStock' and r.get('start')]
    out=[]
    for total in totals:
        pool={}
        for p in preferred:
            if p['unit']!=total['unit'] or p['filed']>total['filed'] or not total['start']<=p['start']<=p['end']<=total['end']:continue
            slot=(p['start'],p['end']);old=pool.get(slot)
            if old is None or (p['filed'],p['accn'])>(old['filed'],old['accn']):pool[slot]=p
            elif (p['filed'],p['accn'])==(old['filed'],old['accn']) and p['val']!=old['val']:pool[slot]=dict(old,val=None)
        components=[];cursor=total['start']
        while cursor<=total['end']:
            options=[p for p in pool.values() if p['start']==cursor and p['val'] is not None]
            if not options:break
            chosen=max(options,key=lambda p:p['end']);components.append(chosen)
            cursor=(date.fromisoformat(chosen['end'])+timedelta(days=1)).isoformat()
        if cursor<=total['end'] or not components:continue
        value=total['val']-sum(p['val'] for p in components)
        if value<0:continue
        out.append(dict(total,tag='PaymentsOfDividendsCommonStock',val=value,
            extra_sources=sorted({p.get('source_id','companyfacts.json') for p in components}),
            formula='전체 현금배당 지급액 − 같은 기간 확인된 우선주 현금배당 지급액',
            evidence=dict(rule='cash_paid_total_less_preferred',total=total,preferred=components)))
    return out
