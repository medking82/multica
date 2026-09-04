import json
from pathlib import Path
import tempfile
import unittest
import install_sources as installer

class Tests(unittest.TestCase):
    def test_committed_blobs_not_dirty_worktree_are_installed_and_tampering_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); repo=root/'repo'; repo.mkdir()
            installer.git(repo,'init','-b',installer.BRANCH)
            installer.git(repo,'config','user.name','fixture')
            installer.git(repo,'config','user.email','fixture@example.invalid')
            installer.git(repo,'remote','add','origin','https://github.com/medking82/multica.git')
            source=repo/'deploy/ga401-upgrade'; source.mkdir(parents=True)
            for name in installer.NAMES: (source/name).write_bytes(b'# committed\n')
            installer.git(repo,'add','--all')
            installer.git(repo,'-c','core.hooksPath=NUL','commit','-m','fixture')
            sha=installer.git(repo,'rev-parse','HEAD').decode().strip()
            (source/'cycle.py').write_bytes(b'# uncommitted\n')
            installed=installer.install(repo,sha,root/'install')
            self.assertEqual((installed/'cycle.py').read_bytes(),b'# committed\n')
            self.assertEqual(installer.install(repo,sha,root/'install'),installed)
            (installed/'cycle.py').write_bytes(b'# tampered\n')
            with self.assertRaisesRegex(RuntimeError,'integrity'): installer.install(repo,sha,root/'install')

    def test_foreign_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); (path/'manifest.json').write_text(json.dumps({'commit':'b'*40,'files':{}}))
            with self.assertRaisesRegex(RuntimeError,'identity'): installer.verify(path,'a'*40)

if __name__=='__main__': unittest.main()
