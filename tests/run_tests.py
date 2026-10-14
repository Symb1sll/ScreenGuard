"""Run suites in separate processes; all output stays under artifacts/test_runs."""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent

def main():
    run=ROOT/'artifacts/test_runs'/uuid4().hex[:12]
    run.mkdir(parents=True)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',TEMP=str(run),TMP=str(run),TMPDIR=str(run))
    groups={
        'layout':[HERE/'test_layout.py'],
        'core':[HERE/'test_workbench.py'],
        'runtime':[HERE/'test_runtime.py'],
        'api_and_recognition':[HERE/'test_model_interfaces.py',HERE/'recognition'],
        'enterprise':[HERE/name for name in ('test_enterprise.py','test_benchmark.py','test_supervisor.py')],
    }
    for name,paths in groups.items():
        command=[sys.executable,'-X','utf8','-B','-m','pytest',*map(str,paths),'-q','-p','no:cacheprovider',
                 '--rootdir',str(ROOT),'--confcutdir',str(ROOT),'--basetemp',str(run/name),'--junitxml',str(run/(name+'.xml'))]
        print('Running '+name,flush=True)
        result=subprocess.run(command,cwd=ROOT,env=env)
        if result.returncode:return result.returncode
    print('All regression suites passed. Results: '+str(run),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
