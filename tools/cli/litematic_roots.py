"""Where the file dialogs start. Shared by both litematic apps.

A file dialog remembers the last folder *the process* used, not the last folder
you used for this kind of thing. Convert ten schematics into
`data/frostline/structures/r1/` and the next "open a schematic" dialog opens in
`structures/r1`, miles from the schematics. So the apps do not let the dialog
choose: each kind of thing has a **root**, and a dialog for that kind opens
inside its root or at the root itself, never anywhere else.

    schematics  where the .litematic files live
    structures  where converted .nbt files go (convert only)

`anchor()` is the whole idea: it takes where you *were* and returns where the
dialog should open. Inside the root, you carry on from there; outside it, you
are put back at the root and navigate down from there.

Roots are per-user, not per-pack: they are kept outside the repo, in the usual
per-user config spot, so a checkout never carries one person's paths.
"""
import json
import os
import sys

SCHEMATICS = 'schematics'
STRUCTURES = 'structures'

LABELS = {
    SCHEMATICS: 'Schematics root',
    STRUCTURES: 'Structure output root',
}
BLURB = {
    SCHEMATICS: 'where your .litematic files live -- every "open a schematic" '
                'dialog starts here',
    STRUCTURES: 'where converted .nbt files go -- every output folder dialog '
                'starts here',
}

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.dirname(_HERE)
_PACK = os.path.dirname(_TOOLS)


def config_path():
    """The settings file. `FROSTLINE_TOOLS_HOME` overrides it, for tests."""
    home = os.environ.get('FROSTLINE_TOOLS_HOME')
    if not home:
        if sys.platform == 'win32':
            base = os.environ.get('APPDATA') or os.path.expanduser('~')
        else:
            base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
        home = os.path.join(base, 'frostline-tools')
    return os.path.join(home, 'litematic_roots.json')


def detect_schematics():
    """The instance `schematics/` folder, found by walking up from the pack."""
    d = _TOOLS
    for _ in range(10):
        cand = os.path.join(d, 'schematics')
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ''


def detect_structures():
    cand = os.path.join(_PACK, 'data', 'frostline', 'structures')
    return cand if os.path.isdir(cand) else ''


DETECT = {SCHEMATICS: detect_schematics, STRUCTURES: detect_structures}


def is_under(path, root):
    """Is `path` the root itself or somewhere inside it?

    Compared case-insensitively on Windows, and a different drive is simply
    "not under" rather than an error.
    """
    if not path or not root:
        return False
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(root))
    except ValueError:
        return False
    if a == b:
        return True
    return a.startswith(b.rstrip(os.sep) + os.sep)


class Roots(object):
    """The roots, and the one question the apps ask them: where do I open?"""

    def __init__(self, values=None, path=None):
        self.path = path or config_path()
        self.values = {}
        for kind in (SCHEMATICS, STRUCTURES):
            self.values[kind] = (values or {}).get(kind) or ''

    @classmethod
    def load(cls, path=None):
        """Read the settings file. A missing or broken one falls back to the
        detected defaults rather than to nothing."""
        path = path or config_path()
        values = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                values = {k: v for k, v in loaded.items() if isinstance(v, str)}
        except (OSError, ValueError):
            values = {}
        roots = cls(values, path)
        # A key that is *absent* has never been set, so detect a sensible one. A
        # key that is present but empty was cleared on purpose: leave it empty,
        # or "no root" would be impossible to keep.
        for kind in (SCHEMATICS, STRUCTURES):
            if kind not in values:
                roots.values[kind] = roots.default(kind)
        return roots

    def save(self):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.values, f, indent=1, sort_keys=True)
        return self.path

    # -- values
    def default(self, kind):
        return DETECT[kind]()

    def get(self, kind):
        return self.values.get(kind) or ''

    def set(self, kind, path):
        self.values[kind] = os.path.abspath(path) if path else ''

    def usable(self, kind):
        """The root, if it is set and still exists."""
        root = self.get(kind)
        return root if root and os.path.isdir(root) else ''

    # -- the point of all this
    def anchor(self, kind, preferred=None):
        """Where a dialog for `kind` should open.

        `preferred` is where you were -- the folder of the file in hand, or what
        an output box already says. It is honoured only while it is inside the
        root; outside it, you get the root, which is what stops a dialog landing
        in whatever folder the last unrelated dialog happened to use.
        """
        root = self.usable(kind)
        if preferred:
            preferred = os.path.abspath(preferred)
            if not os.path.isdir(preferred):
                preferred = os.path.dirname(preferred)
            if os.path.isdir(preferred) and (not root or is_under(preferred, root)):
                return preferred
        if root:
            return root
        return preferred if preferred and os.path.isdir(preferred) else os.getcwd()
