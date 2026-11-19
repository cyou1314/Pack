// Build the existing headless entry with the L2 socket service, without
// modifying the Muse4AI source tree. Pass desktop root and output directory.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(process.argv[2] || `${process.env.HOME}/muse4ai/desktop`);
const out = path.resolve(process.argv[3] || `${process.env.HOME}/pack-headless-app`);
const requireDesktop = createRequire(path.join(root, 'package.json'));
const { build } = requireDesktop('esbuild');
const entry = path.join(root, 'src/main/headless.ts');
let source = fs.readFileSync(entry, 'utf8');
const marker = '  await api.init();';
const closeMarker = 'try { await api.shutdown();';
if (source.split(marker).length !== 2 || source.split(closeMarker).length !== 2 || source.split('  let closing = false;').length !== 2) throw Error('headless source does not match expected hooks');
source = `import { startHandoff, cookieEncryptionEnabled } from ${JSON.stringify(path.join(here, 'engine.cjs'))};\n` + source;
source = source.replace('  let closing = false;', '  let stopHandoff: (() => void) | undefined;\n  let closing = false;');
source = source.replace(closeMarker, 'try { stopHandoff?.(); await api.shutdown();');
source = source.replace(marker, `${marker}\n  try { stopHandoff = await startHandoff({ repo, coord, accounts, sessionData: app.getPath('sessionData'), encryptionEnabled: cookieEncryptionEnabled }); } catch { logger.log('error', 'l2', 'handoff service unavailable'); }`);
const hooks = { name: 'l2-hook', setup(b) { b.onLoad({ filter: /headless\.ts$/ }, args => args.path === entry ? { contents: source, loader: 'ts', resolveDir: path.dirname(entry) } : null); } };
const raw = { name: 'raw', setup(b) { b.onResolve({ filter: /\?raw$/ }, args => ({ path: path.resolve(args.resolveDir, args.path.replace(/\?raw$/, '')), namespace: 'raw' })); b.onLoad({ filter: /.*/, namespace: 'raw' }, args => ({ contents: fs.readFileSync(args.path, 'utf8'), loader: 'text' })); } };
fs.mkdirSync(out, { recursive: true });
await build({ entryPoints: [entry], outfile: path.join(out, 'headless.cjs'), alias: { '@content': path.join(root, '../content') }, plugins: [hooks, raw], bundle: true, platform: 'node', format: 'cjs', target: 'node22', external: ['electron', 'node:sqlite'], logLevel: 'info', legalComments: 'inline' });
await build({ entryPoints: [path.join(root, 'src/page/gatewayPage.ts')], outfile: path.join(out, 'gateway-page.js'), bundle: true, platform: 'browser', format: 'iife', target: 'chrome130', sourcemap: false, legalComments: 'none', logLevel: 'info' });
