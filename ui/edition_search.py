"""Find Discogs editions and review their clues before choosing one."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk
from typing import TypeVar
import webbrowser

from core.clue_parser import parse_folder_clues
from core.flac_input import LocalRelease
from providers.discogs import DiscogsRelease, ReleaseCandidate, ReleaseQuery, SearchPage, load_release, search_releases
from ui.theme import BACKGROUND, INPUT, TEXT


T = TypeVar("T")
QUERY_FIELDS = (("text", "Keywords"), ("catalog", "Catalog number"), ("artist", "Artist"),
                ("title", "Release title"), ("format_name", "Format"))
FORMAT_CLUES = {"WEB": {"file"}, "CD": {"cd"}, "DVD": {"dvd"}, "Vinyl": {"vinyl", "lp"}, "LP": {"lp"}}


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    minutes, seconds_at_end = divmod(round(seconds), 60)
    hours, minutes_at_end = divmod(minutes, 60)
    return f"{hours}:{minutes_at_end:02}:{seconds_at_end:02}" if hours else f"{minutes}:{seconds_at_end:02}"


def _compare(label: str, clue: str | None, actual: str, *, match: bool | None = None) -> str:
    if not clue:
        return f"{label}: {actual or 'Unspecified'}"
    if not actual:
        return f"{label}: Unspecified · Folder clue: {clue}"
    if match is None:
        match = " ".join(clue.casefold().split()) == " ".join(actual.casefold().split())
    return f"{label}: {actual} · " + ("Matches folder clue" if match else f"Folder clue: {clue}")


class EditionSearchWindow:
    def __init__(
        self, parent: tk.Tk, local: LocalRelease,
        on_use: Callable[[ReleaseCandidate, DiscogsRelease | None], None],
    ) -> None:
        self.local = local
        self.on_use = on_use
        self.clues = parse_folder_clues(local.root.name)
        self.page: SearchPage | None = None
        self.query: ReleaseQuery | None = None
        self.candidates: dict[str, ReleaseCandidate] = {}
        self.previews: dict[str, DiscogsRelease] = {}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending: str | None = None
        self.busy = False
        self.window = tk.Toplevel(parent, background=BACKGROUND)
        self.window.title("foobar-tagger · Find Discogs editions")
        self.window.geometry("1030x710")
        self.window.minsize(790, 595)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.status = tk.StringVar(master=self.window)
        self.entries = {field: tk.StringVar(master=self.window) for field, _ in QUERY_FIELDS}
        if self.clues.catalog:
            self.entries["catalog"].set(self.clues.catalog)
        else:
            self._fill_names()
        self._layout()
        self._controls()
        self.window.wait_visibility()
        self.window.grab_set()
        self.entries_widgets["catalog" if self.clues.catalog else "title"].focus_set()
        self.window.after_idle(self._search)

    def _layout(self) -> None:
        body = ttk.Frame(self.window, padding=(17, 13))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text="Find Discogs editions", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=self.local.root.name, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 9))

        filters = ttk.Frame(body, style="Panel.TFrame", padding=10)
        filters.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.entries_widgets: dict[str, ttk.Entry] = {}
        for col, (field, label) in enumerate(QUERY_FIELDS):
            filters.columnconfigure(col, weight=2 if field in ("text", "title") else 1)
            ttk.Label(filters, text=label, style="Panel.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 6))
            entry = ttk.Entry(filters, textvariable=self.entries[field], width=12)
            entry.grid(row=1, column=col, sticky="ew", padx=(0, 6), pady=(2, 0))
            entry.bind("<Return>", lambda _event: self._search())
            self.entries_widgets[field] = entry
        self.search_button = ttk.Button(filters, text="Search", command=self._search)
        self.search_button.grid(row=1, column=len(QUERY_FIELDS), sticky="e")
        self.names_button = ttk.Button(filters, text="Use artist and title clues", command=self._search_names)
        self.names_button.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        results = ttk.Frame(body, style="Panel.TFrame", padding=10)
        results.grid(row=3, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(results, columns=("title", "catalog", "format", "year", "country", "id"),
                                  show="headings", selectmode="browse", height=8)
        for key, heading, width, minimum, stretch in (
            ("title", "Edition", 270, 195, True), ("catalog", "Catalog", 115, 78, True),
            ("format", "Format", 155, 113, True), ("year", "Year", 55, 48, False),
            ("country", "Country", 96, 78, True), ("id", "Release ID", 84, 75, False),
        ):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, minwidth=minimum, stretch=stretch)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._show_selection())
        controls = ttk.Frame(results, style="Panel.TFrame")
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.previous = ttk.Button(controls, text="Previous page", command=lambda: self._page(-1))
        self.next = ttk.Button(controls, text="Next page", command=lambda: self._page(1))
        self.previous.pack(side="left")
        self.next.pack(side="left", padx=(6, 0))
        ttk.Label(controls, textvariable=self.status, style="Panel.TLabel", wraplength=580).pack(side="left", padx=14)

        evidence = ttk.Frame(body)
        evidence.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        evidence.columnconfigure((0, 1), weight=1, uniform="evidence")
        self.clue_panel = self._evidence(evidence, 0, "Edition clues")
        self.track_panel = self._evidence(evidence, 1, "Track evidence")
        self._text(self.clue_panel, "Select a result to compare it with the folder clues.")
        self._text(self.track_panel, self._local_tracks())

        footer = ttk.Frame(body)
        footer.grid(row=5, column=0, sticky="ew", pady=(9, 0))
        ttk.Button(footer, text="Data provided by Discogs", command=self._open_source).pack(side="left")
        self.choose = ttk.Button(footer, text="Use selected edition", style="Apply.TButton", command=self._use)
        self.choose.pack(side="right")
        self.preview = ttk.Button(footer, text="Preview tracklist", command=self._preview_tracks)
        self.preview.pack(side="right", padx=(0, 6))
        ttk.Button(footer, text="Cancel", command=self.close).pack(side="right", padx=(0, 6))

    @staticmethod
    def _evidence(parent: ttk.Frame, column: int, title: str) -> tk.Text:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=8)
        frame.grid(row=0, column=column, sticky="ew", padx=(0, 5) if column == 0 else (5, 0))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        view = tk.Text(frame, height=6, width=36, wrap="word", font=("Segoe UI", 10), background=INPUT,
                       foreground=TEXT, borderwidth=0, padx=7, pady=4, state="disabled")
        view.grid(row=1, column=0, sticky="ew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=view.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        view.configure(yscrollcommand=scroll.set)
        return view

    @staticmethod
    def _text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _selected(self) -> ReleaseCandidate | None:
        selection = self.table.selection()
        return self.candidates.get(selection[0]) if selection else None

    def _controls(self) -> None:
        candidate = self._selected()
        selected = candidate is not None and not self.busy
        for entry in self.entries_widgets.values():
            entry.state(("disabled" if self.busy else "!disabled",))
        for widget, enabled in (
            (self.search_button, not self.busy), (self.names_button, not self.busy and bool(self.clues.artist or self.clues.album)),
            (self.preview, selected and candidate is not None and candidate.release_id not in self.previews), (self.choose, selected),
            (self.previous, not self.busy and self.page is not None and self.page.page > 1),
            (self.next, not self.busy and self.page is not None and self.page.page < self.page.pages),
        ):
            widget.state(("!disabled" if enabled else "disabled",))

    def _run(self, status: str, work: Callable[[], T], done: Callable[[T], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.set(status)
        self._controls()
        future = self.pool.submit(work)
        self.pending = self.window.after(45, self._finish, future, done)

    def _finish(self, future: Future[T], done: Callable[[T], None]) -> None:
        if not future.done():
            self.pending = self.window.after(45, self._finish, future, done)
            return
        self.busy = False
        self.pending = None
        try:
            result = future.result()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
        else:
            done(result)
        self._controls()

    def _fill_names(self) -> None:
        self.entries["catalog"].set("")
        self.entries["artist"].set(self.clues.artist or "")
        self.entries["title"].set(self.clues.album or "")

    def _search_names(self) -> None:
        self._fill_names()
        self._search()

    def _search(self) -> None:
        if self.busy:
            return
        query = ReleaseQuery(**{key: value.get().strip() for key, value in self.entries.items()})
        self._load_page(query, 1)

    def _page(self, step: int) -> None:
        if self.page is not None and self.query is not None:
            self._load_page(self.query, self.page.page + step)

    def _load_page(self, query: ReleaseQuery, page: int) -> None:
        def done(result: SearchPage) -> None:
            self.query, self.page = query, result
            self.table.delete(*self.table.get_children())
            self.candidates = {item.release_id: item for item in result.candidates}
            for candidate in result.candidates:
                self.table.insert("", "end", iid=candidate.release_id, values=(
                    candidate.title, candidate.catalog or "—", ", ".join(candidate.formats) or "—",
                    candidate.year or "—", candidate.country or "—", candidate.release_id,
                ))
            status = (f"Page {result.page} of {result.pages} · {len(result.candidates)} editions" if result.candidates else
                      "No editions found. Try broader clues or load a specific release URL.")
            self.status.set(status)
            self._show_selection()

        self._run("Searching Discogs editions…", lambda: search_releases(query, page), done)

    def _local_tracks(self) -> str:
        lines = ["Selected FLACs"]
        for file in self.local.files:
            if file.file_id in self.local.selected_file_ids:
                name = " / ".join(file.tags.get("%TITLE%", ())) or file.file_id
                lines.append(f"{duration_text(file.duration_seconds) or '—'}  ·  {name}")
        return "\n".join(lines)

    def _show_selection(self) -> None:
        candidate = self._selected()
        if candidate is None:
            self._text(self.clue_panel, "Select a result to compare it with the folder clues.")
        else:
            clues = self.clues
            artist, separator, title = candidate.title.partition(" - ")
            formats = ", ".join(candidate.formats)
            expected_formats = FORMAT_CLUES.get(clues.media_type or "", set())
            media_matches = bool(expected_formats.intersection(value.casefold() for value in candidate.formats))
            self._text(self.clue_panel, "\n".join((
                _compare("Catalog", clues.catalog, candidate.catalog),
                _compare("Artist", clues.artist, artist if separator else ""),
                _compare("Title", clues.album, title if separator else candidate.title),
                _compare("Format", clues.media_type, formats, match=media_matches),
                _compare("Edition year", clues.year, candidate.year),
                f"Country: {candidate.country or 'Unspecified'} · Label: {', '.join(candidate.labels) or 'Unspecified'}",
            )))
        loaded = self.previews.get(candidate.release_id) if candidate is not None else None
        if loaded is None:
            self._text(self.track_panel, self._local_tracks())
        else:
            lines = [f"Discogs edition {loaded.edition.release_id}"]
            if not loaded.edition.tracklist_complete:
                lines.append("Incomplete tracklist")
            lines.extend(f"{track.position or '—'} · {duration_text(track.duration_seconds) or '—'} · {track.title or 'Untitled'}"
                         for track in loaded.edition.tracks)
            self._text(self.track_panel, "\n".join((*lines, "", self._local_tracks())))
        self._controls()

    def _preview_tracks(self) -> None:
        candidate = self._selected()
        if self.busy or candidate is None:
            return
        if candidate.release_id in self.previews:
            return

        def done(release: DiscogsRelease) -> None:
            self.previews[candidate.release_id] = release
            self.status.set(f"Tracklist loaded for edition {candidate.release_id}.")
            self._show_selection()

        self._run("Loading the selected edition's tracklist…", lambda: load_release(candidate.url), done)

    def _use(self) -> None:
        candidate = self._selected()
        if candidate is None or self.busy:
            return
        preview = self.previews.get(candidate.release_id)
        self.close()
        self.on_use(candidate, preview)

    def _open_source(self) -> None:
        candidate = self._selected()
        webbrowser.open_new_tab(candidate.url if candidate is not None else "https://www.discogs.com/")

    def close(self) -> None:
        if self.pending is not None:
            self.window.after_cancel(self.pending)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()
