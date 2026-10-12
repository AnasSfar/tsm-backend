from pathlib import Path
import shutil

root = Path('C:/Users/sfara/Documents/GitHub/tsm-frontend-redesign/frontend')
shutil.copyfile(Path(__file__).with_name('expanded.json'), root / 'src/data/collections.json')
p = root / 'src/pages/TopCollections.jsx'
s = p.read_text(encoding='utf-8')
s = s.replace('src={selected.image} alt=""', 'src={selected.image} style={{ objectPosition: selected.image_position }} alt=""')
s = s.replace('src={image} alt=""', 'src={image} style={{ objectPosition: row.image_position }} alt=""')
p.write_text(s, encoding='utf-8')
p = root / 'scripts/check-collections.mjs'
s = p.read_text(encoding='utf-8')
s += '''
const tv = catalog.find(c => c.slug === "taylors-version");
assert.equal(tv.tracks.length,103);
assert.ok(tv.tracks.every(t => t.title.includes("(Taylor's Version)")));
const vault = catalog.find(c => c.slug === "from-the-vault");
assert.equal(vault.tracks.length,27);
assert.ok(vault.tracks.every(t => t.title.includes("(From The Vault)")));
assert.equal(new Set(catalog.filter(c => c.kind === "track-numbers").map(c => c.image)).size,18);
assert.ok(catalog.every(c => c.image !== "/headers/all-eras.jpg"));
console.log("TV/Vault membership and individual collection artwork: PASS");
'''
p.write_text(s, encoding='utf-8')
