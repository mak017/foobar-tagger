"""The review table and its explicit field approvals."""

import tkinter as tk
from tkinter import ttk

from core.rules_engine import FieldDecision, MetadataProposal
from core.tag_writer import DecisionKey


class ReviewApproval:
    def __init__(self, proposal: MetadataProposal) -> None:
        self.decisions = tuple(decision for decision in proposal.decisions if decision.state in ("SET", "DELETE"))
        self._keys = {(decision.file_id, decision.field) for decision in self.decisions}
        self._linked: dict[DecisionKey, frozenset[DecisionKey]] = {}
        self._approved: set[DecisionKey] = set()
        for group in proposal.approval_groups:
            keys = frozenset((file_id, field) for file_id, field, _ in group.changes)
            for key in keys:
                self._linked[key] = keys

    def toggle(self, key: DecisionKey) -> None:
        if key not in self._keys:
            return
        linked = self._linked.get(key, frozenset({key}))
        if key in self._approved:
            self._approved.difference_update(linked)
        else:
            self._approved.update(linked)

    def set_all(self, approved: bool) -> None:
        self._approved = set(self._keys) if approved else set()

    def is_approved(self, key: DecisionKey) -> bool:
        return key in self._approved

    def selected(self) -> tuple[FieldDecision, ...]:
        return tuple(decision for decision in self.decisions if (decision.file_id, decision.field) in self._approved)


def _display(values: tuple[str, ...] | None) -> str:
    if values is None:
        return "—"
    return " | ".join(value or "(empty)" for value in values) or "(empty)"


class DiffTable(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.proposal: MetadataProposal | None = None
        self.approval: ReviewApproval | None = None
        self.rows: dict[str, FieldDecision] = {}
        self.locked = False
        self.show_matching = False
        self.detail = tk.StringVar(master=self, value="Select a field to see its source and reason.")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self, columns=("use", "file", "field", "old", "new", "action"),
                                 show="headings", selectmode="browse")
        for col, label, width, minimum, stretch in (
            ("use", "Use", 45, 40, False), ("file", "Track", 125, 105, True),
            ("field", "Field", 135, 125, False), ("old", "Existing", 120, 74, True),
            ("new", "Proposed", 120, 74, True), ("action", "Action", 96, 93, False),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, minwidth=minimum, stretch=stretch)
        self.tree.tag_configure("unresolved", foreground="#f3bd76")
        self.tree.tag_configure("delete", foreground="#f3a9a9")
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._space)

    def show(self, proposal: MetadataProposal | None) -> None:
        self.proposal = proposal
        self.approval = ReviewApproval(proposal) if proposal is not None else None
        self._render()

    def set_show_matching(self, show: bool) -> None:
        self.show_matching = show
        self._render()
        self._refresh_checks()

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        proposal = self.proposal
        if proposal is not None:
            for decision in proposal.decisions:
                if decision.state == "KEEP" and not self.show_matching and decision.evidence.provider != "manual":
                    continue
                action, tag, mark = "Unresolved", "unresolved", ""
                if decision.state == "SET":
                    action, tag, mark = ("Add" if decision.existing_values is None else "Replace"), "", "☐"
                elif decision.state == "DELETE":
                    action, tag, mark = "Delete", "delete", "☐"
                elif decision.state == "KEEP":
                    action, tag = "Keep", ""
                item = self.tree.insert("", "end", values=(
                    mark, decision.file_id, decision.field.strip("%"),
                    _display(decision.existing_values), _display(decision.proposed_values), action,
                ), tags=(tag,) if tag else ())
                self.rows[item] = decision
        self._show_detail()
        self.event_generate("<<ApprovalsChanged>>")

    def pick_all(self, approved: bool) -> None:
        if self.approval is not None and not self.locked:
            self.approval.set_all(approved)
            self._refresh_checks()

    def _refresh_checks(self) -> None:
        if self.approval is None:
            return
        for item, decision in self.rows.items():
            if decision.state in ("SET", "DELETE"):
                mark = "☑" if self.approval.is_approved((decision.file_id, decision.field)) else "☐"
                self.tree.set(item, "use", mark)
        self.event_generate("<<ApprovalsChanged>>")

    def _show_detail(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            self.detail.set("Select a field to see its source and reason." if self.rows else "No fields to review.")
            return
        decision = self.rows[selected[0]]
        evidence = decision.evidence
        source = _display(evidence.original_value if isinstance(evidence.original_value, tuple) else
                          (evidence.original_value,) if evidence.original_value is not None else None)
        related = () if self.proposal is None else tuple(
            (f"{issue.message} " if issue.message != decision.reason else "")
            + "Affected files: " + ", ".join(issue.file_ids)
            for issue in self.proposal.issues
            if decision.field in issue.fields and decision.file_id in issue.file_ids
        )
        comparison = f"Existing: {_display(decision.existing_values)}"
        if decision.state == "SET":
            comparison += f" · Proposed: {_display(decision.proposed_values)}"
        elif decision.state == "DELETE":
            comparison += " · Proposed: Remove field"
        self.detail.set("\n".join((comparison, decision.reason, f"{evidence.provider} · {evidence.release_id} · Source: {source}",
                                   *dict.fromkeys(related))))

    def _toggle(self, item: str) -> None:
        decision = self.rows.get(item)
        if decision is not None and self.approval is not None and not self.locked:
            self.approval.toggle((decision.file_id, decision.field))
            self._refresh_checks()

    def _click(self, event: tk.Event) -> None:
        if self.tree.identify_column(event.x) == "#1":
            self._toggle(self.tree.identify_row(event.y))

    def _space(self, _event: tk.Event) -> str:
        self._toggle(self.tree.focus())
        return "break"
