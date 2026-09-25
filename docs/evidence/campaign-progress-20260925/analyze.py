"""Recompute the report metrics from the sanitized log projection; no network access."""
import gzip,json,pathlib,statistics
from collections import Counter,defaultdict
from datetime import datetime

def describe(rr):
    first=rr[0];last=rr[-1]
    start=datetime.fromisoformat(first['recorded_at_utc']);end=datetime.fromisoformat(last['recorded_at_utc']);seconds=(end-start).total_seconds()
    phases=defaultdict(list);calls=defaultdict(list);quantities=defaultdict(list)
    import re
    for r in rr:
        for p in r.get('phases') or []:
            if p.get('status')=='returned' and isinstance(p.get('seconds'),(int,float)): phases[p['stage']].append(p['seconds'])
        for k,v in (r.get('performance') or {}).get('calls',{}).items():
            if v.get('count'): calls[k].append(v['total_ns']/1e9)
        m=re.match(r'Transferred (\d+) (\S+)',str(r.get('outcome','')))
        if m: quantities[r['action']+':'+m[2]].append(int(m[1]))
    def stats(vals):return {'count':len(vals),'sum_seconds':sum(vals),'median_seconds':statistics.median(vals),'p95_seconds':sorted(vals)[int(.95*(len(vals)-1))]}
    f0=(first.get('after_state')or first['state'])['factory'];f1=(last.get('after_state')or last['state'])['factory']
    return {'records':len(rr),'first_utc':first['recorded_at_utc'],'last_utc':last['recorded_at_utc'],'elapsed_seconds':seconds,'actions':dict(Counter(r['action'] for r in rr)),'status':dict(Counter(r['status'] for r in rr)),'model_calls':dict(Counter(str(r.get('model_call')) for r in rr)),'phase_timings':{k:stats(v) for k,v in phases.items()},'inclusive_call_timings':{k:stats(v) for k,v in calls.items()},'observation_phase_seconds':sum(sum(phases[k]) for k in ['observe','pre_dispatch_observe','post_dispatch_observe']),'production_delta':{k:f1.get('produced',{}).get(k,0)-f0.get('produced',{}).get(k,0) for k in sorted(set(f0.get('produced',{}))|set(f1.get('produced',{})))},'transfer_batches':{k:{'count':len(v),'quantity_sum':sum(v),'median_quantity':statistics.median(v),'one_item_transfers':v.count(1)} for k,v in quantities.items()}}

if __name__ == "__main__":
    root=pathlib.Path(__file__).resolve().parent
    records=[json.loads(line) for line in gzip.decompress((root/"gameplay-projection.jsonl.gz").read_bytes()).splitlines()]
    expected=json.loads((root/"summary.json").read_text())
    accepted=[r for r in records if (r.get("code_revision") or {}).get("commit")==expected["accepted_commit"]]
    assert describe(records)==expected["all_captured"]
    assert describe(accepted)==expected["accepted_revision"]
    print(f"Verified {len(records)} records; {len(accepted)} on accepted revision")
