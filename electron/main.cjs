const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');

let mainWindow;
let backend;
let backendLog;
let quitting = false;
let appUrl;

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

function allocatePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function ensureSecret(userDataPath) {
  const secretPath = path.join(userDataPath, 'secret.key');
  try {
    return fs.readFileSync(secretPath, 'utf8').trim();
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    const secret = crypto.randomBytes(32).toString('hex');
    fs.writeFileSync(secretPath, `${secret}\n`, { mode: 0o600, flag: 'wx' });
    return secret;
  }
}

function backendExecutable() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'habix-backend.exe');
  }
  return path.join(__dirname, '..', 'backend', 'dist', 'habix-backend.exe');
}

function probeServer(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.setTimeout(900, () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function waitForBackend(child, url) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`The HABIX service stopped during startup (exit code ${child.exitCode}).`);
    }
    if (await probeServer(url)) return;
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  throw new Error('The HABIX service did not become ready within 30 seconds.');
}

async function startBackend() {
  const executable = backendExecutable();
  if (!fs.existsSync(executable)) {
    throw new Error(`The bundled HABIX service was not found:\n${executable}`);
  }

  const userDataPath = app.getPath('userData');
  fs.mkdirSync(userDataPath, { recursive: true });
  const port = await allocatePort();
  const url = `http://127.0.0.1:${port}`;
  const logPath = path.join(userDataPath, 'backend.log');
  backendLog = fs.createWriteStream(logPath, { flags: 'a' });
  backend = spawn(executable, [], {
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      HABIX_HOST: '127.0.0.1',
      HABIX_PORT: String(port),
      HABIX_DATABASE: path.join(userDataPath, 'habix.sqlite3'),
      HABIX_SECRET_KEY: ensureSecret(userDataPath),
      HABIX_DEBUG: '0',
      HABIX_COOKIE_SECURE: '0',
    },
  });
  backend.stdout.pipe(backendLog);
  backend.stderr.pipe(backendLog);
  backend.on('error', (error) => {
    if (mainWindow && !quitting) {
      dialog.showErrorBox('HABIX AI could not start', `${error.message}\n\nSee ${logPath} for details.`);
      app.quit();
    }
  });
  backend.on('exit', (code) => {
    if (!quitting && code !== 0 && mainWindow) {
      dialog.showErrorBox('HABIX AI stopped', `The local service exited with code ${code}.\n\nSee ${logPath} for details.`);
      app.quit();
    }
  });
  await waitForBackend(backend, url);
  appUrl = url;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 920,
    minHeight: 680,
    show: false,
    title: 'HABIX AI',
    backgroundColor: '#0c1019',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'splash.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, targetUrl) => {
    if (!appUrl || !(targetUrl === appUrl || targetUrl.startsWith(`${appUrl}/`))) event.preventDefault();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  if (!gotSingleInstanceLock) return;
  app.setAppUserModelId('com.habixai.desktop');
  createWindow();
  try {
    await startBackend();
    if (mainWindow) await mainWindow.loadURL(appUrl);
  } catch (error) {
    dialog.showErrorBox('HABIX AI could not start', error.message);
    app.quit();
  }
});

app.on('before-quit', () => {
  quitting = true;
  if (backend && backend.exitCode === null) backend.kill();
  if (backendLog) backendLog.end();
});

app.on('window-all-closed', () => app.quit());
