#Requires -Version 5.1
param([string]$Name, [string]$ExpectedHash)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'VisualForge: источник зависимости'
$form.Size = New-Object System.Drawing.Size(640,340)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.Font = New-Object System.Drawing.Font('Segoe UI',10)
$label = New-Object System.Windows.Forms.Label
$label.SetBounds(16,12,590,80)
$label.Text = "Не удалось скачать: $Name`r`nУкажите прямую ссылку или файл той же версии.`r`nРазмер и SHA-256 будут проверены."
$form.Controls.Add($label)
$inputBox = New-Object System.Windows.Forms.TextBox
$inputBox.SetBounds(16,100,590,28)
$form.Controls.Add($inputBox)
$state = @{answer=$null; deadline=[DateTime]::UtcNow.AddSeconds(30)}
$url = New-Object System.Windows.Forms.Button
$url.Text = 'Скачать по ссылке'; $url.SetBounds(16,145,150,36)
$url.Add_Click({if ($inputBox.Text.Trim()) {$state.answer=@{kind='url';value=$inputBox.Text.Trim()};$form.Close()}})
$local = New-Object System.Windows.Forms.Button
$local.Text = 'Использовать файл'; $local.SetBounds(180,145,150,36)
$local.Add_Click({if ($inputBox.Text.Trim()) {$state.answer=@{kind='file';value=$inputBox.Text.Trim()};$form.Close()}})
$browse = New-Object System.Windows.Forms.Button
$browse.Text = 'Выбрать файл…'; $browse.SetBounds(345,145,150,36)
$browse.Add_Click({
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    try {
        if ($picker.ShowDialog($form) -eq 'OK' -and [DateTime]::UtcNow -lt $state.deadline) {
            $state.answer=@{kind='file';value=$picker.FileName};$form.Close()
        }
    } finally {$picker.Dispose()}
})
$wait = New-Object System.Windows.Forms.Label
$wait.SetBounds(16,195,590,30)
$skip = New-Object System.Windows.Forms.Button
$skip.Text = 'Пропустить'; $skip.SetBounds(16,235,150,36)
$skip.Add_Click({$form.Close()})
$form.Controls.AddRange(@($url,$local,$browse,$wait,$skip))
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$timer.Add_Tick({
    $left = [Math]::Max(0,[Math]::Ceiling(($state.deadline-[DateTime]::UtcNow).TotalSeconds))
    $wait.Text = "Осталось $left с. Затем файл будет пропущен."
    if ($left -le 0) {$form.Close()}
})
try {
    $timer.Start()
    [void]$form.ShowDialog()
    if ($null -ne $state.answer) {$state.answer | ConvertTo-Json -Compress}
} finally {$timer.Stop();$timer.Dispose();$form.Dispose()}
