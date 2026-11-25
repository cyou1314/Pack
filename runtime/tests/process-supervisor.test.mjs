import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runBoundedProcess } from '../process-supervisor.mjs';

test('bounded process captures stdout, stderr and stdin', async () => {
  const script = 'let value="";process.stdin.setEncoding("utf8");process.stdin.on("data",x=>value+=x);process.stdin.on("end",()=>{process.stdout.write("received:"+value);process.stderr.write("diagnostic");});';
  const result = await runBoundedProcess(process.execPath, ['-e', script], {
    input: 'Pack fixture',
    timeoutMs: 2000,
    maxOutputBytes: 1024,
  });
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'received:Pack fixture');
  assert.equal(result.stderr, 'diagnostic');
});

test('driver process is terminated at its wall-clock deadline', async () => {
  await assert.rejects(
    runBoundedProcess(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {
      timeoutMs: 60,
      maxOutputBytes: 1024,
    }),
    error => error.message === 'driver_timeout',
  );
});

test('driver process is terminated when combined output exceeds the cap', async () => {
  await assert.rejects(
    runBoundedProcess(process.execPath, ['-e', 'process.stdout.write("x".repeat(2000000));setInterval(()=>{},1000)'], {
      timeoutMs: 3000,
      maxOutputBytes: 4096,
    }),
    error => error.message === 'driver_output_limit',
  );
});

test('AbortSignal cancellation terminates the child process', async () => {
  const controller = new AbortController();
  const pending = runBoundedProcess(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {
    signal: controller.signal,
    timeoutMs: 3000,
    maxOutputBytes: 1024,
  });
  setTimeout(() => controller.abort(), 60);
  await assert.rejects(pending, error => error.message === 'driver_aborted');
});

test('invalid process limits fail before spawning', async () => {
  await assert.rejects(
    runBoundedProcess(process.execPath, ['-e', 'process.exit(0)'], { timeoutMs: 0 }),
    error => error.message === 'invalid_process_limits',
  );
});

test('driver input is rejected before spawn when it exceeds the cap', async () => {
  await assert.rejects(
    runBoundedProcess(process.execPath, ['-e', 'process.exit(0)'], {
      input: 'x'.repeat(1025),
      maxInputBytes: 1024,
    }),
    error => error.message === 'driver_input_limit',
  );
});
