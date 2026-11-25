// Runs the Quote Templates editor model from webapp/static/app.js in a VM and prints what each edit did.
// Only the block between the "quote template model" markers runs: it is plain data code with no screen,
// DOM or network use, so nothing is stubbed. Input (stdin): { definition, scenarios: [{ name, ops }] },
// where each op is [method, ...args] and an argument { type } means that block, { type, column } that
// table column. Output (stdout): one result per scenario, with each op's return value, the edited
// definition, the model's problems and its page-fit numbers.
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("webapp/static/app.js", "utf8").replace(/\r\n/g, "\n");
const model = source.match(/\/\/ --- quote template model: start ---\n([^]*?)\/\/ --- quote template model: end ---/);
if (!model) throw new Error("Quote template model markers not found in webapp/static/app.js.");
const ctx = { inputText: fs.readFileSync(0, "utf8"), console };
vm.createContext(ctx);
vm.runInContext(model[1] + `
const input = JSON.parse(inputText);
const results = input.scenarios.map((scenario) => {
  const draft = new QuoteTemplateDraft(input.definition);
  const resolve = (arg) => {
    if (!arg || typeof arg !== "object" || Array.isArray(arg) || !arg.type) return arg;
    const block = draft.firstOfType(arg.type);
    if (!block) throw new Error(scenario.name + ": no " + arg.type + " block");
    if (!arg.column) return block;
    const column = block.columns.find((item) => item.key === arg.column);
    if (!column) throw new Error(scenario.name + ": no " + arg.column + " column");
    return column;
  };
  const returned = scenario.ops.map(([method, ...args]) => {
    if (typeof draft[method] !== "function") throw new Error(scenario.name + ": unknown method " + method);
    const value = draft[method](...args.map(resolve));
    return value === undefined ? null : value;
  });
  return {
    name: scenario.name,
    returned,
    definition: draft.toDefinition(draft.t.name),
    problems: draft.problems(),
    bodyOrder: draft.bodyBlocks().map((block) => block.type),
    keysFollowBlocks: draft.t.blocks.every((block, index) => draft.block(draft.keys[index]) === block),
    printScale: qtPrintScale(draft.t),
    printableRows: qtPrintableRows(draft.t),
  };
});
console.log(JSON.stringify(results));
`, ctx);
