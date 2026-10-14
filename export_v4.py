"""Stable command entry point; implementation is in scripts/export_v4.py."""
from pathlib import Path
import runpy
if __name__=='__main__':
    runpy.run_path(str(Path(__file__).resolve().parent/'scripts/export_v4.py'),run_name='__main__')
