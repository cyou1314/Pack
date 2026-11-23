# Pack local hub

`install-local.sh` deploys the repository's unchanged hub, watcher and backup scripts to `~/.pack/local-hub`, creates a private venv using Python 3.10 or newer, installs `fastmcp==4.1.8`, initializes the database and starts three user LaunchAgents. Set `PACK_PYTHON` to an absolute Python executable if `python3` is too old. The listener is restricted to `127.0.0.1:8794`; the watcher uses that address and the local `.dispatcher_token` file. The local directory is mode 0700 and the token is mode 0600. Existing database without its token is refused rather than recreated.

`install-local.sh` is a fresh-install developer script and intentionally refuses an existing service directory. It is not the packaged upgrade path. For a DMG-installed Pack, use the DMG root's `upgrade.command`; do not run the local script or uninstall/reinstall to update a production installation.

```sh
PACK_PYTHON=/path/to/python3 ./local/install-local.sh
launchctl print gui/$(id -u)/com.pack.local-hub
launchctl print gui/$(id -u)/com.pack.local-watch
launchctl print gui/$(id -u)/com.pack.local-backup
bash ~/.pack/local-hub/backup-db.sh
bash ~/.pack/local-hub/verify-backup.sh
./local/uninstall-local.sh
```

Backup runs daily at 03:15. Uninstall removes the three agents and service files/venv, but preserves `~/.pack/local-hub/pack.db`, attachments, credentials, and backups. It does not remove an App. Stop/start a service with `launchctl bootout`/`bootstrap` against its matching plist. Reinstall after service code changes; already loaded services must be restarted explicitly to run the new files.

The installer does not replace an existing VM App configuration or credentials. For a local App profile, provision local dispatcher and owner credentials through PackConfig's Keychain migration in a private 0600 configuration, and set the hub URL to `http://127.0.0.1:8794/mcp`. `PACK_CONFIG_PATH` can select an isolated absolute configuration path for an acceptance copy; its distinct bundle identifier supplies a separate Keychain service. Loopback addresses never invoke published-address discovery, including after restart. Shipping an installer and first-run credential provisioning are subsequent work.
