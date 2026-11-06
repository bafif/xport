"""Genera los diagramas del README y de la página: `flujo` (cómo funciona) y `tope` (la
ventana de 24 horas), en castellano e inglés. SVG a mano, sin dependencias, con la
paleta oscura del popup y fondo propio, así se leen igual en el tema claro y el oscuro.

    python3 docs/img/generar.py

`popup.png` no sale de acá: es el popup real, renderizado con datos de ejemplo.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

DIR = Path(__file__).parent
FONT = "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
BG, CARD, BORDER = "#161d26", "#1e2732", "#38444d"
TEXT, MUTED = "#e7e9ea", "#8b98a5"
BLUE, AMBER, RED = "#1d9bf0", "#ffd400", "#f4212e"

TEXTOS = {
    "es": {
        "flujo_titulo": "Cómo funciona",
        "cajas": [
            ("x.com", ["en tu navegador,", "con tu sesión"]),
            ("Extensión", ["scrollea la búsqueda y", "copia lo que x.com", "ya descargó"]),
            ("Servicio local", ["interpreta y guarda", "sin repetir"]),
            ("CSV", ["uno por cuenta"]),
        ],
        "tope_caja": ("Tope de 24 horas", ["cuenta cada post visto", "y frena en 900.000"]),
        "corte": ["si no lo puede contar,", "la captura se corta"],
        "tope_titulo": "El tope: una ventana de 24 horas que se corre con el reloj",
        "ejes": ("hace 48 h", "hace 24 h", "ahora"),
        "ventana": "cuenta: las últimas 24 horas",
        "afuera": "ya no cuenta",
        "suma": "se suma",
        "terminos": "1.000.000: términos de X",
        "cap": "900.000: tope de xport",
        "uso": "uso ahora",
        "pie": (
            "Cada acceso se anota con su hora y sale de la cuenta a las 24 horas. "
            "Si no entra, el servidor espera y la extensión corta."
        ),
    },
    "en": {
        "flujo_titulo": "How it works",
        "cajas": [
            ("x.com", ["in your browser,", "with your session"]),
            ("Extension", ["scrolls the search and", "copies what x.com", "already downloaded"]),
            ("Local service", ["parses and stores", "without repeats"]),
            ("CSV", ["one per account"]),
        ],
        "tope_caja": ("24-hour cap", ["counts every post seen", "and stops at 900,000"]),
        "corte": ["if it cannot count it,", "the capture stops"],
        "tope_titulo": "The cap: a 24-hour window that moves with the clock",
        "ejes": ("48 h ago", "24 h ago", "now"),
        "ventana": "counted: the last 24 hours",
        "afuera": "no longer counted",
        "suma": "adds up",
        "terminos": "1,000,000: X's terms",
        "cap": "900,000: xport's cap",
        "uso": "usage now",
        "pie": (
            "Every access is logged with its time and leaves the count after 24 hours. "
            "If it does not fit, the server waits and the extension stops."
        ),
    },
}


def texto(
    x: float,
    y: float,
    s: str,
    *,
    size: float = 13,
    color: str = TEXT,
    anchor: str = "start",
    weight: str = "normal",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" '
        f'font-weight="{weight}">{escape(s)}</text>'
    )


def caja(x: float, y: float, w: float, h: float, titulo: str, lineas: list[str], borde: str) -> str:
    partes = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{CARD}" '
        f'stroke="{borde}" stroke-width="1.5"/>',
        texto(x + 16, y + 30, titulo, size=15, weight="bold"),
    ]
    partes += [texto(x + 16, y + 54 + 19 * i, linea, color=MUTED) for i, linea in enumerate(lineas)]
    return "\n".join(partes)


def svg(w: int, h: int, cuerpo: list[str], titulo: str) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}" role="img" '
            f'aria-label="{escape(titulo)}">',
            f"<title>{escape(titulo)}</title>",
            '<defs><marker id="flecha" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>',
            f'<rect width="{w}" height="{h}" rx="16" fill="{BG}"/>',
            *cuerpo,
            "</svg>",
            "",
        ]
    )


def flujo(t: dict) -> str:
    cuerpo = [texto(24, 38, t["flujo_titulo"], size=18, weight="bold")]
    xs, ws = [24, 254, 504, 724], [190, 210, 180, 152]
    for (titulo, lineas), x, w in zip(t["cajas"], xs, ws, strict=True):
        cuerpo.append(caja(x, 62, w, 124, titulo, lineas, BLUE if titulo == "x.com" else BORDER))
    for x, w, siguiente in zip(xs, ws, xs[1:], strict=False):
        cuerpo.append(
            f'<line x1="{x + w + 4}" y1="124" x2="{siguiente - 6}" y2="124" stroke="{MUTED}" '
            'stroke-width="2" marker-end="url(#flecha)"/>'
        )
    titulo, lineas = t["tope_caja"]
    cuerpo.append(caja(494, 222, 200, 96, titulo, lineas, AMBER))
    cuerpo.append(
        f'<line x1="594" y1="190" x2="594" y2="218" stroke="{MUTED}" stroke-width="2" '
        'marker-start="url(#flecha)" marker-end="url(#flecha)"/>'
    )
    cuerpo.append(
        f'<path d="M490,270 L359,270 L359,192" fill="none" stroke="{AMBER}" stroke-width="2" '
        'stroke-dasharray="6 5" marker-end="url(#flecha)"/>'
    )
    cuerpo += [
        texto(348, 258 + 18 * i, linea, color=AMBER, anchor="end")
        for i, linea in enumerate(t["corte"])
    ]
    return svg(900, 340, cuerpo, t["flujo_titulo"])


def tope(t: dict) -> str:
    x0, x1, base = 40, 600, 222

    def x_hora(h: float) -> float:  # h de -48 (hace 48 h) a 0 (ahora)
        return x0 + (h + 48) / 48 * (x1 - x0)

    cuerpo = [texto(24, 36, t["tope_titulo"], size=17, weight="bold")]
    ventana = x_hora(-24)
    cuerpo.append(
        f'<rect x="{ventana}" y="54" width="{x1 - ventana}" height="{base - 54}" rx="6" '
        f'fill="{BLUE}" fill-opacity="0.10" stroke="{BLUE}" stroke-dasharray="5 4"/>'
    )
    cuerpo.append(texto(ventana + 10, 74, t["ventana"], color=BLUE))
    cuerpo.append(texto(x0 + 4, 74, t["afuera"], color=MUTED))
    barras = [
        (-46, 30),
        (-43, 55),
        (-41, 20),
        (-37, 70),
        (-33, 40),
        (-30, 25),
        (-27, 60),
        (-22, 35),
        (-19, 80),
        (-16, 50),
        (-12, 65),
        (-9, 30),
        (-7, 90),
        (-4, 45),
        (-2, 60),
    ]
    for h, alto in barras:
        adentro = h > -24
        cuerpo.append(
            f'<rect x="{x_hora(h) - 5:.1f}" y="{base - alto}" width="10" height="{alto}" rx="2" '
            f'fill="{BLUE if adentro else MUTED}" fill-opacity="{1 if adentro else 0.35}"/>'
        )
    cuerpo.append(
        f'<line x1="{x0}" y1="{base}" x2="{x1}" y2="{base}" stroke="{BORDER}" stroke-width="2"/>'
    )
    for x, etiqueta, anchor in zip(
        (x0, ventana, x1), t["ejes"], ("start", "middle", "end"), strict=True
    ):
        cuerpo.append(texto(x, base + 22, etiqueta, color=MUTED, anchor=anchor))
    # El medidor: arriba 1.000.000 (términos), la línea de 900.000 (tope) y el uso actual.
    mx, top, abajo = 700, 58, base

    def y_uso(fraccion: float) -> float:
        return abajo - fraccion * (abajo - top)

    cuerpo.append(
        f'<line x1="{x1 + 12}" y1="150" x2="{mx - 12}" y2="150" stroke="{MUTED}" stroke-width="2" '
        'marker-end="url(#flecha)"/>'
    )
    cuerpo.append(texto((x1 + mx) / 2, 140, t["suma"], color=MUTED, anchor="middle"))
    cuerpo.append(
        f'<rect x="{mx}" y="{top}" width="40" height="{abajo - top}" rx="6" fill="{CARD}" '
        f'stroke="{BORDER}"/>'
    )
    cuerpo.append(
        f'<rect x="{mx}" y="{y_uso(0.35)}" width="40" height="{abajo - y_uso(0.35)}" rx="6" '
        f'fill="{BLUE}"/>'
    )
    for fraccion, color, etiqueta, dy in (
        (1.0, RED, t["terminos"], -6),
        (0.9, AMBER, t["cap"], 16),
    ):
        y = y_uso(fraccion)
        cuerpo.append(
            f'<line x1="{mx - 6}" y1="{y}" x2="{mx + 46}" y2="{y}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        cuerpo.append(texto(mx + 56, y + dy, etiqueta, color=color))
    cuerpo.append(texto(mx + 56, y_uso(0.35) + 5, t["uso"], color=BLUE))
    cuerpo.append(texto(24, 288, t["pie"], color=MUTED))
    return svg(960, 310, cuerpo, t["tope_titulo"])


def main() -> None:
    for idioma, t in TEXTOS.items():
        (DIR / f"flujo.{idioma}.svg").write_text(flujo(t), encoding="utf-8")
        (DIR / f"tope.{idioma}.svg").write_text(tope(t), encoding="utf-8")


if __name__ == "__main__":
    main()
