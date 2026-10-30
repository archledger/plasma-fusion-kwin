// SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
// SPDX-License-Identifier: GPL-2.0-or-later
const { spawnSync } = require('node:child_process');
if (!process.env.ACTIONS_RUNTIME_TOKEN || !process.env.ACTIONS_RESULTS_URL) {
  throw new Error('Artifact runtime is unavailable');
}
const result = spawnSync('python3', ['-I', '-B', '-c',
  "import runpy,sys; sys.path.insert(0, '.'); runpy.run_module('tools.probe_live_worker', run_name='__main__')"],
  { stdio: 'inherit', timeout: 240000, env: { ...process.env, PROBE_NODE: process.execPath } });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
