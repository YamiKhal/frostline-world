#!/usr/bin/env python3
"""Point-and-click front end for litematic2nbt.

Run: python tools/apps/litematic_convert.py  (or litematic2nbt.py --gui)

Add schematics, look at every block state and entity they contain, untick the
ones you do not want, pick where the .nbt goes, convert. Nothing is dropped
unless you untick it -- block NBT included, whatever mod put it there.

Tkinter only -- no install, no dependencies.
"""
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'cli'))   # litematic2nbt, nbtio
import litematic2nbt as L
import litematic_roots as R

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import rootsui
except ImportError as e:  # pragma: no cover - only on a python built without tk
    tk = None
    _IMPORT_ERROR = e

TICK = '✓'
UNTICK = '·'


class CheckTree(ttk.Frame):
    """A Treeview used as a checklist. Click the tick column, or hit space."""

    def __init__(self, master, heading, value_heading='count'):
        ttk.Frame.__init__(self, master)
        self.tree = ttk.Treeview(self, columns=('on', 'name', 'n'), show='headings',
                                 selectmode='extended', height=9)
        self.tree.heading('on', text='')
        self.tree.heading('name', text=heading)
        self.tree.heading('n', text=value_heading)
        self.tree.column('on', width=28, anchor='center', stretch=False)
        self.tree.column('name', width=300, anchor='w')
        self.tree.column('n', width=70, anchor='e', stretch=False)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(buttons, text='All', width=5,
                   command=lambda: self.set_all(True)).pack(side='left')
        ttk.Button(buttons, text='None', width=6,
                   command=lambda: self.set_all(False)).pack(side='left', padx=(4, 0))
        ttk.Button(buttons, text='Invert', width=7,
                   command=self.invert).pack(side='left', padx=(4, 0))

        self.state = {}         # item name -> bool
        self.on_change = None
        self.tree.bind('<Button-1>', self._click)
        self.tree.bind('<space>', self._space)
        self.tree.bind('<Return>', self._space)

    # -- data
    def load(self, rows):
        """rows: [(name, count)], preserving previous ticks for names still present."""
        previous = dict(self.state)
        self.tree.delete(*self.tree.get_children())
        self.state = {}
        for name, n in rows:
            on = previous.get(name, True)
            self.state[name] = on
            self.tree.insert('', 'end', iid=name,
                             values=(TICK if on else UNTICK, name, n))

    def checked(self):
        return {k for k, v in self.state.items() if v}

    def unchecked(self):
        return {k for k, v in self.state.items() if not v}

    # -- interaction
    def _refresh(self, name):
        self.tree.set(name, 'on', TICK if self.state[name] else UNTICK)

    def toggle(self, names):
        for name in names:
            if name in self.state:
                self.state[name] = not self.state[name]
                self._refresh(name)
        if self.on_change:
            self.on_change()

    def set_all(self, value):
        for name in self.state:
            self.state[name] = value
            self._refresh(name)
        if self.on_change:
            self.on_change()

    def invert(self):
        self.toggle(list(self.state))

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) != 'cell':
            return None
        name = self.tree.identify_row(event.y)
        if not name:
            return None
        # Clicking the tick column toggles just that row; elsewhere selects.
        if self.tree.identify_column(event.x) == '#1':
            self.toggle([name])
            return 'break'
        return None

    def _space(self, _event):
        self.toggle(self.tree.selection())
        return 'break'


class App(ttk.Frame):
    def __init__(self, master, outdir=None):
        ttk.Frame.__init__(self, master, padding=8)
        self.grid(row=0, column=0, sticky='nsew')
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)

        self.scenes = {}        # path -> Scene or the exception that stopped it
        self.roots = R.Roots.load()
        self.master_window = master

        self._build_files()
        self._build_lists()
        self._build_options(outdir)
        self._build_actions()
        self._build_log()
        self.refresh()

    # -- widgets
    def _build_files(self):
        box = ttk.LabelFrame(self, text='Schematics', padding=6)
        box.grid(row=0, column=0, sticky='nsew')
        box.columnconfigure(0, weight=1)

        self.files = ttk.Treeview(box, columns=('name', 'size', 'path'),
                                  show='headings', height=5)
        for key, text, width, anchor in (('name', 'file', 200, 'w'),
                                         ('size', 'size', 90, 'w'),
                                         ('path', 'folder', 340, 'w')):
            self.files.heading(key, text=text)
            self.files.column(key, width=width, anchor=anchor)
        bar = ttk.Scrollbar(box, orient='vertical', command=self.files.yview)
        self.files.configure(yscrollcommand=bar.set)
        self.files.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        box.rowconfigure(0, weight=1)

        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(row, text='Add files...', command=self.add_files).pack(side='left')
        ttk.Button(row, text='Add folder...', command=self.add_folder).pack(side='left',
                                                                            padx=(6, 0))
        ttk.Button(row, text='Remove', command=self.remove_selected).pack(side='left',
                                                                          padx=(6, 0))
        ttk.Button(row, text='Clear', command=self.clear_files).pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Roots...', command=self.edit_roots).pack(side='right')

    def _build_lists(self):
        pane = ttk.Frame(self)
        pane.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        pane.columnconfigure(0, weight=1)
        pane.columnconfigure(1, weight=1)
        pane.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(pane, text='Blocks  (unticked become structure_void)',
                              padding=6)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.blocks = CheckTree(left, 'block state', 'cells')
        self.blocks.grid(row=0, column=0, sticky='nsew')
        self.blocks.on_change = self.update_summary

        right = ttk.LabelFrame(pane, text='Entities  (unticked are dropped)', padding=6)
        right.grid(row=0, column=1, sticky='nsew', padx=(4, 0))
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.entities = CheckTree(right, 'entity type', 'count')
        self.entities.grid(row=0, column=0, sticky='nsew')
        self.entities.on_change = self.update_summary

    def _build_options(self, outdir):
        box = ttk.LabelFrame(self, text='Options', padding=6)
        box.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        box.columnconfigure(1, weight=1)

        self.air = tk.StringVar(value='keep')
        row = ttk.Frame(box)
        row.grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(row, text='Plain air:').pack(side='left')
        for value, text in (('keep', 'keep (carves terrain)'),
                            ('void', 'make structure_void'),
                            ('drop', 'omit entirely')):
            ttk.Radiobutton(row, text=text, value=value, variable=self.air,
                            command=self.update_summary).pack(side='left', padx=(8, 0))

        self.gap = tk.StringVar(value='void')
        self.block_nbt = tk.BooleanVar(value=True)
        self.dry_run = tk.BooleanVar(value=False)
        row2 = ttk.Frame(box)
        row2.grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 0))
        ttk.Checkbutton(row2, text='keep block NBT data (chests, signs, spawners, '
                                   'modded block data)',
                        variable=self.block_nbt,
                        command=self.update_summary).pack(side='left')
        ttk.Checkbutton(row2, text='dry run (write nothing)',
                        variable=self.dry_run).pack(side='left', padx=(12, 0))

        ttk.Label(box, text='Output folder:').grid(row=2, column=0, sticky='w', pady=(6, 0))
        self.outdir = tk.StringVar(value=outdir or '')
        ttk.Entry(box, textvariable=self.outdir).grid(row=2, column=1, sticky='ew',
                                                      padx=(6, 6), pady=(6, 0))
        ttk.Button(box, text='Browse...', command=self.pick_outdir).grid(row=2, column=2,
                                                                        pady=(6, 0))
        ttk.Label(box, text='Leave empty to write each .nbt next to its schematic.',
                  foreground='#666').grid(row=3, column=1, sticky='w', padx=(6, 0))

    def _build_actions(self):
        row = ttk.Frame(self)
        row.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        row.columnconfigure(0, weight=1)
        self.summary = ttk.Label(row, text='')
        self.summary.grid(row=0, column=0, sticky='w')
        self.convert_button = ttk.Button(row, text='Convert', command=self.convert)
        self.convert_button.grid(row=0, column=1, sticky='e')

    def _build_log(self):
        box = ttk.LabelFrame(self, text='Log', padding=6)
        box.grid(row=4, column=0, sticky='nsew', pady=(8, 0))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.log = tk.Text(box, height=9, wrap='word')
        bar = ttk.Scrollbar(box, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set, state='disabled')
        self.log.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.log.tag_configure('warn', foreground='#a60')
        self.log.tag_configure('bad', foreground='#b00')
        self.log.tag_configure('head', font=('TkDefaultFont', 9, 'bold'))

    # -- log
    def say(self, text, tag=None):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n', tag or ())
        self.log.see('end')
        self.log.configure(state='disabled')

    # -- file list
    def add_paths(self, paths):
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if p in self.scenes:
                continue
            try:
                self.scenes[p] = L.read_schematic(p)
                added += 1
            except Exception as e:
                self.scenes[p] = e
                self.say('%s: cannot read: %s' % (os.path.basename(p), e), 'bad')
        if added:
            self.say('added %d schematic%s' % (added, '' if added == 1 else 's'))
        self.refresh()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title='Pick schematics',
            initialdir=self.anchor_in(),
            filetypes=[('Litematica schematics', '*.litematic *.litematica'),
                       ('All files', '*.*')])
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title='Pick a folder of schematics',
                                         initialdir=self.anchor_in())
        if not folder:
            return
        found = L.walk_dir(folder)
        if not found:
            messagebox.showinfo('Nothing found',
                                'No .litematic files under:\n%s' % folder)
            return
        self.add_paths(found)

    def remove_selected(self):
        for item in self.files.selection():
            self.scenes.pop(item, None)
        self.refresh()

    def clear_files(self):
        self.scenes.clear()
        self.refresh()

    def pick_outdir(self):
        folder = filedialog.askdirectory(
            title='Where should the .nbt files go?',
            initialdir=self.roots.anchor(R.STRUCTURES, self.outdir.get().strip() or None))
        if folder:
            self.outdir.set(folder)

    # -- roots
    def anchor_in(self):
        """Where a "pick schematics" dialog opens: inside the schematics root,
        or at the root itself when the last folder used was outside it. Without
        this, converting into data/frostline/structures/ leaves the next open
        dialog sitting in structures/, nowhere near the schematics."""
        last = None
        if self.scenes:
            last = os.path.dirname(sorted(self.scenes)[-1])
        return self.roots.anchor(R.SCHEMATICS, last)

    def edit_roots(self):
        dialog = rootsui.RootsDialog(self.master_window, self.roots,
                                     kinds=(R.SCHEMATICS, R.STRUCTURES))
        if dialog.saved:
            self.say('schematics root: %s' % (self.roots.get(R.SCHEMATICS) or '(none)'))
            self.say('structure output root: %s'
                     % (self.roots.get(R.STRUCTURES) or '(none)'))

    # -- refresh
    def good_scenes(self):
        return [s for s in self.scenes.values() if isinstance(s, L.Scene)]

    def refresh(self):
        self.files.delete(*self.files.get_children())
        for path, scene in sorted(self.scenes.items()):
            size = ('%dx%dx%d' % tuple(scene.size)) if isinstance(scene, L.Scene) else 'error'
            self.files.insert('', 'end', iid=path,
                              values=(os.path.basename(path), size, os.path.dirname(path)))

        blocks, entities = L.Counter(), L.Counter()
        for scene in self.good_scenes():
            for key, n in scene.block_counts().items():
                blocks[L.state_label(key)] += n
            entities.update(scene.entity_counts())
        self.blocks.load(sorted(blocks.items(), key=lambda kv: (-kv[1], kv[0])))
        self.entities.load(sorted(entities.items(), key=lambda kv: (-kv[1], kv[0])))
        self.update_summary()

    def policy(self):
        # An unticked block state means "do not place this block". The state
        # labels carry properties, so map them back to plain block names: a name
        # is dropped only when every one of its states is unticked.
        by_name = {}
        for label, on in self.blocks.state.items():
            by_name.setdefault(label.split('[')[0], []).append(on)
        drop_blocks = {name for name, flags in by_name.items() if not any(flags)}
        return L.Policy(air=self.air.get(), gap=self.gap.get(),
                        drop_entities=self.entities.unchecked(),
                        drop_blocks=drop_blocks,
                        block_nbt=self.block_nbt.get())

    def update_summary(self):
        scenes = self.good_scenes()
        if not scenes:
            self.summary.configure(text='No schematics loaded.')
            self.convert_button.state(['disabled'])
            return
        self.convert_button.state(['!disabled'])
        policy = self.policy()
        cells = sum(s.volume for s in scenes)
        dropped_entities = sum(n for eid, n in
                               sum((s.entity_counts() for s in scenes), L.Counter()).items()
                               if not policy.keeps_entity(eid))
        partial = {label.split('[')[0] for label, on in self.blocks.state.items() if not on}
        partial -= policy.drop_blocks
        note = ''
        if policy.drop_blocks:
            note += '   %d block type(s) voided' % len(policy.drop_blocks)
        if partial:
            note += '   (%d type(s) unticked only in some states: still placed)' % len(partial)
        if dropped_entities:
            note += '   %d entit%s dropped' % (dropped_entities,
                                               'y' if dropped_entities == 1 else 'ies')
        self.summary.configure(
            text='%d schematic%s, %d cells%s'
                 % (len(scenes), '' if len(scenes) == 1 else 's', cells, note))

    # -- convert
    def convert(self):
        scenes = self.good_scenes()
        if not scenes:
            return
        outdir = self.outdir.get().strip()
        policy = self.policy()
        dry = self.dry_run.get()
        self.say('')
        self.say('converting %d schematic%s%s' % (len(scenes), '' if len(scenes) == 1 else 's',
                                                  ' (dry run)' if dry else ''), 'head')
        failures = 0
        for scene in scenes:
            name = os.path.basename(scene.path)
            try:
                root, report = L.build(scene, policy)
            except Exception as e:
                self.say('%s: FAILED: %s' % (name, e), 'bad')
                failures += 1
                continue
            counts = report['counts']
            solid = sum(v for k, v in counts.items() if k not in (L.AIR, L.VOID))
            self.say('%s  %dx%dx%d  solid %d  air %d  void %d'
                     % (name, report['size'][0], report['size'][1], report['size'][2],
                        solid, counts.get(L.AIR, 0), counts.get(L.VOID, 0)))
            for label, counter in (('entities kept', report['entities_kept']),
                                   ('entities dropped', report['entities_dropped']),
                                   ('block NBT kept', report['block_nbt']),
                                   ('blocks voided', report['blocks_dropped'])):
                if counter:
                    self.say('    %s: %s' % (label, ', '.join(
                        '%s x%d' % (k.split(':')[-1], v) for k, v in sorted(counter.items()))))
            for w in report['warnings']:
                self.say('    WARNING: %s' % w, 'warn')
            dest = os.path.join(outdir or os.path.dirname(scene.path),
                                os.path.splitext(name)[0] + '.nbt')
            if dry:
                self.say('    would write %s' % dest)
                continue
            try:
                os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
                L.nbtio.save(dest, '', root)
            except OSError as e:
                self.say('    could not write %s: %s' % (dest, e), 'bad')
                failures += 1
                continue
            self.say('    -> %s (%d bytes)' % (dest, os.path.getsize(dest)))
        if failures:
            self.say('%d file(s) failed' % failures, 'bad')
        else:
            self.say('done', 'head')


def run(inputs=(), outdir=None):
    if tk is None:
        print('tkinter is not available in this Python build: %s' % _IMPORT_ERROR,
              file=sys.stderr)
        return 2
    root = tk.Tk()
    root.title('litematic convert')
    root.geometry('980x820')
    root.minsize(760, 620)
    try:
        app = App(root, outdir=outdir)
    except Exception:
        traceback.print_exc()
        return 1
    if inputs:
        try:
            app.add_paths(L.collect(list(inputs)))
        except L.ConvertError as e:
            app.say(str(e), 'bad')
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
