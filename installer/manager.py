#!/usr/bin/env python3
"""Install the packaged local stack; PACK_INSTALL_TEST_ROOT isolates acceptance."""
import asyncio, fcntl, json, os, pathlib, plistlib, shutil, socket, subprocess, sys, time, uuid
from urllib.parse import urlsplit

BUNDLE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
TEST = os.environ.get('PACK_INSTALL_TEST_ROOT')
if TEST:
    base = pathlib.Path(TEST).resolve()
    allowed = (HOME / 'commander-work').resolve()
    if base == allowed or allowed not in base.parents: raise SystemExit('Test root must be inside ~/commander-work')
    hub, engine, config = base/'local-hub', base/'local-engine', base/'config.json'
    # Keep test LaunchAgents inside the isolated test root. The test mode
    # must not write plists into the user's real ~/Library/LaunchAgents.
    agents = base/'LaunchAgents'
    prefix = 'com.pack.installer-test'
    hp, ep = 18795, 47822
    app_path = base/'Applications/Pack.app'
    socket_dir = base/'handoff-socket'
else:
    base = HOME/'.pack'
    hub, engine, config = base/'local-hub', base/'local-engine', base/'config.json'
    agents = HOME/'Library/LaunchAgents'
    prefix = 'com.pack.local'
    hp, ep = 8794, 47821
    app_path = pathlib.Path('/Applications/Pack.app')
    socket_dir = base/'l2-ipc'
labels = {kind: prefix+'-'+kind for kind in ('hub','watch','backup','engine')}
# Keep this exact allowlist in sync with build-dmg.sh; the regression test
# checks both lists. Never copy the whole Hub directory (it can hold secrets).
HUB_PROGRAM_FILES = (
    'hub.py', 'room_watch.py', 'backup-db.sh', 'verify-backup.sh',
    'collaboration_policy.py', 'collaboration_store.py',
    'collaboration_dispatch.py', 'collaboration_guard.py', 'collaboration_context.py',
    'collaboration_migrate.py',
)
INSTALLER_MARKER = 'com.cyou.pack.installer.v1'
OWNER_MARKER = '.pack-installer-owner.json'
STATE_PATH = base/'.pack-install-state.json'
LOCK_PATH = base/'.pack-install.lock'
uid = os.getuid()

def ctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

def available(port):
    with socket.socket() as s:
        try: s.bind(('127.0.0.1',port)); return True
        except OSError: return False

def ensure_agent_directory_safe():
    if agents.is_symlink() or (agents.exists() and not agents.is_dir()):
        raise SystemExit(f'LaunchAgents directory is not a real directory; refusing to modify: {agents}')


def ensure_agent_paths_available():
    conflicts = [agents / (label + '.plist') for label in labels.values()
                 if (agents / (label + '.plist')).exists() or (agents / (label + '.plist')).is_symlink()]
    if conflicts:
        paths = ', '.join(str(path) for path in conflicts)
        raise SystemExit(f'Existing LaunchAgent plist found; refusing to overwrite: {paths}')

def path_exists(path):
    return path.exists() or path.is_symlink()


def ensure_base_directory():
    if base.is_symlink():
        raise SystemExit(f'Install base must not be a symlink: {base}')
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not base.is_dir() or base.stat().st_uid != uid:
        raise SystemExit(f'Install base is not a user-owned directory: {base}')
    os.chmod(base, 0o700)


def acquire_install_lock():
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(LOCK_PATH, flags, 0o600)
    try:
        if not os.path.isfile(LOCK_PATH) or os.fstat(fd).st_uid != uid:
            raise SystemExit('Installer lock is not a regular user-owned file.')
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise SystemExit('Another Pack installer operation is active or the lock is unsafe.')


def release_install_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_install_state(strict=True):
    if not path_exists(STATE_PATH):
        return None
    try:
        if STATE_PATH.is_symlink() or not STATE_PATH.is_file():
            raise ValueError('state path is not a regular file')
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        txid = state.get('transaction_id', '')
        if (state.get('schema_version') != 1
                or state.get('hub') != str(hub)
                or state.get('engine') != str(engine)
                or len(txid) != 32
                or any(ch not in '0123456789abcdef' for ch in txid)
                or state.get('phase') not in ('installing', 'failed', 'completed', 'uninstalled')):
            raise ValueError('unsupported or invalid installer state')
        return state
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        if strict:
            raise SystemExit('Pack installer state is unreadable; preserve it and inspect before proceeding.')
        return None


def save_install_state(state):
    temp = base / f'.pack-install-state.{state["transaction_id"]}.tmp'
    if path_exists(temp):
        if temp.is_symlink() or not temp.is_file() or temp.stat().st_uid != uid:
            raise OSError('installer state temporary path is unsafe')
        temp.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, sort_keys=True)
            stream.write(chr(10))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
        try:
            directory_fd = os.open(base, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        try: temp.unlink()
        except OSError: pass
        raise


def write_root_marker(root, role, transaction_id):
    marker = {'schema_version': 1, 'transaction_id': transaction_id,
              'role': role, 'root': str(root)}
    write_private(root / OWNER_MARKER, json.dumps(marker, ensure_ascii=False))


def is_managed_root(root, role):
    try:
        if root.is_symlink() or not root.is_dir():
            return False
        path = root / OWNER_MARKER
        if path.is_symlink() or not path.is_file():
            return False
        marker = json.loads(path.read_text(encoding='utf-8'))
        txid = marker.get('transaction_id', '')
        return (marker.get('schema_version') == 1
                and marker.get('role') == role
                and marker.get('root') == str(root)
                and len(txid) == 32
                and all(ch in '0123456789abcdef' for ch in txid))
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return False

def is_allowed_hub_url(value):
    try:
        url = urlsplit(value)
        if (url.username is not None or url.password is not None
                or url.query or url.fragment or '?' in value or '#' in value
                or url.path != '/mcp'):
            return False
        if url.scheme == 'http':
            return url.hostname in ('127.0.0.1', 'localhost', '::1') and url.port == hp
        if url.scheme == 'https':
            return (bool(url.hostname) and url.hostname.endswith('.pinggy-free.link')
                    and url.port in (None, 443))
        return False
    except (TypeError, ValueError):
        return False


def validate_reinstall_data():
    """Allow an upgrade only into Pack-marked roots with intact credentials."""
    if not is_managed_root(hub, 'hub') or not is_managed_root(engine, 'engine'):
        raise SystemExit('Existing data roots are not verified Pack-owned roots; refusing reinstall.')
    for path in (config, hub/'.dispatcher_token', hub/'.owner_token'):
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f'Reinstall requires the preserved credential/config file: {path}')
    try:
        config_data = json.loads(config.read_text(encoding='utf-8'))
        if (not isinstance(config_data, dict)
                or not isinstance(config_data.get('hubUrl'), str)
                or not is_allowed_hub_url(config_data['hubUrl'])):
            raise ValueError('missing or untrusted hubUrl')
        dispatcher = (hub/'.dispatcher_token').read_text(encoding='utf-8').strip()
        owner = (hub/'.owner_token').read_text(encoding='utf-8').strip()
        if not dispatcher or not owner:
            raise ValueError('empty credential file')
        if config_data.get('token') and config_data['token'] != dispatcher:
            raise ValueError('dispatcher credential mismatch')
        if config_data.get('ownerToken') and config_data['ownerToken'] != owner:
            raise ValueError('owner credential mismatch')
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise SystemExit('Preserved Pack credentials/config are invalid; refusing reinstall.')
    program_paths = (hub/'python', *(hub/name for name in HUB_PROGRAM_FILES),
                     engine/'app', engine/'Electron.app')
    if any(path_exists(path) for path in program_paths):
        raise SystemExit('Pack program files still exist; run uninstall.command before reinstalling.')
    for path in (hub/'attachments', engine/'data', engine/'data'/'local'):
        if path.is_symlink() or (path_exists(path) and not path.is_dir()):
            raise SystemExit(f'Existing Pack data path is unsafe: {path}')
        if path_exists(path) and path.stat().st_uid != uid:
            raise SystemExit(f'Existing Pack data directory is not user-owned: {path}')
    for path in (hub/'pack.db', hub/'pack.db-wal', hub/'pack.db-shm',
                 hub/'pack.db-journal'):
        if path.is_symlink() or (path_exists(path) and not path.is_file()):
            raise SystemExit(f'Existing Pack database path is unsafe: {path}')
        if path_exists(path) and path.stat().st_uid != uid:
            raise SystemExit(f'Existing Pack database file is not user-owned: {path}')
    if path_exists(socket_dir):
        raise SystemExit(f'Existing IPC path must be removed by uninstall before reinstall: {socket_dir}')
    os.chmod(config, 0o600)
    os.chmod(hub/'.dispatcher_token', 0o600)
    os.chmod(hub/'.owner_token', 0o600)


def rollback_managed_agents():
    for label in labels.values():
        plist = agents/(label+'.plist')
        if plist.is_file() and not plist.is_symlink() and is_managed_agent(plist, label):
            service = f'gui/{uid}/{label}'
            try:
                if ctl('print', service, check=False).returncode == 0:
                    if ctl('bootout', service, check=False).returncode != 0:
                        continue
                plist.unlink()
            except OSError:
                # Leave the plist for a later explicit uninstall retry.
                continue


def remove_program_files(root, names):
    if root.is_symlink() or not root.is_dir():
        return
    for name in names:
        path = root/name
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()

def write_agent(kind, args, env, keepalive=False, calendar=False):
    label = labels[kind]
    doc = {'Label':label,'PackInstallerMarker':INSTALLER_MARKER,
           'ProgramArguments':[str(x) for x in args], 'WorkingDirectory':str(hub),
           'EnvironmentVariables':env,'Umask':63,
           'StandardOutPath':str(hub/(kind+'.out.log')),'StandardErrorPath':str(hub/(kind+'.err.log'))}
    if keepalive: doc.update(RunAtLoad=True,KeepAlive=True)
    if calendar: doc['StartCalendarInterval']={'Hour':3,'Minute':15}
    path=agents/(label+'.plist')
    # Exclusive creation closes the check/write race and never truncates an
    # existing LaunchAgent plist, even if another process creates it meanwhile.
    created = False
    bootstrap_attempted = False
    try:
        with path.open('xb') as f:
            created = True
            plistlib.dump(doc, f)
        bootstrap_attempted = True
        ctl('bootstrap', f'gui/{uid}', str(path))
    except BaseException:
        # Remove only the file created by this call; if bootstrap may have
        # loaded it despite reporting an error, retain the ownership record.
        keep_for_recovery = False
        if created and bootstrap_attempted:
            try:
                keep_for_recovery = ctl('print', f'gui/{uid}/{label}', check=False).returncode == 0
            except OSError:
                keep_for_recovery = True
        if created and not keep_for_recovery:
            try: path.unlink()
            except OSError: pass
        raise

async def setup_owner():
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    token=(hub/'.dispatcher_token').read_text().strip()
    async with Client(StreamableHttpTransport(f'http://127.0.0.1:{hp}/mcp',headers={'Authorization':'Bearer '+token})) as c:
        r=await c.call_tool('member_register',{'name':'owner'})
        owner=json.loads(r.content[0].text)['token']
    write_private(hub/'.owner_token',owner)
    write_private(config,json.dumps({'hubUrl':f'http://127.0.0.1:{hp}/mcp','token':token,'ownerToken':owner}))

def validate_hub_payload():
    root = BUNDLE/'hub'
    if root.is_symlink() or not root.is_dir():
        raise SystemExit('Missing or unsafe Hub payload directory.')
    for name in HUB_PROGRAM_FILES:
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f'Missing or unsafe Hub program file: {name}')


def install():
    validate_hub_payload()
    ensure_base_directory()
    lock_fd = acquire_install_lock()
    try:
        previous = load_install_state()
        reinstall_mode = False
        if previous:
            phase = previous['phase']
            if phase in ('installing', 'failed'):
                raise SystemExit('An interrupted Pack installation was found; run uninstall.command to remove Pack program files while preserving data, then inspect before reinstalling.')
            if phase == 'uninstalled':
                if not any(path_exists(path) for path in (hub, engine, config, socket_dir)):
                    STATE_PATH.unlink()
                else:
                    validate_reinstall_data()
                    reinstall_mode = True
            else:
                raise SystemExit(f'Existing Pack installer state ({phase}); refusing to overwrite.')

        if not reinstall_mode:
            for path in (hub, engine, config, socket_dir):
                if path_exists(path):
                    raise SystemExit(f'Existing service data or App config found; refusing to overwrite: {path}')
        if agents.is_symlink() or (path_exists(agents) and not agents.is_dir()):
            raise SystemExit(f'LaunchAgents path must be a real directory: {agents}')
        if not available(hp) or not available(ep):
            raise SystemExit('A required local port is in use; refusing to proceed.')
        for label in labels.values():
            if ctl('print', f'gui/{uid}/{label}', check=False).returncode == 0:
                raise SystemExit('A service label is already in use.')
        ensure_agent_directory_safe()
        ensure_agent_paths_available()

        state = {
            'schema_version': 1,
            'transaction_id': uuid.uuid4().hex,
            'phase': 'installing',
            'hub': str(hub),
            'engine': str(engine),
            'started_at': int(time.time()),
            'updated_at': int(time.time()),
        }
        save_install_state(state)
        previous_umask = os.umask(0o077)
        try:
            # Ownership markers are written immediately after exclusive root
            # creation. They let uninstall recover a transaction interrupted
            # before the first LaunchAgent was installed.
            if not reinstall_mode:
                hub.mkdir(mode=0o700)
                write_root_marker(hub, 'hub', state['transaction_id'])
                engine.mkdir(mode=0o700)
                write_root_marker(engine, 'engine', state['transaction_id'])
            else:
                os.chmod(hub, 0o700)
                os.chmod(engine, 0o700)

            shutil.copytree(BUNDLE/'python', hub/'python', symlinks=True)
            for name in HUB_PROGRAM_FILES:
                shutil.copy2(BUNDLE/'hub'/name, hub/name)
            shutil.copytree(BUNDLE/'engine'/'app', engine/'app', symlinks=True)
            subprocess.run(['/usr/bin/ditto', str(BUNDLE/'engine'/'Electron.app'),
                            str(engine/'Electron.app')], check=True)
            (hub/'attachments').mkdir(mode=0o700, exist_ok=reinstall_mode)
            (engine/'data'/'local').mkdir(parents=True, mode=0o700, exist_ok=reinstall_mode)
            socket_dir.mkdir(mode=0o700)
            write_root_marker(socket_dir, 'socket', state['transaction_id'])
            agents.mkdir(parents=True, exist_ok=True, mode=0o700)
            if agents.is_symlink() or not agents.is_dir():
                raise RuntimeError('LaunchAgents path is not a real directory')

            py = hub/'python/bin/python3'
            common = {'NO_PROXY':'127.0.0.1,localhost',
                      'no_proxy':'127.0.0.1,localhost',
                      'PYTHONUNBUFFERED':'1'}
            write_agent('hub', [py, hub/'hub.py', 'serve'],
                        {**common, 'PACK_HUB_HOST':'127.0.0.1', 'PACK_HUB_PORT':str(hp)}, True)
            for _ in range(120):
                try:
                    with socket.create_connection(('127.0.0.1', hp), timeout=.3):
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Local hub did not start')

            if not reinstall_mode:
                os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'
                asyncio.run(setup_owner())
            write_agent('watch', [py, hub/'room_watch.py'],
                        {**common, 'PACK_WATCH_URL':f'http://127.0.0.1:{hp}/mcp'}, True)
            write_agent('backup', ['/bin/bash', hub/'backup-db.sh'], {}, calendar=True)
            env = {'PACK_ENGINE_ROAMING':str(engine/'data'),
                   'PACK_ENGINE_LOCAL':str(engine/'data/local'),
                   'PACK_ENGINE_PORT':str(ep),
                   'PACK_L2_SOCKET_DIR':str(socket_dir)}
            write_agent('engine', [engine/'Electron.app/Contents/MacOS/Electron',
                                   engine/'app'], env, True)
            for _ in range(120):
                try:
                    with socket.create_connection(('127.0.0.1', ep), timeout=.3):
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Local engine did not start')

            state['phase'] = 'completed'
            state['updated_at'] = int(time.time())
            save_install_state(state)
        except BaseException as exc:
            # Stop only agents positively identified as Pack-owned. Keep
            # databases, tokens, config, attachments and engine data intact.
            try:
                rollback_managed_agents()
            except Exception:
                print('Warning: one or more Pack services could not be rolled back.', file=sys.stderr)
            state['phase'] = 'failed'
            state['updated_at'] = int(time.time())
            state['error_type'] = type(exc).__name__
            try:
                save_install_state(state)
            except Exception:
                print('Warning: could not persist failed installer state.', file=sys.stderr)
            raise
        finally:
            os.umask(previous_umask)

        print(f'Installed local services; hub 127.0.0.1:{hp}, engine 127.0.0.1:{ep}.')
        print('Open Pack to migrate the private local credentials into Keychain.')
    finally:
        release_install_lock(lock_fd)

def is_managed_agent(path, label):
    """Accept current marked plists and exact legacy Pack plists only."""
    try:
        with path.open('rb') as f:
            doc = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    if doc.get('Label') != label or doc.get('WorkingDirectory') != str(hub):
        return False
    # Match the complete command for both marked and legacy plists. The marker
    # is an additional ownership signal, never a substitute for path checks.
    expected = {
        labels['hub']: [str(hub/'python/bin/python3'), str(hub/'hub.py'), 'serve'],
        labels['watch']: [str(hub/'python/bin/python3'), str(hub/'room_watch.py')],
        labels['backup']: ['/bin/bash', str(hub/'backup-db.sh')],
        labels['engine']: [str(engine/'Electron.app/Contents/MacOS/Electron'), str(engine/'app')],
    }
    if doc.get('ProgramArguments') != expected.get(label):
        return False
    return doc.get('PackInstallerMarker') in (None, INSTALLER_MARKER)


def uninstall():
    ensure_base_directory()
    lock_fd = acquire_install_lock()
    try:
        state = load_install_state(strict=False)
        managed_agents = set()
        blocked_agents = set()
        agents_safe = not agents.is_symlink() and (not path_exists(agents) or agents.is_dir())
        if not agents_safe:
            blocked_agents.update(labels.keys())
        for kind, label in (labels.items() if agents_safe else ()):
            plist = agents/(label+'.plist')
            service = f'gui/{uid}/{label}'
            if plist.is_file() and not plist.is_symlink() and is_managed_agent(plist, label):
                if ctl('print', service, check=False).returncode == 0:
                    if ctl('bootout', service, check=False).returncode != 0:
                        blocked_agents.add(kind)
                        continue
                plist.unlink()
                managed_agents.add(kind)
            elif ctl('print', service, check=False).returncode == 0:
                # A colliding but unverified loaded label must not be disrupted
                # by deleting files from underneath it.
                blocked_agents.add(kind)

        hub_owned = (not hub.is_symlink()
                     and (is_managed_root(hub, 'hub')
                          or bool(managed_agents.intersection({'hub', 'watch', 'backup'}))))
        engine_owned = (not engine.is_symlink()
                        and (is_managed_root(engine, 'engine') or 'engine' in managed_agents))
        if blocked_agents.intersection({'hub', 'watch', 'backup'}):
            hub_owned = False
        if 'engine' in blocked_agents:
            engine_owned = False

        if hub_owned:
            remove_program_files(hub, ('python', *HUB_PROGRAM_FILES))
        if engine_owned:
            remove_program_files(engine, ('app', 'Electron.app'))
        if ('engine' not in blocked_agents
                and is_managed_root(socket_dir, 'socket') and not socket_dir.is_symlink()):
            shutil.rmtree(socket_dir)

        if not hub_owned and not engine_owned:
            print('No Pack-owned program root found; preserving local program files.')

        if app_path.is_dir() and not app_path.is_symlink():
            try:
                with (app_path/'Contents/Info.plist').open('rb') as f:
                    info = plistlib.load(f)
                if info.get('PackInstallerMarker') == INSTALLER_MARKER:
                    shutil.rmtree(app_path)
                    print('Installed Pack App removed.')
                else:
                    print('App not removed: installer marker missing.')
            except (OSError, plistlib.InvalidFileException, ValueError):
                print('App not removed: could not verify installer marker.')

        if state:
            state['phase'] = 'uninstalled'
            state['updated_at'] = int(time.time())
            try:
                save_install_state(state)
            except OSError:
                print('Warning: could not update installer state after uninstall.', file=sys.stderr)
        print(f'Data preserved: {hub}, {engine}/data, {config}')
    finally:
        release_install_lock(lock_fd)

if __name__ == '__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('install','uninstall'): raise SystemExit('Use install or uninstall')
    try: install() if sys.argv[1]=='install' else uninstall()
    except Exception as exc: raise SystemExit(f'Operation failed: {type(exc).__name__}: {exc}')
