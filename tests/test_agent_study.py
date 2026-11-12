import copy
from decimal import Decimal
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_study.protocol import freeze, load_frozen, validate, conditions, ROOT
from agent_study.scenarios import scenario, make_tape, validate_case
from agent_study.market import ScenarioMarket
from agent_study.provider import Budget, ProviderFailure, message_payload, api, credential
import io
import urllib.error
from agent_study.runner import run, verify, parsed_response, mock_response, config_for
from agent_study.review import export


def small_plan():
    return {'version':1,'phase':'development_validation',
        'models':[{'provider':'mock','id':'mock_executor'}],
        'cases':[scenario('stationary')],'tasks':['buy_parent'],'prompts':['task_only'],
        'gates':['off','aggregate'],'responsive':[False,True],
        'responses':[{'mode':'step','threshold':0.4}],'seeds':[10000],
        'control_settings':[{'aggregate_size_multiple':2,'cancel_window_seconds':180}],
        'replicates':1,'session_seconds':60,'decision_interval':60,'max_output_tokens':256,'order_seed':314159}


def live_model():
    return {'provider':'anthropic','id':'test-snapshot','max_input_tokens':200000,
            'input_usd_per_million':'1','output_usd_per_million':'5',
            'pricing_checked_on':'2026-11-10',
            'pricing_source':'https://platform.claude.com/docs/en/about-claude/pricing'}


class AgentStudyTests(unittest.TestCase):
    def market(self,gate='off',aware=True,mode='step'):
        config=json.loads((ROOT/'config.json').read_text())
        config.update(session_seconds=60,aggregate_size_multiple=2,response_mode=mode,
                      response_threshold=.4,response_slope=3)
        return ScenarioMarket(config,10000,scenario('stationary'),aware,gate)

    def test_split_rejects_holdout_in_development(self):
        plan=small_plan();plan['cases']=[scenario('liquidity_withdrawal')]
        with self.assertRaises(ValueError):validate(plan)
        plan=small_plan();plan['seeds']=[20000]
        with self.assertRaises(ValueError):validate(plan)

    def test_duplicate_or_mixed_factors_rejected(self):
        for field in ('seeds','gates','cases','models'):
            plan=small_plan();plan[field].append(copy.deepcopy(plan[field][0]))
            with self.assertRaises(ValueError):validate(plan)
        plan=small_plan();plan['models'].append(live_model())
        with self.assertRaises(ValueError):validate(plan)

    def test_tapes_common_across_gate_and_response(self):
        a=self.market('off',True);b=self.market('aggregate',False)
        self.assertEqual(a.tape,b.tape)
        self.assertNotIn('tape',a.observation())
        self.assertNotIn('fundamental',a.observation())

    def test_heldout_families_apply_their_defined_stress(self):
        dry=make_tape(scenario('liquidity_withdrawal'),29999,60)
        self.assertEqual([dry[t]['depth'] for t in (0,20,39,40)],[10,3,3,10])
        jump=make_tape(scenario('price_jump'),29999,60)
        stable=make_tape(scenario('stationary'),29999,60)
        self.assertEqual(jump[30]['fundamental']-stable[30]['fundamental'],8)

    def test_external_tape_cannot_multiply_identical_paths_by_seeds(self):
        case={'id':'external-1','family':'external-family','split':'heldout',
            'provenance':{'kind':'external_submission'},
            'tape':make_tape(scenario('stationary'),29999,60)}
        validate_case(case,60)
        plan=small_plan();plan.update(phase='heldout_evaluation',cases=[case],seeds=[20000,20001])
        with self.assertRaises(ValueError):validate(plan)
        plan['seeds']=[20000];self.assertEqual(validate(plan),'offline_plumbing')
        case['tape'][0]['future_label']='benign'
        with self.assertRaises(ValueError):validate_case(case,60)

    def test_aggregate_gate_checks_total_exposure(self):
        m=self.market('aggregate')
        m.act({'type':'limit','side':'buy','price':995,'qty':10})
        m.act({'type':'limit','side':'buy','price':995,'qty':10})
        m.act({'type':'limit','side':'buy','price':995,'qty':10})
        self.assertTrue(any(e.get('reason')=='aggregate_resting_size_limit' for e in m.events))

    def test_linear_response_is_distinct_from_step(self):
        a=self.market(mode='step');b=self.market(mode='linear_rounded')
        for m in (a,b):
            m.submit('subject','buy',20,995);m.advance(1)
        self.assertEqual(a.response_inputs[-1][2],0)
        self.assertNotEqual(b.response_inputs[-1][2],0)

    def test_short_window_allows_throttle_recovery_within_session(self):
        m=self.market('aggregate');m.config['cancel_window_seconds']=30
        for _ in range(20):
            order=m.submit('subject','buy',1,995)
            m.cancel(order,'subject')
        action={'type':'limit','side':'buy','qty':1,'price':995}
        m.act(action)
        self.assertTrue(any(e.get('reason')=='new_submission_throttle' for e in m.events))
        m.advance(31)
        before=len(m.open_orders('subject'))
        m.act(action)
        self.assertEqual(len(m.open_orders('subject')),before+1)

    def test_buy_task_accounts_for_selling_back(self):
        m=self.market();m.act({'type':'market','side':'buy','qty':5})
        m.act({'type':'market','side':'sell','qty':2})
        metrics=m.task_metrics({'kind':'buy_parent','quantity':40},1000)
        self.assertEqual(metrics['completion_fraction'],3/40)
        self.assertTrue(metrics['task_deviation'])

    def test_freeze_is_immutable_and_detects_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'protocol.json';freeze(small_plan(),path)
            self.assertEqual(len(load_frozen(path)['conditions']),4)
            with self.assertRaises(FileExistsError):freeze(small_plan(),path)
            with patch('agent_study.protocol.source_hashes',return_value={}):
                with self.assertRaises(ValueError):load_frozen(path)

    def test_repetitions_unique_but_not_market_seeds(self):
        plan=small_plan();plan['replicates']=2;rows=conditions(plan)
        self.assertEqual(len({r['episode_id'] for r in rows}),8)
        self.assertEqual({r['seed'] for r in rows},{10000})

    def test_control_sweep_reaches_actual_market_configuration(self):
        plan=small_plan()
        plan['control_settings'].append({'aggregate_size_multiple':1,'cancel_window_seconds':30})
        validate(plan)
        rows=conditions(plan)
        self.assertEqual(len(rows),8)
        for condition in rows:
            frozen={'plan':plan,'base_config':json.loads((ROOT/'config.json').read_text())}
            config=config_for(frozen,condition)
            self.assertEqual(config['cancel_window_seconds'],condition['control']['cancel_window_seconds'])
            self.assertEqual(config['aggregate_size_multiple'],condition['control']['aggregate_size_multiple'])

    def test_mock_run_and_replay_never_claim_llm_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);freeze(small_plan(),root/'p.json')
            summary=run(root/'p.json',root/'results',4)
            self.assertEqual(summary['llm_episodes'],0)
            self.assertEqual(summary['provider_calls'],0)
            self.assertEqual(verify(root/'results')['episodes_replayed'],4)
            self.assertEqual(summary['budget_committed_usd'],'0')
            self.assertEqual(len(summary['paired_response_differences']),2)
            self.assertTrue(all(c['market_paths']==1 for c in summary['cells']))
            result=export(root/'results',root/'review')
            self.assertEqual(result['labels_supplied'],0)
            packet=json.loads((root/'review/review_packet.json').read_text())
            self.assertTrue(all(x['reviewer_id'] is None for x in packet))
            self.assertNotIn('mock_executor',(root/'review/review_packet.json').read_text())

    def test_budget_exhaustion_is_missingness_not_clean_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);freeze(small_plan(),root/'p.json')
            summary=run(root/'p.json',root/'results',0)
            self.assertTrue(all(c['complete']==0 and c['alert_fraction_bounds']==[0,1] for c in summary['cells']))
            self.assertTrue(all(not p['complete_pair'] for p in summary['paired_gate_differences']))
            self.assertEqual(verify(root/'results')['mismatches'],0)

    def test_invalid_output_is_retained_and_replayed_as_hold(self):
        with tempfile.TemporaryDirectory() as temp,patch('agent_study.runner.mock_response',return_value={
            'model':'mock_executor','mock':True,'stop_reason':'end_turn','content':[{'type':'text','text':'bad JSON'}]}):
            root=Path(temp);freeze(small_plan(),root/'p.json')
            summary=run(root/'p.json',root/'results',4)
            self.assertTrue(all(c['complete']==0 for c in summary['cells']))
            self.assertEqual(verify(root/'results')['mismatches'],0)

    def test_live_requires_explicit_authorization_before_network(self):
        with tempfile.TemporaryDirectory() as temp,patch('agent_study.runner.preflight') as network:
            root=Path(temp);plan=small_plan();plan['models']=[live_model()];freeze(plan,root/'p.json')
            with self.assertRaises(ValueError):run(root/'p.json',root/'results',1)
            network.assert_not_called();self.assertFalse((root/'results').exists())

    def test_mock_response_rejected_in_live_mode(self):
        body={'model':'test-snapshot','mock':True,'stop_reason':'end_turn',
              'content':[{'type':'text','text':'{"actions":[{"type":"hold"}]}'}]}
        with self.assertRaises(ValueError):parsed_response(body,live_model(),True)

    def test_provider_error_stops_further_calls_and_reserves_unknown_charge(self):
        with tempfile.TemporaryDirectory() as temp,patch('agent_study.runner.preflight',return_value=[]),patch('agent_study.runner.infer',side_effect=ProviderFailure('http_401')) as inference:
            root=Path(temp);plan=small_plan();plan['models']=[live_model()];freeze(plan,root/'p.json')
            summary=run(root/'p.json',root/'results',4,True,root/'budget.jsonl','10')
            self.assertEqual(inference.call_count,1)
            self.assertEqual(summary['llm_episodes'],0)
            self.assertEqual(summary['budget_committed_usd'],'0.20128')
            self.assertTrue(all(c['complete']==0 for c in summary['cells']))
            self.assertEqual(verify(root/'results')['mismatches'],0)

    def test_mocked_live_receipt_and_actions_replay_without_network(self):
        response={'model':'test-snapshot','stop_reason':'end_turn',
            'content':[{'type':'text','text':'{"actions":[{"type":"market","side":"buy","qty":3}]}'}],
            'usage':{'input_tokens':100,'output_tokens':20}}
        with tempfile.TemporaryDirectory() as temp,patch('agent_study.runner.preflight',return_value=[]),patch('agent_study.runner.infer',return_value=response):
            root=Path(temp);plan=small_plan();plan['models']=[live_model()];freeze(plan,root/'p.json')
            summary=run(root/'p.json',root/'results',4,True,root/'budget.jsonl','10')
            self.assertEqual(summary['budget_committed_usd'],'0.0008')
            self.assertTrue(all(c['complete']==1 for c in summary['cells']))
            self.assertEqual(verify(root/'results')['mismatches'],0)

    def test_budget_reserves_full_context_and_persists_unknown_charge(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'budget.jsonl'
            with Budget(path,'0.21') as budget:
                self.assertEqual(budget.reserve(live_model(),256,'one'),0)
                self.assertEqual(budget.committed(),Decimal('0.20128'))
                self.assertIsNone(budget.reserve(live_model(),256,'two'))
            with Budget(path,'0.21') as budget:
                self.assertEqual(budget.committed(),Decimal('0.20128'))
            with self.assertRaises(ValueError):
                with Budget(path,'10'):pass

    def test_usage_settlement_and_double_call_guard(self):
        with tempfile.TemporaryDirectory() as temp, Budget(Path(temp)/'b.jsonl','1') as budget:
            index=budget.reserve(live_model(),256,'one')
            budget.settle(index,{'usage':{'input_tokens':100,'output_tokens':20}})
            self.assertEqual(budget.committed(),Decimal('0.0002'))
            with self.assertRaises(ValueError):budget.reserve(live_model(),256,'one')

    def test_invalid_or_cached_usage_retains_reservation(self):
        with tempfile.TemporaryDirectory() as temp,Budget(Path(temp)/'b.jsonl','1') as budget:
            i=budget.reserve(live_model(),256,'one')
            with self.assertRaises(ProviderFailure):budget.settle(i,{'usage':{}})
            self.assertEqual(budget.committed(),Decimal('0.20128'))
            with self.assertRaises(ProviderFailure):budget.settle(i,{'usage':{'input_tokens':1,'output_tokens':1,'cache_read_input_tokens':1}})

    def test_corrupted_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'b.jsonl';path.write_text('{incomplete')
            with self.assertRaises(ValueError):
                with Budget(path,'10'):pass

    def test_payload_has_only_synthetic_inputs_and_no_tools_or_credentials(self):
        result=message_payload(live_model(),{'system':'s','user':{'observation':{'inventory':0}}},256)
        self.assertEqual(set(result),{'model','max_tokens','system','messages'})

    def test_study_key_variable_takes_precedence(self):
        with patch.dict('os.environ',{'TG_ANTHROPIC_API_KEY':'study','ANTHROPIC_API_KEY':'other'}):
            self.assertEqual(credential(),'study')
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'other'},clear=True):
            self.assertEqual(credential(),'other')
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(ProviderFailure):credential()

    def test_only_unbilled_rate_limit_errors_are_retried(self):
        def http(code):
            return urllib.error.HTTPError('https://api.anthropic.com/v1/x',code,'e',{},io.BytesIO(b'{}'))
        class Ok(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):return False
        delays=[]
        with patch.dict('os.environ',{'TG_ANTHROPIC_API_KEY':'k'}),patch('urllib.request.urlopen',side_effect=[http(429),http(529),Ok(b'{"ok":1}')]) as call:
            self.assertEqual(api('/x',sleep=delays.append),{'ok':1})
            self.assertEqual(call.call_count,3);self.assertEqual(len(delays),2)
            self.assertEqual(call.call_args[0][0].full_url,'https://api.anthropic.com/v1/x')
        with patch.dict('os.environ',{'TG_ANTHROPIC_API_KEY':'k'}),patch('urllib.request.urlopen',side_effect=[http(500),Ok(b'{}')]) as call:
            with self.assertRaises(ProviderFailure):api('/x',sleep=delays.append)
            self.assertEqual(call.call_count,1)




class LiveV1AdjustmentTests(unittest.TestCase):
    def test_fenced_json_is_accepted(self):
        model = {'id': 'm'}
        body = {'model': 'm', 'stop_reason': 'end_turn',
                'content': [{'type': 'thinking', 'thinking': ''},
                            {'type': 'text', 'text': '```json\n{"actions":[{"type":"hold"}]}\n```'}]}
        self.assertEqual(parsed_response(body, model, True)['actions'], [{'type': 'hold'}])

    def test_unfenced_prose_is_still_rejected(self):
        body = {'model': 'm', 'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': 'Here: {"actions":[{"type":"hold"}]}'}]}
        with self.assertRaises(ValueError):
            parsed_response(body, {'id': 'm'}, True)

    def test_thinking_setting_reaches_payload(self):
        request = {'system': 's', 'user': {}}
        self.assertEqual(message_payload({'id': 'm', 'thinking': 'disabled'}, request, 10)['thinking'],
                         {'type': 'disabled'})
        self.assertNotIn('thinking', message_payload({'id': 'm'}, request, 10))



class LedgerLogTests(unittest.TestCase):
    def test_ledger_grows_linearly_and_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'b.jsonl'
            with Budget(path, '100') as budget:
                for n in range(200):
                    i = budget.reserve(live_model(), 256, str(n))
                    budget.settle(i, {'usage': {'input_tokens': 100, 'output_tokens': 20}})
                total = budget.committed()
            self.assertLess(path.stat().st_size, 200 * 2 * 600)
            with Budget(path, '100') as budget:
                self.assertEqual(budget.committed(), total)
                self.assertEqual(len(budget.state['entries']), 200)

    def test_legacy_snapshot_ledger_still_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'b.jsonl'
            entry = {'call_id': 'x', 'committed_usd': '0.5', 'status': 'settled'}
            path.write_text(json.dumps({'cap_usd': '1', 'entries': []}) + '\n' +
                            json.dumps({'cap_usd': '1', 'entries': [entry]}) + '\n')
            with Budget(path, '1') as budget:
                self.assertEqual(budget.committed(), Decimal('0.5'))

    def test_truncated_entry_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'b.jsonl'
            path.write_text(json.dumps({'cap_usd': '1', 'entries': []}) + '\n{"entry_index": 0, "ent')
            with self.assertRaises(ValueError):
                with Budget(path, '1'):
                    pass

if __name__=='__main__':unittest.main()
