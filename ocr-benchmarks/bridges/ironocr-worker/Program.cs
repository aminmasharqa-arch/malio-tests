// Malio OCR benchmark — IronOCR worker bridge.
//
// Candidate #5 in OCR_BENCHMARK_ROADMAP.md §4.
//
// Contract: the worker reads a JSON request from stdin, runs IronOcr with
// pinned languages/filters, and writes a JSON response to stdout. All
// business-field extraction stays in the Python parser — this worker only
// surfaces OCR text + per-word geometry + confidence + metadata.
//
// Request shape (JSON):
//   {
//     "pages": [
//       {"page_index": 0, "image_path": "C:/.../page_0.png"},
//       {"page_index": 1, "image_path": "C:/.../page_1.png"}
//     ],
//     "languages": ["Hebrew", "English"],       // optional, defaults below
//     "options": {                                // optional
//       "read_barcodes": false,
//       "detect_rotation": true
//     }
//   }
//
// Response shape (JSON):
//   {
//     "worker_version": "...",
//     "ironocr_version": "...",
//     "pages": [
//       {
//         "page_index": 0,
//         "width": 1234,
//         "height": 2345,
//         "regions": [
//           {
//             "text": "...",
//             "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
//             "granularity": "word" | "line",
//             "confidence": 0.95,
//             "reading_order": 0
//           }
//         ]
//       }
//     ],
//     "timings_ms": {"load_ms": 123.0, "ocr_ms": 456.0},
//     "error": null | {"label": "...", "message": "..."}
//   }
//
// Licensing: set IRONOCR_LICENSE_KEY before invoking. The worker does not
// read license keys from the request payload.

using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using IronOcr;

namespace Malio.OcrBench.IronOcrWorker;

internal static class Program
{
    private const string WorkerVersion = "0.1.0";

    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;

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

        var licenseKey = Environment.GetEnvironmentVariable("IRONOCR_LICENSE_KEY");
        if (!string.IsNullOrWhiteSpace(licenseKey))
        {
            // IronOcr.License.LicenseKey is the documented licensing entry point.
            License.LicenseKey = licenseKey;
        }

        var loadStopwatch = Stopwatch.StartNew();
        IronTesseract ocr;
        try
        {
            ocr = BuildTesseract(request);
        }
        catch (Exception ex)
        {
            return WriteError("ENGINE_INIT_ERROR", ex.Message);
        }
        loadStopwatch.Stop();

        var ocrStopwatch = Stopwatch.StartNew();
        var pageResults = new List<PageOutput>(request.Pages.Count);
        try
        {
            foreach (var page in request.Pages)
            {
                if (string.IsNullOrWhiteSpace(page.ImagePath) || !File.Exists(page.ImagePath))
                {
                    return WriteError("IMAGE_NOT_FOUND", page.ImagePath ?? "<null>");
                }

                using var input = new OcrInput();
                input.LoadImage(page.ImagePath);
                var result = ocr.Read(input);
                pageResults.Add(ExtractPage(page.PageIndex, result));
            }
        }
        catch (Exception ex)
        {
            return WriteError("OCR_FAILURE", ex.Message);
        }
        ocrStopwatch.Stop();

        var response = new OcrResponse
        {
            WorkerVersion = WorkerVersion,
            IronOcrVersion = typeof(IronTesseract).Assembly.GetName().Version?.ToString() ?? "unknown",
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

    private static IronTesseract BuildTesseract(OcrRequest request)
    {
        var ocr = new IronTesseract();

        // Primary language and additional languages. We default to Hebrew +
        // English for Malio's invoice workload; the request can override.
        var languages = request.Languages is { Count: > 0 }
            ? request.Languages
            : new List<string> { "Hebrew", "English" };

        ocr.Language = ParseLanguage(languages[0]);
        for (int i = 1; i < languages.Count; i++)
        {
            try
            {
                ocr.AddSecondaryLanguage(ParseLanguage(languages[i]));
            }
            catch (Exception)
            {
                // Secondary language unavailable — skip, main language still works.
            }
        }

        if (request.Options is not null)
        {
            // Only turn on features that IronOcr exposes stably; the roadmap
            // flags filters as part of the pinned configuration.
            if (request.Options.ReadBarcodes.HasValue)
            {
                ocr.Configuration.ReadBarCodes = request.Options.ReadBarcodes.Value;
            }
        }

        return ocr;
    }

    private static OcrLanguage ParseLanguage(string name) =>
        name.Trim().ToLowerInvariant() switch
        {
            "hebrew" or "heb" => OcrLanguage.Hebrew,
            "arabic" or "ara" => OcrLanguage.Arabic,
            "english" or "eng" => OcrLanguage.English,
            _ => throw new ArgumentException($"unsupported language: {name}"),
        };

    private static PageOutput ExtractPage(int pageIndex, OcrResult result)
    {
        var firstPage = result.Pages?.FirstOrDefault();
        int width = (int)Math.Round(firstPage?.Width ?? 0.0);
        int height = (int)Math.Round(firstPage?.Height ?? 0.0);
        var regions = new List<RegionOutput>();
        int order = 0;

        if (firstPage?.Words is { } words)
        {
            foreach (var w in words)
            {
                if (string.IsNullOrWhiteSpace(w.Text))
                {
                    continue;
                }
                regions.Add(new RegionOutput
                {
                    Text = w.Text,
                    Polygon = BoundingBoxToPolygon(w.X, w.Y, w.Width, w.Height),
                    Granularity = "word",
                    Confidence = NormalizeConfidence(w.Confidence),
                    ReadingOrder = order++,
                });
            }
        }

        if (regions.Count == 0 && firstPage?.Lines is { } lines)
        {
            // Fall back to line-granularity so the Python parser still gets text.
            foreach (var line in lines)
            {
                if (string.IsNullOrWhiteSpace(line.Text))
                {
                    continue;
                }
                regions.Add(new RegionOutput
                {
                    Text = line.Text,
                    Polygon = BoundingBoxToPolygon(line.X, line.Y, line.Width, line.Height),
                    Granularity = "line",
                    Confidence = NormalizeConfidence(line.Confidence),
                    ReadingOrder = order++,
                });
            }
        }

        return new PageOutput
        {
            PageIndex = pageIndex,
            Width = width,
            Height = height,
            Regions = regions,
        };
    }

    private static double[][] BoundingBoxToPolygon(int x, int y, int width, int height) => new[]
    {
        new[] { (double)x,         (double)y },
        new[] { (double)x + width, (double)y },
        new[] { (double)x + width, (double)y + height },
        new[] { (double)x,         (double)y + height },
    };

    private static double NormalizeConfidence(double raw) =>
        raw > 1.0 ? raw / 100.0 : raw;

    private static int WriteError(string label, string message)
    {
        var response = new OcrResponse
        {
            WorkerVersion = WorkerVersion,
            IronOcrVersion = TryGetIronOcrVersion(),
            Pages = new List<PageOutput>(),
            TimingsMs = new TimingsOutput(),
            Error = new ErrorOutput { Label = label, Message = message },
        };
        Console.Out.Write(JsonSerializer.Serialize(response, OcrJsonContext.Default.OcrResponse));
        return 1;
    }

    private static string TryGetIronOcrVersion()
    {
        try { return typeof(IronTesseract).Assembly.GetName().Version?.ToString() ?? "unknown"; }
        catch { return "unknown"; }
    }
}

internal sealed class OcrRequest
{
    [JsonPropertyName("pages")]
    public List<PageRequest>? Pages { get; set; }

    [JsonPropertyName("languages")]
    public List<string>? Languages { get; set; }

    [JsonPropertyName("options")]
    public OcrOptions? Options { get; set; }
}

internal sealed class PageRequest
{
    [JsonPropertyName("page_index")]
    public int PageIndex { get; set; }

    [JsonPropertyName("image_path")]
    public string? ImagePath { get; set; }
}

internal sealed class OcrOptions
{
    [JsonPropertyName("read_barcodes")]
    public bool? ReadBarcodes { get; set; }

    [JsonPropertyName("detect_rotation")]
    public bool? DetectRotation { get; set; }
}

internal sealed class OcrResponse
{
    [JsonPropertyName("worker_version")]
    public string? WorkerVersion { get; set; }

    [JsonPropertyName("ironocr_version")]
    public string? IronOcrVersion { get; set; }

    [JsonPropertyName("pages")]
    public List<PageOutput>? Pages { get; set; }

    [JsonPropertyName("timings_ms")]
    public TimingsOutput? TimingsMs { get; set; }

    [JsonPropertyName("error")]
    public ErrorOutput? Error { get; set; }
}

internal sealed class PageOutput
{
    [JsonPropertyName("page_index")]
    public int PageIndex { get; set; }

    [JsonPropertyName("width")]
    public int Width { get; set; }

    [JsonPropertyName("height")]
    public int Height { get; set; }

    [JsonPropertyName("regions")]
    public List<RegionOutput>? Regions { get; set; }
}

internal sealed class RegionOutput
{
    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("polygon")]
    public double[][]? Polygon { get; set; }

    [JsonPropertyName("granularity")]
    public string? Granularity { get; set; }

    [JsonPropertyName("confidence")]
    public double? Confidence { get; set; }

    [JsonPropertyName("reading_order")]
    public int? ReadingOrder { get; set; }
}

internal sealed class TimingsOutput
{
    [JsonPropertyName("load_ms")]
    public double LoadMs { get; set; }

    [JsonPropertyName("ocr_ms")]
    public double OcrMs { get; set; }
}

internal sealed class ErrorOutput
{
    [JsonPropertyName("label")]
    public string? Label { get; set; }

    [JsonPropertyName("message")]
    public string? Message { get; set; }
}

[JsonSerializable(typeof(OcrRequest))]
[JsonSerializable(typeof(OcrResponse))]
[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    WriteIndented = false)]
internal partial class OcrJsonContext : JsonSerializerContext { }
