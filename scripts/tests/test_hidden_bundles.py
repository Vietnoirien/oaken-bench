"""Tests for scripts/hidden.sh's named-bundle registry (issue #35).

hidden.sh is bash, not Python, so this drives it as a subprocess. Every test
here uses a throwaway BENCH root via OAKEN_BENCH_ROOT and dummy plaintext
files -- NEVER the real repo root. That is deliberate:
issue #35's brief forbids testing this machinery by unlocking the real
held-out suite (that would mean reading `hidden/`, which is exactly the
contamination CANARY.md exists to prevent). A temp dir stands in for BENCH,
so the roundtrip is proven without the real `hidden.tar.gz.enc` /
`hidden.sha256` ever being touched.
"""
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HIDDEN_SH = os.path.join(REPO, 'scripts', 'hidden.sh')


def _run(args, env, cwd=REPO):
    return subprocess.run([HIDDEN_SH, *args], env=env, cwd=cwd,
                           capture_output=True, text=True)


def registered_bundles():
    out = subprocess.run([HIDDEN_SH, 'bundles'], cwd=REPO,
                          capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_hidden_is_registered_and_still_the_default():
    names = registered_bundles()
    assert 'hidden' in names
    # `hidden.sh` with no bundle argument must keep meaning the held-out
    # suite -- bootstrap.sh and every existing doc call it bare.
    default_status = subprocess.run([HIDDEN_SH, 'status'], cwd=REPO,
                                     capture_output=True, text=True)
    named_status = subprocess.run([HIDDEN_SH, 'status', 'hidden'], cwd=REPO,
                                   capture_output=True, text=True)
    assert default_status.stdout == named_status.stdout


@pytest.mark.parametrize('name', registered_bundles())
def test_bundle_plaintext_dir_is_gitignored(name):
    """A registered bundle whose plaintext dir isn't gitignored risks the
    plaintext getting committed the first time someone runs `unlock` and
    then `git add -A` -- the exact silent-decay failure CANARY.md exists to
    prevent. This fails loudly at test time instead.

    Trailing slash matters: git only matches a non-existent path against a
    directory-only gitignore pattern (like `/hidden/`) when asked with one.
    """
    target = os.path.join(REPO, name) + '/'
    check = subprocess.run(['git', 'check-ignore', '-q', target], cwd=REPO)
    assert check.returncode == 0, (
        f"{name}/ is not gitignored -- add it to .gitignore before "
        f"registering '{name}' in scripts/hidden.sh's BUNDLE_DEFAULT_PASS"
    )


def test_unknown_bundle_name_is_rejected(tmp_path):
    env = dict(os.environ, OAKEN_BENCH_ROOT=str(tmp_path))
    result = _run(['status', 'not-a-real-bundle'], env)
    assert result.returncode != 0
    assert 'unknown bundle' in result.stderr


def test_lock_unlock_verify_roundtrip_on_throwaway_bundle(tmp_path):
    """Full lifecycle against dummy files in tmp_path, standing in for BENCH.
    Proves the generalised lock/unlock/verify/status machinery works without
    going anywhere near the real hidden.tar.gz.enc.
    """
    hidden_dir = tmp_path / 'hidden'
    hidden_dir.mkdir()
    content = '// throwaway fixture for issue #35 tests, not real suite data\n'
    (hidden_dir / 'dummy.test.ts').write_text(content)

    env = dict(os.environ, OAKEN_BENCH_ROOT=str(tmp_path))

    locked = _run(['lock', 'hidden'], env)
    assert locked.returncode == 0, locked.stderr
    assert (tmp_path / 'hidden.tar.gz.enc').exists()
    assert (tmp_path / 'hidden.sha256').exists()

    verified = _run(['verify', 'hidden'], env)
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.startswith('OK:')

    status = _run(['status', 'hidden'], env)
    assert status.returncode == 0, status.stderr
    assert 'agreement  : hidden/ matches the blob' in status.stdout

    # Re-locking an unchanged bundle must be a no-op, not a re-encrypt.
    relocked = _run(['lock', 'hidden'], env)
    assert relocked.returncode == 0
    assert 'unchanged' in relocked.stdout

    # Drop the plaintext and rebuild it purely from the encrypted blob.
    shutil.rmtree(hidden_dir)
    unlocked = _run(['unlock', 'hidden'], env)
    assert unlocked.returncode == 0, unlocked.stderr
    assert (hidden_dir / 'dummy.test.ts').read_text() == content

    # unlock must refuse to clobber a plaintext dir that has since drifted.
    (hidden_dir / 'dummy.test.ts').write_text(content + 'tampered\n')
    drifted = _run(['unlock', 'hidden'], env)
    assert drifted.returncode != 0
    assert 'differs from' in drifted.stderr


def test_passphrase_override_is_per_bundle(tmp_path):
    """OAKEN_<BUNDLE>_PASS must gate that bundle specifically -- the default
    passphrase must not decrypt a blob locked under an override.
    """
    hidden_dir = tmp_path / 'hidden'
    hidden_dir.mkdir()
    (hidden_dir / 'dummy.test.ts').write_text('dummy\n')

    override_env = dict(os.environ, OAKEN_BENCH_ROOT=str(tmp_path),
                         OAKEN_HIDDEN_PASS='throwaway-pass-for-test-only')
    assert _run(['lock', 'hidden'], override_env).returncode == 0
    shutil.rmtree(hidden_dir)

    default_env = dict(os.environ, OAKEN_BENCH_ROOT=str(tmp_path))
    bad = _run(['unlock', 'hidden'], default_env)
    assert bad.returncode != 0
    assert not hidden_dir.exists()

    good = _run(['unlock', 'hidden'], override_env)
    assert good.returncode == 0, good.stderr
    assert hidden_dir.exists()
