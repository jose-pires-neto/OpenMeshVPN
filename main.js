const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');

let mainWindow;
let pythonProcess;

function getPythonScriptPath() {
    const platform = os.platform();
    if (platform === 'win32') {
        return path.join(__dirname, 'Windows', 'main.py');
    } else if (platform === 'linux') {
        return path.join(__dirname, 'Linux', 'main.py');
    } else if (platform === 'darwin') {
        return path.join(__dirname, 'MacOS', 'main.py');
    }
    return path.join(__dirname, 'Windows', 'main.py');
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1000,
        height: 700,
        title: "OpenMeshVPN",
        autoHideMenuBar: true,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    // Iniciar o backend Python em background
    const scriptPath = getPythonScriptPath();
    console.log(`Iniciando backend Python para a plataforma ${os.platform()}: ${scriptPath}`);
    
    // Assumimos que o python ou python3 está no PATH
    const pythonExecutable = os.platform() === 'win32' ? 'python' : 'python3';
    pythonProcess = spawn(pythonExecutable, [scriptPath]);

    pythonProcess.stdout.on('data', (data) => {
        console.log(`[Python]: ${data}`);
    });

    pythonProcess.stderr.on('data', (data) => {
        console.error(`[Python Erro]: ${data}`);
    });

    // Carregar a interface do usuário localmente
    mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));

    // Esperar um pouquinho e tentar recarregar os dados na UI (como a API vai iniciar)
    // mainWindow.webContents.openDevTools();
}

app.whenReady().then(() => {
    createWindow();

    app.on('activate', function () {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

// Matar o processo python quando o electron fechar
app.on('will-quit', () => {
    if (pythonProcess) {
        pythonProcess.kill();
    }
});
