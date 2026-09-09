import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { zipSync, unzipSync } from 'fflate';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const extension = resolve(frontend, '../browser-extension');
const files = ['manifest.json', 'capture-utils.js', 'capture-download.js', 'page-capture.js', 'content.js', 'background.js', 'popup.html', 'popup.css', 'popup.js', 'README.md'];
const entries = {};
const hash = createHash('sha256');
for (const name of files) {
  const content = (await readFile(resolve(extension, name), 'utf8')).replace(/\r\n/g, '\n');
  entries[name] = new TextEncoder().encode(content);
  hash.update(name).update('\0').update(content).update('\0');
}
const manifest = JSON.parse(new TextDecoder().decode(entries['manifest.json']));
// Chrome must see a distinct script URL for each execution world.
const pageMain = new TextDecoder().decode(entries['capture-utils.js']) + '\n' + new TextDecoder().decode(entries['page-capture.js']);
entries['page-capture-main.js'] = new TextEncoder().encode(pageMain);
const metadata = { version: manifest.version, build: hash.digest('hex').slice(0, 16) };
const buildInfo = `globalThis.EnruiCaptureBuild = Object.freeze(${JSON.stringify(metadata)});\n`;
entries['build-info.js'] = new TextEncoder().encode(buildInfo);
for (const script of manifest.content_scripts.flatMap((item) => item.js)) {
  if (!entries[script]) throw new Error(`Missing extension script: ${script}`);
}
const options = { mtime: new Date(1980, 0, 1), level: 9 };
const archive = zipSync(Object.fromEntries(Object.entries(entries).map(([name, bytes]) => [name, [bytes, options]])));
const unpacked = unzipSync(archive);
for (const [name, bytes] of Object.entries(entries)) {
  if (!Buffer.from(bytes).equals(Buffer.from(unpacked[name] || []))) throw new Error(`Archive verification failed: ${name}`);
}
await mkdir(resolve(frontend, 'public/downloads'), { recursive: true });
await writeFile(resolve(extension, 'build-info.js'), buildInfo);
await writeFile(resolve(extension, 'page-capture-main.js'), pageMain);
await writeFile(resolve(frontend, 'src/lib/videoCaptureBuild.json'), JSON.stringify(metadata, null, 2) + '\n');
await writeFile(resolve(frontend, 'public/downloads/enrui-video-capture-extension.zip'), archive);
console.log(`Video capture extension ${metadata.version}, build ${metadata.build}: verified ${Object.keys(entries).length} files`);
