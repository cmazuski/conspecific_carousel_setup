# tk_window_helpers.py — shared Tkinter layout helpers (scrolling, sizing) for the setup windows
import tkinter as tk
from tkinter import ttk


def make_scrollable(parent: tk.Widget) -> tk.Frame:
    """Wrap parent's content area in a vertically scrollable canvas.

    Returns an inner frame; build the UI by adding widgets to it exactly as
    they would have been added to `parent` directly (grid/pack both work).
    Mouse-wheel scrolling is active while the pointer is over the canvas.
    """
    container = tk.Frame(parent)
    container.pack(fill="both", expand=True)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(container, highlightthickness=0)
    vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")

    inner = tk.Frame(canvas)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _sync_scrollregion(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _sync_inner_width(event):
        # Stretch the inner frame to at least the canvas width so widgets
        # with sticky="ew" still fill the row correctly.
        if event.width > inner.winfo_reqwidth():
            canvas.itemconfigure(inner_id, width=event.width)

    inner.bind("<Configure>", _sync_scrollregion)
    canvas.bind("<Configure>", _sync_inner_width)

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_wheel(_event=None):
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _unbind_wheel(_event=None):
        canvas.unbind_all("<MouseWheel>")

    canvas.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)

    return inner


def fit_window_to_screen(scroll_frame: tk.Frame, max_fraction: float = 0.85) -> None:
    """Size the scrollable canvas (and its toplevel) to fit the content,
    capped to a comfortable fraction of the screen size, and center the window.

    `scroll_frame` is the frame returned by make_scrollable. Call once after
    all widgets have been added to it — a Canvas doesn't auto-size to its
    embedded content, so its actual required size must be measured from the
    inner frame and applied explicitly. Any overflow beyond the screen cap
    remains reachable via the scrollbar. The window stays freely resizable —
    this only sets the initial size so it doesn't fill a standard laptop screen.
    """
    canvas = scroll_frame.master
    root = canvas.winfo_toplevel()
    root.update_idletasks()

    content_w = scroll_frame.winfo_reqwidth()
    content_h = scroll_frame.winfo_reqheight()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    canvas.configure(
        width=min(content_w, int(screen_w * max_fraction)),
        height=min(content_h, int(screen_h * max_fraction)),
    )

    root.update_idletasks()
    win_w = root.winfo_reqwidth()
    win_h = root.winfo_reqheight()
    x = max(0, (screen_w - win_w) // 2)
    y = max(0, (screen_h - win_h) // 2)
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")
