"""Larkmere Desk: the demo CRM.

Deliberately offers no API. The only way in from another process is the screen, mouse
and keyboard, which is the position a deployment engineer is in with many real systems.

Every screen carries a header naming the app and screen, and a footer naming the
environment. The automation reads both before it does anything.
"""

import argparse
import sqlite3
import tkinter as tk

from larkmere.world import LAST_MONTH, displayed_statement

ENVIRONMENTS = {"demo": "Environment: demo data", "production": "Environment: production"}
LINES_PER_PAGE = 4
BG = "#f4f2ec"
INK = "#1d1c1a"


def money(cents):
    return f"${cents / 100:,.2f}"


class Desk(tk.Tk):
    def __init__(self, db_path, environment="demo", geometry="1000x720+20+20", topmost=False):
        super().__init__()
        self.db = sqlite3.connect(db_path)
        self.environment = environment
        self.title("Larkmere Desk")
        self.geometry(geometry)
        self.configure(bg=BG)
        self.resizable(False, False)
        if topmost:
            self.attributes("-topmost", True)
        self.option_add("*Font", ("DejaVu Sans" if self._has_font("DejaVu Sans") else "Segoe UI", 14))
        self.body = None
        self.show_accounts()

    def _has_font(self, name):
        import tkinter.font
        return name in tkinter.font.families(self)

    def _screen(self, title):
        if self.body:
            self.body.destroy()
        self.body = tk.Frame(self, bg=BG, padx=24, pady=16)
        self.body.pack(fill="both", expand=True)
        tk.Label(self.body, text=f"Larkmere Desk - {title}", bg=BG, fg=INK, font=("", 18, "bold"),
                 anchor="w").pack(fill="x")
        nav = tk.Frame(self.body, bg=BG)
        nav.pack(fill="x", pady=(10, 14))
        tk.Button(nav, text="Accounts", command=self.show_accounts).pack(side="left")
        tk.Label(self.body, text=ENVIRONMENTS[self.environment], bg=BG, fg=INK, anchor="e").pack(fill="x", side="bottom")
        content = tk.Frame(self.body, bg=BG)
        content.pack(fill="both", expand=True)
        return content

    def show_accounts(self, query=""):
        content = self._screen("Accounts")
        bar = tk.Frame(content, bg=BG)
        bar.pack(fill="x", pady=(0, 12))
        tk.Label(bar, text="Find account", bg=BG, fg=INK).pack(side="left", padx=(0, 10))
        entry = tk.Entry(bar, width=26)
        entry.insert(0, query)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _: self.show_accounts(entry.get()))
        tk.Button(bar, text="Search", command=lambda: self.show_accounts(entry.get())).pack(side="left", padx=10)
        like = f"%{query.strip()}%"
        rows = self.db.execute(
            "SELECT id, name, segment, owner FROM customers WHERE id LIKE ? OR name LIKE ? ORDER BY id LIMIT 10",
            (like, like)).fetchall()
        for cid, name, segment, owner in rows:
            tk.Button(content, text=f"{cid}    {name}    {segment}    {owner}", anchor="w",
                      command=lambda c=cid: self.show_account(c)).pack(fill="x", pady=1)
        if not rows:
            tk.Label(content, text="No accounts found", bg=BG, fg=INK).pack(anchor="w")

    def _account_tabs(self, customer_id, title):
        name = self.db.execute("SELECT name FROM customers WHERE id=?", (customer_id,)).fetchone()[0]
        content = self._screen(title)
        tk.Label(content, text=f"{customer_id}    {name}", bg=BG, fg=INK, font=("", 15, "bold"), anchor="w").pack(fill="x")
        tabs = tk.Frame(content, bg=BG)
        tabs.pack(fill="x", pady=10)
        tk.Button(tabs, text="Statements", command=lambda: self.show_statements(customer_id)).pack(side="left", padx=(0, 8))
        tk.Button(tabs, text="Notes", command=lambda: self.show_notes(customer_id)).pack(side="left")
        return content

    def show_account(self, customer_id):
        content = self._account_tabs(customer_id, "Account")
        segment, region, owner = self.db.execute(
            "SELECT segment, region, owner FROM customers WHERE id=?", (customer_id,)).fetchone()
        for text in (f"Segment: {segment}", f"Region: {region}", f"Owner: {owner}"):
            tk.Label(content, text=text, bg=BG, fg=INK, anchor="w").pack(fill="x", pady=2)

    def show_statements(self, customer_id):
        content = self._account_tabs(customer_id, "Statements")
        months = [r[0] for r in self.db.execute(
            "SELECT month FROM statements WHERE customer_id=? AND month<=? ORDER BY month DESC LIMIT 6",
            (customer_id, LAST_MONTH))]
        tk.Label(content, text="Choose a period", bg=BG, fg=INK, anchor="w").pack(fill="x", pady=(0, 6))
        for month in months:
            tk.Button(content, text=month, width=12, command=lambda m=month: self.show_statement(customer_id, m, 1)
                      ).pack(anchor="w", pady=1)

    def show_statement(self, customer_id, month, page):
        content = self._account_tabs(customer_id, "Statement")
        s, lines = displayed_statement(self.db, customer_id, month)
        pages = max(1, -(-len(lines) // LINES_PER_PAGE))
        tk.Label(content, text=f"Period: {month}    Page {page} of {pages}    Line items: {len(lines)}",
                 bg=BG, fg=INK, anchor="w").pack(fill="x", pady=(0, 8))
        grid = tk.Frame(content, bg=BG)
        grid.pack(anchor="w")
        widths = (18, 15, 13, 15)
        heads = ("Item", "Tokens (M)", "Rate", "Amount")
        for col, (head, width) in enumerate(zip(heads, widths)):
            tk.Label(grid, text=head, bg=BG, fg=INK, width=width, anchor="w" if col == 0 else "e",
                     font=("", 14, "bold")).grid(row=0, column=col, padx=(0, 24), pady=3)
        start = (page - 1) * LINES_PER_PAGE
        for row, line in enumerate(lines[start:start + LINES_PER_PAGE], 1):
            cells = (f"{line['tier']} {line['kind']}", f"{line['tokens_centi_m'] / 100:,.2f}",
                     money(line["rate_cents"]), money(line["amount_cents"]))
            for col, (cell, width) in enumerate(zip(cells, widths)):
                tk.Label(grid, text=cell, bg=BG, fg=INK, width=width, anchor="w" if col == 0 else "e"
                         ).grid(row=row, column=col, padx=(0, 24), pady=3)
        if page == pages:
            summary = tk.Frame(content, bg=BG)
            summary.pack(anchor="w", pady=(16, 0))
            for row, (label, key) in enumerate((("Usage total", "usage_cents"), ("Monthly commit", "commit_cents"),
                                                ("Overage", "overage_cents"), ("Unused commit", "unused_commit_cents"),
                                                ("Total due", "total_due_cents"))):
                tk.Label(summary, text=label, bg=BG, fg=INK, width=18, anchor="w").grid(row=row, column=0, padx=(0, 24))
                tk.Label(summary, text=money(s[key]), bg=BG, fg=INK, width=15, anchor="e").grid(row=row, column=1)
        controls = tk.Frame(content, bg=BG)
        controls.pack(anchor="w", pady=14)
        if page < pages:
            tk.Button(controls, text="Next page",
                      command=lambda: self.show_statement(customer_id, month, page + 1)).pack(side="left")

    def show_notes(self, customer_id):
        content = self._account_tabs(customer_id, "Notes")
        tk.Label(content, text="New note", bg=BG, fg=INK, anchor="w").pack(fill="x")
        box = tk.Text(content, height=9, width=76, wrap="word")
        box.pack(anchor="w", pady=(4, 8))
        box.bind("<Button-1>", lambda _: box.focus_force())

        def save():
            body = box.get("1.0", "end").strip()
            if body:
                self.db.execute("INSERT INTO notes (customer_id, body, saved_by) VALUES (?,?,?)",
                                (customer_id, body, "desk user"))
                self.db.commit()
            self.show_notes(customer_id)

        tk.Button(content, text="Save note", command=save).pack(anchor="w")
        count = self.db.execute("SELECT COUNT(*) FROM notes WHERE customer_id=?", (customer_id,)).fetchone()[0]
        tk.Label(content, text=f"Saved notes: {count}", bg=BG, fg=INK, anchor="w").pack(fill="x", pady=(12, 2))
        latest = self.db.execute("SELECT body FROM notes WHERE customer_id=? ORDER BY id DESC LIMIT 1",
                                 (customer_id,)).fetchone()
        if latest:
            tk.Label(content, text=latest[0][:200], bg=BG, fg=INK, anchor="w", justify="left", wraplength=920
                     ).pack(fill="x")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Larkmere Desk demo CRM.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--environment", choices=ENVIRONMENTS, default="demo")
    parser.add_argument("--geometry", default="1000x720+20+20")
    parser.add_argument("--topmost", action="store_true")
    args = parser.parse_args(argv)
    Desk(args.db, args.environment, args.geometry, args.topmost).mainloop()


if __name__ == "__main__":
    main()
