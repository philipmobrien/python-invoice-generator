#!/usr/bin/env python3
"""
Invoice generator — config-driven, multi-client, multi-entity.

Usage:
    python3 generate_invoice.py -c client.yaml
    python3 generate_invoice.py -c client.yaml -qt 2
    python3 generate_invoice.py -c client.yaml -qt 2 3 1

Each client has their own YAML config and invoice log.
A global settings.yaml defines base paths.
"""

import argparse
import re
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, PageTemplate,
    Paragraph, Spacer, Table, TableStyle
)


# ── Colours ───────────────────────────────────────────────────────────────────

BLACK      = colors.HexColor("#1a1a1a")
MID_GREY   = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#f2f2f2")
RULE_GREY  = colors.HexColor("#cccccc")


# ── Named tuples ──────────────────────────────────────────────────────────────

Paths = namedtuple("Paths", ["base", "config", "generated", "clients"])

Layout = namedtuple("Layout", [
    "page_w", "page_h",
    "margin_l", "margin_r", "margin_t", "margin_b",
    "content_w", "left_w", "right_w"
])

InvoiceStyles = namedtuple("InvoiceStyles", [
    "normal", "bold", "invoice", "entity",
    "bank_label", "bank_val", "vat_note"
])


# ── Styles ────────────────────────────────────────────────────────────────────

def build_styles() -> InvoiceStyles:
    def style(name, font="Helvetica", size=9, leading=13, align=TA_LEFT,
              textColor=BLACK, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=font, fontSize=size,
                              leading=leading, alignment=align,
                              textColor=textColor, spaceBefore=spaceBefore,
                              spaceAfter=spaceAfter)

    return InvoiceStyles(
        normal     = style("normal"),
        bold       = style("bold",    font="Helvetica-Bold"),
        invoice    = style("invoice", font="Helvetica", size=26, leading=30),
        entity     = style("entity",  font="Helvetica-Bold", size=11, leading=14),
        bank_label = style("blabel",  textColor=MID_GREY),
        bank_val   = style("bval"),
        vat_note   = style("vat_note", font="Helvetica-Oblique", size=8,
                           leading=11, textColor=MID_GREY),
    )


STYLES = build_styles()


# ── Required config keys ──────────────────────────────────────────────────────

REQUIRED_CONFIG_KEYS = {
    "entity":       ["name", "logo_file"],
    "client":       ["name", "company", "address"],
    "your_details": ["phone", "email", "address"],
    "bank":         ["account_name", "sort_code", "account", "bank"],
    "root":         ["log_file", "re_line", "terms", "line_items"],
}


# ── Settings and paths ────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent


def load_settings() -> dict:
    settings_path = SCRIPT_DIR / "settings.yaml"
    if not settings_path.exists():
        print(f"settings.yaml not found in {SCRIPT_DIR}")
        sys.exit(1)
    with open(settings_path, "r") as f:
        return yaml.safe_load(f)


def get_paths(settings: dict) -> Paths:
    base = Path(settings["base_dir"]).expanduser()
    return Paths(
        base      = base,
        config    = base / settings.get("config_subdir",    "Config"),
        generated = base / settings.get("generated_subdir", "Generated"),
        clients   = base / settings.get("clients_subdir",   "Clients"),
    )


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def validate_config(cfg: dict, config_path: Path) -> None:
    errors = []

    for key in REQUIRED_CONFIG_KEYS["root"]:
        if key not in cfg:
            errors.append(f"Missing required field: '{key}'")

    for section, keys in REQUIRED_CONFIG_KEYS.items():
        if section == "root":
            continue
        if section not in cfg:
            errors.append(f"Missing required section: '{section}'")
            continue
        for key in keys:
            if key not in cfg[section]:
                errors.append(f"Missing required field: '{section}.{key}'")

    for i, item in enumerate(cfg.get("line_items", [])):
        if "description" not in item:
            errors.append(f"line_items[{i}] missing 'description'")
        if "unit_price" not in item:
            errors.append(f"line_items[{i}] missing 'unit_price'")

    if errors:
        print(f"Config error(s) in {config_path.name}:")
        for e in errors:
            print(f"  — {e}")
        sys.exit(1)


# ── Log helpers ───────────────────────────────────────────────────────────────

def log_path(cfg: dict, paths: Paths) -> Path:
    return paths.config / cfg["log_file"]


def last_invoice_number(cfg: dict, paths: Paths) -> str:
    lf = log_path(cfg, paths)
    lines = [l.strip() for l in lf.read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    if not lines:
        raise ValueError(f"Log file {lf} is empty — add a seed entry to start.")
    return lines[-1].split()[0]


def append_to_log(cfg: dict, paths: Paths, invoice_number: str, date_str: str) -> None:
    lf = log_path(cfg, paths)
    content = lf.read_text()
    with open(lf, "a") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"{invoice_number}  {date_str}\n")


def next_invoice_number(current: str) -> str:
    prefix = re.match(r"[A-Za-z]+", current).group()
    num    = int(re.search(r"\d+", current).group())
    width  = len(current) - len(prefix)
    return f"{prefix}{str(num + 1).zfill(width)}"


def today_str() -> str:
    return datetime.today().strftime("%d/%m/%Y")


def due_date_str(payment_days: int) -> str:
    return (datetime.today() + timedelta(days=payment_days)).strftime("%d/%m/%Y")


# ── Quantity resolution ───────────────────────────────────────────────────────

def resolve_qty(items: list, qty_list: list) -> list:
    """
    Returns a list of resolved quantities, one per line item.
    Scaleable items are assigned positionally from qty_list; non-scaleable are always 1.
    Fewer qty values than scaleable items: remainder default to 1.
    More qty values than scaleable items: excess logged as a warning.
    """
    n_scaleable   = sum(1 for item in items if item.get("scaleable", True))
    scaleable_idx = 0
    resolved      = []

    if len(qty_list) > n_scaleable:
        print(f"Warning: {len(qty_list)} quantities supplied but only "
              f"{n_scaleable} scaleable item(s). Extra values ignored.")

    for item in items:
        if item.get("scaleable", True):
            qty = qty_list[scaleable_idx] if scaleable_idx < len(qty_list) else 1
            scaleable_idx += 1
        else:
            qty = 1
        resolved.append(qty)

    return resolved


# ── Document builder ──────────────────────────────────────────────────────────

def _build_doc(pdf_path: Path, layout: Layout) -> BaseDocTemplate:
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(layout.margin_l, layout.page_h - 38*mm,
                    layout.page_w - layout.margin_r, layout.page_h - 38*mm)
        canvas.line(layout.margin_l, layout.margin_b + 4*mm,
                    layout.page_w - layout.margin_r, layout.margin_b + 4*mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MID_GREY)
        canvas.drawRightString(layout.page_w - layout.margin_r,
                               layout.margin_b - 2*mm, str(doc.page))
        canvas.restoreState()

    frame = Frame(
        layout.margin_l, layout.margin_b + 8*mm,
        layout.content_w, layout.page_h - layout.margin_t - layout.margin_b - 8*mm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0
    )
    doc = BaseDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=layout.margin_l, rightMargin=layout.margin_r,
        topMargin=layout.margin_t, bottomMargin=layout.margin_b + 8*mm,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])
    return doc


# ── Section builders ──────────────────────────────────────────────────────────

def build_header(cfg: dict, paths: Paths, layout: Layout) -> list:
    logo_h = 18 * mm
    logo   = Image(str(paths.config / cfg["entity"]["logo_file"]),
                   width=logo_h, height=logo_h)
    table  = Table(
        [[logo, Paragraph(cfg["entity"]["name"], STYLES.entity)]],
        colWidths=[logo_h + 4*mm, layout.content_w - logo_h - 4*mm]
    )
    table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
    ]))
    return [table, Spacer(1, 14*mm)]


def build_address_block(cfg: dict, layout: Layout) -> list:
    client  = cfg["client"]
    you     = cfg["your_details"]

    client_lines = ([f"Attention: {client['name']}", client["company"]]
                    + client["address"])
    your_lines   = [you["phone"], you["email"]] + you["address"]

    your_contact = (
        [Paragraph(your_lines[0], STYLES.bold),
         Paragraph(your_lines[1], STYLES.bold),
         Spacer(1, 4*mm)]
        + [Paragraph(l, STYLES.bold) for l in your_lines[2:]]
    )
    left_col = [Paragraph("INVOICE", STYLES.invoice), Spacer(1, 4*mm)] + your_contact

    table = Table(
        [[left_col, [Paragraph(l, STYLES.normal) for l in client_lines]]],
        colWidths=[layout.left_w, layout.right_w]
    )
    table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    return [table, Spacer(1, 6*mm)]


def build_meta(cfg: dict, invoice_number: str,
               invoice_date: str, layout: Layout) -> list:
    payment_days = cfg.get("payment_days", 0)
    due_date     = due_date_str(payment_days)
    LABEL_W      = 32 * mm
    VALUE_W      = layout.right_w - LABEL_W

    table = Table(
        [
            ["", Paragraph("Date:",           STYLES.normal), Paragraph(invoice_date,   STYLES.normal)],
            ["", Paragraph("Due Date:",        STYLES.normal), Paragraph(due_date,       STYLES.normal)],
            ["", Paragraph("Invoice Number:", STYLES.normal), Paragraph(invoice_number, STYLES.normal)],
            ["", Paragraph("Terms:",          STYLES.normal), Paragraph(cfg["terms"],   STYLES.normal)],
            ["", Paragraph(f"Re. {cfg['re_line']}", STYLES.normal), ""],
        ],
        colWidths=[layout.left_w, LABEL_W, VALUE_W]
    )
    table.setStyle(TableStyle([
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1),
        ("SPAN",         (1, 4), (2, 4)),
    ]))
    return [table, Spacer(1, 6*mm)]


def build_line_items(cfg: dict, qty_list: list, layout: Layout) -> list:
    items           = cfg["line_items"]
    quantities      = resolve_qty(items, qty_list)
    vat_registered  = cfg.get("vat_registered", False)
    vat_number      = cfg.get("vat_number", None)
    vat_rate        = cfg.get("vat_rate", 0.20)
    subtotal        = 0.0
    data_rows       = []

    for item, qty in zip(items, quantities):
        unit_price = item["unit_price"]
        cost       = unit_price * qty
        subtotal  += cost
        data_rows.append([
            Paragraph(item["description"],    STYLES.normal),
            Paragraph(str(qty),               STYLES.normal),
            Paragraph(f"£{unit_price:.2f}",   STYLES.normal),
            Paragraph(f"£{cost:.2f}",         STYLES.normal),
        ])

    if vat_registered:
        vat_amount = subtotal * vat_rate
        total      = subtotal + vat_amount
        vat_label  = f"VAT ({int(vat_rate * 100)}%)"
        totals_rows = [
            ["", "", Paragraph("Subtotal",  STYLES.normal), Paragraph(f"£{subtotal:.2f}",   STYLES.normal)],
            ["", "", Paragraph(vat_label,   STYLES.normal), Paragraph(f"£{vat_amount:.2f}", STYLES.normal)],
            ["", "", Paragraph("Total",     STYLES.bold),   Paragraph(f"£{total:.2f}",      STYLES.bold)],
        ]
    else:
        total = subtotal
        totals_rows = [
            ["", "", Paragraph("Subtotal", STYLES.normal), Paragraph(f"£{subtotal:.2f}", STYLES.normal)],
            ["", "", Paragraph("Total",    STYLES.bold),   Paragraph(f"£{total:.2f}",    STYLES.bold)],
        ]

    table_data = (
        [[Paragraph(h, STYLES.bold) for h in ["Description", "Quantity", "Unit Price", "Cost"]]]
        + data_rows
        + totals_rows
    )

    DESC_W  = layout.right_w * 0.50
    QTY_W   = layout.right_w * 0.15
    PRICE_W = layout.right_w * 0.175
    COST_W  = layout.right_w * 0.175
    col_w   = [DESC_W, QTY_W, PRICE_W, COST_W]

    offset_data = [[""] + row for row in table_data]
    last_row    = len(table_data) - 1

    table = Table(offset_data, colWidths=[layout.left_w] + col_w, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",   (1, 0),  (-1, 0),             LIGHT_GREY),
        ("FONTNAME",     (1, 0),  (-1, 0),              "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0),  (-1, -1),             9),
        ("TOPPADDING",   (1, 0),  (-1, 0),              5),
        ("BOTTOMPADDING",(1, 0),  (-1, 0),              5),
        ("TOPPADDING",   (1, 1),  (-1, -1),             4),
        ("BOTTOMPADDING",(1, 1),  (-1, -1),             4),
        ("LEFTPADDING",  (0, 0),  (-1, -1),             4),
        ("RIGHTPADDING", (0, 0),  (-1, -1),             4),
        ("LEFTPADDING",  (0, 0),  (0, -1),              0),
        ("LINEBELOW",    (1, 0),  (-1, last_row - 1),   0.3, RULE_GREY),
        ("LINEABOVE",    (1, last_row), (-1, last_row), 0.8, BLACK),
        ("LINEBELOW",    (1, last_row), (-1, last_row), 0.8, BLACK),
        ("LINEBEFORE",   (2, 0),  (2, last_row - 1),    0.3, RULE_GREY),
        ("LINEBEFORE",   (3, 0),  (3, last_row - 1),    0.3, RULE_GREY),
        ("LINEBEFORE",   (4, 0),  (4, last_row - 1),    0.3, RULE_GREY),
        ("VALIGN",       (0, 0),  (-1, -1),             "MIDDLE"),
    ]))
    vat_note_style = ParagraphStyle("vat_note_offset",
                                    parent=STYLES.vat_note,
                                    leftIndent=layout.left_w)
    result = [table, Spacer(1, 4*mm)]

    if vat_registered and vat_number:
        result.append(Paragraph(f"VAT Registration Number: {vat_number}", vat_note_style))
    elif not vat_registered:
        result.append(Paragraph("VAT not applicable — not VAT registered", vat_note_style))

    result.append(Spacer(1, 6*mm))
    return result


def build_payment_details(cfg: dict, layout: Layout) -> list:
    bank = cfg["bank"]
    offset_bank = [
        ["", Paragraph("Account Name:", STYLES.bank_label), Paragraph(bank["account_name"], STYLES.bank_val)],
        ["", Paragraph("Sort Code:",    STYLES.bank_label), Paragraph(bank["sort_code"],    STYLES.bank_val)],
        ["", Paragraph("Account:",      STYLES.bank_label), Paragraph(str(bank["account"]), STYLES.bank_val)],
        ["", Paragraph("Bank:",         STYLES.bank_label), Paragraph(bank["bank"],         STYLES.bank_val)],
    ]
    table = Table(offset_bank, colWidths=[layout.left_w, 35*mm, layout.right_w - 35*mm])
    table.setStyle(TableStyle([
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    return [
        Paragraph('<u>Payment To:</u>', STYLES.normal),
        Spacer(1, 3*mm),
        table,
    ]


# ── PDF orchestrator ──────────────────────────────────────────────────────────

def build_pdf(pdf_path: Path, cfg: dict, paths: Paths,
              invoice_number: str, invoice_date: str, qty_list: list) -> None:

    layout = Layout(
        page_w    = A4[0],
        page_h    = A4[1],
        margin_l  = 20 * mm,
        margin_r  = 20 * mm,
        margin_t  = 18 * mm,
        margin_b  = 18 * mm,
        content_w = A4[0] - 40 * mm,
        left_w    = 55 * mm,
        right_w   = A4[0] - 40 * mm - 55 * mm,
    )

    doc   = _build_doc(pdf_path, layout)
    story = (
        build_header(cfg, paths, layout)
        + build_address_block(cfg, layout)
        + build_meta(cfg, invoice_number, invoice_date, layout)
        + build_line_items(cfg, qty_list, layout)
        + build_payment_details(cfg, layout)
    )
    doc.build(story)


# ── Notification ──────────────────────────────────────────────────────────────

def send_notification(invoice_number: str, pdf_path: Path) -> None:
    script = f"""
display notification "PDF saved to Generated folder — ready to send." ¬
    with title "Invoice {invoice_number} generated" ¬
    subtitle "{pdf_path.name}"
"""
    subprocess.run(["osascript", "-e", script], check=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate an invoice from a client config file")
    parser.add_argument("-c",  "--config",   required=True,
                        help="Client YAML config filename (in Clients/), e.g. sample_client.yaml")
    parser.add_argument("-qt", "--quantity", type=int, nargs="+", default=None,
                        help="Quantity per scaleable line item (space-separated). Fewer values than items defaults remainder to 1.")
    args = parser.parse_args()

    settings = load_settings()
    paths    = get_paths(settings)

    config_path = paths.clients / args.config
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    cfg = load_config(config_path)
    validate_config(cfg, config_path)

    logo_file = paths.config / cfg["entity"]["logo_file"]
    if not logo_file.exists():
        print(f"Logo not found: {logo_file}")
        sys.exit(1)

    lf = log_path(cfg, paths)
    if not lf.exists():
        print(f"Log file not found: {lf}")
        print("Create it with a seed entry, e.g.: INV0000  2026-01-01 "
              "(the first generated invoice will be INV0001)")
        sys.exit(1)

    # Quantity precedence: CLI -qt > YAML default_qty > 1
    if args.quantity is not None:
        qty_list = args.quantity
    else:
        raw = cfg.get("default_qty", 1)
        qty_list = raw if isinstance(raw, list) else [raw]

    current     = last_invoice_number(cfg, paths)
    new_invoice = next_invoice_number(current)
    new_date    = today_str()
    pdf_out     = paths.generated / f"{new_invoice}.pdf"

    print(f"Generating {new_invoice} dated {new_date} (qty: {qty_list})...")

    build_pdf(pdf_out, cfg, paths, new_invoice, new_date, qty_list)

    append_to_log(cfg, paths, new_invoice, datetime.today().strftime("%Y-%m-%d"))

    print(f"PDF saved: {pdf_out}")
    send_notification(new_invoice, pdf_out)


if __name__ == "__main__":
    main()