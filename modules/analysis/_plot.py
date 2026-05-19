"""Matplotlib defaults + Konvenienz-Helper fuer Notebook-Analysen.

Notebook-Boilerplate (Top-Zelle):

    from modules.analysis._plot import setup
    setup()

setup() setzt:
  - rcParams (figure-size, dpi, font, grid)
  - eine kompakte sequentielle Palette
  - pandas display-options (max-rows/cols/width)
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt

# Lab-Palette — bewusst gedaempft, niedrig gesaettigt, druckbar.
# Sequenziell, damit man auch ohne Legende noch lesen kann was die n-te
# Linie ist (helle -> dunkle Tendenz).
PALETTE: list[str] = [
    "#1f4e79",  # navy
    "#2e75b6",  # mid-blue
    "#5b9bd5",  # light-blue
    "#9dc3e6",  # pale-blue
    "#bf9000",  # ochre
    "#a9a9a9",  # mid-gray
    "#7f7f7f",  # darker-gray
    "#c00000",  # red (Warning / Hervorhebung)
]

# Spezial-Farben fuer wiederkehrende semantische Layer:
COLORS = {
    "ok":        "#2e75b6",
    "warn":      "#bf9000",
    "danger":    "#c00000",
    "neutral":   "#7f7f7f",
    "highlight": "#1f4e79",
    "positive":  "#2e7d32",
    "negative":  "#c62828",
}


def setup(figsize: tuple[float, float] = (10.0, 5.5), dpi: int = 110) -> None:
    """Setzt matplotlib + pandas defaults fuer dieses Notebook."""
    mpl.rcParams.update({
        "figure.figsize":  figsize,
        "figure.dpi":      dpi,
        "savefig.dpi":     dpi,
        "axes.titlesize":  12,
        "axes.titleweight": "bold",
        "axes.labelsize":  10,
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.grid":         True,
        "grid.linestyle":    ":",
        "grid.alpha":        0.5,
        "grid.color":        "#999999",
        "legend.frameon":    False,
        "legend.fontsize":   9,
        "xtick.labelsize":   9,
        "ytick.labelsize":   9,
        "font.family":       "DejaVu Sans",
        "axes.prop_cycle":   mpl.cycler(color=PALETTE),
    })

    # pandas defaults — Notebook-friendly aber nicht ueberladen.
    try:
        import pandas as pd
        pd.set_option("display.max_columns", 50)
        pd.set_option("display.max_rows", 200)
        pd.set_option("display.width", 200)
        pd.set_option("display.float_format", lambda v: f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}")
    except Exception:  # noqa: BLE001
        pass


def annotate_last(ax, x, y, fmt: str = "{:,.2f}", **kwargs) -> None:
    """Letzten Punkt einer Serie beschriften — typisch fuer Time-Series."""
    if len(x) == 0 or len(y) == 0:
        return
    ax.annotate(
        fmt.format(y[-1]),
        xy=(x[-1], y[-1]),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color=COLORS["highlight"],
        **kwargs,
    )


def hline(ax, value: float, label: str | None = None, color: str = COLORS["neutral"]) -> None:
    """Horizontale Referenzlinie mit dezentem Label."""
    ax.axhline(value, color=color, linestyle="--", linewidth=0.8, alpha=0.7)
    if label:
        ax.text(
            ax.get_xlim()[1], value, f" {label}",
            color=color, fontsize=8, va="center", ha="left",
        )


def style_axes(ax, title: str = "", xlabel: str = "", ylabel: str = "") -> None:
    """Compact wrapper — sets title + labels in einem call."""
    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)


__all__ = ["setup", "PALETTE", "COLORS", "annotate_last", "hline", "style_axes", "plt"]
