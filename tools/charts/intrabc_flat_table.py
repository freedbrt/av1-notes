#!/usr/bin/env python3
"""Generates the charts for posts/av1-intrabc.md as inline SVG.

Run from the repo root: python3 tools/charts/intrabc_flat_table.py
Reads tools/charts/intrabc_data.json and writes assets/charts/*.svg.
Colors come from CSS variables in assets/css/extended/charts.css, so the
charts follow the site theme.

intrabc_data.json holds key frame times of libaom main 71ccfafa26 and the
patches on top of it (p8 flat table, p10 per tile build), RT speed 11, IntraBC on, release builds with NDEBUG, 20 key frames
per clip, 5 interleaved rounds, medians in ms. A change between two
versions is the median of the per round changes.
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "charts"
DATA = json.loads((pathlib.Path(__file__).parent /
                   "intrabc_data.json").read_text())

# Clip id in the bench files, label and resolution, in chart order.
CLIPS = [
    ("capture", "IDE, terminal", "2560x1440"),
    ("capture2", "IDE, chat, terminal", "2560x1440"),
    ("capture3", "Windows IDEs", "2559x1372"),
    ("capture4", "Browser, file manager", "2560x1440"),
    ("capture5", "IDE, video", "2560x1440"),
]

# Each version keeps its label and color class in every chart.
SERIES = {
    "main": ("libaom main", "viz-main"),
    "p8": ("Flat hash table", "viz-flat"),
    "p10": ("+ per tile build", "viz-tile"),
    "all": ("With our patches", "viz-all"),
}

# Extra time per key frame with IntraBC on against IntraBC off, ms and
# percent of the IntraBC off key frame, per machine. main 71ccfafa26
# against main with our patches. 1 thread CPU, 4 threads wall. Measured
# on the M3 Pro on 2026-10-15 and on the Ryzen on 2026-10-16.
IBC_COST = {
    "Apple M3 Pro": {
        "1 thread, CPU time": {
            "capture": (38.1, 34.8, 11.1, 9.9),
            "capture2": (37.9, 41.0, 11.5, 11.9),
            "capture3": (29.9, 25.5, 7.8, 6.7),
            "capture4": (43.6, 37.9, 12.1, 10.3),
            "capture5": (46.5, 31.6, 9.2, 6.3),
        },
        "4 threads, wall time": {
            "capture": (41.5, 127, 5.0, 14.8),
            "capture2": (44.7, 148, 5.7, 18.9),
            "capture3": (35.8, 102, 4.2, 11.7),
            "capture4": (48.9, 144, 5.6, 16.3),
            "capture5": (48.5, 108, 4.6, 9.9),
        },
    },
    "AMD Ryzen 9 5900X": {
        "1 thread, CPU time": {
            "capture": (102.3, 55.6, 23.4, 12.8),
            "capture2": (105.5, 66.0, 25.8, 16.3),
            "capture3": (96.1, 48.6, 23.4, 12.0),
            "capture4": (122.7, 63.1, 29.7, 15.5),
            "capture5": (124.2, 50.8, 26.6, 11.0),
        },
        "4 threads, wall time": {
            "capture": (107.9, 180.6, 12.5, 21.2),
            "capture2": (106.8, 194.3, 12.8, 23.4),
            "capture3": (98.7, 155.2, 11.0, 17.3),
            "capture4": (121.6, 193.9, 13.3, 21.4),
            "capture5": (131.7, 169.8, 12.2, 15.8),
        },
    },
}

MACHINES = ["Apple M3 Pro", "AMD Ryzen 9 5900X"]

# Hash table build cost on the "IDE, video" clip, M3 Pro, 1 thread, in ms
# per key frame. Profile sample share times the measured CPU time per key
# frame (182.1 ms before, 153.0 ms after), from the earlier opt9 run.
BUILD = [
    ("Before", {"crc": 0.069 * 182.1, "insert": 0.167 * 182.1,
                "free": 0.035 * 182.1}),
    ("After", {"crc": 0.081 * 153.0, "insert": 0.056 * 153.0, "free": 0.0}),
]
# Series in fixed color slot order, drawn left to right in this order too.
BUILD_SERIES = [("crc", "CRC32C hashing", 1), ("insert", "Table insert", 2),
                ("free", "Table free", 3)]

# Stage timers with our patches, 1 thread, medians over 100 key frames per
# clip, ms. Build stages, then the search. Same sources as IBC_COST.
STAGES = [("2x2", "2x2 pass"), ("4x4", "4x4 CRC"), ("8x8", "8x8 CRC"),
          ("count", "Count"), ("fill", "Fill"), ("search", "Search")]
STAGE_MS = {
    "Apple M3 Pro": {
        "capture": [2.29, 2.03, 2.05, 1.97, 5.13, 5.67],
        "capture2": [2.33, 2.10, 2.11, 1.81, 5.26, 4.49],
        "capture3": [2.21, 1.93, 1.95, 1.81, 5.28, 6.40],
        "capture4": [2.29, 2.09, 2.06, 2.02, 6.05, 4.93],
        "capture5": [2.29, 2.10, 2.06, 2.09, 6.38, 6.03],
    },
    "AMD Ryzen 9 5900X": {
        "capture": [5.43, 4.11, 2.71, 2.91, 12.60, 9.02],
        "capture2": [5.42, 4.12, 2.72, 2.85, 12.48, 6.96],
        "capture3": [5.16, 3.91, 2.58, 2.77, 12.44, 10.68],
        "capture4": [5.43, 4.12, 2.71, 2.97, 15.20, 7.66],
        "capture5": [5.44, 4.12, 2.71, 3.00, 17.10, 9.57],
    },
}

SHORT = {"Apple M3 Pro": "M3 Pro", "AMD Ryzen 9 5900X": "Ryzen 9 5900X"}

FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'


def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def fmt(value):
    return f"{value:.1f}"


def bar_path(x, y, w, h, r, round_end):
    """A horizontal bar, square at the left, optionally rounded at the right."""
    if not round_end or w < 2 * r:
        return f'M{x:.1f},{y:.1f}h{w:.1f}v{h:.1f}h{-w:.1f}z'
    return (f'M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}'
            f'v{h - 2 * r:.1f}a{r},{r} 0 0 1 {-r},{r}h{-(w - r):.1f}z')


def svg_open(width, height, ident, title, desc):
    return [f'<svg class="viz" viewBox="0 0 {width} {height}" '
            f'width="100%" role="img" {FONT} '
            f'aria-labelledby="{ident}-title {ident}-desc">',
            f'<title id="{ident}-title">{esc(title)}</title>',
            f'<desc id="{ident}-desc">{esc(desc)}</desc>']


def heading_and_legend(parts, heading, items):
    """items is a list of (label, css class) for the swatches."""
    parts.append(f'<text x="0" y="16" class="viz-ink" font-size="15" '
                 f'font-weight="600">{esc(heading)}</text>')
    lx = 0
    for label, cls in items:
        parts.append(f'<rect x="{lx:.1f}" y="32" width="12" height="12" '
                     f'rx="2" class="{cls}"/>')
        parts.append(f'<text x="{lx + 18:.1f}" y="42" class="viz-ink2" '
                     f'font-size="12">{esc(label)}</text>')
        lx += 18 + len(label) * 6.1 + 24


def x_axis(parts, x0, scale, top, bottom, ticks, unit_x,
           unit="ms per key frame"):
    for t in ticks:
        x = x0 + t * scale
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
                     f'y2="{bottom}" class="viz-grid" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{bottom + 18}" class="viz-muted" '
                     f'font-size="12" text-anchor="middle">{t}</text>')
    parts.append(f'<text x="{unit_x:.1f}" y="{bottom + 36}" '
                 f'class="viz-muted" font-size="12" text-anchor="end">'
                 f'{unit}</text>')


def grouped_chart(ident, heading, desc, groups, keys, domain, ticks,
                  panels=MACHINES, unit="ms per key frame", sign=""):
    """Grouped horizontal bars, one panel each, one shared axis.

    groups is a list of (label, sublabel) and a rows(panel) function that
    gives one (values by key, change label) per group. Panels default to the
    two machines.
    """
    labels, rows = groups
    width = 720
    label_w = 150
    panel_gap = 28
    panel_w = (width - label_w - panel_gap) / 2
    plot_w = panel_w - 56  # room for the change label after the bars
    top = 92
    bar_h = {2: 12, 3: 10}.get(len(keys), 8)
    bar_gap = 2
    group_h = len(keys) * bar_h + (len(keys) - 1) * bar_gap
    row_h = group_h + 18
    bottom = top + row_h * len(labels)
    height = bottom + 44
    scale = plot_w / domain
    parts = svg_open(width, height, ident, heading, desc)
    heading_and_legend(parts, heading, [SERIES[k] for k in keys])

    for i, (name, sub) in enumerate(labels):
        cy = top + row_h * i + row_h / 2
        parts.append(f'<text x="0" y="{cy - 2:.1f}" class="viz-ink2" '
                     f'font-size="12">{esc(name)}</text>')
        parts.append(f'<text x="0" y="{cy + 13:.1f}" class="viz-muted" '
                     f'font-size="11">{esc(sub)}</text>')

    for p, machine in enumerate(panels):
        x0 = label_w + p * (panel_w + panel_gap)
        parts.append(f'<text x="{x0:.1f}" y="{top - 14}" class="viz-ink" '
                     f'font-size="13" font-weight="600">{esc(machine)}'
                     f'</text>')
        x_axis(parts, x0, scale, top, bottom, ticks, x0 + plot_w, unit)
        for i, (values, change) in enumerate(rows(machine)):
            y = top + row_h * i + (row_h - group_h) / 2
            text = ", ".join(f"{SERIES[k][0]} {fmt(values[k])} ms"
                             for k in keys)
            tip = f"{labels[i][0]}, {machine}: {text}"
            parts.append(f'<g tabindex="0"><title>{esc(tip)}</title>')
            parts.append(f'<rect x="{x0:.1f}" y="{y - 4:.1f}" '
                         f'width="{panel_w:.1f}" height="{group_h + 8}" '
                         f'fill="transparent"/>')
            for j, k in enumerate(keys):
                yb = y + j * (bar_h + bar_gap)
                parts.append(f'<path d="{bar_path(x0, yb, values[k] * scale, bar_h, 4, True)}"'
                             f' class="{SERIES[k][1]}"/>')
            last = values[keys[-1]]
            parts.append(f'<text x="{x0 + last * scale + 6:.1f}" '
                         f'y="{yb + bar_h - 1:.1f}" class="viz-ink2" '
                         f'font-size="12">{change:{sign}.1f}%</text>')
            parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts)


def ibc_cost(machine):
    """Rows of the extra IntraBC time on main and patched for each clip."""
    labels = [(name, res) for _, name, res in CLIPS]

    def rows(panel):
        out = []
        for clip, _, _ in CLIPS:
            main_ms, _, all_ms, all_pct = IBC_COST[machine][panel][clip]
            out.append(({"main": main_ms, "all": all_ms}, all_pct))
        return out

    return labels, rows


def per_clip(threads, metric, keys, pair):
    """Rows of one value per version for each clip, labeled with pair."""
    labels = [(name, res) for _, name, res in CLIPS]

    def rows(machine):
        out = []
        for clip, _, _ in CLIPS:
            d = DATA[machine][str(threads)][clip]
            values = {k: d[k][metric] for k in keys}
            out.append((values, d["changes"][pair][metric]))
        return out

    return labels, rows


def per_thread_count(keys):
    """Rows of the mean wall time over all clips for each thread count."""
    counts = [1, 2, 4]
    labels = [(f"{t} thread" + ("s" if t > 1 else ""),
               "1 tile" if t == 1 else ("2x1 tiles" if t == 2 else "2x2 tiles"))
              for t in counts]

    def rows(machine):
        out = []
        for t in counts:
            clips = DATA[machine][str(t)]
            values = {k: sum(clips[c][k]["wall"] for c, _, _ in CLIPS) /
                      len(CLIPS) for k in keys}
            out.append((values, 100 * (values[keys[-1]] / values["main"] - 1)))
        return out

    return labels, rows


def build_cost_chart():
    width = 720
    x0 = 64
    x1 = 640
    domain = 50
    scale = (x1 - x0) / domain
    top = 72
    row_h = 44
    bar_h = 24
    gap = 2
    bottom = top + row_h * len(BUILD)
    height = bottom + 44
    heading = "Hash table build per key frame, IDE and video clip, M3 Pro, 1 thread"
    parts = svg_open(
        width, height, "bc", heading,
        f'Stacked bars. Before, CRC32C hashing takes '
        f'{fmt(BUILD[0][1]["crc"])} ms, table insert '
        f'{fmt(BUILD[0][1]["insert"])} ms and table free '
        f'{fmt(BUILD[0][1]["free"])} ms. After, CRC32C hashing takes '
        f'{fmt(BUILD[1][1]["crc"])} ms, table insert '
        f'{fmt(BUILD[1][1]["insert"])} ms and nothing is freed.')
    heading_and_legend(parts, heading,
                       [(name, f"viz-s{slot}") for _, name, slot in BUILD_SERIES])

    x_axis(parts, x0, scale, top, bottom, range(0, domain + 1, 10), x1)

    for i, (label, values) in enumerate(BUILD):
        y = top + row_h * i + (row_h - bar_h) / 2
        cy = y + bar_h / 2
        parts.append(f'<text x="0" y="{cy + 4:.1f}" class="viz-ink2" '
                     f'font-size="12">{label}</text>')
        drawn = [(key, name, slot) for key, name, slot in BUILD_SERIES
                 if values[key] > 0]
        x = x0
        for j, (key, name, slot) in enumerate(drawn):
            last = j == len(drawn) - 1
            w = values[key] * scale
            draw_w = w if last else w - gap
            parts.append(f'<g tabindex="0"><title>{label}, {esc(name)}: '
                         f'{fmt(values[key])} ms</title>')
            parts.append(f'<path d="{bar_path(x, y, draw_w, bar_h, 4, last)}"'
                         f' class="viz-s{slot}"/>')
            text = fmt(values[key])
            if draw_w >= len(text) * 7 + 16:
                parts.append(f'<text x="{x + draw_w / 2:.1f}" '
                             f'y="{cy + 4:.1f}" class="viz-on-fill" '
                             f'font-size="12" text-anchor="middle">'
                             f'{text}</text>')
            parts.append('</g>')
            x += w
        total = sum(values.values())
        parts.append(f'<text x="{x + 8:.1f}" y="{cy + 4:.1f}" '
                     f'class="viz-ink" font-size="12" font-weight="600">'
                     f'{fmt(total)} ms</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def stage_chart(machine, ident, domain, step):
    """Stacked bars of the IntraBC stages per clip on one machine."""
    width = 720
    x0 = 150
    x1 = 640
    scale = (x1 - x0) / domain
    top = 72
    row_h = 36
    bar_h = 20
    gap = 2
    bottom = top + row_h * len(CLIPS)
    height = bottom + 44
    heading = f"IntraBC time per key frame by stage, {SHORT[machine]}, 1 thread"
    parts = svg_open(
        width, height, ident, heading,
        "Stacked bars per clip with our patches: the 2x2 pass, the 4x4 "
        "and 8x8 CRC passes, the count, the fill and the search. The fill "
        "and the search are the largest parts. Each segment's tooltip has "
        "its time.")
    heading_and_legend(parts, heading,
                       [(name, f"viz-s{i + 1}") for i, (_, name)
                        in enumerate(STAGES)])
    x_axis(parts, x0, scale, top, bottom, range(0, domain + 1, step), x1)
    for i, (clip, name, res) in enumerate(CLIPS):
        y = top + row_h * i + (row_h - bar_h) / 2
        cy = y + bar_h / 2
        parts.append(f'<text x="0" y="{cy - 2:.1f}" class="viz-ink2" '
                     f'font-size="12">{esc(name)}</text>')
        parts.append(f'<text x="0" y="{cy + 12:.1f}" class="viz-muted" '
                     f'font-size="11">{res}</text>')
        x = x0
        values = STAGE_MS[machine][clip]
        for j, ((_, stage), ms) in enumerate(zip(STAGES, values)):
            last = j == len(values) - 1
            w = ms * scale
            draw_w = w if last else w - gap
            parts.append(f'<g tabindex="0"><title>{esc(name)}, '
                         f'{esc(stage)}: {fmt(ms)} ms</title>')
            parts.append(f'<path d="{bar_path(x, y, draw_w, bar_h, 4, last)}"'
                         f' class="viz-s{j + 1}"/>')
            parts.append('</g>')
            x += w
        parts.append(f'<text x="{x + 8:.1f}" y="{cy + 4:.1f}" '
                     f'class="viz-ink" font-size="12" font-weight="600">'
                     f'{fmt(sum(values))} ms</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def cost_chart(machine, ident, domain, ticks):
    return grouped_chart(
        ident, f"Extra time per key frame from IntraBC, {SHORT[machine]}",
        "Grouped bars per clip: the extra key frame time with IntraBC on "
        "against IntraBC off, on libaom main and with our patches, "
        "at 1 thread (CPU time) and 4 threads (wall time). "
        "Each clip's tooltip has the exact times.",
        ibc_cost(machine), ["main", "all"], domain, ticks,
        panels=list(IBC_COST[machine]), unit="extra ms per key frame",
        sign="+")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    both = "for the Apple M3 Pro and the AMD Ryzen 9 5900X"
    all_keys = ["main", "p8", "p10"]
    charts = {
        "intrabc-cpu-1t.svg": grouped_chart(
            "cpu", "CPU time per key frame, 1 thread",
            f"Grouped bars per clip {both}, libaom main against the flat "
            f"hash table. Each clip's tooltip has the exact times.",
            per_clip(1, "cpu", ["main", "p8"], "main-p8"), ["main", "p8"],
            400, [0, 100, 200, 300, 400]),
        "intrabc-wall-4t.svg": grouped_chart(
            "wall", "Wall time per key frame, 4 threads",
            f"Grouped bars per clip {both}, libaom main against the flat "
            f"hash table. Each clip's tooltip has the exact times.",
            per_clip(4, "wall", ["main", "p8"], "main-p8"), ["main", "p8"],
            200, [0, 50, 100, 150, 200]),
        "intrabc-wall-4t-all.svg": grouped_chart(
            "all", "Wall time per key frame, 4 threads, both patches",
            f"Grouped bars per clip {both}: libaom main, the flat hash "
            f"table, and the flat table with the per tile build. Each clip's "
            f"tooltip has the exact times.",
            per_clip(4, "wall", all_keys, "main-p10"), all_keys,
            200, [0, 50, 100, 150, 200]),
        "intrabc-wall-2t-all.svg": grouped_chart(
            "all2", "Wall time per key frame, 2 threads, both patches",
            f"Grouped bars per clip {both}: libaom main, the flat hash "
            f"table, and the flat table with the per tile build, 2x1 tiles. "
            f"Each clip's tooltip has the exact times.",
            per_clip(2, "wall", all_keys, "main-p10"), all_keys,
            300, [0, 100, 200, 300]),
        "intrabc-threads.svg": grouped_chart(
            "thr", "Wall time per key frame, mean of the five clips",
            f"Grouped bars per thread count {both}: libaom main, the flat "
            f"hash table, and the flat table with the per tile build. Each "
            f"group's tooltip has the exact times.",
            per_thread_count(all_keys), all_keys,
            300, [0, 100, 200, 300]),
        "intrabc-build-cost.svg": build_cost_chart(),
        "intrabc-stages.svg": stage_chart("Apple M3 Pro", "st", 25, 5),
        "intrabc-stages-x86.svg": stage_chart("AMD Ryzen 9 5900X", "stx", 50,
                                              10),
        "intrabc-cost.svg": cost_chart("Apple M3 Pro", "cost", 50,
                                       [0, 10, 20, 30, 40, 50]),
        "intrabc-cost-x86.svg": cost_chart("AMD Ryzen 9 5900X", "costx", 150,
                                           [0, 50, 100, 150]),
    }
    for name, svg in charts.items():
        (OUT / name).write_text(svg + "\n")


if __name__ == "__main__":
    main()
