# IronOCR worker bridge

Local .NET 9 executable that wraps IronOcr for the Phase 1 benchmark
(roadmap §4 candidate #5 and §8.5). The Python `IronOCRAdapter`
(`benchmarks/ocr/adapters/ironocr.py`) spawns this binary, pipes a JSON
request on stdin, and parses the JSON response on stdout. Business-field
extraction stays in the Python parser — this worker only surfaces OCR
text, per-word geometry, confidence, and version metadata.

## Pinned dependencies

- `IronOcr` **2026.10.2**
- `IronOcr.Languages.Hebrew` **2020.11.2**
- `IronOcr.Languages.Arabic` **2020.11.2**
- `.NET 9.0`

Record these — plus the resolved IronOcr runtime hash — in the run
manifest. If you upgrade any of them, re-run Phase 1 and update the
roadmap's pinning table.

## Build

```powershell
cd ocr-benchmarks/bridges/ironocr-worker
dotnet publish -c Release -r win-x64 --self-contained false
```

Publish output: `bin/Release/net9.0/win-x64/publish/IronOcrWorker.exe`.

Point the Python adapter at it via the config:

```yaml
- id: ironocr-heb-ara-eng
  adapter: ironocr
  params:
    lang: Hebrew,Arabic,English
    bridge_path: C:\dev\tests\malio-tests\ocr-benchmarks\bridges\ironocr-worker\bin\Release\net9.0\win-x64\publish\IronOcrWorker.exe
```

## Licensing

IronOcr requires a commercial license for production use. The worker reads
the key from the `IRONOCR_LICENSE_KEY` environment variable at startup:

```powershell
$env:IRONOCR_LICENSE_KEY = "<key>"
```

**Never commit the key.** The repo-level `.gitignore` excludes `bin/` and
`obj/`, but double-check your settings/user.config too.

Record the applicable license tier, permitted usage, developer/server/core
limits, renewal fees, and amortization period in the Phase 1 cost model
(§9). The library's absence of per-page metering does not imply a free
runtime or SaaS redistribution rights.

## JSON request/response contract

See the comment at the top of `Program.cs`. Request fields:

| Field | Required | Notes |
|---|---|---|
| `pages[].page_index` | yes | 0-based within the source document. |
| `pages[].image_path` | yes | Absolute path to a rasterized page image. |
| `languages` | no | Defaults to `["Hebrew", "English"]`. First is primary. |
| `options.read_barcodes` | no | Default off; benchmark currently does not need it. |

Response fields are structured so the Python adapter can build `OCRRegion`s
with no post-processing (`polygon` is a 4-point rectangle, `confidence` is
normalized to `[0,1]`, `granularity` is `"word"` or `"line"`).

## Running locally for smoke tests

```powershell
$req = @"
{
  "pages": [{"page_index": 0, "image_path": "C:\\dev\\tests\\malio-tests\\ocr-benchmarks\\images-tests\\photo_2 (2).jpg"}],
  "languages": ["Hebrew", "English"]
}
"@
$req | .\bin\Release\net9.0\win-x64\publish\IronOcrWorker.exe
```

The response is a single JSON object on stdout. Errors use the shape
`{"error": {"label": "...", "message": "..."}}` with exit code 1.
