"""CI-only import/context-construction staging; no assertion edits or browser execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('app', type=Path)
PARSER.add_argument('--proof', type=Path)
PARSER.add_argument('--dry-run', action='store_true')
ARGS = PARSER.parse_args()
APP = ARGS.app.resolve(strict=True)
HARNESS = Path(__file__).resolve().parent
TEMPLATE = HARNESS / 'webkit-persistent-fixtures.ts'
E2E = APP / 'tests/e2e'
GENERATED = E2E / 'helpers/webkit-persistent-fixtures.ts'
PROOF = ARGS.proof or HARNESS / 'webkit-persistent-stage-proof.json'

def sha(data):
    return hashlib.sha256(data).hexdigest()

assert (APP / 'node_modules/typescript/package.json').is_file(), 'Use existing installed TypeScript'
assert (E2E / 'playwright.config.ts').is_file()
assert not GENERATED.exists(), 'Generated fixture already exists; use a fresh CI checkout'
assert TEMPLATE.is_file() and not TEMPLATE.is_symlink()
if not ARGS.dry_run:
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Source staging is CI-only; local inspection must use --dry-run'
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    assert APP.is_relative_to(workspace), 'Stage only the CI app checkout'

# TypeScript's public parser finds literal module specifiers (including import types).
# UTF-16 AST positions are converted to UTF-8 byte offsets before replacing anything.
FIND_IMPORTS = r'''
import fs from 'node:fs';
import ts from 'typescript';
const names = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = [];
for (const name of names) {
  const text = fs.readFileSync(name, 'utf8');
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
  const ranges = [];
  const constructionRanges = [];
  const normalized = name.replaceAll('\\', '/');
  const expected = normalized.endsWith('/browser-compatibility.spec.ts') ? 2
    : normalized.endsWith('/offline-content-index.spec.ts')
      || normalized.endsWith('/talk-catalog-session.spec.ts') ? 1 : 0;
  const byte = offset => Buffer.byteLength(text.slice(0, offset));
  function visit(node) {
    if (ts.isStringLiteral(node) && node.text === '@playwright/test') {
      const parent = node.parent;
      const moduleSpecifier = (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent))
        && parent.moduleSpecifier === node;
      const importType = ts.isLiteralTypeNode(parent) && ts.isImportTypeNode(parent.parent)
        && parent.parent.argument === parent;
      const dynamicImport = ts.isCallExpression(parent)
        && parent.expression.kind === ts.SyntaxKind.ImportKeyword && parent.arguments[0] === node;
      if (moduleSpecifier || importType || dynamicImport) {
        ranges.push({start: Buffer.byteLength(text.slice(0, node.getStart(source) + 1)),
          end: Buffer.byteLength(text.slice(0, node.end - 1))});
      }
    }
    if (expected && ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && ts.isIdentifier(node.expression.expression) && node.expression.expression.text === 'browser'
      && node.expression.name.text === 'newContext') {
      constructionRanges.push({start: byte(node.expression.getStart(source)), end: byte(node.expression.end),
        from: 'browser.newContext', to: 'appBrowserContext', kind: 'context-constructor'});
    }
    if (expected && ts.isObjectBindingPattern(node) && node.elements.length === 1
      && ts.isIdentifier(node.elements[0].name) && node.elements[0].name.text === 'browser'
      && !node.elements[0].propertyName && !node.elements[0].dotDotDotToken
      && ts.isParameter(node.parent) && ts.isArrowFunction(node.parent.parent)) {
      constructionRanges.push({start: byte(node.getStart(source)), end: byte(node.end),
        from: '{ browser }', to: '{ appBrowserContext }', kind: 'fixture-parameter'});
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  if (expected && (constructionRanges.filter(r => r.kind === 'context-constructor').length !== expected
    || constructionRanges.filter(r => r.kind === 'fixture-parameter').length !== expected)) {
    throw new Error(`Unexpected explicit context/fixture count: ${name}`);
  }
  result.push({name, ranges: ranges.sort((a, b) => a.start - b.start), constructionRanges});
}
process.stdout.write(JSON.stringify(result));
'''

# Config/global setup retain their original imports; only specs and their helpers change.
targets = sorted(path for path in set(E2E.glob('*.spec.ts')) | set((E2E / 'helpers').glob('*.ts'))
                 | {E2E / 'runtime-helpers.ts', E2E / 'ios-overflow-helpers.ts'} if path != GENERATED)
for path in targets:
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(E2E.resolve())
parsed = json.loads(subprocess.check_output(
    ['node', '--input-type=module', '-e', FIND_IMPORTS], cwd=APP,
    input=json.dumps([str(path) for path in targets]).encode('utf-8')))
changes = []
pending = []
for item in parsed:
    path = Path(item['name'])
    before = path.read_bytes()
    relative = os.path.relpath(GENERATED.with_suffix(''), path.parent).replace(os.sep, '/')
    if not relative.startswith('.'):
        relative = './' + relative
    spans = [{'start': r['start'], 'end': r['end'], 'from': '@playwright/test',
              'to': relative, 'kind': 'module-specifier'} for r in item['ranges']]
    spans.extend(item['constructionRanges'])
    spans.sort(key=lambda span: span['start'])
    after = before
    transformations = []
    for span in reversed(spans):
        start, end = span['start'], span['end']
        assert before[start:end] == span['from'].encode('utf-8')
        after = after[:start] + span['to'].encode('utf-8') + after[end:]
        transformations.append({'startByte': start, 'endByte': end,
                                'from': span['from'], 'to': span['to'], 'kind': span['kind']})
    if not transformations:
        continue
    # Reversal proves that every byte outside these module specifiers is identical.
    rebuilt = bytearray()
    cursor = 0
    unchanged = bytearray()
    for span in spans:
        assert cursor <= span['start'], 'Overlapping transformations'
        rebuilt.extend(before[cursor:span['start']])
        unchanged.extend(before[cursor:span['start']])
        rebuilt.extend(span['to'].encode('utf-8'))
        cursor = span['end']
    rebuilt.extend(before[cursor:])
    unchanged.extend(before[cursor:])
    assert bytes(rebuilt) == after
    changes.append({'path': path.relative_to(APP).as_posix(), 'oldBytes': len(before),
                    'newBytes': len(after), 'oldSha256': sha(before), 'newSha256': sha(after),
                    'onlyAllowedSetupBytesChanged': True, 'outsideAllowedSpansSha256': sha(unchanged),
                    'transformations': list(reversed(transformations))})
    pending.append((path, before, after))
assert changes, 'No Playwright imports were found'
assert sum(t['kind'] == 'context-constructor' for c in changes for t in c['transformations']) == 4
assert sum(t['kind'] == 'fixture-parameter' for c in changes for t in c['transformations']) == 4
template = TEMPLATE.read_bytes()
proof = {'mode': 'dry-run' if ARGS.dry_run else 'CI_STAGED',
         'scope': 'WebKit CI app copy only; Chromium source matrix unchanged',
         'generatedFixture': GENERATED.relative_to(APP).as_posix(),
         'fixtureBytes': len(template), 'fixtureSha256': sha(template),
         'files': len(changes),
         'moduleSpecifiers': sum(t['kind'] == 'module-specifier' for c in changes for t in c['transformations']),
         'explicitContextConstructors': 4, 'explicitFixtureParameters': 4,
         'assertionsUnchanged': True, 'onlyImportsAndContextSetupChanged': True,
         'otherExplicitBrowserNewContextUnchanged': True, 'changes': changes}
if not ARGS.dry_run:
    for path, before, _ in pending:
        assert path.read_bytes() == before, f'Concurrent source change: {path}'
    GENERATED.write_bytes(template)
    for path, _, after in pending:
        path.write_bytes(after)
        assert path.read_bytes() == after
    PROOF.parent.mkdir(parents=True, exist_ok=True)
    PROOF.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
print(json.dumps(proof if ARGS.dry_run else {k: v for k, v in proof.items() if k != 'changes'}))
