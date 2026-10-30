# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict result contract for the pinned three-suite native qualification."""
import json
import re
import xml.etree.ElementTree as ET

SUITES = {
    'kwin-testInternalWindow': 'KWin::InternalWindowTest',
    'kwin-testOffscreenQuickView': 'KWin::OffscreenQuickViewTest',
    'kwin-testTabBox': 'TabBoxTest',
}

# This inventory belongs to the pinned v6.7.5 patch set, including data rows.
CASES = {
    'KWin::InternalWindowTest': (
        'initTestCase()', 'testGeometry()', 'testEnterLeave()', 'testPointerPressRelease()',
        'testPointerAxis()', 'testTouch()', 'testOpacity()', 'testMove()',
        'testSkipCloseAnimation(set)', 'testSkipCloseAnimation(not set)',
        'testModifierClickUnrestrictedMove()', 'testModifierScroll()', 'testPopup()',
        'testScale()', 'testEffectWindow()', 'testReentrantMoveResize()',
        'testDismissPopup()', 'cleanupTestCase()',
    ),
    'KWin::OffscreenQuickViewTest': (
        'initTestCase()', 'testOpacity(zero)', 'testOpacity(fractional)', 'testOpacity(opaque)',
        'testResynchronizeOpacity()', 'testOpaqueRegion(zero)', 'testOpaqueRegion(fractional)',
        'testOpaqueRegion(opaque)', 'testOpaqueRegion(alpha-zero)',
        'testOpaqueRegion(alpha-fractional)', 'testOpaqueRegion(alpha-opaque)',
        'testRepaint()', 'testComposition()', 'testEffectFrame()', 'cleanupTestCase()',
    ),
    'TabBoxTest': (
        'initTestCase()', 'testMoveForward()', 'testMoveBackward()',
        'testReleaseActivationModifierWithOtherModifierHeld()', 'testEscapeReleasesGrab()',
        'testClickOutsideReleasesGrab()', 'testCapsLock()', 'testKeyboardFocus()',
        'testActiveClientOutsideModel()', 'cleanupTestCase()',
    ),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON field: ' + key)
        result[key] = value
    return result


def verify_registration(text):
    value = json.loads(text, object_pairs_hook=object_pairs)
    require(isinstance(value, dict) and isinstance(value.get('tests'), list), 'invalid CTest registration')
    tests = value['tests']
    require(all(isinstance(test, dict) and isinstance(test.get('name'), str) for test in tests),
            'invalid registered test record')
    require(len(tests) == 3 and {test.get('name') for test in tests} == set(SUITES),
            'missing, duplicate or unexpected registered suite')
    for test in tests:
        require(test.get('command') == ['/usr/bin/dbus-run-session',
                                        '/tmp/build/bin/' + test['name'].removeprefix('kwin-')],
                'CTest command differs from the qualified D-Bus wrapper')
        properties = test.get('properties', [])
        require(isinstance(properties, list) and all(isinstance(prop, dict) for prop in properties),
                'invalid test properties')
        require(not any(prop.get('name') in {'DISABLED', 'WILL_FAIL'} and prop.get('value')
                        for prop in properties), 'disabled or inverted test')
        working = [prop.get('value') for prop in properties if prop.get('name') == 'WORKING_DIRECTORY']
        require(working == ['/tmp/build/autotests/integration'], 'unexpected CTest working directory')


def verify_tests(junit, log, registration):
    for value in [junit, log, registration]:
        require(isinstance(value, str) and len(value.encode()) <= 5 * 1024**2, 'oversized or nontext result')
    verify_registration(registration)
    require(not re.search(r'<!\s*(?:DOCTYPE|ENTITY)', junit, re.IGNORECASE), 'XML declarations forbidden')
    try:
        root = ET.fromstring(junit)
    except ET.ParseError as error:
        raise ValueError('invalid JUnit XML') from error
    require(root.tag == 'testsuite' and int(root.get('tests', '-1')) == 3, 'invalid JUnit root')
    require(all(int(root.get(key, '0')) == 0 for key in ['failures', 'errors', 'skipped', 'disabled']),
            'JUnit reports failed or skipped tests')
    tests = root.findall('testcase')
    require(len(tests) == 3 and {test.get('name') for test in tests} == set(SUITES),
            'missing, duplicate or unexpected JUnit suite')
    require(all(test.get('status') == 'run' and
                not any(test.find(key) is not None for key in ['failure', 'error', 'skipped'])
                for test in tests), 'JUnit suite did not pass')
    log = log.replace('\r\n', '\n')
    require(not re.search(r'^(?:FAIL!|SKIP|XFAIL|XPASS|BFAIL|BPASS|BXFAIL|BXPASS)\s*:', log, re.MULTILINE),
            'explicit non-passing QtTest outcome')
    require(len(re.findall(r'^\*{9} Start testing of ', log, re.MULTILINE)) == 3,
            'unexpected QtTest suite count')
    totals = r'^Totals: (\d+) passed, (\d+) failed, (\d+) skipped, (\d+) blacklisted'
    require(len(re.findall(totals, log, re.MULTILINE)) == 3, 'unexpected QtTest totals count')
    measured = {}
    renderers = []
    for suite, expected in CASES.items():
        pattern = (r'^' + re.escape('********* Start testing of ' + suite + ' *********') + r'$'
                   + r'(.*?)^' + re.escape('********* Finished testing of ' + suite + ' *********') + r'$')
        sections = re.findall(pattern, log, re.MULTILINE | re.DOTALL)
        require(len(sections) == 1, 'missing or repeated QtTest suite: ' + suite)
        section = sections[0]
        rows = re.findall(totals, section, re.MULTILINE)
        require(len(rows) == 1 and tuple(map(int, rows[0])) == (len(expected), 0, 0, 0),
                'QtTest totals differ: ' + suite)
        passed = re.findall(r'^PASS\s+: ' + re.escape(suite) + r'::([^\n]+?)\s*$', section, re.MULTILINE)
        require(len(passed) == len(expected) and set(passed) == set(expected),
                'missing, repeated or unexpected QtTest case: ' + suite)
        measured[suite] = len(passed)
        if suite == 'KWin::OffscreenQuickViewTest':
            renderers = re.findall(r'qt\.rhi\.general: OpenGL VENDOR:[^\n]*RENDERER: '
                                   r'(llvmpipe[^\n]*)', section)
            require(renderers, 'no actual offscreen llvmpipe evidence')
    return {'suites': 3, 'qt_test_passed': sum(measured.values()), 'failed': 0, 'skipped': 0,
            'blacklisted': 0, 'cases_by_suite': measured, 'renderer_records': sorted(set(renderers))}
