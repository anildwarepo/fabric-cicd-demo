"""Generate the Fabric CI/CD design diagrams as self-contained SVGs.

Follows the azure-architecture-svg skill in C:\\repos\\AzureArchitectureCenter: official icons
from Azure_Icons embedded inline with prefixed IDs, Segoe UI, title 20 / headers 14 / box
titles 12 / text 10, neutral orthogonal arrows drawn last with solid white label backgrounds,
and a legend on every diagram. Text that would overflow its box is reported and fails the run.

Usage (Windows; text is measured with Segoe UI and Consolas from C:/Windows/Fonts):
    pip install pillow
    set AZURE_ICONS_DIR=<path to AzureArchitectureCenter/Azure_Icons>   (optional)
    python docs/tools/generate_diagrams.py
"""

import os
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import ImageFont

ICONS = Path(os.environ.get("AZURE_ICONS_DIR", r"C:\repos\AzureArchitectureCenter\Azure_Icons"))
OUT = Path(__file__).resolve().parent.parent / "images"
FONTS = {(False, False): r"C:\Windows\Fonts\segoeui.ttf", (True, False): r"C:\Windows\Fonts\segoeuib.ttf",
         (False, True): r"C:\Windows\Fonts\consola.ttf", (True, True): r"C:\Windows\Fonts\consolab.ttf"}
SANS = "Segoe UI, Arial, sans-serif"
MONO = "Cascadia Code, Consolas, monospace"

# Semantic palette (header, fill, stroke, text) from the skill's color system.
REPOS = ("#2563eb", "#dbeafe", "#60a5fa", "#1e3a8a")
PIPE = ("#d97706", "#fffbeb", "#fbbf24", "#78350f")
FABRIC = ("#059669", "#ecfdf5", "#10b981", "#065f46")
LIB = ("#7c3aed", "#f5f3ff", "#8b5cf6", "#5b21b6")
IDENT = ("#4f46e5", "#eef2ff", "#818cf8", "#3730a3")
SLATE = ("#64748b", "#f1f5f9", "#94a3b8", "#334155")
RED = ("#dc2626", "#fef2f2", "#f87171", "#991b1b")
GIT = ("#0d9488", "#f0fdfa", "#5eead4", "#134e4a")

AZURE_ICONS = {
    "branch": "general/10782-icon-service-Branch.svg",
    "repo": "general/10843-icon-service-TFS-VC-Repository.svg",
    "ado": "devops/10261-icon-service-Azure-DevOps.svg",
    "appreg": "identity/10232-icon-service-App-Registrations.svg",
    "sqldb": "databases/10130-icon-service-SQL-Database.svg",
    "adf": "analytics/10126-icon-service-Data-Factories.svg",
}

# Fabric items have no icon in Azure_Icons; these are simple custom glyphs (18x18), listed in each legend.
CUSTOM_ICONS = {
    "fabric": '<path d="M3 1.5h12l-3.2 4.2H-0.2z" fill="#0f766e" transform="translate(1.5 0)"/>'
              '<path d="M3 6.9h12l-3.2 4.2H-0.2z" fill="#14b8a6" transform="translate(1.5 0)"/>'
              '<path d="M3 12.3h8l-3.2 4.2H-0.2z" fill="#5eead4" transform="translate(1.5 0)"/>',
    "lakehouse": '<path d="M9 1.2 1.2 7.4h2.2V16h11.2V7.4h2.2z" fill="#0f766e"/>'
                 '<rect x="5" y="8.6" width="8" height="6" rx="0.6" fill="#ccfbf1"/>'
                 '<path d="M5.4 10.6c1-.8 1.9-.8 2.9 0s1.9.8 2.9 0M5.4 12.8c1-.8 1.9-.8 2.9 0s1.9.8 2.9 0" '
                 'stroke="#0d9488" stroke-width="0.9" fill="none"/>',
    "notebook": '<rect x="3" y="1" width="12.5" height="16" rx="1.2" fill="#fff7ed" stroke="#ea580c" stroke-width="1.1"/>'
                '<rect x="3" y="1" width="2.6" height="16" rx="0.8" fill="#ea580c"/>'
                '<path d="M7.6 5h5.6M7.6 8h5.6M7.6 11h3.8" stroke="#ea580c" stroke-width="1.1" stroke-linecap="round"/>',
    "varlib": '<rect x="1" y="1.5" width="16" height="15" rx="2" fill="#7c3aed"/>'
              '<path d="M5.2 4.6c-1.3 0-1.3.9-1.3 1.8 0 .8-.4 1.6-1.1 2.1.7.5 1.1 1.3 1.1 2.1 0 .9 0 1.8 1.3 1.8" '
              'stroke="#fff" stroke-width="1" fill="none"/>'
              '<path d="M12.8 4.6c1.3 0 1.3.9 1.3 1.8 0 .8.4 1.6 1.1 2.1-.7.5-1.1 1.3-1.1 2.1 0 .9 0 1.8-1.3 1.8" '
              'stroke="#fff" stroke-width="1" fill="none"/>'
              '<path d="M7 6.2h4M7 8.6h4M7 11h4" stroke="#ddd6fe" stroke-width="1.2" stroke-linecap="round"/>',
    "person": '<circle cx="9" cy="5.2" r="3.4" fill="#475569"/><path d="M2.4 16.6c.5-3.9 3.1-5.9 6.6-5.9s6.1 2 6.6 5.9z" fill="#475569"/>',
}

_font_cache = {}


def text_width(s, size, bold=False, mono=False):
    key = (bold, mono, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONTS[(bold, mono)], size * 4)
    return _font_cache[key].getlength(s) / 4


def load_azure_icon(name, rel):
    raw = (ICONS / rel).read_text(encoding="utf-8")
    inner = re.search(r"<svg[^>]*>(.*)</svg>", raw, re.S).group(1)
    inner = re.sub(r"<title>.*?</title>", "", inner, flags=re.S)
    for old in set(re.findall(r'id="([^"]+)"', inner)):
        new = f"{name}-{old[:8]}"
        inner = inner.replace(f'id="{old}"', f'id="{new}"').replace(f"url(#{old})", f"url(#{new})")
    return inner


class Svg:
    def __init__(self, width, height, title, subtitle=None):
        self.w, self.h = width, height
        self.defs, self.body, self.fg = [], [], []
        self.used_icons = set()
        self.warnings = []
        self.title = title
        self.body.append(f'<rect width="{width}" height="{height}" rx="12" fill="#fafafa"/>')
        self.text(width / 2, 38, title, 20, 700, "#1e293b", "middle")
        if subtitle:
            self.text(width / 2, 60, subtitle, 12, 400, "#475569", "middle")

    # --- primitives -------------------------------------------------------------------------
    def text(self, x, y, s, size=10, weight=400, fill="#334155", anchor="start", mono=False, italic=False, target=None, max_width=None):
        fam = MONO if mono else SANS
        style = ' font-style="italic"' if italic else ""
        (target if target is not None else self.body).append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}" font-family="{fam}"{style}>{escape(s)}</text>')
        if max_width is not None:
            width = text_width(s, size, weight >= 600, mono)
            if width > max_width + 0.5:
                self.warnings.append(f"overflow {width:.0f}>{max_width:.0f}: {s!r}")

    def rect(self, x, y, w, h, fill, stroke, sw=1.0, rx=8, dash=None, shadow=None, target=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' filter="url(#{shadow})"' if shadow else ""
        (target if target is not None else self.body).append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{extra}/>')

    def icon(self, name, x, y, size=36, target=None):
        self.used_icons.add(name)
        (target if target is not None else self.body).append(
            f'<use href="#icon-{name}" xlink:href="#icon-{name}" transform="translate({x:.1f} {y:.1f}) scale({size / 18:.3f})"/>')

    # --- composite elements -----------------------------------------------------------------
    def layer(self, x, y, w, h, title, palette, dashed=False):
        header, fill, stroke, _ = palette
        gid = f"grad-{header[1:]}"
        if not any(f'id="{gid}"' in d for d in self.defs):
            self.defs.append(f'<linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{header}"/>'
                             f'<stop offset="1" stop-color="{header}" stop-opacity="0.85"/></linearGradient>')
        self.body.append('<g filter="url(#shadowLg)">')
        self.rect(x, y, w, h, "#ffffff", stroke, 1.5, 10, "6,3" if dashed else None)
        self.body.append(f'<rect x="{x}" y="{y}" width="{w}" height="28" rx="10" fill="url(#{gid})"/>')
        self.body.append(f'<rect x="{x}" y="{y + 18}" width="{w}" height="10" fill="url(#{gid})"/>')
        self.body.append("</g>")
        self.text(x + w / 2, y + 19, title, 14, 700, "#ffffff", "middle", max_width=w - 20)

    def box(self, x, y, w, h, palette, title, lines=(), icon=None, icon_size=34, title_size=12, center=False,
            line_size=10, mono_lines=False, bold_first=False, emphasis=None):
        _, fill, stroke, dark = palette
        self.rect(x, y, w, h, fill, stroke, 1.2, 8, shadow="shadow")
        if center:
            iy = y + 10
            if icon:
                self.icon(icon, x + w / 2 - icon_size / 2, iy, icon_size)
                iy += icon_size + 4
            self.text(x + w / 2, iy + title_size, title, title_size, 600, dark, "middle", max_width=w - 12)
            ly = iy + title_size + 14
            for line in lines:
                self.text(x + w / 2, ly, line, line_size, 400, "#334155", "middle", mono=mono_lines, max_width=w - 12)
                ly += line_size + 4
            if ly - line_size > y + h:
                self.warnings.append(f"vertical overflow in box {title!r}")
            return
        tx = x + 14
        if icon:
            self.icon(icon, x + 12, y + (h - icon_size) / 2 if h < 80 else y + 12, icon_size)
            tx = x + 12 + icon_size + 12
        inner = x + w - 10 - tx
        ty = y + 22
        self.text(tx, ty, title, title_size, 700, dark, max_width=inner)
        ty += title_size + 6
        for i, line in enumerate(lines):
            weight = 700 if (bold_first and i == 0) or (emphasis and line in emphasis) else 400
            color = dark if weight == 700 else "#334155"
            self.text(tx, ty, line, line_size, weight, color, mono=mono_lines, max_width=inner)
            ty += line_size + 5
        if ty - line_size - 5 > y + h - 4:
            self.warnings.append(f"vertical overflow in box {title!r}")

    def arrow(self, points, label=None, label_at=None, dashed=False, both=False, color="#94a3b8"):
        pts = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        start = ' marker-start="url(#arrowStart)"' if both else ""
        self.fg.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"{dash}{start} marker-end="url(#arrow)"/>')
        if label:
            lx, ly = label_at
            width = text_width(label, 10) + 14
            self.fg.append(f'<rect x="{lx - width / 2:.1f}" y="{ly - 12:.1f}" width="{width:.1f}" height="18" rx="4" fill="#ffffff" stroke="#94a3b8" stroke-width="0.5"/>')
            self.text(lx, ly + 1, label, 10, 400, "#64748b", "middle", italic=True, target=self.fg)

    def number(self, cx, cy, n, color="#0078d4"):
        self.fg.append(f'<circle cx="{cx}" cy="{cy}" r="12" fill="{color}"/>')
        self.text(cx, cy + 4, str(n), 11, 700, "#ffffff", "middle", target=self.fg)

    def legend(self, x, y, items):
        self.text(x, y + 12, "LEGEND:", 10, 700, "#475569")
        cx = x + 62
        for kind, value, label in items:
            if kind == "swatch":
                header, fill, stroke, _ = value
                self.rect(cx, y + 1, 14, 14, fill, header, 1.2, 3)
                cx += 20
            elif kind == "icon":
                self.icon(value, cx, y - 1, 18)
                cx += 22
            elif kind == "arrow":
                self.body.append(f'<line x1="{cx}" y1="{y + 8}" x2="{cx + 26}" y2="{y + 8}" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>')
                cx += 32
            elif kind == "dashed":
                self.body.append(f'<line x1="{cx}" y1="{y + 8}" x2="{cx + 26}" y2="{y + 8}" stroke="#94a3b8" stroke-width="2" stroke-dasharray="6,4"/>')
                cx += 32
            self.text(cx, y + 12, label, 10, 400, "#475569")
            cx += text_width(label, 10) + 22
        if cx > self.w - 20:
            self.warnings.append(f"legend too wide: ends at {cx:.0f} > {self.w - 20}")

    def save(self, name):
        icon_defs = []
        for icon_name in sorted(self.used_icons):
            content = load_azure_icon(icon_name, AZURE_ICONS[icon_name]) if icon_name in AZURE_ICONS else CUSTOM_ICONS[icon_name]
            icon_defs.append(f'<g id="icon-{icon_name}">{content}</g>')
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {self.w} {self.h}" '
            f'font-family="{SANS}" role="img" aria-labelledby="title-{name}">',
            f'<title id="title-{name}">{escape(self.title)}</title>',
            "<defs>",
            '<filter id="shadow" x="-4%" y="-4%" width="108%" height="112%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.15"/></filter>',
            '<filter id="shadowLg" x="-2%" y="-4%" width="104%" height="112%"><feDropShadow dx="0" dy="3" stdDeviation="5" flood-opacity="0.12"/></filter>',
            '<marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M1 1 L10 6 L1 11 Z" fill="#94a3b8"/></marker>',
            '<marker id="arrowStart" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" refX="2" refY="6" orient="auto"><path d="M11 1 L2 6 L11 11 Z" fill="#94a3b8"/></marker>',
            *self.defs, *icon_defs, "</defs>",
            *self.body,
            "<!-- foreground: cross-layer arrows and labels -->",
            *self.fg, "</svg>",
        ]
        (OUT / f"{name}.svg").write_text("\n".join(svg), encoding="utf-8")
        status = "OK" if not self.warnings else "WARN"
        print(f"{status} {name}.svg" + "".join(f"\n   - {w}" for w in self.warnings))
        return not self.warnings


# =============================================================================================
def architecture():
    s = Svg(1510, 1000, "Fabric CI/CD design: branches, pipeline, workspaces and variable library value sets",
            "Only Dev is connected to Git. UAT and Prod are deployed from their branch by fabric-cicd in one Azure Pipeline.")
    cols = [70, 545, 1020]
    cw = 420
    env = ["dev", "uat", "prod"]

    # Layer 1: Azure Repos
    s.layer(50, 85, 1410, 130, "AZURE REPOS GIT  ·  anildwa-fabric-deployments", REPOS)
    branch_text = {
        "dev": ["Dev workspace commits here (Source control)", "Developers raise PRs from here"],
        "uat": ["Changes arrive by PR from dev", "A merge triggers the UAT deployment"],
        "prod": ["Changes arrive by PR from uat", "A merge triggers the Prod deployment"],
    }
    for x, e in zip(cols, env):
        s.box(x, 125, cw, 74, REPOS, f"branch: {e}", branch_text[e], icon="branch", icon_size=34)

    # Layer 2: Git integration (Dev) and Azure Pipeline (UAT/Prod)
    s.layer(50, 255, 460, 165, "FABRIC GIT INTEGRATION", GIT)
    s.box(70, 295, cw, 110, GIT, "Source control: Commit / Update", [
        "Only the Dev workspace is connected to Git", "Developers build and test in Dev",
        "Commit sends item definitions to the dev branch"], icon="fabric")
    s.layer(525, 255, 935, 165, "AZURE PIPELINES  ·  fabric-cicd-deploy  (deploy/azure-pipelines.yml)", PIPE)
    for x, e, sc in ((545, "uat", "fabric-cicd-uat"), (1020, "prod", "fabric-cicd-prod")):
        s.box(x, 295, cw, 110, PIPE, f"Deploy {e.upper()}  (runs on branch {e})", [
            "1  Validate: deploy.py --check-only",
            f"2  Approval: Azure DevOps environment {e.upper()}",
            f"3  deploy.py --environment {e}  (fabric-cicd)",
            f"Service connection {sc} (workload identity)"], icon="ado")

    # Layer 3: Fabric workspaces
    s.layer(50, 460, 1410, 345, "MICROSOFT FABRIC  ·  one workspace per environment, each with its own lakehouse and SQL database", FABRIC)
    names = ["anildwa-dev-workspace", "anildwa-uat-workspace", "anildwa-prod-workspace"]
    sub = ["Git-connected to dev", "Deployed from uat by fabric-cicd", "Deployed from prod by fabric-cicd"]
    active = ["Default value set", "uat", "prod"]
    ids = ["Dev IDs", "UAT IDs", "Prod IDs"]
    who = [("person", "Developers  ·  Admin / Member", "Edit items, commit to Git"),
           ("appreg", "sp-fabric-cicd-uat  ·  Contributor", "UAT workspace only, no secret"),
           ("appreg", "sp-fabric-cicd-prod  ·  Contributor", "Prod workspace only, no secret")]
    for i, x in enumerate(cols):
        wx = x - 10
        s.rect(wx, 500, cw + 20, 290, "#ffffff", FABRIC[2], 1.2, 8, shadow="shadow")
        s.icon("fabric", wx + 12, 508, 22)
        s.text(wx + 42, 524, names[i], 12, 700, FABRIC[3], max_width=cw - 40)
        s.text(wx + 42, 540, sub[i], 10, 400, "#475569", max_width=cw - 40)
        s.box(x, 552, cw, 74, LIB, "Variable library  CustomerDemographicsConfig", [
            f"Active value set: {active[i]}", f"Items read {ids[i]} from the active value set"],
            icon="varlib", icon_size=34, emphasis={f"Active value set: {active[i]}"})
        tile_w = (cw - 3 * 10) / 4
        tiles = [("lakehouse", "Lakehouse", "own ID"), ("sqldb", "SQL database", "own ID + schema"),
                 ("notebook", "Notebook", "%%configure"), ("adf", "Copy pipeline", "libraryVariables")]
        for j, (ic, title, line) in enumerate(tiles):
            s.box(x + j * (tile_w + 10), 640, tile_w, 86, SLATE, title, [line], icon=ic, icon_size=30, center=True,
                  title_size=11)
        s.box(x, 740, cw, 40, IDENT, who[i][1], [], icon=who[i][0], icon_size=22, title_size=11)
        s.text(x + cw - 12, 765, who[i][2], 10, 400, "#475569", "end")

    # Naming rule
    s.rect(50, 830, 1410, 46, RED[1], RED[0], 1.2, 8)
    s.text(755, 858, "Naming rule:  branch name  =  --environment passed to fabric-cicd  =  value set name   (uat, prod)  —  exact and case-sensitive",
           13, 700, RED[3], "middle", max_width=1390)

    # Cross-layer arrows (foreground). They stop at each layer's top edge so they never cross header text.
    s.arrow([(280, 203), (280, 251)], "commit / update", (280, 230), both=True)
    s.arrow([(755, 203), (755, 251)], "merge triggers run", (755, 230))
    s.arrow([(1230, 203), (1230, 251)], "merge triggers run", (1230, 230))
    s.arrow([(280, 409), (280, 456)], "sync items", (280, 438))
    s.arrow([(755, 409), (755, 456)], "publish · activate 'uat' · verify", (755, 438))
    s.arrow([(1230, 409), (1230, 456)], "publish · activate 'prod' · verify", (1230, 438))
    s.arrow([(490, 162), (541, 162)], "PR", (515, 150))
    s.arrow([(965, 162), (1016, 162)], "PR", (990, 150))

    s.legend(50, 905, [
        ("swatch", REPOS, "Azure Repos"), ("swatch", PIPE, "Azure Pipelines"), ("swatch", GIT, "Fabric Git integration"),
        ("swatch", FABRIC, "Fabric workspace"), ("swatch", LIB, "Variable library"), ("swatch", IDENT, "Identity"),
        ("arrow", None, "Flow"), ("icon", "fabric", "Fabric item (custom glyph)")])
    s.text(50, 950, "Azure icons from Azure_Icons: Branch, Azure DevOps, App Registrations, SQL Database, Data Factories. "
                    "Fabric has no icon in that set; Fabric items use custom glyphs.", 10, 400, "#64748b")
    return s.save("01-architecture")


def value_sets():
    s = Svg(1510, 960, "Variable library value sets: one library, one value set per environment",
            "Each workspace activates one value set. Items always read the active value set, so the same code gets each environment's IDs.")
    name_x, name_w = 70, 300
    vx = [390, 745, 1100]
    vw = 340
    s.layer(50, 85, 1410, 420, "VARIABLE LIBRARY  ·  CustomerDemographicsConfig  (one item, stored in Git)", LIB)
    heads = [("Default value set", "variables.json  ·  active in Dev", SLATE),
             ("uat", "valueSets/uat.json  ·  active in UAT", PIPE),
             ("prod", "valueSets/prod.json  ·  active in Prod", FABRIC)]
    s.box(name_x, 125, name_w, 52, LIB, "Variable", ["Defined once in variables.json"])
    for x, (title, sub, pal) in zip(vx, heads):
        s.box(x, 125, vw, 52, pal, title, [sub])
    rows = [
        ("DestinationWorkspaceId", "e2ebf2eb-…  Dev workspace", "af368112-…  UAT workspace", "8dc36b95-…  Prod workspace"),
        ("DestinationDatabaseId", "3e769b16-…  Dev wwi-importers", "7da6ebb8-…  UAT wwi-importers", "f862e8e8-…  Prod wwi-importers"),
        ("LakehouseWorkspaceId", "e2ebf2eb-…  Dev workspace", "af368112-…  UAT workspace", "8dc36b95-…  Prod workspace"),
        ("LakehouseId", "8340a38b-…  Dev lakehouse", "20803294-…  UAT lakehouse", "c2021fce-…  Prod lakehouse"),
        ("LakehouseName", "CustomerDemographicsLakehouse", "inherits Default", "inherits Default"),
        ("Source*, connections, schema, table", "shared values", "inherits Default", "inherits Default"),
    ]
    y = 187
    for name, *values in rows:
        s.rect(name_x, y, name_w, 36, "#ffffff", LIB[2], 1, 6)
        s.text(name_x + 12, y + 23, name, 11, 600, LIB[3], mono=True, max_width=name_w - 20)
        for x, value, (_, _, pal) in zip(vx, values, heads):
            inherits = value == "inherits Default"
            s.rect(x, y, vw, 36, "#ffffff" if inherits else pal[1], pal[2], 1, 6, dash="4,3" if inherits else None)
            s.text(x + 12, y + 23, ("↳ " + value) if inherits else value, 11, 400, "#94a3b8" if inherits else "#1e293b",
                   italic=inherits, mono=not inherits and "…" in value, max_width=vw - 20)
        y += 44
    s.text(name_x, y + 14, "A value set stores only overrides. Every environment-specific ID must be overridden, otherwise that environment "
                           "silently inherits the Default (Dev) value.", 11, 600, LIB[3], max_width=1370)
    s.text(name_x, y + 34, "settings.json  →  \"valueSetsOrder\": [\"uat\", \"prod\"]   ·   value set names must equal the environment the pipeline "
                           "passes (the branch name).", 11, 400, "#334155", max_width=1370)

    s.layer(50, 545, 1410, 175, "MICROSOFT FABRIC WORKSPACES  ·  each activates one value set", FABRIC)
    s.box(name_x, 585, name_w, 120, IDENT, "Active value set", [
        "A setting on the library in each workspace", "Not stored in Git, not in the definition",
        "fabric-cicd sets it on every deployment", "Manual: library  ⋯  › Set as active"])
    ws = [("anildwa-dev-workspace", "Default value set", "Set when Dev was created; not changed by CI/CD"),
          ("anildwa-uat-workspace", "uat", "Set by fabric-cicd: --environment uat"),
          ("anildwa-prod-workspace", "prod", "Set by fabric-cicd: --environment prod")]
    for x, (name, act, how), (_, _, pal) in zip(vx, ws, heads):
        s.box(x, 585, vw, 120, FABRIC, name, [f"Active value set: {act}", how, "Reads only this column's values"],
              icon="fabric", icon_size=30, emphasis={f"Active value set: {act}"})

    s.layer(50, 760, 1410, 120, "SAME CODE IN EVERY WORKSPACE  ·  no IDs in item definitions", SLATE)
    code = [("notebook", "Notebook", 'getLibrary("CustomerDemographicsConfig").LakehouseId'),
            ("notebook", "Notebook %%configure", '$(/**/CustomerDemographicsConfig/LakehouseId)'),
            ("adf", "Copy pipeline", "@pipeline().libraryVariables.LakehouseId")]
    for i, (ic, title, snippet) in enumerate(code):
        x = 70 + i * 467
        s.box(x, 800, 450, 64, SLATE, title, [snippet], icon=ic, icon_size=30, mono_lines=True)

    for x in vx:
        s.arrow([(x + vw / 2, 509), (x + vw / 2, 541)], "active", (x + vw / 2, 527))

    s.legend(50, 900, [("swatch", LIB, "Variable library"), ("swatch", SLATE, "Default value set (Dev)"),
                       ("swatch", PIPE, "uat value set"), ("swatch", FABRIC, "prod value set / workspace"),
                       ("dashed", None, "inherits Default"), ("arrow", None, "Active in"),
                       ("icon", "varlib", "Variable library (custom glyph)")])
    return s.save("02-value-sets")


def activation():
    s = Svg(1270, 660, "How the right value set becomes active during a deployment",
            "The branch name flows through the pipeline to fabric-cicd, which activates the value set with exactly that name.")
    w, h = 260, 104
    xs = [60, 350, 640, 930]
    row1 = [("branch", "Merge PR into prod", ["Pull request uat → prod completed", "Branch policy / reviewers apply"], REPOS),
            ("ado", "Pipeline run starts", ["Build.SourceBranchName = \"prod\"", "targetEnvironment = prod"], PIPE),
            ("ado", "Validate (no credentials)", ["deploy.py --environment prod --check-only", "Value set 'prod' exists and overrides", "every workspace/lakehouse/DB ID"], PIPE),
            ("ado", "Approve PROD", ["Azure DevOps environment PROD", "Approval check before deploying"], PIPE)]
    for i, (ic, title, lines, pal) in enumerate(row1):
        s.box(xs[i], 100, w, h, pal, title, lines, icon=ic, icon_size=30)
        s.number(xs[i] + 4, 104, i + 1)
    for i in range(3):
        s.arrow([(xs[i] + w + 2, 152), (xs[i + 1] - 4, 152)])

    y2 = 270
    s.box(930, y2, w, h, FABRIC, "Publish with fabric-cicd", ["deploy.py --environment prod", "Items published unchanged", "(no IDs are replaced)"], icon="fabric", icon_size=30)
    s.number(934, y2 + 4, 5)
    s.arrow([(1060, 206), (1060, y2 - 4)])

    dx, dw, dh = 650, 240, 124
    cy = y2 + h / 2
    s.body.append(f'<polygon points="{dx + dw / 2},{cy - dh / 2} {dx + dw},{cy} {dx + dw / 2},{cy + dh / 2} {dx},{cy}" '
                  f'fill="{PIPE[1]}" stroke="{PIPE[0]}" stroke-width="1.5" filter="url(#shadow)"/>')
    for k, line in enumerate(["\"prod\" in settings.json", "valueSetsOrder?", "(exact, case-sensitive)"]):
        s.text(dx + dw / 2, cy - 14 + k * 16, line, 11 if k < 2 else 10, 700 if k < 2 else 400, PIPE[3], "middle", max_width=150)
    s.number(dx + dw / 2, cy - dh / 2 - 2, 6)
    s.arrow([(928, cy), (dx + dw + 4, cy)])

    s.box(350, y2, w, h, FABRIC, "Activate value set 'prod'", ["PATCH …/variableLibraries/{id}", "activeValueSetName = \"prod\"", "Run log: Active value set changed"], icon="varlib", icon_size=30)
    s.number(354, y2 + 4, 7)
    s.arrow([(dx - 2, cy), (350 + w + 4, cy)], "yes", (630, cy - 12))

    s.box(60, y2, w, h, FABRIC, "Verify the Prod workspace", ["Active value set == prod", "Every ID belongs to Prod", "References resolve, else FAIL"], icon="ado", icon_size=30)
    s.number(64, y2 + 4, 8)
    s.arrow([(348, cy), (60 + w + 4, cy)])

    s.box(560, 450, 420, 104, RED, "No match: Default value set is activated", [
        "fabric-cicd only logs a warning: \"does not match any value sets.", "Using 'Default value set'\" → Prod items read Dev IDs.",
        "Prevented here by step 3, which fails before publishing."], icon="varlib", icon_size=30)
    s.arrow([(dx + dw / 2, cy + dh / 2 + 2), (dx + dw / 2, 446)], "no", (dx + dw / 2 + 22, 420))

    s.legend(60, 600, [("swatch", REPOS, "Azure Repos"), ("swatch", PIPE, "Azure Pipelines"), ("swatch", FABRIC, "Fabric"),
                       ("swatch", RED, "Failure mode"), ("arrow", None, "Flow"), ("icon", "fabric", "Fabric (custom glyph)")])
    return s.save("03-activation")


def anti_pattern():
    s = Svg(1510, 690, "Who owns the environment values? Two owners vs one owner",
            "Value sets and parameter.yml can both set environment IDs. Using both for the same values overwrites the library in the target workspace.")
    panels = [(50, "ANTI-PATTERN  ·  value sets + unfiltered find_replace", RED),
              (775, "RECOMMENDED  ·  one owner per value", FABRIC)]
    for x, title, pal in panels:
        s.layer(x, 85, 685, 505, title, pal)
    bx, bw = 30, 645
    left = [
        (LIB, "Library in Git  (correct)", ["Default value set: LakehouseId = <Dev lakehouse ID>",
                                            "DEV value set: inherits Default (Dev)",
                                            "UAT Value set: LakehouseId = <UAT lakehouse ID>"], "varlib"),
        (RED, "parameter.yml  —  find_replace with no filter", ["find_value: \"<Dev lakehouse ID>\"   →   uat: $items.Lakehouse.<name>.$id",
                                                                "No item_type / item_name / file_path, so it rewrites the ID",
                                                                "in EVERY file of EVERY item, including the variable library"], "ado"),
        (RED, "Library deployed to UAT  (broken)", ["Default value set: LakehouseId = <UAT lakehouse ID>  (rewritten)",
                                                    "DEV value set: now shows UAT IDs",
                                                    "Active: Default value set  (branch 'uat' ≠ 'UAT Value set')"], "varlib"),
    ]
    right = [
        (LIB, "Library in Git", ["Default value set: <Dev IDs>", "uat: <UAT IDs>     prod: <Prod IDs>",
                                 "settings.json: valueSetsOrder = [\"uat\", \"prod\"]"], "varlib"),
        (PIPE, "Pipeline: environment = branch = \"uat\"", ["No parameter.yml rule touches the variable library",
                                                         "parameter.yml only for IDs inside item definitions that",
                                                         "can't read the library, always filtered by item_type/name"], "ado"),
        (FABRIC, "Library deployed to UAT  (correct)", ["Default value set: <Dev IDs>  (unchanged)",
                                                        "uat: <UAT IDs>     prod: <Prod IDs>",
                                                        "Active: uat  →  items read UAT IDs"], "varlib"),
    ]
    for (px, _, _), items in zip(panels, (left, right)):
        y = 128
        for k, (pal, title, lines, ic) in enumerate(items):
            s.box(px + bx - 10, y, bw, 92, pal, title, lines, icon=ic, icon_size=32)
            if k < 2:
                s.arrow([(px + 342, y + 94), (px + 342, y + 128)])
            y += 132
    s.box(60, 524, 665, 52, RED, "Result: Dev and UAT values look identical in UAT", ["Two mechanisms set the same values; the broadest rule wins."])
    s.box(785, 524, 665, 52, FABRIC, "Result: each workspace uses its own value set", ["One owner: value sets. Git shows every environment's IDs."])
    s.legend(50, 612, [("swatch", LIB, "Variable library"), ("swatch", PIPE, "Azure Pipelines"), ("swatch", RED, "Problem"),
                       ("swatch", FABRIC, "Correct state"), ("arrow", None, "Deployment flow"),
                       ("icon", "varlib", "Variable library (custom glyph)")])
    s.text(50, 652, "Placeholders like <Dev lakehouse ID> stand for real GUIDs.", 10, 400, "#64748b")
    return s.save("04-two-owners")


if __name__ == "__main__":
    results = [architecture(), value_sets(), activation(), anti_pattern()]
    sys.exit(0 if all(results) else 1)
