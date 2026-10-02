# Catalogue Validate API Endpoint

## Overview

The `catalogue/validate/` endpoint stores invalid/failed SKU codes from an uploaded text file into `ShopSettings.invalid_stock_items`. It simply reads the file and saves the contents as a JSON list - no validation against the database is performed.

## Endpoint Details

- **URL**: `https://dev.merrypak.co.za/api/sync/catalogue/validate/`
- **Method**: `POST`
- **Content-Type**: `multipart/form-data`
- **Authentication**: FixedToken (same as other D3 API endpoints)

## Input File Format

The endpoint accepts a `.txt` file with SKU codes that failed validation. Each line should contain either:
- A single SKU code
- A pipe-separated line (e.g., `SKU|PRICE|QOH`) - the SKU is extracted from the first field

### Example Input File (Failed SKUs)

**Simple format (one SKU per line):**
```text
INVALID001
INVALID002
INVALID003
INVALID004
INVALID005
```

**Pipe-separated format:**
```text
INVALID001|0.00|0|
INVALID002|0.00|0|
INVALID003|0.00|0|
```

## Request Example

### cURL

```bash
curl -X POST \
  https://dev.merrypak.co.za/api/sync/catalogue/validate/ \
  -H 'Authorization: Token YOUR_API_TOKEN' \
  -F 'file=@failed_skus.txt'
```

### Python (requests)

```python
import requests

url = "https://dev.merrypak.co.za/api/sync/catalogue/validate/"
headers = {
    "Authorization": "Token YOUR_API_TOKEN"
}
files = {
    "file": open("failed_skus.txt", "rb")
}

response = requests.post(url, headers=headers, files=files)
print(response.json())
```

## Response Format

### Success Response (200 OK)

```json
{
  "message": "Invalid stock items saved",
  "invalid_stock_items": [
    "INVALID001",
    "INVALID002",
    "INVALID003",
    "INVALID004",
    "INVALID005"
  ]
}
```

### Error Responses

**Missing file (400 Bad Request):**
```json
{
  "error": "No file uploaded. Please include a txt file in the request."
}
```

**Invalid file type (400 Bad Request):**
```json
{
  "error": "Invalid file type. Only .txt files are accepted."
}
```

**Encoding error (400 Bad Request):**
```json
{
  "error": "File encoding error. Please ensure the file is UTF-8 encoded."
}
```

**Server error (500 Internal Server Error):**
```json
{
  "error": "Unexpected error processing file: <error_message>"
}
```

## Model Storage

The invalid stock items are stored in the `ShopSettings` model:

```python
class ShopSettings(models.Model):
    invalid_stock_items = JSONField(
        default=list,
        verbose_name="Invalid Stock Items",
        blank=True,
        null=True,
        help_text="List of invalid SKU codes from catalogue validation"
    )
```

### Accessing Invalid Stock Items Programmatically

```python
from shop.models import ShopSettings

settings = ShopSettings.load()
invalid_items = settings.invalid_stock_items

if invalid_items:
    print(f"Found {len(invalid_items)} invalid SKUs:")
    for sku in invalid_items:
        print(f"  - {sku}")
```

## How It Works

1. **Read the file**: The file is read as UTF-8 encoded text
2. **Extract SKUs**: Each line is stripped and the first field (before pipe `|` if present) is used as the SKU
3. **Store as JSON**: The list of SKUs is saved directly to `ShopSettings.invalid_stock_items`

## Use Cases

- Store SKUs that failed validation from an external system
- Record catalogue import errors for later review
- Save failed SKU lists from D3 or other integrations
- Track invalid products for cleanup operations

## Related Endpoints

- [`/api/d3/products/`](d3-api.md) - Bulk update products from text file
- [`/api/d3/stock/profile/`](d3-api.md) - Get stock profile for a single SKU
