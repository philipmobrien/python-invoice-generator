# python-invoice-generator

A config-driven PDF invoice generator for freelancers and small businesses. Generates clean, professional invoices directly from Python using ReportLab — no Word, Pages, or LibreOffice required.

Supports multiple clients, multiple trading entities (each with their own logo and branding), recurring monthly automation via launchd, and flexible per-line-item quantity control.

---

## Platform

**macOS only.** The invoice generation itself is pure Python and cross-platform, but the scheduling (launchd) and desktop notifications (AppleScript) are macOS-specific. Forks adapting the automation layer for Linux (systemd) or Windows (Task Scheduler) are welcome.

---

## Features

- PDF generation via ReportLab — no external applications involved
- One YAML config per client — all client, entity, and payment details in one place
- Multiple trading entities — different logo, name, and email per client config
- Monthly automation via a single launchd job and a manifest file
- Per-line-item quantity control, with scaleable and fixed items
- Append-only invoice log per client — full history, human-readable
- Due date calculated automatically from configurable payment terms
- `auto_send` flag in client config — hook ready for future email automation

---

## Requirements

Python 3.9+ and two packages:

```bash
pip3 install reportlab pyyaml --break-system-packages
```

---

## Folder structure

```
Invoices/
├── Config/                                       — scripts, settings, logs, logos
│   ├── generate_invoice.py                       — invoice generator
│   ├── run_monthly_invoices.py                   — monthly batch runner
│   ├── settings.yaml                             — local path config (not committed)
│   ├── monthly_manifest.yaml                     — list of active monthly clients
│   ├── your_logo.png                             — logo file(s) referenced by client configs
│   ├── client_invoice_log.txt                    — one log file per client
│   ├── pas-invoice.log → ~/Library/Logs/...      — optional symlink to launchd output log
│   └── pas-invoice-error.log → ~/Library/Logs/… — optional symlink to launchd error log
├── Clients/                                      — one YAML file per client
│   ├── sample_client.yaml                        — template for new clients
│   └── your_client.yaml
├── Generated/                                    — PDF output
└── Archive/                                      — manually move sent invoices here
```

---

## Setup

### 1. Clone the repo and install dependencies

```bash
git clone https://github.com/yourusername/python-invoice-generator.git
pip3 install reportlab pyyaml --break-system-packages
```

### 2. Create settings.yaml

Copy and edit — this file is gitignored and holds your local path:

```yaml
base_dir: ~/path/to/your/Invoices
config_subdir:    Config
clients_subdir:   Clients
generated_subdir: Generated
```

### 3. Create your client config

Copy `sample_client.yaml` from `Clients/` to `Clients/your_client.yaml` and fill in your details. See [Client config](#client-config) below.

### 4. Create the invoice log

Create an empty log file for each client with a seed entry — the script reads this to determine the next invoice number:

```
# Client name invoice log
# Format: INVOICE_NUMBER  DATE
INV0000  2026-01-01
```

The seed entry is the last invoice you raised manually, or `INV0000` if this is a new client — the script will generate INV0001 on the first run.

### 5. Add your logo

Place your logo file(s) in `Config/` and reference them by filename in the client YAML under `entity.logo_file`.

---

## Running manually

```bash
cd /path/to/Invoices/Config
python3 generate_invoice.py -c your_client.yaml
```

With quantity override (e.g. two scaleable items at 2 and 3 units respectively):

```bash
python3 generate_invoice.py -c your_client.yaml -qt 2 3
```

---

## Monthly automation (macOS)

A sample plist is provided in `Config/net.example.monthly-invoices.plist`. Before using it:

1. Edit the path to `run_monthly_invoices.py` — this must be the full absolute path to the script in your `Config/` folder
2. Edit the `StandardOutPath` and `StandardErrorPath` log paths, replacing `yourusername` with your macOS username
3. Rename it — replace `example` with something meaningful, e.g. `net.yourname.monthly-invoices.plist`

Then copy it to `~/Library/LaunchAgents/` and load it:

```bash
cp Config/net.yourname.monthly-invoices.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/net.yourname.monthly-invoices.plist
```

The job runs `run_monthly_invoices.py` at 09:00 on the 1st of each month, processing all clients listed in `monthly_manifest.yaml`.

**If the Mac is off on the 1st**, run the batch manually:

```bash
python3 run_monthly_invoices.py
```

**To reload after editing the plist:**

```bash
launchctl unload ~/Library/LaunchAgents/net.example.monthly-invoices.plist
cp Config/net.example.monthly-invoices.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/net.example.monthly-invoices.plist
```

**To disable temporarily:**

```bash
launchctl unload ~/Library/LaunchAgents/net.example.monthly-invoices.plist
```

Logs are written to `~/Library/Logs/` by launchd. Optionally symlink them into `Config/` for convenience:

```bash
ln -s ~/Library/Logs/pas-invoice.log Config/pas-invoice.log
ln -s ~/Library/Logs/pas-invoice-error.log Config/pas-invoice-error.log
```

---

## Client config

All client, entity, and payment details live in a single YAML file. Copy `sample_client.yaml` as your starting point.

```yaml
# Trading entity — logo and name shown on the invoice header
entity:
  name: Your Trading Name
  logo_file: your_logo.png        # filename only; file must be in Config/

# Invoice sequence
log_file: client_invoice_log.txt  # create with seed entry before first run
default_qty: 1                    # default quantity for scaleable items
                                  # can be a list for multiple scaleable items: [2, 1, 3]
auto_send: false                  # reserved for future automatic email sending
payment_days: 0                   # 0 = immediate; 30 = net-30 etc.
terms: Immediate                  # free-text terms line printed on the invoice
re_line: Services rendered        # Re. line on the invoice

your_details:
  phone: "+44 7000 000000"
  email: you@example.com
  address:
    - 1 Example Street
    - Your Town
    - AB1 2CD

client:
  name: Client Contact Name
  company: Client Company Ltd
  address:
    - 1 Client Street
    - Client Town
    - CD3 4EF

bank:
  account_name: Your Account Name
  sort_code: 00-00-00        # or BIC/SWIFT for international payments
  account: "00000000"        # or IBAN
  bank: Your Bank

line_items:
  - description: Your service description
    unit_price: 0.00
    scaleable: true               # quantity multiplied by -qt / manifest qty
  # - description: Fixed annual charge
  #   unit_price: 0.00
  #   scaleable: false            # always qty 1 regardless of -qt
```

---

## Quantity control

Quantity applies **positionally to scaleable items only**. Non-scaleable items are always qty 1.

| Scenario | How to set |
|---|---|
| Single scaleable item, default qty | `default_qty: 1` in YAML |
| Single scaleable item, override | `-qt 2` on CLI, or `qty: 2` in manifest |
| Multiple scaleable items | `-qt 2 3 1` on CLI, `qty: [2, 3, 1]` in manifest, or `default_qty: [2, 3, 1]` in YAML |
| Fewer values than scaleable items | Remaining items default to 1 |
| More values than scaleable items | Excess values ignored with a warning |

**Precedence:** CLI `-qt` → manifest `qty` → YAML `default_qty` → 1

---

## Monthly manifest

`Config/monthly_manifest.yaml` lists which clients run automatically each month:

```yaml
clients:
  - config: client_a.yaml
    qty: 1
  - config: client_b.yaml
    qty: 2
  - config: client_c.yaml
    qty:
      - 2
      - 1
  # - config: suspended_client.yaml  ← comment out to suspend without deleting
  #   qty: 1
```

---

## Adding a new client

1. Copy `Clients/sample_client.yaml` to `Clients/newclient.yaml` and fill in all fields
2. Place the entity logo in `Config/`
3. Create the invoice log in `Config/` with a seed entry
4. Add the client to `Config/monthly_manifest.yaml` if they're a recurring monthly client
5. Test with a manual run: `python3 generate_invoice.py -c newclient.yaml`

---

## Adding a second trading entity

Each client config specifies its own `entity.name`, `entity.logo_file`, and `your_details.email`, so a second trading entity is simply a different set of values in those fields. No script changes needed.

---

## Invoice log format

```
# Client invoice log
# Format: INVOICE_NUMBER  DATE
INV0000  2026-01-01  ← seed entry; first generated invoice will be INV0001
INV0001  2026-02-01
```

Lines beginning with `#` are ignored. The script reads only the last non-comment line to determine the next number. To correct the sequence or backfill historical invoices, edit the file directly.

---

## Archiving

Once you've confirmed a PDF has been sent, move it manually from `Generated/` to `Archive/`.

---

## Contributing

Bug fixes and improvements welcome via pull request. For platform ports (Linux, Windows), please fork rather than adding platform-specific code to the main branch — the goal is to keep the macOS version clean and maintainable.

---

## Licence

MIT