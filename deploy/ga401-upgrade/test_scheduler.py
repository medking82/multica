"""Exercise the configured task host with the real required hidden runner."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import json

class Tests(unittest.TestCase):
    def test_registered_host_can_run_the_canonical_hidden_python_child(self):
        installer=Path(__file__).with_name('install-scheduler.ps1').read_text(encoding='utf-8')
        match=re.search(r"\$pwsh = '([^']+)'", installer)
        self.assertIsNotNone(match)
        host=match[1]
        self.assertTrue(Path(host).is_file())
        def ps(value): return "'" + value.replace("'", "''") + "'"
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'hidden-child.txt'
            code='from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("passed")'
            command=("& 'C:\\Users\\Marck\\.codex\\bin\\invoke-hidden.ps1' -FilePath " + ps(sys.executable)
                     + " -ArgumentList @('-c'," + ps(code) + ') -WorkingDirectory ' + ps(directory))
            result=subprocess.run([host,'-NoProfile','-NonInteractive','-Command',command],
                                  capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(marker.read_text(),'passed')

    def test_windowless_entry_propagates_success_and_failure(self):
        installer=Path(__file__).with_name('install-scheduler.ps1').read_text(encoding='utf-8')
        source=re.search(r"\$launcherSource = @'\n(.*?)\n'@", installer, re.S).group(1)
        host=re.search(r"\$pwsh = '([^']+)'", installer).group(1)
        wscript=Path(__import__('os').environ['SystemRoot'])/'System32/wscript.exe'
        with tempfile.TemporaryDirectory(prefix='multica task ') as directory:
            root=Path(directory); launcher=root/'run-cycle-hidden.vbs'
            launcher.write_text(source, encoding='ascii')
            wrapper=root/'run-cycle.ps1'
            for code in (0, 19):
                with self.subTest(code=code):
                    wrapper.write_text(f'exit {code}\n', encoding='utf-8')
                    result=subprocess.run([str(wscript),'//B','//NoLogo',str(launcher),host,str(wrapper)],
                                          capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
                    self.assertEqual(result.returncode,code,result.stderr)

    def test_status_reads_registered_physical_root_and_retains_merge_hold(self):
        status_script=Path(__file__).with_name('scheduler-status.ps1')
        def ps(value): return "'" + str(value).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory(prefix='multica physical ') as directory:
            root=Path(directory); (root/'state').mkdir()
            hold={'status':'needs_attention','stage':'merge','error':'protected owner changed'}
            (root/'state/status.json').write_text(json.dumps(hold),encoding='utf-8')
            actions=[('pwsh.exe', f'-NoProfile -File "{root / "run-cycle.ps1"}"'),
                     ('wscript.exe', f'//B //NoLogo "{root / "run-cycle-hidden.vbs"}" "C:\\Program Files\\PowerShell\\7\\pwsh.exe" "{root / "run-cycle.ps1"}"')]
            for executable, arguments in actions:
                with self.subTest(executable=executable):
                    command=("function Get-ScheduledTask { [pscustomobject]@{ Description='Multica GA401 committed upstream updater v1'; State='Ready'; Settings=@{Enabled=$true}; Actions=@([pscustomobject]@{Execute="
                             + ps(executable) + ";Arguments=" + ps(arguments) + "}) } }; "
                             + "function Get-ScheduledTaskInfo { [pscustomobject]@{NextRunTime=[datetime]'2026-09-23';LastTaskResult=1} }; & "
                             + ps(status_script))
                    result=subprocess.run([r'C:\Program Files\PowerShell\7\pwsh.exe','-NoProfile','-NonInteractive','-Command',command],
                                          capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
                    self.assertEqual(result.returncode,0,result.stderr)
                    value=json.loads(result.stdout)
                    self.assertEqual(value['root'],str(root))
                    self.assertEqual(value['cycle'],hold)
            self.assertEqual(json.loads((root/'state/status.json').read_text()),hold)

    def test_installer_retains_existing_root_and_rejects_state_relocation(self):
        owner=Path(__file__).parent
        installer=(owner/'install-scheduler.ps1').read_text(encoding='utf-8')
        # Exercise the real admission prefix; stop before any installation mutation.
        prefix=installer.split('& $runner -FilePath $python', 1)[0]
        self.assertNotEqual(prefix,installer)
        def ps(value): return "'" + str(value).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory(prefix='multica root guard ') as directory:
            root=Path(directory); old=root/'existing'; new=root/'empty'
            (old/'state').mkdir(parents=True)
            hold={'status':'needs_attention','stage':'merge'}
            state=old/'state/status.json'; state.write_text(json.dumps(hold),encoding='utf-8')
            (root/'scheduler-contract.ps1').write_text((owner/'scheduler-contract.ps1').read_text(),encoding='utf-8')
            probe=root/'installer-admission.ps1'
            probe.write_text(prefix + "Write-Output $root\n",encoding='utf-8')
            stub=("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); function Get-ScheduledTask { [pscustomobject]@{Description='Multica GA401 committed upstream updater v1'; State='Ready'; Actions=@([pscustomobject]@{Execute='pwsh.exe';Arguments="
                  + ps(f'-NoProfile -File "{old / "run-cycle.ps1"}"') + "})} }; & " + ps(probe)
                  # The prefix is tested from this worktree, but its installed
                  # source admission intentionally accepts only the reserved owner.
                  + " -SourceDirectory " + ps(Path(r'C:\github\tools\upstream\multica-ga401')) + " -SourceCommit " + ps('a'*40))
            for supplied in (None, old, new):
                with self.subTest(supplied=supplied):
                    command=stub + (" -InstallationRoot " + ps(supplied) if supplied else '')
                    result=subprocess.run([r'C:\Program Files\PowerShell\7\pwsh.exe','-NoProfile','-NonInteractive','-Command',command],
                                          capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
                    if supplied == new:
                        self.assertNotEqual(result.returncode,0)
                        self.assertIn('differs from the registered task', result.stderr)
                    else:
                        self.assertEqual(result.returncode,0,result.stderr)
                        self.assertEqual(result.stdout.strip(),str(old))
                    self.assertFalse(new.exists())
                    self.assertEqual(json.loads(state.read_text()),hold)

if __name__=='__main__': unittest.main()
