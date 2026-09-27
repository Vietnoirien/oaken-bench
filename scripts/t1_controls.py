#!/usr/bin/env python3
"""Restore each omitted reference module and require a clean 132/132 score."""
import json
from pathlib import Path
import shutil
import tempfile

from t1 import MODULES, ROOT, assemble, score_run


def main():
    checks = {}
    with tempfile.TemporaryDirectory(prefix='oaken-t1-controls-') as td:
        for module in MODULES:
            result = Path(td) / ('t1-' + module + '-reference-control')
            package = assemble(Path(td) / ('package-' + module), module, restore=True)
            result.mkdir()
            shutil.copyfile(package / 'workspace.tgz', result / 'workspace.tgz')
            shutil.copyfile(package / 'manifest.json', result / 't1-manifest.json')
            (result / 'run.meta').write_text('harness=control model=reference tier=t1\n')
            (result / 'exit.code').write_text('0\n')
            (result / 'wallclock.seconds').write_text('0\n')
            report = score_run(result)
            valid = (report['fullHidden']['passed'] == 132 and
                     report['fullHidden']['collected'] == 132 and
                     report['visible']['passed'] == 52 and
                     report['typecheckClean'] and
                     not report['tamperedFrozenFiles'] and
                     not report['fixedSourceChanges'])
            checks[module] = {'hiddenPassed': report['fullHidden']['passed'],
                              'visiblePassed': report['visible']['passed'],
                              'typecheckClean': report['typecheckClean'],
                              'targetPassed': report['targetHidden']['passed'],
                              'valid': valid}
            if not valid:
                raise SystemExit('T1 reference control failed for ' + module)
    output = ROOT / 't1' / 'control-results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'schemaVersion': 1, 'controls': checks}, indent=2) + '\n')


if __name__ == '__main__':
    main()
