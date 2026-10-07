# ABBYY FineReader Engine 12 worker bridge

Local .NET 9 executable that drives ABBYY FineReader Engine 12 (FRE 12) for
candidate #6 (roadmap §4 and §8.5). Mirrors the IronOCR bridge:
JSON request on stdin (or `arg[0]` = request file path), JSON response on
stdout. Business-field extraction stays in Python; this worker only
surfaces OCR text + per-word geometry + confidence + metadata.

FRE 12 is a licensed C++ SDK that exposes a Windows COM API. We bind to
it **late** (`Type.GetTypeFromProgID("FREngine.Engine.12")`) so the
project compiles without the SDK present. At runtime:

- SDK not installed / not registered → BLOCKED (`"BLOCKED: ABBYY FineReader
  Engine COM object not creatable..."`).
- SDK installed but license invalid → BLOCKED (COM license HRESULTs are
  mapped to the BLOCKED label so the harness reports access blockers
  consistently with the other candidates).
- SDK + license OK → full OCR output.

## Prerequisites to actually run (your action)

1. **Install FineReader Engine 12** — licensed MSI from ABBYY. Install the
   SDK (not FineReader PDF or the consumer app).
2. **Install language modules** for Hebrew, Arabic, English.
3. **Register FREngine for COM**. The SDK's installer typically does this;
   if not, run `regsvr32` or FRE's provided registration helper against
   `FREngine.dll`. Confirm with PowerShell:

   ```powershell
   [Type]::GetTypeFromProgID("FREngine.Engine.12")
   # should print RuntimeType, not $null
   ```

4. **Provide license credentials** via environment variables before
   invoking the worker:

   ```powershell
   $env:ABBYY_FRE_DEVELOPER_SERIAL = "<your developer serial>"
   # OR, if your license uses a file:
   $env:ABBYY_FRE_LICENSE_PATH    = "C:\path\to\license.ABBYY.LicenseKey"
   ```

5. **Record pins** in the Phase 1 report (roadmap §4): installed FRE
   build identifier, licensed language modules, image-processing settings,
   bridge build hash, and the dated deployment-specific quote.

## Build

```powershell
cd ocr-benchmarks/bridges/abbyy-worker
dotnet publish -c Release -r win-x64 --self-contained false
```

Publish output: `bin/Release/net9.0/win-x64/publish/AbbyyWorker.exe`.

Point the Python adapter at it via the config:

```yaml
- id: abbyy-fre12-heb-ara-eng
  adapter: abbyy
  params:
    lang: Hebrew,Arabic,English
    bridge_path: C:\dev\tests\malio-tests\ocr-benchmarks\bridges\abbyy-worker\bin\Release\net9.0\win-x64\publish\AbbyyWorker.exe
```

## JSON contract

Same shape as `ironocr-worker` with FRE-specific metadata keys:

```json
{
  "worker_version": "0.1.0",
  "abbyy_engine_version": "12.x.y.z",
  "engine_prog_id": "FREngine.Engine.12",
  "pages": [
    {
      "page_index": 0,
      "width": 1700,
      "height": 2200,
      "regions": [
        {"text": "...", "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
         "granularity": "word", "confidence": 0.92, "reading_order": 0}
      ]
    }
  ],
  "timings_ms": {"load_ms": 1850.0, "ocr_ms": 740.0},
  "error": null
}
```

Errors use `{"error": {"label": "...", "message": "..."}}` with exit code 1.

## COM API notes (for future wiring hardening)

The scaffold uses late-bound reflection against the FRE 12 COM object graph
documented in the ABBYY SDK (`CreateFRDocument` → `AddImageFile` →
`Process` → `Pages[i].Layout.Blocks[].GetAsTextBlock().Text.Paragraphs[]
.Lines[].Chars.Words[]`). Different SDK generations expose slightly
different property names; adjust `CollectWords()` and `TryApplyLicense()`
in `Program.cs` to match the installed build's documentation if the
scaffold returns zero regions.

## Caveats

- **Windows-only.** The scaffold refuses to run on non-Windows hosts.
- **COM thread model.** FRE wants STA. The default .NET console app
  thread is MTA; if ABBYY emits HR 0x8001010E (`RPC_E_WRONG_THREAD`),
  mark `Main` with `[STAThread]` and rebuild.
- **No per-page metering**, but deployment licenses often cap pages/month
  or cores. Record the applicable limits.
