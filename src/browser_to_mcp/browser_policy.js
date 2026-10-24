// Capability refusals shared by recording, selection, and replay documents.
// Browser/context routing remains authoritative for HTTP and WebSocket traffic.
(() => {
  const notify = window.__btmPolicy;
  const report = code => { void notify(code).catch(() => {}); };
  const deny = code => () => {
    report(code);
    return Promise.reject(new DOMException('Unavailable in this workflow', 'NotAllowedError'));
  };
  const replace = (object, name, value) => {
    if (object && name in object) {
      Object.defineProperty(object, name, {value, writable: false, configurable: false});
    }
  };
  replace(window, 'open', () => { report('duplicate_tab'); return null; });
  replace(window.Notification, 'requestPermission', deny('permission_request'));
  for (const name of ['getUserMedia', 'getDisplayMedia'])
    replace(navigator.mediaDevices, name, deny('permission_request'));
  for (const name of ['read', 'readText', 'write', 'writeText'])
    replace(navigator.clipboard, name, deny('permission_request'));
  for (const name of ['showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker'])
    replace(window, name, deny('file_transfer'));
  for (const [object, name] of [
    [navigator.usb, 'requestDevice'], [navigator.hid, 'requestDevice'],
    [navigator.serial, 'requestPort'], [navigator.bluetooth, 'requestDevice'],
    [navigator.credentials, 'get'], [navigator.credentials, 'create'],
  ]) replace(object, name, deny('permission_request'));
  if (navigator.geolocation) {
    const fail = (_success, failure) => {
      report('permission_request');
      failure?.({code: 1, message: 'Unavailable in this workflow'});
      return 0;
    };
    replace(navigator.geolocation, 'getCurrentPosition', fail);
    replace(navigator.geolocation, 'watchPosition', fail);
  }
  replace(navigator.serviceWorker, 'register', deny('service_worker'));
  for (const [name, code] of [
    ['Worker', 'worker'], ['SharedWorker', 'worker'],
    ['WebTransport', 'socket'], ['EventSource', 'socket'], ['RTCPeerConnection', 'socket'],
    ['webkitRTCPeerConnection', 'socket'],
  ]) replace(window, name, function() {
    report(code);
    throw new DOMException('Unavailable in this workflow', 'NotAllowedError');
  });
  // Stop native file pickers before they can open. An uploaded file is never supplied.
  addEventListener('click', event => {
    if (event.composedPath().some(el => el instanceof Element && el.matches('input[type=file]'))) {
      event.preventDefault(); event.stopImmediatePropagation(); report('file_transfer');
    }
  }, true);
})();
