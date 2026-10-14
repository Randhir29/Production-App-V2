# agent.md — ProDash Backend (FastAPI re-platform of the CodeIgniter 4 application)

> **Who this file is for:** every human or AI agent that writes, reviews or tests code for the ProDash backend.
> It is the single source of truth for *what the system does* (business rules), *how the new backend is structured*
> (architecture), and *how work is done* (conventions, tests, definition of done).
> **Read sections 1–5 before writing any code. Never change a rule in §5 without an explicit ticket from the product owner.**

---

## 0. How to use this file

| You are asked to… | Read first |
|---|---|
| Implement an endpoint / feature | §4 (data), §5 (rules for that feature), §6 (API catalogue), §7 (architecture), §8 (conventions) |
| Write SQL against a plant database | §4.2, §5.1 (constants), §8.4 (SQL rules) |
| Touch authentication | §7.8 |
| Write tests | §9 |
| Decide whether to "fix" odd legacy behaviour | §11 (legacy defects register) — default is **preserve** |
| Plan analytics / schema work | §12 |

**Prompt recipe for future tasks** (copy this into new agent prompts):

```
Context: follow agent.md (ProDash backend). Task: <feature / endpoint id from §6>.
Constraints: keep business logic identical to §5.<n>; layering per §7.3; SQL rules §8.4;
PHP numeric compatibility §8.3; add unit + parity tests per §9; update §6/§11 if anything changes.
Deliver: code + tests + short note of any deviation, referencing defect ids (D-xx).
```

---

## 1. Application summary

**ProDash — "Production Data Analytics"** is a monitoring and reporting backend for **LPG cylinder bottling plants**
(about 55 plants, organised by zone). Each plant runs one to three **filling carousels** (rotary filling machines with 12–72
**filling heads / scales**). The carousel control system (a Kosan Crisplant–style PLC/SCADA, “kosan” in the legacy code) writes every
cylinder event into a **PostgreSQL database at each plant**.

ProDash connects **live to each plant's PostgreSQL database**, using connection details stored in a central database, and computes:

* **Dashboard KPIs:** production, productivity (cylinders/hour), check-scale (CS) rejection, gas-leak-detector (GD/ELD) rejection, pressure/O-ring test (PT/OLD/ORT) rejection, plus day-over-day change.
* **Charts:** hourly production, filling-accuracy distribution, CS rejection breakdown, GD/PT rejection rates, per-head GD/PT drill-down, productivity moving average.
* **Scale analytics:** under-performing scales (efficiency against the rated output) and inaccurate scales (share of fills off by more than 100 g).
* **Tables:** periodic production (configurable interval), per-scale efficiency, per-scale filling statistics (variation histogram, mean, std-dev), accuracy trend.
* **Performance reports:** V1 (time range) and V2 (date range, split into **normal / break / overtime** phases using shift and break configuration, stoppage-gap analysis, non-operating days).
* **Mailer feed:** a flattened, per-carousel performance row set used by an external e-mail/report job, plus a zone map.
* **Plant connectivity probe:** whether each plant database is reachable, and its latency.

There is no transactional write path to the plant databases. The application is **read-only analytics**, apart from legacy admin and debug
utilities, which are listed in §11 and §13.

---

## 2. As-is system (CodeIgniter 4) — what exists today

```
                ┌───────────────────────────── Vue 3 SPA (Vite bundle, served at /prodash) ─────────────────────────────┐
                │  polls GET /prodash/<endpoint>/<plant>/<from>/<to> … and POSTs multipart forms for tables/reports  │
                └───────────────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                                                │ HTTP (no auth enforced for prodash routes)
┌───────────────────────────────────────────────────────────────▼──────────────────────────────────────────────────────┐
│ CodeIgniter 4 app                                                                                                     │
│  Controllers: ProDash (extends ProdashTest = all logic), MailerController, ConfigController, Router, Home             │
│  Models:      PlantsModel, CarousalsModel, BreaksModel (central MySQL)                                                │
│               Kosan\ProductionLogModel (≈4.3k lines: every plant-DB SQL + calculations)                               │
│  Config:      Config\Secure (static plant list, carousal config, speeds, device-id plants)                             │
│               Config\Database (central MySQL + one static Postgre group per plant – legacy)                           │
│  Auth:        myth/auth (users, groups) – present but NOT enforced on prodash/mailer routes                           │
└───────────────┬───────────────────────────────────────────────────────┬──────────────────────────────────────────────┘
                │ MySQL  (db "prodash")                                  │ PostgreSQL ×N  (db "ccc" per plant, port 5432)
     ┌──────────▼──────────┐                                 ┌──────────▼──────────────────────────────┐
     │ plants, carousals,  │  plants.host_ip/db_user/... ──► │ production_log  (filling / check-scale)  │
     │ breaks, options,    │      used to open per-plant     │ event_log       (GD / PT leak tests)     │
     │ users + auth_* …    │      connection on each request │                                          │
     └─────────────────────┘                                 └──────────────────────────────────────────┘
```

Key facts:

* **Routing.** Explicit routes live in `app/Config/Routes.php`, and `setAutoRoute(true)` is on. In addition, `GET /(:alphanum)` maps any single path segment to a `ProDash::<method>`. As a result, many debug and test methods, **including destructive ones**, can be reached without authentication (D-08).
* **Per-request plant connection.** `ProductionLogModel::setPlant($shortName)` reads `plants` from the central DB and builds a PostgreSQL connection config with `hostname=host_ip`, `username=db_user`, `password=db_password`, `database=db_database` and `port=5432`.
* **There are two sources of carousel configuration** (D-11):
  1. The static `Config\Secure::$carousalConfig` (heads and `stdOutput`, the rated output in cylinders per hour) is used by the **scale efficiency** features.
  2. The DB tables `carousals` and `breaks` (heads, rated_productivity, start/stop times, breaks) are used by **performance reports** (`getCarousals()`, phases).
* **Timezone:** `Asia/Kolkata`. Plant timestamps are naive local times.
* The frontend bundle calls these endpoints: getPlants, getPlants2, getConnectivityStatus, getDeviceName, the 5 KPI stats, getHourlyProductionData, getFillingAccuracyData, getCSRejectionData, getGDPTData, getGDDrillDownData, getPTDrillDownData, getUnderPerformingScales, getInaccurateScales, productivityMovingAverage, getHourlyProductionTableData, getScaleEfficiencyDetailsTableData2, getFillingStatistics, getPerformanceReport and getPerformanceReportV2. The external mailer job calls `POST /performance-report` and `GET /zone-maps`.

---

## 3. Domain glossary

| Term (legacy spelling) | Meaning |
|---|---|
| Plant / `short_name` | Bottling plant id, lower-case slug (`anantapur`, `jabalpur1`…). Primary key used in every API call. |
| Zone | Regional grouping of plants (central DB `plants.zone`). |
| Carousel / **carousal** / `system_id` | One rotary filling line inside a plant (1, 2, sometimes 3). `system_id` in plant tables. |
| Filling head / scale / FM | One filling machine position on the carousel. Identified by `machine_id` **or** `device_id` (see `plantsWithDeviceId`, §5.1). |
| `heads` | Number of filling heads on a carousel (12, 24, 48, 72). |
| `stdOutput` / `rated_productivity` / AOP | Rated output of a carousel in **cylinders per hour**. |
| `carousalSpeed` | Fallback cycle time in **seconds per cylinder per head**: `{1: 50, 2: 48}`. |
| Cylinder type `cyl_type` | `1` = 14.2 kg domestic cylinder (nominal net 14 200 g); `2` = 19 kg commercial cylinder (nominal net 19 000 g). |
| 19 kg equivalence | In productivity maths a 19 kg cylinder counts as **1.25** cylinders. |
| `check_net` | Net weight measured by the check scale, in **grams**. Variation = `check_net − nominal`. |
| Check scale (CS) | Weighing station after filling. Rejects ("sort-outs") under- and over-filled cylinders, tare errors, time-outs and so on. Rows are in `production_log`. |
| GD / ELD | Gas (leak) Detector, the Electronic Leak Detection station. Rows are in `event_log`, with `process_id` 3 or 23. |
| PT / OLD / ORT | Pressure / O-ring leak test (labelled "OLD" or "ORT" in the UI). Rows are in `event_log`, with `process_id` 4 or 24. |
| `process_id` | Station or event type: 1 = (operation event, used only for non-operating-day detection), 2 / 22 = check-scale weighing (two variants or lines), 3 / 23 = GD, 4 / 24 = PT. |
| `process_status` | Result code. It looks like a bitmask (see §12.3). 0 = OK. |
| Sort-out | Cylinder rejected by a station. |
| Phases | For each carousel and day: **normal** = working windows between shift start/end minus breaks; **break** = configured breaks; **overtime** = 00:00→start and end→23:59:59.999. |
| Gap / stoppage / interruption | Time between consecutive cylinders above a threshold (60 s in reports; 5 min for the KPI productivity tile). |
| Non-operating day | A calendar day with zero qualifying rows for a carousel. |
| Productivity adjustment factor | `8.5 / 7.75`. A shift-normalisation factor applied to KPI productivity and to the moving average. |

---

## 4. Data sources and schemas

### 4.1 Central database ("prodash"): owned by this application and migrated with Alembic

Today it is MySQL. The target is PostgreSQL; the MySQL driver `mysql+aiomysql` is kept for the transition and for the data-copy job.

| Table | Columns (inferred from code) | Used by |
|---|---|---|
| `plants` | `id` PK AI, `erp_id`, `short_name`, `plant_name`, `zone`, `host_ip`, `db_user`, `db_password` (**plaintext today**), `db_database`, `use_device_id` (bool, **not read by code**; the static list is used instead) | plant registry, plant DB connection, zone map |
| `carousals` | `id` PK AI, `plant_id` → plants.id, `carousal_id` (1..n), `heads`, `rated_productivity`, `start_time` TIME, `stop_time` TIME. The config-copy job also tried to write `short_name`, but the model's allowedFields discarded it. | `getCarousalConfig`, reports, phases |
| `breaks` | `id` PK AI, `plant_id`, `carousal_id`, `start_time` TIME, `stop_time` TIME | phases (break windows) |
| `options` | `name` PK, `value`, `created_at`/`updated_at`/`deleted_at` (int epochs, soft delete) | unused (keep model; do not build features on it) |
| myth/auth tables | `users` (id, email, username, password_hash, reset_*, activate_hash, status, status_message, active, force_pass_reset, permissions, deleted_at, created_at, updated_at, + custom `fullname`, `phone`), `auth_groups` (id, name, description), `auth_groups_users`, `auth_permissions`, `auth_groups_permissions`, `auth_users_permissions`, `auth_logins`, `auth_tokens`, `auth_reset_attempts`, `auth_activation_attempts` | legacy login (not enforced for prodash) |

The derived carousel configuration has this shape, and must be reproduced exactly by `CarouselConfigService.get_config(plant)`:

```json
{ "<carousal_id>": { "heads": int, "stdOutput": int,
                     "times": { "start": "HH:MM:SS"|null, "end": "HH:MM:SS"|null,
                                "breaks": [ {"from": "HH:MM:SS", "to": "HH:MM:SS"}, ... ] | null } } }
```

Rules:

* `breaks` is `null` when a carousel has no rows in the `breaks` table.
* Carousels are keyed by `carousal_id`, in table order.
* Breaks are kept in table order. The new code adds `ORDER BY id`.

### 4.2 Plant databases ("ccc" on each plant, PostgreSQL 5432): external and read-only, **never migrated by Alembic**

**`production_log`** (one row per check-scale weighing). The columns the code uses are:

* `production_log_id` (bigint, monotonic)
* `process_date` (timestamp without time zone, millisecond precision, local IST)
* `process_id` (int: 1, 2, 22)
* `process_status` (int; can be negative, which means a communication error)
* `system_id` (carousel)
* `machine_id` (scale index)
* `device_id` (scale or device id)
* `cyl_type` (1 or 2)
* `check_net` (int grams)

**`event_log`** (one row per GD or PT test). The columns the code uses are:

* `event_log_id`
* `process_date`
* `process_id` (3, 23, 4, 24)
* `process_status` (0 = OK)
* `system_id`
* `device_id` (head)

**Plant DB side-effects:** two PL/pgSQL functions are (re)created on the plant DB by report V2: `getGapBetweenTimes(text,text,text,text) → interval` and `getEndGapForPhase(time,text,text) → interval`. Their bodies are given verbatim in §5.7.4.

### 4.3 Static configuration: version-controlled YAML ported verbatim from `Config\Secure`

The file `config/static_plant_config.yaml` must contain the following keys:

* `plants`: map `short_name → display name`. 55 entries; `jabalpur1` and `jabalpur2` are separate plants.
* `plant_name`: `"Mysore LPG Plant"`, used by `getPlantName`.
* `carousal_speed`: `{1: 50, 2: 48}`.
* `interruption_to_exclude_minutes`: `5`.
* `plants_with_device_id`: anantapur, visakh, rajahmundry, chakan, cherlapalli, kondapalli, warangal, jodhpur, ajmer, jatni, bhatinda, bahadurgarh, hazarwadi, barhi, loni, champaran, patalganga, goa, usar, hoshiarpur, indore, gummidipoondi.
* `plants_with_48_head`: `{chakan: [1]}` (unused, but keep).
* `carousal_config`: per plant, then per carousel, `{heads, stdOutput, times?{start,end,breaks[]}}`. **Port it exactly, including PHP duplicate-key semantics, where the last key wins:** `kota → {1: heads 24, stdOutput 1800}` and `jammu → {1: 24/1630}`. `jodhpur` is present here but missing from `plants`.

Provide a loader `domain/static_config.py`. It builds a frozen Pydantic model cached with `functools.lru_cache`, and the YAML path comes from `Settings.STATIC_PLANT_CONFIG_PATH`. **Never hard-code these values in services.**

---

## 5. Business rules (MUST be preserved exactly)

All maths, filters, thresholds, rounding and output field names below come from the PHP code. Where the legacy behaviour is buggy, it is still
listed here, and the decision is recorded in §11.

### 5.1 Constants (`app/domain/constants.py`)

```python
TZ = "Asia/Kolkata"

CHECK_SCALE_PROCESS_IDS = (2, 22)        # most production queries
CHECK_SCALE_PRIMARY_ID   = (2,)          # only a few legacy V1 helpers
NON_OP_DAY_PROCESS_IDS   = (1, 2, 22)    # with process_status NOT IN (16)
GD_PROCESS_IDS = (3, 23)                 # "ELD"
PT_PROCESS_IDS = (4, 24)                 # "OLD"/"ORT"

NEGATIVE_TARE = (1296, 5392)
POSITIVE_TARE = (17424,)
TARE_EXCLUDED = NEGATIVE_TARE + POSITIVE_TARE          # excluded from production counts

CS_SORTOUT_STATUSES   = (1040, 2064, 1296, 17424, 1048, 4120, 5392)
CS_OTHER_ERR_STATUSES = (1041, 1042, 2192, 4112, 4113, 5136, 6160)
# commErrorSortout: process_status < 0 OR process_status == 4096
CS_UNDERFILLED, CS_OVERFILLED = 1040, 2064
CS_NTARE  = (1296, 5392);  CS_PTARE = (17424,);  CS_TIMEOUT = (1048, 4120)

FILL_ACCEPTED_STATUSES = (0, 1040, 2064)                # filling statistics / accuracy

NOMINAL_NET_G = {1: 14200, 2: 19000}
EQUIV_19KG = 1.25
PRODUCTIVITY_ADJUSTMENT_FACTOR = 8.5 / 7.75
MIN_INTERRUPTION_SECONDS = 60            # reports (gap threshold)
DEFAULT_CAROUSELS = (1, 2)               # dashboard endpoints are hard-wired to carousels 1 & 2 (D-10)
DEFAULT_CYL_TYPES = (1, 2)

GD_DRILL_STATUSES = {0: "ok", 272: "leak"}                                   # + others,total,total-rejected
PT_DRILL_STATUSES = {0: "ok", 8208: "leak", 16400: "no-connect", 18: "time-out"}
```

**Scale identifier rule:** `scale = device_id if plant in plants_with_device_id else machine_id`. The exception is filling statistics, which **always** uses `device_id` (§5.5.3).

### 5.2 Time-input conventions (preserve per endpoint)

| Input style | Conversion |
|---|---|
| GET path `from`, `to` (KPI and charts) | Epoch **seconds** converted to `'YYYY-MM-DD HH:MM:SS'` in Asia/Kolkata (naive). "Yesterday" means the same window minus 86 400 s. |
| Chart endpoints ("day" based) | `day = local_date(from)`. The window is `day 00:00:00` to `day 23:59:59`. `to` is ignored. |
| POST form `from`, `to` in **milliseconds** | Divide by 1000 and **truncate** to an int second (PHP `date()` casts to int), then convert to a local datetime string. |
| Report V2 `from`/`to` = `'YYYY-MM-DD'` | `from = 'D 00:00:00'`, `to = 'D 23:59:59'` (PHP drops the `.999`). The **phase, gap and OT SQL** instead embed `'{fromDate} 00:00:00'` and `'{toDate} 23:59:59.999'`. |
| Scales endpoints | If `from` and `to` are both non-zero, use them. Otherwise, if `period == 0`, `from = today 00:00 local`; otherwise `from = now − period×3600`. In both cases `to = now`. |

All SQL `BETWEEN` bounds are inclusive. Never send tz-aware datetimes to plant DBs.

### 5.3 Dashboard KPI tiles (GET, per plant, epoch-second window)

Every KPI response has the shape `{name, value, subvalue, todayStat, [yesterdayStat]}`.

* **Legacy error rule:** if computing *today's* stat raises an exception, return HTTP 200 with `{"exception_type": "<class>"}`.
* Class mapping for parity: DB or connection errors map to `"CodeIgniter\\Database\\Exceptions\\DatabaseException"`, and `ZeroDivisionError` maps to `"DivisionByZeroError"`.
* The old frontend checks `exception_type.includes("DatabaseException")` and then shows "Unable to connect to selected plant!".

#### 5.3.1 Production (`getProductionStat`)

```sql
SELECT system_id, SUM(CASE WHEN cyl_type=1 THEN 1 WHEN cyl_type=2 THEN 1 ELSE 0 END) AS total
FROM production_log p
WHERE process_date BETWEEN :from AND :to AND process_id IN (2,22)
  AND system_id IN (1,2) AND cyl_type IN (1,2) AND process_status NOT IN (TARE_EXCLUDED)
GROUP BY system_id
```

* If there are no rows, the stat is `false`.
* Otherwise `overall = Σ total`. Both cylinder types weigh **1** here, with no 1.25 factor.
* `todayStat = {"overall": overall}`. If overall is 0, the value is `{"overall": 0}`.
* `yesterdayStat` is the same query over the window minus 24 h.
* `value = today.overall` (0 if false).
* `subvalue = today.overall / yesterday.overall − 1` when both are truthy, else 0. This is a fraction.

#### 5.3.2 Productivity (`getProductivityStat`)

Step 1: compute these per `system_id` in (1, 2), over the window, with `process_id IN (2,22)`, `cyl_type IN (1,2)` and `process_status NOT IN TARE_EXCLUDED`:

* `total = Σ(1 if cyl_type=1, 1.25 if cyl_type=2)`
* `first_cyl_time = MIN(process_date)`
* `last_cyl_time = MAX(process_date)`

These are computed as correlated `ORDER BY … LIMIT 1` subqueries in the legacy code. If there are no rows, the stat is `false`.

Step 2: compute the interruptions for each carousel present. The legacy algorithm, which must be kept, is:

* Load **all** rows for that `system_id` in the window with `process_id IN (2,22)` and `cyl_type IN (1,2)`. There is **no status filter**. Order them by `production_log_id ASC`.
* `gap_i = int_seconds(t_i) − int_seconds(t_{i−1})`, where `gap_0 = 0`. Timestamps are truncated to whole seconds (PHP `strtotime`).
* An interruption is a gap with `gap > interruption_to_exclude_minutes × 60` (300 s). `totalInterruption = Σ` of those gaps.

Step 3: per carousel:

* `interval_h = (int(last) − int(first) − totalInterruption) / 3600`
* `productivity = total / interval_h × 8.5/7.75`
* Each row object is returned as `{system_id, total, last_cyl_time: epoch, first_cyl_time: epoch, interval, productivity}`.

Step 4:

* `overall = Σtotal / Σinterval_h × 8.5/7.75`, or 0 if Σtotal is 0.
* Legacy `todayStat` = `array_merge(["overall"=>…], rowsKeyedBySystemId)`. **PHP renumbers the integer keys**, so the result is `{"overall": x, "0": row(c1), "1": row(c2)}`. The legacy router reproduces this; v1 returns `{"overall": x, "carousels": {"1": row, "2": row}}`.
* `value = today.overall`, and `subvalue = today.overall / yesterday.overall − 1`. Yesterday is the same algorithm over the window minus 24 h.

#### 5.3.3 CS rejection (`getCSRejectionStat`)

The query groups `production_log` by `process_status, system_id`, with `process_id IN (2,22)` and `system_id IN (1,2)`. There is no cyl_type or status filter. The totals are summed over carousels 1 and 2:

* `total = Σcount`
* `totalSortout = Σcount` where the status is in `CS_SORTOUT ∪ CS_OTHER_ERR`
* `commErrorSortout = Σcount` where the status is < 0 or equal to 4096
* `cylFilled = total − totalSortout`
* `sortOutPercentage = totalSortout / total`, or 0 if total is 0. This is a **fraction**, not a percentage.

The response has `name` "CS Rejection", `value = totalSortout` and `subvalue = sortOutPercentage`, and contains no yesterday data.

#### 5.3.4 GD / PT rejection (`getGDRejectionStat`, `getPTRejectionStat`)

The query groups `event_log` by `process_status, process_id`, with `system_id IN (1,2)` and `process_id IN GD_PROCESS_IDS | PT_PROCESS_IDS`.

* `handled = Σcount`
* `sortout = Σcount` where the status is not 0
* `sortOutPercentage = sortout / handled`

If there are no rows, the stat is `false`, and `value` and `subvalue` stay 0. The names are "ELD Rejection" and "OLD Rejection". `value = sortout` and `subvalue = sortOutPercentage`.

### 5.4 Dashboard charts (GET; day window from §5.2)

#### 5.4.1 Hourly production (`getHourlyProductionData`)

The query buckets rows by `DATE_TRUNC('hour', process_date)`. It returns `c1_t1, c1_t2, c2_t1, c2_t2` (counts per carousel and type) with `process_id IN (2,22)`, carousels (1,2), types (1,2), status `NOT IN TARE_EXCLUDED`, ordered by hour. For each bucket:

* `label = f"{HH}00 - {HH+1h}00"`, where hour+1 wraps to `00` (`"2300 - 0000"`)
* `count1 = php_round(c1_t1 + c1_t2*1.25)`
* `count2` is calculated the same way

The response is `{"1": [count1…], "2": [count2…], "labels": [...], "total1": Σcount1, "total2": Σcount2}`. When there is no data, the response is `{"1": [], "2": [], "labels": [], "total1": 0, "total2": 0}`.

#### 5.4.2 Filling-accuracy distribution (`getFillingAccuracyData`)

The query groups by `system_id`, with carousels (1,2), `process_id IN (2,22)`, types (1,2) and status `IN FILL_ACCEPTED_STATUSES`. The buckets count rows on this variation `d`:

* `nil_var`: `d = 0`
* `zero_fifty`: `0 < |d| ≤ 50`
* `fifty_hundred`: `50 < |d| ≤ 100`
* `hundred_plus`: for cyl_type 1, `100 < |d| ≤ 200`. For cyl_type 2 the **legacy formula is used verbatim**: `(d19 > 100 AND check_net−14200 ≤ 200) OR (d19 < −100 AND check_net−14200 ≥ −200)` (D-01).

For each carousel present:

* `T = nil + z50 + f100 + h100`, the sum of the four buckets, not COUNT
* `carNData = [php_round(nil/T*100), php_round(z50/T*100), php_round(f100/T*100), php_round(h100/T*100)]`
* A missing carousel gives `[0, 0, 0, 0]`

The response is `{"car1Data": [...], "car2Data": [...]}`. If there are no rows at all, both lists are empty. If T is 0, see D-05.

#### 5.4.3 CS rejection breakdown (`getCSRejectionData`)

This uses the same grouping as §5.3.3 for the day window, computed per carousel 1 and 2:

* `underfilled = cnt[1040]`
* `overfilled = cnt[2064]`
* `ntare = cnt[1296] + cnt[5392]`
* `ptare = cnt[17424]`
* `timeout = cnt[1048] + cnt[4120]`
* `other = Σ CS_OTHER_ERR`
* `totalSortout` and `total` are as in §5.3.3

The response is:

```json
{ "car1Data": [underfilled, overfilled, ntare, ptare, timeout+other],
  "car2Data": [...],
  "labels": ["Underfilled","Overfilled","Negative Tare","Positive Tare","Other Errors"],
  "total1": totalSortout1 / max(total1,1),
  "total2": … }
```

#### 5.4.4 GD/PT rejection rates (`getGDPTData`)

The query groups `event_log` by `system_id, process_id, process_status`, with `process_id IN (3,23,4,24)` and carousels (1,2). Rows are classified as gd for process 3 or 23 and as pt for 4 or 24. Per carousel and kind, `handled = Σcount` and `sortout = Σ(count where status ≠ 0)`. Missing values default to 0.

Each value in `"1"` and `"2"` is computed as `php_round(sortout / (handled or 1) * 100, 2)`. The response is:

```json
{ "labels": ["ELD","OLD"],
  "1": [gd_pct_c1, pt_pct_c1],
  "2": [gd_pct_c2, pt_pct_c2],
  "eld1": gd.sortout c1, "eld2": gd.sortout c2,
  "ort1": pt.sortout c1, "ort2": pt.sortout c2 }
```

If there are no rows, the defaults are returned: empty lists and zero counts.

#### 5.4.5 GD and PT per-head drill-down (`getGDDrillDownData`, `getPTDrillDownData`)

The query groups `event_log` by `system_id, device_id, process_status`, using GD ids (3,23) or PT ids (4,24) and carousels (1,2).

1. **Status mapping.** Known statuses (see `*_DRILL_STATUSES`) go into their own bucket. Unknown statuses go into the "others" bucket by **assignment, not addition**: the last unknown status row overwrites the previous ones (D-04).
2. **Totals.** Per carousel and head, `total = Σ` of all bucket values and `total-rejected = Σ` of the non-OK buckets.
3. **Output.** The response is transposed to `{category: {head: [value_car1, value_car2]}}`.
   * Heads are always `1..6`, because the legacy `if ($plant = "panagarh")` is an assignment that is always true (D-03).
   * Carousels are `[1, 2]`. Missing values are 0.
   * GD categories are `ok, leak, others, total, total-rejected`. PT categories are `ok, leak, no-connect, time-out, others, total, total-rejected`.
   * JSON keys for heads are the strings `"1".."6"`.

#### 5.4.6 Productivity moving average (`productivityMovingAverage/{plant}/{from}/{to}`)

The parameters are `avgDuration = 30` minutes and `dataInterval = 15` minutes (not exposed in legacy), and carousels default to (1,2). The window is from → to, as datetimes.

Bucket key:

```
pe = date_part('epoch', date_trunc('hour', process_date) + floor(minute/15)*15 minutes)
```

Note that `date_part('epoch', …)` is taken from a *naive* timestamp, so PostgreSQL treats it as UTC (D-09).

Per bucket, `c1` and `c2` are weighted sums (type 1 → 1, type 2 → 1.25), with status `NOT IN TARE_EXCLUDED` and `process_id IN (2,22)`.

For each bucket B, in ascending order:

* `sum_c = Σ c over buckets X with B.pe − 1800 ≤ X.pe < B.pe`. This covers the previous 30 minutes **excluding B itself**.
* `value = sum_c × (3600 / 1800)`
* `label = local_time_HH:MM(B.pe)`, formatted from the epoch in Asia/Kolkata as PHP does

The totals are `totalN = php_round(Σ values / count(non-zero values) × 8.5/7.75, 0)`, or 0 if no values are non-zero.

The response is `{"1": [...], "2": [...], "labels": [...], "total1", "total2"}`.

### 5.5 Scale analytics

#### 5.5.1 Scale filling count (shared helper, `getScalesFillingCount(from, to, carousels)`)

* **Counts:** `COUNT(*)` grouped by `machine_id, system_id, device_id`, with `process_id IN (2,22)`, `system_id IN carousels`, types (1,2) and status `NOT IN TARE_EXCLUDED`. Ordered by system, then machine.
* **Per-carousel metadata:** `MIN/MAX(process_date)` grouped by system_id, with the same filters, giving `metaData[c] = {first_cyl_time, last_cyl_time}`.
* If there are no count rows, the result is `false`.

#### 5.5.2 Efficiency maths (`getScalesEfficiencies`, used by under-performing scales)

* `interval[c] = min(to − from, last_c − first_c)` in seconds, only for carousels that have metadata.
* If the plant is in **static** `carousal_config`:

  ```
  stdOutput[c] = stdOutput_cfg × (interval[c]/3600) / heads_cfg    # per-head expected cylinders
  ```

  `stdOutput[c]` is 0 when the carousel is not configured.
* Otherwise: `stdOutput[c] = (1 / carousal_speed[c]) × interval[c]`.
* Per row:
  * `scale` follows the §5.1 rule
  * `carousal = system_id`
  * `efficiency = count / stdOutput[system_id]`
  * `stdOutput` and `interval` are attached to the row
* Meta, for a configured plant: `car{c}Eff = Σcount_c / (stdOutput[c] × heads_cfg)`, or 0 if the carousel is unconfigured. The keys `car1Eff` and `car2Eff` always exist and default to 0. For an unconfigured plant, `carNEff = Σcount_N / (stdOutput[N] × 24)`.
* With no data, the result is `{rows: [], meta: {car1Eff: 0, car2Eff: 0}}`.

**Under-performing scales** (`getUnderPerformingScales/{plant}/{rowCount}/{period}/{from}/{to}`):

* Resolve the window as in §5.2. `rowCount` defaults to 10. Carousels are `[1, 2]`.
* Sort rows **ascending** with the comparator `int((a.efficiency − b.efficiency) × 100)`. PHP 8 casts the float result to int, truncating toward zero, so differences under 0.01 compare equal and keep their original order (stable sort). Implement this with `functools.cmp_to_key` and `int()`.
* Return the first `rowCount` rows as `{rows, meta}`.
* Each row has `{system_id, machine_id, device_id, count, scale, carousal, efficiency, stdOutput, interval}`.
* *The legacy frontend tags rows: efficiency ≤ 0.75 is below-average, ≤ 1 is average, otherwise above-average. v1 may add `tag` server-side.*

#### 5.5.3 Inaccurate scales (`getInaccurateScales/...`)

`rowCount` defaults to **5**. The window is resolved as in §5.2. The variation query uses all statuses, **ignores the carousel argument**, and uses carousels (1,2), `process_id IN (2,22)` and types (1,2). It groups by machine, system and device, and computes:

* `total = COUNT(*)`
* `hundred_plus` (the same legacy formula as §5.4.2, D-01)

Per row:

* `accuracy = 1 − hundred_plus/total`
* `scale` follows the §5.1 rule
* `carousal = system_id`
* `interval = to − from`

The meta values are `car1Acc = 1 − Σhp_1 / max(Σtotal_1, 1)` and `car2Acc`, calculated the same way.

Sort ascending with the comparator `int((a.accuracy − b.accuracy) × 10000)`, then slice to rowCount. With no data, the result is `{rows: [], meta: {car1Acc: 0, car2Acc: 0}}`.

*Frontend tag thresholds: 0.97 and 1.*

#### 5.5.4 Scale efficiency table (`POST getScaleEfficiencyDetailsTableData2`)

* **Form:** `from` and `to` in ms, `plant`, and `carousal` (0 → [1,2], 1 → [1], 2 → [2], anything else → [1,2]; the default is 0).
* **Computation:** the same as §5.5.2 (per-head `stdOutput[c]`), pivoted by scale.
* **Rows:** `{scaleId: int(scale), car1Count, car1Percent = count/stdOutput[1], car2Count, car2Percent}`. A key is present only when that carousel has data for the scale. Rows keep first-seen order (`array_values`).
* **Meta:** for a configured plant, `car{c}Eff` is calculated for each requested c (D-05 guard when stdOutput is 0).
* **Response:** `{status: "success", message: "Data Fetched Successfully!", tabledata: rows, tableparams: {carousals: int(carousal)}}`. With no data, `tabledata` is `[]`.

#### 5.5.5 Filling statistics (`POST getFillingStatistics`)

* **Form:** `from` and `to` in ms, `plant`, `carousal` (a single int) and `cylType` (1 → 14200, 2 → 19000).
* **Query:** grouped by `system_id, device_id`, with `system_id = carousal`, `process_id IN (2,22)`, `cyl_type = cylType` and status `IN FILL_ACCEPTED_STATUSES`. With `d = check_net − nominal`, it computes:
  * `n200 (d ≤ −200)`, `n150 (d = −150)`, `n100 (= −100)`, `n50 (= −50)`, `nil_var (= 0)`, `p50`, `p100`, `p150`, `p200 (d ≥ 200)`. These are exact-equality buckets, because the scale resolution is 50 g.
  * `average = Σ clamp(d, −200, 200) / COUNT`
  * `stddev = STDDEV_POP(clamp(d, −200, 200))`
  * `count`
* **Order:** by device_id.
* **Output row:** `{scaleId: int(device_id), n200, n150, n100, n50, nilVar, p50, p100, p150, p200, total: int(count), average: float, stdDev: float}`.
* **Response:** `{status: "success", message: "Data Fetched Successfully!", tabledata}`. With no data, the response is `{status: "failure", errors: null}` (D-18).
* *Frontend highlights a row when `average ≤ −10` or `average ≥ 10`.*

#### 5.5.6 Accuracy trend (`POST getAccuracyTrend`)

* **Form:** `from` and `to` (datetime strings, passed through as-is), `plant`, `carousal` and `cylType`.
* **Query:**
  * Variation is `check_net − nominal(cyl_type)` for `system_id = carousal`, `process_id IN (2,22)` and `cyl_type = cylType`.
  * Only rows with variation `BETWEEN −300 AND 300` are kept.
  * They are bucketed by `date_trunc('minute', process_date) − (minute % 15) minutes`.
* **Each row:** `{carousal, interval_start, variation_eq_0, total_cylinders, variation_eq_0_percentage = ROUND(eq0*100.0/NULLIF(total,0), 2)}`.
* **Response:** `{status: "success", data: rows | null}`.

### 5.6 Periodic production table (`POST getHourlyProductionTableData`)

* **Form:** `from` and `to` in ms, `plant` and `interval` (minutes, default 60).
* **Bucket:** `date_trunc('hour', pd) + ((minute / interval) * interval) minutes`. This is integer division, so intervals of more than 60 collapse to hourly buckets; keep that.
* **Counts:** `c1_t1, c1_t2, c2_t1, c2_t2`, with `process_id IN (2,22)`, carousels (1,2), types (1,2), and **no status exclusion**. The legacy `NOT IN (5454545454545)` is a no-op.
* **`showDate`:** true when `local_date(from_ms) != local_date(to_ms)`.
* **Output row:**
  * `from`: `'Y-m-d H:i'` if showDate, otherwise `'H:i'`
  * `to`: the period start plus `interval` minutes, in the same format
  * `c1_t1`, `c1_t2`, `c1_total = c1_t1 + c1_t2`, `c2_t1`, `c2_t2`, `c2_total`
* **Response:** `{status: "success", message: "Data Fetched Successfully!", tabledata}`. With no data, the message is `"No data found!"` and `tabledata` is `[]`.

### 5.7 Performance reports

#### 5.7.1 Building blocks (plant DB; carousels come from the **DB** config in §4.1)

`get_carousels(plant)` returns the list of `carousal_id`s from the DB config. If there are none, raise `PlantConfigMissing` (legacy produced broken SQL).

**`bottling_summary(from, to)`**

* Per carousel: `production_14_2 = count(type 1)` and `production_19 = count(type 2)`, with `process_id IN (2,22)`, types (1,2) and status `NOT IN TARE_EXCLUDED`. Grouped and ordered by system_id.
* The result is `{carousal: {carousal, production_14_2, production_19}}`.
* It returns `null` if there are no rows, **or if the first row has both counts at 0**.

**`first_last_cyl_times(from, to)`**

* Per system_id: `MAX/MIN(process_date)`, with `process_id IN (2,22)` and types (1,2), and no status filter.
* Each carousel gets `{carousal, last_cyl_time, first_cyl_time, total_hours = (int(last) − int(first))/3600}`, or `{}` if it has no rows.
* The result is `null` when the query returns nothing.

**`production_hours_summary(from, to)`** (V1 gaps)

Per carousel, port this SQL verbatim, including `LAG(...) OVER()` with no ORDER BY (D-22):

```sql
SELECT sum(gap)/60/60 AS total_gap FROM (
  WITH cte AS (
    SELECT id, production_log_id, LAG(process_date) OVER() AS from_time, process_date AS to_time,
           EXTRACT(EPOCH FROM (process_date - LAG(process_date) OVER())) AS gap
    FROM (SELECT (ROW_NUMBER() OVER (ORDER BY process_date) - 1) + 1 AS id, *
          FROM production_log
          WHERE process_date BETWEEN :from AND :to AND system_id = :car
            AND process_id IN (2,22) AND cyl_type IN (1,2)
          ORDER BY process_date ASC) AS p_log
    ORDER BY production_log_id ASC)
  SELECT * FROM cte WHERE gap > 60 * 1) AS gap_data;
```

The threshold is `60 × (MIN_INTERRUPTION_SECONDS/60)`, which is 60 s. The `minInterruption` argument is ignored by legacy. The result is `{carousal: {total_gap, carousal, …first_last_cyl_times[c]}}`.

**`check_scale_rejection_report(from, to)`**

Per DB carousel, using the §5.3.3 grouping with `system_id IN db_carousels`. All values are ints:

* `total`
* `cylFilled`
* `underfilled`
* `overfilled`
* `ntare`
* `ptare`
* `timeout`
* `other`
* `totalSortout`
* `commErrorSortout`

**`gdpt_rejection_report(from, to, kind)`**

* Per carousel (`event_log`, GD or PT ids): `{carousal, accepted = Σ(status=0), rejected = Σ(status≠0), total = COUNT}`, keyed by carousel.
* Carousels with no rows are absent. The result is `false` if there are no rows at all.

#### 5.7.2 Phases (`configToPhases`), daily operating hours

For each carousel in the DB config, take `start`, `end` and `breaks` (null is treated as an empty list). Then:

```
current = start
for br in breaks (config order):
    if current < br.from (string compare "HH:MM:SS"): working.append({from: current, to: br.from})
    breaks_out.append(br); current = br.to
if current < end: working.append({from: current, to: end})
overtime = [{from:"00:00:00", to:start}, {from:end, to:"23:59:59.999"}]
```

`daily_op_hours[c] = {normal: Σ duration(working)/3600, break: Σ duration(breaks)/3600}`. Durations use the h, m and s components only; days are ignored.

#### 5.7.3 Phase-wise production (V2)

The query uses the window `'{fromDate} 00:00:00'` to `'{toDate} 23:59:59.999'`, `process_id IN (2,22)`, status `NOT IN TARE_EXCLUDED` and `system_id = c`. Each row is classified like this:

```
phase = 'normal'   if process_date::time BETWEEN w.from::time AND w.to::time for any working window
      = 'break'    elif … any break window
      = 'overtime' otherwise
```

Per phase: `prod_14_2 = count(type 1)` and `prod_19 = count(type 2)`.

The result is `{normal: {phase, prod_14_2, prod_19}, break: {…}, overtime: {…}}`. A phase with no rows defaults to `{prod_14_2: 0, prod_19: 0}`. If a carousel has **no breaks**, the legacy SQL is invalid; see D-06.

#### 5.7.4 Production gaps per phase (V2), with PL/pgSQL functions created verbatim on the plant DB

```sql
CREATE OR REPLACE FUNCTION getGapBetweenTimes(process_time TEXT, prev_process_time text, start_time text, end_time TEXT)
RETURNS INTERVAL AS $$
begin
  return case
    when prev_process_time is null then case
        when process_time::time <= start_time::time then '0 seconds'::interval
        when process_time::time >= end_time::time   then end_time::time - start_time::time
        else process_time::time - start_time::time end
    WHEN process_time::time > end_time::time AND prev_process_time::time > end_time::time THEN '0 seconds'::interval
    WHEN process_time::time < start_time::time AND prev_process_time::time < start_time::time THEN '0 seconds'::interval
    WHEN prev_process_time::time >= start_time::time AND process_time::time <= end_time::time THEN process_time::time - prev_process_time::time
    WHEN prev_process_time::time < start_time::time AND process_time::time > end_time::time THEN end_time::time - start_time::time
    WHEN prev_process_time::time < start_time::time AND process_time::time between start_time::time and end_time::time THEN process_time::time - start_time::time
    WHEN process_time::time > end_time::time AND prev_process_time::time between start_time::time and end_time::time THEN end_time::time - prev_process_time::time
    ELSE '2 days'::interval
  END;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION getEndGapForPhase(last_cyl_time TIME, phase_start text, phase_end text)
RETURNS INTERVAL AS $$
begin
  return case
    WHEN last_cyl_time < phase_start::time THEN phase_end::time - phase_start::time
    WHEN last_cyl_time > phase_end::time THEN '0 seconds'::interval
    WHEN last_cyl_time between phase_start::time and phase_end::time THEN phase_end::time - last_cyl_time
    ELSE '0 seconds'::interval
  END;
END; $$ LANGUAGE plpgsql;
```

The gap query is built by `buildProductionGapQuery(c, phases[c], fromDate, toDate)`. Port its structure exactly:

* **`day_wise_data`**: `process_day = process_date::date` and `process_time = to_char(process_date,'HH24:MI:SS.MS')`, for rows in the window with `process_id IN (2,22)`, types (1,2) and `system_id = c`. No status filter.
* **`time_gaps`**: `prev_process_time = LAG(process_time) OVER (PARTITION BY system_id, process_day ORDER BY process_time)`.
* **`grouped_gaps`**:
  * `overtime_gap` = `process_time::time − prev_process_time::time`, but only when prev is not null **and** both times are inside the **same** overtime window (OR across windows); otherwise 0.
  * `break_gap` = `Σ getGapBetweenTimes(t, prev, b.from, b.to)` over the break windows.
  * `normal_gap` = the same Σ over the working windows.
* **`last_cyl_data`**: the `MAX(process_time::time)` for each day.
* **`end_gap_data`**: `normal_end_gap = Σ getEndGapForPhase(last, w.from, w.to)` over the working windows, and `break_end_gap` over the break windows.
* **`intervening_gaps_data`**: for each distinct process_day, the SUMs of `break_gap`, `normal_gap` and `overtime_gap` over the `grouped_gaps` rows where `break_gap + normal_gap + overtime_gap > '60 seconds'`. Use COALESCE to 0.
* **Final result** (hours):
  * `total_normal_gap = EPOCH(Σ(total_normal_gap + normal_end_gap))/3600`
  * `total_break_gap = EPOCH(Σ(total_break_gap + break_end_gap))/3600`
  * `total_overtime_gap = EPOCH(Σ total_overtime_gap)/3600`

All three values are null if there is no data.

#### 5.7.5 Non-operating days, operating hours, overtime production time

* **`non_operating_days(c, fromDate, toDate)`**: count the days from `generate_series(fromDate, toDate, '1 day')` that have zero rows in `production_log`, with `system_id = c`, `process_id IN (1,2,22)` and `process_status NOT IN (16)`.
* **`production_gaps_for_all_carousels(fromDate, toDate)`**: if from > to, return `false`. Otherwise, for each carousel, the gap totals (§5.7.4) are combined with:
  * `carousal`
  * `intervening_days = (to − from).days + 1`
  * `non_op_days`
  * `net_op_days = intervening − non_op`
  * `daily_op_hours`
  * `max_op_hours = {normal: daily.normal × net_op_days, break: daily.break × net_op_days}`
  * `net_op_hours = {normal: max.normal − total_normal_gap, break: max.break − total_break_gap}`
* **`ot_production_period(c, fromDate, toDate)`**: per day, compute `production_time = max(time) − min(time)` separately for rows with `time BETWEEN '00:00:00' AND start` and rows with `time BETWEEN end AND '23:59:59.999'`. The result, in hours, is `{total_pre_shift_time: Σ, total_post_shift_time: Σ}`; each is null if there are no rows.

#### 5.7.6 Productivity calculations

**V1** (`calculateProductivity`):

* Per bottling row: `total_production = p14_2 + 1.25 × p19`
* `net_hours = hours[c].total_hours − hours[c].total_gap`, with null treated as 0
* `productivity = total_production / net_hours`
* The result is `{c: {carousal, total_production, net_hours, productivity}}`. If bottling is null, it is `{}`.

**V2** (`calculateProductivityV2`): for each carousel and each phase (normal, break, overtime):

* `total_production = prod_14_2 + 1.25 × prod_19`
* `gap = hours2[c]["total_<phase>_gap"]`, with null treated as 0
* For normal and break: `net_hours = max_op_hours[phase] − gap`
* For overtime: `net_hours = (pre ?? 0) + (post ?? 0) − gap`
* `productivity = total_production / net_hours` if net_hours is truthy (≠ 0), else 0. Negative net hours are allowed.

#### 5.7.7 Report endpoints

**V1, `POST getPerformanceReport`**

* **Form:** `from` and `to` in ms, and `plant`.
* **Response:**

  ```
  { status: "success", message: "Data Fetched Successfully!",
    data: { carousals: [ids], bottlingData, productionHoursData,
            checkScaleRejectionSummary, gdRejectionSummary, ptRejectionSummary, productivityData } }
  ```

* If `bottling_summary` raises, the response is `{status: "failure", error: "Unable to connect to selected Plant!", th: {}}`.

**V2, `POST getPerformanceReportV2`** (and the alias route `getPerformanceReportV2Test2`)

* **Form:** `from` and `to` as `YYYY-MM-DD`, and `plant`.
* **`data`** contains:
  * `carousals`: ids
  * `bottlingData`: V1 summary
  * `bottlingData2`: phase-wise
  * `productionHoursData`: V1 gaps
  * `productionHoursData2`: §5.7.5
  * `checkScaleRejectionSummary`, `gdRejectionSummary`, `ptRejectionSummary`
  * `productivityData`: V1
  * `productivityData2`: V2
* The time strings for the CS, GD and PT reports are from `'D 00:00:00'` to `'D 23:59:59'`.
* In legacy, a plant connection error is **re-thrown**, giving HTTP 500 (D-19).

**Mailer data (`getPerformanceReportV2Data`)**

This is V2 with these keys: `carousals` (the **full** DB config map), `bottlingData` (phase-wise), `productionHoursData` (§5.7.5), CS, GD and PT, and `productivityData` (V2). On a connection error it returns `{status: "failure", error, detailedError}`.

#### 5.7.8 Mailer feed (`POST /performance-report`, JSON `{from, to, plant}`; CORS `*`)

1. Load `allPlants` from `getAllPlantsConfig()` (§5.8). Build `carousalCounts[short] = carousals_count` and `zoneMaps[short] = zone`.
2. Set `plantRow = getPlantsConfigByShortName(plant)`, which is `{short_name, name, zone, carousals_count}` or null.
3. Set `resp = getPerformanceReportV2Data(plant, from, to)`. On success, `dRow = {plant: plantRow, **resp.data}`; otherwise `dRow = {plant: plantRow}`, which yields `[]`.
4. For each `carId, car` in `dRow.carousals`, emit this row:

   ```
   isAdditionalCarousel = carId > 1
   short_name, name = plant.short_name, plant.name ; zone = zoneMaps[short_name]
   carousel = carId ; filling_heads = car.heads ; carousel_count = carousalCounts[short_name]
   bottling.14_2kg = Σ_phase bottlingData[carId][phase].prod_14_2
   bottling.19kg   = Σ_phase bottlingData[carId][phase].prod_19 ; bottling.total = sum of both
   productivity.normal   = {production: prod.normal.total_production,
                            stoppages: php_round(hours[carId].total_normal_gap, 2),
                            productivity: php_round(prod.normal.productivity, 0)}
   productivity.break    = {production, net_hours: php_round(.,2), productivity: php_round(.,0)}
   productivity.overtime = {production, net_hours: php_round(.,2), productivity: php_round(.,0)}
   rejections.eld.percent = php_round(gd[carId].rejected / gd[carId].total × 100, 2) if present & total≠0 else 0
   rejections.ort.percent = same with pt
   rejections.cs.percent  = php_round(cs[carId].totalSortout / cs[carId].total × 100, 2) if total≠0 else 0
   ```

The response is the list of rows. *The `Views/mail/performance-report-plant.php` template uses stale keys and is not part of the contract.*

### 5.8 Plants, meta, connectivity

* **`getPlants`**: from the static `plants`, sorted by key (ksort), as `{"plants": [{"value": short, "text": name}]}`.
* **`getPlants2`**: from the central DB, as `{"plants": rows}`, where each row is `{value: short_name, text: plant_name, zone, carousals_count}`.
  * The rows come from `plants LEFT JOIN carousals ON plants.id = carousals.plant_id`, grouped by plant and ordered by short_name.
  * `carousals_count = COUNT(carousals.carousal_id)`.
  * On PostgreSQL, list every non-aggregated column in GROUP BY.
* **`getPlantsConfigByShortName(short)`**: the same join, filtered to the plant, as `{short_name, name, zone, carousals_count}`, or null unless exactly one row is found.
* **`zone-maps`**: `[{zone, plant: value}]` from `getAllPlantsConfig`.
* **`getPlantName`**: `{"name": static plant_name}`.
* **`getDeviceName`**: `{"client": reverse_dns(client_ip) or null}`. Run the lookup in a thread pool with a 1 s timeout, and honour `X-Forwarded-For` only from trusted proxies.
* **`getConnectivityStatus/{plant}`**:
  * The plant must be in the static `plants` **and** resolvable to DB credentials (D-13).
  * Run `SELECT production_log_id FROM production_log LIMIT 1`, measuring the latency from the request start, including the connection, in **milliseconds** (php_round).
  * Responses:
    * Success: `{status: "success", message: "Connection Successful!", latency}`
    * No row: `{status: "failure", message: "Connection Error"}`
    * Exception: the same failure payload (never echo exception internals)
    * Unknown plant, or no plant given: `{status: "failure", message: "Unknown Plant"}`

### 5.9 Admin configuration utilities (legacy `ConfigController`)

Port these as **CLI commands only** (admin, explicit `--confirm`, default dry-run). They must not be HTTP routes.

* **`sync-carousels-from-static`** (`copyCarousalsConfig`):
  * Truncate `carousals`.
  * For each plant in the DB `plants` table, and each carousel in the static config, insert `{plant_id, carousal_id, heads, rated_productivity = stdOutput, start_time = times.start ?? NULL, stop_time = times.end ?? NULL}`.
* **`sync-breaks-from-static`** (`copyBreaksConfig`):
  * Truncate `breaks`.
  * Insert one row per static `times.breaks` entry.
* **`copyPlantsFromConfig`**: blocked in legacy ("Execution prevented to avoid data loss."). **Do not port.**

---

## 6. API catalogue (new backend)

There are two routers over the **same services**:

* **`/api/v1`** is the primary, clean, typed API for the new frontend. It uses snake_case query params, explicit units in param names, and the standard error envelope (§7.6).
* **`/prodash`** is the legacy-compatible router. It has the exact paths, methods, form fields and JSON shapes of the PHP app. It exists for **parity testing** and so the current Vue bundle can run during the transition. Controlled by `LEGACY_API_ENABLED`.

| # | Legacy (method path) | v1 (method path) | Service function | Rule |
|---|---|---|---|---|
| E01 | GET `/getPlants` | GET `/plants` | `PlantService.list_static` | §5.8 |
| E02 | GET `/getPlants2` | GET `/plants/registry` | `PlantService.list_registry` | §5.8 |
| E03 | GET `/zone-maps` | GET `/plants/zone-map` | `PlantService.zone_map` | §5.8 |
| E04 | GET `/getPlantName` | GET `/meta/plant-name` | `MetaService.plant_name` | §5.8 |
| E05 | GET `/getDeviceName` | GET `/meta/client-device` | `MetaService.client_device` | §5.8 |
| E06 | GET `/getConnectivityStatus/{plant}` | GET `/plants/{plant}/connectivity` | `ConnectivityService.check` | §5.8 |
| E07 | GET `/getProductionStat/{plant}/{from}/{to}` | GET `/plants/{plant}/kpis/production?from_ts&to_ts` | `KpiService.production` | §5.3.1 |
| E08 | GET `/getProductivityStat/...` | GET `/plants/{plant}/kpis/productivity` | `KpiService.productivity` | §5.3.2 |
| E09 | GET `/getCSRejectionStat/...` | GET `/plants/{plant}/kpis/cs-rejection` | `KpiService.cs_rejection` | §5.3.3 |
| E10 | GET `/getGDRejectionStat/...` | GET `/plants/{plant}/kpis/gd-rejection` | `KpiService.gdpt_rejection("gd")` | §5.3.4 |
| E11 | GET `/getPTRejectionStat/...` | GET `/plants/{plant}/kpis/pt-rejection` | `KpiService.gdpt_rejection("pt")` | §5.3.4 |
| E12 | GET `/getHourlyProductionData/...` | GET `/plants/{plant}/charts/hourly-production` | `ChartService.hourly_production` | §5.4.1 |
| E13 | GET `/getFillingAccuracyData/...` | GET `/plants/{plant}/charts/filling-accuracy` | `ChartService.filling_accuracy` | §5.4.2 |
| E14 | GET `/getCSRejectionData/...` | GET `/plants/{plant}/charts/cs-rejection` | `ChartService.cs_rejection_breakdown` | §5.4.3 |
| E15 | GET `/getGDPTData/...` | GET `/plants/{plant}/charts/gd-pt-rejection` | `ChartService.gdpt_rates` | §5.4.4 |
| E16 | GET `/getGDDrillDownData/...` | GET `/plants/{plant}/charts/gd-drilldown` | `ChartService.drilldown("gd")` | §5.4.5 |
| E17 | GET `/getPTDrillDownData/...` | GET `/plants/{plant}/charts/pt-drilldown` | `ChartService.drilldown("pt")` | §5.4.5 |
| E18 | GET `/productivityMovingAverage/{plant}/{from}/{to}` | GET `/plants/{plant}/charts/productivity-moving-average` | `ChartService.productivity_moving_average` | §5.4.6 |
| E19 | GET `/getUnderPerformingScales/{plant}/{rowCount}/{period}/{from}/{to}` | GET `/plants/{plant}/scales/under-performing?row_count&period_hours&from_ts&to_ts` | `ScaleService.under_performing` | §5.5.2 |
| E20 | GET `/getInaccurateScales/...` | GET `/plants/{plant}/scales/inaccurate` | `ScaleService.inaccurate` | §5.5.3 |
| E21 | POST `/getScaleEfficiencyDetailsTableData2` (form) | GET `/plants/{plant}/tables/scale-efficiency?from_ms&to_ms&carousel` | `ScaleService.efficiency_table` | §5.5.4 |
| E22 | POST `/getFillingStatistics` (form) | GET `/plants/{plant}/tables/filling-statistics?from_ms&to_ms&carousel&cyl_type` | `FillingService.statistics` | §5.5.5 |
| E23 | POST `/getAccuracyTrend` (form) | GET `/plants/{plant}/tables/accuracy-trend?from&to&carousel&cyl_type` | `FillingService.accuracy_trend` | §5.5.6 |
| E24 | POST `/getHourlyProductionTableData` (form) | GET `/plants/{plant}/tables/periodic-production?from_ms&to_ms&interval_min` | `ProductionService.periodic_table` | §5.6 |
| E25 | POST `/getPerformanceReport` (form) | GET `/plants/{plant}/reports/performance?from_ms&to_ms` | `ReportService.performance_v1` | §5.7.7 |
| E26 | POST `/getPerformanceReportV2`, `/getPerformanceReportV2Test2` (form) | GET `/plants/{plant}/reports/performance-v2?from_date&to_date` | `ReportService.performance_v2` | §5.7.7 |
| E27 | POST `/performance-report` (JSON) | POST `/reports/performance-mail-rows` | `MailerService.performance_rows` | §5.7.8 |
| E28 | — (myth/auth login pages) | POST `/auth/login`, POST `/auth/refresh`, GET `/auth/me` | `AuthService` | §7.8 |
| E29 | — | GET `/health/live`, GET `/health/ready` (central DB ping) | — | §7.9 |
| E30 | — (ConfigController via auto-route) | CLI `prodash sync-carousels`, `prodash sync-breaks`, `prodash migrate-central-data` | `ConfigSyncService` | §5.9 |

**Not ported** (dead, debug or destructive; see §13):

* `getRandom*`
* `getArenaStats`
* `temp`
* `test1`
* `minuteTest`
* `changeProcessStatus*`
* `correctData`
* `getProductionSummary`
* `getCarousalConfig` (HTML table)
* `getPerformanceReportTest`
* `testStoppageReport*`
* `testConfigToPhases`
* `getPerformanceReportV2Test` (HTML view)
* `getUnderPerformingScales2`
* `getProductivityStat2`
* `getScaleEfficiencyDetailsTableData` / `…3`
* page-view methods returning `index.html` (the SPA is served elsewhere)
* VMS leftovers (packedtruck, bulktruck, athithi, notices, Entities, `secure_helper`)
* `CommandModel` (biometric device commands)
* `/sendmail`
* `/mailer/test`

v1 query validation (Pydantic):

* `from_ts` and `to_ts` are non-negative ints in seconds, with `to_ts ≥ from_ts`.
* `from_ms` and `to_ms` are ints in milliseconds.
* Dates are ISO `date` values.
* `carousel` is 0, 1 or 2 where the legacy code mapped it.
* `cyl_type` is a `Literal[1, 2]`.
* `interval_min` is between 1 and 1440.
* `row_count` is between 1 and 500.
* `period_hours` is ≥ 0.
* `plant` matches `^[a-z0-9]+$`, as in the legacy `(:alphanum)` route segment.

---

## 7. Target architecture

### 7.1 Technology stack (fixed)

| Concern | Choice |
|---|---|
| Framework | FastAPI (ASGI; run with uvicorn) |
| Validation / schemas | Pydantic v2 (`model_config = ConfigDict(from_attributes=True, populate_by_name=True)`) |
| Settings | pydantic-settings (`.env` + env vars, prefix-free names below) |
| ORM / SQL | SQLAlchemy 2.x **async** (`AsyncEngine`, `async_sessionmaker`, `text()` for plant SQL) |
| Central DB | PostgreSQL target (`postgresql+psycopg://`). MySQL is supported during the transition (`mysql+aiomysql://`). |
| Plant DBs | PostgreSQL via `postgresql+psycopg://` (psycopg[binary] v3, async) |
| Migrations | Alembic (central DB only) |
| Auth | JWT (HS256 by default; RS256 optional), passlib[bcrypt] |
| HTTP client | httpx (parity harness, outbound calls, tests via `ASGITransport`) |
| Logging | Python `logging` (dictConfig, JSON formatter, request-id filter) |
| Tests | pytest, pytest-asyncio (`asyncio_mode = "auto"`) |
| Docs | OpenAPI / Swagger at `/docs`, ReDoc at `/redoc`, schema at `/openapi.json` |

Pin `bcrypt==4.0.1` alongside `passlib[bcrypt]==1.7.4`, because newer bcrypt breaks passlib's version detection. On Windows, the psycopg async driver needs `WindowsSelectorEventLoopPolicy`.

### 7.2 Project layout

```
prodash-backend/
├── pyproject.toml                 # deps, ruff, mypy, pytest config
├── alembic.ini
├── .env.example                   # every Settings field, no secrets
├── config/
│   └── static_plant_config.yaml   # verbatim port of Config\Secure prodash section (§4.3)
├── migrations/                    # Alembic env + versions (central DB only)
├── app/
│   ├── main.py                    # create_app(): routers, middleware, exception handlers, lifespan
│   ├── core/
│   │   ├── config.py              # Settings (pydantic-settings)
│   │   ├── logging.py             # dictConfig, JSON formatter, request-id contextvar
│   │   ├── security.py            # JWT encode/decode, password hashing (legacy-compatible)
│   │   ├── errors.py              # AppError hierarchy + handlers (v1 envelope, legacy shapes)
│   │   ├── middleware.py          # request id, access log, timing, CORS setup
│   │   ├── timeutils.py           # IST conversions, epoch/ms parsing, PHP-like formatting
│   │   └── php_compat.py          # php_round, php_usort_key, int-trunc seconds, json key helpers
│   ├── db/
│   │   ├── base.py                # DeclarativeBase, naming convention
│   │   ├── central.py             # central AsyncEngine + session dependency
│   │   └── plant_registry.py      # per-plant AsyncEngine cache (LRU+TTL), DSN build, functions bootstrap
│   ├── models/                    # SQLAlchemy ORM (central DB): plant.py, carousel.py, carousel_break.py,
│   │                              #   option.py, user.py, role.py (user_roles), refresh_token.py
│   ├── domain/                    # PURE logic, no I/O
│   │   ├── constants.py           # §5.1
│   │   ├── static_config.py       # YAML loader → frozen model
│   │   ├── phases.py              # configToPhases, daily op hours
│   │   ├── productivity.py        # V1/V2 calculations, moving-average windowing, KPI interruption algo
│   │   ├── rejections.py          # CS status aggregation, GD/PT aggregation, drill-down mapping
│   │   ├── scales.py              # efficiency/accuracy maths, sorting, pivot
│   │   └── mail_rows.py           # transformData
│   ├── repositories/
│   │   ├── central/               # plant_repo.py, carousel_repo.py, user_repo.py
│   │   └── plant/                 # production_log_repo.py, event_log_repo.py, gaps_repo.py,
│   │       └── sql/               # *.py modules holding text() SQL constants + safe builders
│   ├── services/                  # orchestration: one module per §6 service
│   ├── schemas/                   # Pydantic request/response models (v1) + legacy loose models
│   ├── api/
│   │   ├── deps.py                # get_settings, get_central_session, get_plant_conn, auth guards
│   │   ├── v1/                    # routers: plants.py, meta.py, kpis.py, charts.py, scales.py,
│   │   │                          #   tables.py, reports.py, mailer.py, auth.py, health.py
│   │   └── legacy/                # prodash_compat.py (exact PHP paths/shapes)
│   └── cli/                       # `prodash` Typer-free argparse entry: sync-carousels, sync-breaks,
│                                  #   migrate-central-data, create-admin, check-plants
└── tests/
    ├── conftest.py
    ├── unit/                      # domain/* pure functions, php_compat, timeutils
    ├── repositories/              # against seeded Postgres (marker: integration)
    ├── api/                       # httpx AsyncClient + ASGITransport, dependency overrides
    ├── parity/                    # golden JSON from PHP vs new legacy router (marker: parity)
    └── fixtures/                  # SQL seeds for production_log/event_log, static config sample
```

### 7.3 Layering rules (enforced in review)

```
api (routers)  →  services  →  repositories  →  db engines
        ↘              ↘ domain (pure functions, constants, static config)
```

* **Routers:**
  * Parse and validate input with Pydantic.
  * Resolve dependencies (auth, plant connection).
  * Call **one** service method and map the result to a response schema.
  * Contain no SQL and no maths.
* **Services:**
  * Orchestrate repository calls.
  * Apply domain functions.
  * Raise `AppError` subclasses.
  * Know nothing about HTTP (no `Request` or `Response` objects).
* **Repositories:**
  * Run SQL only, with bound parameters.
  * Return plain dicts or dataclasses with Python-native types.
  * Do no business maths. They may, however, port aggregations that the legacy code did inside SQL.
* **Domain:**
  * Deterministic, synchronous and fully unit-tested.
  * Must not import FastAPI, SQLAlchemy sessions or settings. Static config is passed in as an argument.
* **Legacy router:** a thin adapter that calls the same services and reshapes the output into the PHP JSON (key renumbering, string keys, legacy error payloads). **No business logic in adapters.**

### 7.4 Database access design

**Central DB**

* One `AsyncEngine` (`CENTRAL_DATABASE_URL`) with `pool_pre_ping=True`.
* A session per request via `async_sessionmaker(expire_on_commit=False)`.
* Alembic owns its schema.
* When the central DB is MySQL, `aiomysql` returns `TIME` columns as `timedelta`. Normalise every time value to a zero-padded `"HH:MM:SS"` string in the repository.

**Plant DBs** (`db/plant_registry.py`):

1. `get_plant_engine(short_name)` resolves credentials from the central `plants` table and caches the result per plant.
2. It builds `postgresql+psycopg://{db_user}:{quote(db_password)}@{host_ip}:{PLANT_DB_PORT}/{db_database}`.
3. Engine options:
   * `pool_size=PLANT_DB_POOL_SIZE` (default 2)
   * `max_overflow=3`
   * `pool_pre_ping=True`
   * `pool_recycle=1800`
   * `connect_args={"connect_timeout": PLANT_DB_CONNECT_TIMEOUT_S, "options": f"-c statement_timeout={PLANT_DB_STATEMENT_TIMEOUT_MS} -c default_transaction_read_only=on"}`
4. The engine cache is LRU-bounded (`PLANT_ENGINE_CACHE_MAX`, default 64) with TTL eviction. Evicted engines are disposed. The cache can be invalidated by the CLI when a plant changes.
5. A per-plant `asyncio.Semaphore(PLANT_MAX_CONCURRENCY)` protects slow plant links. The old frontend fires the V2 report for every plant in parallel.
6. **PL/pgSQL bootstrap.** Before the first V2 gap query on an engine, run the two `CREATE OR REPLACE FUNCTION` statements once, in a separate **read-write** connection, then cache a flag on the engine. The legacy code re-created them on every request (D-21).
   * If permission is denied, log a WARNING and continue if the functions already exist.
   * Otherwise raise `PlantFunctionsUnavailable`.
7. An unknown plant raises `PlantNotFound`. In legacy this surfaced as a DatabaseException.
8. **Connection or OS errors** (`OperationalError`, `asyncio.TimeoutError`) raise `PlantUnreachable(plant)`. **SQL errors** (`ProgrammingError`/`DataError`) raise `PlantQueryError`. Both carry the plant short_name and never include the DSN.

### 7.5 Configuration (`core/config.py`, pydantic-settings)

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `ProDash API` | |
| `APP_ENV` | `development` | `development`, `test` or `production`. Swagger is disabled in production unless `DOCS_ENABLED=true`. |
| `API_V1_PREFIX` | `/api/v1` | |
| `LEGACY_API_ENABLED` / `LEGACY_PREFIX` | `true` / `/prodash` | |
| `TIMEZONE` | `Asia/Kolkata` | Used by all PHP-equivalent date handling. |
| `CENTRAL_DATABASE_URL` | — | `postgresql+psycopg://…` or `mysql+aiomysql://…` |
| `LEGACY_MYSQL_URL` | optional | Source for `migrate-central-data`. |
| `PLANT_DB_PORT` | `5432` | |
| `PLANT_DB_POOL_SIZE`, `PLANT_DB_MAX_OVERFLOW` | `2`, `3` | |
| `PLANT_DB_CONNECT_TIMEOUT_S` | `5` | |
| `PLANT_DB_STATEMENT_TIMEOUT_MS` | `120000` | Report V2 over long ranges is heavy. |
| `PLANT_ENGINE_CACHE_MAX`, `PLANT_ENGINE_TTL_S` | `64`, `3600` | |
| `PLANT_MAX_CONCURRENCY` | `4` | |
| `STATIC_PLANT_CONFIG_PATH` | `config/static_plant_config.yaml` | |
| `JWT_SECRET_KEY` | — (required) | ≥ 32 random bytes |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `30` / `7` | |
| `AUTH_ENABLED` | `true` | When false, read endpoints are open (legacy parity mode). Admin endpoints and the CLI are always protected. |
| `SERVICE_API_KEYS` | `[]` | Hashed keys for the machine caller of E27 (`X-API-Key`). |
| `CORS_ORIGINS` | `[]` | E27 legacy path keeps `*` (legacy header behaviour) only when `LEGACY_API_ENABLED`. |
| `TRUSTED_PROXIES` | `[]` | For the client IP in E05. |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | |

Secrets come only from the environment or a secret store, **never** from the repo. The legacy `Config/Database.php` contains plaintext credentials for every plant: rotate them and do not copy them into the repo.

### 7.6 Error handling contract

The `AppError` hierarchy is defined in `core/errors.py`:

| Error | HTTP (v1) | code |
|---|---|---|
| `ValidationError` (Pydantic) | 422 | `VALIDATION_ERROR` |
| `PlantNotFound` | 404 | `PLANT_NOT_FOUND` |
| `PlantConfigMissing` (no carousels or breaks needed) | 409 | `PLANT_CONFIG_INCOMPLETE` |
| `PlantUnreachable` | 502 | `PLANT_UNREACHABLE` |
| `PlantQueryError` | 502 | `PLANT_QUERY_FAILED` |
| `ComputationError` (zero division in legacy crash paths, D-05) | 200 with guarded value **or** 422, depending on the §11 decision | `COMPUTATION_UNDEFINED` |
| `AuthError` / `PermissionDenied` | 401 / 403 | `UNAUTHENTICATED` / `FORBIDDEN` |
| Unhandled | 500 | `INTERNAL_ERROR` (generic message; details only in logs) |

The v1 error envelope is:

```json
{ "status": "failure", "error": { "code": "PLANT_UNREACHABLE", "message": "Unable to connect to selected plant", "details": {"plant": "mysore"} }, "request_id": "…" }
```

v1 success responses return the typed schema directly. Use no wrapper unless the endpoint is a report, in which case it returns `{status, message, data}`, mirroring legacy.

The legacy router reproduces the PHP payloads:

* KPI `exception_type` (§5.3)
* report `{status: "failure", error: "Unable to connect to selected Plant!"}`
* connectivity `failure`
* `{status: "failure", errors: null}`

**Never leak stack traces, SQL or DSNs.** The legacy code returned `th` and `detailedError` objects; the legacy router returns `{}` in their place.

### 7.7 Logging

* Configure stdlib `logging.config.dictConfig` once in the `lifespan` handler.
* Use a JSON formatter with the fields `ts, level, logger, msg, request_id, plant, path, method, status, duration_ms`.
* Request-id middleware takes `X-Request-ID` or generates a uuid4, stores it in a contextvar and echoes it in the response header.
* Write an access log line per request at INFO. Log plant query timings at DEBUG (statement name and duration, never parameters containing credentials).
* Log at WARNING for: legacy crash-path guards triggered (D-05/D-06), PL/pgSQL bootstrap failures, slow queries (> `SLOW_QUERY_MS`, default 5000).
* Logger names follow `prodash.<layer>.<module>`. No `print` anywhere: the legacy `print_r` and echo debugging must not be ported.

### 7.8 Authentication and authorisation (JWT)

* **Users** live in the central DB. Port the myth/auth `users` table (id, email, username, password_hash, active, status, force_pass_reset, fullname, phone, deleted_at, timestamps).
  * The Alembic migration creates `roles` and `user_roles`.
  * Seed the roles from `auth_groups`: `admin → admin`, `officers → officer`, and `security`/`dataentry → viewer`.
  * The data-copy CLI copies users and group memberships.
* **Password hashing (legacy-compatible):** myth/auth stored `password_hash = bcrypt(base64(sha384(password)))` (`$2y$`, cost 10). Keep **the same pre-hash** for all users so existing hashes verify unchanged:

  ```python
  pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__default_rounds=12)
  def _prehash(p: str) -> str: return base64.b64encode(hashlib.sha384(p.encode()).digest()).decode()
  def verify_password(p, h): return pwd_context.verify(_prehash(p), h)          # accepts $2y$/$2b$
  def hash_password(p): return pwd_context.hash(_prehash(p))
  # on successful login: if pwd_context.needs_update(h): store hash_password(p)
  ```

* **Tokens:**
  * The access JWT carries `sub` (user id), `roles`, `type="access"`, `iat`, `exp` and `jti`.
  * The refresh JWT has `type="refresh"` and is stored hashed in `refresh_tokens` so it can be revoked. It rotates on every use.
* **Endpoints (E28):**
  * `POST /auth/login` takes `{username_or_email, password}` and returns `{access_token, refresh_token, token_type: "bearer", expires_in}`.
  * Reject users who are inactive, banned or soft-deleted.
  * Rate-limit failed logins per account (lockout counter in the DB).
* **Guards (`api/deps.py`):** `require_user`, `require_roles("admin")`.
  * All read endpoints need `viewer`, `officer` or `admin` when `AUTH_ENABLED`.
  * E27 (the mailer feed) accepts either a service API key (`X-API-Key`, compared in constant time against hashed keys) or an admin JWT.
  * The legacy router obeys the same guards when `AUTH_ENABLED=true`. Set it to false only in a closed network during migration.

### 7.9 Operational concerns

* **Health checks.** `/health/live` always returns 200. `/health/ready` pings the central DB and loads the static config.
* **Startup** (`lifespan`): load the settings and static config (fail fast if they are invalid), create the central engine, and warm nothing on the plant side.
* **Shutdown:** dispose every cached plant engine and the central engine.
* **Optional cache.** A short in-process TTL cache (15–60 s) for KPI and chart endpoints, keyed by `(endpoint, plant, window)`, because the dashboard polls on intervals. It must be behind `RESPONSE_CACHE_TTL_S` (default 0, off) and must never be used for reports.
* **Timeouts.** Reports over long date ranges can run for minutes. Keep them synchronous for parity. Moving them to background jobs is a later enhancement (§12.4).
* **OpenAPI.** Every v1 route needs a `summary`, a `description` that references the rule id (§5.x), `response_model`, and `responses={4xx/5xx: ErrorEnvelope}`. Tags: plants, kpis, charts, scales, tables, reports, mailer, auth, health, legacy.

---

## 8. Coding conventions

### 8.1 General

* Python 3.12+. Full type hints and `mypy --strict` on `app/domain` and `app/core`. Ruff (lint and format) with a line length of 110.
* Everything is async end-to-end: no blocking I/O in the event loop, and DNS lookups go through `run_in_executor`.
* Name things in English. Keep the legacy spelling **only** in legacy JSON keys (`carousal`, `carousals`, `tableparams`…). Use `carousel` internally and in v1.
* Never use `print`. Never leave commented-out code. Every public function gets a docstring that cites its §5 rule.

### 8.2 Pydantic v2

* Request query models inherit from `BaseModel` and are used via `Annotated[Model, Query()]` (FastAPI ≥ 0.115) or explicit `Query` params.
* Response models are explicit for v1. Legacy responses may use `dict[str, Any]`, built by the adapters.
* Use `Field(description=…, examples=[…])` on all public fields, `model_validator(mode="after")` for cross-field checks such as `to ≥ from`, and `Literal` for enumerations.

### 8.3 PHP numeric and format compatibility (`core/php_compat.py`) — mandatory

* **`php_round(x, precision=0)`** rounds half away from zero, using `Decimal(repr(x)).quantize(Decimal(1).scaleb(-precision), ROUND_HALF_UP)` on the absolute value with the sign restored. **Never use the built-in `round()`** for ported maths.
* **Int truncation of epoch seconds:** `int(ms / 1000)` and `int(dt.timestamp())`. PHP `strtotime` drops milliseconds.
* **usort comparator:** `cmp = lambda a, b: int((a.k - b.k) * factor)` with `sorted(..., key=cmp_to_key(cmp))`. Python's sort is stable, like PHP 8.
* **PHP division:** `/` always gives a float. Integer counts from the DB are Python `int`.
* **JSON keys:** PHP arrays with non-sequential int keys serialise to objects with string keys (`{"1": …, "2": …}`). PHP `array_merge` renumbers int keys (§5.3.2). Use helpers `php_object_keys()` and `php_array_merge()` in the legacy adapters only.
* **Numeric type change (accepted):** legacy JSON returned many DB numbers as **strings** (`"count": "123"`). The new APIs return numbers. Parity tests normalise numeric strings (§9.3).
* **Time formatting:** `strftime('%H')`, `'%H:%M'`, `'%Y-%m-%d %H:%M'` in Asia/Kolkata. Use `timeutils.from_epoch_local(sec) -> naive datetime`.

### 8.4 SQL rules (plant DB)

1. **Port the SQL text faithfully.** The filters, groupings, CASE logic, window functions and thresholds *are* the business logic. Do not "optimise" a query unless a parity test proves the outputs are identical.
2. **Use bound parameters for every value.** The legacy code interpolated strings: plant, dates, carousel, cylType (D-07). Use `text(...).bindparams(bindparam("cars", expanding=True))` for IN lists.
3. **Dynamic fragments.** Phase windows and gap expressions are built by builder functions that:
   * accept only time strings validated by `^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$`
   * emit them as **bind parameters** (`:w0_from`, `:w0_to`, …)
   * have unit tests asserting the generated SQL text
4. Keep constant tuples (status codes, process ids) in `domain/constants.py` and bind them; never re-type them inline.
5. Name each statement (a module-level constant `SQL_<NAME>`) for logging and tests.
6. Plant connections are read-only (§7.4). The only write is the function bootstrap.
7. Central DB access goes through the ORM, except reporting joins like `getAllPlantsConfig`, which may use Core `select()`. On PostgreSQL every non-aggregated column must be in GROUP BY.

### 8.5 Git and PR hygiene

* One endpoint or rule per PR. The PR description lists the §6 id, the §5 rule, its tests, and any D-xx touched.
* Conventional commits (`feat(kpis): …`, `fix(parity): …`).

---

## 9. Testing strategy

### 9.1 Unit tests (`tests/unit`, fast, no I/O) — required for every domain function

* `php_round`: halves (0.5, 1.5, 2.5, −0.5, 2.675 at precision 2), negatives and precision.
* Phases (`configToPhases`) for these cases:
  * no breaks
  * one or two breaks
  * a break at the start
  * the anantapur config (start 14:00, break 19:00–19:30, end 22:30)
* Daily operating hours.
* Productivity V1 and V2, including net_hours 0 → 0, null gaps and a negative net.
* KPI interruption algorithm: gap list, threshold 300 s, first gap 0, ms truncation.
* Moving-average windowing: the current bucket is excluded, the ×2 factor, and the mean of non-zero values.
* CS status aggregation (every code bucket, comm errors < 0 and 4096), GD/PT aggregation, and the drill-down "others" overwrite (D-04) with heads 1..6.
* Scale efficiency: configured and unconfigured plants, the device-id rule, the comparator truncation and the slice.
* Accuracy maths, mail-row transformation (all rounding), and the label wrap `"2300 - 0000"`.
* Time conversions: ms truncation, the IST day window and the `showDate` rule.

### 9.2 Repository and integration tests (`tests/repositories`, marker `integration`)

* Run against a disposable PostgreSQL database (docker compose service `pg-test`), seeded from `tests/fixtures/plant_seed.sql`.
* The seed holds synthetic `production_log` and `event_log` rows covering: two carousels, both cylinder types, every status code, gaps under and over 60 s and 300 s, rows across phases and overtime, a non-operating day, and unknown GD/PT statuses.
* Central DB tests use the same server with Alembic migrated to head.
* Fixtures use `pytest-asyncio` with function-scoped transactions rolled back after each test.

### 9.3 Parity (golden master) tests (`tests/parity`, marker `parity`)

* `scripts/capture_golden.py` (httpx) calls the **running PHP app** for a matrix of plants, dates and parameters and stores the JSON in `tests/parity/golden/<endpoint>/<case>.json`. The matrix covers at least: a plant with a static config and times (anantapur), a single-carousel plant (kochi), an unconfigured fallback plant, a device-id plant, a no-data day and a multi-day V2 range.
* The test replays the same request against the new **legacy router** and compares after normalisation:
  * numeric strings become numbers
  * floats are compared with `abs ≤ 1e-9` or a relative tolerance of `1e-12`
  * key order is ignored
  * `exception_type` class names are compared by suffix
* Any intentional difference must be listed in `tests/parity/allowed_diffs.yaml`, referencing a D-xx id.
* **Release gate:** 100 % parity for every E07–E27 case, apart from the allowed diffs.

### 9.4 API tests (`tests/api`)

* Use `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
* Override `get_plant_conn` and `get_central_session` through dependency overrides.
* Assert status codes, schemas, the auth guards (401, 403, API key for E27) and the error envelope.

### 9.5 Tooling

* `pytest -q -m "not parity and not integration"` runs in CI on every push. Integration tests run in CI with the Postgres service. Parity runs on demand or nightly against staging PHP.
* Coverage target: ≥ 90 % for `app/domain` and `app/core`, ≥ 80 % overall.

---

## 10. Delivery plan (ordered tasks for agents)

| Phase | Tasks | Done when |
|---|---|---|
| **P0 Foundations** | Scaffold the §7.2 layout, Settings, logging, error handlers, request-id middleware, health routes, `php_compat`, `timeutils`, constants, static YAML (port verbatim and test the duplicate-key semantics) | Unit tests green; `/docs` renders |
| **P1 Central DB** | ORM models and an Alembic baseline (plants, carousals, breaks, options, users, roles, user_roles, refresh_tokens); `migrate-central-data` CLI (MySQL → PostgreSQL, idempotent, dry-run); plant and carousel repositories; `CarouselConfigService` (§4.1 shape) | Config for every plant matches PHP `getCarousalConfig` output |
| **P2 Plant connectivity** | `plant_registry` (engine cache, read-only, timeouts, semaphores, function bootstrap), E06, E01–E05 | Connectivity parity |
| **P3 Dashboard** | E07–E18 services, domain functions and both routers | Parity for E07–E18 |
| **P4 Scales and tables** | E19–E24 | Parity |
| **P5 Reports** | Phases, gap SQL builders, OT, non-op days, V1/V2 calculations, E25–E27 | Parity, including multi-day V2 and the mailer rows |
| **P6 Auth** | User migration, JWT, guards, login rate-limit, `create-admin` CLI, API keys | Legacy users can log in with their existing passwords |
| **P7 Hardening** | Load test (55 plants × V2 in parallel), slow-query logging, the optional cache, Docker image, runbook | p95 targets agreed with the owner; no plant connection leaks |

### P1 implementation notes (central DB)

* **Baseline mirrors the legacy schema.** `migrations/versions/0001_baseline.py` adds no constraint that §12.1 lists as needing owner sign-off (unique `short_name`, FKs on `carousals`/`breaks`, CHECKs). Legacy-guaranteed uniques on `users.email` and `users.username` are kept. New tables: `roles` (seeded `admin`, `officer`, `viewer`), `user_roles`, `refresh_tokens`.
* **Alembic lives in `migrations/`** (as §7.2), not `alembic/` as the CLAUDE.md layout sketch says. The URL comes from `CENTRAL_DATABASE_URL`.
* **`migrate-central-data`** (`python -m app.cli.main migrate-central-data [--confirm]`): dry run by default, rolls back; with `--confirm` it upserts by primary key, so reruns are idempotent and legacy ids are preserved. Rows that cannot be represented are skipped and reported: `carousals` with NULL `heads`/`rated_productivity` or a TIME of 24 h or more, `breaks` with a NULL time, and group memberships with no role mapping or an unknown user. `plants.db_password` is copied unchanged (D-23 still open).
* **`CarouselConfigService.get_config(plant)`** returns `{carousal_id: {heads, stdOutput, times: {start, end, breaks}}}` in `ORDER BY id`. An unknown plant raises `PlantNotFound`; a plant without carousel rows gives `{}`; `get_carousels` raises `PlantConfigMissing` for that case (§5.7.1).
* **Open verification:** the exit criterion "matches PHP `getCarousalConfig`" needs golden files in `tests/parity/golden/carousel_config/` (format in `tests/parity/test_carousel_config_parity.py`). Until they are captured the parity test is skipped.

### P2 implementation notes (plant connectivity)

* **Plant access** lives in `app/core/db.py` (`PlantDatabases`: engine cache, read-only sessions, semaphores, 10 s connect timeout, per-connection latency log, unpooled read-write session for the bootstrap only) and `app/services/plant_access.py` (`PlantAccess`: credentials from the central `plants` row, `PlantNotFound` / `PlantUnreachable` / `PlantQueryError` mapping). `app/db/plant_registry.py` from §7.2 is not created; `core/db.py` plays that role.
* **Gap-function bootstrap (D-21):** `PlantAccess.ensure_gap_functions(plant)` checks `pg_proc` read-only first, creates the two functions over a separate read-write connection only when missing, and caches the result per plant per process. A permission failure is tolerated only when the functions exist, otherwise `PlantFunctionsUnavailable` (502, `PLANT_FUNCTIONS_UNAVAILABLE`). Nothing calls it until P5.
* **Connectivity (E06, D-13):** static-`plants` membership is checked before any connection; credentials come from the `plants` table. Plant-side errors and a missing probe row give `Connection Error` (HTTP 200); latency is `php_round` of milliseconds from the start of the check.
* **Legacy paths:** `/zone-maps` is served under `LEGACY_PREFIX` (`/prodash/zone-maps`) like every other legacy route; the PHP app exposed it at the site root for the mailer job. Mount it at the root too if that job cannot be repointed.
* **Static config:** `domain/static_config.py` and `config/static_plant_config.yaml` exist, but `plants` and `carousal_config` are empty because `Config\Secure` is not in this repository. **Until they are ported verbatim, E01 returns an empty list and E06 answers `Unknown Plant` for every plant.**
* **Auth guards are not applied yet** (P6); these routes are open as in the legacy app.

### P4 implementation notes (scales and tables)

* **Static config:** `config/static_plant_config.yaml` `plants` (55 entries) was derived from the connection groups of the legacy `Database.php` (all Postgre groups except `kosan`, `testplant`, `jodhpur`). Values are Title-Cased slugs because that file has no display names, and `carousal_config` is still empty because it is not in `Database.php`. **Until `carousal_config` is ported from `Config\Secure`, every plant is treated as unconfigured by §5.5.2, so E19 and E21 use the `carousal_speed` fallback and will differ from PHP for configured plants.**
* **SQL:** `repositories/plant/production_log_repo.py`. All values bound; the D-01 `hundred_plus` formula is kept verbatim. The count column is aliased `cnt` because `Row.count` is the tuple method.
* **Guards (D-05):** efficiency or meta with `stdOutput` 0 (carousel absent from the plant's static config, or zero interval) returns 0 and logs a WARNING instead of raising.
* **v1 validation:** `carousel` and `cyl_type` are bounded ints (0-2 / 1-2) rather than `Literal`, because query and form strings do not coerce to integer literals. Accuracy-trend `from`/`to` must look like `YYYY-MM-DD[ T]HH:MM[:SS]` in both routers; legacy passed any string to SQL (D-07).
* **Legacy error payloads (assumption, verify against PHP):** E19/E20 return `{"exception_type": "...DatabaseException"}` and E21-E24 return `{status: failure, error: "Unable to connect to selected Plant!", th: {}}` on any plant error, mirroring §5.3 and §5.7.7. `rowCount` 0 falls back to the endpoint default (10 / 5).
* **Not covered here:** SQL against a real PostgreSQL (needs the `pg-test` integration database, §9.2) and golden parity for E19-E24 (needs PHP captures plus seeded plant data).

---

## 11. Legacy defect and quirk register

The default decision is **PRESERVE** (business logic unchanged). **GUARD** means the legacy code crashed, so the port returns a defined result and logs a WARNING. **FIX** is a non-business, technical fix. **DROP** means the code is not ported. Items marked "OWNER" need a product decision before any behavioural change.

| ID | Where | Legacy behaviour | Decision |
|---|---|---|---|
| D-01 | `hundred_plus` (filling accuracy, inaccurate scales) | For 19 kg the bounds use `check_net − 14200`, so 19 kg overfills of more than 100 g are never counted, and underfills between 14.0 and 18.9 kg are | PRESERVE + OWNER (proposed fix: use 19000) |
| D-02 | `getFillingAccuracyData` | `average`/`stddev` computed with 14200 for both types (not part of the output) | n/a (do not expose) |
| D-03 | GD/PT drill-down | `if ($plant = "panagarh")` is an assignment, so heads are always 1..6 | PRESERVE + OWNER (proposed: heads from carousel config) |
| D-04 | GD/PT drill-down | Unknown statuses overwrite the "others" bucket instead of adding to it, so totals are under-counted | PRESERVE + OWNER |
| D-05 | Several divisions: KPI productivity with interval 0, filling accuracy with T = 0, efficiency with stdOutput 0, V1 productivity with net_hours 0, subvalue with yesterday 0 | PHP 8 `DivisionByZeroError`, giving a 500 or an `exception_type` | GUARD: the KPI legacy router keeps the `exception_type: "DivisionByZeroError"` payload; elsewhere the value is 0, plus a WARNING |
| D-06 | Report V2 for a carousel with no breaks | An empty CASE / `+` fragment, so a SQL syntax error | GUARD: raise `PlantConfigMissing` (409, `PLANT_CONFIG_INCOMPLETE`); OWNER may approve "no break phase" support |
| D-07 | All plant SQL and `getPlantsConfigByShortName` | String-interpolated values, which allow SQL injection | FIX (bound params) |
| D-08 | Auto-route and `/(:alphanum)` | Unauthenticated access to debug and destructive methods (`changeProcessStatus` updates production data, `correctData`, the `copy*` truncations) | DROP from HTTP; the needed ones become CLI |
| D-09 | Moving-average labels | `date_part('epoch', naive ts)` is UTC-based, and PHP formats it in IST, so labels may be shifted by +05:30 | PRESERVE whatever the PHP output is (verify in parity); OWNER |
| D-10 | Dashboard endpoints | Carousels hard-coded to (1, 2); a third carousel (loni) is ignored | PRESERVE + OWNER |
| D-11 | Carousel config | Static YAML for the scale features, DB tables for reports; the values can drift | PRESERVE both sources; add a `check-plants` CLI diff report (§12) |
| D-12 | Static config | Duplicate PHP keys (`kota`, `jammu`): the last one wins | PRESERVE (kota = 1800) |
| D-13 | Connectivity | Used the static per-plant DB config in `Database.php` | FIX: use the `plants` table credentials, plus the static `plants` membership check |
| D-14 | KPI productivity | Interruptions computed over all statuses, ordered by id, in PHP memory | PRESERVE (stream rows, fetching only `process_date`, with `yield_per`) |
| D-15 | Sorting | The usort comparator truncates to int | PRESERVE |
| D-16 | Rounding | PHP rounds half away from zero | PRESERVE via `php_round` |
| D-17 | KPI `todayStat` | `array_merge` renumbers carousel keys | PRESERVE in the legacy router; v1 uses `carousels` keyed by id |
| D-18 | Filling statistics with no data | `{status: "failure", errors: null}` | PRESERVE (legacy); v1 returns `[]` with success |
| D-19 | V2 report plant error | Re-thrown, giving a 500 (V1 returns failure JSON) | Legacy: 500 with a generic body; v1: 502 `PLANT_UNREACHABLE` |
| D-20 | `getCylsFilled` | Wrote queries to `records4.sql` on disk | DROP |
| D-21 | V2 gaps | `CREATE OR REPLACE FUNCTION` on every request | FIX: bootstrap once per engine |
| D-22 | V1 gaps SQL | `LAG() OVER()` without ORDER BY relies on subquery order | PRESERVE verbatim; covered by parity |
| D-23 | `plants.db_password` | Plaintext in the central DB, and plaintext per-plant credentials in the repo | FIX (recommended): an encrypted column or secret store; rotate the credentials |
| D-24 | Auth | myth/auth present, but prodash and mailer were in `noAuthRoutes` | FIX: JWT guards (switchable through `AUTH_ENABLED` during migration) |
| D-25 | `getBottlingSummary` | Returns null if the **first** carousel row is 0/0, even when others have data | PRESERVE + OWNER |
| D-26 | `getPreviousDay*` (not routed) | Recursion resets the day counter | DROP |
| D-27 | `getPlants2` / `getAllPlantsConfig` | Relies on MySQL loose GROUP BY | FIX (full GROUP BY) with the same output |

---

## 12. Schema analysis and suggested additional analysis

### 12.1 Central database: issues and recommended improvements (each needs a migration and an owner sign-off)

1. **Constraints.**
   * `plants.short_name` UNIQUE NOT NULL
   * `carousals (plant_id, carousal_id)` UNIQUE, with FKs from `carousals.plant_id` and `breaks.plant_id` to `plants.id` (ON DELETE CASCADE)
   * a CHECK that `start_time < stop_time` on breaks
   * a CHECK that `heads > 0` and `rated_productivity > 0`
2. **Single source of truth for carousel config.** Today `heads`/`stdOutput` live in both the static YAML and the `carousals` table, and `use_device_id` lives in both `plants` and the static list. Recommended steps:
   1. Run the `check-plants` diff report.
   2. Reconcile the values with the owner.
   3. Move everything into the DB, with columns `carousels.uses_device_id` and a `plants.is_active` flag.
   4. Delete the YAML usage. The scale results change only if the values differ, which is why the report comes first.
3. **Plants without config.** For example, `jodhpur` appears in the static config but not in `plants`. Some plants have no `times` or breaks, which blocks V2 (D-06). Add a readiness view.
4. **Credentials.** Encrypt `db_password` (for example with a KMS or Vault reference) and add `db_port` and `db_sslmode` columns. Use a read-only DB role on every plant.
5. **Zones.** Normalise them into a `zones` table with an FK, so zone dashboards can roll up reliably.
6. **Audit columns.** `created_at`, `updated_at`, `updated_by` on plants, carousels and breaks; config changes alter KPIs historically.
7. **Effective-dated config.** Rated output, heads and shift times change over time. Add `valid_from`/`valid_to` to carousels and breaks so that historical reports use the config that applied *at that time*. Today, history is recomputed with the current config.
8. **Unused tables.** Drop or archive `options` and the VMS leftovers once confirmed.

### 12.2 Plant databases (`production_log`, `event_log`): performance

* Ask the vendor or DBA for, or verify (`EXPLAIN ANALYZE`), these indexes:

  ```sql
  production_log (system_id, process_date) INCLUDE (process_id, process_status, cyl_type, check_net, device_id, machine_id)
  event_log (system_id, process_date) INCLUDE (process_id, process_status, device_id)
  ```

  Almost every query filters by a date range and system_id.
* **Precomputed rollups.** A nightly or incremental job writes `plant_hourly_stats` (per plant, carousel, hour, cylinder type and status class) and `plant_daily_gaps` into a central analytics schema. Dashboards and multi-plant reports then become cheap, and plant links are queried only for the current day. Validate the rollups against the live parity outputs first.

### 12.3 Data dictionary and data quality

* **Decode `process_status` as a bitmask.** The codes break down cleanly into powers of two (hypothesis, to be confirmed with the control-system vendor):
  * 1040 = 1024 + 16 (underweight)
  * 2064 = 2048 + 16 (overweight)
  * 1296 = 1024 + 256 + 16 (negative tare)
  * 17424 = 16384 + 1024 + 16
  * 1048 = 1024 + 16 + 8
  * 4120 = 4096 + 16 + 8
  * 272 = 256 + 16 (GD leak)
  * 8208 = 8192 + 16 (PT leak)
  * 16400 = 16384 + 16
  * 18 = 16 + 2

  The 16 bit looks like "rejected". A `status_codes` reference table (code, bit meaning, station, category) would replace the hard-coded lists, explain the "others" bucket and make the rules configurable.
* **Data-quality checks per plant and day:**
  * the share of negative or 4096 statuses (comm errors)
  * `check_net` outliers (> ±2 kg)
  * duplicate `production_log_id`s
  * clock jumps (`process_date` going backwards while the id increases), which breaks the gap SQL in D-22
  * gaps > 24 h
  * `machine_id`/`device_id` outside `1..heads`
* **Freshness monitor:** `max(process_date)` per plant against now, plus the connectivity latency history. This feeds a plant-health board.

### 12.4 Additional analytics this data supports (candidate features)

1. **OEE per carousel and shift.** Availability = net op hours ÷ scheduled hours (from the gaps); Performance = actual output ÷ (`stdOutput` × net hours); Quality = 1 − (CS + GD + PT sort-outs) ÷ handled.
2. **Gas give-away (overfill cost).** Σ positive variation (grams) of accepted cylinders × LPG cost per kg, per scale, carousel, plant and zone. Underfill exposure is the mirror metric for compliance with legal-metrology tolerance.
3. **Scale SPC.** Per-scale mean and std-dev control charts (X̄-S), Cp/Cpk against the ±tolerance, and drift alerts. The data already exists in filling statistics.
4. **Stoppage analytics.** A Pareto of gap durations by time of day and phase; MTBF/MTTR per carousel; start-up delay against scheduled start (the legacy `getStartStopTime` prototype); early shutdowns.
5. **Head-level leak analytics.** GD/PT leak rate per head over time, to spot failing filling valves or test heads (predictive maintenance).
6. **Benchmarking.** Plant, zone and national league tables for productivity (normalised by `heads` and `stdOutput`), rejection rates and overtime dependence (OT production ÷ total).
7. **Capacity planning.** Utilisation against the rated output (AOP), and the 14.2 kg vs 19 kg mix trend.
8. **Anomaly detection.** Hourly production against a seasonal baseline per plant, flagging sudden drops beyond the gap rules.
9. **Report scheduling.** Move the V2 reports and mailer rows into background jobs with stored results (a `report_runs` table), so the e-mail job and the UI read finished results instead of hitting 55 plants live.

---

## 13. Out of scope / not to be ported

* Every view-rendering method (the SPA will be rebuilt separately), the error and debug views, and the CodeIgniter debug toolbar.
* The VMS / security-gate modules that leaked into this codebase: the `Entities/*` (trucks, transporters, visitors, workmen, notices), the `secure_helper` functions, the routes for `packedtruck`, `bulktruck`, `athithi` and `notices`, `CommandModel` (biometric device commands), and the `Router` controller redirects.
* The test and debug methods listed under §6 "Not ported", and data-mutation utilities against plant DBs (`changeProcessStatus`, `correctData`).
* E-mail sending. The legacy app only exposed data for an external mailer; the SMTP settings exist but were never used. If e-mail is needed later, add a `notifications` module with httpx/SMTP behind a feature flag.

---

## 14. Definition of Done (every task)

* [ ] Behaviour matches the cited §5 rule. Any deviation is recorded as a D-xx entry with a decision.
* [ ] Layering (§7.3), SQL rules (§8.4) and PHP compatibility (§8.3) are respected. No secrets, prints or commented-out code.
* [ ] Unit tests cover the domain logic. Repository and integration tests cover the SQL. Parity cases are added for every legacy endpoint touched.
* [ ] The OpenAPI documentation is complete (summary, description with the rule id, models, error responses).
* [ ] Structured logs exist for errors and slow paths. Errors use the §7.6 envelope.
* [ ] `ruff`, `mypy` (strict on the domain and core), and `pytest -m "not parity"` all pass.
* [ ] This `agent.md` is updated if an endpoint, rule, setting or decision changed.
