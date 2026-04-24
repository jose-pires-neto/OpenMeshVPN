/**
 * OpenMeshVPN — Preload Script (IPC seguro)
 * ==========================================
 * Bridge segura entre o processo Renderer (UI HTML) e o Main Process (Node.js).
 * contextIsolation: true garante que a UI não tem acesso direto ao Node.js.
 * Apenas os métodos explicitamente expostos aqui são acessíveis na UI.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    /** Retorna a plataforma do SO (win32, linux, darwin) */
    getPlatform: () => ipcRenderer.invoke('get-platform'),

    /** Retorna o hostname da máquina (sugestão de nome na UI) */
    getHostname: () => ipcRenderer.invoke('get-hostname'),

    /** Retorna a versão do app (exibida na UI) */
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
});
