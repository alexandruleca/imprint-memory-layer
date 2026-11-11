'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform:   process.platform,
  requestMicrophoneAccess: () => ipcRenderer.invoke('voice-microphone-access'),
  openMicrophoneSettings: () => ipcRenderer.invoke('voice-microphone-settings'),
  close:      () => ipcRenderer.send('win-close'),
  quit:       () => ipcRenderer.send('app-quit'),
  minimize:   () => ipcRenderer.send('win-minimize'),
  maximize:   () => ipcRenderer.send('win-maximize'),
  // The dashboard's theme choice: 'light', 'dark' or 'system' (ADR 0154).
  setTheme:   choice => ipcRenderer.send('theme-set', choice),
  openCompanion: () => ipcRenderer.send('voice-open-companion'),
  closeCompanion: () => ipcRenderer.send('voice-close-companion'),
  pinCompanion: pinned => ipcRenderer.send('voice-pin-companion', pinned),
  recoverCompanion: () => ipcRenderer.send('voice-recover-companion'),
  beginCompanionDrag: point => ipcRenderer.send('voice-drag-start', point),
  moveCompanionDrag: point => ipcRenderer.send('voice-drag-move', point),
  endCompanionDrag: () => ipcRenderer.send('voice-drag-end'),
  collapseCompanion: () => ipcRenderer.send('voice-collapse-companion'),
  openDashboard: () => ipcRenderer.send('launcher-open'),
  launcherMenu: () => ipcRenderer.send('launcher-menu'),
  // The orb and its ring of actions (ADR 0135).
  toggleLauncherRing: focus => ipcRenderer.send('launcher-ring', focus),
  nudgeLauncher: delta => ipcRenderer.send('launcher-nudge', delta),
  getLauncherState: () => ipcRenderer.invoke('launcher-state'),
  onLauncherState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('launcher-state', listener);
    return () => ipcRenderer.removeListener('launcher-state', listener);
  },
  getHaloLayout: () => ipcRenderer.invoke('halo-layout'),
  onHaloLayout: callback => {
    const listener = (_event, layout) => callback(layout);
    ipcRenderer.on('halo-layout', listener);
    return () => ipcRenderer.removeListener('halo-layout', listener);
  },
  haloReady: seq => ipcRenderer.send('halo-ready', seq),
  haloAction: action => ipcRenderer.send('halo-action', action),
  haloSize: height => ipcRenderer.send('halo-size', height),
  beginLauncherDrag: point => ipcRenderer.send('launcher-drag-start', point),
  moveLauncherDrag: point => ipcRenderer.send('launcher-drag-move', point),
  endLauncherDrag: () => ipcRenderer.send('launcher-drag-end'),
  // Meeting recordings: the dashboard records and reports; the launcher asks.
  reportRecorder: state => ipcRenderer.send('recorder-state', state),
  reportRecorderLevels: levels => ipcRenderer.send('recorder-levels', levels),
  onRecorderCommand: callback => {
    const listener = (_event, command) => callback(command);
    ipcRenderer.on('recorder-command', listener);
    return () => ipcRenderer.removeListener('recorder-command', listener);
  },
  // The tray opens the dashboard at a page: Recordings, or Voice with "Hey Echo".
  onDashboardGoto: callback => {
    const listener = (_event, page) => callback(page);
    ipcRenderer.on('dashboard-goto', listener);
    return () => ipcRenderer.removeListener('dashboard-goto', listener);
  },
  getRecorder: () => ipcRenderer.invoke('launcher-recorder'),
  startRecording: options => ipcRenderer.send('launcher-record', options),
  stopRecording: () => ipcRenderer.send('launcher-record-stop'),
  onRecorder: callback => {
    const state = (_event, value) => callback({ state: value });
    const levels = (_event, value) => callback({ levels: value });
    ipcRenderer.on('recorder-state', state);
    ipcRenderer.on('recorder-levels', levels);
    return () => { ipcRenderer.removeListener('recorder-state', state); ipcRenderer.removeListener('recorder-levels', levels); };
  },
  // "Hey Echo" (ADR 0137): the choice, its state, and Echo's side of a wake.
  getWakeState: () => ipcRenderer.invoke('voice-wake-state'),
  onWakeState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('wake-state', listener);
    return () => ipcRenderer.removeListener('wake-state', listener);
  },
  setWake: choice => ipcRenderer.invoke('voice-wake-set', choice),
  controlWake: action => ipcRenderer.send('wake-control', action),
  reportMicrophone: on => ipcRenderer.send('voice-microphone-state', on),
  getWakeSession: () => ipcRenderer.invoke('voice-wake-session'),
  onWake: callback => {
    const listener = (_event, event) => callback(event);
    ipcRenderer.on('voice-wake', listener);
    return () => ipcRenderer.removeListener('voice-wake', listener);
  },
  wakeDone: (id, restore) => ipcRenderer.send('voice-wake-done', { id, restore }),
  // The hidden listener page.
  wakeListenerConfig: () => ipcRenderer.invoke('wake-listener-config'),
  onWakeListenerConfig: callback => {
    const listener = (_event, config) => callback(config);
    ipcRenderer.on('wake-listener-config', listener);
    return () => ipcRenderer.removeListener('wake-listener-config', listener);
  },
  reportWakeListener: report => ipcRenderer.send('wake-listener', report),
  getCompanionState: () => ipcRenderer.invoke('voice-companion-state'),
  setCompanionVisible: enabled => ipcRenderer.invoke('voice-set-companion', enabled),
  onCompanionState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('voice-companion-state', listener);
    return () => ipcRenderer.removeListener('voice-companion-state', listener);
  },
  watchCursor: callback => {
    const listener = (_event, point) => callback(point);
    ipcRenderer.on('voice-cursor', listener);
    ipcRenderer.send('voice-cursor-watch', true);
    return () => { ipcRenderer.removeListener('voice-cursor', listener); ipcRenderer.send('voice-cursor-watch', false); };
  },
  // Reminders (ADR 0158): the dashboard reports one that rang; a click on
  // its notification comes back to open it.
  notifyReminder: alert => ipcRenderer.send('reminder-notify', alert),
  onReminderOpen: callback => {
    const listener = (_event, id) => callback(id);
    ipcRenderer.on('reminder-open', listener);
    return () => ipcRenderer.removeListener('reminder-open', listener);
  },
  startupState: () => ipcRenderer.invoke('startup-state'),
  activity: () => ipcRenderer.invoke('activity'),
  retryStartup: () => ipcRenderer.send('startup-retry'),
  openStartupLog: () => ipcRenderer.send('startup-log'),
  onStartupState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('startup-state', listener);
    return () => ipcRenderer.removeListener('startup-state', listener);
  },
});
