"""Bounded one-request/second Marple replay benchmark. Never controls X-Plane."""
import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from xplane_marple import get_sdk_db
from xplane_live import SIGNALS
from xplane_signals import signal_definitions


def replay_bins(path, seconds):
    samples=[];first=None
    for line in path.open():
        s=json.loads(line);first=first or s['time'];offset=s['time']-first-5_000_000_000
        if offset<0:continue
        if offset>=seconds*1_000_000_000:break
        samples.append((offset,{k:v for k,v in s.items() if k!='time'}))
    if not samples or samples[-1][0]<(seconds-1)*1e9:raise ValueError('Capture too short')
    bins=[[] for _ in range(seconds)]
    for offset,values in samples: bins[offset//1_000_000_000].append((offset,values))
    return bins,sorted({k for _,v in samples for k in v})


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture',type=Path,default=Path('outputs/xplane/20260922T023252-210798Z/flight-001.jsonl'))
    ap.add_argument('--counts',type=int,nargs='+',default=[50,100,200,300,400,480])
    ap.add_argument('--seconds',type=int,default=65)
    args=ap.parse_args()
    bins,names=replay_bins(args.capture,args.seconds)
    rref=[n for n,_,_ in SIGNALS if n in names];extra=[n for n in names if n not in rref]
    random.Random(20260923).shuffle(extra);order=rref+extra
    if any(n>len(names) or n<1 for n in args.counts):ap.error('Count outside captured signal range')
    run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=Path('outputs/xplane/benchmarks')/run;folder.mkdir(parents=True)
    report={'run':run,'source_capture':str(args.capture),'seconds_per_case':args.seconds,
            'interval_s':1,'signal_order':order,'criteria':'All scheduled appends succeed, p95 latency <1s, maximum scheduling lag <0.25s; no automatic append retries. UI rendering is not measured.', 'cases':[]}
    def save(): (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    save();print('Report:',folder/'results.json',flush=True)
    db=get_sdk_db();stream_name='X-Plane Throughput Tests'
    streams=[s for s in db.get_streams() if s.name==stream_name]
    stream=streams[0] if streams else db.create_stream(stream_name,type='realtime',description='Explicitly labelled replay benchmarks; not flight sessions')
    for count in args.counts:
        selected=set(order[:count]);case={'signals':count,'batches':[],'state':'SETUP'};report['cases'].append(case);save()
        dataset=stream.add_dataset(f'Replay_{run}_{count}_signals',metadata={'Flight Type':'Recorded telemetry replay benchmark','signals_tested':count,'source_capture':str(args.capture)})
        case['dataset_id']=dataset.id;save()
        dataset.upsert_signals(signal_definitions(selected,SIGNALS))
        original_post=db.client.post
        response_info={}
        def observed_post(url,*a,**kw):
            if url.endswith('/append'):kw['timeout']=(5,15)
            response=original_post(url,*a,**kw)
            if url.endswith('/append'):
                response_info.clear();response_info['http_status']=response.status_code
                response_info['rate_headers']={k:v for k,v in response.headers.items() if 'ratelimit' in k.lower() or k.lower()=='retry-after'}
                if response.status_code>=400:
                    body=response.text.replace(db.client.api_token,'[REDACTED]')
                    response_info['error_body']=body[:1500]
            return response
        db.client.post=observed_post
        capture=folder/f'{count}-signals.jsonl';now_ns=time.time_ns();base=time.monotonic()
        print('Testing',count,'signals at one append/second',flush=True)
        try:
            with capture.open('x') as file:
                for i,chunk in enumerate(bins):
                    rows=[];samples=[]
                    for offset,values in chunk:
                        kept={k:v for k,v in values.items() if k in selected}
                        if kept:
                            samples.append({'time':now_ns+offset,**kept})
                            rows.extend({'time':now_ns+offset,'signal':k,'value':v} for k,v in kept.items())
                    frame=pd.DataFrame(rows)
                    due=base+i+1
                    time.sleep(max(0,due-time.monotonic()))
                    started=time.monotonic();entry={'second':i+1,'points':len(rows),'schedule_lag_s':max(0,started-due)}
                    response_info.clear()
                    try:
                        dataset.append(frame,shape='long')
                        for s in samples:file.write(json.dumps(s)+'\n')
                    except Exception as exc:
                        entry['error_type']=type(exc).__name__
                    entry.update(response_info);entry['latency_s']=time.monotonic()-started;case['batches'].append(entry);save()
                    if 'error_type' in entry or entry['latency_s']>3 or entry['schedule_lag_s']>1:
                        print('Case stopped:',json.dumps(entry),flush=True);break
        finally:
            db.client.post=original_post
            case['state']='FINALIZING';save()
            try:
                dataset=dataset.cool().wait_for_import(timeout=90)
                case['final_status']=dataset.import_status
            except Exception as exc:case['finalization_error']=type(exc).__name__
            lat=sorted(b['latency_s'] for b in case['batches'])
            case['p95_latency_s']=lat[math.ceil(.95*len(lat))-1] if lat else None
            case['max_lag_s']=max((b['schedule_lag_s'] for b in case['batches']),default=0)
            case['points_per_second']=sum(b['points'] for b in case['batches'])/max(1,len(case['batches']))
            case['passed']=len(case['batches'])==args.seconds and not any('error_type' in b for b in case['batches']) and case['p95_latency_s']<1 and case['max_lag_s']<.25
            case['state']='COMPLETE';save()
        print(json.dumps({k:v for k,v in case.items() if k!='batches'}),flush=True)
        if any(b.get('http_status')==429 for b in case['batches']):
            # Respect a numeric Retry-After if supplied, and avoid immediately probing again.
            delay=max([60]+[float(b.get('rate_headers',{}).get('Retry-After','0')) for b in case['batches'] if b.get('rate_headers',{}).get('Retry-After','0').isdigit()])
            print('Rate limit cooldown:',delay,'seconds',flush=True);time.sleep(delay)
    report['max_passing_tested_signals']=max([c['signals'] for c in report['cases'] if c['passed']],default=0);save()
    print('Completed:',folder/'results.json',flush=True)

if __name__=='__main__':main()
