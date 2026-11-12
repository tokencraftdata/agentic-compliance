"""Closed-loop agent evaluation with source freeze, budget ledger and exact replay."""
import re
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import quote

from experiments.runner import parse_actions
from pilot.revision import extended_detect
from pilot.run import canonical, digest, subject_events
from .analysis import summarize
from .market import ScenarioMarket
from .protocol import load_frozen, source_hashes
from .provider import Budget, ProviderFailure, api, infer


def config_for(frozen, condition):
    plan=frozen['plan'];response=condition['response']
    return {**frozen['base_config'],'session_seconds':plan['session_seconds'],
        'decision_interval':plan['decision_interval'],'response_mode':response['mode'],
        'response_threshold':response.get('threshold',0.4),'response_slope':response.get('slope',3),
        **condition['control'],'spoof_window_seconds':180}


def make_market(frozen, condition):
    case=next(c for c in frozen['plan']['cases'] if c['id']==condition['case'])
    return ScenarioMarket(config_for(frozen,condition),condition['seed'],case,
                          condition['responsive'],condition['gate'])


def request_for(frozen, condition, market, history):
    request={'system':frozen['prompts'][condition['prompt']]+' '+frozen['action_contract'],
        'user':{'task':frozen['tasks'][condition['task']], 'observation':market.observation(),
                'position_limit':market.config['position_limit'],
                'initial_midpoint':history['arrival_mid'],
                'recent_decisions':history['decisions'][-3:]}}
    # Normalize tuple book levels to their actual JSON representation before
    # recording or sending, so replay checks the exact transmitted structure.
    return json.loads(canonical(request))


def mock_response(model, request, max_output_tokens):
    user=request['user'];obs=user['observation'];task=user['task']
    actions=[{'type':'hold'}]
    if model['id']=='mock_executor' and task['kind']=='buy_parent':
        remaining=task['quantity']-obs['inventory']
        if remaining>0:actions=[{'type':'market','side':'buy','qty':min(5,remaining)}]
    if model['id']=='mock_maker':
        actions=[{'type':'cancel','order_id':o['id']} for o in obs['orders'][:6]]
        actions += [{'type':'limit','side':side,'qty':5,'price':obs[key][0][0]}
                    for side,key in [('buy','bids'),('sell','asks')]]
    return {'model':model['id'],'stop_reason':'end_turn','mock':True,
            'content':[{'type':'text','text':canonical({'actions':actions})}],
            'usage':{'input_tokens':0,'output_tokens':0}}


def parsed_response(body, model, live):
    if body.get('model') != model['id'] or bool(body.get('mock',False)) == live:
        raise ValueError('wrong model or mock/live contamination')
    if body.get('stop_reason')!='end_turn':raise ValueError('incomplete model response')
    text=''.join(part['text'] for part in body.get('content',[]) if part.get('type')=='text').strip()
    # Accept one surrounding Markdown code fence (```json ... ```); nothing else is repaired.
    fenced=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n?```',text,re.S)
    return parse_actions(fenced.group(1) if fenced else text)


def preflight(models, max_output_tokens):
    metadata=[]
    for model in models:
        if model['pricing_checked_on'] != datetime.now(timezone.utc).date().isoformat():
            raise ValueError('recheck standard prices before live execution')
        found=api('/models/'+quote(model['id'],safe=''))
        if found.get('id')!=model['id'] or found.get('max_input_tokens')!=model['max_input_tokens']:
            raise ProviderFailure('model_or_context_limit_mismatch')
        if found.get('lifecycle')=='retired' or not isinstance(found.get('max_tokens'),int) or found['max_tokens']<max_output_tokens:
            raise ProviderFailure('model_not_available_for_plan')
        metadata.append(found)
    return metadata


def finish_record(m, condition, calls, status, arrival_mid, task):
    events=subject_events(m.events)
    alerts=extended_detect(events,m.config)
    attempted_sell=sum(e['kind']=='attempt' and e['action'].get('side')=='sell' for e in events)
    return {'condition':condition,'status':status,'calls':calls,'events':events,'alerts':alerts,
        'full_event_sha256':digest(m.events),'exogenous_tape_sha256':digest(m.tape),
        'market_metrics':m.metrics(),'task_metrics':m.task_metrics(task,arrival_mid),
        'attempted_sell_actions_in_buy_task':attempted_sell if task['kind']=='buy_parent' else None,
        'conduct_alert':any(a['detector']!='excessive_cancels' for a in alerts),
        'model_responses':sum('response' in c and not c['response'].get('mock',False) for c in calls),
        'provider_calls':sum(c.get('provider_called',False) for c in calls)}


def append_journal(stream, value):
    stream.write(canonical(value)+'\n');stream.flush();os.fsync(stream.fileno())


def run(protocol_path, out, max_calls, allow_live=False, budget_path=None, max_usd=None):
    frozen=load_frozen(protocol_path);plan=frozen['plan'];live=frozen['evidence_type']=='live_provider'
    if type(max_calls) is not int or max_calls<0:raise ValueError('invalid call limit')
    if live and (not allow_live or budget_path is None or max_usd is None):
        raise ValueError('live execution requires authorization, a persistent budget ledger and a USD cap')
    if out.exists():raise ValueError('output already exists; evidence is not overwritten')
    metadata=preflight(plan['models'],plan['max_output_tokens']) if live else []
    budget_context=Budget(budget_path,max_usd) if live else nullcontext(None)
    with budget_context as budget:
        out.mkdir(parents=True)
        (out/'protocol.json').write_text(json.dumps(frozen,indent=2)+'\n')
        (out/'provider_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
        (out/'execution.json').write_text(json.dumps({'max_calls':max_calls,
            'max_usd':str(max_usd) if live else None,'allow_live':allow_live,
            'sampling':'provider defaults; no provider random seed is assumed',
            'max_output_tokens':plan['max_output_tokens']},indent=2)+'\n')
        records=[];calls_used=0;stopped=None
        with (out/'journal.jsonl').open('x') as journal, (out/'traces.jsonl.gz').open('xb') as raw, gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as traces:
            for condition in frozen['conditions']:
                model=next(m for m in plan['models'] if m['id']==condition['model'])
                m=make_market(frozen,condition);arrival=(m.touch('buy')+m.touch('sell'))/2
                history={'arrival_mid':arrival,'decisions':[]};calls=[];status='complete'
                for step,t in enumerate(range(0,plan['session_seconds'],plan['decision_interval'])):
                    m.advance(t);request=request_for(frozen,condition,m,history)
                    call_id=frozen['protocol_sha256']+':'+condition['episode_id']+':'+str(step)
                    call={'decision_index':step,'time':t,'request':request,'provider_called':False}
                    actions=[{'type':'hold'}]
                    reservation=None
                    if stopped:
                        call['status']='not_attempted_after_'+stopped
                    elif calls_used>=max_calls:
                        call['status']='call_budget_exhausted'
                    elif live and (reservation:=budget.reserve(model,plan['max_output_tokens'],call_id)) is None:
                        call['status']='usd_budget_exhausted';stopped='usd_budget'
                    else:
                        append_journal(journal,{'event':'decision_started','call_id':call_id,'request':request,
                            'reserved_budget_entry':reservation})
                        calls_used+=1;start=time.monotonic();call['provider_called']=live
                        try:
                            body=(infer if live else mock_response)(model,request,plan['max_output_tokens'])
                            call['response']=body
                            if live:budget.settle(reservation,body)
                            parsed=parsed_response(body,model,live)
                            actions=parsed['actions'];call['status']='ok'
                            history['decisions'].append({'time':t,**parsed})
                        except ProviderFailure as error:
                            call['status']='provider_error';call['error_code']=error.code
                            stopped='provider_error'
                        except (ValueError,KeyError,TypeError) as error:
                            call['status']='invalid_output';call['error_type']=type(error).__name__
                        call['elapsed_seconds']=round(time.monotonic()-start,6)
                    if call['status']!='ok':status='incomplete'
                    call['executed_actions']=actions
                    append_journal(journal,{'event':'decision_finished','call_id':call_id,'record':call})
                    calls.append(call)
                    for action in actions:m.act(action)
                m.advance(plan['session_seconds'])
                r=finish_record(m,condition,calls,status,arrival,frozen['tasks'][condition['task']])
                records.append(r);traces.write((canonical(r)+'\n').encode());traces.flush()
                print('Recorded %d/%d episodes (%s)'%(len(records),len(frozen['conditions']),status),flush=True)
        summary=summarize(records,frozen)
        summary['decision_calls']=calls_used
        summary['budget_committed_usd']=str(budget.committed()) if live else '0'
        summary['approved_usd_cap']=str(budget.cap) if live else None
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        if live:(out/'budget_snapshot.json').write_text(json.dumps(budget.state,indent=2)+'\n')
        manifest={'evidence_type':frozen['evidence_type'],'source_sha256':source_hashes(),
                  'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}}
        (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return summary


def verify(out):
    frozen=load_frozen(out/'protocol.json');manifest=json.loads((out/'manifest.json').read_text())
    if manifest['source_sha256']!=source_hashes():raise ValueError('source mismatch')
    for name,expected in manifest['files'].items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=expected:raise ValueError('evidence hash mismatch: '+name)
    rows=[];live=frozen['evidence_type']=='live_provider';decision_calls=0
    with gzip.open(out/'traces.jsonl.gz','rt') as stream:
        for index,line in enumerate(stream):
            saved=json.loads(line);condition=saved['condition']
            if condition!=frozen['conditions'][index]:raise ValueError('condition order mismatch')
            model=next(m for m in frozen['plan']['models'] if m['id']==condition['model'])
            m=make_market(frozen,condition);arrival=(m.touch('buy')+m.touch('sell'))/2
            history={'arrival_mid':arrival,'decisions':[]};times=list(range(0,m.config['session_seconds'],m.config['decision_interval']))
            if len(saved['calls'])!=len(times):raise ValueError('decision coverage mismatch')
            for step,(t,call) in enumerate(zip(times,saved['calls'])):
                m.advance(t)
                if call['time']!=t or call['decision_index']!=step or call['request']!=request_for(frozen,condition,m,history):
                    raise ValueError('observation or request mismatch')
                actions=[{'type':'hold'}]
                if call['status']=='ok':
                    parsed=parsed_response(call['response'],model,live);actions=parsed['actions']
                    history['decisions'].append({'time':t,**parsed})
                if call['executed_actions']!=actions:raise ValueError('recorded action disagrees with model output')
                decision_calls+=('elapsed_seconds' in call)
                for action in actions:m.act(action)
            m.advance(m.config['session_seconds'])
            status='complete' if all(c['status']=='ok' for c in saved['calls']) else 'incomplete'
            if finish_record(m,condition,saved['calls'],status,arrival,frozen['tasks'][condition['task']])!=saved:
                raise ValueError('replay record mismatch')
            rows.append(saved)
    if len(rows)!=len(frozen['conditions']):raise ValueError('missing planned episodes')
    summary=json.loads((out/'summary.json').read_text());computed=summarize(rows,frozen)
    if any(summary[k]!=v for k,v in computed.items()) or summary['decision_calls']!=decision_calls:
        raise ValueError('summary mismatch')
    return {'episodes_replayed':len(rows),'mismatches':0,'requests_actions_metrics_verified':True,
            'evidence_type':frozen['evidence_type'],'llm_episodes':computed['llm_episodes']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    run_args=sub.add_parser('run');run_args.add_argument('protocol',type=Path);run_args.add_argument('output',type=Path)
    run_args.add_argument('--max-calls',type=int,required=True);run_args.add_argument('--allow-live',action='store_true')
    run_args.add_argument('--budget-ledger',type=Path);run_args.add_argument('--max-usd')
    verify_args=sub.add_parser('verify');verify_args.add_argument('output',type=Path)
    sub.add_parser('models')
    args=parser.parse_args()
    if args.command=='models':
        try:print(json.dumps(api('/models?limit=100'),indent=2))
        except ProviderFailure as error:print(json.dumps({'error':error.code}));raise SystemExit(1)
    elif args.command=='run':
        result=run(args.protocol,args.output,args.max_calls,args.allow_live,args.budget_ledger,args.max_usd)
        print(json.dumps({k:v for k,v in result.items() if k not in ('cells','paired_gate_differences','paired_response_differences')},indent=2))
    else:print(json.dumps(verify(args.output),indent=2))
