"""Read-only coverage declarations and command selection, using only stdlib."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PREREQUISITES = "always() && steps.runtime_prerequisites.outcome == 'success'"
JOB_CONDITION = (
    "always() && needs.retrospective_exact_starting_head_red.result == 'success' "
    "&& needs.secret-scan.result == 'success' "
    "&& needs.dependency-audit.result == 'success'"
)


def table(document, name, columns):
    """Read the deliberately small Markdown table format, not arbitrary YAML."""
    start, end = f"<!-- coverage-{name}:start -->", f"<!-- coverage-{name}:end -->"
    if document.count(start) != 1 or document.count(end) != 1:
        raise ValueError(f"Missing or duplicate coverage table: {name}")
    body = document.split(start)[1].split(end)[0].strip().splitlines()
    cells = [tuple(cell.strip().strip('`') for cell in line.strip('|').split('|'))
             for line in body]
    if len(cells) < 2 or cells[0] != columns or cells[1] != ('---',) * len(columns):
        raise ValueError(f"Invalid coverage table header: {name}")
    rows = cells[2:]
    if any(len(row) != len(columns) or not all(row) for row in rows):
        raise ValueError(f"Incomplete coverage row: {name}")
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate coverage row: {name}")
    return rows


def validate_commands(document, workflow, package):
    """Reject unsupported test-step syntax rather than guessing shell meaning."""
    declarations = table(document, 'ci', ('Step', 'Command', 'Scope', 'Condition'))
    if 'python -m unittest discover -s tests' not in {row[1] for row in declarations}:
        raise ValueError('Missing complete unittest discovery, including this contract')
    jobs = re.split(r'^  ([\w-]+):\s*$', workflow, flags=re.M)
    if jobs[1::2].count('validate') != 1:
        raise ValueError('Expected one validate job')
    job = jobs[2 + jobs[1::2].index('validate') * 2]
    if job.count('    steps:\n') != 1:
        raise ValueError('Expected one validate steps list')
    header, steps = job.split('    steps:\n')
    if re.findall(r'^    if: (.+)$', header, re.M) != [JOB_CONDITION]:
        raise ValueError('Validate job condition changed')
    if re.search(r'^\s+(?:defaults|continue-on-error|SQAG_VALIDATION_SCOPE):', header, re.M):
        raise ValueError('Unexpected job-wide execution or scope override')
    chunks = re.split(r'^      - name: ', steps, flags=re.M)
    if chunks[0].strip():
        raise ValueError('Unsupported validate step structure')
    blocks = {}
    for chunk in chunks[1:]:
        name = chunk.splitlines()[0]
        if name in blocks:
            raise ValueError('Duplicate validate step')
        blocks[name] = '      - name: ' + chunk.rstrip() + '\n'
    selected = {name for name, block in blocks.items()
                if re.search(r'\b(?:npm run playwright:|python -m unittest\b)', block)}
    if selected != {row[0] for row in declarations}:
        raise ValueError('CI test step inventory differs from the coverage matrix')
    prerequisite = '        id: runtime_prerequisites\n'
    if steps.count(prerequisite) != 1:
        raise ValueError('Missing or ambiguous browser prerequisite step')
    commands = set()
    for name, command, scope, condition in declarations:
        expected = f'      - name: {name}\n'
        if condition == 'prerequisites':
            if steps.index(prerequisite) > steps.index(f'      - name: {name}\n'):
                raise ValueError('Browser prerequisite must run before the browser suite')
            expected += f'        if: {PREREQUISITES}\n'
        elif condition != 'none':
            raise ValueError('Unknown step condition')
        if scope != 'none':
            expected += f'        env:\n          SQAG_VALIDATION_SCOPE: {scope}\n'
        expected += f'        run: {command}\n'
        if blocks.get(name) != expected:
            raise ValueError(f'CI command/scope/condition differs: {name}')
        commands.add(command)

    suites = table(document, 'browser', (
        'Suite', 'Package script', 'Script definition', 'CI command', 'Reason or limit'))
    required = {'app-full', 'app-recovery', 'app-terminal', 'alpha-full', 'pricing-startup'}
    if not required <= {row[0] for row in suites}:
        raise ValueError('Missing critical browser lifecycle declaration')
    scripts = {key: value for key, value in package['scripts'].items()
               if key.startswith('playwright:')}
    if set(scripts) != {row[1] for row in suites}:
        raise ValueError('Undeclared or removed package browser script')
    direct = set()
    for suite, script, definition, command, reason in suites:
        if scripts[script] != definition:
            raise ValueError(f'Package script definition differs: {suite}')
        if command != 'absent':
            if command not in commands or not command.startswith(f'npm run {script}'):
                raise ValueError(f'Browser CI selection differs: {suite}')
            direct.add(command)
        if len(reason) < 20:
            raise ValueError(f'Missing meaningful scope/omission reason: {suite}')
    if direct != {command for command in commands if command.startswith('npm run playwright:')}:
        raise ValueError('Browser commands missing from suite inventory')


def scope_skips(sources):
    """Inspect scope exclusions without importing application or test modules."""
    found = []
    references = 0
    for path, source in sources.items():
        if 'SQAG_VALIDATION_SCOPE' not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and node.args and
                    isinstance(node.args[0], ast.Constant) and
                    node.args[0].value == 'SQAG_VALIDATION_SCOPE' and
                    ast.unparse(node.func) in {'os.environ.get', 'os.getenv'}):
                references += 1
            elif (isinstance(node, ast.Subscript) and ast.unparse(node.value) == 'os.environ' and
                  isinstance(node.slice, ast.Constant) and
                  node.slice.value == 'SQAG_VALIDATION_SCOPE'):
                references += 1
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in (node for node in cls.body if isinstance(node, ast.FunctionDef)):
                for decorator in method.decorator_list:
                    if 'SQAG_VALIDATION_SCOPE' not in ast.unparse(decorator):
                        continue
                    if (not isinstance(decorator, ast.Call) or
                            ast.unparse(decorator.func) != 'unittest.skipIf' or
                            len(decorator.args) != 2 or decorator.keywords):
                        raise ValueError('Unsupported scope skip declaration')
                    predicate, reason = decorator.args
                    if not isinstance(reason, ast.Constant) or not isinstance(reason.value, str):
                        raise ValueError('Scope skip reason must be literal text')
                    found.append((f'{path}::{cls.name}.{method.name}',
                                  ast.unparse(predicate), reason.value))
    if references != len(found):
        raise ValueError('Unexplained validation scope reference outside declared skips')
    return sorted(found)


class CoverageMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = (ROOT / 'docs/testing-plan.md').read_text(encoding='utf-8')
        cls.workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        cls.package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))

    def test_declared_commands_match_workflow_and_package(self):
        validate_commands(self.document, self.workflow, self.package)

    def test_scope_skips_match_declared_predicates_and_reasons(self):
        declared = sorted(table(self.document, 'skips', ('Test', 'Predicate', 'Reason')))
        sources = {path.relative_to(ROOT).as_posix(): path.read_text(encoding='utf-8')
                   for path in (ROOT / 'tests').rglob('*.py')}
        self.assertEqual(scope_skips(sources), declared)

    def test_workflow_mutations_cannot_silently_narrow_or_suppress_coverage(self):
        mutations = (
            ('python -m unittest discover -s tests', 'python -m unittest tests.test_generate_quote'),
            ('python -m unittest discover -s tests', 'python -m unittest discover -s tests -k fresh'),
            ('run: npm run playwright:smoke\n', 'run: npm run playwright:smoke -- --pricing-startup\n'),
            ('run: npm run playwright:alpha-smoke\n', 'run: npm run playwright:alpha-smoke -- --pricing-startup\n'),
            ('--recovery-only', '--pricing-startup'),
            ('npm run playwright:ai-stress', 'npm run playwright:ai-stress || true'),
            ('      - name: Run Python tests\n', '      - name: Run Python tests\n        env:\n          SQAG_VALIDATION_SCOPE: narrowed\n'),
            ('--terminal-only', '--pricing-startup'),
            ('id: runtime_prerequisites', 'id: renamed_prerequisites'),
            (f'if: {PREREQUISITES}', 'if: false'),
            (f'if: {JOB_CONDITION}', 'if: false'),
            ('      - name: Run Python tests', '      - name: Run Python tests\n        continue-on-error: true'),
            ('      - name: Run app smoke test', '      - name: Run app smoke test\n        shell: custom'),
        )
        for old, new in mutations:
            with self.subTest(change=new):
                self.assertIn(old, self.workflow)
                with self.assertRaises(ValueError):
                    validate_commands(self.document, self.workflow.replace(old, new), self.package)

    def test_valid_multiline_job_overrides_reject_at_header_guard(self):
        for override in ('    defaults:\n      run:\n        shell: bash\n',
                         '    continue-on-error: true\n',
                         '    env:\n      SQAG_VALIDATION_SCOPE: narrowed\n'):
            with self.subTest(override=override):
                jobs = self.workflow.split('  validate:\n')
                self.assertEqual(len(jobs), 2)
                changed = jobs[0] + '  validate:\n' + jobs[1].replace('    steps:\n', override + '    steps:\n', 1)
                with self.assertRaisesRegex(ValueError, 'Unexpected job-wide execution or scope override'):
                    validate_commands(self.document, changed, self.package)

    def test_missing_ci_step_is_detected(self):
        changed = re.sub(r'      - name: Run minimum internal alpha browser smoke\n.*', '',
                         self.workflow, flags=re.S)
        with self.assertRaises(ValueError):
            validate_commands(self.document, changed, self.package)

    def test_undocumented_and_retargeted_browser_scripts_are_detected(self):
        for key, definition in (('playwright:new', 'node scripts/new.mjs'),
                                ('playwright:smoke', 'node scripts/playwright-smoke.mjs --pricing-startup')):
            package = json.loads(json.dumps(self.package))
            package['scripts'][key] = definition
            with self.subTest(script=key), self.assertRaises(ValueError):
                validate_commands(self.document, self.workflow, package)

    def test_missing_full_lifecycle_row_or_omission_reason_is_detected(self):
        changes = (
            re.sub(r'^\| app-full .*\n', '', self.document, flags=re.M),
            re.sub(r'^(\| alpha-full .*\|) [^|]+ \|$', r'\1 \|', self.document, flags=re.M),
        )
        for changed in changes:
            self.assertNotEqual(changed, self.document)
            with self.assertRaises(ValueError):
                validate_commands(changed, self.workflow, self.package)

    def test_scope_skip_addition_reason_change_and_unexplained_scope_are_detected(self):
        original = (
            'class ExampleTest:\n'
            "    @unittest.skipIf(os.environ.get('SQAG_VALIDATION_SCOPE') == 'narrowed', 'Reviewed reason')\n"
            '    def test_example(self): pass\n'
        )
        expected = [('tests/example.py::ExampleTest.test_example',
                     "os.environ.get('SQAG_VALIDATION_SCOPE') == 'narrowed'", 'Reviewed reason')]
        self.assertEqual(scope_skips({'tests/example.py': original}), expected)
        changed = original.replace('Reviewed reason', 'Unreviewed omission')
        self.assertNotEqual(scope_skips({'tests/example.py': changed}), expected)
        doubled = {'tests/example.py': original, 'tests/extra.py': original}
        self.assertEqual(len(scope_skips(doubled)), len(expected) + 1)
        with self.assertRaises(ValueError):
            scope_skips({'tests/extra.py': "scope = os.environ.get('SQAG_VALIDATION_SCOPE')\n"})


if __name__ == '__main__':
    unittest.main()
