from pathlib import Path
import shutil

ROOT = Path('C:/Users/sfara/Documents/GitHub/tsm-frontend-redesign/frontend')
assert ROOT.resolve().name == 'frontend' and ROOT.parent.name == 'tsm-frontend-redesign'
shutil.copyfile(Path(__file__).with_name('expanded.json'), ROOT / 'src/data/collections.json')
p = ROOT / 'src/utils/collectionStats.mjs'
s = p.read_text(encoding='utf-8')
s = s.replace('const issues = [];', 'const issues = (collection.membership_issues || []).map(reason => ({ reason }));')
p.write_text(s, encoding='utf-8')
p = ROOT / 'src/pages/TopCollections.jsx'
s = p.read_text(encoding='utf-8').replace('`${row.title} ${row.description}`.toLowerCase()', 'row.title.toLowerCase()')
p.write_text(s, encoding='utf-8')
p = ROOT / 'scripts/check-collections.mjs'
s = p.read_text(encoding='utf-8')
s = s.replace('catalog.at(-1)', 'catalog.find(c => c.slug === "songwriting")')
s = s.replace('for (const c of catalog.slice(0,-1)) assert.ok(collectionStats(c,"2026-10-09",current,previous).ready,c.slug);', '''for (const c of catalog) {
 const result = collectionStats(c,"2026-10-09",current,previous);
 if (c.slug === "songwriting" || c.membership_issues?.length) {
  assert.equal(result.ready,false,c.slug);
  assert.equal(result.daily,null); assert.equal(result.total,null);
 } else {
  assert.ok(result.ready,c.slug);
  assert.equal(result.daily,c.tracks.reduce((sum,t)=>sum+current[t.track_id].d,0));
  assert.equal(result.total,c.tracks.reduce((sum,t)=>sum+current[t.track_id].s,0));
 }
 assert.equal(new Set(c.tracks.map(t=>t.track_id)).size,c.tracks.length,c.slug);
}
assert.equal(new Set(catalog.map(c=>c.slug)).size,catalog.length);
for (let index=1;index<=18;index++) {
 const c=catalog.find(c=>c.slug===`track-${index}`);
 assert.ok(c); assert.ok(c.tracks.every(t=>!t.title.includes("From The Vault")));
}
assert.equal(catalog.find(c=>c.slug==="track-1").tracks.length,12);
assert.equal(catalog.find(c=>c.slug==="track-18").tracks.length,1);
assert.equal(catalog.find(c=>c.slug==="billboard-hot-100-number-ones").tracks.length,16);
assert.equal(catalog.find(c=>c.slug==="billboard-hot-100-top-tens").tracks.length,74);
assert.equal(collectionStats({...sample,membership_issues:["unresolved identity"]},"2026-10-09",current,previous).ready,false);''')
p.write_text(s, encoding='utf-8')
print('Updated membership, identity guard, title search and integrity checks in redesign worktree.')
