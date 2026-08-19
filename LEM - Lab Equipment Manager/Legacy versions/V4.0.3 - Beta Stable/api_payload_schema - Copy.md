# API Payload Schema Documentation

## `/api/status` Endpoint

**Method**: `GET`  
**Auth Required**: No  
**Content-Type**: `application/json`

### Response Structure

```json
{
  "generated_at": "2023-01-01T12:00:00",
  "boxes": [
    { /* BoxStatus object - see below */ }
  ],
  "errors": [
    { /* Error object - see below */ }
  ],
  "refresh_seconds": 300
}
```

---

## BoxStatus Object

Complete structure of each equipment/box status payload.

### Example

```json
{
  "uid": "box_1234567890",
  "title": "GC-MS Analyzer",
  
  // === Legacy Fields (Backward Compatible) ===
  "status": "RED",
  "reason": "Critical issues in: QC, TESTING",
  
  // === Multi-Dimensional Status Fields (NEW) ===
  "sub_statuses": {
    "qc": {
      "status": "RED",
      "reason": "QC stale (Last valid: 2023-01-01 10:00)"
    },
    "calibration": {
      "status": "GREEN",
      "reason": "No Due Calibration"
    },
    "pm": {
      "status": "YELLOW",
      "reason": "Due Today: Routine PM"
    },
    "testing": {
      "status": "RED",
      "reason": "Testing failed in contexts: Gasoline: Density out of spec (0.95)."
    }
  },
  
  "context_results": {
    "Diesel": "GREEN",
    "Gasoline": "RED"
  },
  
  "overall_explanation": "Critical issues in: QC, TESTING",
  
  // === Timestamps ===
  "last_good_qc": "2023-01-01T10:00:00",
  "latest_match_time": "2023-01-01T12:00:00",
  
  // === Manual Override ===
  "manual_override": "",  // Or "SERVICE", "DEAD-LINE"
  
  // === Detailed Results ===
  "results": [
    {
      "label": "Diesel / Density",
      "value": 0.85,
      "value_display": "0.85 g/cm³",
      "in_spec": true,
      "expected": 0.85,
      "low": 0.83,
      "high": 0.87,
      "note": "",
      "timestamp": "2023-01-01T12:00:00",
      "timestamp_source": "parsed"
    }
  ],
  
  // === Configuration Metadata ===
  "csv_path": "\\\\server\\data\\gc-ms.csv",
  "csv_name": "gc-ms.csv",
  "spec": [
    {"sample": "Diesel", "test": "Density"},
    {"sample": "Gasoline", "test": "Octane"}
  ],
  "qc_expire_hours": 24,
  
  // === Maintenance Tasks ===
  "tasks": [
    {
      "id": "task_123",
      "name": "Routine PM",
      "kind": "pm",
      "next_due": "2023-01-01",
      "status": "PENDING"
    }
  ],
  
  // === UI Metadata ===
  "status_color": "#f85b5b",
  "pos": [100.0, 200.0],
  "size": [300.0, 150.0],
  "locked": false,
  
  // === File Status ===
  "file_status": "ok",  // "ok", "missing", "none"
  "file_error": ""
}
```

---

## Field Definitions

### Core Status Fields

| Field | Type | Description |
|-------|------|-------------|
| `uid` | string | Unique box identifier |
| `title` | string | Human-readable equipment name |
| `status` | string | Overall status: `"GREEN"`, `"YELLOW"`, `"RED"`, `"UNKNOWN"`, `"SERVICE"`, `"DEAD-LINE"` |
| `reason` | string | Brief overall status explanation |
| `status_color` | string | Hex color code for UI rendering |

### Multi-Dimensional Status (NEW)

| Field | Type | Description |
|-------|------|-------------|
| `sub_statuses` | object | Map of sub-status dimensions to status objects |
| `sub_statuses.qc` | object | Quality Control health status |
| `sub_statuses.calibration` | object | Calibration maintenance status |
| `sub_statuses.pm` | object | Preventative Maintenance status |
| `sub_statuses.testing` | object | Test result status |
| `context_results` | object | Map of context/sample names to status strings |
| `overall_explanation` | string | Concise summary identifying root cause drivers |

#### Sub-Status Object Structure

```json
{
  "status": "RED",  // GREEN, YELLOW, RED, UNKNOWN
  "reason": "Explanation text"
}
```

### Timestamps

| Field | Type | Description |
|-------|------|-------------|
| `last_good_qc` | string\|null | ISO 8601 timestamp of last in-spec QC sample |
| `latest_match_time` | string\|null | ISO 8601 timestamp of most recent data match |
| `generated_at` | string | ISO 8601 timestamp when snapshot was generated |

### Manual Override

| Field | Type | Description |
|-------|------|-------------|
| `manual_override` | string | Active override: `""` (none), `"SERVICE"`, or `"DEAD-LINE"` |

When override is active:
- `status` reflects the override value
- `reason` mentions "override"
- `overall_explanation` includes underlying issues
- `sub_statuses` remain computed (not affected by override)

### Detailed Results Array

Each result object:

| Field | Type | Description |
|-------|------|-------------|
| `label` | string | Display name (format: "Sample / Test") |
| `value` | float\|null | Latest numeric test result |
| `value_display` | string | Formatted value with units |
| `in_spec` | bool\|null | Whether value is within expected range |
| `expected` | float\|null | Expected/target value |
| `low` | float\|null | Lower control limit |
| `high` | float\|null | Upper control limit |
| `note` | string | Explanation if value is null or error |
| `timestamp` | string\|null | ISO 8601 timestamp of this result |
| `timestamp_source` | string | How timestamp was determined: `"parsed"`, `"derived"`, `"file_mtime"`, `"generated"` |

---

## Status Values

### Overall and Sub-Status Values

- `"GREEN"` - Nominal operation
- `"YELLOW"` - Warning/degraded condition
- `"RED"` - Critical issue requiring attention
- `"UNKNOWN"` - Insufficient data
- `"SERVICE"` - Manual override: equipment in service
- `"DEAD-LINE"` - Manual override: equipment out of service

### Derivation Policy

Overall status is derived via severity ordering:

1. If any sub-status is `RED` → overall `RED`
2. Else if any sub-status is `YELLOW` → overall `YELLOW`
3. Else if any sub-status is `UNKNOWN` → overall `UNKNOWN`
4. Else → overall `GREEN`

Manual overrides (`SERVICE`, `DEAD-LINE`) take precedence over computed overall status.

---

## Context Breakdown

The `context_results` object groups test results by **sample name** (acting as fuel-type/context identifier).

**Example**:
```json
"context_results": {
  "Diesel": "GREEN",   // All Diesel tests passed
  "Gasoline": "RED",   // Some Gasoline test(s) failed
  "Jet Fuel": "UNKNOWN" // No data for Jet Fuel
}
```

The Testing sub-status is derived from the worst context:
- If any context is `RED` → Testing `RED`
- Else if any context is `UNKNOWN` → Testing `UNKNOWN`
- Else → Testing `GREEN`

---

## Error Object

```json
{
  "path": "\\\\server\\data\\missing.csv",
  "error": "FileNotFoundError: No such file"
}
```

## Backward Compatibility

**Guaranteed stable fields** (will never be removed):
- `uid`, `title`, `status`, `reason`, `results`

**New fields** (additive, safe to ignore):
- `sub_statuses`, `context_results`, `overall_explanation`

Existing clients that only read `status` and `reason` will continue to function without modification.

---

## Server-Sent Events (SSE)

### `/api/events` Endpoint

**Method**: `GET`  
**Content-Type**: `text/event-stream`

Publishes real-time updates:

```
data: {"type": "status", "data": { /* same structure as /api/status response */ }}

data: {"type": "config", "data": { /* configuration change payload */ }}
```

The `data.boxes` array in status events has the same structure as the `/api/status` response, including all multi-dimensional fields.

---

## Version

**API Version**: 5

This documentation describes the multi-dimensional status framework available from version 5 onwards.
