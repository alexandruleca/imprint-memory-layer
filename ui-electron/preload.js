'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform:   process.platform,
  close:      () => ipcRenderer.send('win-close'),
  quit:       () => ipcRenderer.send('app-quit'),
  minimize:   () => ipcRenderer.send('win-minimize'),
  maximize:   () => ipcRenderer.send('win-maximize'),
  openCompanion: () => ipcRenderer.send('voice-open-companion'),
  closeCompanion: () => ipcRenderer.send('voice-close-companion'),
  pinCompanion: pinned => ipcRenderer.send('voice-pin-companion', pinned),
  getCompanionState: () => ipcRenderer.invoke('voice-companion-state'),
  setCompanionVisible: enabled => ipcRenderer.invoke('voice-set-companion', enabled),
  onCompanionState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('voice-companion-state', listener);
    return () => ipcRenderer.removeListener('voice-companion-state', listener);
  },
  startupState: () => ipcRenderer.invoke('startup-state'),
  retryStartup: () => ipcRenderer.send('startup-retry'),
  openStartupLog: () => ipcRenderer.send('startup-log'),
  onStartupState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('startup-state', listener);
    return () => ipcRenderer.removeListener('startup-state', listener);
  },
});
