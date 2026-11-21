"""Summarize Playwright's existing JSON schema; never execute or retry tests."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

TOOLS = ('giu-gioi', 'bat-quan-trai', 'thanh-tinh', 'le-phat', 'meditation',
         'rosary', 'cong-phu', 'thoi-khoa', 'tiep-tuc-cong-phu')
CDP_TITLES = {
    *(f'{tool} has a canonical catalog link, reloads once and follows Back/Forward' for tool in TOOLS),
    'meditation water preserves session and controls 375 false',
    'meditation water preserves session and controls 375 true',
    'canceling a partial offline download leaves no copy, then retry is complete and readable offline',
}
CHROMIUM_FILES = {'journal-keyboard-overlap.spec.ts', 'personal-journal-zen.spec.ts'}
PROJECTS = {'mobile-390-light', 'mobile-390-dark', 'desktop-1440-light', 'desktop-1440-dark'}


def records(document):
    rows = []

    def visit(suite, entry):
        for spec in suite.get('specs', []):
            for test in spec.get('tests', []):
                row = {'entry': entry.replace('\\', '/'), 'file': spec.get('file', '').replace('\\', '/'),
                       'line': spec.get('line'), 'column': spec.get('column'), 'title': spec.get('title', ''),
                       'project': test.get('projectName', ''), 'outcome': test.get('status'),
                       'expectedStatus': test.get('expectedStatus'), 'annotations': test.get('annotations', []),
                       'results': test.get('results', [])}
                rows.append(row)
        for child in suite.get('suites', []):
            visit(child, entry)

    for suite in document.get('suites', []):
        visit(suite, suite.get('file', ''))
    return rows


def key(row):
    return tuple(row[name] for name in ('entry', 'file', 'line', 'column', 'title', 'project'))


def excluded_reason(row):
    if Path(row['entry']).name in CHROMIUM_FILES:
        return 'Explicit Chromium spec; its WebKit counterpart remains selected'
    if row['title'] in CDP_TITLES:
        return 'Requires Chromium CDP; retained in the original suite'
    return None


def public(row):
    return {name: row[name] for name in ('entry', 'file', 'line', 'title', 'project')}


def project_counts(rows):
    return dict(sorted(Counter(row['project'] for row in rows).items()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('list', 'final'), required=True)
    parser.add_argument('--engine', choices=('original', 'webkit'), required=True)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--expected-original', type=int, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    problems = []

    def read(name):
        try:
            value = json.loads((args.evidence / name).read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('The report is not a JSON object')
            if value.get('errors'):
                problems.append(f'{name}: {len(value["errors"])} reporter/global errors')
            return value
        except (OSError, ValueError) as error:
            problems.append(f'{name}: {error}')
            return {}

    original_doc = read('original-list.json')
    webkit_doc = read('webkit-list.json')
    shard_doc = read('shard-list.json')
    original, webkit, shard = map(records, (original_doc, webkit_doc, shard_doc))
    if len(original) != args.expected_original:
        problems.append(f'Original selection changed: {len(original)} != {args.expected_original}')
    if not 1 <= args.shard <= args.shards:
        problems.append('Invalid shard coordinates')
    for name, document in (('original', original_doc), ('webkit', webkit_doc)):
        projects = {project.get('name') for project in document.get('config', {}).get('projects', [])}
        if projects != PROJECTS:
            problems.append(f'{name}: project ids changed: {sorted(str(item) for item in projects)}')
    selected = original if args.engine == 'original' else webkit
    exclusions = [{**public(row), 'reason': excluded_reason(row)}
                  for row in original if excluded_reason(row)]
    cdp_count = sum(row['title'] in CDP_TITLES for row in original)
    if cdp_count != 12:
        problems.append(f'Expected exactly 12 original CDP-only executions, found {cdp_count}')
    expected_webkit = Counter(key(row) for row in original if not excluded_reason(row))
    actual_webkit = Counter(map(key, webkit))
    if expected_webkit != actual_webkit:
        problems.append(f'WebKit selection differs from declared exclusions: '
                        f'{sum((expected_webkit-actual_webkit).values())} missing, '
                        f'{sum((actual_webkit-expected_webkit).values())} unexpected')
    selected_keys = Counter(map(key, selected))
    shard_keys = Counter(map(key, shard))
    if not shard or shard_keys - selected_keys:
        problems.append('Shard list is empty or contains executions outside the selected suite')
    summary = {
        'engine': args.engine, 'shard': args.shard, 'shards': args.shards,
        'originalMeaning': 'Original config honors explicit per-spec browser engines',
        'webkitMeaning': 'WebKit project default; two Chromium specs and twelve CDP-only cases excluded',
        'originalSelected': len(original), 'originalByProject': project_counts(original),
        'webkitSelected': len(webkit), 'webkitByProject': project_counts(webkit),
        'selectedForEngine': len(selected), 'selectedForShard': len(shard),
        'shardByProject': project_counts(shard), 'webkitExclusions': exclusions,
        'existingConditionalSkipsPolicy': 'Reported with original annotations; never converted to passes',
    }
    if args.mode == 'final':
        report = read('results.json')
        executed = records(report)
        if Counter(map(key, executed)) != shard_keys:
            problems.append(f'Execution/list mismatch: {len(executed)} report rows, {len(shard)} selected rows')
        stats = report.get('stats', {})
        for name in ('expected', 'unexpected', 'flaky', 'skipped'):
            if not isinstance(stats.get(name), int):
                problems.append(f'Missing or invalid result statistic: {name}')
        if stats.get('unexpected', 0) or stats.get('flaky', 0):
            problems.append('Unexpected or flaky results are present')
        if sum(stats.get(name, 0) for name in ('expected', 'unexpected', 'flaky', 'skipped')) != len(executed):
            problems.append('Result totals do not account for every reported execution')
        by_project = {}
        skipped, failed = [], []
        for row in executed:
            project = by_project.setdefault(row['project'], Counter())
            project[row['outcome'] or 'unknown'] += 1
            last = row['results'][-1] if row['results'] else {}
            project['actual_' + str(last.get('status', 'notRun'))] += 1
            if row['outcome'] == 'skipped':
                skipped.append({**public(row), 'annotations': row['annotations'],
                                'expectedStatus': row['expectedStatus']})
            elif row['outcome'] in ('unexpected', 'flaky'):
                failed.append({**public(row), 'outcome': row['outcome'],
                               'lastStatus': last.get('status'), 'error': last.get('error')})
        summary.update({'reportedExecutions': len(executed), 'stats': stats,
                        'resultsByProject': {name: dict(value) for name, value in sorted(by_project.items())},
                        'conditionalSkips': skipped, 'unexpectedOrFlaky': failed,
                        'reporterErrors': report.get('errors', [])})
    summary['problems'] = problems
    summary['gate'] = 'FAILED' if problems else ('SELECTION_VERIFIED' if args.mode == 'list' else 'PASSED')
    output = args.evidence / ('selection-summary.json' if args.mode == 'list' else 'final-summary.json')
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({name: summary[name] for name in ('engine', 'shard', 'gate', 'originalSelected',
          'webkitSelected', 'selectedForShard', 'shardByProject', 'problems')}, ensure_ascii=False, indent=2))
    if args.mode == 'final' and os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as stream:
            stream.write(f'\nUI {args.engine}, shard {args.shard}/{args.shards}: **{summary["gate"]}**\n\n')
            stream.write(f'Original selection: {len(original)}; WebKit selection: {len(webkit)}; '
                         f'this shard: {len(shard)}. Declared WebKit exclusions: {len(exclusions)}.\n\n')
            if 'stats' in summary:
                stream.write('| Expected | Unexpected | Flaky | Skipped |\n| --- | --- | --- | --- |\n')
                stream.write('| ' + ' | '.join(str(summary['stats'].get(name, 'missing'))
                             for name in ('expected', 'unexpected', 'flaky', 'skipped')) + ' |\n')
            for problem in problems:
                stream.write(f'\n- {problem}\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
