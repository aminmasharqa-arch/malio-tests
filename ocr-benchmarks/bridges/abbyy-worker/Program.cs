// Malio OCR benchmark — ABBYY FineReader Engine 12 worker bridge.
//
// Candidate #6 in OCR_BENCHMARK_ROADMAP.md §4.
//
// Design mirrors ironocr-worker: JSON request on stdin (or arg[0] = request
// file), JSON response on stdout. Business-field extraction stays in the
// Python parser — this worker only surfaces OCR text + per-word geometry +
// confidence + metadata.
//
// FRE 12 is a licensed C++ SDK accessed on Windows via COM. We bind
// late (`Type.GetTypeFromProgID`) so the project compiles without the
// SDK installed; at run time, missing SDK or missing license surfaces as
// a BLOCKED result row in the Python harness.
//
// Request shape (JSON):
//   {
//     "pages": [{"page_index": 0, "image_path": "C:/.../page_0.png"}],
//     "languages": ["Hebrew", "English"],  // defaults to Hebrew + English
//     "options": {
//       "detect_rotation": true
//     }
//   }
//
// Response shape mirrors ironocr-worker.
//
// Licensing env vars (both optional — provide what your license uses):
//   ABBYY_FRE_DEVELOPER_SERIAL  — developer serial number string
//   ABBYY_FRE_LICENSE_PATH      — path to a .ABBYY.LicenseKey file
//
// If neither is set, FRE will attempt its default activation chain, which
// typically fails outside licensed environments → BLOCKED.

using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Malio.OcrBench.AbbyyWorker;

internal static class Program
{
    private const string WorkerVersion = "0.1.0";

    // ABBYY documents several variants depending on install generation.
    // We try each in order; the first one that creates an engine wins.
    private static readonly string[] CandidateProgIds =
    {
        "FREngine.Engine.12",
        "FREngine.Engine",
        "FREngine.OutprocLoader.12",
        "FREngine.OutprocLoader",
    };

    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;

        if (!OperatingSystem.IsWindows())
        {
            return WriteError("UNSUPPORTED_PLATFORM", "ABBYY FineReader Engine is Windows-only.");
        }

        string requestJson;
        try
        {
            requestJson = args.Length == 1 && File.Exists(args[0])
                ? File.ReadAllText(args[0], Encoding.UTF8)
                : Console.In.ReadToEnd();
        }
        catch (Exception ex)
        {
            return WriteError("REQUEST_READ_ERROR", ex.Message);
        }

        OcrRequest? request;
        try
        {
            request = JsonSerializer.Deserialize(requestJson, OcrJsonContext.Default.OcrRequest);
        }
        catch (JsonException ex)
        {
            return WriteError("REQUEST_PARSE_ERROR", ex.Message);
        }

        if (request is null || request.Pages is null || request.Pages.Count == 0)
        {
            return WriteError("REQUEST_EMPTY", "no pages in request");
        }

        var loadStopwatch = Stopwatch.StartNew();
        object? engine = null;
        string? engineProgId = null;
        string engineVersion = "unknown";
        try
        {
            (engine, engineProgId) = CreateEngine();
            if (engine is null)
            {
                return WriteError(
                    "BLOCKED",
                    "ABBYY FineReader Engine COM object not creatable. Install the licensed SDK and register FREngine, then confirm with `Type.GetTypeFromProgID(\"FREngine.Engine.12\")` from a .NET REPL."
                );
            }
            engineVersion = TryReadStringProp(engine, "Version") ?? "unknown";
            TryApplyLicense(engine);
            TryEnableLanguages(engine, request.Languages ?? new List<string> { "Hebrew", "English" });
        }
        catch (COMException comEx)
        {
            return WriteError("ENGINE_INIT_ERROR", $"COMException {comEx.HResult:X}: {comEx.Message}");
        }
        catch (Exception ex)
        {
            return WriteError("ENGINE_INIT_ERROR", ex.Message);
        }
        loadStopwatch.Stop();

        var pageResults = new List<PageOutput>(request.Pages.Count);
        var ocrStopwatch = Stopwatch.StartNew();

        try
        {
            foreach (var page in request.Pages)
            {
                if (string.IsNullOrWhiteSpace(page.ImagePath) || !File.Exists(page.ImagePath))
                {
                    return WriteError("IMAGE_NOT_FOUND", page.ImagePath ?? "<null>");
                }

                var pageOutput = RecognizeOne(engine, page);
                pageResults.Add(pageOutput);
            }
        }
        catch (COMException comEx) when (IsLicenseIssue(comEx))
        {
            return WriteError(
                "BLOCKED",
                $"ABBYY FineReader Engine license required or expired (COM HRESULT 0x{comEx.HResult:X}). " +
                "Set ABBYY_FRE_DEVELOPER_SERIAL or ABBYY_FRE_LICENSE_PATH and verify the SDK installation."
            );
        }
        catch (COMException comEx)
        {
            return WriteError("OCR_FAILURE", $"COMException {comEx.HResult:X}: {comEx.Message}");
        }
        catch (Exception ex)
        {
            return WriteError("OCR_FAILURE", ex.Message);
        }
        finally
        {
            ReleaseCom(engine);
        }
        ocrStopwatch.Stop();

        var response = new OcrResponse
        {
            WorkerVersion = WorkerVersion,
            AbbyyEngineVersion = engineVersion,
            EngineProgId = engineProgId,
            Pages = pageResults,
            TimingsMs = new TimingsOutput
            {
                LoadMs = loadStopwatch.Elapsed.TotalMilliseconds,
                OcrMs = ocrStopwatch.Elapsed.TotalMilliseconds,
            },
            Error = null,
        };
        Console.Out.Write(JsonSerializer.Serialize(response, OcrJsonContext.Default.OcrResponse));
        return 0;
    }

    private static (object? engine, string? progId) CreateEngine()
    {
        foreach (var progId in CandidateProgIds)
        {
            Type? type;
            try
            {
                type = Type.GetTypeFromProgID(progId, throwOnError: false);
            }
            catch
            {
                continue;
            }
            if (type is null)
            {
                continue;
            }
            try
            {
                var engine = Activator.CreateInstance(type);
                if (engine is not null)
                {
                    return (engine, progId);
                }
            }
            catch
            {
                // try next ProgID
            }
        }
        return (null, null);
    }

    private static void TryApplyLicense(object engine)
    {
        var devSerial = Environment.GetEnvironmentVariable("ABBYY_FRE_DEVELOPER_SERIAL");
        var licensePath = Environment.GetEnvironmentVariable("ABBYY_FRE_LICENSE_PATH");

        // FRE licensing API variants:
        //   engine.LoadPredefinedProfile(...)
        //   engine.SetPredefinedProcessingProfile(...)
        //   engine.SetCurrentLicense(path, serial)
        //   engine.LoadLicense(...)
        // We invoke whichever is present. The scaffold is permissive —
        // leave sophisticated licensing policy to the user who holds the SDK.

        if (!string.IsNullOrWhiteSpace(licensePath) && !string.IsNullOrWhiteSpace(devSerial))
        {
            TryInvoke(engine, "SetCurrentLicense", licensePath, devSerial);
            return;
        }
        if (!string.IsNullOrWhiteSpace(devSerial))
        {
            TryInvoke(engine, "SetCurrentLicense", "", devSerial);
        }
    }

    private static void TryEnableLanguages(object engine, List<string> languages)
    {
        // Older FRE: engine.CreateLanguage("Hebrew") → ILanguage, then
        // batch.Languages.Add(language). Modern FRE: pass language names
        // as a string to the FRPage/FRDocument when calling Recognize.
        //
        // We keep this scaffold permissive: just attempt to set a global
        // "RecognizedLanguages" property if available. The real wiring
        // depends on the installed FRE build and must be validated against
        // the SDK documentation.

        var langString = string.Join(',', languages.Select(NormalizeLanguage));
        TrySetProperty(engine, "RecognizedLanguages", langString);
    }

    private static PageOutput RecognizeOne(object engine, PageRequest page)
    {
        // FRE 12 typical flow (details vary by SDK build — adapt to your
        // installed documentation):
        //
        //   var document = engine.CreateFRDocument();
        //   document.AddImageFile(path, null, null);
        //   document.Process(null);
        //   foreach (var frPage in document.Pages) {
        //       var layout = frPage.Layout;
        //       foreach (var block in layout.Blocks) {
        //           if (block.Type == BT_Text) {
        //               var textBlock = block.GetAsTextBlock();
        //               foreach (var para in textBlock.Text.Paragraphs)
        //                   foreach (var line in para.Lines)
        //                       foreach (var word in line.Chars.Words)
        //                           emit(word.Text, word.Rect, word.Confidence);
        //           }
        //       }
        //   }
        //
        // We call into these via late binding so this file builds without
        // the SDK. The scaffold returns an empty region list if the
        // installed FRE doesn't expose the expected shape; failures bubble
        // up as COMExceptions handled by Main.

        object? document = null;
        try
        {
            document = TryInvoke(engine, "CreateFRDocument");
            if (document is null)
            {
                return new PageOutput { PageIndex = page.PageIndex, Width = 0, Height = 0, Regions = new List<RegionOutput>() };
            }

            TryInvoke(document, "AddImageFile", page.ImagePath!, null, null);
            TryInvoke(document, "Process", null);

            int order = 0;
            var regions = new List<RegionOutput>();
            int width = 0, height = 0;

            var pages = TryGetProperty(document, "Pages");
            if (pages is null)
            {
                return new PageOutput { PageIndex = page.PageIndex, Width = 0, Height = 0, Regions = regions };
            }

            // COM collections typically expose `Count` and an indexer `Item(i)`.
            int count = Convert.ToInt32(TryGetProperty(pages, "Count") ?? 0, CultureInfo.InvariantCulture);
            if (count == 0)
            {
                return new PageOutput { PageIndex = page.PageIndex, Width = 0, Height = 0, Regions = regions };
            }

            var frPage = TryInvoke(pages, "Item", 0);
            if (frPage is null)
            {
                return new PageOutput { PageIndex = page.PageIndex, Width = 0, Height = 0, Regions = regions };
            }

            width = Convert.ToInt32(TryGetProperty(frPage, "ImageWidth") ?? 0, CultureInfo.InvariantCulture);
            height = Convert.ToInt32(TryGetProperty(frPage, "ImageHeight") ?? 0, CultureInfo.InvariantCulture);

            CollectWords(frPage, regions, ref order);

            return new PageOutput
            {
                PageIndex = page.PageIndex,
                Width = width,
                Height = height,
                Regions = regions,
            };
        }
        finally
        {
            if (document is not null)
            {
                TryInvoke(document, "Close");
                ReleaseCom(document);
            }
        }
    }

    private static void CollectWords(object frPage, List<RegionOutput> regions, ref int order)
    {
        var layout = TryGetProperty(frPage, "Layout");
        var blocks = layout is null ? null : TryGetProperty(layout, "Blocks");
        if (blocks is null)
        {
            return;
        }

        int blockCount = Convert.ToInt32(TryGetProperty(blocks, "Count") ?? 0, CultureInfo.InvariantCulture);
        for (int b = 0; b < blockCount; b++)
        {
            var block = TryInvoke(blocks, "Item", b);
            if (block is null) continue;
            var textBlock = TryInvoke(block, "GetAsTextBlock") ?? block;
            var text = TryGetProperty(textBlock, "Text");
            if (text is null) continue;
            var paragraphs = TryGetProperty(text, "Paragraphs");
            if (paragraphs is null) continue;
            int pCount = Convert.ToInt32(TryGetProperty(paragraphs, "Count") ?? 0, CultureInfo.InvariantCulture);
            for (int p = 0; p < pCount; p++)
            {
                var para = TryInvoke(paragraphs, "Item", p);
                var lines = para is null ? null : TryGetProperty(para, "Lines");
                if (lines is null) continue;
                int lCount = Convert.ToInt32(TryGetProperty(lines, "Count") ?? 0, CultureInfo.InvariantCulture);
                for (int l = 0; l < lCount; l++)
                {
                    var line = TryInvoke(lines, "Item", l);
                    var chars = line is null ? null : TryGetProperty(line, "Chars");
                    if (chars is null) continue;
                    var words = TryGetProperty(chars, "Words") ?? TryGetProperty(line, "Words");
                    if (words is null) continue;
                    int wCount = Convert.ToInt32(TryGetProperty(words, "Count") ?? 0, CultureInfo.InvariantCulture);
                    for (int w = 0; w < wCount; w++)
                    {
                        var word = TryInvoke(words, "Item", w);
                        if (word is null) continue;
                        var wText = TryGetProperty(word, "Text") as string;
                        if (string.IsNullOrWhiteSpace(wText)) continue;
                        var rect = TryGetProperty(word, "Rect");
                        var polygon = RectToPolygon(rect);
                        var confidence = NormalizeConfidence(TryGetProperty(word, "Confidence"));
                        regions.Add(new RegionOutput
                        {
                            Text = wText,
                            Polygon = polygon,
                            Granularity = "word",
                            Confidence = confidence,
                            ReadingOrder = order++,
                        });
                    }
                }
            }
        }
    }

    private static double[][]? RectToPolygon(object? rect)
    {
        if (rect is null) return null;
        try
        {
            double left = Convert.ToDouble(TryGetProperty(rect, "Left") ?? 0.0, CultureInfo.InvariantCulture);
            double top = Convert.ToDouble(TryGetProperty(rect, "Top") ?? 0.0, CultureInfo.InvariantCulture);
            double right = Convert.ToDouble(TryGetProperty(rect, "Right") ?? 0.0, CultureInfo.InvariantCulture);
            double bottom = Convert.ToDouble(TryGetProperty(rect, "Bottom") ?? 0.0, CultureInfo.InvariantCulture);
            return new[]
            {
                new[] { left,  top },
                new[] { right, top },
                new[] { right, bottom },
                new[] { left,  bottom },
            };
        }
        catch
        {
            return null;
        }
    }

    private static double? NormalizeConfidence(object? raw)
    {
        if (raw is null) return null;
        try
        {
            double c = Convert.ToDouble(raw, CultureInfo.InvariantCulture);
            if (c < 0) return null;
            return c > 1.0 ? c / 100.0 : c;
        }
        catch
        {
            return null;
        }
    }

    private static bool IsLicenseIssue(COMException ex) =>
        ex.Message.Contains("license", StringComparison.OrdinalIgnoreCase)
        || ex.Message.Contains("protection", StringComparison.OrdinalIgnoreCase)
        || ex.Message.Contains("activation", StringComparison.OrdinalIgnoreCase);

    private static string NormalizeLanguage(string name) =>
        name.Trim().ToLowerInvariant() switch
        {
            "hebrew" or "heb" => "Hebrew",
            "arabic" or "ara" => "Arabic",
            "english" or "eng" => "English",
            _ => name,
        };

    private static object? TryGetProperty(object com, string name)
    {
        try
        {
            return com.GetType().InvokeMember(name,
                BindingFlags.GetProperty | BindingFlags.Instance | BindingFlags.Public,
                binder: null, target: com, args: null);
        }
        catch (MissingMemberException)
        {
            return null;
        }
    }

    private static string? TryReadStringProp(object com, string name) => TryGetProperty(com, name) as string;

    private static object? TryInvoke(object com, string name, params object?[]? args)
    {
        try
        {
            return com.GetType().InvokeMember(name,
                BindingFlags.InvokeMethod | BindingFlags.Instance | BindingFlags.Public,
                binder: null, target: com, args: args);
        }
        catch (MissingMemberException)
        {
            return null;
        }
    }

    private static void TrySetProperty(object com, string name, object? value)
    {
        try
        {
            com.GetType().InvokeMember(name,
                BindingFlags.SetProperty | BindingFlags.Instance | BindingFlags.Public,
                binder: null, target: com, args: new[] { value });
        }
        catch (MissingMemberException)
        {
            // not supported on this FRE build — ignore
        }
    }

    private static void ReleaseCom(object? com)
    {
        if (com is null) return;
        try
        {
            if (Marshal.IsComObject(com))
            {
                Marshal.ReleaseComObject(com);
            }
        }
        catch { /* best effort */ }
    }

    private static int WriteError(string label, string message)
    {
        var response = new OcrResponse
        {
            WorkerVersion = WorkerVersion,
            AbbyyEngineVersion = null,
            EngineProgId = null,
            Pages = new List<PageOutput>(),
            TimingsMs = new TimingsOutput(),
            Error = new ErrorOutput { Label = label, Message = message },
        };
        Console.Out.Write(JsonSerializer.Serialize(response, OcrJsonContext.Default.OcrResponse));
        return 1;
    }
}

internal sealed class OcrRequest
{
    [JsonPropertyName("pages")] public List<PageRequest>? Pages { get; set; }
    [JsonPropertyName("languages")] public List<string>? Languages { get; set; }
    [JsonPropertyName("options")] public OcrOptions? Options { get; set; }
}

internal sealed class PageRequest
{
    [JsonPropertyName("page_index")] public int PageIndex { get; set; }
    [JsonPropertyName("image_path")] public string? ImagePath { get; set; }
}

internal sealed class OcrOptions
{
    [JsonPropertyName("detect_rotation")] public bool? DetectRotation { get; set; }
}

internal sealed class OcrResponse
{
    [JsonPropertyName("worker_version")] public string? WorkerVersion { get; set; }
    [JsonPropertyName("abbyy_engine_version")] public string? AbbyyEngineVersion { get; set; }
    [JsonPropertyName("engine_prog_id")] public string? EngineProgId { get; set; }
    [JsonPropertyName("pages")] public List<PageOutput>? Pages { get; set; }
    [JsonPropertyName("timings_ms")] public TimingsOutput? TimingsMs { get; set; }
    [JsonPropertyName("error")] public ErrorOutput? Error { get; set; }
}

internal sealed class PageOutput
{
    [JsonPropertyName("page_index")] public int PageIndex { get; set; }
    [JsonPropertyName("width")] public int Width { get; set; }
    [JsonPropertyName("height")] public int Height { get; set; }
    [JsonPropertyName("regions")] public List<RegionOutput>? Regions { get; set; }
}

internal sealed class RegionOutput
{
    [JsonPropertyName("text")] public string? Text { get; set; }
    [JsonPropertyName("polygon")] public double[][]? Polygon { get; set; }
    [JsonPropertyName("granularity")] public string? Granularity { get; set; }
    [JsonPropertyName("confidence")] public double? Confidence { get; set; }
    [JsonPropertyName("reading_order")] public int? ReadingOrder { get; set; }
}

internal sealed class TimingsOutput
{
    [JsonPropertyName("load_ms")] public double LoadMs { get; set; }
    [JsonPropertyName("ocr_ms")] public double OcrMs { get; set; }
}

internal sealed class ErrorOutput
{
    [JsonPropertyName("label")] public string? Label { get; set; }
    [JsonPropertyName("message")] public string? Message { get; set; }
}

[JsonSerializable(typeof(OcrRequest))]
[JsonSerializable(typeof(OcrResponse))]
[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    WriteIndented = false)]
internal partial class OcrJsonContext : JsonSerializerContext { }
