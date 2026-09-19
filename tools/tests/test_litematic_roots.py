#!/usr/bin/env python3
"""Tests for the folder roots both litematic apps use.
Run: python tools/tests/test_litematic_roots.py

The point of a root is that a dialog opens where the *work* is, not where some
unrelated dialog happened to leave the process. So the checks are all the same
shape: given where you were, where does the dialog open?

The settings file is redirected with FROSTLINE_TOOLS_HOME, so nothing here can
touch your real one.
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_TOOLS, 'apps'))
sys.path.insert(0, os.path.join(_TOOLS, 'cli'))

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


def same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def tree(tmp, *parts):
    path = os.path.join(tmp, *parts)
    os.makedirs(path, exist_ok=True)
    return path


# --- is_under ------------------------------------------------------------

def test_is_under(tmp):
    print('what counts as inside a root')
    import litematic_roots as R
    root = tree(tmp, 'schematics')
    inside = tree(tmp, 'schematics', 'tree', 'r1')
    outside = tree(tmp, 'data', 'frostline')
    check(R.is_under(root, root), 'the root is inside itself')
    check(R.is_under(inside, root), 'a folder below it is inside')
    check(not R.is_under(outside, root), 'a sibling folder is not')
    check(not R.is_under(os.path.join(tmp, 'schematics_other'), root),
          'a folder whose name merely starts the same is not inside')
    check(not R.is_under('', root) and not R.is_under(root, ''),
          'an empty path is never inside anything')
    check(R.is_under(root.upper(), root) == (sys.platform == 'win32'),
          'case is ignored exactly where the filesystem ignores it')


# --- anchor --------------------------------------------------------------

def test_anchor(tmp):
    print('where a dialog opens')
    import litematic_roots as R
    root = tree(tmp, 'sch')
    inside = tree(tmp, 'sch', 'tree', 'r1')
    outside = tree(tmp, 'data', 'structures', 'r1')
    roots = R.Roots({R.SCHEMATICS: root}, path=os.path.join(tmp, 'cfg.json'))

    check(same(roots.anchor(R.SCHEMATICS, inside), inside),
          'somewhere inside the root, you carry on from there')
    check(same(roots.anchor(R.SCHEMATICS, outside), root),
          'somewhere outside it, you are put back at the root')
    check(same(roots.anchor(R.SCHEMATICS, None), root),
          'with nowhere in mind, you get the root')
    check(same(roots.anchor(R.SCHEMATICS, os.path.join(tmp, 'gone')), root),
          'a folder that no longer exists falls back to the root')
    check(same(roots.anchor(R.SCHEMATICS,
                            os.path.join(inside, 'fallen_tree_1.litematic')), inside),
          'a file is read as the folder it is in')


def test_anchor_without_a_root(tmp):
    print('no root set')
    import litematic_roots as R
    somewhere = tree(tmp, 'anywhere')
    roots = R.Roots({}, path=os.path.join(tmp, 'cfg2.json'))
    check(same(roots.anchor(R.SCHEMATICS, somewhere), somewhere),
          'without a root, where you were is honoured')
    check(roots.anchor(R.SCHEMATICS, None) == os.getcwd(),
          'and with nothing at all, the working directory')


def test_anchor_ignores_a_missing_root(tmp):
    print('a root that has been moved or deleted')
    import litematic_roots as R
    somewhere = tree(tmp, 'still_here')
    roots = R.Roots({R.SCHEMATICS: os.path.join(tmp, 'deleted')},
                    path=os.path.join(tmp, 'cfg3.json'))
    check(roots.usable(R.SCHEMATICS) == '', 'a root that is not a folder is not usable')
    check(same(roots.anchor(R.SCHEMATICS, somewhere), somewhere),
          'and it does not drag the dialog to a folder that is not there')


def test_roots_are_independent(tmp):
    print('one root per kind')
    import litematic_roots as R
    sch = tree(tmp, 'two', 'sch')
    out = tree(tmp, 'two', 'out')
    roots = R.Roots({R.SCHEMATICS: sch, R.STRUCTURES: out},
                    path=os.path.join(tmp, 'cfg4.json'))
    check(same(roots.anchor(R.SCHEMATICS, out), sch),
          'the output folder does not pull the schematics dialog with it')
    check(same(roots.anchor(R.STRUCTURES, sch), out),
          'and the other way round -- this is the whole point')


# --- the settings file ---------------------------------------------------

def test_save_and_load(tmp):
    print('remembering across runs')
    import litematic_roots as R
    cfg = os.path.join(tmp, 'sub', 'roots.json')
    sch = tree(tmp, 'keepme')
    roots = R.Roots({}, path=cfg)
    roots.set(R.SCHEMATICS, sch)
    roots.save()
    check(os.path.isfile(cfg), 'the settings file is written, folder and all')

    again = R.Roots.load(cfg)
    check(same(again.get(R.SCHEMATICS), sch), 'and read back next time')
    check(os.path.isabs(again.get(R.SCHEMATICS)), 'stored as an absolute path')

    again.set(R.SCHEMATICS, '')
    again.save()
    cleared = R.Roots.load(cfg)
    check(cleared.get(R.SCHEMATICS) == '',
          'a root cleared on purpose stays cleared, rather than being re-detected')


def test_load_survives_rubbish(tmp):
    print('a settings file that is not usable')
    import litematic_roots as R
    cfg = os.path.join(tmp, 'broken.json')
    open(cfg, 'w', encoding='utf-8').write('this is not json{{')
    roots = R.Roots.load(cfg)
    check(roots.get(R.SCHEMATICS) == roots.default(R.SCHEMATICS),
          'a broken file falls back to the detected default, not to a crash')

    missing = R.Roots.load(os.path.join(tmp, 'never_written.json'))
    check(missing.get(R.SCHEMATICS) == missing.default(R.SCHEMATICS),
          'so does a file that was never written')


def test_defaults_point_at_this_pack():
    print('the detected defaults')
    import litematic_roots as R
    sch = R.detect_schematics()
    check(bool(sch) and os.path.isdir(sch) and os.path.basename(sch) == 'schematics',
          'the instance schematics/ folder is found by walking up')
    st = R.detect_structures()
    check(bool(st) and st.replace(os.sep, '/').endswith('data/frostline/structures'),
          'and the structure output folder inside the pack')


def test_config_path_is_outside_the_repo():
    print('where the settings live')
    import litematic_roots as R
    saved = os.environ.pop('FROSTLINE_TOOLS_HOME', None)
    try:
        path = R.config_path()
    finally:
        if saved is not None:
            os.environ['FROSTLINE_TOOLS_HOME'] = saved
    check(not R.is_under(path, _TOOLS),
          'per-user settings are not written into the pack')
    check(path.endswith('litematic_roots.json'), 'under a name that says what it is')


# --- the apps actually using it -----------------------------------------

class FakeDialogs(object):
    """Stands in for tkinter.filedialog and records what it was asked."""

    def __init__(self, answer=''):
        self.answer = answer
        self.calls = []

    def _record(self, **kw):
        self.calls.append(kw)
        return self.answer

    askopenfilename = askopenfilenames = askdirectory = asksaveasfilename = _record

    @property
    def last_dir(self):
        return self.calls[-1].get('initialdir')


def test_apps(tmp):
    print('the apps')
    try:
        import tkinter as tk
        import litematic_roots as R
        import litematic_convert as C
        import litematic_edit as E
        from test_litematic2nbt import state, region, write_litematic
        root_window = tk.Tk()
    except Exception as e:
        print('  skip  no tkinter or no display (%s)' % e)
        return

    sch = tree(tmp, 'app', 'schematics', 'tree')
    out = tree(tmp, 'app', 'data', 'structures')
    cfg = os.path.join(tmp, 'app', 'roots.json')
    path = os.path.join(sch, 'one.litematic')
    write_litematic(path, [('r', region([0, 0, 0], [2, 1, 1],
                                        [state('minecraft:air'), state('minecraft:stone')],
                                        {(0, 0, 0): 1}))])
    roots = R.Roots({R.SCHEMATICS: os.path.dirname(sch), R.STRUCTURES: out}, path=cfg)

    try:
        root_window.withdraw()

        edit = E.App(root_window)
        edit.roots = roots
        fake = FakeDialogs()
        E.filedialog = fake
        edit.open_file()
        check(same(fake.last_dir, os.path.dirname(sch)),
              'edit: with nothing open, Open starts at the schematics root')

        edit.load_path(path)
        edit.open_file()
        check(same(fake.last_dir, sch),
              'edit: with a file open, Open starts in that file\'s folder')
        edit.save_as()
        check(same(fake.last_dir, sch), 'edit: Save as starts there too')

        # The case this exists for: the file in hand is outside the root.
        stray = tree(tmp, 'app', 'elsewhere')
        shutil.copy(path, os.path.join(stray, 'one.litematic'))
        edit.load_path(os.path.join(stray, 'one.litematic'))
        edit.open_file()
        check(same(fake.last_dir, os.path.dirname(sch)),
              'edit: from outside the root, the dialog is pulled back to the root')

        convert = C.App(root_window)
        convert.roots = roots
        cfake = FakeDialogs()
        C.filedialog = cfake
        convert.add_files()
        check(same(cfake.last_dir, os.path.dirname(sch)),
              'convert: picking schematics starts at the schematics root')
        convert.add_folder()
        check(same(cfake.last_dir, os.path.dirname(sch)),
              'convert: so does picking a folder of them')

        convert.outdir.set(out)
        convert.pick_outdir()
        check(same(cfake.last_dir, out),
              'convert: the output dialog starts at the structure root')

        convert.outdir.set(sch)
        convert.pick_outdir()
        check(same(cfake.last_dir, out),
              'convert: an output path outside that root is pulled back to it')

        convert.add_paths([path])
        convert.add_files()
        check(same(cfake.last_dir, sch),
              'convert: once files are loaded, it starts in their folder -- still '
              'inside the root, so not overridden')
    finally:
        E.filedialog = E.tk and __import__('tkinter').filedialog
        C.filedialog = C.tk and __import__('tkinter').filedialog
        root_window.destroy()


def test_dialog(tmp):
    print('the Roots... dialog')
    try:
        import tkinter as tk
        import litematic_roots as R
        import rootsui
        window = tk.Tk()
    except Exception as e:
        print('  skip  no tkinter or no display (%s)' % e)
        return
    # The dialog is modal: it blocks in wait_window until the user answers.
    # Answer it from here instead.
    window.wait_window = lambda *_a: None
    cfg = os.path.join(tmp, 'dlg', 'roots.json')
    picked = tree(tmp, 'dlg', 'picked')
    roots = R.Roots({}, path=cfg)
    try:
        window.withdraw()

        dialog = rootsui.RootsDialog(window, roots, kinds=(R.SCHEMATICS, R.STRUCTURES))
        check(set(dialog.vars) == {R.SCHEMATICS, R.STRUCTURES},
              'a field per kind the caller asked for')
        dialog.vars[R.SCHEMATICS].set(picked)
        dialog._ok()
        check(dialog.saved and same(roots.get(R.SCHEMATICS), picked),
              'Save puts the typed folder into the roots')
        check(same(R.Roots.load(cfg).get(R.SCHEMATICS), picked),
              'and writes it to the settings file')

        dialog = rootsui.RootsDialog(window, roots, kinds=(R.SCHEMATICS,))
        dialog._default(R.SCHEMATICS)
        check(dialog.vars[R.SCHEMATICS].get() == R.detect_schematics(),
              'Default puts the detected folder back in the box')
        dialog._cancel()
        check(not dialog.saved and same(roots.get(R.SCHEMATICS), picked),
              'Cancel changes nothing')

        dialog = rootsui.RootsDialog(window, roots, kinds=(R.SCHEMATICS,))
        dialog.vars[R.SCHEMATICS].set(os.path.join(tmp, 'not_a_folder'))
        errors = []
        rootsui.messagebox.showerror = lambda *a, **kw: errors.append(a)
        dialog._ok()
        check(errors and not dialog.saved, 'a folder that does not exist is refused')
        check(same(roots.get(R.SCHEMATICS), picked), 'and the old root is kept')
        dialog._cancel()
    finally:
        window.destroy()


def main():
    tmp = tempfile.mkdtemp(prefix='literoots')
    os.environ['FROSTLINE_TOOLS_HOME'] = os.path.join(tmp, 'home')
    try:
        test_is_under(tmp)
        test_anchor(tmp)
        test_anchor_without_a_root(tmp)
        test_anchor_ignores_a_missing_root(tmp)
        test_roots_are_independent(tmp)
        test_save_and_load(tmp)
        test_load_survives_rubbish(tmp)
        test_defaults_point_at_this_pack()
        test_config_path_is_outside_the_repo()
        test_dialog(tmp)
        test_apps(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('')
    if FAILS:
        print('%d FAILED:' % len(FAILS))
        for f in FAILS:
            print('  ' + f)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
