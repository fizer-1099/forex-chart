const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');

function createWindow() {
  const w = new BrowserWindow({
    width: 1200, height: 850, minWidth: 420, minHeight: 600,
    backgroundColor: '#ffffff', autoHideMenuBar: true,
    icon: path.join(__dirname, 'app', 'icon-512.png'),
    webPreferences: { contextIsolation: true, sandbox: true, spellcheck: false }
  });
  Menu.setApplicationMenu(null);
  w.loadFile(path.join(__dirname, 'app', 'index.html'));
  // لینک‌های بیرونی در مرورگر باز شوند
  w.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  // F11 = تمام‌صفحه ، Ctrl+Plus/Minus/0 = زوم
  w.webContents.on('before-input-event', (e, i) => {
    if (i.type !== 'keyDown') return;
    if (i.key === 'F11') { w.setFullScreen(!w.isFullScreen()); e.preventDefault(); }
    if (i.control && (i.key === '=' || i.key === '+')) { w.webContents.setZoomLevel(w.webContents.getZoomLevel() + 0.5); e.preventDefault(); }
    if (i.control && i.key === '-') { w.webContents.setZoomLevel(w.webContents.getZoomLevel() - 0.5); e.preventDefault(); }
    if (i.control && i.key === '0') { w.webContents.setZoomLevel(0); e.preventDefault(); }
  });
}

const lock = app.requestSingleInstanceLock();
if (!lock) { app.quit(); } else {
  app.whenReady().then(() => {
    createWindow();
    app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
  });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
}
