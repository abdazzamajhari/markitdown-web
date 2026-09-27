// Small, dependency-free ZIP writer. Entries are stored without compression.
// The browser assembles the already-converted Markdown; no second upload is needed.
(function () {
  const encoder = new TextEncoder();
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[n] = value >>> 0;
  }
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = table[(crc ^ byte) & 255] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }
  function filenameFor(name, used) {
    const basename = String(name).split(/[\\/]/).pop().replace(/[\x00-\x1f\x7f<>:"|?*]/g, '_');
    const stem = basename.replace(/\.[^.]*$/, '').replace(/^\.+/, '').trim().slice(0, 110) || 'document';
    let filename = `${stem}.md`;
    let number = 2;
    while (used.has(filename.toLowerCase())) filename = `${stem} (${number++}).md`;
    used.add(filename.toLowerCase());
    return filename;
  }
  function createMarkdownZip(items) {
    if (!Array.isArray(items) || !items.length || items.length > 1000) throw new Error('Jumlah berkas ZIP tidak valid');
    const parts = [], directory = [], used = new Set();
    let offset = 0, directorySize = 0;
    for (const item of items) {
      const name = encoder.encode(filenameFor(item.name, used));
      const content = encoder.encode(item.markdown);
      const crc = crc32(content);
      if (offset + content.length + name.length + 30 > 0xffffffff) throw new Error('ZIP terlalu besar');
      const local = new Uint8Array(30 + name.length);
      const l = new DataView(local.buffer);
      l.setUint32(0, 0x04034b50, true);
      l.setUint16(4, 20, true);
      l.setUint16(6, 0x0800, true); // UTF-8 names
      l.setUint32(14, crc, true);
      l.setUint32(18, content.length, true);
      l.setUint32(22, content.length, true);
      l.setUint16(26, name.length, true);
      local.set(name, 30);
      parts.push(local, content);

      const central = new Uint8Array(46 + name.length);
      const c = new DataView(central.buffer);
      c.setUint32(0, 0x02014b50, true);
      c.setUint16(4, 20, true);
      c.setUint16(6, 20, true);
      c.setUint16(8, 0x0800, true);
      c.setUint32(16, crc, true);
      c.setUint32(20, content.length, true);
      c.setUint32(24, content.length, true);
      c.setUint16(28, name.length, true);
      c.setUint32(42, offset, true);
      central.set(name, 46);
      directory.push(central);
      offset += local.length + content.length;
      directorySize += central.length;
    }
    const end = new Uint8Array(22);
    const e = new DataView(end.buffer);
    e.setUint32(0, 0x06054b50, true);
    e.setUint16(8, items.length, true);
    e.setUint16(10, items.length, true);
    e.setUint32(12, directorySize, true);
    e.setUint32(16, offset, true);
    return new Blob([...parts, ...directory, end], {type: 'application/zip'});
  }
  globalThis.createMarkdownZip = createMarkdownZip;
})();
