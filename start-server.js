const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// Check for local virtual environment first, then fallback to system python
const venvPythonWin = path.join(__dirname, 'venv', 'Scripts', 'python.exe');
const venvPythonUnix = path.join(__dirname, 'venv', 'bin', 'python');

let pythonCmd = 'python';
if (process.platform === 'win32' && fs.existsSync(venvPythonWin)) {
  pythonCmd = venvPythonWin;
} else if (process.platform !== 'win32' && fs.existsSync(venvPythonUnix)) {
  pythonCmd = venvPythonUnix;
}

console.log(`[Logical AI] Starting FastAPI backend with Python interpreter: ${pythonCmd}`);

const env = {
  ...process.env,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
};

const child = spawn(pythonCmd, ['server.py'], {
  cwd: __dirname,
  stdio: 'inherit',
  shell: false,
  env,
});

child.on('error', (err) => {
  console.error('[Logical AI] Failed to start backend process:', err.message);
  process.exit(1);
});

child.on('exit', (code) => {
  if (code !== 0 && code !== null) {
    console.error(`[Logical AI] Backend exited with code ${code}`);
  }
  process.exit(code || 0);
});

process.on('SIGINT', () => {
  try {
    child.kill('SIGINT');
  } catch (e) {}
  process.exit();
});

process.on('SIGTERM', () => {
  try {
    child.kill('SIGTERM');
  } catch (e) {}
  process.exit();
});
