'use strict';

const api = window.electronAPI;
function update(state) {
  if (!state) return;
  const failed = state.phase === 'error';
  document.getElementById('status').textContent = state.message;
  document.getElementById('spinner').hidden = failed;
  document.getElementById('actions').hidden = !failed;
  document.getElementById('log').hidden = !state.logPath;
  document.getElementById('hint').textContent = failed ? 'Check the log for details, or try starting again.' : 'Preparing your local dashboard.';
}
api.onStartupState(update);
api.startupState().then(update);
document.getElementById('close').addEventListener('click', () => api.close());
document.getElementById('retry').addEventListener('click', () => api.retryStartup());
document.getElementById('log').addEventListener('click', () => api.openStartupLog());
