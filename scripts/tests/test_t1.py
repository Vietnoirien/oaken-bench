"""T1 packages must omit exactly the target implementation."""
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from t1 import MODULES, ROOT, assemble, digest, fixed_source_changes  # noqa: E402


def test_package_omits_one_reference_module(tmp_path):
    package = assemble(tmp_path / 'package', 'economy')
    manifest = json.loads((package / 'manifest.json').read_text())
    with tarfile.open(package / 'workspace.tgz') as archive:
        files = {m.name: m for m in archive if m.isfile()}
        with archive.extractfile(files['work/src/economy.ts']) as stream:
            import hashlib
            assert hashlib.sha256(stream.read()).hexdigest() == digest(ROOT / 'seed/src/economy.ts')
        for module in MODULES:
            if module != 'economy':
                assert module + '.ts' in manifest['fixedSourceSha256']
                assert 'work/src/' + module + '.ts' in files
    assert fixed_source_changes(package, manifest) == []


def test_restored_control_uses_reference_target(tmp_path):
    package = assemble(tmp_path / 'package', 'rng', restore=True)
    with tarfile.open(package / 'workspace.tgz') as archive:
        target = archive.extractfile('work/src/rng.ts').read()
    import hashlib
    assert hashlib.sha256(target).hexdigest() == digest(ROOT / 'refengine/rng.ts')
