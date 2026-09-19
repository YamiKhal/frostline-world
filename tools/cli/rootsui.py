"""The "Roots..." dialog, shared by litematic_convert and litematic_edit.

Widgets only -- what a root *means* is in litematic_roots.py, next door. Never
run on its own, so it lives here with the other imported-only modules rather
than in apps/, which is the list of things you can actually launch.

Importing this needs tkinter; the apps already import it inside the same try
that guards their own tkinter import, so a Python without tk fails there, with
a message, rather than here.
"""
import os
import sys

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import litematic_roots as R


class RootsDialog(object):
    """Set one root per kind. Saves to the settings file on OK."""

    def __init__(self, master, roots, kinds=(R.SCHEMATICS,)):
        self.roots = roots
        self.kinds = list(kinds)
        self.saved = False
        self.vars = {}

        self.top = top = tk.Toplevel(master)
        top.title('Folder roots')
        top.transient(master)
        frame = ttk.Frame(top, padding=10)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, wraplength=620, justify='left',
                  text='A root is where the file dialogs for that kind of thing open. '
                       'Anywhere inside the root, you carry on from where you were; '
                       'anywhere outside it, you are put back at the root -- so a '
                       'dialog can never open in whatever folder you last used for '
                       'something else.').grid(row=0, column=0, columnspan=4, sticky='w')

        row = 1
        for kind in self.kinds:
            ttk.Separator(frame, orient='horizontal').grid(
                row=row, column=0, columnspan=4, sticky='ew', pady=(10, 6))
            row += 1
            ttk.Label(frame, text=R.LABELS[kind] + ':').grid(row=row, column=0, sticky='w')
            var = tk.StringVar(value=roots.get(kind))
            self.vars[kind] = var
            entry = ttk.Entry(frame, textvariable=var, width=62)
            entry.grid(row=row, column=1, sticky='ew', padx=(8, 6))
            ttk.Button(frame, text='Browse...', width=10,
                       command=lambda k=kind: self._browse(k)).grid(row=row, column=2)
            ttk.Button(frame, text='Default', width=9,
                       command=lambda k=kind: self._default(k)).grid(row=row, column=3,
                                                                     padx=(6, 0))
            row += 1
            ttk.Label(frame, text=R.BLURB[kind], foreground='#666', wraplength=560,
                      justify='left').grid(row=row, column=1, columnspan=3, sticky='w',
                                           padx=(8, 0))
            row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=4, sticky='e', pady=(14, 0))
        ttk.Button(buttons, text='Cancel', command=self._cancel).pack(side='right')
        ttk.Button(buttons, text='Save',
                   command=self._ok).pack(side='right', padx=(0, 6))

        top.bind('<Return>', lambda _e: self._ok())
        top.bind('<Escape>', lambda _e: self._cancel())
        top.protocol('WM_DELETE_WINDOW', self._cancel)
        top.resizable(True, False)
        top.grab_set()
        master.wait_window(top)

    def _browse(self, kind):
        current = self.vars[kind].get().strip()
        folder = filedialog.askdirectory(
            parent=self.top, title=R.LABELS[kind],
            initialdir=self.roots.anchor(kind, current or None))
        if folder:
            self.vars[kind].set(os.path.abspath(folder))

    def _default(self, kind):
        self.vars[kind].set(self.roots.default(kind))

    def _ok(self):
        for kind in self.kinds:
            value = self.vars[kind].get().strip()
            if value and not os.path.isdir(value):
                messagebox.showerror(
                    'No such folder',
                    '%s is not a folder:\n%s\n\nLeave it empty to use no root.'
                    % (R.LABELS[kind], value), parent=self.top)
                return
            self.roots.set(kind, value)
        try:
            self.roots.save()
        except OSError as e:
            messagebox.showerror('Could not save',
                                 'The roots are set for this session, but could not be '
                                 'written to\n%s\n\n%s' % (self.roots.path, e),
                                 parent=self.top)
        self.saved = True
        self.top.destroy()

    def _cancel(self):
        self.saved = False
        self.top.destroy()
