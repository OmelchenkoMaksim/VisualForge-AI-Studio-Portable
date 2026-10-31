// One place for pictures that enter the studio: any common format may be chosen, pasted or dragged in.
// The browser decodes what it can (fast, local); when it cannot (HEIC, TIFF, PSD, a damaged colour profile...) or the file is too big for the
// chosen neural network, the file is sent to the studio's own converter (/api/image/convert, Pillow on this computer) and the answer is used instead.
// Nothing leaves the machine.
window.StudioImages = (() => {
  // What the file dialogs offer besides "image/*": some systems do not know the MIME type of these files.
  const ACCEPT = 'image/*,.heic,.heif,.avif,.tif,.tiff,.bmp,.dib,.psd,.tga,.dds,.qoi,.ico,.cur,.gif,.webp,.jp2,.j2k,.jfif,.jpe,.pcx,.ppm,.pgm,.pbm,.sgi,.icns';
  const EXTENSION = /\.(?:png|jpe?g|jfif|webp|avif|gif|bmp|dib|tiff?|heic|heif|psd|tga|dds|qoi|ico|cur|jp2|j2k|pcx|ppm|pgm|pbm|sgi|icns)$/i;

  // A picture by type or, when the system gave no type, by name.
  const isPicture = file => !!file && (/^image\//.test(file.type || '') || EXTENSION.test(file.name || ''));

  // The first picture of a paste event (a screenshot arrives as a file of the clipboard), or null.
  function fromClipboard(event) {
    const data = event.clipboardData;
    if (!data) return null;
    const files = [...(data.files || [])].filter(isPicture);
    if (files.length) return files[0];
    for (const item of data.items || []) {
      if (item.kind === 'file' && /^image\//.test(item.type)) { const file = item.getAsFile(); if (file) return file; }
    }
    return null;
  }

  // The file converted by the studio: {blob, width, height, changes, source}. profile: 'video' | 'image' | 'chat' (what the neural network takes).
  async function convertOnServer(blob, profile = 'image') {
    let response;
    try {
      response = await fetch('/api/image/convert?profile=' + encodeURIComponent(profile), {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: blob});
    } catch {
      throw new Error(StudioI18n.t('common.image_convert_server_unreachable'));
    }
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(answer.error || StudioI18n.t('common.image_convert_server_failed'));
    const bytes = Uint8Array.from(atob(answer.base64), char => char.charCodeAt(0));
    return {blob: new Blob([bytes], {type: answer.mime}), width: answer.width, height: answer.height, changes: answer.changes || [], source: answer.source || {}};
  }

  // The browser's own decoding: an ImageBitmap, or null when it cannot read the file.
  async function decode(blob) {
    try { return await createImageBitmap(blob); } catch { /* try the image element below */ }
    const url = URL.createObjectURL(blob);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      return await createImageBitmap(image);
    } catch {
      return null;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  // A bitmap (decoded by the browser or from the server) that is safe to draw: `source` is the file itself when the browser reads it, otherwise
  // the converted file. `converted` tells the panel to keep the PNG from the studio instead of the original.
  async function bitmapOf(blob, profile = 'image') {
    const own = await decode(blob);
    if (own) return {bitmap: own, blob, converted: false};
    const result = await convertOnServer(blob, profile);
    const bitmap = await decode(result.blob);
    if (!bitmap) throw new Error(StudioI18n.t('common.image_convert_server_failed'));
    return {bitmap, blob: result.blob, converted: true, changes: result.changes};
  }

  // Replace `accept` of every picture input of the page (inputs made later call StudioImages.accept(input) themselves).
  function accept(input) { if (input && input.type === 'file') input.accept = ACCEPT; return input; }
  function applyAccept(root = document) {
    for (const input of root.querySelectorAll('input[type="file"][accept^="image/"]')) accept(input);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => applyAccept());
  else applyAccept();

  return {ACCEPT, isPicture, fromClipboard, convertOnServer, decode, bitmapOf, accept, applyAccept};
})();
