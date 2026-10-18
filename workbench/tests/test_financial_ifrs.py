import copy,unittest
from financial_sec_ifrs import reporting_currency,statement_tables
from financial_sec_filing import inline_facts
from financial_model import sec_normalize
from financial_focus import summarize


class IFRS(unittest.TestCase):
 def source(self):
  def row(value,start='2025-01-01',end='2025-12-31'):
   return dict(val=value,start=start,end=end,form='20-F',filed='2026-02-01',accn='a')
  tax={key:dict(units={'DKK':[row(value)]}) for key,value in
       [('Revenue',100),('ProfitLossFromOperatingActivities',40),('ProfitLossAttributableToOwnersOfParent',30),('ProfitLoss',31)]}
  tax['Revenue']['units']['DKK'].append(row(80,'2024-01-01','2024-12-31'))
  tax['Revenue']['units']['USD']=[row(14)]
  for tag,value in [('BasicEarningsLossPerShare',3),('DilutedEarningsLossPerShare',2.9)]:tax[tag]=dict(units={'DKK/shares':[row(value)]})
  for tag,value in [('Assets',200),('Liabilities',80),('Equity',120),('CurrentAssets',50),('CurrentLiabilities',20)]:
   x=row(value);x.pop('start');tax[tag]=dict(units={'DKK':[x]})
  return dict(provider='SEC',identity=dict(cik='353278',name='Novo Nordisk A/S',sic='2834'),facts=dict(cik=353278,facts={'ifrs-full':tax}),security_basis={'per_share':'ordinary_share','notice':'공시 통화·보통주'})
 def test_other_ifrs_currency_metrics_and_no_source_mutation(self):
  s=self.source();original=copy.deepcopy(s);r=summarize(sec_normalize(s),dict(market='US',symbol='NVO',currency='USD',bars=[]))
  self.assertEqual(s,original);self.assertEqual(r['currency'],'DKK');self.assertEqual(r['standard'],'IFRS · 연결')
  a=r['annual'][-1];self.assertEqual(a['values']['sales']['value'],100);self.assertEqual(a['values']['eps']['value'],2.9)
  self.assertEqual(a['metrics']['operating_margin']['value'],.4);self.assertEqual(a['metrics']['current_ratio']['value'],2.5)
  self.assertEqual(a['values']['sales']['reported_tag'],'ifrs-full:Revenue')
 def test_ambiguous_currency_refused(self):
  s=self.source();s['facts']['facts']['ifrs-full']['Revenue']['units']['USD'].append(dict(s['facts']['facts']['ifrs-full']['Revenue']['units']['DKK'][1]))
  with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):reporting_currency(s)
 def test_inline_entity_dimension_unit_and_zero_validation(self):
  raw='''<xbrli:context id="ok"><xbrli:entity><xbrli:identifier>353278</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="segment"><xbrli:entity><xbrli:identifier>353278</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="ifrs-full:ClassesOfOrdinarySharesAxis">x:Segment</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:unit id="dkk"><xbrli:measure>iso4217:DKK</xbrli:measure></xbrli:unit><xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="ok" unitRef="dkk" scale="6">100</ix:nonFraction>
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="segment" unitRef="dkk">999</ix:nonFraction>
  <ix:nonFraction name="ifrs-full:Revenue" contextRef="ok" unitRef="usd">14</ix:nonFraction>
  <ix:nonFraction name="ifrs-full:PurchaseOfTreasuryShares" contextRef="ok" unitRef="dkk" format="ixt:zeroDash">—</ix:nonFraction>'''
  f=dict(accessionNumber='a',filingDate='2026-02-01',form='20-F')
  rows=inline_facts(raw,353278,f,'a.html','DKK',taxonomy='ifrs-full');self.assertEqual([r['val'] for r in rows],[100_000_000,0])
  self.assertEqual(inline_facts(raw,999,f,'a.html','DKK',taxonomy='ifrs-full'),[])
  precise=raw.replace('>100</ix:nonFraction>','>1,618,206.8</ix:nonFraction>'); self.assertEqual(inline_facts(precise,353278,f,'a.html','DKK',taxonomy='ifrs-full')[0]['val'],1_618_206_800_000)
  fragment='<ix:nonFraction name="ifrs-full:Revenue" contextRef="ok" unitRef="dkk" scale="6">100</ix:nonFraction>'
  self.assertEqual(inline_facts(fragment,353278,f,'attachment.html','DKK',taxonomy='ifrs-full',resource_raw=raw)[0]['val'],100_000_000)
  self.assertEqual(inline_facts(fragment,999,f,'attachment.html','DKK',taxonomy='ifrs-full',resource_raw=raw),[])
 def test_table_exact_currency_period_amount_columns_and_issuer(self):
  raw='''<p>Novo Nordisk A/S and Subsidiaries</p><p>CONSOLIDATED STATEMENTS OF INCOME</p><p>In Millions of Danish kroner</p><table>
  <tr><td></td><td>Three Months Ended June 30</td><td></td><td>Six Months Ended June 30</td></tr>
  <tr><td></td><td>2026</td><td>2025</td><td>2026</td><td>2025</td></tr>
  <tr><td></td><td>Amount</td><td>Amount</td><td>Amount</td><td>Amount</td></tr>
  <tr><td>Revenue</td><td>100</td><td>80</td><td>180</td><td>140</td></tr>
  <tr><td>Basic earnings per share</td><td>3.25</td><td>2.5</td><td>6.5</td><td>5</td></tr></table>'''
  identity={'name':'Novo Nordisk A S'};f=dict(reportDate='2026-06-30',accessionNumber='q',filingDate='2026-08-01',form='6-K')
  rows=statement_tables(raw,identity,f,'q.html','DKK');self.assertEqual(len(rows),8)
  self.assertEqual(rows[0]['start'],'2026-04-01');self.assertEqual(rows[2]['start'],'2026-01-01')
  self.assertEqual(rows[0]['val'],100_000_000);self.assertEqual(rows[4]['val'],3.25)
  self.assertEqual(statement_tables(raw,identity,f,'q.html','USD'),[])
  self.assertEqual(statement_tables(raw,{'name':'Other issuer'},f,'q.html','DKK'),[])
  split='''<p>Novo Nordisk A/S</p><p>CONSOLIDATED STATEMENTS OF INCOME</p><p>In Millions of Danish kroner</p><table><tr><td></td><td colspan=4>Three Months Ended June 30</td></tr><tr><td></td><td colspan=2>2026</td><td colspan=2>2025</td></tr><tr><td></td><td colspan=2>Amount</td><td colspan=2>Amount</td></tr><tr><td>Revenue</td><td>$</td><td>(100)</td><td>$</td><td>80</td></tr><tr><td>Operating profit</td><td>$</td><td>40</td><td>$</td><td>30</td></tr></table>'''
  self.assertEqual(statement_tables(split,identity,f,'split.html','DKK')[0]['val'],-100_000_000)


 def test_condensed_ias34_periods_balance_and_adjusted_exclusion(self):
  raw="""<p>Novo Nordisk A/S</p><p>These condensed consolidated financial statements have been prepared in accordance with IAS 34.</p><p>APPENDIX 1: CONDENSED INCOME STATEMENT</p><table>
  <tr><td>DKK million</td><td colspan=2>Q2 2026</td><td colspan=2>Q2 2025</td><td colspan=2>H1 2026</td></tr>
  <tr><td></td></tr><tr><td></td></tr>
  <tr><td>Net sales</td><td colspan=2>100</td><td colspan=2>80</td><td colspan=2>180</td></tr>
  <tr><td>Operating profit</td><td colspan=2>40</td><td colspan=2>30</td><td colspan=2>70</td></tr>
  <tr><td>Adjusted operating profit</td><td colspan=2>999</td><td colspan=2>888</td><td colspan=2>777</td></tr>
  <tr><td>Diluted earnings per share (DKK)</td><td colspan=2>3.25</td><td colspan=2>2.5</td><td colspan=2>6.5</td></tr></table>
  <p>APPENDIX 3: CONDENSED BALANCE SHEET</p><table><tr><td>DKK million</td><td colspan=2>30 Jun 2026</td><td colspan=2>31 Dec 2025</td></tr>
  <tr><td></td></tr><tr><td></td></tr><tr><td>Total assets</td><td colspan=2>200</td><td colspan=2>180</td></tr><tr><td>Total liabilities</td><td colspan=2>80</td><td colspan=2>70</td></tr></table>"""
  f=dict(reportDate='2026-06-30',accessionNumber='q',filingDate='2026-08-01',form='6-K')
  rows=statement_tables(raw,{'name':'Novo Nordisk A S'},f,'q.html','DKK')
  self.assertEqual(len(rows),13);self.assertEqual(rows[0]['start'],'2026-04-01');self.assertEqual(rows[2]['start'],'2026-01-01')
  self.assertEqual(rows[6]['val'],3.25);self.assertEqual(rows[9]['end'],'2026-06-30')
  self.assertEqual(statement_tables(raw.replace('condensed consolidated financial statements','separate financial statements'),{'name':'Novo Nordisk A S'},f,'q.html','DKK'),[])


 def test_ifrs_eps_stays_with_selected_income_cohort(self):
  from financial_cohort import select_bundles
  def cell(value,filing,unit='TWD'):
   return dict(value=value,accession=filing,filed='2026-04-01' if filing=='new' else '2026-02-01',start='2025-01-01',end='2025-12-31',unit=unit,accounting_basis='IFRS')
  values={k:cell(v,'old') for k,v in [('sales',100),('operating',40),('income',30),('net_income',31)]}
  for key in ('basic_eps','eps'):
   values[key]=dict(cell(3.2,'new','TWD/share'),disclosure_candidates=[cell(3,'old','TWD/share')])
  select_bundles(values)
  self.assertEqual(values['eps']['value'],3);self.assertEqual(values['eps']['selected_cohort'],'old')
  self.assertEqual(values['eps']['latest_disclosure']['value'],3.2)

if __name__=='__main__':unittest.main()
