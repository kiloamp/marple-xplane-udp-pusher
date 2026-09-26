"""Replay with safe 1.05-second post-response pacing and adaptive batch sizes."""
import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from xplane_benchmark import replay_bins
from xplane_live import SIGNALS
from xplane_signals import signal_definitions
from xplane_marple import get_sdk_db
from xplane_verify import verify_capture


def main():
    bins,names=replay_bins(Path('outputs/xplane/20260922T023252-210798Z/flight-001.jsonl'),65)
    samples=[s for b in bins for s in b]
    order=[n for n,_,_ in SIGNALS]+sorted(n for n in names if n not in {x[0] for x in SIGNALS})
    folder=Path('outputs/xplane/benchmarks')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-paced');folder.mkdir(parents=True)
    report={'mode':'1.05 seconds after previous response; batch all samples received since last append','seconds_per_case':65,'cases':[]}
    def save():(folder/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    db=get_sdk_db();stream=db.get_stream('X-Plane Throughput Tests')
    print('Report:',folder/'results.json',flush=True)
    for count in [50,100,200,480]:
        selected=set(order[:count]);case={'signals':count,'batches':[]};report['cases'].append(case)
        dataset=stream.add_dataset(f'Replay_paced_{folder.name}_{count}',metadata={'Flight Type':'Recorded telemetry replay benchmark','signals_tested':count,'pacing':'1.05 seconds after response'})
        case['dataset_id']=dataset.id;save();dataset.upsert_signals(signal_definitions(selected,SIGNALS))
        capture=folder/f'{count}-signals.jsonl';cursor=0;total=0;base=time.monotonic();origin=time.time_ns();next_send=base+1;previous=None
        print('Testing safely paced',count,'signals',flush=True)
        try:
            with capture.open('x') as file:
                while cursor<len(samples):
                    time.sleep(max(0,next_send-time.monotonic()))
                    now=time.monotonic();end=min(65e9,(now-base)*1e9);batch=[];rows=[]
                    while cursor<len(samples) and samples[cursor][0]<end:
                        offset,values=samples[cursor];cursor+=1;v={k:x for k,x in values.items() if k in selected}
                        if v:
                            batch.append({'time':origin+offset,**v});rows.extend({'time':origin+offset,'signal':k,'value':x} for k,x in v.items())
                    if not rows:continue
                    started=time.monotonic();entry={'points':len(rows),'start_interval_s':started-previous if previous else None};previous=started
                    try:
                        dataset.append(pd.DataFrame(rows),shape='long')
                        for s in batch:file.write(json.dumps(s)+'\n')
                    except Exception as exc:
                        entry['error_type']=type(exc).__name__;resp=getattr(exc,'response',None)
                        if resp is not None:entry.update(http_status=resp.status_code,error_body=resp.text.replace(db.client.api_token,'[REDACTED]')[:1000])
                    entry['latency_s']=time.monotonic()-started;entry['latest_sample_age_s']=(time.time_ns()-batch[-1]['time'])/1e9
                    case['batches'].append(entry);total+=len(rows);save();next_send=time.monotonic()+1.05
                    if 'error_type' in entry:break
        finally:
            case['points']=total;case['elapsed_s']=time.monotonic()-base
            try:dataset=dataset.cool().wait_for_import(timeout=90);case['final_status']=dataset.import_status
            except Exception as exc:case['finalization_error']=type(exc).__name__
            lat=sorted(b['latency_s'] for b in case['batches']);intervals=[b['start_interval_s'] for b in case['batches'] if b['start_interval_s']]
            case['p95_latency_s']=lat[math.ceil(.95*len(lat))-1];case['mean_upload_interval_s']=sum(intervals)/len(intervals)
            case['append_passed']=cursor==len(samples) and not any('error_type' in b for b in case['batches']);save()
            if case.get('final_status')=='FINISHED':
                try:case['cold_verification']=verify_capture(dataset,capture,repair=False)
                except Exception as exc:
                    result_path=capture.with_suffix('.verification.json')
                    case['cold_verification']=json.loads(result_path.read_text()) if result_path.exists() else {'error':type(exc).__name__}
            save();print(json.dumps({k:v for k,v in case.items() if k not in ['batches','cold_verification']}),flush=True)
    print('Completed:',folder/'results.json',flush=True)

if __name__=='__main__':main()
