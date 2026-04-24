/**
 * OpenMeshVPN — Electron Main Process
 * =====================================
 * Responsável por:
 *   - Em DEV: iniciar o backend via `python Windows/main.py`
 *   - Em PRODUÇÃO: iniciar o backend via `backend.exe` (gerado pelo PyInstaller)
 *   - Aguardar o backend estar pronto antes de carregar a UI
 *   - Criar a janela principal da aplicação
 *   - Limpar processos ao fechar
 */

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const os = require('os');

let mainWindow;
let pythonProcess;

// ─────────────────────────────────────────────
//  Detecção de ambiente: DEV vs PRODUÇÃO
// ─────────────────────────────────────────────

/**
 * Em produção (app empacotado pelo electron-builder),
 * app.isPackaged === true e o backend.exe fica em resources/backend.exe
 *
 * Em desenvolvimento (npm start), usa python diretamente.
 */
function getBackendCommand() {
    if (app.isPackaged) {
        // Produção: usa o backend.exe gerado pelo PyInstaller
        // O electron-builder copia extraResources para process.resourcesPath
        const backendExe = path.join(process.resourcesPath, 'backend.exe');
        console.log(`[Electron] Modo PRODUÇÃO — usando: ${backendExe}`);
        return { cmd: backendExe, args: [], cwd: process.resourcesPath };
    } else {
        // Desenvolvimento: usa python normalmente
        const pythonExe = os.platform() === 'win32' ? 'python' : 'python3';
        const scriptPath = (() => {
            const p = os.platform();
            if (p === 'win32')  return path.join(__dirname, 'Windows', 'main.py');
            if (p === 'linux')  return path.join(__dirname, 'Linux',   'main.py');
            if (p === 'darwin') return path.join(__dirname, 'MacOS',   'main.py');
            return path.join(__dirname, 'Windows', 'main.py');
        })();
        console.log(`[Electron] Modo DEV — usando: ${pythonExe} ${scriptPath}`);
        return { cmd: pythonExe, args: [scriptPath], cwd: __dirname };
    }
}

// ─────────────────────────────────────────────
//  Aguardar o backend Flask estar pronto
// ─────────────────────────────────────────────

/**
 * Tenta conectar na API Flask em http://127.0.0.1:8080/
 * Retorna uma Promise que resolve quando a API responder.
 * Tenta até maxAttempts vezes com intervalo de delayMs.
 */
function waitForBackend(maxAttempts = 30, delayMs = 500) {
    return new Promise((resolve, reject) => {
        let attempts = 0;

        const tryConnect = () => {
            attempts++;
            const req = http.get('http://127.0.0.1:8080/', (res) => {
                console.log(`[Electron] ✅ Backend pronto! (tentativa ${attempts})`);
                resolve();
            });

            req.on('error', () => {
                if (attempts >= maxAttempts) {
                    reject(new Error(`Backend não iniciou após ${maxAttempts} tentativas`));
                    return;
                }
                setTimeout(tryConnect, delayMs);
            });

            req.setTimeout(400, () => {
                req.destroy();
            });
        };

        tryConnect();
    });
}

// ─────────────────────────────────────────────
//  Criar janela principal
// ─────────────────────────────────────────────

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1100,
        height: 720,
        minWidth: 800,
        minHeight: 600,
        title: 'OpenMeshVPN',
        autoHideMenuBar: true,
        backgroundColor: '#0b0f19',
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            // preload.js para IPC seguro entre UI e processo main
            preload: path.join(__dirname, 'preload.js'),
        },
    });

    // Mostrar janela de carregamento antes do backend ficar pronto
    mainWindow.loadFile(path.join(__dirname, 'ui', 'loading.html'))
        .catch(() => {
            // Se loading.html não existir, carrega direto
            mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
        });
}

// ─────────────────────────────────────────────
//  Inicialização
// ─────────────────────────────────────────────

app.whenReady().then(() => {
    // 1. Criar janela imediatamente (com tela de loading)
    createWindow();

    // 2. Iniciar backend (backend.exe em produção, python em dev)
    const { cmd, args, cwd } = getBackendCommand();

    pythonProcess = spawn(cmd, args, {
        cwd,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
    });

    pythonProcess.stdout.on('data', (data) => {
        process.stdout.write(`[Python] ${data}`);
    });

    pythonProcess.stderr.on('data', (data) => {
        process.stderr.write(`[Python ERR] ${data}`);
    });

    pythonProcess.on('exit', (code) => {
        console.log(`[Electron] Backend Python encerrou (código: ${code})`);
    });

    // 3. Aguardar backend e carregar UI real
    waitForBackend(40, 500)
        .then(() => {
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
            }
        })
        .catch((err) => {
            console.error(`[Electron] ❌ ${err.message}`);
            // Mesmo sem backend, carregar a UI (vai mostrar estado offline)
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
            }
        });

    // macOS: recriar janela ao clicar no dock
    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

// ─────────────────────────────────────────────
//  Limpeza ao fechar
// ─────────────────────────────────────────────

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
    if (pythonProcess) {
        console.log('[Electron] Encerrando backend Python...');
        pythonProcess.kill('SIGTERM');
        // Force kill após 3s se não responder
        setTimeout(() => {
            if (pythonProcess) pythonProcess.kill('SIGKILL');
        }, 3000);
    }
});

// ─────────────────────────────────────────────
//  IPC — canais seguros expostos via preload.js
// ─────────────────────────────────────────────

ipcMain.handle('get-platform', () => os.platform());
ipcMain.handle('get-hostname', () => os.hostname());
ipcMain.handle('get-app-version', () => app.getVersion());
