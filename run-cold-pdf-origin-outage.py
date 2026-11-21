"""Mac CI-only controller: stop only its origin PGID; reconnect only after cold proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

MANIFEST = '2c91b9b11751a53b9f6ded01aedff82858de6a07335d19789f7ffe786b11bacd'
CORE = '9393fa79e1c67c74edc26b610d65a4f7ed73d345a762465cc88340a33a2454ac'
FIXTURE = '516c34cc0dcdf51bd07dfe72d9dcbf34090bd038f3b178ec16d2fb35bf092b1b'
TITLE = 'current app cold PDF survives owned origin outage and authenticated reconnect'
BOOTSTRAP = '''
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root=process.cwd();
const {startLocalDevRuntime}=await import(pathToFileURL(path.join(root,'tests/e2e/local-dev-runtime.mjs')).href);
await startLocalDevRuntime({config:path.join(root,'tests/e2e/wrangler.sveltekit.jsonc'),
 persistence:process.env.TUTAM_PDF_RUNTIME_PERSISTENCE,port:8787,origin:'http://localhost:8787',
 assets:path.join(root,'dist-sveltekit'),cleanup:()=>{}});
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return int(time.time() * 1000)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def ps_rows():
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,stat='], text=True, timeout=10)
    return [(int(parts[0]), int(parts[1]), parts[2]) for line in output.splitlines()
            if len(parts := line.split()) >= 3]


def members(group):
    return [pid for pid, pgid, status in ps_rows() if pgid == group and not status.startswith('Z')]


def started(pid):
    return subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'lstart='], text=True, timeout=10).strip()


def identity(process, label):
    assert os.getpgid(process.pid) == process.pid and process.pid > 1
    assert process.pid != os.getpgrp()
    return {'label': label, 'pid': process.pid, 'pgid': process.pid, 'started': started(process.pid)}


def alive(process):
    return process is not None and process.poll() is None


def tcp_open(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=.25):
            return True
    except OSError:
        return False


def origin_refused():
    return not tcp_open(8787) and not tcp_open(8787, 'localhost')


def listeners(port):
    result = subprocess.run(['/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode in (0, 1)
    return sorted({int(line) for line in result.stdout.splitlines() if line.strip()})


def stop_group(process, owner, protected_groups):
    group = owner['pgid']
    assert group > 1 and group != os.getpgrp() and group not in protected_groups
    if owner.get('verifiedStopped'):
        return {**owner, 'alreadyStopped': True, 'stopped': True, 'remaining': []}
    if alive(process):
        assert os.getpgid(process.pid) == group
        actual_start = started(process.pid)
        if owner['started'] is None:
            owner['started'] = actual_start
        assert actual_start == owner['started']
    before = members(group)
    if process.pid in before:
        assert started(process.pid) == owner['started'], 'PID was reused; refuse to signal it'
    forced = False
    if before:
        os.killpg(group, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while members(group) and time.monotonic() < deadline:
            time.sleep(.1)
        if members(group):
            forced = True
            os.killpg(group, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while members(group) and time.monotonic() < deadline:
                time.sleep(.1)
    remaining = members(group)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    assert not remaining, f'Owned group remains alive: {group}'
    owner['verifiedStopped'] = True
    return {**owner, 'before': before, 'remaining': remaining, 'forcedKill': forced, 'stopped': True,
            'exitCode': process.poll(), 'completedAt': now()}


def interrupted(signum, _frame):
    raise RuntimeError(f'Controller interrupted by signal {signum}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'darwin'
    assert app == workspace / 'app' and not app.is_symlink()
    assert not tcp_open(8787) and not tcp_open(8799), 'Refuse to replace a pre-existing local listener'
    assert digest((app / 'node_modules/playwright-core/lib/coreBundle.js').read_bytes()) == CORE
    assert digest((app / 'tests/e2e/helpers/webkit-persistent-fixtures.ts').read_bytes()) == FIXTURE
    assert json.loads((app / 'node_modules/@playwright/test/package.json').read_text())['version'] == '1.62.1'
    harness = workspace / 'harness'
    assert digest((harness / 'moderation-brand-overlay.json').read_bytes()) == MANIFEST
    manifest = read_json(harness / 'moderation-brand-overlay.json')
    assert manifest['sourceConcurrencyGuard']['allCountedFilesReread'] == 3292
    assert digest((harness / 'apply-moderation-brand.py').read_bytes()) == 'f83aabd62b835cd91290a19b0d7f494be201866ee7113f9e3b308dba42465fc7'
    assert digest((app / 'qa-evidence/moderation-brand-expected.json').read_bytes()) == '4c9a429cf83f26548bec69ee6c551003ee71d23ad774d044c37588cf8e61ef4f'
    expected = read_json(app / 'qa-evidence/moderation-brand-expected.json')
    assert len(expected) == 3292
    critical = ['package.json', 'package-lock.json', 'src/service-worker.ts', 'src/lib/components/library/ReaderBoundary.svelte',
                'src/lib/components/reader/PdfCanvasReader.svelte', 'src/lib/browser/offline-pdf-database.ts',
                'src/lib/browser/offline-pdf-downloads.ts', 'tests/e2e/runtime-helpers.ts']
    for name in critical:
        data = (app / name).read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == expected[name]
    output = app / 'qa-evidence/cold-pdf-origin-outage'
    output.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    owned = Path(tempfile.mkdtemp(prefix='cold-pdf-origin-', dir=temporary_root)).resolve()
    assert owned.parent == temporary_root and owned.name.startswith('cold-pdf-origin-')
    control = owned / 'control'
    control.mkdir(mode=0o700)
    persistence = owned / 'persistence'
    persistence.mkdir()
    persistence_inode = persistence.stat().st_ino
    nonce = secrets.token_hex(32)
    nonce_hash = digest(nonce.encode())
    persistence_id = digest(str(persistence).encode())
    owner_path = output / 'controller-owner.json'
    write_json(owner_path, {'pid': os.getpid(), 'pgid': os.getpgrp(), 'sourceManifest': MANIFEST,
                           'nonceSha256': nonce_hash, 'temporaryRoot': str(owned), 'persistenceId': persistence_id})
    write_json(output / 'source-provenance.json', {
        'sourceManifest': MANIFEST, 'effectiveSourceFiles': 3292, 'snapshotDeltaFiles': manifest['fileCount'],
        'criticalGitBlobs': {name: expected[name] for name in critical},
        'packageVersion': '1.62.1', 'coreSha256': CORE, 'fixtureSha256': FIXTURE,
        'runtimeBootstrapSha256': digest(BOOTSTRAP.encode()),
        'runtimeHelper': 'tests/e2e/local-dev-runtime.mjs:startLocalDevRuntime',
        'bindings': 'canonical local D1/R2 fixtures only', 'migrationsAndSeedRunsPlanned': 1,
        'outageKind': 'owned_origin_process_group_stopped', 'browserOfflineEmulation': False,
        'equivalentToAirplaneMode': False,
    })
    write_json(control / 'owner.json', {'nonce': nonce, 'controllerPid': os.getpid(), 'sourceManifest': MANIFEST})
    env = os.environ.copy()
    env.update({'SVELTEKIT_BASE_URL': 'http://localhost:8787', 'TUTAM_E2E_PREBUILT': '1',
                'TUTAM_E2E_DEV_API': '1', 'TUTAM_E2E_LOCAL_ONLY': '1', 'TUTAM_E2E_PROXY_PORT': '8799',
                'TUTAM_E2E_RUNTIME_PORT': '8787', 'TUTAM_PDF_RUNTIME_PERSISTENCE': str(persistence),
                'TUTAM_PDF_ORIGIN_CONTROL': str(control), 'TUTAM_PDF_ORIGIN_NONCE': nonce,
                'PLAYWRIGHT_JSON_OUTPUT_FILE': str(output / 'results.json')})
    command = ['node', 'node_modules/@playwright/test/cli.js', 'test',
               '--config=tests/e2e/github-cold-pdf-origin-outage.config.ts', '--workers=1', '--retries=0', '--max-failures=0']
    processes = []
    history = []
    cleanup = []
    runtime = None
    runtime_owner = None
    tests = None
    test_owner = None
    phase = 'PREPARING'
    stop_receipt = None
    requested_sequence = 1
    exit_code = 1
    failure = None
    runtime_count = 0
    seed_count = 0
    migration_count = 0
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)

    def spawn(command, label, log, command_env=env):
        process = subprocess.Popen(command, cwd=app, env=command_env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        record = {'label': label, 'pid': process.pid, 'pgid': process.pid, 'started': None, 'spawnedAt': now()}
        processes.append((process, record))
        record.update(identity(process, label))
        return process, record

    def ready(process):
        attempts = []
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            assert alive(process), 'Origin exited before readiness; no automatic restart'
            attempt = {'at': now()}
            try:
                for label, path in [('health', '/api/health'), ('root', '/')]:
                    req = urllib.request.Request('http://127.0.0.1:8787' + path,
                                                 headers={'Host': 'localhost:8787', 'Cache-Control': 'no-store'})
                    with opener.open(req, timeout=2) as response:
                        body = response.read(262144)
                        attempt[label] = {'status': response.status, 'bytes': len(body), 'type': response.headers.get('content-type')}
                        assert response.status == 200
                        if label == 'root':
                            assert b'<html' in body.lower()
                attempts.append(attempt)
                return {'ready': True, 'attempts': attempts, 'completedAt': now()}
            except Exception as error:
                attempt['error'] = str(error)
                attempts.append(attempt)
                time.sleep(.2)
        write_json(output / 'failed-readiness.json', {'attempts': attempts})
        raise RuntimeError('Owned origin did not become ready within the existing runtime readiness bound')

    try:
        with (output / 'preparation.log').open('w') as log:
            assets = app / 'assets'
            target_assets = app / 'dist-sveltekit/assets'
            assert (app / 'dist-sveltekit/_worker.js').is_file()
            if target_assets.exists():
                assert target_assets.resolve().is_relative_to(app / 'dist-sveltekit') and not target_assets.is_symlink()
                shutil.rmtree(target_assets)
            shutil.copytree(assets, target_assets)
            for arguments in [
                ['migrations', 'apply', 'DB', '--local'],
                ['execute', 'DB', '--local', '--file', 'tests/e2e/fixtures/local-runtime-seed.sql'],
            ]:
                process, record = spawn(['node', 'node_modules/wrangler/bin/wrangler.js', 'd1', *arguments,
                                         '--config', 'tests/e2e/wrangler.sveltekit.jsonc', '--persist-to', str(persistence)],
                                        'local-fixture-preparation', log)
                assert process.wait(timeout=180) == 0
                cleanup.append(stop_group(process, record, set()))
                if arguments[0] == 'execute':
                    seed_count += 1
                else:
                    migration_count += 1
            assert seed_count == migration_count == 1
        listing_env = {**env, 'PLAYWRIGHT_JSON_OUTPUT_FILE': str(output / 'selected.json')}
        with (output / 'selection.log').open('w') as log:
            selection, selection_owner = spawn(command + ['--list', '--reporter=json'], 'test-selection', log, listing_env)
            assert selection.wait(timeout=60) == 0
            cleanup.append(stop_group(selection, selection_owner, set()))
        selected = read_json(output / 'selected.json')
        listed = []

        def collect(suites):
            for suite in suites:
                listed.extend(suite.get('specs', []))
                collect(suite.get('suites', []))

        collect(selected.get('suites', []))
        assert len(listed) == 1 and listed[0]['title'] == TITLE and len(listed[0]['tests']) == 1
        assert not selected.get('errors')
        with (output / 'runtime-initial.log').open('w') as log:
            runtime, runtime_owner = spawn(['node', '--input-type=module', '-e', BOOTSTRAP], 'origin-initial', log)
            runtime_count += 1
            initial_ready = ready(runtime)
        assert set(listeners(8787)).issubset(set(members(runtime_owner['pgid']))) and listeners(8787)
        phase = 'ONLINE'
        write_json(output / 'initial-runtime.json', {'owner': runtime_owner, 'readiness': initial_ready,
                                                   'persistenceId': persistence_id, 'seedCount': seed_count})
        with (output / 'test.log').open('w') as test_log, (output / 'runtime-reconnect.log').open('w') as reconnect_log:
            tests, test_owner = spawn(command + ['--reporter=line,json'], 'test-cli-and-proxy', test_log)
            deadline = time.monotonic() + 360
            while alive(tests):
                assert time.monotonic() < deadline, 'Single-case controller deadline exceeded'
                if phase in ('ONLINE', 'RECONNECTED'):
                    assert alive(runtime), 'Origin exited unexpectedly; automatic restart is forbidden'
                if phase == 'DOWN':
                    assert not members(stop_receipt['pgid']) and origin_refused(), 'Origin restarted before cold proof'
                    assert tcp_open(8799), 'Loopback-only proxy must remain alive during the outage'
                request_path = control / f'request-{requested_sequence}.json'
                if request_path.exists():
                    request = read_json(request_path)
                    assert request['nonce'] == nonce and request['sequence'] == requested_sequence
                    operation = request['operation']
                    ack = {'nonce': nonce, 'sequence': requested_sequence, 'operation': operation,
                           'sourceManifest': MANIFEST, 'persistenceId': persistence_id}
                    try:
                        assert alive(tests) and started(tests.pid) == test_owner['started']
                        assert runtime_owner['pgid'] != test_owner['pgid'] != os.getpgrp()
                        assert listeners(8799) and set(listeners(8799)).issubset(set(members(test_owner['pgid'])))
                        if operation == 'stop':
                            assert requested_sequence == 1 and phase == 'ONLINE'
                            assert listeners(8787) and set(listeners(8787)).issubset(set(members(runtime_owner['pgid'])))
                            stop_receipt = stop_group(runtime, runtime_owner, {test_owner['pgid']})
                            assert not stop_receipt['forcedKill'], 'Controlled outage must preserve a graceful local persistence shutdown'
                            assert origin_refused() and not listeners(8787)
                            assert persistence.is_dir() and persistence.stat().st_ino == persistence_inode
                            phase = 'DOWN'
                            ack.update({'phase': phase, 'status': 'ok', 'runtimePgid': runtime_owner['pgid'],
                                        'testAlive': alive(tests), 'proxyAlive': tcp_open(8799), 'port8787Refused': True,
                                        'persistenceInode': persistence_inode, 'completedAt': now()})
                            write_json(output / 'origin-stopped.json', {**stop_receipt, 'ack': {**ack, 'nonce': None}})
                        elif operation == 'reconnect':
                            assert requested_sequence == 2 and phase == 'DOWN' and origin_refused()
                            assert persistence.is_dir() and persistence.stat().st_ino == persistence_inode
                            proof = read_json(control / 'cold-proof.json')
                            assert proof['nonce'] == nonce and proof['phase'] == 'COLD_VERIFIED' and proof['sourceManifest'] == MANIFEST
                            assert proof['stopSequence'] == 1 and proof['navigation']['status'] == 200 and proof['navigation']['fromServiceWorker'] is True
                            assert proof['health']['failed'] is True
                            assert all(proof[key] is True for key in ['privatePdfRetained', 'progressRetained', 'guestLoginGateVerified', 'fontRetained'])
                            assert stop_receipt['completedAt'] <= proof['navigationStartedAt'] <= proof['navigationCompletedAt'] <= proof['writtenAt'] <= request['requestedAt']
                            assert proof['retained']['status'] == 'complete' and proof['progress']['page'] == 2
                            asset = (app / 'assets/pdfs/bai-viet-mau-gieo-nhan-lanh.pdf').read_bytes()
                            assert proof['retained']['checksum'] == proof['retained']['actualChecksum'] == digest(asset)
                            assert proof['retained']['size'] == proof['retained']['blobSize'] == len(asset)
                            write_json(output / 'cold-proof.json', {**proof, 'nonce': None, 'nonceSha256': nonce_hash})
                            phase = 'RECONNECTING'
                            previous = runtime_owner['pgid']
                            runtime, runtime_owner = spawn(['node', '--input-type=module', '-e', BOOTSTRAP], 'origin-explicit-reconnect', reconnect_log)
                            runtime_count += 1
                            assert runtime_owner['pgid'] != previous
                            readiness = ready(runtime)
                            phase = 'RECONNECTED'
                            ack.update({'phase': phase, 'status': 'ok', 'runtimePgid': runtime_owner['pgid'], 'completedAt': now(),
                                        'readiness': readiness, 'seedCount': seed_count, 'persistenceInode': persistence_inode})
                        else:
                            raise AssertionError('Only stop and proven reconnect are allowed')
                    except Exception as error:
                        ack.update({'status': 'error', 'phase': phase, 'error': f'{type(error).__name__}: {error}', 'completedAt': now()})
                        write_json(control / f'ack-{requested_sequence}.json', ack)
                        raise
                    write_json(control / f'ack-{requested_sequence}.json', ack)
                    history.append({**request, 'nonce': None, 'ack': {**ack, 'nonce': None}})
                    write_json(output / 'control-history.json', {'nonceSha256': nonce_hash, 'history': history})
                    requested_sequence += 1
                time.sleep(.05)
            exit_code = tests.returncode
            assert phase == 'RECONNECTED' and requested_sequence == 3 and runtime_count == 2
            assert exit_code == 0, 'Real app outage case failed; preserve its actual result'
    except BaseException as error:
        failure = f'{type(error).__name__}: {error}'
        (output / 'controller-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        exit_code = 1
    finally:
        errors = []
        for process, owner in reversed(processes):
            try:
                cleanup.append(stop_group(process, owner, set()))
            except BaseException as error:
                errors.append(str(error))
        all_stopped = not errors and all(owner.get('verifiedStopped') for _, owner in processes)
        if all_stopped:
            assert owned.parent == temporary_root and owned.name.startswith('cold-pdf-origin-') and owned.resolve() == owned
            shutil.rmtree(owned)
        else:
            exit_code = 1
        write_json(output / 'controller-final.json', {'allStopped': all_stopped, 'cleanup': cleanup, 'cleanupErrors': errors,
                   'temporaryPersistenceRemoved': all_stopped and not owned.exists(), 'phase': phase, 'runtimeStarts': runtime_count,
                   'seedCount': seed_count, 'migrationCount': migration_count, 'failure': failure, 'testExitCode': tests.poll() if tests else None,
                   'sourceManifest': MANIFEST, 'nonceSha256': nonce_hash, 'persistenceId': persistence_id,
                   'outageKind': 'owned_origin_process_group_stopped', 'browserOfflineEmulation': False,
                   'equivalentToAirplaneMode': False})
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
