#!/usr/bin/env python3
"""T3's bug generator (issue #42): plants N deterministic bugs into an
unlocked copy of the `refengine` bundle (issue #37), confirms each one
breaks at least one held-out test via the real scorer, and seals the
result as its own bundle for scripts/hidden.sh (issue #35's registry --
see the `t3instance` entry there and in .gitignore).

  ./scripts/planted_bugs.py generate --seed S --n N [--refengine DIR] [--out DIR]
  ./scripts/planted_bugs.py catalogue

WHO MAY RUN THIS AND WHY IT MATTERS
  This script's author must never be the reference engine's author
  (CANARY.md section 4: reaching 132/132 against the held-out suite
  contaminates whoever did it for every later spec/oracle on the issue
  #26 ladder). This module never reads `hidden/` directly -- confirmation
  goes through scripts/score.py's own container path
  (score_in_container()), which decrypts the held-out suite to a
  container and hands back suite counts only. That is the whole point of the
  design: a bug's fitness is judged by the scorer's pass/fail numbers,
  never by reading the suite that produced them.

MUTATION CATALOGUE (operator classes -- see CATALOGUE below for the
authoritative, machine-readable copy)
  comparator_swap    a comparison operator (<, <=, >, >=) becomes a
                      DIFFERENT one from that same set -- covers both
                      off-by-one (a boundary shifts by one: < <-> <=) and
                      wrong-comparison (the direction flips: < <-> >) bugs
                      with one operator, since both are "the wrong member
                      of this same token family" at the source level.
  equality_invert     ===/!==  or  ==/!=  swap polarity.
  dropped_clamp       Math.min(A, B) or Math.max(A, B) collapses to just
                      A -- the clamp silently stops bounding the value.
  sign_flip           a `+` or `-` between two simple operands (a bare
                      identifier, member access, or number on each side)
                      becomes the other sign.
  operand_swap        A - B  becomes  B - A  (a distinct failure mode from
                      sign_flip at the same site: this preserves the
                      operator and swaps the operands' ORDER instead,
                      which for subtraction changes the sign of the
                      result but not by a constant amount -- the two
                      classes are deliberately allowed to compete for the
                      same span so sampling can land on either).
  boolean_invert      `if (COND)` for a COND with no nested parens or
                      binary operators (a bare identifier, optionally
                      negated, or a simple property access) has its
                      leading `!` added or removed.

  Most edits preserve the expression type, but the scorer checks every
  candidate's typecheck and collection anyway. A compile failure would
  make tests uncollected and would not count as a planted logic bug.
  Visible failures are recorded in the manifest; they are not required
  to be zero.

  Candidates are never taken from inside a comment or a string/template
  literal -- see _excluded_spans(). Mutating a comment does not plant a
  bug; it wastes a slot on a no-op and a manifest entry that points at
  nothing.

DETERMINISM
  Candidate order is fixed: files sorted by name, then CATALOGUE order,
  then each operator's regex matches left-to-right within the file. A
  `random.Random(seed)` shuffles the candidate INDEX order once and walks
  it in that order, so the same seed always proposes bugs in the same
  sequence -- see plant_bugs()'s docstring for exactly how "propose,
  confirm, accept-or-skip" turns that sequence into a chosen set.
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(BENCH, 'seed')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Span bookkeeping: comments and string/template literals are never mutated.
# ---------------------------------------------------------------------------

def _excluded_spans(text):
    """Exclude complete comments and quoted literals before choosing edits.

    Scan once so comment markers inside strings and escaped quotes cannot
    shift the spans. Template interpolation is excluded with its literal.
    """
    spans = []
    i = 0
    while i < len(text):
        start = i
        if text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = len(text) if end < 0 else end + 2
        elif text[i] in "'\"`":
            quote = text[i]
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        else:
            i += 1
            continue
        spans.append((start, min(i, len(text))))
    return spans


def _overlaps_excluded(start, end, spans):
    return any(a < end and start < b for a, b in spans)


# ---------------------------------------------------------------------------
# Small paren/brace aware helpers (Math.min/max args can nest, e.g.
# economy.ts's `Math.min(MAX_HP_CAP, run.maxHp + Math.floor(run.maxHp * 0.1))`)
# ---------------------------------------------------------------------------

def _matching_close(text, open_idx):
    """Index of the ')' matching the '(' at open_idx, or -1."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top_level_args(s):
    parts, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts]


# ---------------------------------------------------------------------------
# Candidate: one place in one file where one operator COULD apply.
# ---------------------------------------------------------------------------

class Candidate:
    __slots__ = ('file', 'operator', 'start', 'end', 'original', 'replacement',
                 'confirm_result')

    def __init__(self, file, operator, start, end, original, replacement):
        self.file = file
        self.operator = operator
        self.start = start
        self.end = end
        self.original = original
        self.replacement = replacement
        self.confirm_result = None

    def line(self, text):
        return text.count('\n', 0, self.start) + 1

    def key(self):
        # Deterministic id independent of dict iteration order -- used both
        # as the manifest's bug id and to make overlap-checking exact.
        return f'{self.file}:{self.start}:{self.end}:{self.operator}:{self.replacement}'

    def to_dict(self, text):
        return {
            'id': self.key(),
            'file': self.file,
            'operator': self.operator,
            'line': self.line(text),
            'original': self.original,
            'mutated': self.replacement,
        }


# A comparator token becomes a DIFFERENT member of this family. Deterministic
# per-candidate replacement pick (not random at find time) keeps
# find_candidates() a pure function of the file text.
_COMPARATORS = ('<', '<=', '>', '>=')
_EQUALITIES = ('===', '!==', '==', '!=')


def _find_comparator_swap(text, excluded):
    out = []
    for m in re.finditer(r'(?<=\s)(<=|>=|<|>)(?=\s)', text):
        s, e = m.start(), m.end()
        if _overlaps_excluded(s, e, excluded):
            continue
        op = m.group(1)
        # Every OTHER member of the family is a valid mutation; which one
        # gets used is picked later (plant_bugs) from a seeded RNG, so one
        # Candidate per (site, replacement) pair keeps find_* pure and lets
        # the caller choose reproducibly.
        for repl in _COMPARATORS:
            if repl != op:
                out.append(Candidate(None, 'comparator_swap', s, e, op, repl))
    return out


def _find_equality_invert(text, excluded):
    out = []
    for m in re.finditer(r'(?<=\s)(===|!==|==|!=)(?=\s)', text):
        s, e = m.start(), m.end()
        if _overlaps_excluded(s, e, excluded):
            continue
        op = m.group(1)
        # Only the same-arity partner (=== with !==, == with !=): crossing
        # arity (== -> !==) is a second, unrelated change JS/TS does not
        # even let you make with one flip, so it is not "a bug", it is a
        # different expression shape.
        partner = {'===': '!==', '!==': '===', '==': '!=', '!=': '=='}[op]
        out.append(Candidate(None, 'equality_invert', s, e, op, partner))
    return out


def _find_dropped_clamp(text, excluded):
    out = []
    for m in re.finditer(r'Math\.(min|max)\(', text):
        open_idx = m.end() - 1
        close_idx = _matching_close(text, open_idx)
        if close_idx == -1:
            continue
        s, e = m.start(), close_idx + 1
        if _overlaps_excluded(s, e, excluded):
            continue
        inner = text[open_idx + 1:close_idx]
        args = _split_top_level_args(inner)
        if len(args) != 2:
            continue
        out.append(Candidate(None, 'dropped_clamp', s, e, text[s:e], args[0]))
    return out


def _simple_operand(tok):
    return re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*|\d+(\.\d+)?', tok.strip()) is not None


def _find_sign_and_operand(text, excluded):
    out = []
    for m in re.finditer(
        r'([A-Za-z0-9_.]+)[ \t](\+|-)[ \t]([A-Za-z0-9_.]+)', text
    ):
        s, e = m.start(), m.end()
        if _overlaps_excluded(s, e, excluded):
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        if not (_simple_operand(left) and _simple_operand(right)):
            continue
        flipped = '-' if op == '+' else '+'
        out.append(Candidate(None, 'sign_flip', s, e,
                             f'{left} {op} {right}', f'{left} {flipped} {right}'))
        if op == '-':
            # a - b -> b - a: only meaningful (and only a DIFFERENT bug
            # from sign_flip) for subtraction -- addition is commutative,
            # so swapping operands of `a + b` is not a mutation at all.
            out.append(Candidate(None, 'operand_swap', s, e,
                                 f'{left} {op} {right}', f'{right} {op} {left}'))
    return out


def _find_boolean_invert(text, excluded):
    out = []
    for m in re.finditer(r'if \((!?[A-Za-z_][A-Za-z0-9_.]*)\)', text):
        s, e = m.start(1), m.end(1)
        if _overlaps_excluded(s, e, excluded):
            continue
        cond = m.group(1)
        flipped = cond[1:] if cond.startswith('!') else '!' + cond
        out.append(Candidate(None, 'boolean_invert', s, e, cond, flipped))
    return out


# Fixed order -> deterministic candidate list for a given file's text.
CATALOGUE = (
    ('comparator_swap', _find_comparator_swap),
    ('equality_invert', _find_equality_invert),
    ('dropped_clamp', _find_dropped_clamp),
    ('sign_operand', _find_sign_and_operand),  # yields sign_flip + operand_swap
    ('boolean_invert', _find_boolean_invert),
)


def find_candidates(filename, text):
    excluded = _excluded_spans(text)
    out = []
    for _name, fn in CATALOGUE:
        for c in fn(text, excluded):
            c.file = filename
            out.append(c)
    # Stable, content-only order: by position, then operator name, then
    # replacement -- independent of dict/regex engine iteration quirks, so
    # the same file text always yields the same candidate LIST order.
    out.sort(key=lambda c: (c.start, c.end, c.operator, c.replacement))
    return out


def load_ts_files(src_dir):
    """{relpath: text} for every *.ts directly under src_dir, sorted."""
    files = {}
    for fn in sorted(os.listdir(src_dir)):
        if fn.endswith('.ts'):
            files[fn] = open(os.path.join(src_dir, fn), encoding='utf-8').read()
    return files


def all_candidates(files):
    out = []
    for fn in sorted(files):
        out.extend(find_candidates(fn, files[fn]))
    return out


def _apply(text, candidates_for_file):
    """Apply non-overlapping candidates to one file's text, highest offset
    first so earlier offsets are never invalidated by a later edit."""
    for c in sorted(candidates_for_file, key=lambda c: c.start, reverse=True):
        text = text[:c.start] + c.replacement + text[c.end:]
    return text


def apply_bugs(files, chosen):
    """files: {relpath: text}. chosen: list[Candidate]. Returns a NEW dict;
    the input is untouched."""
    by_file = {}
    for c in chosen:
        by_file.setdefault(c.file, []).append(c)
    out = dict(files)
    for fn, cs in by_file.items():
        out[fn] = _apply(out[fn], cs)
    return out


class PlantResult:
    def __init__(self, mutated_files, chosen, rejected):
        self.mutated_files = mutated_files
        self.chosen = chosen          # list[Candidate]
        self.rejected = rejected      # list[(Candidate, reason)]


def plant_bugs(files, seed, n, confirm=None):
    """Deterministically choose `n` non-overlapping candidates from `files`
    and return the mutated engine plus the chosen Candidates.

    Selection is propose/confirm/accept-or-skip, not plain rng.sample():
    `random.Random(seed)` shuffles every candidate's index ONCE (so the
    proposal order is fixed for a given seed regardless of how many get
    skipped), then walks that order. A candidate is skipped, not fatal,
    when it overlaps a span already chosen (two operator classes can
    target the same token, see CATALOGUE's sign_flip/operand_swap note)
    or when `confirm` rejects it. `confirm(candidate, single_bug_files)`,
    if given, must return a dict with at least a truthy/falsy 'ok' --
    normally "this candidate, planted ALONE, fails >=1 held-out test"
    (see build_confirmer()). Passing confirm=None accepts every
    non-overlapping proposal instead, which is how the unit tests exercise
    this function without a docker scorer or an unlocked refengine.

    Raises ValueError if the candidate pool is exhausted before `n` bugs
    are accepted -- silently returning fewer than asked for would let a
    generation call look successful while quietly shipping a weaker
    instance than its manifest's `n` claims.
    """
    if n < 1:
        raise ValueError('n must be positive')
    candidates = all_candidates(files)
    if not candidates:
        raise ValueError('no mutation candidates found in the given files')
    order = list(range(len(candidates)))
    random.Random(seed).shuffle(order)

    chosen = []
    rejected = []
    chosen_spans = {}  # file -> list[(start, end)]

    def overlaps_chosen(c):
        for a, b in chosen_spans.get(c.file, []):
            if a < c.end and c.start < b:
                return True
        return False

    for idx in order:
        if len(chosen) >= n:
            break
        c = candidates[idx]
        if overlaps_chosen(c):
            rejected.append((c, 'overlaps an already-chosen span'))
            continue
        if confirm is not None:
            single = apply_bugs(files, [c])
            result = confirm(c, single)
            if not result.get('ok'):
                rejected.append((c, result.get('reason', 'confirm rejected')))
                continue
            c.confirm_result = result
        chosen.append(c)
        chosen_spans.setdefault(c.file, []).append((c.start, c.end))

    if len(chosen) < n:
        raise ValueError(
            f'only {len(chosen)}/{n} bugs could be planted and confirmed '
            f'from {len(candidates)} candidates for seed={seed}')

    mutated = apply_bugs(files, chosen)
    return PlantResult(mutated, chosen, rejected)


# ---------------------------------------------------------------------------
# Confirmation: run the REAL scorer (scripts/score.py) against a synthetic
# workspace, never against hidden/ directly. See the module docstring.
# ---------------------------------------------------------------------------

def _assemble_work(dest, src_files):
    """A scoreable `dest/` workspace: seed's frozen artefacts plus the given
    src/*.ts. tests/ content does not matter -- docker/scorer.sh replaces it
    from /opt/seed/tests (visible) and the decrypted bundle (hidden) before
    every suite runs, see docker/scorer.sh's restore()/run_suite()."""
    os.makedirs(dest, exist_ok=True)
    for rel in ('SPEC.md', 'data', 'package.json', 'tsconfig.json', 'vitest.config.ts', 'tests'):
        s = os.path.join(SEED, rel)
        d = os.path.join(dest, rel)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy(s, d)
    src_dir = os.path.join(dest, 'src')
    os.makedirs(src_dir, exist_ok=True)
    for fn, text in src_files.items():
        with open(os.path.join(src_dir, fn), 'w', encoding='utf-8') as f:
            f.write(text)


def write_synthetic_result_dir(result_dir, src_files, label, model='refengine-bugcheck'):
    """Builds a results/<label>-shaped directory good enough for
    score.score_run(): a workspace.tgz whose top-level entry is `work/`
    (restore_workspace()'s contract), plus the handful of sidecar files
    score_run() reads directly. No harness ran -- this is the "hand the
    scorer a workspace" path score.py already exercises for prior scratch
    verifications (see refengine.score.json's _comment)."""
    os.makedirs(result_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = os.path.join(td, 'work')
        _assemble_work(work, src_files)
        tgz = os.path.join(result_dir, 'workspace.tgz')
        with tarfile.open(tgz, 'w:gz') as t:
            t.add(work, arcname='work')
    with open(os.path.join(result_dir, 'run.meta'), 'w') as f:
        f.write(f'harness=synthetic model={model}\n')
    with open(os.path.join(result_dir, 'exit.code'), 'w') as f:
        f.write('0\n')
    with open(os.path.join(result_dir, 'wallclock.seconds'), 'w') as f:
        f.write('1\n')


def score_synthetic(src_files, label, image=None, quiet=True):
    """Score one synthetic workspace through the container, then discard it."""
    import score as score_module
    old_image = score_module.IMAGE
    if image:
        score_module.IMAGE = image
    with tempfile.TemporaryDirectory(prefix='t3confirm-') as result_dir:
        write_synthetic_result_dir(result_dir, src_files, label)
        if quiet:
            devnull = open(os.devnull, 'w')
            old_stdout = sys.stdout
            sys.stdout = devnull
        try:
            report = score_module.score_run(result_dir, detail=False)
        finally:
            score_module.IMAGE = old_image
            if quiet:
                sys.stdout = old_stdout
                devnull.close()
    return report


def _sound_score(report):
    """A test failure only means a bug if both suites actually completed."""
    return (report.get('restored') and report.get('typecheckClean')
            and not report.get('suiteHung')
            and not report.get('tamperedFrozenFiles')
            and all(report.get(name, {}).get('total', 0) > 0
                    and report[name].get('collected') == report[name]['total']
                    and report[name].get('passed', 0) + report[name].get('failed', 0)
                        == report[name]['total']
                    and not report[name].get('error')
                    for name in ('visible', 'hidden')))


def build_confirmer(baseline_files, image=None, label_prefix='t3confirm'):
    """A `confirm(candidate, single_bug_files)` for plant_bugs(): scores the
    single-bug variant and accepts it iff it breaks >=1 held-out test AND
    still typechecks (a candidate that fails to typecheck did not plant a
    LOGIC bug -- see the module docstring's type-preservation note; the
    catalogue is designed so this should never actually trigger, and it is
    kept as a guard, not the primary filter).

    The baseline must pass the complete oracle. Otherwise a hidden failure
    in a single-bug workspace might belong to the reference engine itself."""
    state = {}

    def confirm(candidate, single_bug_files):
        if 'baseline' not in state:
            rep = score_synthetic(baseline_files, 'baseline', image=image)
            if (not _sound_score(rep)
                    or rep['hidden']['passed'] != rep['hidden']['total']
                    or rep['visible']['passed'] != rep['visible']['total']):
                raise RuntimeError('reference engine baseline did not pass both complete suites')
            state['baseline'] = rep
        base_rep = state['baseline']
        label = f'{label_prefix}-{candidate.key()}'.replace('/', '_')
        rep = score_synthetic(single_bug_files, label, image=image)
        hidden_failed = base_rep['hidden']['passed'] - rep['hidden']['passed']
        visible_failed = rep['visible']['failed']
        ok = _sound_score(rep) and hidden_failed >= 1
        return {
            'ok': ok,
            'reason': None if ok else (
                'incomplete or invalid scorer result' if not _sound_score(rep)
                else 'broke no hidden test'),
            'hiddenFailed': hidden_failed,
            'hiddenTotal': rep['hidden']['total'],
            'visibleFailed': visible_failed,
            'visibleTotal': rep['visible']['total'],
            'typecheckClean': rep['typecheckClean'],
        }

    confirm.state = state
    return confirm


# ---------------------------------------------------------------------------
# Sealing: reuse scripts/hidden.sh's generic bundle machinery (issue #35)
# rather than a second AES implementation in Python. See scripts/hidden.sh's
# `t3instance` registry entry and CANARY.md section 5.
# ---------------------------------------------------------------------------

def seal_instance(instance_dir, bench_root=None):
    """Seal only the T3 instance; other registered bundles are oracles."""
    root = bench_root or BENCH
    bundle = 't3instance'
    expect = os.path.join(root, bundle)
    if os.path.abspath(instance_dir) != os.path.abspath(expect):
        raise ValueError(f'instance_dir must be {expect} for hidden.sh to find it, '
                         f'got {instance_dir}')
    env = dict(os.environ, OAKEN_BENCH_ROOT=root)
    subprocess.run([os.path.join(BENCH, 'scripts', 'hidden.sh'), 'lock', bundle],
                   check=True, env=env, cwd=root)
    return os.path.join(root, f'{bundle}.tar.gz.enc')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _digest_files(files):
    h = hashlib.sha256()
    for fn in sorted(files):
        h.update(fn.encode())
        h.update(b'\0')
        h.update(files[fn].encode())
        h.update(b'\0')
    return h.hexdigest()


def cmd_catalogue(_args):
    print(__doc__)


def cmd_generate(args):
    refengine_dir = args.refengine or os.path.join(BENCH, 'refengine')
    if not os.path.isdir(refengine_dir):
        raise SystemExit(
            f'{refengine_dir} not found -- run `scripts/hidden.sh unlock refengine` first')
    baseline = load_ts_files(refengine_dir)

    confirm = build_confirmer(baseline, image=args.image)
    result = plant_bugs(baseline, args.seed, args.n, confirm=confirm)

    combined_rep = score_synthetic(result.mutated_files,
                                   f'combined-seed{args.seed}-n{args.n}', image=args.image)
    if not _sound_score(combined_rep):
        raise RuntimeError('combined instance did not complete both suites and typecheck')
    if combined_rep['hidden']['passed'] == combined_rep['hidden']['total']:
        raise RuntimeError('combined mutations cancel out against the hidden suite')

    out_dir = args.out or os.path.join(BENCH, 't3instance')
    if args.seal and os.path.abspath(out_dir) != os.path.join(BENCH, 't3instance'):
        raise ValueError('--seal requires --out to name the registered bundle directory')
    src_out = os.path.join(out_dir, 'src')
    if os.path.exists(out_dir):
        if (not os.path.isdir(out_dir) or os.path.islink(out_dir)
                or set(os.listdir(out_dir)) - {'src', 'manifest.json'}
                or os.path.islink(src_out)):
            raise ValueError(f'refusing to replace non-instance directory: {out_dir}')
        shutil.rmtree(out_dir)
    os.makedirs(src_out)
    for fn, text in result.mutated_files.items():
        with open(os.path.join(src_out, fn), 'w', encoding='utf-8') as f:
            f.write(text)

    original_by_file = {c.file: baseline[c.file] for c in result.chosen}
    manifest = {
        'schemaVersion': 1,
        'tier': 't3',
        'seed': args.seed,
        'n': args.n,
        'refengineDigest': _digest_files(baseline),
        'generatorAuthorNote': (
            'Generator author must NOT be the reference engine author '
            '(CANARY.md section 4).'),
        'bugs': [
            {**c.to_dict(original_by_file[c.file]),
             'confirm': c.confirm_result}
            for c in result.chosen
        ],
        'combined': {
            'hiddenPassed': combined_rep['hidden']['passed'],
            'hiddenTotal': combined_rep['hidden']['total'],
            'visiblePassed': combined_rep['visible']['passed'],
            'visibleTotal': combined_rep['visible']['total'],
            'typecheckClean': combined_rep['typecheckClean'],
        },
    }
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f'planted {len(result.chosen)} bug(s) from seed={args.seed} into {out_dir}')
    for c in result.chosen:
        cr = getattr(c, 'confirm_result', None)
        extra = f" hiddenFailed={cr['hiddenFailed']} visibleFailed={cr['visibleFailed']}" if cr else ''
        print(f'  [{c.operator}] {c.file}:{c.line(original_by_file[c.file])}'
              f' {c.original!r} -> {c.replacement!r}{extra}')
    if 'combined' in manifest:
        cb = manifest['combined']
        print(f"combined instance: hidden {cb['hiddenPassed']}/{cb['hiddenTotal']}, "
              f"visible {cb['visiblePassed']}/{cb['visibleTotal']}, "
              f"typecheck {'clean' if cb['typecheckClean'] else 'DIRTY'}")

    if args.seal:
        enc = seal_instance(out_dir)
        print(f'sealed -> {enc}')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    g = sub.add_parser('generate', help='plant N bugs from a seed into refengine/')
    g.add_argument('--seed', type=int, required=True)
    g.add_argument('--n', type=int, required=True)
    g.add_argument('--refengine', default=None, help='default: <repo>/refengine')
    g.add_argument('--out', default=None, help='default: <repo>/t3instance')
    g.add_argument('--image', default=None, help='OAKEN_IMAGE override for confirmation')
    g.add_argument('--seal', action='store_true',
                   help='also run scripts/hidden.sh lock <bundle> on the output')
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser('catalogue', help='print the mutation catalogue')
    c.set_defaults(func=cmd_catalogue)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
