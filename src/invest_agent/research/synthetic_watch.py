"""Invented daily bars: no actual securities, prices or exchange calendars."""
from datetime import date, timedelta
import math

def series(values,volumes=None):
    return [{'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),'open':c-.15,'high':c+.4,'low':c-.4,'close':c,
             'volume':volumes[i] if volumes else 10000} for i,c in enumerate(values)]

def vcp():
    values=[60+i*.5 for i in range(300)]
    for amplitude in (8,4,1.5):values += [210+amplitude*math.cos(i*2*math.pi/9) for i in range(10)]
    volume=[10000]*320+[2000]*10
    values += [220.];volume += [40000]
    b=series(values,volume);b[-1].update(open=215,low=214.8,high=220.5)
    return b

def cup():
    values=[25+i*.6 for i in range(250)]
    values += [175-35*math.sin(math.pi*i/59) for i in range(60)]
    values += [174,173,171,170,169,169,170,171,172,173]
    volumes=[10000]*310+[2000]*10
    values += [176.];volumes += [40000]
    b=series(values,volumes);b[-1].update(open=174.5,low=174,high=176.3)
    return b

def spr(b):
    return {'windows':{'126':{'stage':'S4','completion_date':b[-1]['date'],'completion_level':218.5,'retest_low':210.,
        'retest_confirmed_at':b[-3]['date'],'retest_pivot_date':b[-5]['date'],'anchor_high_a_date':b[-50]['date'],
        'completion_source_date':b[-10]['date']}}}

def bars(kind='bottom'):
    if kind=='bottom':prices=[180.]*220+[180.-80*i/59 for i in range(60)]
    else:prices=[60.+110*i/264 for i in range(265)]+[170.-20*i/14 for i in range(15)]
    floor=100 if kind=='bottom' else 148
    prices += [floor+(0,2,5,8,6,3,1,4)[i%8] for i in range(39)]+[floor+4]
    return [dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=c,close=c,high=c+1,low=c-1,volume=100000.) for i,c in enumerate(prices)]

