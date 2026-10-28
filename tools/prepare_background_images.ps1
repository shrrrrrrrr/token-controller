param(
  [Parameter(Mandatory=$true)][string]$SourceDirectory,
  [Parameter(Mandatory=$true)][string]$DestinationDirectory,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
$destinationRoot = [IO.Path]::GetFullPath($DestinationDirectory)
if ($destinationRoot.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or $destinationRoot.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be outside the original gallery' }
if (Test-Path -LiteralPath $destinationRoot) { throw 'Output directory already exists; preserved' }
$photos = @(Get-ChildItem -LiteralPath $sourceRoot -File | Where-Object { $_.Extension.ToLowerInvariant() -in @('.jpg','.jpeg','.png') })
if ($photos.Count -lt 1 -or $photos.Count -gt 100) { throw 'Expected 1 to 100 photos' }
if (-not $Apply) { Write-Output ('PREPARATION_PLAN images=' + $photos.Count); exit 0 }
New-Item -ItemType Directory -Path $destinationRoot | Out-Null
$thumbRoot = Join-Path $destinationRoot 'thumbnails'
New-Item -ItemType Directory -Path $thumbRoot | Out-Null
$jpegEncoder = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg' | Select-Object -First 1
function Save-WebPhoto($image, [string]$target, [int]$maxEdge) {
  if (Test-Path -LiteralPath $target) { throw 'Duplicate output name; preserved' }
  $ratio = [Math]::Min(1.0, $maxEdge / [double][Math]::Max($image.Width,$image.Height))
  $width = [Math]::Max(1,[int][Math]::Round($image.Width*$ratio))
  $height = [Math]::Max(1,[int][Math]::Round($image.Height*$ratio))
  $bitmap = New-Object Drawing.Bitmap($width,$height)
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $parameters = New-Object Drawing.Imaging.EncoderParameters(1)
  try {
    $graphics.Clear([Drawing.Color]::White)
    $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.DrawImage($image,0,0,$width,$height)
    $parameters.Param[0] = New-Object Drawing.Imaging.EncoderParameter([Drawing.Imaging.Encoder]::Quality, [long]88)
    $bitmap.Save($target,$jpegEncoder,$parameters)
  } finally { $parameters.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
}
foreach ($photo in $photos) {
  if (($photo.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Linked source photo is not accepted' }
  $image = [Drawing.Image]::FromFile($photo.FullName)
  try {
    # Deliver upright web copies, without changing the original EXIF metadata.
    if ($image.PropertyIdList -contains 274) {
      $orientation = [BitConverter]::ToUInt16($image.GetPropertyItem(274).Value,0)
      switch ($orientation) {
        2 { $image.RotateFlip([Drawing.RotateFlipType]::RotateNoneFlipX) }
        3 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate180FlipNone) }
        4 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate180FlipX) }
        5 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate90FlipX) }
        6 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate90FlipNone) }
        7 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate270FlipX) }
        8 { $image.RotateFlip([Drawing.RotateFlipType]::Rotate270FlipNone) }
      }
    }
    $name = [IO.Path]::GetFileNameWithoutExtension($photo.Name)+'.jpg'
    Save-WebPhoto $image (Join-Path $destinationRoot $name) 1920
    Save-WebPhoto $image (Join-Path $thumbRoot $name) 480
  } finally { $image.Dispose() }
}
Write-Output ('WEB_BACKGROUNDS_READY images=' + $photos.Count + ' originals_modified=false')
