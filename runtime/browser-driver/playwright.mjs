import { createRequire } from 'node:module';
import { BrowserSessions } from './sessions.mjs';

const require = createRequire(import.meta.url);
export const PLAYWRIGHT_VERSION = '1.65.1';

/** Opt-in adapter. No default Chrome profile, CDP exposure or cookie import. */
export async function createBrowserSessions(options) {
  const metadata = require('playwright-core/package.json');
  if (metadata.version !== PLAYWRIGHT_VERSION) throw Error('unexpected_playwright_version');
  const { chromium } = await import('playwright-core');
  return new BrowserSessions({
    ...options,
    launch: (directory, launchOptions) => chromium.launchPersistentContext(directory, {
      ...launchOptions,
      acceptDownloads: false,
      chromiumSandbox: true
    })
  });
}
