/**
 * Bounded child-process runner for the local browser driver.
 * It caps combined stdout/stderr, enforces a wall-clock deadline, and
 * propagates AbortSignal cancellation while allowing a short SIGTERM grace.
 */
import { spawn } from 'node:child_process';

function codedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

export function runBoundedProcess(command, args = [], options = {}) {
  const {
    input = '',
    signal,
    timeoutMs = 6 * 60_000,
    maxOutputBytes = 8 * 1024 * 1024,
    maxInputBytes = 8 * 1024 * 1024,
    cwd,
    env,
  } = options;

  if (typeof command !== 'string' || !command || !Array.isArray(args)
      || !args.every(arg => typeof arg === 'string')) {
    return Promise.reject(new TypeError('invalid_process_command'));
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1
      || !Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1
      || !Number.isSafeInteger(maxInputBytes) || maxInputBytes < 1) {
    return Promise.reject(new RangeError('invalid_process_limits'));
  }
  let inputBuffer;
  try {
    inputBuffer = Buffer.isBuffer(input) ? input : Buffer.from(input);
  } catch {
    return Promise.reject(new TypeError('invalid_process_input'));
  }
  if (inputBuffer.length > maxInputBytes) {
    return Promise.reject(codedError('driver_input_limit'));
  }
  if (signal?.aborted) return Promise.reject(codedError('driver_aborted'));

  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(command, args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        ...(cwd ? { cwd } : {}),
        ...(env ? { env } : {}),
      });
    } catch (error) {
      reject(error);
      return;
    }

    const stdout = [], stderr = [];
    let outputBytes = 0;
    let failure = null;
    let settled = false;
    let timeout = null;
    let killTimer = null;

    const cleanup = () => {
      if (timeout) clearTimeout(timeout);
      if (killTimer) clearTimeout(killTimer);
      signal?.removeEventListener('abort', onAbort);
    };
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve(value);
    };
    const terminate = error => {
      if (failure || settled) return;
      failure = error;
      try { child.kill('SIGTERM'); } catch {}
      killTimer = setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) {
          try { child.kill('SIGKILL'); } catch {}
        }
      }, 1000);
      killTimer.unref?.();
    };
    const collect = (target, chunk) => {
      if (failure || settled) return;
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      outputBytes += buffer.length;
      if (outputBytes > maxOutputBytes) {
        terminate(codedError('driver_output_limit'));
        return;
      }
      target.push(buffer);
    };
    const onAbort = () => terminate(codedError('driver_aborted'));

    child.stdout.on('data', chunk => collect(stdout, chunk));
    child.stderr.on('data', chunk => collect(stderr, chunk));
    child.stdin.on('error', error => {
      if (error.code !== 'EPIPE') terminate(codedError('driver_stdin_error'));
    });
    child.once('error', error => finish(error));
    child.once('close', (code, signalCode) => {
      if (failure) return finish(failure);
      if (signal?.aborted) return finish(codedError('driver_aborted'));
      finish(null, {
        code,
        signal: signalCode,
        stdout: Buffer.concat(stdout).toString('utf8'),
        stderr: Buffer.concat(stderr).toString('utf8'),
      });
    });

    timeout = setTimeout(() => terminate(codedError('driver_timeout')), timeoutMs);
    timeout.unref?.();
    if (signal) signal.addEventListener('abort', onAbort, { once: true });
    try { child.stdin.end(inputBuffer); }
    catch { terminate(codedError('driver_stdin_error')); }
  });
}
