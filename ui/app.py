"""Tkinter review session for one local FLAC release."""

from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TypeVar

from core.clue_parser import parse_folder_clues
from core.flac_input import LocalRelease, inspect_release
from core.rules_engine import ManualDate, MetadataPolicy, ReleaseContext, propose_metadata
from core.tag_writer import ApplyResult, apply_metadata, find_staged_files
from providers.discogs import DiscogsRelease, load_release
from ui.diff_table import DiffTable
from ui.theme import BACKGROUND, INPUT, TEXT, apply_theme


T = TypeVar("T")


class ReviewWindow:
    def __init__(self, root: tk.Tk, discogs_url: str, accepted_styles: frozenset[str]) -> None:
        self.root = root
        self.local: LocalRelease | None = None
        self.release: DiscogsRelease | None = None
        self.mapping: dict[str, str] = {}
        self.manual_date: ManualDate | None = None
        self.manual_styles: tuple[str, ...] | None = None
        self.policy = MetadataPolicy(accepted_styles=accepted_styles)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.busy = False
        self.writing = False
        self.needs_refresh = False
        self.path_text = tk.StringVar(master=root, value="Open a folder or select FLAC files.")
        self.scope_text = tk.StringVar(master=root, value="No release open")
        self.edition_text = tk.StringVar(master=root, value="Confirm the file grouping to load an edition.")
        self.warnings = tk.StringVar(master=root)
        self.status = tk.StringVar(master=root)
        self.approval_text = tk.StringVar(master=root)
        self.url = tk.StringVar(master=root, value=discogs_url)
        self.confirmed = tk.BooleanVar(master=root)
        self._layout()
        self.url.trace_add("write", self._url_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._controls()

    def _layout(self) -> None:
        root = self.root
        root.title("foobar-tagger · Release review")
        root.geometry("1190x760")
        root.minsize(930, 610)
        apply_theme(root)
        shell = ttk.Frame(root, padding=(20, 16))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Release review", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(header, textvariable=self.path_text, style="Muted.TLabel", width=60).grid(row=1, column=0, columnspan=2, sticky="ew")
        self.folder_button = ttk.Button(header, text="Open folder…", command=self._choose_folder)
        self.folder_button.grid(row=0, column=2, padx=(8, 0))
        self.files_button = ttk.Button(header, text="Select FLACs…", command=self._choose_files)
        self.files_button.grid(row=0, column=3, padx=(6, 0))

        provider = ttk.Frame(shell, style="Panel.TFrame", padding=(12, 10))
        provider.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        provider.columnconfigure(1, weight=1)
        ttk.Label(provider, text="Discogs release URL", style="Panel.TLabel").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.url_entry = ttk.Entry(provider, textvariable=self.url)
        self.url_entry.grid(row=0, column=1, sticky="ew")
        self.url_entry.bind("<Return>", lambda _event: self._load_edition())
        self.load_button = ttk.Button(provider, text="Load edition", command=self._load_edition)
        self.load_button.grid(row=0, column=2, padx=(8, 0))
        edition_label = ttk.Label(provider, textvariable=self.edition_text, style="Panel.TLabel")
        edition_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        edition_label.bind("<Configure>", self._wrap)

        panes = ttk.Panedwindow(shell, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        local = ttk.Frame(panes, style="Panel.TFrame", padding=12, width=310)
        review = ttk.Frame(panes, style="Panel.TFrame", padding=12)
        panes.add(local, weight=0)
        panes.add(review, weight=1)
        local.columnconfigure(0, weight=1)
        local.rowconfigure(2, weight=1)
        ttk.Label(local, text="Release files", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(local, textvariable=self.scope_text, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 8))
        file_area = ttk.Frame(local, style="Panel.TFrame")
        file_area.grid(row=2, column=0, sticky="nsew")
        file_area.columnconfigure(0, weight=1)
        file_area.rowconfigure(0, weight=1)
        self.file_tree = ttk.Treeview(file_area, columns=("scope", "track", "position"),
                                     show="headings", selectmode="browse", height=6)
        for key, heading, width in (("scope", "Scope", 70), ("track", "File", 155), ("position", "Map", 60)):
            self.file_tree.heading(key, text=heading)
            self.file_tree.column(key, width=width, minwidth=42, stretch=key == "track")
        scroll = ttk.Scrollbar(file_area, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=scroll.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.file_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_mapping())
        self.file_tree.bind("<space>", self._toggle_file_key)
        self.file_tree.bind("<Double-1>", self._toggle_file_click)
        self.local_tag = ttk.Label(local, style="Panel.TLabel", wraplength=284)
        self.local_tag.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        local_actions = ttk.Frame(local, style="Panel.TFrame")
        local_actions.grid(row=4, column=0, sticky="ew", pady=(4, 8))
        self.toggle_file_button = ttk.Button(local_actions, text="Include / exclude", command=self._toggle_file)
        self.toggle_file_button.pack(side="left")
        self.refresh_button = ttk.Button(local_actions, text="Refresh files", command=self._refresh)
        self.refresh_button.pack(side="right")
        self.confirm_button = ttk.Checkbutton(local, text="Confirm release grouping", variable=self.confirmed,
                                              command=self._rebuild)
        self.confirm_button.grid(row=5, column=0, sticky="w")
        ttk.Label(local, text="Map selected file to Discogs track", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 2))
        self.track_choice = ttk.Combobox(local, state="disabled", width=29)
        self.track_choice.grid(row=7, column=0, sticky="ew")
        self.track_choice.bind("<<ComboboxSelected>>", self._map_file)
        warning_label = ttk.Label(local, textvariable=self.warnings, style="Panel.TLabel", foreground="#f3bd76",
                                  wraplength=284)
        warning_label.grid(row=8, column=0, sticky="ew", pady=(6, 0))

        review.columnconfigure(0, weight=1)
        review.rowconfigure(1, weight=1)
        review_bar = ttk.Frame(review, style="Panel.TFrame")
        review_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(review_bar, text="Metadata changes", style="Section.TLabel").pack(side="left")
        self.styles_button = ttk.Button(review_bar, text="Edit styles…", command=self._edit_styles)
        self.styles_button.pack(side="right")
        self.date_button = ttk.Button(review_bar, text="Edit date…", command=self._edit_date)
        self.date_button.pack(side="right", padx=(0, 6))
        self.diff = DiffTable(review)
        self.diff.grid(row=1, column=0, sticky="nsew")
        self.diff.bind("<<ApprovalsChanged>>", lambda _event: self._controls())
        self.diff.tree.bind("<Double-1>", self._edit_field)
        detail = tk.Text(review, height=4, wrap="word", font=("Segoe UI", 10), background=INPUT,
                         foreground=TEXT, borderwidth=0, highlightthickness=0, padx=8, pady=6, takefocus=False)
        detail.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.diff.detail.trace_add("write", lambda *_args: self._fill_text(detail, self.diff.detail.get()))
        self._fill_text(detail, self.diff.detail.get())

        footer = ttk.Frame(shell)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(2, weight=1)
        self.approve_button = ttk.Button(footer, text="Select all changes", command=lambda: self.diff.pick_all(True))
        self.approve_button.grid(row=0, column=0, padx=(0, 6))
        self.clear_button = ttk.Button(footer, text="Clear selection", command=lambda: self.diff.pick_all(False))
        self.clear_button.grid(row=0, column=1)
        ttk.Label(footer, textvariable=self.approval_text, style="Muted.TLabel").grid(row=0, column=2, padx=12, sticky="w")
        self.apply_button = ttk.Button(footer, text="Apply approved…", style="Apply.TButton", command=self._apply)
        self.apply_button.grid(row=0, column=3, sticky="e")
        status = ttk.Label(footer, textvariable=self.status, style="Muted.TLabel")
        status.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        status.bind("<Configure>", self._wrap)

    @staticmethod
    def _fill_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    @staticmethod
    def _wrap(event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, ttk.Label):
            widget.configure(wraplength=max(1, event.width - 2))

    def _controls(self) -> None:
        local_available = self.local is not None and not self.busy
        local = local_available and not self.needs_refresh
        grouped = local and self.confirmed.get()
        editing = grouped and self.release is not None
        approval = self.diff.approval
        approved = approval.selected() if approval is not None else ()
        self.approval_text.set(f"{len(approved)} approved" if approval is not None else "")
        for widget, enabled in (
            (self.folder_button, not self.busy), (self.files_button, not self.busy),
            (self.url_entry, not self.busy), (self.load_button, grouped and bool(self.url.get().strip())),
            (self.confirm_button, local), (self.refresh_button, local_available),
            (self.toggle_file_button, local and bool(self.file_tree.selection())),
            (self.styles_button, editing), (self.date_button, editing),
            (self.approve_button, editing and bool(approval and approval.decisions)),
            (self.clear_button, editing and bool(approved)), (self.apply_button, editing and bool(approved)),
        ):
            widget.state(("!disabled" if enabled else "disabled",))
        self.diff.locked = self.busy
        self._sync_mapping()

    def _start(self, text: str, task: Callable[[], T], done: Callable[[T], None], *, writing: bool = False) -> None:
        if self.busy:
            return
        self.busy, self.writing = True, writing
        self.status.set(text)
        self._controls()
        future = self.pool.submit(task)
        self.root.after(45, self._finish, future, done)

    def _finish(self, future: Future[T], done: Callable[[T], None]) -> None:
        if not future.done():
            self.root.after(45, self._finish, future, done)
            return
        self.busy, self.writing = False, False
        try:
            result = future.result()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
        else:
            done(result)
        self._controls()

    def _close(self) -> None:
        if self.writing:
            self.status.set("Wait for the current write to finish before closing.")
            return
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def open_paths(self, paths: list[Path], release_folder: Path | None = None) -> None:
        def opened(local: LocalRelease) -> None:
            self.local = local
            self.needs_refresh = False
            self.release = None
            self.mapping.clear()
            self.manual_date = self.manual_styles = None
            self.confirmed.set(False)
            self.status.set("Confirm which files belong to this release.")
            staged = find_staged_files(local)
            if staged:
                self.status.set("Review leftover staging copies after confirming that no other write is active.")
                self._report("Leftover staging files", "\n".join(str(path) for path in staged))
            self._show_files()
            self._rebuild()

        self._start("Reading FLAC tags…", lambda: inspect_release(paths, release_folder), opened)

    def _choose_folder(self) -> None:
        choice = filedialog.askdirectory(parent=self.root)
        if choice:
            self.open_paths([Path(choice)])

    def _choose_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=(("FLAC files", "*.flac"),))
        if paths:
            self.open_paths([Path(path) for path in paths])

    def _refresh(self) -> None:
        if self.local is None or self.busy:
            return
        previous = self.local
        self.needs_refresh = True
        self.confirmed.set(False)
        self._rebuild()

        def refreshed(local: LocalRelease) -> None:
            self.local = replace(local, selected_file_ids=previous.selected_file_ids.intersection(local.file_states))
            self.needs_refresh = False
            self.mapping = {file_id: track for file_id, track in self.mapping.items() if file_id in local.file_states}
            self.status.set("Files refreshed. Confirm the grouping and review any remaining changes.")
            self._show_files()
            self._rebuild()

        self._start("Refreshing file comparisons…", lambda: inspect_release([previous.root]), refreshed)

    def _show_files(self) -> None:
        local = self.local
        if local is None:
            return
        selected = self.file_tree.selection()
        self.file_tree.delete(*self.file_tree.get_children())
        self.path_text.set(f"{local.root.name}   ·   {local.root.parent}")
        self.scope_text.set(f"{len(local.selected_file_ids)} selected · {len(local.files) - len(local.selected_file_ids)} context only")
        tracks = {} if self.release is None else {track.track_id: track.position or "Confirmed" for track in self.release.edition.tracks}
        for file in local.files:
            in_selection = file.file_id in local.selected_file_ids
            position = tracks.get(self.mapping.get(file.file_id, ""), "—") if in_selection else "—"
            self.file_tree.insert("", "end", iid=file.file_id,
                                  values=("Selected" if in_selection else "Context", file.file_id, position))
        if selected and self.file_tree.exists(selected[0]):
            self.file_tree.selection_set(selected[0])
        elif local.files:
            first = next((file.file_id for file in local.files if file.file_id in local.selected_file_ids), local.files[0].file_id)
            self.file_tree.selection_set(first)
            self.file_tree.focus(first)
        self.warnings.set(local.warnings[0] if local.warnings else "")

    def _toggle_file_key(self, _event: tk.Event) -> str:
        self._toggle_file()
        return "break"

    def _toggle_file_click(self, event: tk.Event) -> None:
        if self.file_tree.identify_row(event.y):
            self._toggle_file()

    def _toggle_file(self) -> None:
        selected = self.file_tree.selection()
        if not selected or self.local is None or self.busy or self.needs_refresh:
            return
        file_id = selected[0]
        self.local = replace(self.local, selected_file_ids=self.local.selected_file_ids.symmetric_difference({file_id}))
        if file_id not in self.local.selected_file_ids:
            self.mapping.pop(file_id, None)
        self._show_files()
        self._rebuild()

    def _sync_mapping(self) -> None:
        selection = self.file_tree.selection()
        file_id = selection[0] if selection else ""
        local, release = self.local, self.release
        if local is not None:
            file = next((file for file in local.files if file.file_id == file_id), None)
            title = "" if file is None else " · ".join(file.tags.get("%TITLE%", ()) + file.tags.get("%ALBUM%", ()))
            self.local_tag.configure(text=title)
        if self.busy or local is None or release is None or not self.confirmed.get() or file_id not in local.selected_file_ids:
            self.track_choice.configure(state="disabled", values=())
            self.track_choice.set("")
            return
        tracks = release.edition.tracks
        self.track_choice.configure(state="readonly", values=("Unmapped", *(
            f"{index}. {track.position or '—'} · {track.title or 'Untitled'}" for index, track in enumerate(tracks, 1)
        )))
        track_id = self.mapping.get(file_id)
        match = next((index for index, track in enumerate(tracks, 1) if track.track_id == track_id), 0)
        self.track_choice.current(match)

    def _map_file(self, _event: tk.Event) -> None:
        index = self.track_choice.current()
        selection = self.file_tree.selection()
        if self.busy or not selection or self.release is None or index < 0:
            return
        file_id = selection[0]
        if index == 0:
            self.mapping.pop(file_id, None)
        else:
            track = self.release.edition.tracks[index - 1]
            if any(mapped == track.track_id for other, mapped in self.mapping.items() if other != file_id):
                self.status.set("That Discogs track is already mapped to another selected file.")
                self._sync_mapping()
                return
            self.mapping[file_id] = track.track_id
        self.status.set("Mapping updated. Review the new proposal before applying.")
        self._show_files()
        self._rebuild()

    def _url_changed(self, *_args: object) -> None:
        if self.release is not None:
            self.release = None
            self.mapping.clear()
            self.manual_date = self.manual_styles = None
            self._show_files()
        self._rebuild()

    def _load_edition(self) -> None:
        url = self.url.get().strip()
        if self.busy or self.local is None or not self.confirmed.get() or not url:
            return
        self.release = None
        self.mapping.clear()
        self.manual_date = self.manual_styles = None
        self._show_files()
        self._rebuild()

        def loaded(release: DiscogsRelease) -> None:
            self.release = release
            self.status.set("Select a file and confirm its Discogs track to review changes.")
            self._rebuild()

        self._start("Loading the Discogs edition…", lambda: load_release(url), loaded)

    def _rebuild(self) -> None:
        local, release = self.local, self.release
        if local is None or self.needs_refresh or not self.confirmed.get():
            self.edition_text.set("Refresh the release files before reviewing." if self.needs_refresh else
                                  "Confirm the file grouping to load an edition.")
            self.diff.show(None)
        elif release is None:
            self.edition_text.set("Load a specific Discogs edition.")
            self.diff.show(None)
        else:
            edition = release.edition
            cues = [f"Discogs {edition.release_id} · {release.title} · {edition.media_type or 'Media unresolved'}"]
            if not edition.tracklist_complete:
                cues.append("Incomplete tracklist")
            clues = parse_folder_clues(local.root.name)
            if clues.media_type and edition.media_type and clues.media_type != edition.media_type:
                cues.append(f"Folder media: {clues.media_type}")
            if clues.artist and clues.album and clues.album.casefold() != release.title.strip().casefold():
                cues.append(f"Folder album: {clues.album}")
            self.edition_text.set("  |  ".join(cues))
            context = ReleaseContext(local.files, local.selected_file_ids, edition, self.mapping, membership_complete=True,
                                     manual_genres=self.manual_styles, manual_date=self.manual_date)
            try:
                self.diff.show(propose_metadata(context, self.policy))
            except ValueError as exc:
                self.diff.show(None)
                self.status.set(str(exc))
        self._controls()

    def _edit_field(self, event: tk.Event) -> None:
        if self.diff.tree.identify_column(event.x) == "#1":
            return
        decision = self.diff.rows.get(self.diff.tree.identify_row(event.y))
        if decision is not None:
            if decision.field in ("%DATE%", "%RELEASE DATE%"):
                self._edit_date()
            elif decision.field == "%GENRE%":
                self._edit_styles()

    def _edit_date(self) -> None:
        if self.busy or self.release is None or not self.confirmed.get():
            return
        date = self.manual_date
        result = self._edit("Release date", (("Release date or year (blank uses provider evidence)", date.value if date else ""),),
                            ("Explicitly clear the existing exact date", bool(date and date.clear_exact_date)))
        if result is not None:
            (value,), clear = result
            if clear and not value:
                self.status.set("Enter a year to request deletion of the exact date.")
                return
            self.manual_date = ManualDate(value, clear) if value else None
            self.status.set("Date updated. Review the new proposal before applying.")
            self._rebuild()

    def _edit_styles(self) -> None:
        if self.busy or self.release is None or not self.confirmed.get():
            return
        styles = tuple(dict.fromkeys(style for source in self.release.edition.genre_sources or () for style in source.styles))
        result = self._edit("Release styles", (
            (f"Discogs styles: {', '.join(styles) or 'None supplied'}\nAccepted vocabulary (comma separated)", ", ".join(sorted(self.policy.accepted_styles))),
            ("Selected styles (blank uses Discogs styles)", ", ".join(self.manual_styles or ())),
        ))
        if result is not None:
            (vocabulary, chosen), _ = result
            self.policy = replace(self.policy, accepted_styles=frozenset(style.strip() for style in vocabulary.split(",") if style.strip()))
            self.manual_styles = tuple(style.strip() for style in chosen.split(",") if style.strip()) or None
            self.status.set("Styles updated. Review the new proposal before applying.")
            self._rebuild()

    def _edit(self, title: str, fields: tuple[tuple[str, str], ...],
              checkbox: tuple[str, bool] | None = None) -> tuple[tuple[str, ...], bool] | None:
        dialog = tk.Toplevel(self.root, background=BACKGROUND)
        dialog.title(title)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        entries: list[ttk.Entry] = []
        for label, value in fields:
            ttk.Label(body, text=label, wraplength=420).pack(anchor="w")
            entry = ttk.Entry(body, width=57)
            entry.insert(0, value)
            entry.pack(fill="x", pady=(4, 10))
            entries.append(entry)
        checked = tk.BooleanVar(master=dialog, value=checkbox[1] if checkbox else False)
        if checkbox:
            ttk.Checkbutton(body, text=checkbox[0], variable=checked).pack(anchor="w")
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        result = None

        def accept() -> None:
            nonlocal result
            result = (tuple(entry.get().strip() for entry in entries), checked.get())
            dialog.destroy()

        ttk.Button(actions, text="Update proposal", command=accept).pack(side="right")
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_visibility()
        dialog.grab_set()
        entries[0].focus_set()
        dialog.wait_window()
        return result

    def _apply(self) -> None:
        local, proposal, approval = self.local, self.diff.proposal, self.diff.approval
        if self.busy or not self.confirmed.get() or local is None or proposal is None or approval is None:
            return
        approved = approval.selected()
        if not approved:
            return
        files = len({decision.file_id for decision in approved})
        if not messagebox.askyesno("Apply approved metadata", f"Write {len(approved)} approved field changes to {files} FLAC file(s)?",
                                   default="no", parent=self.root):
            return
        self.diff.pick_all(False)
        self._start("Verifying and applying approved metadata…", lambda: apply_metadata(local, proposal, approved),
                    self._after_apply, writing=True)

    def _after_apply(self, result: ApplyResult) -> None:
        counts = Counter(field.status for field in result.fields)
        text = [f"{counts['changed']} changed · {counts['failed']} failed · {counts['skipped']} skipped", ""]
        for field in result.fields:
            text.append(f"{field.file_id} · {field.field} · {field.status.title()}\n  {field.reason}")
        if result.inconsistent_fields:
            text.extend(("", "Inconsistent fields: " + ", ".join(result.inconsistent_fields)))
        text.extend(result.warnings)
        self._report("Metadata results", "\n".join(text))
        self._refresh()

    def _report(self, title: str, body: str) -> None:
        popup = tk.Toplevel(self.root, background=BACKGROUND)
        popup.title(title)
        popup.transient(self.root)
        popup.geometry("680x420")
        frame = ttk.Frame(popup, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        details = tk.Text(frame, wrap="word", font=("Segoe UI", 10), background=INPUT, foreground=TEXT,
                          borderwidth=0, padx=10, pady=8)
        details.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=details.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        details.configure(yscrollcommand=scroll.set)
        self._fill_text(details, body)
        ttk.Button(frame, text="Close", command=popup.destroy).grid(row=1, column=0, sticky="e", pady=(8, 0))
        popup.bind("<Escape>", lambda _event: popup.destroy())


def run_review(paths: list[Path], release_folder: Path | None, discogs_url: str, accepted_styles: frozenset[str]) -> int:
    root = tk.Tk()
    app = ReviewWindow(root, discogs_url, accepted_styles)
    if paths:
        app.open_paths(paths, release_folder)
    root.mainloop()
    return 0
