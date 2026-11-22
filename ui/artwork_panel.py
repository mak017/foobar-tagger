"""Read-only comparison of existing artwork and edition cover candidates."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from typing import TypeVar
import webbrowser

from PIL import Image, ImageTk

from core.cover_engine import (
    ArtworkError, ArtworkInfo, ExistingArtwork, cover_paths, existing_artwork, prepare_cover, preview_image,
)
from core.flac_input import LocalRelease
from providers.discogs import DiscogsRelease, download_image
from ui.theme import BACKGROUND


T = TypeVar("T")


def _dimensions(info: ArtworkInfo) -> str:
    return f"{info.format}  ·  {info.width:,} × {info.height:,} px  ·  {info.byte_count:,} bytes"


@dataclass(frozen=True)
class Preview:
    image: Image.Image
    summary: str
    note: str


class PreviewPane(ttk.Frame):
    def __init__(self, parent: tk.Misc, title: str, empty: str) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=14)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.photo: ImageTk.PhotoImage | None = None
        ttk.Label(self, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.choice = ttk.Combobox(self, state="disabled", width=36)
        self.choice.grid(row=1, column=0, sticky="ew")
        self.image = ttk.Label(self, text=empty, anchor="center", style="Panel.TLabel", wraplength=340)
        self.image.grid(row=2, column=0, sticky="nsew", pady=10)
        self.summary = ttk.Label(self, style="Section.TLabel", wraplength=350)
        self.summary.grid(row=3, column=0, sticky="ew")
        self.note = ttk.Label(self, style="Panel.TLabel", wraplength=350, justify="left")
        self.note.grid(row=4, column=0, sticky="ew", pady=(5, 0))

    def clear(self, text: str) -> None:
        self.image.configure(text=text, image="")
        self.photo = None
        self.summary.configure(text="")
        self.note.configure(text="")

    def show(self, preview: Preview) -> None:
        with preview.image:
            self.photo = ImageTk.PhotoImage(preview.image, master=self)
        self.image.configure(text="", image=self.photo)
        self.summary.configure(text=preview.summary)
        self.note.configure(text=preview.note)


class ArtworkWindow:
    def __init__(self, parent: tk.Tk, local: LocalRelease, release: DiscogsRelease | None) -> None:
        self.local = local
        self.release = release
        self.sources: tuple[ExistingArtwork, ...] = ()
        self.existing_warnings: tuple[str, ...] = ()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.pending: dict[object, str] = {}
        self.window = tk.Toplevel(parent, background=BACKGROUND)
        self.window.title("foobar-tagger · Artwork review")
        self.window.geometry("890x670")
        self.window.minsize(750, 570)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())

        body = ttk.Frame(self.window, padding=(18, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure((0, 1), weight=1, uniform="art")
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="Artwork review", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        edition = f"{release.title}  ·  Discogs {release.edition.release_id}" if release is not None else local.root.name
        ttk.Label(body, text=edition, style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.current = PreviewPane(body, "Existing artwork", "Looking for folder and embedded images…")
        self.current.grid(row=2, column=0, padx=(0, 5), sticky="nsew")
        self.current.choice.bind("<<ComboboxSelected>>", lambda _event: self._show_existing())
        self.candidate = PreviewPane(body, "Replacement preview", "No candidate selected")
        self.candidate.grid(row=2, column=1, padx=(5, 0), sticky="nsew")
        self.candidate.choice.bind("<<ComboboxSelected>>", lambda _event: self._show_discogs())

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(footer, text="Preview only", style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="Close", command=self.close).pack(side="right")
        self.open_button = ttk.Button(footer, text="Open local candidate…", command=self._choose_file)
        self.open_button.pack(side="right", padx=(0, 6))
        self.retry_button = ttk.Button(footer, text="Retry Discogs image", command=self._show_discogs, state="disabled")
        self.retry_button.pack(side="right", padx=(0, 6))
        if release is not None:
            entry_url = f"https://www.discogs.com/release/{release.edition.release_id}"
            ttk.Button(footer, text="Data provided by Discogs", command=lambda: webbrowser.open_new_tab(entry_url)).pack(side="left", padx=10)

        self._run(lambda: existing_artwork(local), self._list_existing, self.current.clear)
        if release is not None and release.images:
            self.candidate.choice.configure(values=tuple(
                f"{index}. {'Primary' if image.primary else 'Secondary'} image" for index, image in enumerate(release.images, 1)
            ))
            self.candidate.choice.current(0)
            self._show_discogs()
        else:
            self.candidate.clear("This edition has no available Discogs images." if release is not None else
                                 "Load an edition in the main review to see its Discogs images.")

    def _run(self, task: Callable[[], T], done: Callable[[T], None], error: Callable[[str], None]) -> None:
        future = self.pool.submit(task)
        key = object()

        def poll() -> None:
            if not future.done():
                self.pending[key] = self.window.after(45, poll)
                return
            self.pending.pop(key, None)
            try:
                result = future.result()
            except (OSError, ValueError) as exc:
                error(str(exc))
            else:
                done(result)

        self.pending[key] = self.window.after(45, poll)

    def close(self) -> None:
        for timer in self.pending.values():
            self.window.after_cancel(timer)
        self.pending.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()

    def _list_existing(self, result: tuple[tuple[ExistingArtwork, ...], tuple[str, ...]]) -> None:
        self.sources, self.existing_warnings = result
        if self.sources:
            self.current.choice.configure(values=tuple(source.label for source in self.sources))
            self.current.choice.current(0)
            self._show_existing()
        else:
            self.current.clear("No folder or embedded artwork found.")
            if self.existing_warnings:
                self.current.note.configure(text="\n".join(self.existing_warnings))

    def _show_existing(self) -> None:
        index = self.current.choice.current()
        if index < 0:
            return
        source = self.sources[index]
        self.current.choice.configure(state="disabled")
        self.current.clear("Reading existing artwork…")

        def work() -> Preview:
            info, image = preview_image(source.read())
            note = "\n".join(("Existing artwork · Edition identity unverified", *self.existing_warnings))
            return Preview(image, _dimensions(info), note)

        def done(preview: Preview) -> None:
            self.current.show(preview)
            self.current.choice.configure(state="readonly")

        def error(message: str) -> None:
            self.current.clear(message)
            self.current.choice.configure(state="readonly")

        self._run(work, done, error)

    def _show_discogs(self) -> None:
        if self.release is None or not self.release.images:
            return
        index = self.candidate.choice.current()
        if index < 0:
            self.candidate.choice.current(0)
            index = 0
        source = self.release.images[index]
        self._show_candidate(lambda: download_image(source), "Discogs associates this image with the selected edition.")

    def _choose_file(self) -> None:
        chosen = filedialog.askopenfilename(parent=self.window, title="Choose a local artwork candidate",
                                            filetypes=(("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp *.avif"),))
        if chosen:
            path = Path(chosen)
            self.candidate.choice.set(f"Local · {path.name}")
            self._show_candidate(path.read_bytes, f"Local alternative · {path.name} · Edition identity unverified")

    def _show_candidate(self, read: Callable[[], bytes], identity: str) -> None:
        self.open_button.state(("disabled",))
        self.retry_button.state(("disabled",))
        self.candidate.choice.configure(state="disabled")
        self.candidate.clear("Checking the artwork…")

        def work() -> Preview:
            data = read()
            try:
                cover = prepare_cover(data)
            except ArtworkError as exc:
                info, image = preview_image(data)
                return Preview(image, _dimensions(info), f"{identity}\nUnresolved · {exc}")
            target, present = cover_paths(self.local.root, cover)
            primary = next((path.name for path in present if path.name.casefold() == target.name.casefold()), None)
            alternate = tuple(path.name for path in present if path.name.casefold() != target.name.casefold())
            target_info = f"Existing target: {primary}" if primary else f"New target: {target.name}"
            if alternate:
                target_info += " · Other cover files: " + ", ".join(alternate)
            _, image = preview_image(cover.data)
            return Preview(image, _dimensions(cover.info), f"{identity}\n{cover.change} · {target_info}")

        def ready() -> None:
            self.open_button.state(("!disabled",))
            if self.release is not None and self.release.images:
                self.retry_button.state(("!disabled",))
                self.candidate.choice.configure(state="readonly")

        def done(preview: Preview) -> None:
            self.candidate.show(preview)
            ready()

        def error(message: str) -> None:
            self.candidate.clear(message)
            ready()

        self._run(work, done, error)
