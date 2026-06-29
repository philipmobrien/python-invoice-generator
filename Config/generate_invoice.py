#!/usr/bin/env python3
"""
Invoice generator — config-driven, multi-client, multi-entity.

Usage:
    python3 generate_invoice.py -c client.yaml
    python3 generate_invoice.py -c client.yaml -qt 2

Each client has their own YAML config and invoice log.
A global settings.yaml defines base paths.
"""

import argparse
import re
import subprocess
import sys
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


# ── Settings loader ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent

def load_settings() -> dict:
    settings_path = SCRIPT_DIR / "settings.yaml"
    if not settings_path.exists():
        print(f"settings.yaml not found in {SCRIPT_DIR}")
        sys.exit(1)
    with open(settings_path, "r") as f:
        return yaml.safe_load(f)


def get_paths(settings: dict) -> tuple[Path, Path, Path, Path]:
    base          = Path(settings["base_dir"]).expanduser()
    config_dir    = base / settings.get("config_subdir",   "Config")
    generated_dir = base / settings.get("generated_subdir","Generated")
    clients_dir   = base / settings.get("clients_subdir",  "Clients")
    return base, config_dir, generated_dir, clients_dir


# ── Config loader ─────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ── Log helpers ───────────────────────────────────────────────────────────────

def log_path(cfg: dict, config_dir: Path) -> Path:
    return config_dir / cfg["log_file"]


def last_invoice_number(cfg: dict, config_dir: Path) -> str:
    lf = log_path(cfg, config_dir)
    lines = [l.strip() for l in lf.read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    if not lines:
        raise ValueError(f"Log file {lf} is empty — add the last used invoice number to start.")
    return lines[-1].split()[0]


def append_to_log(cfg: dict, config_dir: Path, invoice_number: str, date_str: str) -> None:
    lf = log_path(cfg, config_dir)
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


# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(pdf_path: Path, cfg: dict, config_dir: Path,
              invoice_number: str, invoice_date: str, qty_list: list) -> None:

    PAGE_W, PAGE_H = A4
    MARGIN_L  = 20 * mm
    MARGIN_R  = 20 * mm
    MARGIN_T  = 18 * mm
    MARGIN_B  = 18 * mm
    CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
    LEFT_W    = 55 * mm
    RIGHT_W   = CONTENT_W - LEFT_W

    # ── Styles ────────────────────────────────────────────────────────────────

    def style(name, font="Helvetica", size=9, leading=13, align=TA_LEFT,
              textColor=BLACK, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=font, fontSize=size,
                              leading=leading, alignment=align,
                              textColor=textColor, spaceBefore=spaceBefore,
                              spaceAfter=spaceAfter)

    s_normal     = style("normal")
    s_bold       = style("bold",    font="Helvetica-Bold")
    s_invoice    = style("invoice", font="Helvetica", size=26, leading=30)
    s_entity     = style("entity",  font="Helvetica-Bold", size=11, leading=14)
    s_bank_label = style("blabel",  textColor=MID_GREY)
    s_bank_val   = style("bval")

    # ── Page template ─────────────────────────────────────────────────────────

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_L, PAGE_H - 38*mm, PAGE_W - MARGIN_R, PAGE_H - 38*mm)
        canvas.line(MARGIN_L, MARGIN_B + 4*mm, PAGE_W - MARGIN_R, MARGIN_B + 4*mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MID_GREY)
        canvas.drawRightString(PAGE_W - MARGIN_R, MARGIN_B - 2*mm, str(doc.page))
        canvas.restoreState()

    frame = Frame(MARGIN_L, MARGIN_B + 8*mm,
                  CONTENT_W, PAGE_H - MARGIN_T - MARGIN_B - 8*mm,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    doc = BaseDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
        topMargin=MARGIN_T, bottomMargin=MARGIN_B + 8*mm,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])

    # ── Unpack config ─────────────────────────────────────────────────────────

    client  = cfg["client"]
    you     = cfg["your_details"]
    bank    = cfg["bank"]
    entity  = cfg["entity"]

    logo_file    = config_dir / entity["logo_file"]
    header_name  = entity["name"]
    payment_days = cfg.get("payment_days", 0)
    due_date     = due_date_str(payment_days)
    terms_str    = cfg.get("terms", "Immediate")

    client_lines = [f"Attention: {client['name']}", client["company"]] + client["address"]
    your_lines   = [you["phone"], you["email"]] + you["address"]

    # ── Story ─────────────────────────────────────────────────────────────────

    story = []

    # Header — logo + entity name
    logo_h = 18 * mm
    logo   = Image(str(logo_file), width=logo_h, height=logo_h)
    header = Table([[logo, Paragraph(header_name, s_entity)]],
                   colWidths=[logo_h + 4*mm, CONTENT_W - logo_h - 4*mm])
    header.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 14*mm))

    # INVOICE heading + addresses
    your_contact = (
        [Paragraph(your_lines[0], s_bold), Paragraph(your_lines[1], s_bold), Spacer(1, 4*mm)]
        + [Paragraph(l, s_bold) for l in your_lines[2:]]
    )
    left_col = [Paragraph("INVOICE", s_invoice), Spacer(1, 4*mm)] + your_contact

    two_col = Table([[left_col, [Paragraph(l, s_normal) for l in client_lines]]],
                    colWidths=[LEFT_W, RIGHT_W])
    two_col.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(two_col)
    story.append(Spacer(1, 6*mm))

    # Meta — date, due date, invoice number, terms, re
    LABEL_W = 32 * mm
    VALUE_W = RIGHT_W - LABEL_W

    meta = Table(
        [
            ["", Paragraph("Date:",           s_normal), Paragraph(invoice_date,   s_normal)],
            ["", Paragraph("Due Date:",        s_normal), Paragraph(due_date,       s_normal)],
            ["", Paragraph("Invoice Number:", s_normal), Paragraph(invoice_number, s_normal)],
            ["", Paragraph("Terms:",          s_normal), Paragraph(terms_str,      s_normal)],
            ["", Paragraph(f"Re. {cfg['re_line']}", s_normal), ""],
        ],
        colWidths=[LEFT_W, LABEL_W, VALUE_W]
    )
    meta.setStyle(TableStyle([
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1),
        ("SPAN",         (1, 4), (2, 4)),
    ]))
    story.append(meta)
    story.append(Spacer(1, 6*mm))

    # Line items — apply qty_list positionally to scaleable items
    items         = cfg["line_items"]
    subtotal      = 0.0
    data_rows     = []
    scaleable_idx = 0
    n_scaleable   = sum(1 for i in items if i.get("scaleable", True))

    if len(qty_list) > n_scaleable:
        print(f"Warning: {len(qty_list)} quantities supplied but only {n_scaleable} scaleable item(s). Extra values ignored.")

    for item in items:
        if item.get("scaleable", True):
            item_qty      = qty_list[scaleable_idx] if scaleable_idx < len(qty_list) else 1
            scaleable_idx += 1
        else:
            item_qty = 1
        unit_price = item["unit_price"]
        cost       = unit_price * item_qty
        subtotal  += cost
        data_rows.append([
            Paragraph(item["description"], s_normal),
            Paragraph(str(item_qty),       s_normal),
            Paragraph(f"£{unit_price:.2f}", s_normal),
            Paragraph(f"£{cost:.2f}",       s_normal),
        ])

    total = subtotal

    table_data = (
        [[Paragraph(h, s_bold) for h in ["Description", "Quantity", "Unit Price", "Cost"]]]
        + data_rows
        + [["", "", Paragraph("Subtotal", s_normal), Paragraph(f"£{subtotal:.2f}", s_normal)]]
        + [["", "", Paragraph("Total",    s_bold),   Paragraph(f"£{total:.2f}",    s_bold)]]
    )

    DESC_W  = RIGHT_W * 0.50
    QTY_W   = RIGHT_W * 0.15
    PRICE_W = RIGHT_W * 0.175
    COST_W  = RIGHT_W * 0.175
    col_w   = [DESC_W, QTY_W, PRICE_W, COST_W]

    offset_data = [[""] + row for row in table_data]
    last_row    = len(table_data) - 1

    items_table = Table(offset_data, colWidths=[LEFT_W] + col_w, repeatRows=1)
    items_table.setStyle(TableStyle([
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
    story.append(items_table)
    story.append(Spacer(1, 8*mm))

    # Payment details
    story.append(Paragraph('<u>Payment To:</u>', s_normal))
    story.append(Spacer(1, 3*mm))

    offset_bank = [
        ["", Paragraph("Account Name:", s_bank_label), Paragraph(bank["account_name"], s_bank_val)],
        ["", Paragraph("Sort Code:",    s_bank_label), Paragraph(bank["sort_code"],    s_bank_val)],
        ["", Paragraph("Account:",      s_bank_label), Paragraph(str(bank["account"]), s_bank_val)],
        ["", Paragraph("Bank:",         s_bank_label), Paragraph(bank["bank"],         s_bank_val)],
    ]
    bank_table = Table(offset_bank, colWidths=[LEFT_W, 35*mm, RIGHT_W - 35*mm])
    bank_table.setStyle(TableStyle([
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(bank_table)

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
                        help="Client YAML config filename (in Config/), e.g. sample_client.yaml")
    parser.add_argument("-qt", "--quantity", type=int, nargs="+", default=None,
                        help="Quantity per scaleable line item (space-separated). Fewer values than items defaults remainder to 1.")
    args = parser.parse_args()

    settings                              = load_settings()
    _, config_dir, generated_dir, clients_dir = get_paths(settings)

    config_path = clients_dir / args.config
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    cfg = load_config(config_path)

    logo_file = config_dir / cfg["entity"]["logo_file"]
    if not logo_file.exists():
        print(f"Logo not found: {logo_file}")
        sys.exit(1)

    lf = log_path(cfg, config_dir)
    if not lf.exists():
        print(f"Log file not found: {lf}")
        print(f"Create it with a seed entry, e.g.: INV0000  2026-01-01 (the first generated invoice will be INV0001)")
        sys.exit(1)

    # Resolve quantity list: CLI > manifest/YAML default > 1
    if args.quantity is not None:
        qty_list = args.quantity
    else:
        raw = cfg.get("default_qty", 1)
        qty_list = raw if isinstance(raw, list) else [raw]
    current     = last_invoice_number(cfg, config_dir)
    new_invoice = next_invoice_number(current)
    new_date    = today_str()
    pdf_out     = generated_dir / f"{new_invoice}.pdf"

    print(f"Generating {new_invoice} dated {new_date} (qty: {qty_list})...")

    build_pdf(pdf_out, cfg, config_dir, new_invoice, new_date, qty_list)

    append_to_log(cfg, config_dir, new_invoice, datetime.today().strftime("%Y-%m-%d"))

    print(f"PDF saved: {pdf_out}")
    send_notification(new_invoice, pdf_out)


if __name__ == "__main__":
    main()
