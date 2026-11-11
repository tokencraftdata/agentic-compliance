"""Attempted behavior, executed outcomes and missingness remain separate."""
from collections import Counter, defaultdict
import statistics
from pilot.run import canonical


def summarize(records, frozen):
    groups=defaultdict(list)
    for r in records:
        c=r['condition']
        key=canonical({k:v for k,v in c.items() if k not in ('seed','replicate','episode_id')})
        groups[key].append(r)
    cells=[]
    for key, rows in sorted(groups.items()):
        import json
        cell=json.loads(key)
        complete=[r for r in rows if r['status']=='complete']
        flagged=sum(r['conduct_alert'] for r in rows)
        unresolved=sum(not r['conduct_alert'] and r['status']!='complete' for r in rows)
        cell.update(planned=len(rows),complete=len(complete),observed_flagged=flagged,
            market_paths=len({r['exogenous_tape_sha256'] for r in rows}),
            missingness=dict(Counter(r['status'] for r in rows if r['status']!='complete')),
            alert_fraction_bounds=[flagged/len(rows),(flagged+unresolved)/len(rows)])
        for field in ('filled_lots','rejected_actions','wealth_fundamental'):
            cell['complete_mean_'+field]=statistics.mean(r['market_metrics'][field] for r in complete) if complete else None
        cell['complete_task_deviations']=sum(r['task_metrics']['task_deviation'] for r in complete)
        cells.append(cell)
    # Pair conditions on market seed and repetition index. New provider calls in
    # each arm are not matched model randomness or fixed-action counterfactuals.
    pairs=defaultdict(dict)
    for r in records:
        c=r['condition']
        if c['gate'] in ('off','aggregate'):
            pairs[canonical({k:v for k,v in c.items() if k not in ('gate','episode_id')})][c['gate']]=r
    effects=[]
    for key, by_gate in sorted(pairs.items()):
        import json
        if set(by_gate)!= {'off','aggregate'}:continue
        a,b=by_gate['off'],by_gate['aggregate']
        item=json.loads(key);item['complete_pair']=a['status']==b['status']=='complete'
        if item['complete_pair']:
            item.update(alert_difference=int(b['conduct_alert'])-int(a['conduct_alert']),
                volume_difference=b['market_metrics']['filled_lots']-a['market_metrics']['filled_lots'],
                wealth_difference=round(b['market_metrics']['wealth_fundamental']-a['market_metrics']['wealth_fundamental'],6),
                task_deviation_difference=int(b['task_metrics']['task_deviation'])-int(a['task_metrics']['task_deviation']))
            endpoint='completion_fraction' if item['task']=='buy_parent' else 'two_sided_filled_lots'
            item['task_endpoint']=endpoint
            item['task_endpoint_difference']=b['task_metrics'][endpoint]-a['task_metrics'][endpoint]
        effects.append(item)
    response_pairs=defaultdict(dict)
    for r in records:
        c=r['condition']
        response_pairs[canonical({k:v for k,v in c.items() if k not in ('responsive','episode_id')})][c['responsive']]=r
    response_effects=[]
    for key,arms in sorted(response_pairs.items()):
        import json
        if set(arms)!={True,False}:continue
        item=json.loads(key);a,b=arms[False],arms[True]
        item['complete_pair']=a['status']==b['status']=='complete'
        if item['complete_pair']:
            item['response_toggle_wealth_difference']=round(b['market_metrics']['wealth_fundamental']-a['market_metrics']['wealth_fundamental'],6)
        response_effects.append(item)
    return {'evidence_type':frozen['evidence_type'],'phase':frozen['plan']['phase'],
        'planned_episodes':len(frozen['conditions']),'recorded_episodes':len(records),
        'llm_episodes':sum(r['model_responses']>0 for r in records) if frozen['evidence_type']=='live_provider' else 0,
        'provider_calls':sum(r['provider_calls'] for r in records),'cells':cells,'paired_gate_differences':effects,
        'paired_response_differences':response_effects,
        'inference':'Descriptive pilot output. No population accuracy, legal intent, independent validation, or significance claim. Repeat IDs do not seed provider randomness; missing episodes are not benign.'}
