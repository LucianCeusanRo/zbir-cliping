"""`python -m zbirclip selftest [--quick]`: runs the automated tests in an isolated temp folder
(never touches your real data/state.db or TikTok)."""
import os, subprocess, sys, tempfile
from pathlib import Path
from .util import ROOT


def main(quick=False):
    with tempfile.TemporaryDirectory(prefix="zbir-test-") as tmp:
        env = dict(os.environ, ZBIR_HOME=tmp, ZBIR_CONFIG=str(Path(os.environ.get("ZBIR_CONFIG", ROOT / "config.yaml"))),
                   ZBIR_FONTS=str(ROOT / "fonts"), ZBIR_MODELS=str(ROOT / "models"), ZBIR_QUICK="1" if quick else "0",
                   PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-m", "unittest", "-v", "zbirclip._tests"], env=env, cwd=str(ROOT))
        print("\nTOATE TESTELE AU TRECUT ✔" if r.returncode == 0 else "\nUNELE TESTE AU PICAT ✘ (vezi mai sus)")
        return r.returncode
