#!/usr/bin/env node
const { execSync } = require('child_process');

const issues = JSON.parse(execSync('gh issue list --state open --json number,title,body,labels --jq \'[.[] | {number, title, labels: [.labels[].name]}]\'', { encoding: 'utf8' }));
const prs = JSON.parse(execSync('gh pr list --state open --json number,body --jq \'[.[] | {number, body}]\'', { encoding: 'utf8' }));

const prLinked = new Set();
prs.forEach(pr => {
  const matches = (pr.body || '').match(/#(\d+)/g) || [];
  matches.forEach(m => prLinked.add(parseInt(m.slice(1))));
});

const unlinked = issues.filter(i => !prLinked.has(i.number));
const critical = unlinked.filter(i => i.labels.includes('critical') || i.title.startsWith('fix:'));
const quick = unlinked.filter(i => i.labels.includes('test') || i.labels.includes('docs') || i.title.startsWith('[fix]'));
const rest = unlinked.filter(i => !critical.includes(i) && !quick.includes(i));

console.log('WAVE 1 (critical + quick):');
[...critical, ...quick].slice(0, 8).forEach(i => console.log('  #' + i.number + ' [' + i.labels.join(',') + '] ' + i.title.substring(0,80)));
console.log('\nWAVE 2+:');
rest.slice(0, 8).forEach(i => console.log('  #' + i.number + ' [' + i.labels.join(',') + '] ' + i.title.substring(0,80)));
console.log('\nTotal unlinked: ' + unlinked.length);
