param(
    [Parameter(Mandatory=$true)]
    [string]$ImagePath
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapTransform, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

$resolvedPath = (Resolve-Path $ImagePath).Path
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedPath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

$w = $decoder.PixelWidth
$h = $decoder.PixelHeight

function Get-ScaledBitmap($Scale) {
    if ($Scale -eq 1.0) {
        return Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }
    $tr = [Windows.Graphics.Imaging.BitmapTransform]::new()
    $tr.ScaledWidth = [uint32][Math]::Max(50, [int]($w * $Scale))
    $tr.ScaledHeight = [uint32][Math]::Max(50, [int]($h * $Scale))
    return Await ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
        $tr,
        [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
        [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
    )) ([Windows.Graphics.Imaging.SoftwareBitmap])
}

$engEn = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new("en-US"))
$engAr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new("ar-SA"))

$enLines = @()
$arLines = @()

foreach ($s in @(1.0, 0.35, 2.0)) {
    $bmp = Get-ScaledBitmap $s
    if ($s -eq 1.0 -or $s -eq 2.0) {
        $resEn = Await ($engEn.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        foreach ($l in $resEn.Lines) { $enLines += $l.Text }
    }
    $resAr = Await ($engAr.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    foreach ($l in $resAr.Lines) { $arLines += $l.Text }
}

$stream.Dispose()

$result = @{
    en_lines = $enLines
    ar_lines = $arLines
    en_text  = ($enLines -join "`n")
    ar_text  = ($arLines -join "`n")
}

$result | ConvertTo-Json -Depth 4 -Compress
