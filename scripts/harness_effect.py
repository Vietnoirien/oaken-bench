#!/usr/bin/env python3
"""Compare direct, pi, and dsh on one seeded T0.5 question set.

Each mode gets the same seeded ledger and question wording. Direct mode gets
the ledger inline; pi and dsh run in the pinned benchmark container and must
read `ledger.txt` from their mounted workspace. The artefact
contains only aggregate counts and a digest of the case set: expected values
and model answers stay in memory, where publishing either would turn this
public screening probe into a reusable answer key.

pi and dsh are asked to return a JSON object as their final answer. Their
container commands can be replaced by local fake commands for tests with
`--pi-command` / `--dsh-command` (a shell-like argv string; `{prompt}` is
replaced by the prompt as one argument). No shell is used to execute them.
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abstain  # noqa: E402
import direct  # noqa: E402
import haystack  # noqa: E402
import recall  # noqa: E402

SCHEMA_VERSION = 2
DEFAULT_DEPTHS = (4096, 16384, 32768, 65536, 131072)


def build_case_set(depth_tokens, seed, *, chars_per_token=None, count_tokens_fn=None,
                   num_facts=3, num_abstention_each=2):
    """Build the ground truth once; all modes consume this immutable case set."""
    depth_seed = recall._depth_seed(seed, depth_tokens)
    stack = haystack.generate_haystack(
        seed=depth_seed, target_tokens=depth_tokens, num_facts=num_facts,
        position_fractions=recall.POSITION_FRACTIONS[:num_facts],
        chars_per_token=chars_per_token or haystack.DEFAULT_CHARS_PER_TOKEN,
        count_tokens_fn=count_tokens_fn)
    absent = abstain.build_abstention_questions(
        stack, seed=depth_seed + 1, num_each=num_abstention_each)
    all_pairs = [(f.entity, f.attribute) for f in stack.facts]
    all_pairs.extend((q.entity, q.attribute) for q in absent)
    canonical = json.dumps({
        'seed': depth_seed, 'depthTokens': depth_tokens, 'text': stack.text,
        'facts': [(f.entity, f.attribute, f.value, f.depth_position) for f in stack.facts],
        'absent': [(q.entity, q.attribute, q.kind) for q in absent],
    }, sort_keys=True, separators=(',', ':')).encode()
    return {
        'haystack': stack, 'absent': tuple(absent), 'pairs': tuple(all_pairs),
        'digest': hashlib.sha256(canonical).hexdigest(),
    }


def build_prompt(case_set, *, ledger_path=None):
    """Build the common question contract; harness mode references its workspace file."""
    stack = case_set['haystack']
    pairs = [(f.entity, f.attribute) for f in stack.facts]
    pairs.extend((q.entity, q.attribute) for q in case_set['absent'])
    lines = [f'- entity {entity!r}, attribute {attribute!r}' for entity, attribute in pairs]
    document_instruction = (
        f'Read the document from the workspace file `{ledger_path}` and use only that document.'
        if ledger_path else 'Use only the document below.'
    )
    document_section = (f'\n\nDOCUMENT\n{stack.text}' if ledger_path is None else '')
    return (
        document_instruction + ' For each listed entity and attribute, give the value '
        'exactly as written. Do not guess or use outside knowledge. If a pair is not stated, '
        f'answer exactly {abstain.INSTRUCTED_PHRASE!r}. Return one answer for every pair.'
        + document_section + '\n\nPAIRS\n' + '\n'.join(lines) +
        '\n\nReturn only one JSON object with this shape: '
        '{"answers":[{"entity":"...","attribute":"...","value":"..."}, ...]}.'
    )


def parse_answers(text):
    """Read a final JSON answer without retaining arbitrary prose in reports."""
    if not isinstance(text, str):
        return None
    candidates = [text.strip()]
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get('answers'), list):
            return value
    return None


def score_answers(case_set, answers):
    """Return aggregate counts only; answer values and planted facts are dropped."""
    present = recall._score_recall(case_set['haystack'].facts, answers)
    absent = recall._score_abstention(case_set['absent'], answers)
    pc = recall._present_question_counts(present)
    ac = recall._abstention_counts(absent)
    return {
        'present': {k: pc[k] for k in (recall.CORRECT_ANSWER, recall.WRONG_ANSWER,
                                      recall.FALSE_ABSTENTION, 'total')},
        'absent': {k: ac['overall'][k] for k in
                   (recall.CORRECT_ABSTENTION, recall.INVENTED_ANSWER, 'total')},
        'abstentionByKind': {
            kind: {k: v[k] for k in (recall.CORRECT_ABSTENTION,
                                      recall.INVENTED_ANSWER, 'total')}
            for kind, v in ac['byKind'].items()},
    }


def _item_id(depth_tokens, entity, attribute):
    """Stable opaque identifier for one pair at one depth. It carries no answer."""
    payload = json.dumps([depth_tokens, entity, attribute],
                         ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def classify_items(case_set, depth_tokens, answers):
    """Return classifications keyed by digest, with identifying text removed."""
    present = recall._score_recall(case_set['haystack'].facts, answers)
    absent = recall._score_abstention(case_set['absent'], answers)
    items = []
    for result in present:
        items.append({
            'itemId': _item_id(depth_tokens, result['entity'], result['attribute']),
            'questionType': 'present', 'kind': 'recall',
            'classification': result['classification'],
        })
    for result in absent:
        items.append({
            'itemId': _item_id(depth_tokens, result['entity'], result['attribute']),
            'questionType': 'absent', 'kind': result['kind'],
            'classification': result['classification'],
        })
    return items


def _compare_item_rows(item_rows, left_mode, right_mode, left_status, right_status,
                       *, allow_partial=False):
    """Compare matching classified items. Failed modes produce null deltas."""
    groups = (
        ('present', 'correct_answer'),
        ('absent', recall.CORRECT_ABSTENTION),
    )
    comparison = {'leftMode': left_mode, 'rightMode': right_mode,
                  'leftStatus': left_status, 'rightStatus': right_status}
    modes_ok = allow_partial or (left_status == 'ok' and right_status == 'ok')
    for item_type, correct_class in groups:
        matched = [row for row in item_rows
                   if row['questionType'] == item_type
                   and row['modes'].get(left_mode, {}).get('status') == 'scored'
                   and row['modes'].get(right_mode, {}).get('status') == 'scored']
        delta = None
        if modes_ok and matched:
            left_correct = sum(row['modes'][left_mode]['classification'] == correct_class
                               for row in matched)
            right_correct = sum(row['modes'][right_mode]['classification'] == correct_class
                                for row in matched)
            delta = round((left_correct - right_correct) * 100 / len(matched), 2)
        group_label = 'presentRecall' if item_type == 'present' else 'absentAbstention'
        comparison[f'{group_label}DeltaPercentagePoints'] = delta
        comparison[f'{group_label}CommonItemCount'] = len(matched) if modes_ok else 0
    return comparison


def _run_command(command, prompt, timeout, cwd, env):
    argv = [part.replace('{prompt}', prompt) for part in command]
    # The built-in Docker command already ends with the prompt. Custom
    # commands may use {prompt}, or may rely on this append.
    if not any('{prompt}' in part for part in command) and (not argv or argv[-1] != prompt):
        argv.append(prompt)
    start = time.monotonic()
    completed = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=timeout, check=False, env=env)
    return completed, time.monotonic() - start


def _make_container_files_removable(workspace, config_dir):
    """Docker writes as a remapped UID; open its nested dirs for host cleanup."""
    image = os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0')
    try:
        subprocess.run([
            'docker', 'run', '--rm', '-v', f'{workspace}:/work',
            '-v', f'{config_dir}:/config', '--entrypoint', 'chmod',
            image, '-R', 'a+rwx', '/work', '/config',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Cleanup must not replace the harness result or its real error.


def _docker_command(mode, command, prompt, model, workspace, config_dir):
    image = os.environ.get('OAKEN_IMAGE', 'oaken-bench:1.0')
    if command is not None:
        return command
    executable = 'pi' if mode == 'pi' else 'dsh'
    args = (['--provider', 'local-llama', '--model', model, '--api-key', 'local',
             '-p', '--mode', 'text'] if mode == 'pi' else ['--profile', 'headless'])
    return [
        'docker', 'run', '--rm', '--add-host=llama:host-gateway',
        '-e', 'LOCAL_LLAMA_KEY=local', '-v', f'{workspace}:/work',
        '-v', f'{config_dir}:/root/{".pi/agent" if mode == "pi" else ".dsh"}',
        '-w', '/work', '--entrypoint', executable, image, *args, prompt,
    ]


def _checked_base_url(base_url):
    from urllib.parse import urlsplit
    if not base_url:
        raise ValueError('an explicit --base-url is required for every mode')
    parsed = urlsplit(base_url)
    if parsed.port == 8080:
        raise ValueError('port 8080 is reserved by another project; choose a separate endpoint')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('base URL must be an absolute http(s) URL')
    return base_url.rstrip('/')


def _container_base_url(base_url):
    """Translate host loopback to Docker's host-gateway alias for pi/dsh."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(base_url)
    if parts.hostname == 'localhost' or parts.hostname == '127.0.0.1' or (
            parts.hostname and parts.hostname.startswith('127.')):
        host = 'llama'
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return base_url


def run_harness(mode, prompt, *, model, base_url, timeout, command=None,
                ledger_text=None, context_window=None):
    base_url = _checked_base_url(base_url)
    if mode == 'direct':
        # Qwen can spend more than 1024 tokens reasoning before its JSON.
        response = direct.chat(
            base_url, model,
            [{'role': 'system', 'content': 'Return the requested JSON only.'},
             {'role': 'user', 'content': prompt}], max_tokens=4096,
            timeout=timeout, api_key=direct.api_key_from_env())
        choice = (response.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        raw = message.get('content')
        answers = parse_answers(raw)
        # Some OpenAI-compatible servers return tool calls even when the prompt
        # requests plain JSON. Accept the same schema used by recall.py.
        if answers is None:
            answers = recall._first_tool_call(message)
        return answers, None

    if mode not in ('pi', 'dsh'):
        raise ValueError(f'unknown mode: {mode}')
    if command is None:
        command = None
    if ledger_text is None:
        raise ValueError('harness mode requires ledger text to write into its workspace')
    with tempfile.TemporaryDirectory(prefix=f'oaken-{mode}-',
                                     ignore_cleanup_errors=True) as cwd:
        container_url = _container_base_url(base_url)
        workspace = os.path.join(cwd, 'workspace')
        os.makedirs(workspace)
        ledger_file = os.path.join(workspace, 'ledger.txt')
        with open(ledger_file, 'w', encoding='utf-8') as f:
            f.write(ledger_text)
        env = os.environ.copy()
        env['HOME'] = cwd
        config_dir = None
        if mode == 'pi':
            config_dir = os.path.join(cwd, '.pi', 'agent')
            os.makedirs(config_dir)
            with open(os.path.join(config_dir, 'models.json'), 'w', encoding='utf-8') as f:
                json.dump({'providers': {'local-llama': {
                    'baseUrl': container_url, 'api': 'openai-completions', 'apiKey': 'local',
                    'models': [{'id': model, 'name': model,
                                'contextWindow': context_window or 131072,
                                'maxTokens': 32768, 'input': ['text']}],
                }}}, f)
        else:
            source = os.path.join(os.path.dirname(__file__), '..', 'docker', 'config', 'dsh')
            target = os.path.join(cwd, '.dsh')
            shutil.copytree(source, target)
            settings_path = os.path.join(target, 'settings.yaml')
            with open(settings_path, encoding='utf-8') as f:
                settings = f.read()
            settings = settings.replace('http://llama:8080/v1', container_url)
            # The model registry already contains Qwen. Replacing the Gemma
            # registry entry too creates duplicate IDs and DSH rejects it.
            lines = settings.splitlines(keepends=True)
            defaults = [i for i, line in enumerate(lines) if line.startswith('  model: ')]
            if len(defaults) != 1:
                raise ValueError('expected one agent-default-model in DSH settings')
            lines[defaults[0]] = f'  model: {model}\n'
            settings = ''.join(lines)
            with open(settings_path, 'w', encoding='utf-8') as f:
                f.write(settings)
            env['LOCAL_LLAMA_KEY'] = 'local'
            config_dir = target
        argv = _docker_command(mode, command, prompt, model, workspace, config_dir)
        # Supplied fake commands run on the host in the same mounted-workspace
        # path so tests exercise file visibility without needing Docker.
        command_cwd = workspace if command is not None else cwd
        try:
            completed, elapsed = _run_command(argv, prompt, timeout, command_cwd, env)
        finally:
            if command is None:
                _make_container_files_removable(workspace, config_dir)
    if completed.returncode:
        raise RuntimeError(f'{mode} exited {completed.returncode}')
    return parse_answers(completed.stdout), elapsed


def run_comparison(depths, seed, model, *, base_url, timeout=180, modes=('direct', 'pi', 'dsh'),
                   pi_command=None, dsh_command=None, count_tokens_fn=None,
                   served_context=None):
    base_url = _checked_base_url(base_url)
    result_depths = []
    all_item_rows = []
    for depth in depths:
        case_set = (build_case_set(depth, seed, count_tokens_fn=count_tokens_fn)
                    if count_tokens_fn is not None else build_case_set(depth, seed))
        direct_prompt = build_prompt(case_set)
        prompt_tokens = None
        if count_tokens_fn is not None:
            try:
                prompt_tokens = count_tokens_fn(direct_prompt)
            except Exception:
                pass
        harness_prompt = build_prompt(case_set, ledger_path='/work/ledger.txt')
        depth_meta = {
            'depthTokens': depth,
            'ledgerTokenCount': case_set['haystack'].token_count,
            'tokenCountSource': case_set['haystack'].token_count_source,
            'directPromptTokenCount': prompt_tokens,
            'servedContextTokens': served_context,
        }
        # The model needs room for the prompt, its answer, and harness framing.
        # An over-window ledger can yield plausible pi/dsh answers after the
        # harness trims context; comparing those with direct would be false.
        if (served_context is not None and prompt_tokens is not None
                and prompt_tokens + 4096 > served_context):
            result_depths.append({
                **depth_meta, 'questionSetDigest': case_set['digest'],
                'validForComparison': False,
                'skipReason': 'direct prompt plus 4096-token reserve exceeds served context',
                'modes': {mode: {'status': 'skipped'} for mode in modes},
                'items': [], 'comparisons': {},
            })
            continue
        mode_results = {}
        classified_by_mode = {}
        for mode in modes:
            command = (shlex.split(pi_command) if mode == 'pi' and pi_command else
                       shlex.split(dsh_command) if mode == 'dsh' and dsh_command else None)
            start = time.monotonic()
            try:
                prompt = direct_prompt if mode == 'direct' else harness_prompt
                answers, elapsed = run_harness(
                    mode, prompt, model=model, base_url=base_url,
                    timeout=timeout, command=command,
                    ledger_text=case_set['haystack'].text if mode in ('pi', 'dsh') else None,
                    context_window=served_context)
                status = 'ok' if answers is not None else 'unparseable'
                entry = {'status': status,
                         'elapsedSeconds': round(elapsed if elapsed is not None else
                                                  time.monotonic() - start, 3)}
                if status == 'ok':
                    entry['score'] = score_answers(case_set, answers)
                    classified_by_mode[mode] = classify_items(case_set, depth, answers)
                mode_results[mode] = entry
            except Exception as exc:  # each adapter failure is an observed outcome
                mode_results[mode] = {'status': 'error', 'errorType': type(exc).__name__}
        item_templates = classify_items(case_set, depth, None)
        item_rows = []
        per_mode = {
            mode: {item['itemId']: item for item in classified_by_mode.get(mode, [])}
            for mode in modes
        }
        for template in item_templates:
            row = {k: template[k] for k in ('itemId', 'questionType', 'kind')}
            row['modes'] = {}
            for mode in modes:
                result = per_mode[mode].get(template['itemId'])
                row['modes'][mode] = ({'status': 'scored',
                                       'classification': result['classification']}
                                      if result else {
                                          'status': mode_results[mode]['status'],
                                          'classification': None,
                                      })
            item_rows.append(row)
        all_item_rows.extend(item_rows)
        comparisons = {}
        if 'direct' in mode_results:
            for mode in modes:
                if mode == 'direct':
                    continue
                comparisons[f'{mode}_vs_direct'] = _compare_item_rows(
                    item_rows, mode, 'direct', mode_results[mode]['status'],
                    mode_results['direct']['status'])
        result_depths.append({
            **depth_meta, 'questionSetDigest': case_set['digest'],
            'validForComparison': True,
            'modes': mode_results, 'items': item_rows, 'comparisons': comparisons,
        })
    effects = {}
    for mode in modes:
        successful = [d['modes'][mode]['score'] for d in result_depths
                      if d['modes'][mode].get('status') == 'ok']
        effects[mode] = {
            'presentCorrect': sum(s['present'][recall.CORRECT_ANSWER] for s in successful),
            'presentTotal': sum(s['present']['total'] for s in successful),
            'absentCorrect': sum(s['absent'][recall.CORRECT_ABSTENTION] for s in successful),
            'absentTotal': sum(s['absent']['total'] for s in successful),
            'depthsScored': len(successful),
        }
        effects[mode]['presentScore'] = _rate(effects[mode]['presentCorrect'],
                                              effects[mode]['presentTotal'])
        effects[mode]['absentScore'] = _rate(effects[mode]['absentCorrect'],
                                             effects[mode]['absentTotal'])
    overall_comparisons = {}
    if 'direct' in modes:
        for mode in modes:
            if mode == 'direct':
                continue
            valid_depths = [d for d in result_depths if d['validForComparison']]
            left_statuses = [d['modes'][mode]['status'] for d in valid_depths]
            right_statuses = [d['modes']['direct']['status'] for d in valid_depths]
            overall_comparisons[f'{mode}_vs_direct'] = _compare_item_rows(
                all_item_rows, mode, 'direct',
                'ok' if all(s == 'ok' for s in left_statuses) else 'partial',
                'ok' if all(s == 'ok' for s in right_statuses) else 'partial',
                allow_partial=True)
    return {
        'schemaVersion': SCHEMA_VERSION, 'model': model, 'seed': seed,
        'modes': list(modes), 'depths': result_depths, 'byMode': effects,
        'harnessEffect': overall_comparisons,
    }


def _rate(correct, total):
    return round(correct / total, 4) if total else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--model', required=True)
    parser.add_argument('--base-url', required=True,
                        help='explicit model endpoint; port 8080 is rejected')
    parser.add_argument('--depths', default='4k,16k,32k,64k,128k')
    parser.add_argument('--seed', type=int, default=20260924)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--modes', default='direct,pi,dsh')
    parser.add_argument('--pi-command')
    parser.add_argument('--dsh-command')
    parser.add_argument('--out', required=True, help='JSON output path')
    args = parser.parse_args(argv)
    args.base_url = _checked_base_url(args.base_url)
    depths = [recall.parse_depth(item) for item in args.depths.split(',')]
    root_url = recall.server_root_url(args.base_url)
    served_context, _ = recall.fetch_served_context_tokens(root_url)
    tokenize_available, _ = recall.probe_tokenize(root_url)
    count_tokens_fn = (lambda text: recall.count_tokens_via_server(root_url, text)) \
        if tokenize_available else None
    report = run_comparison(depths, args.seed, args.model, base_url=args.base_url,
                            timeout=args.timeout,
                            modes=tuple(args.modes.split(',')),
                            pi_command=args.pi_command, dsh_command=args.dsh_command,
                            count_tokens_fn=count_tokens_fn,
                            served_context=served_context)
    report['createdAt'] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report['byMode'], indent=2))
    print(json.dumps(report['harnessEffect'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
