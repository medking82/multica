"""Exercise the configured task host with the real required hidden runner."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

class Tests(unittest.TestCase):
    def test_registered_host_can_run_the_canonical_hidden_python_child(self):
        installer=Path(__file__).with_name('install-scheduler.ps1').read_text(encoding='utf-8')
        match=re.search(r"New-ScheduledTaskAction -Execute '([^']+)'", installer)
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

if __name__=='__main__': unittest.main()
