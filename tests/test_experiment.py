import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.runner import parse_actions,validate_plan,run,verify,call_provider
from experiments.anthropic_adapter import payload

ROOT=Path(__file__).resolve().parents[1]
SPEC=json.loads((ROOT/'experiments/offline_smoke.json').read_text())


class ExperimentTests(unittest.TestCase):
    def small(self):
        s=copy.deepcopy(SPEC);s.update(session_seconds=60,seeds=[9000],prompts=['P0'],book_aware=[True],gates=['off'])
        s['models']=s['models'][:1];return s

    def test_bad_actions_rejected(self):
        for obj in [[],{'actions':[]},{'actions':[{'type':'market','side':'buy','qty':True}]},
                    {'actions':[{'type':'hold','extra':1}]},{'actions':[{'type':'hold'}]*9},
                    {'actions':[{'type':'cancel','order_id':0}]}]:
            with self.assertRaises(ValueError):parse_actions(json.dumps(obj))

    def test_budget_failure_is_unresolved_not_benign(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run';s=run(self.small(),out,max_calls=0)
            self.assertEqual(s['provider_calls'],0);self.assertEqual(s['cells'][0]['flag_rate_bounds'],[0,1])
            self.assertEqual(s['cells'][0]['complete'],0);self.assertEqual(verify(out)['mismatches'],0)

    def test_provider_failure_retains_episode(self):
        with tempfile.TemporaryDirectory() as d,patch('experiments.runner.call_provider',side_effect=OSError('private error')):
            out=Path(d)/'run';s=run(self.small(),out,max_calls=1)
            self.assertEqual(s['recorded_episodes'],1);self.assertEqual(s['cells'][0]['complete'],0)
            self.assertEqual(s['cells'][0]['flag_rate_bounds'],[0,1]);self.assertEqual(verify(out)['mismatches'],0)

    def test_offline_success_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run';s=run(self.small(),out,max_calls=1)
            self.assertEqual(s['llm_episodes'],0);self.assertEqual(s['cells'][0]['complete'],1)
            self.assertEqual(verify(out)['episodes_replayed'],1)
            with self.assertRaises(ValueError):run(self.small(),out,max_calls=1)

    def test_live_needs_explicit_switch_before_output_creation(self):
        spec=self.small();spec['models']=[{'model_id':'test-snapshot','adapter':'command','command':['unused']}]
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run'
            with self.assertRaises(ValueError):run(spec,out,max_calls=1)
            self.assertFalse(out.exists())

    def test_live_budget_zero_does_not_claim_model_inference(self):
        spec=self.small();spec['models']=[{'model_id':'test-snapshot','adapter':'command','command':['unused']}]
        with tempfile.TemporaryDirectory() as d:
            s=run(spec,Path(d)/'run',max_calls=0,allow_live=True)
            self.assertEqual(s['llm_episodes'],0)
            self.assertEqual(s['cells'][0]['flag_rate_bounds'],[0,1])

    def test_incomplete_and_mixed_plans_rejected(self):
        with self.assertRaises(ValueError):validate_plan(json.loads((ROOT/'experiments/full_study.template.json').read_text()))
        s=self.small();s['models'].append({'model_id':'real','adapter':'command','command':['unused']})
        with self.assertRaises(ValueError):validate_plan(s)

    def test_provider_model_mismatch_rejected(self):
        model={'model_id':'pinned','adapter':'command','command':['unused']}
        class Result:returncode=0;stdout='{"model":"different","output_text":"{}"}'
        with patch('experiments.runner.subprocess.run',return_value=Result()):
            with self.assertRaises(ValueError):call_provider(model,{})

    def test_adapter_payload_uses_exact_model_and_no_secret_field(self):
        p=payload({'model':'pinned','system':'policy','max_output_tokens':100,'sampling':{'temperature':0},'observation':{'inventory':0}})
        self.assertEqual(p['model'],'pinned');self.assertEqual(p['max_tokens'],100)
        self.assertNotIn('api_key',p)


if __name__=='__main__':unittest.main()
