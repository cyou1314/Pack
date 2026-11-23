#!/usr/bin/env python3
"""Install the packaged local stack; PACK_INSTALL_TEST_ROOT isolates acceptance."""
import asyncio, json, os, pathlib, plistlib, shutil, socket, subprocess, sys, time

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
uid = os.getuid()

def ctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

def _remove_owned_path(path):
    """Remove one path created by this install attempt, never follow symlinks."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _rollback_install(agent_plists, owned_paths, agents_dir, agents_preexisting):
    """Best-effort rollback limited to paths verified absent before installation."""
    for label in labels.values():
        try:
            ctl('bootout', f'gui/{uid}/{label}', check=False)
        except OSError:
            pass
    for path in (*agent_plists, *owned_paths):
        try:
            _remove_owned_path(path)
        except OSError:
            pass
    if not agents_preexisting:
        try:
            agents_dir.rmdir()
        except OSError:
            pass


def install():
    if hub.exists() or engine.exists() or config.exists(): raise SystemExit('Existing service data or App config found; refusing to overwrite.')
    if not available(hp) or not available(ep): raise SystemExit('A required local port is in use; refusing to proceed.')
    for label in labels.values():
        if ctl('print',f'gui/{uid}/{label}',check=False).returncode == 0: raise SystemExit('A service label is already in use.')
    if socket_dir.exists(): raise SystemExit('Existing handoff socket directory; refusing to overwrite.')
    agent_plists = [agents/(label+'.plist') for label in labels.values()]
    if any(path.exists() or path.is_symlink() for path in agent_plists):
        raise SystemExit('A service LaunchAgent plist already exists; refusing to overwrite.')
    agents_preexisting = agents.exists()
    try:
        os.umask(0o077); hub.mkdir(parents=True,mode=0o700); engine.mkdir(parents=True,mode=0o700); agents.mkdir(parents=True,exist_ok=True)
        shutil.copytree(BUNDLE/'python',hub/'python',symlinks=True)
        for name in ('hub.py','room_watch.py','backup-db.sh','verify-backup.sh'): shutil.copy2(BUNDLE/'hub'/name,hub/name)
        shutil.copytree(BUNDLE/'engine'/'app',engine/'app',symlinks=True)
        subprocess.run(['/usr/bin/ditto',str(BUNDLE/'engine'/'Electron.app'),str(engine/'Electron.app')],check=True)
        (hub/'attachments').mkdir(mode=0o700); (engine/'data'/'local').mkdir(parents=True,mode=0o700); socket_dir.mkdir(parents=True,mode=0o700)
        py=hub/'python/bin/python3'; common={'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost','PYTHONUNBUFFERED':'1'}
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
        # Every destination below was confirmed absent before this attempt.
        # Roll back even when staging/copying fails before any service starts.
        _rollback_install(agent_plists, (config, socket_dir, engine, hub), agents, agents_preexisting)
        raise
    print(f'Installed local services; hub 127.0.0.1:{hp}, engine 127.0.0.1:{ep}.')
    print('Open Pack to migrate the private local credentials into Keychain.')

def uninstall():
    for label in labels.values():
        ctl('bootout',f'gui/{uid}/{label}',check=False)
        plist=agents/(label+'.plist')
        if plist.is_file(): plist.unlink()
    # Program files only. Database, tokens, config, attachments and engine data stay.
    for name in ('python','hub.py','room_watch.py','backup-db.sh','verify-backup.sh'):
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
    if len(sys.argv)!=2 or sys.argv[1] not in ('install','uninstall'): raise SystemExit('Use install or uninstall')
    try: install() if sys.argv[1]=='install' else uninstall()
    except Exception as exc: raise SystemExit(f'Operation failed: {type(exc).__name__}: {exc}')
