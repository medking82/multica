import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import upgrade

NEW = 'f42a0a4678f3aa8ecba981f542d3ef3b66996249'
UPSTREAM = 'b13b0e5678f3aa8ecba981f542d3ef3b66996249'
OLD = 'daad4487937f5cf493f8705c23659d5c48a3055d'
IMAGES = {k: 'sha256:' + ('a' * 64) for k in ('backend','frontend','runtime')}

class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)/'upgrades'; self.root=self.base/NEW
        source=self.root/'source/deploy/ga401-upgrade'; source.mkdir(parents=True)
        (source/'transition.json').write_text(json.dumps({'prior_commit':OLD,'prior_ledger':'450_ok','prior_images':IMAGES,'target_version':'0.4.39','upstream_commit':UPSTREAM}))
        prior=self.base/OLD; prior.mkdir()
        (prior/'state.json').write_text(json.dumps({'status':'complete','completed':list(upgrade.PHASES),'commit':OLD,'images':IMAGES,'rehearsal':{'ledger':'450_ok'}}))
        (self.root/'state.json').write_text(json.dumps({'status':'ready','completed':[],'commit':NEW}))
        self.oldbase=upgrade.BASE; upgrade.BASE=self.base
    def tearDown(self): upgrade.BASE=self.oldbase; self.tmp.cleanup()
    def test_valid_transition_binds_previous_receipt_and_target_version(self):
        u=upgrade.Upgrade(self.root,NEW); self.assertEqual(u.old_commit,OLD); self.assertTrue(u.version.startswith('0.4.39-ga401.'))
    def test_non_string_commit_binding_fails_closed(self):
        p=self.root/'source/deploy/ga401-upgrade/transition.json'; data=json.loads(p.read_text()); data['upstream_commit']=None; p.write_text(json.dumps(data))
        with self.assertRaises(upgrade.Failure): upgrade.Upgrade(self.root,NEW)

    def test_missing_or_malformed_transition_refused(self):
        (self.root/'source/deploy/ga401-upgrade/transition.json').unlink()
        with self.assertRaises(upgrade.Failure): upgrade.Upgrade(self.root,NEW)
    def test_bad_version_and_same_commit_refused(self):
        p=self.root/'source/deploy/ga401-upgrade/transition.json'; data=json.loads(p.read_text()); data['target_version']='latest'; p.write_text(json.dumps(data))
        with self.assertRaisesRegex(upgrade.Failure,'version'): upgrade.Upgrade(self.root,NEW)
        data['target_version']='0.4.39'; data['upstream_commit']=OLD; p.write_text(json.dumps(data))
        with self.assertRaises(upgrade.Failure): upgrade.Upgrade(self.root,OLD)
    def test_snapshot_is_read_only_and_binds_completed_deployment(self):
        contract={'mounts':{},'ports':{},'user':'','network':'fixture'}
        observed={'Image':IMAGES['backend'],'State':{'Running':True},'Mounts':[], 'HostConfig':{'PortBindings':{},'NetworkMode':'fixture'},'Config':{'User':''}}
        state=json.loads((self.root/'state.json').read_text()); state.update({'status':'complete','completed':list(upgrade.PHASES),'images':IMAGES,'rehearsal':{'ledger':'451_ok'},'config_hashes':['x'],'containers':{k:{'contract':contract} for k in IMAGES}}); (self.root/'state.json').write_text(json.dumps(state))
        before=(self.root/'state.json').read_bytes()
        with patch.object(upgrade, 'health') as check, patch.object(upgrade,'ledger',return_value='451_ok'), patch.object(upgrade,'config_hashes',return_value=['x']), patch.object(upgrade,'inspect',return_value=observed):
            result=upgrade.deployed_snapshot(NEW,self.root)
        self.assertEqual(before,(self.root/'state.json').read_bytes())
        check.assert_called_once_with(NEW)
        self.assertEqual(result['source_commit'],NEW); self.assertEqual(result['ledger'],'451_ok')
        with patch.object(upgrade, 'health'), patch.object(upgrade,'ledger',return_value='452_drift'):
            with self.assertRaisesRegex(upgrade.Failure,'ledger drift'): upgrade.deployed_snapshot(NEW,self.root)

    def test_incomplete_or_image_drift_refused(self):
        p=self.base/OLD/'state.json'; data=json.loads(p.read_text()); data['completed']=[]; p.write_text(json.dumps(data))
        with self.assertRaisesRegex(upgrade.Failure,'complete'): upgrade.Upgrade(self.root,NEW)
        data['completed']=list(upgrade.PHASES); data['images']['backend']='sha256:'+'b'*64; p.write_text(json.dumps(data))
        with self.assertRaisesRegex(upgrade.Failure,'image'): upgrade.Upgrade(self.root,NEW)

if __name__=='__main__': unittest.main()
