#!/usr/bin/env python3
"""Edit a .litematic in place: see every block state and entity, take some out,
swap others for a different id, rename the thing, save.
Run: python tools/apps/litematic_edit.py [file.litematic | folder]

Open a folder instead of a file and the whole folder is listed, subfolders
included: click a file to edit it, or walk the list with Prev/Next (Alt+Left,
Alt+Right). Files you have saved keep a mark, so a pass over a set of variants
shows how far you got.

The sibling app, litematic_convert, reads a schematic and writes a structure
.nbt -- the schematic itself is never touched. This one edits the schematic and
writes a .litematic back, so Litematica can still open it and you can keep
building from it.

Nothing is written until Save, and Save can move the old file aside as
`<name>.litematic.backup` first (on by default).

Where a block or an entity carries NBT -- a chest's Items, a sign's Text, a
modded block's tile entity, a mob's inventory -- the list says so, and replacing
it warns first, because that data belongs to the *old* block and cannot follow
it to the new one.

Tkinter only -- no install, no dependencies.
"""
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'cli'))  # litematic_doc, nbtio
import litematic2nbt as L
import litematic_doc as D
import litematic_roots as R

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import rootsui
except ImportError as e:  # pragma: no cover - only on a python built without tk
    tk = None
    _IMPORT_ERROR = e

KEEP = 'keep'
REMOVE = 'remove'
REPLACE = 'replace'


def short(text):
    return text.split(':')[-1]


class ReplaceDialog(object):
    """Ask for the id to swap in. Says up front what the swap costs."""

    def __init__(self, master, title, prompt, initial='', warning='',
                 props_option=False):
        self.value = None
        self.carry_props = False

        self.top = top = tk.Toplevel(master)
        top.title(title)
        top.transient(master)
        top.resizable(True, False)
        frame = ttk.Frame(top, padding=10)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text=prompt, wraplength=460,
                  justify='left').grid(row=0, column=0, sticky='w')

        self.entry_value = tk.StringVar(value=initial)
        entry = ttk.Entry(frame, textvariable=self.entry_value, width=52)
        entry.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        entry.focus_set()
        entry.selection_range(0, 'end')

        row = 2
        if props_option:
            self.props_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(frame, variable=self.props_var,
                            text='carry the old block properties over '
                                 '(axis, facing, ... -- only if the new block has them)'
                            ).grid(row=row, column=0, sticky='w', pady=(8, 0))
            row += 1
        else:
            self.props_var = None

        if warning:
            ttk.Label(frame, text=warning, wraplength=460, justify='left',
                      foreground='#a60').grid(row=row, column=0, sticky='w', pady=(8, 0))
            row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, sticky='e', pady=(12, 0))
        ttk.Button(buttons, text='Cancel', command=self._cancel).pack(side='right')
        ttk.Button(buttons, text='Replace',
                   command=self._ok).pack(side='right', padx=(0, 6))

        top.bind('<Return>', lambda _e: self._ok())
        top.bind('<Escape>', lambda _e: self._cancel())
        top.protocol('WM_DELETE_WINDOW', self._cancel)
        top.grab_set()
        master.wait_window(top)

    def _ok(self):
        text = self.entry_value.get().strip()
        if not text:
            return
        self.value = text
        self.carry_props = bool(self.props_var and self.props_var.get())
        self.top.destroy()

    def _cancel(self):
        self.value = None
        self.top.destroy()


class PlanTree(ttk.Frame):
    """A Treeview of one kind of thing, with the planned action per row."""

    def __init__(self, master, heading, count_heading='cells'):
        ttk.Frame.__init__(self, master)
        self.tree = ttk.Treeview(self, columns=('name', 'n', 'nbt', 'action'),
                                 show='headings', selectmode='extended', height=11)
        for key, text, width, anchor, stretch in (
                ('name', heading, 280, 'w', True),
                ('n', count_heading, 60, 'e', False),
                ('nbt', 'NBT', 45, 'e', False),
                ('action', 'action', 190, 'w', False)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree.tag_configure('remove', foreground='#b00')
        self.tree.tag_configure('replace', foreground='#06c')

        self.rows = {}          # iid -> (name, count, nbt count)
        self.plan = {}          # iid -> (REMOVE,) | (REPLACE, text)
        self.on_remove = self.on_replace = self.on_keep = None

        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(buttons, text='Remove', width=9,
                   command=lambda: self._fire('on_remove')).pack(side='left')
        ttk.Button(buttons, text='Replace...', width=11,
                   command=lambda: self._fire('on_replace')).pack(side='left', padx=(4, 0))
        ttk.Button(buttons, text='Keep', width=8,
                   command=lambda: self._fire('on_keep')).pack(side='left', padx=(4, 0))

        self.tree.bind('<Double-1>', lambda _e: self._fire('on_replace'))
        self.tree.bind('<Delete>', lambda _e: self._fire('on_remove'))

    def _fire(self, attr):
        handler = getattr(self, attr)
        if handler and self.selection():
            handler(self.selection())

    def selection(self):
        return [i for i in self.tree.selection() if i in self.rows]

    def load(self, rows):
        """rows: [(name, count, nbt count)]. Planned actions survive a reload
        only for names that are still there."""
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for name, n, nbt in rows:
            self.rows[name] = (name, n, nbt)
            self.tree.insert('', 'end', iid=name, values=(name, n, nbt or '', ''))
        self.plan = {k: v for k, v in self.plan.items() if k in self.rows}
        for iid in self.rows:
            self._refresh(iid)

    def set_action(self, iids, action):
        for iid in iids:
            if iid not in self.rows:
                continue
            if action is None:
                self.plan.pop(iid, None)
            else:
                self.plan[iid] = action
            self._refresh(iid)

    def clear_plan(self):
        self.plan = {}
        for iid in self.rows:
            self._refresh(iid)

    def _refresh(self, iid):
        action = self.plan.get(iid)
        if action is None:
            self.tree.set(iid, 'action', '')
            self.tree.item(iid, tags=())
        elif action[0] == REMOVE:
            self.tree.set(iid, 'action', 'remove')
            self.tree.item(iid, tags=('remove',))
        else:
            self.tree.set(iid, 'action', '-> ' + action[1])
            self.tree.item(iid, tags=('replace',))

    def nbt_count(self, iids):
        return sum(self.rows[i][2] for i in iids if i in self.rows)


class App(ttk.Frame):
    def __init__(self, master):
        ttk.Frame.__init__(self, master, padding=8)
        self.grid(row=0, column=0, sticky='nsew')
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=3)
        self.rowconfigure(4, weight=1)

        self.doc = None
        self.master_window = master
        self.roots = R.Roots.load()
        self.folder = ''        # the folder being walked, if any
        self.files = []         # every .litematic under it, in order
        self.index = -1         # which of them is open; -1 = none of them
        self.saved_paths = set()

        self._build_file_row()
        self._build_lists()
        self._build_options()
        self._build_actions()
        self._build_log()
        # Walking a folder is a two-handed job -- one hand on the lists, one on
        # the keyboard -- so Prev/Next get keys as well as buttons.
        master.bind('<Alt-Left>', lambda _e: self.step(-1))
        master.bind('<Alt-Right>', lambda _e: self.step(1))
        self.refresh()

    # -- widgets
    def _build_file_row(self):
        box = ttk.LabelFrame(self, text='Schematic', padding=6)
        box.grid(row=0, column=0, sticky='ew')
        box.columnconfigure(1, weight=1)

        ttk.Button(box, text='Open...', command=self.open_file).grid(row=0, column=0)
        self.file_label = ttk.Label(box, text='nothing open')
        self.file_label.grid(row=0, column=1, columnspan=3, sticky='w', padx=(8, 0))
        ttk.Button(box, text='Reload', command=self.reload_file).grid(row=0, column=4)
        ttk.Button(box, text='Roots...', command=self.edit_roots).grid(
            row=0, column=5, padx=(6, 0))
        self.info_label = ttk.Label(box, text='', foreground='#666')
        self.info_label.grid(row=1, column=1, columnspan=5, sticky='w', padx=(8, 0),
                             pady=(4, 0))

        # The name Litematica's list shows. It is metadata, not the filename:
        # a schematic saved by copying another one keeps the name it was copied
        # from, which is how you end up with six files all called boulder_3.
        ttk.Label(box, text='Name:').grid(row=2, column=0, sticky='e', pady=(6, 0))
        self.name_var = tk.StringVar()
        self.name_var.trace_add('write', lambda *_a: self.update_summary())
        self.name_entry = ttk.Entry(box, textvariable=self.name_var)
        self.name_entry.grid(row=2, column=1, sticky='ew', padx=(8, 0), pady=(6, 0))
        ttk.Button(box, text='Match file', command=self.name_from_file).grid(
            row=2, column=2, padx=(6, 0), pady=(6, 0))
        self.name_note = ttk.Label(box, text='', foreground='#666')
        self.name_note.grid(row=2, column=3, columnspan=3, sticky='w', padx=(8, 0),
                            pady=(6, 0))

    def _build_lists(self):
        pane = ttk.Frame(self)
        pane.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        pane.columnconfigure(0, weight=2, minsize=210)
        pane.columnconfigure(1, weight=3)
        pane.columnconfigure(2, weight=2)
        pane.rowconfigure(0, weight=1)

        self._build_folder(pane)

        left = ttk.LabelFrame(pane, text='Blocks', padding=6)
        left.grid(row=0, column=1, sticky='nsew', padx=(4, 4))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.blocks = PlanTree(left, 'block state', 'cells')
        self.blocks.grid(row=0, column=0, sticky='nsew')
        self.blocks.on_remove = self.remove_blocks
        self.blocks.on_replace = self.replace_blocks
        self.blocks.on_keep = self.keep_blocks

        right = ttk.LabelFrame(pane, text='Entities', padding=6)
        right.grid(row=0, column=2, sticky='nsew', padx=(4, 0))
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.entities = PlanTree(right, 'entity type', 'count')
        self.entities.grid(row=0, column=0, sticky='nsew')
        self.entities.on_remove = self.remove_entities
        self.entities.on_replace = self.replace_entities
        self.entities.on_keep = self.keep_entities

    def _build_folder(self, pane):
        """The folder browser: every schematic under one folder, in order.

        Checking six variants of the same tree means opening six files, and
        doing that through the Open dialog is six trips through a file picker.
        Here it is one click, or Next.
        """
        box = ttk.LabelFrame(pane, text='Folder', padding=6)
        box.grid(row=0, column=0, sticky='nsew', padx=(0, 4))
        box.rowconfigure(1, weight=1)
        box.columnconfigure(0, weight=1)

        top = ttk.Frame(box)
        top.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Button(top, text='Open folder...', command=self.open_folder).pack(side='left')
        self.folder_label = ttk.Label(top, text='no folder', foreground='#666')
        self.folder_label.pack(side='left', padx=(6, 0))

        self.file_tree = ttk.Treeview(box, columns=('mark', 'name', 'where'),
                                      show='headings', selectmode='browse', height=12)
        self.file_tree.heading('mark', text='')
        self.file_tree.heading('name', text='file')
        self.file_tree.heading('where', text='in')
        self.file_tree.column('mark', width=22, anchor='center', stretch=False)
        self.file_tree.column('name', width=150, anchor='w')
        self.file_tree.column('where', width=90, anchor='w', stretch=False)
        bar = ttk.Scrollbar(box, orient='vertical', command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=bar.set)
        self.file_tree.grid(row=1, column=0, sticky='nsew', pady=(6, 0))
        bar.grid(row=1, column=1, sticky='ns', pady=(6, 0))
        self.file_tree.tag_configure('open', font=('TkDefaultFont', 9, 'bold'))
        self.file_tree.tag_configure('saved', foreground='#080')
        self.file_tree.bind('<Button-1>', self._click_file)
        self.file_tree.bind('<Return>', lambda _e: self._open_selected())
        self.file_tree.bind('<Double-1>', lambda _e: self._open_selected())

        nav = ttk.Frame(box)
        nav.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        self.prev_button = ttk.Button(nav, text='< Prev', width=8,
                                      command=lambda: self.step(-1))
        self.prev_button.pack(side='left')
        self.next_button = ttk.Button(nav, text='Next >', width=8,
                                      command=lambda: self.step(1))
        self.next_button.pack(side='left', padx=(4, 0))
        self.position_label = ttk.Label(nav, text='', foreground='#666')
        self.position_label.pack(side='left', padx=(8, 0))

    # -- walking a folder
    def open_folder(self):
        if not self.confirm_discard():
            return
        folder = filedialog.askdirectory(title='Pick a folder of schematics',
                                         initialdir=self.anchor())
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder, open_first=True):
        """List every .litematic under `folder`, subfolders included."""
        folder = os.path.abspath(folder)
        found = L.walk_dir(folder)
        if not found:
            messagebox.showinfo('Nothing found',
                                'No .litematic files under:\n%s' % folder)
            return False
        self.folder = folder
        self.files = found
        self.index = -1
        self.saved_paths = set()
        self.fill_file_list()
        self.say('folder %s: %d schematic%s'
                 % (folder, len(found), '' if len(found) == 1 else 's'))
        if open_first:
            self.load_index(0, force=True)
        else:
            self.refresh_nav()
        return True

    def fill_file_list(self):
        self.file_tree.delete(*self.file_tree.get_children())
        for path in self.files:
            where = os.path.relpath(os.path.dirname(path), self.folder)
            self.file_tree.insert('', 'end', iid=path,
                                  values=('', os.path.basename(path),
                                          '' if where == '.' else where))

    def load_index(self, i, force=False):
        """Open the i-th file in the folder, asking about unsaved edits first."""
        if not 0 <= i < len(self.files):
            return False
        if not force and not self.confirm_discard():
            self.refresh_nav()          # put the selection back on what is open
            return False
        self.index = i
        ok = self.load_path(self.files[i], guard=False)
        self.refresh_nav()
        return ok

    def step(self, delta):
        """Prev/Next. From outside the folder list, Next starts at the top."""
        if not self.files:
            return
        target = self.index + delta
        if self.index < 0:
            target = 0 if delta > 0 else len(self.files) - 1
        if 0 <= target < len(self.files):
            self.load_index(target)

    def _click_file(self, event):
        if self.file_tree.identify_region(event.x, event.y) not in ('cell', 'tree'):
            return None
        path = self.file_tree.identify_row(event.y)
        if not path:
            return None
        self._open(path)
        return 'break'

    def _open_selected(self):
        for path in self.file_tree.selection():
            self._open(path)
            return

    def _open(self, path):
        if path in self.files:
            self.load_index(self.files.index(path))

    def mark_saved(self, path):
        """Tick a file, so a pass over a folder of variants shows its progress."""
        if path in self.files:
            self.saved_paths.add(path)
            self.file_tree.set(path, 'mark', '*')
            self.refresh_nav()

    def refresh_nav(self):
        """Selection, ticks and the Prev/Next state, all from self.index."""
        for path in self.files:
            tags = []
            if path in self.saved_paths:
                tags.append('saved')
            if 0 <= self.index < len(self.files) and path == self.files[self.index]:
                tags.append('open')
            self.file_tree.item(path, tags=tuple(tags))
        if 0 <= self.index < len(self.files):
            current = self.files[self.index]
            self.file_tree.selection_set(current)
            self.file_tree.see(current)
            self.position_label.configure(
                text='%d / %d' % (self.index + 1, len(self.files)))
        elif self.files:
            self.file_tree.selection_remove(*self.file_tree.selection())
            self.position_label.configure(
                text='%d files' % len(self.files))
        else:
            self.position_label.configure(text='')
        has = bool(self.files)
        self.prev_button.state(['!disabled']
                               if has and self.index != 0 else ['disabled'])
        self.next_button.state(['!disabled']
                               if has and self.index < len(self.files) - 1
                               else ['disabled'])
        self.folder_label.configure(
            text=(os.path.basename(self.folder) or self.folder) if self.folder
            else 'no folder')

    def _build_options(self):
        box = ttk.LabelFrame(self, text='Options', padding=6)
        box.grid(row=2, column=0, sticky='ew', pady=(8, 0))

        self.fill = tk.StringVar(value=D.AIR)
        row = ttk.Frame(box)
        row.grid(row=0, column=0, sticky='w')
        ttk.Label(row, text='Removed blocks become:').pack(side='left')
        for value, text in ((D.AIR, 'air (normal empty space)'),
                            (D.VOID, 'structure_void (leaves terrain alone once converted)')):
            ttk.Radiobutton(row, text=text, value=value, variable=self.fill,
                            command=self.update_summary).pack(side='left', padx=(8, 0))

        self.backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, variable=self.backup,
                        text='back the old file up as <name>.litematic.backup before saving'
                        ).grid(row=1, column=0, sticky='w', pady=(4, 0))

    def _build_actions(self):
        row = ttk.Frame(self)
        row.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        row.columnconfigure(0, weight=1)
        self.summary = ttk.Label(row, text='')
        self.summary.grid(row=0, column=0, sticky='w')
        self.save_as_button = ttk.Button(row, text='Save as...', command=self.save_as)
        self.save_as_button.grid(row=0, column=1, sticky='e', padx=(0, 6))
        self.save_button = ttk.Button(row, text='Save', command=self.save)
        self.save_button.grid(row=0, column=2, sticky='e')

    def _build_log(self):
        box = ttk.LabelFrame(self, text='Log', padding=6)
        box.grid(row=4, column=0, sticky='nsew', pady=(8, 0))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.log = tk.Text(box, height=8, wrap='word')
        bar = ttk.Scrollbar(box, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set, state='disabled')
        self.log.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        self.log.tag_configure('warn', foreground='#a60')
        self.log.tag_configure('bad', foreground='#b00')
        self.log.tag_configure('head', font=('TkDefaultFont', 9, 'bold'))

    def say(self, text, tag=None):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n', tag or ())
        self.log.see('end')
        self.log.configure(state='disabled')

    # -- files
    def open_file(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title='Open a schematic',
            initialdir=self.anchor(),
            filetypes=[('Litematica schematics', '*.litematic *.litematica'),
                       ('All files', '*.*')])
        if path:
            self.load_path(path)

    def load_path(self, path, keep_plan=False, guard=False):
        if guard and not self.confirm_discard():
            return False
        path = os.path.abspath(path)
        try:
            self.doc = D.Doc.load(path)
        except Exception as e:
            self.doc = None
            self.say('%s: cannot read: %s' % (os.path.basename(path), e), 'bad')
            self.refresh()
            return False
        if not keep_plan:
            self.blocks.clear_plan()
            self.entities.clear_plan()
        # Opening a file that happens to be in the folder list -- through Open,
        # or after a save -- puts the walk back in step with it.
        self.index = self.files.index(path) if path in self.files else -1
        self.say('opened %s' % path)
        orphans = self.doc.orphan_nbt_count()
        if orphans:
            self.say('    %d block NBT entr%s sits outside the region box and is left '
                     'untouched' % (orphans, 'y' if orphans == 1 else 'ies'), 'warn')
        self.refresh()
        return True

    def reload_file(self):
        if self.doc and self.confirm_discard():
            self.blocks.clear_plan()
            self.entities.clear_plan()
            self.load_path(self.doc.path)

    def confirm_discard(self):
        if not self.planned():
            return True
        return messagebox.askyesno(
            'Unsaved edits',
            'There are planned edits that have not been saved.\nThrow them away?')

    # -- plan editing
    def remove_blocks(self, iids):
        nbt = self.blocks.nbt_count(iids)
        if nbt and not messagebox.askyesno(
                'Block data will go',
                '%d of these blocks carry NBT data (chest contents, sign text, a modded '
                'block\'s own data).\n\nRemoving the blocks removes that data too.\n\n'
                'Go ahead?' % nbt):
            return
        self.blocks.set_action(iids, (REMOVE,))
        self.update_summary()

    def replace_blocks(self, iids):
        first = iids[0]
        nbt = self.blocks.nbt_count(iids)
        warning = ''
        if nbt:
            warning = ('%d of these blocks carry NBT data. A replacement that changes the '
                       'block id drops it -- that data describes the old block, not the new '
                       'one. Changing only the properties keeps it.' % nbt)
        dialog = ReplaceDialog(
            self.master_window, 'Replace block',
            'Replace %d block state%s with:\n\nA plain id (minecraft:stone) or a full state '
            '(minecraft:oak_log[axis=y]).'
            % (len(iids), '' if len(iids) == 1 else 's'),
            initial=first.split('[')[0], warning=warning, props_option=True)
        if dialog.value is None:
            return
        try:
            target = D.parse_state(dialog.value)
        except D.EditError as e:
            messagebox.showerror('Not a block id', str(e))
            return
        for iid in iids:
            key = target
            if dialog.carry_props and not target[1]:
                key = (target[0], D.parse_state(iid)[1])
            self.blocks.set_action([iid], (REPLACE, L.state_label(key)))
        self.update_summary()

    def keep_blocks(self, iids):
        self.blocks.set_action(iids, None)
        self.update_summary()

    def remove_entities(self, iids):
        self.entities.set_action(iids, (REMOVE,))
        self.update_summary()

    def replace_entities(self, iids):
        rich = self.entities.nbt_count(iids)
        warning = ('%d of these entities carry their own NBT (inventory, attributes, a '
                   'marker\'s data tag). Only placement -- position, motion, rotation -- '
                   'survives the swap; the rest is dropped.' % rich) if rich else ''
        dialog = ReplaceDialog(
            self.master_window, 'Replace entity',
            'Replace %d entity type%s with:\n\nAn entity id, for example minecraft:marker.'
            % (len(iids), '' if len(iids) == 1 else 's'),
            initial=iids[0], warning=warning)
        if dialog.value is None:
            return
        new_id = L.qualify(dialog.value.strip())
        self.entities.set_action(iids, (REPLACE, new_id))
        self.update_summary()

    def keep_entities(self, iids):
        self.entities.set_action(iids, None)
        self.update_summary()

    # -- roots
    def anchor(self):
        """Where a schematic dialog should open: inside the schematics root,
        or at the root itself if where we are is outside it."""
        here = os.path.dirname(self.doc.path) if self.doc else None
        return self.roots.anchor(R.SCHEMATICS, here)

    def edit_roots(self):
        dialog = rootsui.RootsDialog(self.master_window, self.roots,
                                     kinds=(R.SCHEMATICS,))
        if dialog.saved:
            self.say('schematics root: %s' % (self.roots.get(R.SCHEMATICS) or '(none)'))

    # -- name
    def name_from_file(self):
        """Take the name from the filename: grave_1.litematic -> grave_1."""
        if self.doc:
            self.name_var.set(D.file_stem(self.doc.path))

    def new_name(self):
        """The name to write, or None to leave the one already in the file."""
        if self.doc is None:
            return None
        text = self.name_var.get().strip()
        if not text or text == self.doc.name:
            return None
        return text

    # -- state
    def planned(self):
        return bool(self.blocks.plan or self.entities.plan or self.new_name())

    def refresh(self):
        if self.doc is None:
            self.file_label.configure(text='nothing open')
            self.info_label.configure(text='')
            self.name_var.set('')
            self.name_entry.state(['disabled'])
            self.blocks.load([])
            self.entities.load([])
            self.refresh_nav()
            self.update_summary()
            return
        self.file_label.configure(text=self.doc.path)
        self.name_entry.state(['!disabled'])
        self.name_var.set(self.doc.name)
        rows = self.doc.block_rows()
        ents = self.doc.entity_rows()
        with_nbt = sum(r[2] for r in rows)
        self.info_label.configure(
            text='%s   %dx%dx%d   %d region%s   %d block state%s   %d with NBT'
                 % (self.doc.name or '(no name)', self.doc.size[0], self.doc.size[1],
                    self.doc.size[2], len(self.doc.regions),
                    '' if len(self.doc.regions) == 1 else 's',
                    len(rows), '' if len(rows) == 1 else 's', with_nbt))
        self.blocks.load([(L.state_label(k), n, nbt) for k, n, nbt in rows])
        self.entities.load(ents)
        self.refresh_nav()
        self.update_summary()

    def update_summary(self):
        for button in (self.save_button, self.save_as_button):
            button.state(['!disabled'] if self.doc else ['disabled'])
        if self.doc is None:
            self.summary.configure(text='Open a .litematic to start.')
            self.name_note.configure(text='')
            return
        stem = D.file_stem(self.doc.path)
        shown = self.name_var.get().strip()
        self.name_note.configure(
            text='' if shown == stem else 'the file is called %s' % stem,
            foreground='#a60' if shown and shown != stem else '#666')
        cells = removed = 0
        for iid, action in self.blocks.plan.items():
            n = self.blocks.rows[iid][1]
            cells += n
            if action[0] == REMOVE:
                removed += n
        bits = []
        if self.blocks.plan:
            bits.append('%d block state(s) / %d cells' % (len(self.blocks.plan), cells))
        if removed:
            bits.append('%d cells become %s' % (removed, short(self.fill.get())))
        if self.entities.plan:
            bits.append('%d entity type(s)' % len(self.entities.plan))
        if self.new_name():
            bits.append('rename to "%s"' % self.new_name())
        self.summary.configure(
            text='No edits planned.' if not bits else 'Planned: ' + ',  '.join(bits))

    # -- saving
    def build_plan(self):
        blocks = {}
        for label, action in self.blocks.plan.items():
            key = D.parse_state(label)
            blocks[key] = D.DELETE if action[0] == REMOVE else D.parse_state(action[1])
        entities = {}
        for eid, action in self.entities.plan.items():
            entities[eid] = D.DELETE if action[0] == REMOVE else action[1]
        return blocks, entities

    def save(self):
        self._write(None)

    def save_as(self):
        if self.doc is None:
            return
        path = filedialog.asksaveasfilename(
            title='Save the schematic as',
            defaultextension='.litematic',
            initialfile=os.path.basename(self.doc.path),
            initialdir=self.anchor(),
            filetypes=[('Litematica schematics', '*.litematic'), ('All files', '*.*')])
        if path:
            self._write(path)

    def _write(self, path):
        if self.doc is None:
            return
        try:
            blocks, entities = self.build_plan()
        except D.EditError as e:
            messagebox.showerror('Bad replacement', str(e))
            return
        self.say('')
        self.say('saving %s' % (path or self.doc.path), 'head')
        try:
            root, report = self.doc.apply(blocks, entities,
                                          fill=D.parse_state(self.fill.get()),
                                          name=self.new_name())
        except Exception as e:
            self.say('    FAILED to apply the edits: %s' % e, 'bad')
            traceback.print_exc()
            return
        if report['renamed']:
            self.say('    renamed "%s" -> "%s"' % report['renamed'])
        if report['region_renamed']:
            self.say('    region "%s" renamed to match' % report['region_renamed'][0])
        for label, counter in (('blocks replaced', report['blocks_replaced']),
                               ('blocks removed', report['blocks_deleted']),
                               ('entities replaced', report['entities_replaced']),
                               ('entities removed', report['entities_deleted'])):
            if counter:
                self.say('    %s: %s' % (label, ', '.join(
                    '%s x%d' % (short(k), v) for k, v in sorted(counter.items()))))
        if report['block_nbt_dropped']:
            self.say('    block NBT dropped (the block under it changed): %s' % ', '.join(
                '%s x%d' % (short(k), v)
                for k, v in sorted(report['block_nbt_dropped'].items())), 'warn')
        if report['entity_nbt_stripped']:
            self.say('    entity NBT dropped on retype: %s' % ', '.join(
                '%s (%d tags)' % (short(k), v)
                for k, v in sorted(report['entity_nbt_stripped'].items())), 'warn')
        if report['ticks_dropped']:
            self.say('    %d pending tick(s) dropped at edited positions'
                     % report['ticks_dropped'])
        for w in report['warnings']:
            self.say('    WARNING: %s' % w, 'warn')

        try:
            dest, made = self.doc.save(root, path, backup=self.backup.get())
        except Exception as e:
            self.say('    could not write: %s' % e, 'bad')
            return
        if made:
            self.say('    old file kept as %s' % made)
        self.say('    -> %s (%d bytes)' % (dest, os.path.getsize(dest)))
        # Reopen what was just written, so the lists show the new truth and the
        # plan cannot be applied a second time on top of itself.
        self.blocks.clear_plan()
        self.entities.clear_plan()
        self.load_path(dest)
        self.mark_saved(dest)


def run(inputs=()):
    if tk is None:
        print('tkinter is not available in this Python build: %s' % _IMPORT_ERROR,
              file=sys.stderr)
        return 2
    root = tk.Tk()
    root.title('litematic edit')
    root.geometry('1180x820')
    root.minsize(940, 640)
    try:
        app = App(root)
    except Exception:
        traceback.print_exc()
        return 1
    for path in inputs:
        loaded = app.load_folder(path) if os.path.isdir(path) else app.load_path(path)
        if loaded:
            break
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
