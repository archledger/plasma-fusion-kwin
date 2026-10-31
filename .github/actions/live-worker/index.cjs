// SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
// SPDX-License-Identifier: GPL-2.0-or-later
const { spawnSync } = require('node:child_process');
if (!process.env.ACTIONS_RUNTIME_TOKEN || !process.env.ACTIONS_RESULTS_URL) {
  throw new Error('Artifact runtime is unavailable');
}
const native = process.env.NATIVE_JOB_ROLE === 'native';
const program = native
  ? "import runpy,sys; sys.path.insert(0, '.'); sys.argv=['trusted_native','native']; runpy.run_module('tools.trusted_native', run_name='__main__')"
  : "import runpy,sys; sys.path.insert(0, '.'); runpy.run_module('tools.probe_live_worker', run_name='__main__')";
const result = spawnSync('python3', ['-I', '-B', '-c', program],
  { stdio: 'inherit', timeout: native ? 4500000 : 240000, env: { ...process.env, PROBE_NODE: process.execPath } });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
