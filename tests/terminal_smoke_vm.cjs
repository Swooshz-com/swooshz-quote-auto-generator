// Execute the maintained terminal path. Only browser/server I/O is synthetic.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');

async function terminalRun(source, fault = '') {
  // Python's text-mode stdin uses CRLF on Windows; source slicing is LF-based.
  source = source.replace(/\r\n?/g, '\n');
  const slice = (start, end) => {
    const left = source.indexOf(start), right = source.indexOf(end, left + start.length);
    assert(left >= 0 && right > left, 'Missing maintained terminal function');
    return source.slice(left, right);
  };
  let helper = slice('async function verifyFreshGenerationTerminalStates(', 'async function verifyExpiredQuoteJobsDoNotResume(');
  if (['duplicate', 'polls', 'navigation'].includes(fault)) {
    const marker = '        throw new Error("Live terminal recovery identity differs for " + status);\n      }\n';
    assert.equal(helper.split(marker).length - 1, 1);
    helper = helper.replace(marker, marker + ({duplicate: '      starts++;\n', polls: '      polls = 1;\n',
      navigation: '      navigations++;\n'})[fault]);
  }
  const runner = slice('async function runG3PricingScope()', 'function requireNoQuoteSessionRateLimits(');
  const guard = slice('function requireNoQuoteSessionRateLimits(', 'async function main()');
  const main = slice('async function main()', '  let serverInfo = null;') + '}';
  const receipts = [], checks = [], cleanups = [], lifecycle = [];
  const statuses = ['needs_review', 'blocked', 'failed', 'interrupted'];
  const scope = {args: ['--terminal-only'], options: {port: 1, host: '127.0.0.1'},
    baseUrl: 'http://127.0.0.1:1', root: 'synthetic', process: {env: {}}, URL,
    path: require('node:path'), os: {tmpdir: () => 'synthetic-temp'},
    fs: {mkdtemp: async () => 'synthetic-temp', mkdir: async () => {}, rm: async () => cleanups.push('files')},
    freshBrowserRun: async () => 'synthetic-run', unusedLoopbackPort: async () => 2,
    startServer: () => {lifecycle.push('server'); return {server: {}, output: []};},
    stopServer: async () => cleanups.push('server'), waitForHealth: async () => true,
    chromium: {launch: async () => ({close: async () => cleanups.push('browser')})},
    saveG3PricingReference: async () => ({ok: true, data: {status: 'saved'}}), g3PricingItems: () => [],
    prepareTerminalQuote: async () => {}, currentQuoteSessionId: async () => 'owned-session',
    // If the terminal branch disappears, run the rest safely, then reject the missing receipts.
    verifyPricingReferenceImmediateRefresh: async () => {}, verifyRun030DraftBoundary: async () => {},
    console: {log: value => {const data = JSON.parse(value); if (data.status === 'live_terminal_pass') receipts.push(data);}},
  };
  scope.SyntheticBrowserBoundary = class {
    async newContext() {
      const context = new EventEmitter();
      context.newPage = async () => {
        const page = new EventEmitter(), routes = new Map();
        let active = null, saved = null, terminal = false, count = 0;
        const status = scope.options.terminalStatus;
        const job = 'job-' + status;
        const pollUrl = () => scope.baseUrl + '/api/jobs/' + job;
        const consoleError = (url = pollUrl(), text = 'Failed to load resource: net::ERR_FAILED', type = 'error') =>
          page.emit('console', {type: () => type, text: () => text, location: () => ({url})});
        page.mainFrame = () => page;
        page.goto = async () => {
          count++;
          if (fault === 'pageerror') page.emit('pageerror', new Error('Synthetic uncaught page error'));
        };
        page.route = async (pattern, handler) => routes.set(pattern, handler);
        page.unroute = async pattern => {assert(routes.delete(pattern)); cleanups.push('route');};
        async function poll() {
          const handler = routes.get('**/api/jobs/job-*'); assert(handler, 'Missing terminal poll route');
          await handler({request: () => ({url: pollUrl}),
            fallback: () => assert.fail('Unexpected terminal job'), abort: async () => {
              terminal = true;
              // Real Chromium emits this for route.abort("failed"). Keep it in the happy path.
              consoleError();
              if (fault === 'other-err-failed') consoleError(scope.baseUrl + '/api/unrelated');
              if (fault === 'other-job-err-failed') consoleError(scope.baseUrl + '/api/jobs/job-unrelated');
              if (fault === 'other-origin-err-failed') consoleError('http://localhost:2/api/jobs/' + job);
              if (fault === 'missing-location') consoleError('');
              if (fault === 'console-error') consoleError(pollUrl(), 'Synthetic unrelated console error');
              if (fault === 'changed-err-failed') consoleError(pollUrl(), 'Failed to load resource: net::ERR_FAILED extra');
              if (fault === 'console-warning') consoleError(pollUrl(), undefined, 'warning');
              if (fault === 'interrupted-pageerror') page.emit('pageerror', new Error('Synthetic interrupted page error'));
            },
            fulfill: async ({json}) => {
              terminal = json.status !== 'running';
              if (terminal) {
                saved = null;
                if (fault === 'non-interrupted-err-failed') consoleError();
              } else if (status === 'interrupted' && fault === 'before-abort-err-failed') consoleError();
            }});
        }
        page.locator = selector => ({
          innerText: async () => {checks.push('overlay-title'); return 'Regenerating Excel';},
          click: async () => {
            assert.equal(selector, '#sideDownloadButton:not([disabled])');
            if (fault === 'dispatch') throw new Error('Synthetic dispatch failure');
            const handler = routes.get('**/api/jobs'); assert(handler, 'Missing terminal start route');
            await handler({request: () => ({method: () => 'POST', postDataJSON: () => ({type: 'generate', job_id: job,
              payload: {quote_session: {session_id: 'owned-session'}, line_items: [{description: 'Synthetic row'}]}})}),
              fulfill: async ({status: code}) => {assert.equal(code, 202); active = saved = {id: job};}});
          },
          waitFor: async ({state}) => {
            checks.push(selector + ':' + state);
            if (selector === '#excelGeneratingModal' && state === 'hidden') {
              await poll(); assert(terminal, 'Terminal poll did not finish');
              if (fault === '429') context.emit('response', {status: () => 429,
                url: () => 'http://127.0.0.1/api/quote-sessions/owned-session'});
              if (fault === 'identity') saved = status === 'interrupted' ? null : {id: 'wrong-job'};
            }
          },
        });
        page.waitForResponse = async () => {await poll();};
        page.waitForFunction = async (_fn, expected) => {
          if (typeof expected === 'string') {
            checks.push('status:' + expected);
            assert.equal(expected, {needs_review: 'Needs pricing review', blocked: 'blocked', failed: 'failed',
              interrupted: 'Connection interrupted'}[status]);
          }
        };
        page.evaluate = async fn => {
          const text = String(fn);
          if (text.includes('await confirmBasis()')) return {panel: 'output'};
          if (text.includes('localStorage.getItem')) return saved;
          if (text.includes('hideExcelGeneratingModal')) {
            active = saved = null; cleanups.push('job');
            assert.equal(count, 2, 'Unexpected terminal navigation');
          }
        };
        return page;
      };
      return context;
    }
    assertClean() {checks.push('boundary');}
  };
  vm.runInNewContext(helper + runner + guard + main + '\nglobalThis.subject = main;', scope);
  try {await scope.subject();} catch (error) {
    assert.equal(scope.options.port, 1); assert.equal(scope.baseUrl, 'http://127.0.0.1:1');
    assert(cleanups.includes('browser') && cleanups.includes('server') && cleanups.includes('files'),
      'Terminal rejection lost lifecycle cleanup');
    if (fault !== 'pageerror') assert(cleanups.includes('job'), 'Terminal rejection lost helper cleanup');
    throw error;
  }
  assert.equal(scope.options.port, 1); assert.equal(scope.baseUrl, 'http://127.0.0.1:1');
  assert.deepEqual(receipts.map(item => item.terminal), statuses, 'Missing ordered terminal case receipts');
  assert.equal(lifecycle.length, 4, 'Each terminal case needs its own server');
  for (const receipt of receipts) {
    assert.equal(receipt.starts, 1); assert(receipt.polls >= 2); assert.equal(receipt.navigations, 0);
  }
  for (const kind of ['browser', 'server', 'files', 'job']) assert.equal(cleanups.filter(x => x === kind).length, 4);
  assert.equal(cleanups.filter(x => x === 'route').length, 8);
  assert.equal(checks.filter(x => x === 'overlay-title').length, 4);
  assert.equal(checks.filter(x => x.startsWith('status:')).length, 4);
  assert.equal(checks.filter(x => x === '#excelGeneratingModal:visible').length, 4);
  assert.equal(checks.filter(x => x === '#excelGeneratingModal:hidden').length, 4);
}

module.exports = {terminalRun};
