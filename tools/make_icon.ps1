Add-Type -AssemblyName System.Drawing

$size = 256
$bmp = New-Object System.Drawing.Bitmap -ArgumentList @($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

# Rounded square background
$rect = New-Object System.Drawing.RectangleF -ArgumentList @(8, 8, 240, 240)
$radius = 54
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$d = $radius * 2
$path.AddArc($rect.X, $rect.Y, $d, $d, 180, 90)
$path.AddArc($rect.Right - $d, $rect.Y, $d, $d, 270, 90)
$path.AddArc($rect.Right - $d, $rect.Bottom - $d, $d, $d, 0, 90)
$path.AddArc($rect.X, $rect.Bottom - $d, $d, $d, 90, 90)
$path.CloseFigure()

$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush -ArgumentList @(
    $rect,
    [System.Drawing.Color]::FromArgb(255, 37, 99, 235),
    [System.Drawing.Color]::FromArgb(255, 14, 165, 233),
    45.0
)
$g.FillPath($brush, $path)

# Inner light overlay
$inner = New-Object System.Drawing.RectangleF -ArgumentList @(26, 26, 204, 204)
$innerPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$ir = 40
$id = $ir * 2
$innerPath.AddArc($inner.X, $inner.Y, $id, $id, 180, 90)
$innerPath.AddArc($inner.Right - $id, $inner.Y, $id, $id, 270, 90)
$innerPath.AddArc($inner.Right - $id, $inner.Bottom - $id, $id, $id, 0, 90)
$innerPath.AddArc($inner.X, $inner.Bottom - $id, $id, $id, 90, 90)
$innerPath.CloseFigure()
$innerBrush = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::FromArgb(32, 255, 255, 255))
$g.FillPath($innerBrush, $innerPath)

# White lightning bolt
$bolt = New-Object 'System.Drawing.Point[]' 6
$bolt[0] = New-Object System.Drawing.Point -ArgumentList @(146, 38)
$bolt[1] = New-Object System.Drawing.Point -ArgumentList @(72, 140)
$bolt[2] = New-Object System.Drawing.Point -ArgumentList @(120, 140)
$bolt[3] = New-Object System.Drawing.Point -ArgumentList @(104, 220)
$bolt[4] = New-Object System.Drawing.Point -ArgumentList @(184, 112)
$bolt[5] = New-Object System.Drawing.Point -ArgumentList @(134, 112)
$white = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::White)
$g.FillPolygon($white, $bolt)

$g.Dispose()

$pngPath = Join-Path $env:TEMP 'smartroute-icon.png'
$bmp.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

# Wrap the PNG in a 256x256 ICO container
$png = [System.IO.File]::ReadAllBytes($pngPath)
$icoPath = Join-Path $PSScriptRoot '..\assets\app.ico'
$icoPath = [System.IO.Path]::GetFullPath($icoPath)
New-Item -ItemType Directory -Force -Path (Split-Path $icoPath) | Out-Null
$stream = New-Object System.IO.MemoryStream
$writer = New-Object System.IO.BinaryWriter -ArgumentList $stream
$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]1)
$writer.Write([Byte]0)
$writer.Write([Byte]0)
$writer.Write([Byte]0)
$writer.Write([Byte]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]32)
$writer.Write([UInt32]$png.Length)
$writer.Write([UInt32]22)
$writer.Write($png)
$writer.Flush()
[System.IO.File]::WriteAllBytes($icoPath, $stream.ToArray())
$writer.Dispose()
$stream.Dispose()

Write-Output "ICO $icoPath $((Get-Item $icoPath).Length) bytes"
