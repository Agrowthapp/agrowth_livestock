# Agrowth Livestock - App Frappe

Módulo ganadero para ERPNext: compras, ventas, stock y trazabilidad de ganado.

## Installation

### Requirements

- ERPNext v15+
- Frappe Bench installed

### Steps

1. **Copy app to bench:**

   ```bash
   # On your ERPNext server
   cd /home/frappe/frappe-bench/apps
   git clone <repo-url> agrowth_livestock
   ```

2. **Install dependencies:**

   ```bash
   cd /home/frappe/frappe-bench
   bench get-app agrowth_livestock
   ```

3. **Install on site:**

   ```bash
   bench --site [your_site] install-app agrowth_livestock
   ```

4. **Migrate:**

   ```bash
   bench --site [your_site] migrate
   ```

## Initial Setup

### 1. Create Animal Items

In ERPNext, create Items with `Item Group = Animales`:

- Bovino - Ternero
- Bovino - Vaquillona
- Bovino - Novillo
- Bovino - Vaca

Set the `Tax Category` with the corresponding VAT rate.

### 2. Create Warehouses

Create Warehouses for livestock:

- Depot Principal
- Corral A
- Corral B

### 3. Create Withholding Profiles

Go to: **Configuration > Withholding Profile**

Create profiles for different provinces with their IIBB/IIGG rules:

- **Profile Name**: e.g., "IIBB - Córdoba"
- **Province**: Select province
- **Counterparty Type**: Supplier / Customer / Both
- **Active**: Checked

Add Rules:
- **Type**: IIBB, IIGG, Sellos, Comisión
- **Rate (%)**: Percentage to withhold
- **Fixed Amount**: Or fixed amount instead of rate
- **Min Base**: Minimum base amount to apply
- **Effective From/To**: Validity period

### 4. Configure Withholding Accounts

In ERPNext, ensure you have the following accounts configured:
- Retención IIBB - AC
- Retención Ganancias - AC
- Retención Sellos - AC

## Usage

### Create Purchase Settlement (Liquidación de Compra)

1. Go to: **Livestock > Purchase Settlement**
2. Fill in:
   - Company
   - Supplier
   - Date
   - Document Number
   - Warehouse (destination)
   - Province
3. Add lines with animals:
   - Item
   - Quantity (heads)
   - Average weight (if price per kg)
   - Unit price
4. **Submit**

On submit, automatically generates:
- Purchase Invoice (draft)
- Herd Batch (flock/herd)
- Stock Entry (draft)

### Create Dispatch (Venta Ganadera)

1. Go to: **Livestock > Dispatch**
2. Choose mode:
   - **Full Batch**: Select a complete herd batch to sell
   - **Mixed**: Manually select animals from multiple batches
3. Fill in:
   - Company
   - Customer
   - Date
   - Warehouse (origin)
   - Province
4. **Submit**

On submit, automatically generates:
- Sales Invoice (draft)
- Stock Entry (Material Issue) (draft)
- Updates herd batch status (Sold or deducts quantities)

### View Herds

Go to: **Livestock > Herd Batch**

Shows all active herds with their status.

### Create Reclassification (Reclasificación)

Used to change animal category (e.g., ternero → novillo):

1. Go to: **Livestock > Reclassification**
2. Fill in:
   - Company
   - Date
   - Herd Batch (origin)
   - From Item (e.g., Bovino - Ternero)
   - To Item (e.g., Bovino - Novillo)
   - Quantity
   - Reason (optional)
3. **Submit**

On submit, automatically generates:
- Stock Entry (Repack) - issues from_item, receipts to_item
- Updates herd batch lines

### Individual Animal Tracking (Caravanas)

For operations requiring individual animal tracking:

1. **Create Animal** (for each head):
   - Go to: **Livestock > Animal**
   - Fill in:
     - Ear Tag ID (caravan number) - unique
     - Species
     - Sex
     - Category
     - Herd Batch
     - Warehouse
     - Origin (Purchase, Birth, Transfer)
   - **Submit**

   On submit, automatically creates:
   - Serial No in ERPNext linked to the animal

2. **Record Events**:
   - Go to: **Livestock > Animal Event**
   - Event types: Weighing, Health, Movement, Death, Category Change
   - Events update the animal's current state

| DocTypes Structure

| DocType | Description |
|---------|-------------|
| `Herd Batch` | Herd/flock of animals |
| `Herd Batch Line` | Herd line item |
| `Livestock Settlement` | Purchase settlement |
| `Livestock Settlement Line` | Settlement line item |
| `Livestock Dispatch` | Sales dispatch |
| `Livestock Dispatch Line` | Dispatch line item |
| `Livestock Reclassification` | Category change (ternero→novillo) |
| `Animal` | Individual animal with ear tag (caravana) |
| `Animal Event` | Event: weighing, health, movement, death |
| `Withholding Profile` | Withholding profile |
| `Withholding Rule` | Withholding rule |

## Workflows

### Purchase Flow

```
Livestock Settlement (Submit)
    │
    ├─► Purchase Invoice (draft)
    ├─► Herd Batch (Active)
    └─► Stock Entry - Material Receipt (draft)
```

### Sale Flow (Full Batch)

```
Livestock Dispatch - Full Batch (Submit)
    │
    ├─► Sales Invoice (draft)
    ├─► Stock Entry - Material Issue (draft)
    └─► Herd Batch (status = Sold)
```

### Sale Flow (Mixed)

```
Livestock Dispatch - Mixed (Submit)
    │
    ├─► Sales Invoice (draft)
    ├─► Stock Entry - Material Issue (draft)
    └─► Herd Batch (deduct qty from multiple batches)
```

### Reclassification Flow

```
Livestock Reclassification (Submit)
    │
    ├─► Stock Entry - Repack (draft)
    │      ├─► Issue: from_item (ternero)
    │      └─► Receipt: to_item (novillo)
    └─► Herd Batch
           ├─► Deduct from_item qty
           └─► Add to_item qty
```

## Development

### Regenerate DocTypes

If you modify the JSON files:

```bash
bench --site [your_site] migrate
```

### Logs

Logs are located at:
```
/home/frappe/frappe-bench/logs/
```

## Implementation Status

| Stage | Feature | Status |
|-------|---------|--------|
| 1 | Purchase Settlement + Herd Batch | ✅ Done |
| 2 | Dispatch (Full/Mixed) | ✅ Done |
| 3 | Reclassification | ✅ Done |
| 4 | Animal + Serial No (Caravanas) | ✅ Done |
| 5 | Withholdings IIBB/IIGG | ✅ Done |

---

**Version:** 0.0.2
**Author:** Agrowth
**License:** MIT
