'use strict';

/**
 * Resolve the Python executable and service script for dev and package-equivalent
 * layouts. ADR 0006 calls out "executable and resource discovery in both
 * development and packaged layouts" as required evidence.
 *
 * This module is deliberately pure and synchronous so it can be unit-tested
 * against a fake package directory layout without spawning anything.
 */

const fs = require('node:fs');
const path = require('node:path');

/**
 * Resolve the Python interpreter.
 *
 * Resolution order:
 *   1. env.SPIKE_PYTHON_EXECUTABLE  (explicit override; used by tests)
 *   2. a packaged layout, if `packagedRoot` is given and contains python/bin/python3
 *   3. the dev default "python3" (relied on PATH / active venv)
 *
 * @param {{packagedRoot?: string, env?: NodeJS.ProcessEnv}} [options]
 * @returns {{path: string, source: 'env'|'packaged'|'dev', exists: boolean}}
 */
function resolvePythonExecutable(options = {}) {
  const { packagedRoot, env = process.env } = options;

  if (env.SPIKE_PYTHON_EXECUTABLE) {
    return {
      path: env.SPIKE_PYTHON_EXECUTABLE,
      source: 'env',
      exists: fs.existsSync(env.SPIKE_PYTHON_EXECUTABLE),
    };
  }

  if (packagedRoot) {
    const candidate = path.join(packagedRoot, 'python', 'bin', 'python3');
    return { path: candidate, source: 'packaged', exists: fs.existsSync(candidate) };
  }

  // Dev: rely on the active venv / PATH. The caller (Electron main) is expected
  // to be launched from the project venv in development.
  return { path: 'python3', source: 'dev', exists: true };
}

/**
 * Resolve the backend service script.
 *
 * @param {{packagedRoot?: string, devScriptPath?: string, here?: string}} [options]
 */
function resolveScriptPath(options = {}) {
  const { packagedRoot, devScriptPath, here = __dirname } = options;
  if (packagedRoot) {
    return path.join(packagedRoot, 'backend', 'service.py');
  }
  if (devScriptPath) return devScriptPath;
  // Default dev path: <spike-root>/fake-python/service.py
  return path.resolve(here, '..', '..', 'fake-python', 'service.py');
}

/**
 * Discover a packaged resources root from a list of candidate directories.
 * Returns the first candidate that actually contains a python interpreter,
 * or null when running in development.
 *
 * @param {{candidates?: string[], appRoot?: string}} [options]
 */
function discoverPackagedRoot(options = {}) {
  const candidates = options.candidates
    ? [...options.candidates]
    : [
        options.appRoot ? path.join(options.appRoot, 'resources') : null,
        process.resourcesPath ? path.join(process.resourcesPath) : null,
      ].filter(Boolean);

  for (const candidate of candidates) {
    if (!candidate) continue;
    if (fs.existsSync(path.join(candidate, 'python', 'bin', 'python3'))) {
      return candidate;
    }
  }
  return null;
}

module.exports = {
  resolvePythonExecutable,
  resolveScriptPath,
  discoverPackagedRoot,
};
