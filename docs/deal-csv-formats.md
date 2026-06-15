# Supported Deal CSV Formats

Manager OS accepts two deal CSV formats: **normalized** (internal) and **NetSuite** (export from NetSuite CRM).

---

## 1. Normalized Deals CSV

The original internal format. Use this when creating your own pipeline tracking spreadsheet.

**Required columns:**

| Column | Description |
|--------|-------------|
| `account` | Client or prospect name |
| `deal_name` | Short deal identifier |

**Optional columns:**

| Column | Description |
|--------|-------------|
| `stage` | Deal stage (e.g. Discovery, Proposal, SOW Review) |
| `close_date` | Expected close date (ISO 8601: `YYYY-MM-DD`) |
| `technical_owner` | Assigned engineer or technical lead |
| `ae_name` | Account Executive / ECA |
| `loe_status` | LOE status (`signed`, `in-review`, `not-started`) |
| `sow_status` | SOW status (`signed`, `pending`, `not-started`) |
| `blockers` | Free-text blockers |
| `next_action` | Next action item |
| `probability` | Win probability (`0`–`1` or `0%`–`100%`) |

**Example:**

```csv
account,deal_name,stage,close_date,technical_owner,ae_name,loe_status,sow_status
Acme Corp,ACME ML Platform,Proposal,2026-07-15,Alice Chen,Bob Kim,in-review,not-started
```

**Validation notes:**
- `account` is validated against `config/clients.yaml` if that config is present.
- Missing `sow_status` or `loe_status` with a close date approaching within 14 days produces a warning.
- Missing `technical_owner` on any row produces a warning.

---

## 2. NetSuite Deals CSV

Manager OS auto-detects NetSuite format by looking for the columns `NetSuite Opportunity ID` and `NetSuite Customer`.

**Detection:** If both columns exist, the file is treated as NetSuite format.

**Column mapping:**

| NetSuite Column | Internal Name | Notes |
|----------------|---------------|-------|
| `NetSuite Opportunity ID` | `deal_id` | Required |
| `NetSuite Customer` | `account` | Required; see note below |
| `NetSuite Opportunity Status` | `stage` | |
| `NetSuite Expected Close Date` | `close_date` | Accepts "Jun 19, 2026" style |
| `NetSuite Forecast Category` | `forecast_category` | |
| `NetSuite Probability (%)` | `probability` | Accepts decimal or percent |
| `NetSuite Services ($)` | `services_amount` | Accepts `$213,960` format |
| `NetSuite Last Status Changed Date` | `last_status_changed_date` | |
| `NetSuite Delivery Comment` | `delivery_comment` | |
| `NetSuite Next Steps` | `next_steps` | |

**Deal name derivation:**

NetSuite exports do not include a clean deal name column. Manager OS derives `deal_name` automatically:

```
deal_name = NetSuite Customer + " - " + NetSuite Opportunity ID
```

Examples:
- `MTY Franchising Inc. - OPP025010`
- `Broad Institute of MIT and Harvard - OPP032788`
- `Strategic Education, Inc - OPP033536`

If a future NetSuite export includes an `Opportunity Name` column, it will be preferred as `deal_name` and the derived name used as fallback.

**Important: NetSuite Customer ≠ signed client**

`NetSuite Customer` values are **prospects or opportunities**, not necessarily signed clients. They are stored as `account` in the deals table but are **not validated against `config/clients.yaml`** by default.

`config/clients.yaml` should contain **signed, current, and recent delivery clients only**.

If a deal eventually closes and becomes a signed engagement, add the client to `clients.yaml` at that time.

**Optional fields:**

`technical_owner`, `ae_name`, `loe_status`, `sow_status`, and `staffing_feasibility` are not present in a standard NetSuite export and are optional. They will not produce warnings if absent.

**Validation for NetSuite format:**

| Condition | Severity |
|-----------|----------|
| Malformed `close_date` | Warning |
| Malformed `probability` | Warning |
| Malformed `services_amount` | Warning |
| `close_date` within 14 days | Warning |
| `last_status_changed_date` >30 days ago | Warning |
| Blank `next_steps` | Info (not blocking) |

**Probability parsing:**

Manager OS normalizes probability values to a 0–1 decimal fraction:

| Input | Parsed as |
|-------|-----------|
| `0.65` | `0.65` |
| `0.9` | `0.9` |
| `75%` | `0.75` |
| `75.00%` | `0.75` |
| `65` *(integer assumed percent)* | `0.65` |

**Services amount parsing:**

| Input | Parsed as |
|-------|-----------|
| `213960` | `213960.0` |
| `$213,960` | `213960.0` |
| *(blank)* | `null` |

---

## Running `profile-deals`

```bash
# Validate and preview a deals CSV before ingesting
manager-os profile-deals --path path/to/deals.csv

# JSON output (for scripting)
manager-os profile-deals --path path/to/deals.csv --json
```

The output shows:
- Detected format (`normalized` or `netsuite`)
- Required fields found / derived
- Optional fields present
- Per-row issues (warnings and info notes)
- NetSuite summary: derived deal names, stale records, missing next steps

When no blocking issues exist, the command prints:
```
✓  No issues. Safe to run manager-os ingest.
```

---

## Ingest

```bash
manager-os ingest --source deals
```

After ingestion, all NetSuite fields are stored in the `deals` table including:
`deal_id`, `delivery_comment`, `next_steps`, `forecast_category`, `probability`,
`services_amount`, `last_status_changed_date`, and `source_format = "netsuite"`.
