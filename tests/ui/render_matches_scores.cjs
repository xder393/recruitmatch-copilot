// Minimal DOM surface for the real inline renderer; no browser or npm dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.style = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return []; }
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement() { return new Element(); },
  querySelectorAll() { return []; },
};
const context = vm.createContext({ document, sessionStorage: { getItem: () => null } });
const html = fs.readFileSync('web/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script, context);
for (const [semantic, expected] of [[80, '80'], [0, '0'], [null, '证据不足']]) {
  context.results = [{
    id: 'result', rank: 1, job_title: '工程师', total_score: 0.56, rule_score: 0.5,
    semantic_score: semantic, dimension_scores: {}, evidence: [], missing_items: [], uncertain_items: [],
  }];
  vm.runInContext('renderMatches(results)', context);
  const [card] = elements.get('match-results').children;
  assert.equal(card.children[0].children[1].textContent, '56%');
  assert.equal(card.children[1].textContent, `规则分 50 · 语义补充分 ${expected} · 最终分 56`);
}
console.log('renderMatches: 80→80, 0→0, NULL→证据不足; rule/final scale verified');
