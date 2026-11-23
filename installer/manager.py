#!/usr/bin/env python3
"""Install the packaged local stack; PACK_INSTALL_TEST_ROOT isolates acceptance."""
import asyncio, json, os, pathlib, plistlib, shutil, socket, subprocess, sys, tempfile, time

BUNDLE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
TEST = os.environ.get('PACK_INSTALL_TEST_ROOT')
if TEST:
    base = pathlib.Path(TEST).resolve()
    allowed = (HOME / 'commander-work').resolve()
    if base == allowed or allowed not in base.parents: raise SystemExit('Test root must be inside ~/commander-work')
    hub, engine, config = base/'local-hub', base/'local-engine', base/'config.json'
    agents = HOME/'Library/LaunchAgents'
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
uid = os.getuid()

def ctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def require_real_directory(path, description):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise SystemExit(f'Refusing symlinked {description}: {path}')
    if path.exists() and not path.is_dir():
        raise SystemExit(f'{description} is not a directory: {path}')


def require_absent(path, description):
    path = pathlib.Path(path)
    if path.exists() or path.is_symlink():
        raise SystemExit(f'Existing {description}; refusing to overwrite: {path}')


def read_owned_agent_plist(kind, path):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise SystemExit(f'Refusing symlinked LaunchAgent plist: {path}')
    if not path.exists():
        return None
    if not path.is_file():
        raise SystemExit(f'LaunchAgent plist is not a regular file: {path}')
    try:
        with path.open('rb') as stream:
            document = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise SystemExit(f'Cannot validate LaunchAgent plist: {path}') from exc
    if not isinstance(document, dict) or document.get('Label') != labels[kind]:
        raise SystemExit(f'LaunchAgent label does not belong to this installer: {path}')
    expected_arguments = {
        'hub': [str(hub/'python/bin/python3'), str(hub/'hub.py'), 'serve'],
        'watch': [str(hub/'python/bin/python3'), str(hub/'room_watch.py')],
        'backup': ['/bin/bash', str(hub/'backup-db.sh')],
        'engine': [str(engine/'Electron.app/Contents/MacOS/Electron'), str(engine/'app')],
    }[kind]
    if document.get('ProgramArguments') != expected_arguments:
        raise SystemExit(f'LaunchAgent program path is outside the Pack service directory or does not match its entry point: {path}')
    return document

def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f: f.write(content)

def available(port):
    with socket.socket() as s:
        try: s.bind(('127.0.0.1',port)); return True
        except OSError: return False

def write_agent(kind, args, env, keepalive=False, calendar=False):
    label = labels[kind]
    doc = {'Label':label,'ProgramArguments':[str(x) for x in args], 'WorkingDirectory':str(hub),
           'EnvironmentVariables':env,'Umask':63,
           'StandardOutPath':str(hub/(kind+'.out.log')),'StandardErrorPath':str(hub/(kind+'.err.log'))}
    if keepalive: doc.update(RunAtLoad=True,KeepAlive=True)
    if calendar: doc['StartCalendarInterval']={'Hour':3,'Minute':15}
    path=agents/(label+'.plist')
    with path.open('wb') as f: plistlib.dump(doc,f)
    ctl('bootstrap',f'gui/{uid}',str(path))

async def setup_owner():
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    token=(hub/'.dispatcher_token').read_text().strip()
    async with Client(StreamableHttpTransport(f'http://127.0.0.1:{hp}/mcp',headers={'Authorization':'Bearer '+token})) as c:
        r=await c.call_tool('member_register',{'name':'owner'})
        owner=json.loads(r.content[0].text)['token']
    write_private(hub/'.owner_token',owner)
    write_private(config,json.dumps({'hubUrl':f'http://127.0.0.1:{hp}/mcp','token':token,'ownerToken':owner}))


APP_INSTALLER_MARKER = "com.cyou.pack.installer.v1"
HUB_PROGRAM_DIRS = ("python", "core", "services")
HUB_PROGRAM_FILES = ("hub.py", "room_watch.py", "backup-db.sh", "verify-backup.sh")
ENGINE_PROGRAM_DIRS = ("app", "Electron.app")


def require_regular_file(path, description):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise SystemExit(f"Refusing symlinked {description}: {path}")
    if not path.is_file():
        raise SystemExit(f"Missing or invalid {description}: {path}")


def remove_path(path):
    path = pathlib.Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_path(source, destination):
    source = pathlib.Path(source)
    destination = pathlib.Path(destination)
    if source.is_symlink():
        raise SystemExit(f"Refusing symlinked program path: {source}")
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, destination)
    else:
        raise SystemExit(f"Missing upgrade payload: {source}")


def read_pack_app_info(path):
    path = pathlib.Path(path)
    require_real_directory(path, "Pack App bundle")
    info_path = path / "Contents" / "Info.plist"
    require_regular_file(info_path, "Pack App Info.plist")
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise SystemExit(f"Cannot validate Pack App bundle: {path}") from exc
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != "com.cyou.pack":
        raise SystemExit(f"Pack App bundle identifier mismatch: {path}")
    if info.get("PackInstallerMarker") != APP_INSTALLER_MARKER:
        raise SystemExit(f"Pack App installer marker missing: {path}")
    if not isinstance(info.get("CFBundleExecutable"), str) or not info["CFBundleExecutable"]:
        raise SystemExit(f"Pack App executable metadata missing: {path}")
    return info


def require_pack_app_closed(info):
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", info["CFBundleExecutable"]],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise SystemExit("Cannot verify whether Pack is running; quit Pack and retry.") from exc
    if result.returncode == 0:
        raise SystemExit("Pack is running; quit Pack.app before upgrading.")
    if result.returncode != 1:
        raise SystemExit("Cannot verify whether Pack is running; refusing to upgrade.")


def validate_upgrade_installation():
    require_real_directory(base, "Pack data root")
    require_real_directory(HOME / "Library", "Library directory")
    require_real_directory(agents, "LaunchAgents directory")
    require_real_directory(hub, "local Hub directory")
    require_real_directory(engine, "local Engine directory")
    require_real_directory(socket_dir, "handoff socket directory")
    require_real_directory(hub / "attachments", "Hub attachments directory")
    require_real_directory(engine / "data", "Engine data directory")
    require_regular_file(config, "App config")
    require_regular_file(hub / "pack.db", "Hub database")
    require_regular_file(hub / ".dispatcher_token", "Hub dispatcher token")
    require_regular_file(hub / "backup-db.sh", "installed database backup script")
    require_regular_file(hub / "verify-backup.sh", "installed backup verification script")
    try:
        config_value = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("App config is unreadable or invalid; refusing to upgrade.") from exc
    if not isinstance(config_value, dict):
        raise SystemExit("App config root must be an object; refusing to upgrade.")

    for name in HUB_PROGRAM_DIRS:
        require_real_directory(hub / name, f"installed Hub program directory {name}")
    for name in HUB_PROGRAM_FILES:
        require_regular_file(hub / name, f"installed Hub program file {name}")
    for name in ENGINE_PROGRAM_DIRS:
        require_real_directory(engine / name, f"installed Engine program directory {name}")

    for kind, label in labels.items():
        document = read_owned_agent_plist(kind, agents / (label + ".plist"))
        if document is None:
            raise SystemExit(f"Missing Pack LaunchAgent plist; refusing partial upgrade: {label}")
        if document.get("Umask") != 0o077:
            raise SystemExit(f"LaunchAgent umask is not private; refusing upgrade: {label}")

    for name in HUB_PROGRAM_DIRS:
        require_real_directory(BUNDLE / "python" if name == "python" else BUNDLE / "hub" / name,
                               f"bundled Hub program directory {name}")
    for name in HUB_PROGRAM_FILES:
        require_regular_file(BUNDLE / "hub" / name, f"bundled Hub program file {name}")
    for name in ENGINE_PROGRAM_DIRS:
        require_real_directory(BUNDLE / "engine" / name, f"bundled Engine program directory {name}")
    require_real_directory(BUNDLE / "Pack.app", "bundled Pack App")
    require_real_directory(app_path.parent, "Applications directory")

    installed_info = read_pack_app_info(app_path)
    bundled_info = read_pack_app_info(BUNDLE / "Pack.app")
    if installed_info["CFBundleIdentifier"] != bundled_info["CFBundleIdentifier"]:
        raise SystemExit("Installed and bundled App identities differ; refusing upgrade.")
    require_pack_app_closed(installed_info)


def create_upgrade_backup():
    backup_parent = hub / "backups"
    if backup_parent.is_symlink():
        raise SystemExit(f"Refusing symlinked backup directory: {backup_parent}")
    if backup_parent.exists() and not backup_parent.is_dir():
        raise SystemExit(f"Backup path is not a directory: {backup_parent}")
    backup_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_parent.chmod(0o700)
    backup_root = pathlib.Path(tempfile.mkdtemp(prefix="upgrade-", dir=str(backup_parent)))
    backup_root.chmod(0o700)
    return backup_root


def populate_upgrade_backup(backup_root):
    database_backup = backup_root / "database"
    database_backup.mkdir(mode=0o700)
    env = {
        **os.environ,
        "PACK_HUB_DIR": str(hub),
        "PACK_BACKUP_DIR": str(database_backup),
        "PACK_BACKUP_KEEP": "1",
    }
    subprocess.run(["/bin/bash", str(hub / "backup-db.sh")], cwd=str(hub), env=env, check=True)
    subprocess.run(["/bin/bash", str(hub / "verify-backup.sh")], cwd=str(hub), env=env, check=True)

    state_root = backup_root / "state"
    state_root.mkdir(mode=0o700)
    for source in (config, hub / ".dispatcher_token", hub / ".owner_token"):
        if source.exists() or source.is_symlink():
            copy_path(source, state_root / source.name)
            (state_root / source.name).chmod(0o600)

    code_root = backup_root / "code"
    hub_snapshot = code_root / "hub"
    engine_snapshot = code_root / "engine"
    hub_snapshot.mkdir(parents=True, mode=0o700)
    engine_snapshot.mkdir(parents=True, mode=0o700)
    for name in (*HUB_PROGRAM_DIRS, *HUB_PROGRAM_FILES):
        copy_path(hub / name, hub_snapshot / name)
    for name in ENGINE_PROGRAM_DIRS:
        copy_path(engine / name, engine_snapshot / name)

    agents_snapshot = state_root / "LaunchAgents"
    agents_snapshot.mkdir(mode=0o700)
    for label in labels.values():
        copy_path(agents / (label + ".plist"), agents_snapshot / (label + ".plist"))
        (agents_snapshot / (label + ".plist")).chmod(0o600)

    subprocess.run(["/usr/bin/ditto", str(app_path), str(backup_root / "Pack.app")], check=True)


def stage_upgrade_payload():
    stage_root = pathlib.Path(tempfile.mkdtemp(prefix=".pack-upgrade-", dir=str(base)))
    stage_root.chmod(0o700)
    app_stage = None
    try:
        stage_hub = stage_root / "hub"
        stage_engine = stage_root / "engine"
        stage_hub.mkdir(mode=0o700)
        stage_engine.mkdir(mode=0o700)

        copy_path(BUNDLE / "python", stage_hub / "python")
        for name in HUB_PROGRAM_FILES:
            copy_path(BUNDLE / "hub" / name, stage_hub / name)
        for name in ("core", "services"):
            copy_path(BUNDLE / "hub" / name, stage_hub / name)
        copy_path(BUNDLE / "engine" / "app", stage_engine / "app")
        subprocess.run([
            "/usr/bin/ditto",
            str(BUNDLE / "engine" / "Electron.app"),
            str(stage_engine / "Electron.app"),
        ], check=True)

        smoke = (
            "import asyncio,inspect,hub; tools=hub.build_server().list_tools(); "
            "tools=asyncio.run(tools) if inspect.isawaitable(tools) else tools; "
            "names={tool.name for tool in tools}; "
            "assert {'room_list','member_list','task_audit'}.issubset(names), names"
        )
        subprocess.run(
            [str(stage_hub / "python" / "bin" / "python3"), "-c", smoke],
            cwd=str(stage_hub),
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
            check=True,
        )

        app_stage = app_path.parent / f".Pack.app.upgrade-{time.time_ns()}"
        subprocess.run(["/usr/bin/ditto", str(BUNDLE / "Pack.app"), str(app_stage)], check=True)
        read_pack_app_info(app_stage)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app_stage)], check=True)
        subprocess.run(["plutil", "-lint", str(app_stage / "Contents" / "Info.plist")],
                       check=True, stdout=subprocess.DEVNULL)
        return stage_root, stage_hub, stage_engine, app_stage
    except Exception:
        if app_stage is not None:
            try:
                remove_path(app_stage)
            except OSError:
                pass
        try:
            remove_path(stage_root)
        except OSError:
            pass
        raise


def rollback_program_paths(swaps):
    errors = []
    for target, previous in reversed(swaps):
        try:
            remove_path(target)
            if previous.exists() or previous.is_symlink():
                os.replace(previous, target)
        except Exception as exc:
            errors.append(f"{target}: {type(exc).__name__}: {exc}")
    return errors


def replace_program_paths(stage_root, stage_hub, stage_engine, swaps):
    """Swap staged program paths and record each mutation for caller-owned rollback."""
    previous_root = stage_root / "previous"
    previous_root.mkdir(mode=0o700)
    specs = []
    for name in (*HUB_PROGRAM_DIRS, *HUB_PROGRAM_FILES):
        specs.append((hub / name, stage_hub / name, previous_root / "hub" / name))
    for name in ENGINE_PROGRAM_DIRS:
        specs.append((engine / name, stage_engine / name, previous_root / "engine" / name))

    for target, staged, previous in specs:
        previous.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists() or target.is_symlink():
            os.replace(target, previous)
        # Record before installing the staged path: if the next rename fails,
        # the outer transaction still knows where the original path lives.
        swaps.append((target, previous))
        os.replace(staged, target)


def swap_app_bundle(app_stage, previous_paths):
    previous = app_path.parent / f".Pack.app.previous-{time.time_ns()}"
    if previous.exists() or previous.is_symlink():
        raise RuntimeError(f"App rollback path already exists: {previous}")
    os.replace(app_path, previous)
    # Record the moved bundle before the replacement rename so the caller can
    # recover it even when the second rename itself fails.
    previous_paths.append(previous)
    os.replace(app_stage, app_path)
    return previous


def rollback_app_bundle(previous):
    if previous is None or not (previous.exists() or previous.is_symlink()):
        return []
    try:
        remove_path(app_path)
        os.replace(previous, app_path)
        return []
    except Exception as exc:
        return [f"{app_path}: {type(exc).__name__}: {exc}"]



def restore_database_snapshot(backup_root):
    database_dir = pathlib.Path(backup_root) / "database"
    snapshots = sorted(database_dir.glob("pack-*.db"))
    if len(snapshots) != 1:
        raise RuntimeError(f"Expected one verified database snapshot in {database_dir}; found {len(snapshots)}")
    snapshot = snapshots[0]
    require_regular_file(snapshot, "verified upgrade database snapshot")

    state_root = pathlib.Path(backup_root) / "state"
    require_real_directory(state_root, "upgrade rollback state directory")
    live_db = hub / "pack.db"
    require_regular_file(live_db, "live Hub database during rollback")

    stamp = time.time_ns()
    failed_db = state_root / f"pack.db.failed-{stamp}"
    temporary_db = hub / f".pack.db.rollback-{stamp}"
    sidecars = []
    for suffix in ("-wal", "-shm"):
        sidecar = pathlib.Path(str(live_db) + suffix)
        if sidecar.is_symlink():
            raise RuntimeError(f"Refusing symlinked SQLite sidecar during rollback: {sidecar}")
        if sidecar.exists():
            if not sidecar.is_file():
                raise RuntimeError(f"Invalid SQLite sidecar during rollback: {sidecar}")
            sidecars.append((sidecar, state_root / f"{sidecar.name}.failed-{stamp}"))

    shutil.copy2(snapshot, temporary_db)
    temporary_db.chmod(0o600)
    moved_db = False
    moved_sidecars = []
    try:
        os.replace(live_db, failed_db)
        moved_db = True
        for source, destination in sidecars:
            os.replace(source, destination)
            moved_sidecars.append((source, destination))
        os.replace(temporary_db, live_db)
        temporary_db = None
    except Exception:
        if live_db.exists() or live_db.is_symlink():
            remove_path(live_db)
        if moved_db and failed_db.exists():
            os.replace(failed_db, live_db)
        for source, destination in reversed(moved_sidecars):
            if destination.exists() or destination.is_symlink():
                os.replace(destination, source)
        raise
    finally:
        if temporary_db is not None and (temporary_db.exists() or temporary_db.is_symlink()):
            remove_path(temporary_db)
    return failed_db


def wait_for_port(port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Pack service did not become ready on 127.0.0.1:{port}")


def wait_for_hub_ready(timeout=30):
    """Require an authenticated, read-only MCP call; a listening port alone is insufficient."""
    script = f"""
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

async def main():
    token = open(".dispatcher_token", encoding="utf-8").read().strip()
    url = "http://127.0.0.1:{hp}/mcp"
    deadline = asyncio.get_running_loop().time() + {timeout}
    last_error = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with Client(StreamableHttpTransport(
                url, headers={{"Authorization": "Bearer " + token}}
            )) as client:
                result = await client.call_tool("room_list", {{}})
                if getattr(result, "is_error", False):
                    raise RuntimeError("Hub room_list returned an error")
                return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    raise RuntimeError("Hub authenticated MCP readiness check failed: " + str(last_error))

asyncio.run(main())
"""
    subprocess.run(
        [str(hub / "python" / "bin" / "python3"), "-c", script],
        cwd=str(hub),
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
        check=True,
        timeout=timeout + 10,
    )


def bootstrap_upgrade_agents():
    for kind in ("hub", "watch", "backup", "engine"):
        label = labels[kind]
        target = f"gui/{uid}/{label}"
        if ctl("print", target, check=False).returncode != 0:
            ctl("bootstrap", f"gui/{uid}", str(agents / (label + ".plist")))
    wait_for_hub_ready()
    wait_for_port(ep)


def upgrade():
    previous_umask = os.umask(0o077)
    backup_root = None
    stage_root = None
    app_stage = None
    app_previous = None
    app_swap_state = []
    swaps = []
    services_stopped = False
    backup_verified = False
    new_services_started = False
    try:
        validate_upgrade_installation()
        # Stage and smoke-test the new payload before taking services offline.
        # The backup is created only after every Pack writer has stopped, so it
        # includes writes accepted while staging and cannot rewind them on abort.
        stage_root, stage_hub, stage_engine, app_stage = stage_upgrade_payload()

        services_stopped = True
        for label in labels.values():
            ctl("bootout", f"gui/{uid}/{label}", check=False)
        for label in labels.values():
            if ctl("print", f"gui/{uid}/{label}", check=False).returncode == 0:
                raise RuntimeError(f"Pack LaunchAgent did not stop: {label}")
        if not available(hp) or not available(ep):
            raise RuntimeError("A Pack service port is still occupied after stopping LaunchAgents.")

        backup_root = create_upgrade_backup()
        populate_upgrade_backup(backup_root)
        backup_verified = True

        replace_program_paths(stage_root, stage_hub, stage_engine, swaps)
        app_previous = swap_app_bundle(app_stage, app_swap_state)
        app_stage = None
        new_services_started = True
        bootstrap_upgrade_agents()

        try:
            remove_path(app_previous)
        except OSError:
            print(f"Warning: previous App bundle retained at {app_previous}")
        app_previous = None
        try:
            remove_path(stage_root)
        except OSError:
            print(f"Warning: upgrade staging retained at {stage_root}")
        stage_root = None
        print(f"UPGRADE OK: Pack program files updated; user data preserved. Backup: {backup_root}")
    except Exception as exc:
        rollback_errors = []
        if services_stopped:
            for label in labels.values():
                try:
                    ctl("bootout", f"gui/{uid}/{label}", check=False)
                except Exception as stop_error:
                    rollback_errors.append(f"stop {label}: {stop_error}")
            if app_previous is None and app_swap_state:
                app_previous = app_swap_state[-1]
            rollback_errors.extend(rollback_app_bundle(app_previous))
            rollback_errors.extend(rollback_program_paths(swaps))
            if backup_root is not None and new_services_started:
                try:
                    restore_database_snapshot(backup_root)
                except Exception as database_error:
                    rollback_errors.append(f"restore database snapshot: {database_error}")
            if not rollback_errors:
                try:
                    bootstrap_upgrade_agents()
                except Exception as restart_error:
                    rollback_errors.append(f"restart previous services: {restart_error}")
        if app_stage is not None:
            try:
                remove_path(app_stage)
            except Exception as cleanup_error:
                rollback_errors.append(f"remove staged App: {cleanup_error}")
        if stage_root is not None and not rollback_errors:
            try:
                remove_path(stage_root)
            except Exception as cleanup_error:
                rollback_errors.append(f"remove staging: {cleanup_error}")
        if rollback_errors:
            raise RuntimeError(
                f"Upgrade failed and rollback needs attention. Backup: {backup_root}; "
                f"staging: {stage_root}; details: {'; '.join(rollback_errors)}"
            ) from exc
        if backup_root is not None and not backup_verified:
            raise RuntimeError(
                f"Upgrade aborted before program replacement; previous services were restarted and existing data was left unchanged. "
                f"Partial backup retained at {backup_root}: {type(exc).__name__}: {exc}"
            ) from exc
        if backup_root is not None and not new_services_started:
            raise RuntimeError(
                f"Upgrade failed before new services started; previous program files were restored and live database was left untouched. "
                f"Verified backup retained at {backup_root}: {type(exc).__name__}: {exc}"
            ) from exc
        if backup_root is not None:
            raise RuntimeError(
                f"Upgrade failed; previous program files and database snapshot were restored. "
                f"Verified backup retained at {backup_root}: {type(exc).__name__}: {exc}"
            ) from exc
        raise
    finally:
        os.umask(previous_umask)


def install():
    require_real_directory(base, 'Pack data root')
    require_real_directory(HOME/'Library', 'Library directory')
    require_real_directory(agents, 'LaunchAgents directory')
    for path, description in (
        (hub, 'local Hub directory'),
        (engine, 'local Engine directory'),
        (config, 'App config'),
        (socket_dir, 'handoff socket directory'),
    ):
        require_absent(path, description)
    if not available(hp) or not available(ep): raise SystemExit('A required local port is in use; refusing to proceed.')
    for kind, label in labels.items():
        require_absent(agents/(label+'.plist'), f'LaunchAgent plist {label}')
        if ctl('print',f'gui/{uid}/{label}',check=False).returncode == 0:
            raise SystemExit(f'LaunchAgent label is already in use: {label}')
    os.umask(0o077)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    hub.mkdir(mode=0o700)
    engine.mkdir(mode=0o700)
    agents.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLE/'python',hub/'python',symlinks=True)
    for name in ('hub.py','room_watch.py','backup-db.sh','verify-backup.sh'): shutil.copy2(BUNDLE/'hub'/name,hub/name)
    shutil.copytree(BUNDLE/'hub'/'core',hub/'core',symlinks=True)
    shutil.copytree(BUNDLE/'hub'/'services',hub/'services',symlinks=True)
    shutil.copytree(BUNDLE/'engine'/'app',engine/'app',symlinks=True)
    subprocess.run(['/usr/bin/ditto',str(BUNDLE/'engine'/'Electron.app'),str(engine/'Electron.app')],check=True)
    (hub/'attachments').mkdir(mode=0o700); (engine/'data'/'local').mkdir(parents=True,mode=0o700); socket_dir.mkdir(parents=True,mode=0o700)
    py=hub/'python/bin/python3'; common={'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost','PYTHONUNBUFFERED':'1'}
    smoke = (
        'import asyncio,inspect,hub; tools=hub.build_server().list_tools(); '
        'tools=asyncio.run(tools) if inspect.isawaitable(tools) else tools; '
        'names={tool.name for tool in tools}; '
        'assert {"room_list","member_list"}.issubset(names), names'
    )
    subprocess.run([str(py), '-c', smoke], cwd=hub, check=True,
                   env={**os.environ, 'PYTHONNOUSERSITE':'1'})
    try:
        write_agent('hub',[py,hub/'hub.py','serve'],{**common,'PACK_HUB_HOST':'127.0.0.1','PACK_HUB_PORT':str(hp)},True)
        for _ in range(120):
            try:
                with socket.create_connection(('127.0.0.1',hp),timeout=.3): break
            except OSError: time.sleep(.25)
        else: raise RuntimeError('Local hub did not start')
        os.environ['NO_PROXY']=os.environ['no_proxy']='127.0.0.1,localhost'
        asyncio.run(setup_owner())
        write_agent('watch',[py,hub/'room_watch.py'],{**common,'PACK_WATCH_URL':f'http://127.0.0.1:{hp}/mcp'},True)
        write_agent('backup',['/bin/bash',hub/'backup-db.sh'],{},calendar=True)
        env={'PACK_ENGINE_ROAMING':str(engine/'data'),'PACK_ENGINE_LOCAL':str(engine/'data/local'),
             'PACK_ENGINE_PORT':str(ep),'PACK_L2_SOCKET_DIR':str(socket_dir)}
        write_agent('engine',[engine/'Electron.app/Contents/MacOS/Electron',engine/'app'],env,True)
        for _ in range(120):
            try:
                with socket.create_connection(('127.0.0.1',ep),timeout=.3): break
            except OSError: time.sleep(.25)
        else: raise RuntimeError('Local engine did not start')
    except Exception:
        for label in labels.values(): ctl('bootout',f'gui/{uid}/{label}',check=False)
        raise
    print(f'Installed local services; hub 127.0.0.1:{hp}, engine 127.0.0.1:{ep}.')
    print('Open Pack to migrate the private local credentials into Keychain.')

def uninstall():
    require_real_directory(base, 'Pack data root')
    require_real_directory(HOME/'Library', 'Library directory')
    require_real_directory(agents, 'LaunchAgents directory')
    require_real_directory(hub, 'local Hub directory')
    require_real_directory(engine, 'local Engine directory')

    owned_plists = {}
    for kind, label in labels.items():
        plist = agents/(label+'.plist')
        document = read_owned_agent_plist(kind, plist)
        loaded = ctl('print',f'gui/{uid}/{label}',check=False).returncode == 0
        if document is None and loaded:
            raise SystemExit(f'Loaded LaunchAgent has no verifiable Pack plist; refusing to stop it: {label}')
        owned_plists[kind] = document is not None

    # Validate every service before stopping any of them.
    for kind, label in labels.items():
        ctl('bootout',f'gui/{uid}/{label}',check=False)
        if owned_plists[kind]:
            (agents/(label+'.plist')).unlink()
    # Program files only. Database, tokens, config, attachments and engine data stay.
    for name in ('python','hub.py','core','services','room_watch.py','backup-db.sh','verify-backup.sh'):
        p=hub/name
        if p.is_symlink(): p.unlink()
        elif p.is_dir(): shutil.rmtree(p)
        elif p.is_file(): p.unlink()
    for name in ('app','Electron.app'):
        p=engine/name
        if p.is_symlink(): p.unlink()
        elif p.is_dir(): shutil.rmtree(p)
    if app_path.is_dir() and not app_path.is_symlink():
        try:
            with (app_path/'Contents/Info.plist').open('rb') as f: info=plistlib.load(f)
            if info.get('PackInstallerMarker') == 'com.cyou.pack.installer.v1': shutil.rmtree(app_path); print('Installed Pack App removed.')
            else: print('App not removed: installer marker missing.')
        except (OSError,plistlib.InvalidFileException): print('App not removed: could not verify installer marker.')
    print(f'Data preserved: {hub}, {engine}/data, {config}')

if __name__ == '__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('install','upgrade','uninstall'): raise SystemExit('Use install, upgrade or uninstall')
    try:
        if sys.argv[1]=='install': install()
        elif sys.argv[1]=='upgrade': upgrade()
        else: uninstall()
    except Exception as exc: raise SystemExit(f'Operation failed: {type(exc).__name__}: {exc}')
