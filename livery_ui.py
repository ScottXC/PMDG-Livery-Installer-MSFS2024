"""Tk presentation components for the livery manager.

The gallery and table share one selection model. All file operations remain in
the application controller; thumbnail workers pass pixels back to Tk's thread.
"""
from __future__ import annotations

import math
import queue
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk

from app_version import VERSION


COLORS = {
    "bg": "#15171c", "top": "#15171c", "sidebar": "#101217",
    "sidebar_active": "#243552", "panel": "#20232b", "panel_alt": "#292d36",
    "field": "#181b22", "log": "#181b22", "line": "#363d4a",
    "line_soft": "#2e3440", "muted": "#abb4c4", "text": "#f3f5f8",
    "red": "#b63b53", "red_hover": "#ca4962", "amber": "#edc879",
    "green": "#80d8b0", "cyan": "#75d4ed", "blue": "#376dc5",
    "blue_hover": "#447fdc", "button": "#303642", "button_hover": "#3c4657",
}


def text(parent, value="", *, size=10, color=None, bold=False, variable=None, **kwargs):
    return tk.Label(parent, text=value, textvariable=variable, bg=parent.cget("bg"),
                    fg=color or COLORS["text"], font=("Segoe UI", size, "bold" if bold else "normal"),
                    anchor="w", **kwargs)


def panel(parent, title=None, subtitle=None, padding=18):
    outer = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["line_soft"], highlightthickness=1)
    body = tk.Frame(outer, bg=COLORS["panel"])
    body.pack(fill=tk.BOTH, expand=True, padx=padding, pady=padding)
    if title:
        text(body, title, size=11, bold=True).pack(anchor="w")
    if subtitle:
        text(body, subtitle, size=9, color=COLORS["muted"], wraplength=650, justify="left").pack(anchor="w", pady=(4, 14))
    return outer, body


def field(app, parent, label, variable, row=0, browse=None):
    text(parent, label, size=9, color=COLORS["muted"], bold=True).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 6))
    app.entry(parent, variable).grid(row=row + 1, column=0, sticky="ew", ipady=8)
    parent.columnconfigure(0, weight=1)
    if browse:
        app.button(parent, "Browse…", browse).grid(row=row + 1, column=1, padx=(10, 0))


def text_area(parent, height=8):
    frame = tk.Frame(parent, bg=COLORS["panel"])
    frame.pack(fill=tk.BOTH, expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    widget = tk.Text(frame, height=height, wrap="word", bg=COLORS["log"], fg=COLORS["text"],
                     insertbackground=COLORS["text"], relief="flat", bd=0, padx=14, pady=12,
                     font=("Consolas", 10), highlightthickness=1, highlightbackground=COLORS["line_soft"])
    widget.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    widget.configure(yscrollcommand=scrollbar.set)
    return widget


def make_page(app, title, subtitle, action=None):
    page = tk.Frame(app.page_container, bg=COLORS["bg"])
    page.columnconfigure(0, weight=1)
    page.rowconfigure(1, weight=1)
    header = tk.Frame(page, bg=COLORS["bg"])
    header.grid(row=0, column=0, sticky="ew", pady=(0, 22))
    heading = tk.Frame(header, bg=COLORS["bg"])
    heading.pack(side=tk.LEFT, fill=tk.X, expand=True)
    text(heading, title, size=25, bold=True).pack(anchor="w")
    text(heading, subtitle, color=COLORS["muted"], size=10).pack(anchor="w", pady=(4, 0))
    if action:
        app.button(header, action[0], action[1], accent=True).pack(side=tk.RIGHT, padx=(12, 0), pady=8)
    body = tk.Frame(page, bg=COLORS["bg"])
    body.grid(row=1, column=0, sticky="nsew")
    return page, body


class LiveryGallery(tk.Frame):
    PAGE_SIZE = 24

    def __init__(self, parent, app):
        super().__init__(parent, bg=COLORS["bg"])
        self.app = app
        self.items = {}
        self.cards = {}
        self.images = {}
        self.cache = OrderedDict()
        self.page = 0
        self.columns = 0
        self.generation = 0
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Gallery image")
        self.results = queue.Queue()
        self.closed = False
        self.futures = []
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0, bd=0, takefocus=True)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = tk.Frame(self.canvas, bg=COLORS["bg"])
        self.window_id = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resize)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Control-a>", self.select_all)
        for key, delta in (("Left", -1), ("Right", 1), ("Up", -100), ("Down", 100)):
            self.canvas.bind(f"<{key}>", lambda event, move=delta: self.move_selection(move, event))
        footer = tk.Frame(self, bg=COLORS["bg"])
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.page_label = text(footer, color=COLORS["muted"], size=9)
        self.page_label.pack(side=tk.LEFT)
        self.previous = app.button(footer, "‹ Previous", lambda: self.change_page(-1))
        self.previous.pack(side=tk.RIGHT, padx=(8, 0))
        self.next = app.button(footer, "Next ›", lambda: self.change_page(1))
        self.next.pack(side=tk.RIGHT)
        self.after(80, self.poll_images)

    def set_items(self, items):
        self.items = dict(items)
        self.page = 0
        self.draw()

    def resize(self, event):
        if self.closed:
            return
        self.canvas.itemconfigure(self.window_id, width=max(1, event.width))
        columns = max(1, min(4, event.width // 235))
        if columns != self.columns:
            self.columns = columns
            self.draw()

    def draw(self):
        self.generation += 1
        for future in self.futures:
            future.cancel()
        self.futures.clear()
        for widget in self.content.winfo_children():
            widget.destroy()
        self.cards.clear()
        self.images.clear()
        for column in range(4):
            self.content.columnconfigure(column, weight=0, minsize=0, uniform="")
        columns = max(self.columns, 1)
        for column in range(columns):
            self.content.columnconfigure(column, weight=1, uniform="cards")
        values = list(self.items.items())
        page_count = max(1, math.ceil(len(values) / self.PAGE_SIZE))
        self.page = min(self.page, page_count - 1)
        self.page_label.configure(text=f"Page {self.page + 1} of {page_count}  ·  {len(values)} liveries")
        self.previous.configure(state="normal" if self.page else "disabled")
        self.next.configure(state="normal" if self.page + 1 < page_count else "disabled")
        if not values:
            empty = tk.Frame(self.content, bg=COLORS["bg"])
            empty.grid(row=0, column=0, columnspan=columns, sticky="ew", pady=65)
            text(empty, "Your next livery starts here", size=17, bold=True).pack()
            text(empty, "Select an aircraft, or import a compatible ZIP to build your library.", color=COLORS["muted"],
                 wraplength=440, justify="center").pack(pady=(10, 20))
            self.app.button(empty, "＋  Install a livery", lambda: self.app.show_page("Liveries"), accent=True).pack()
            return
        for index, (iid, livery) in enumerate(values[self.page * self.PAGE_SIZE:(self.page + 1) * self.PAGE_SIZE]):
            card = tk.Frame(self.content, bg=COLORS["panel"], highlightthickness=2, highlightbackground=COLORS["line_soft"])
            card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0, 12), pady=(0, 14))
            card.columnconfigure(0, weight=1)
            preview_shell = tk.Frame(card, height=126, bg="#272c35")
            preview_shell.grid(row=0, column=0, sticky="ew")
            preview_shell.grid_propagate(False)
            preview = tk.Label(preview_shell, text="No thumbnail" if not livery.thumbnail_path else "Loading…",
                               bg="#272c35", fg=COLORS["muted"], font=("Segoe UI", 9))
            preview.place(relx=.5, rely=.5, anchor="center")
            info = tk.Frame(card, bg=COLORS["panel"])
            info.grid(row=1, column=0, sticky="ew", padx=12, pady=12)
            name = livery.metadata.get("ui_variation") or livery.metadata.get("atc_airline") or livery.metadata.get("title") or livery.name
            title = text(info, name, size=11, bold=True, wraplength=200, justify="left", height=2)
            title.pack(fill=tk.X)
            text(info, livery.metadata.get("atc_id", "Registration not provided"), color=COLORS["muted"], size=9).pack(fill=tk.X, pady=(3, 8))
            bottom = tk.Frame(info, bg=COLORS["panel"])
            bottom.pack(fill=tk.X)
            text(bottom, "● Installed", color=COLORS["green"], size=9).pack(side=tk.LEFT)
            text(bottom, self.app.format_size(livery.total_size), color=COLORS["muted"], size=9).pack(side=tk.RIGHT)
            self.cards[iid] = card
            for widget in [card, preview_shell, preview, info, title, bottom, *info.winfo_children(), *bottom.winfo_children()]:
                widget.bind("<Button-1>", lambda event, item=iid: self.choose(item, event))
                widget.bind("<MouseWheel>", self.wheel)
                widget.configure(cursor="hand2")
            if livery.thumbnail_path:
                width = max(100, self.canvas.winfo_width() // columns - 20)
                future = self.executor.submit(self.decode_image, self.generation, iid, livery.thumbnail_path, width)
                self.futures.append(future)
                card.preview = preview
        self.highlight(self.app.installed_tree.selection())

    def decode_image(self, generation, iid, path, width):
        try:
            from PIL import Image
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size, width)
            # File IO and decoding stay off the Tk thread.
            with Image.open(path) as image:
                image.thumbnail((width, 126))
                pixels = image.convert("RGBA")
            self.results.put((generation, iid, key, pixels))
        except Exception:
            self.results.put((generation, iid, None, None))

    def poll_images(self):
        if self.closed:
            return
        try:
            for _ in range(24):
                generation, iid, key, pixels = self.results.get_nowait()
                if generation != self.generation or iid not in self.cards:
                    continue
                preview = self.cards[iid].preview
                if pixels is None:
                    preview.configure(text="Preview unavailable")
                    continue
                from PIL import ImageTk
                image = self.cache.get(key)
                if image is None:
                    image = ImageTk.PhotoImage(pixels, master=self)
                    self.cache[key] = image
                    if len(self.cache) > 72:
                        self.cache.popitem(last=False)
                self.images[iid] = image
                preview.configure(image=image, text="")
        except queue.Empty:
            pass
        self.after(80, self.poll_images)

    def choose(self, iid, event=None):
        if self.app.busy:
            return
        selection = list(self.app.installed_tree.selection())
        state = getattr(event, "state", 0)
        if state & 0x4:
            selection.remove(iid) if iid in selection else selection.append(iid)
        elif state & 0x1 and selection:
            keys = list(self.items)
            start, end = sorted((keys.index(selection[-1]), keys.index(iid)))
            selection = keys[start:end + 1]
        else:
            selection = [iid]
        self.app.installed_tree.selection_set(selection)
        self.app.installed_tree.focus(iid)
        self.canvas.focus_set()
        self.highlight(selection)
        self.app.on_installed_livery_select()

    def highlight(self, selection):
        for iid, card in self.cards.items():
            card.configure(highlightbackground=COLORS["cyan"] if iid in selection else COLORS["line_soft"])

    def select_all(self, _event=None):
        if not self.app.busy:
            self.app.installed_tree.selection_set(list(self.items))
            self.app.on_installed_livery_select()
        return "break"

    def move_selection(self, delta, event):
        keys = list(self.items)
        if not keys or self.app.busy:
            return "break"
        focused = self.app.installed_tree.focus()
        index = keys.index(focused) if focused in keys else 0
        delta = (self.columns or 1) * (1 if delta > 0 else -1) if abs(delta) == 100 else delta
        index = max(0, min(len(keys) - 1, index + delta))
        if index // self.PAGE_SIZE != self.page:
            self.page = index // self.PAGE_SIZE
            self.draw()
        self.choose(keys[index], event)
        self.canvas.yview_moveto((index % self.PAGE_SIZE // max(self.columns, 1)) / max(1, math.ceil(min(len(keys), self.PAGE_SIZE) / max(self.columns, 1))))
        return "break"

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120) if event.delta else 0, "units")
        return "break"

    def change_page(self, delta):
        if self.app.busy:
            return
        self.page = max(0, min(max(0, math.ceil(len(self.items) / self.PAGE_SIZE) - 1), self.page + delta))
        self.draw()
        self.canvas.yview_moveto(0)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


def build_interface(app):
    app.option_add("*Font", ("Segoe UI", 10))
    style = ttk.Style(app)
    style.theme_use("clam")
    style.configure("PMDG.TCombobox", fieldbackground=COLORS["field"], background=COLORS["button"],
                    foreground=COLORS["text"], arrowcolor=COLORS["muted"], bordercolor=COLORS["line"],
                    lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=8)
    style.map("PMDG.TCombobox", fieldbackground=[("readonly", COLORS["field"])], foreground=[("readonly", COLORS["text"])],
              selectbackground=[("readonly", COLORS["field"])], selectforeground=[("readonly", COLORS["text"])])
    style.configure("PMDG.Treeview", background=COLORS["field"], fieldbackground=COLORS["field"], foreground=COLORS["text"],
                    rowheight=34, borderwidth=0, font=("Segoe UI", 10))
    style.configure("PMDG.Treeview.Heading", background=COLORS["panel_alt"], foreground=COLORS["muted"], padding=8, relief="flat")
    style.map("PMDG.Treeview", background=[("selected", COLORS["sidebar_active"])], foreground=[("selected", COLORS["text"])])
    style.configure("TScrollbar", background=COLORS["line"], troughcolor=COLORS["bg"], borderwidth=0, arrowsize=12,
                    arrowcolor=COLORS["muted"], bordercolor=COLORS["bg"], lightcolor=COLORS["line"], darkcolor=COLORS["line"])
    style.configure("TProgressbar", background=COLORS["cyan"], troughcolor=COLORS["sidebar"], borderwidth=0, thickness=3,
                    bordercolor=COLORS["sidebar"], lightcolor=COLORS["cyan"], darkcolor=COLORS["cyan"])
    app.option_add("*TCombobox*Listbox.background", COLORS["field"])
    app.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    app.option_add("*TCombobox*Listbox.selectBackground", COLORS["blue"])

    sidebar = tk.Frame(app, bg=COLORS["sidebar"], width=194)
    sidebar.pack(side=tk.LEFT, fill=tk.Y)
    sidebar.pack_propagate(False)
    brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
    brand.pack(fill=tk.X, padx=22, pady=(28, 32))
    try:
        app.brand_image = tk.PhotoImage(file=str(app.asset_path("assets/pmdg_livery_installer_icon.png"))).subsample(8, 8)
        tk.Label(brand, image=app.brand_image, bg=COLORS["sidebar"]).pack(anchor="w")
    except tk.TclError:
        pass
    text(brand, "LIVERY", size=20, bold=True).pack(anchor="w", pady=(10, 0))
    text(brand, "MANAGER", size=11, color=COLORS["muted"]).pack(anchor="w")
    text(sidebar, "WORKSPACE", size=8, color=COLORS["muted"], bold=True).pack(anchor="w", padx=24, pady=(0, 12))
    names = [("Installed", "My liveries", "grid"), ("Liveries", "Install liveries", "download"), ("Products", "Aircraft", "plane"),
             ("Diagnostics", "Diagnostics", "check"), ("Settings", "Settings", "settings")]
    app.nav_images = {}
    for name, title, icon in names:
        try:
            app.nav_images[name] = tk.PhotoImage(file=str(app.asset_path(f"assets/nav_{icon}.png"))).subsample(2, 2)
        except tk.TclError:
            app.nav_images[name] = None
        button = tk.Button(sidebar, text=f"   {title}", image=app.nav_images[name], compound="left", command=lambda p=name: app.show_page(p),
                           bg=COLORS["sidebar"], fg=COLORS["muted"], activebackground=COLORS["sidebar_active"], activeforeground=COLORS["text"],
                           font=("Segoe UI", 10), relief="flat", bd=0, anchor="w", padx=16, pady=13,
                           highlightthickness=1, highlightbackground=COLORS["sidebar"], highlightcolor=COLORS["cyan"], cursor="hand2")
        button.pack(fill=tk.X, padx=10, pady=3)
        app.nav_buttons[name] = button
    footer = tk.Frame(sidebar, bg=COLORS["sidebar"])
    footer.pack(side=tk.BOTTOM, fill=tk.X, padx=24, pady=22)
    text(footer, "MSFS 2024", size=10, bold=True).pack(anchor="w")
    text(footer, "PMDG community liveries", size=8, color=COLORS["muted"]).pack(anchor="w", pady=5)
    text(footer, f"v{VERSION}  ·  Community utility", size=9, color=COLORS["muted"]).pack(anchor="w", pady=(10, 0))
    main = tk.Frame(app, bg=COLORS["bg"])
    main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    task = tk.Frame(main, bg=COLORS["sidebar"], height=48)
    task.pack(side=tk.BOTTOM, fill=tk.X)
    app.progress_bar = ttk.Progressbar(task, mode="indeterminate")
    app.progress_bar.pack(side=tk.TOP, fill=tk.X)
    text(task, variable=app.status_var, color=COLORS["muted"], size=9).pack(side=tk.LEFT, padx=24, pady=10)
    app.cancel_button = app.button(task, "Cancel", app.cancel_job)
    app.cancel_button.configure(state="disabled")
    app.cancel_button.pack(side=tk.RIGHT, padx=18, pady=5)
    app.page_container = tk.Frame(main, bg=COLORS["bg"])
    app.page_container.pack(fill=tk.BOTH, expand=True, padx=26, pady=24)
    app.page_container.columnconfigure(0, weight=1)
    app.page_container.rowconfigure(0, weight=1)
    for name, builder in [("Installed", library_page), ("Liveries", install_page), ("Products", products_page),
                          ("Diagnostics", diagnostics_page), ("Settings", settings_page)]:
        page = builder(app)
        page.grid(row=0, column=0, sticky="nsew")
        app.pages[name] = page


def library_page(app):
    page, body = make_page(app, "My liveries", "Your aircraft. Your collection.", ("＋  Install livery", lambda: app.show_page("Liveries")))
    body.columnconfigure(0, weight=1)
    body.rowconfigure(2, weight=1)
    controls = tk.Frame(body, bg=COLORS["bg"])
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 14))
    text(controls, "AIRCRAFT", size=8, color=COLORS["muted"], bold=True).pack(side=tk.LEFT, padx=(0, 12))
    app.installed_package_combo = ttk.Combobox(controls, textvariable=app.package_var, state="readonly", style="PMDG.TCombobox")
    app.installed_package_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
    app.installed_package_combo.bind("<<ComboboxSelected>>", lambda _: app.refresh_installed_liveries())
    app.button(controls, "Refresh", app.refresh_packages).pack(side=tk.LEFT, padx=(10, 0))
    search = tk.Frame(body, bg=COLORS["bg"])
    search.grid(row=1, column=0, sticky="ew", pady=(0, 18))
    text(search, "Search", color=COLORS["muted"]).pack(side=tk.LEFT, padx=(0, 10))
    app.search_entry = app.entry(search, app.search_var)
    app.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=9)
    combo = ttk.Combobox(search, values=("All", "Missing thumbnail"), textvariable=app.thumbnail_filter_var, state="readonly", width=18, style="PMDG.TCombobox")
    combo.pack(side=tk.LEFT, padx=10)
    app.button(search, "Grid / List", lambda: app.toggle_library_view()).pack(side=tk.LEFT)
    split = tk.Frame(body, bg=COLORS["bg"])
    split.grid(row=2, column=0, sticky="nsew")
    split.columnconfigure(0, weight=1)
    split.rowconfigure(0, weight=1)
    app.library_views = tk.Frame(split, bg=COLORS["bg"])
    app.library_views.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
    app.library_views.rowconfigure(0, weight=1)
    app.library_views.columnconfigure(0, weight=1)
    app.table_frame = tk.Frame(app.library_views, bg=COLORS["bg"])
    app.table_frame.rowconfigure(0, weight=1)
    app.table_frame.columnconfigure(0, weight=1)
    app.installed_tree = ttk.Treeview(app.table_frame, columns=("aircraft", "livery", "files", "size", "modified"), show="headings", selectmode="extended", style="PMDG.Treeview")
    for key, title, width in (("aircraft", "Aircraft", 130), ("livery", "Livery", 220), ("files", "Files", 55), ("size", "Size", 85), ("modified", "Modified", 135)):
        app.installed_tree.heading(key, text=title)
        app.installed_tree.column(key, width=width, minwidth=50, stretch=key == "livery")
    app.installed_tree.grid(row=0, column=0, sticky="nsew")
    vs = ttk.Scrollbar(app.table_frame, orient="vertical", command=app.installed_tree.yview)
    vs.grid(row=0, column=1, sticky="ns")
    hs = ttk.Scrollbar(app.table_frame, orient="horizontal", command=app.installed_tree.xview)
    hs.grid(row=1, column=0, sticky="ew")
    app.installed_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    app.installed_tree.bind("<<TreeviewSelect>>", app.on_installed_livery_select)
    app.gallery = LiveryGallery(app.library_views, app)
    app.gallery.grid(row=0, column=0, sticky="nsew")
    app.gallery_mode = True
    inspector, detail = panel(split, padding=16)
    inspector.grid(row=0, column=1, sticky="nsew")
    inspector.configure(width=276)
    inspector.pack_propagate(False)
    text(detail, "LIVERY DETAILS", size=9, bold=True, color=COLORS["muted"]).pack(anchor="w", pady=(0, 14))
    shell = tk.Frame(detail, bg=COLORS["field"], height=140)
    shell.pack(fill=tk.X)
    shell.pack_propagate(False)
    app.thumbnail_label = tk.Label(shell, text="Select a livery", bg=COLORS["field"], fg=COLORS["muted"], font=("Segoe UI", 10))
    app.thumbnail_label.place(relx=.5, rely=.5, anchor="center")
    app.selection_count = tk.StringVar(value="No livery selected")
    text(detail, variable=app.selection_count, size=10, bold=True).pack(anchor="w", pady=(14, 8))
    app.installed_detail_text = text_area(detail, height=6)
    app.installed_detail_text.configure(font=("Segoe UI", 10))
    app.button(detail, "Copy folder path", app.copy_installed_livery_path).pack(fill=tk.X, pady=(12, 6))
    app.button(detail, "Export selected ZIP", app.export_selected_liveries).pack(fill=tk.X, pady=3)
    app.button(detail, "Uninstall selected", app.uninstall_selected_livery, danger=True).pack(fill=tk.X, pady=(8, 0))
    bottom = tk.Frame(body, bg=COLORS["bg"])
    bottom.grid(row=3, column=0, sticky="ew", pady=(14, 0))
    app.library_summary = tk.StringVar(value="0 liveries in your library")
    text(bottom, variable=app.library_summary, color=COLORS["muted"], size=9).pack(side=tk.LEFT)
    app.button(bottom, "Restore backup", app.restore_backup).pack(side=tk.RIGHT)
    app.button(bottom, "Select all results", app.gallery.select_all).pack(side=tk.RIGHT, padx=8)
    app.search_var.trace_add("write", lambda *_: app.render_liveries())
    app.thumbnail_filter_var.trace_add("write", lambda *_: app.render_liveries())
    return page


def install_page(app):
    page, body = make_page(app, "Install liveries", "Bring a new look to your next flight.")
    body.columnconfigure(0, weight=3, uniform="install")
    body.columnconfigure(1, weight=2, uniform="install")
    body.rowconfigure(1, weight=1)
    left, source = panel(body, "01   Choose your livery", "Import a compatible PMDG MSFS 2024 ZIP or extracted folder.")
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 16), pady=(0, 16))
    source_fields = tk.Frame(source, bg=COLORS["panel"])
    source_fields.pack(fill=tk.X)
    field(app, source_fields, "LIVERY SOURCE", app.livery_var)
    buttons = tk.Frame(source, bg=COLORS["panel"])
    buttons.pack(fill=tk.X, pady=(12, 4))
    app.button(buttons, "Choose ZIP…", app.choose_zip, accent=True).pack(side=tk.LEFT)
    app.button(buttons, "Choose folder…", app.choose_livery_folder).pack(side=tk.LEFT, padx=10)
    text(source, "ZIP and extracted folders supported. PTP is not supported.", color=COLORS["muted"], size=9).pack(anchor="w", pady=(14, 0))
    right, target = panel(body, "02   Confirm your aircraft", "Files are installed into its companion livery package.")
    right.grid(row=0, column=1, sticky="nsew", pady=(0, 16))
    app.package_combo = ttk.Combobox(target, textvariable=app.package_var, state="readonly", style="PMDG.TCombobox")
    app.package_combo.pack(fill=tk.X, pady=(10, 12))
    app.package_combo.bind("<<ComboboxSelected>>", lambda _: app.refresh_installed_liveries())
    text(target, variable=app.package_count_var, color=COLORS["cyan"], size=9).pack(anchor="w")
    location = tk.Frame(target, bg=COLORS["panel"])
    location.pack(fill=tk.X)
    field(app, location, "COMMUNITY FOLDER", app.community_var, browse=app.choose_community)
    links = tk.Frame(target, bg=COLORS["panel"])
    links.pack(fill=tk.X, pady=(12, 0))
    app.button(links, "Detect paths", app.detect_paths).pack(side=tk.LEFT)
    app.button(links, "Refresh aircraft", app.refresh_packages).pack(side=tk.LEFT, padx=8)
    log_panel, log = panel(body, "Activity", "Current phase, checks, and installation results.")
    log_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
    app.log_text = text_area(log, height=5)
    options_panel, options = panel(body, "03   Review & install", "Inspect the source, destination, and any conflicts before copying.")
    options_panel.grid(row=1, column=1, sticky="nsew")
    app.checkbutton(options, "Allow replacement of existing files", app.overwrite_var).pack(anchor="w", pady=(8, 6))
    app.checkbutton(options, "Keep a layout.json backup", app.backup_var).pack(anchor="w", pady=6)
    app.checkbutton(options, "Allow linked target folders", app.allow_linked_targets_var).pack(anchor="w", pady=6)
    text(options, "Full recovery backups are kept before changes to an existing package.", color=COLORS["green"], wraplength=310, justify="left", size=9).pack(anchor="w", pady=16)
    app.button(options, "Review & Install", app.install_selected, accent=True).pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0))
    return page


def products_page(app):
    page, body = make_page(app, "Aircraft", "The PMDG products available in your selected Community folder.", ("Refresh aircraft", app.refresh_packages))
    body.rowconfigure(1, weight=1)
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=2)
    text(body, variable=app.package_count_var, color=COLORS["cyan"], size=11).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
    list_panel, listing = panel(body, "Your aircraft")
    list_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
    app.product_listbox = tk.Listbox(listing, bg=COLORS["field"], fg=COLORS["text"], selectbackground=COLORS["sidebar_active"],
                                    selectforeground=COLORS["text"], highlightthickness=0, relief="flat", bd=0, font=("Segoe UI", 11), activestyle="none")
    app.product_listbox.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
    app.product_listbox.bind("<<ListboxSelect>>", app.on_product_select)
    info_panel, info = panel(body, "Package overview", "Paths, package health, and recognized liveries.")
    info_panel.grid(row=1, column=1, sticky="nsew")
    app.product_detail_text = text_area(info)
    return page


def diagnostics_page(app):
    page, body = make_page(app, "Diagnostics", "Understand a missing livery before making changes.", ("Run diagnostics", app.run_diagnostics))
    controls = tk.Frame(body, bg=COLORS["bg"])
    controls.pack(fill=tk.X, pady=(0, 18))
    app.button(controls, "Detect paths", app.detect_paths).pack(side=tk.LEFT)
    app.button(controls, "Rebuild livery layout", app.rebuild_selected_layout).pack(side=tk.LEFT, padx=10)
    app.button(controls, "Export report", app.export_diagnostics).pack(side=tk.LEFT)
    app.checkbutton(controls, "Hide personal paths", app.hide_paths_var).pack(side=tk.RIGHT)
    report_panel, report = panel(body, "Package health report", "PASS: checked successfully   ·   FAIL: action needed   ·   UNKNOWN: check in the simulator")
    report_panel.pack(fill=tk.BOTH, expand=True)
    app.diagnostics_text = text_area(report)
    app.diagnostics_text.insert("1.0", "Select an aircraft, then run diagnostics.\n\nRebuild livery layout updates the companion package index.\nIt does not replace textures or change the aircraft package.")
    return page


def settings_page(app):
    page, body = make_page(app, "Settings", "Set up your simulator and installation preferences.", ("Save settings", app._save_settings))
    body.columnconfigure(0, weight=1)
    location, paths = panel(body, "Simulator location", "Select the Community folder used by MSFS 2024.")
    location.grid(row=0, column=0, sticky="ew", pady=(0, 16))
    form = tk.Frame(paths, bg=COLORS["panel"])
    form.pack(fill=tk.X)
    field(app, form, "COMMUNITY FOLDER", app.community_var, browse=app.choose_community)
    behavior, settings = panel(body, "Installation defaults")
    behavior.grid(row=1, column=0, sticky="ew", pady=(0, 16))
    app.checkbutton(settings, "Allow replacement of existing files", app.overwrite_var).pack(anchor="w", pady=(14, 8))
    app.checkbutton(settings, "Keep a copy of layout.json before rebuilding", app.backup_var).pack(anchor="w", pady=8)
    app.checkbutton(settings, "Allow linked Community targets (Addons Linker)", app.allow_linked_targets_var).pack(anchor="w", pady=8)
    size_panel, size = panel(body, "Window size")
    size_panel.grid(row=2, column=0, sticky="ew")
    actions = tk.Frame(size, bg=COLORS["panel"])
    actions.pack(fill=tk.X, pady=(12, 0))
    for width, height in ((1180, 780), (1360, 860), (1540, 960)):
        app.button(actions, f"{width} × {height}", lambda w=width, h=height: app.geometry(f"{w}x{h}")).pack(side=tk.LEFT, padx=(0, 12))
    return page
