const fs = require('fs');
const path = require('path');

const src = path.resolve(__dirname, '..', '..', 'static', 'v3');
const dst = path.resolve(__dirname, '..', 'app-assets', 'v3');

function rmrf(p) {
  if (fs.existsSync(p)) fs.rmSync(p, { recursive: true, force: true });
}

function copyDir(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    if (entry.name.startsWith('._') || entry.name === '.DS_Store') continue;
    const s = path.join(from, entry.name);
    const d = path.join(to, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

rmrf(path.resolve(__dirname, '..', 'app-assets'));
copyDir(src, dst);
console.log('[copy-assets] copied static/v3 → app-assets/v3');
