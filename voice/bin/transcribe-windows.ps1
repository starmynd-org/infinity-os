<#
  Transcribe one wav with the recogniser that is already on this machine.

  System.Speech.Recognition, the .NET desktop speech stack that ships with Windows. On this host
  `InstalledRecognizers()` returns exactly one: MS-1033-80-DESK, en-US. It is LOCAL, it is FREE,
  it sends nothing anywhere, and it is not very good -- measured 2026-08-18 on clean synthesised
  speech it scored 0.646 mean confidence and turned "and the file saved as zero bytes" into "in
  the file suit to 0 B". Both halves of that matter: it is a real baseline that costs nothing and
  leaks nothing, and it is bad enough that the confidence number has to travel with the text.

  IT NEVER GUESSES. `Recognize()` returning $null means the recogniser could not make out speech,
  and this script reports zero segments rather than inventing a plausible sentence. The caller
  turns that into transcription_status='null' and an EMPTY body, which the store's CHECK then
  enforces. D3 measured the rule on stated_goal: a fabricated goal is worse than a null one,
  because it will be believed later.

  Output is one JSON object on stdout, so a partial write cannot be read as a complete result.

  Usage:
    transcribe-windows.ps1 -WavFile C:\path\to\x.wav
#>
param(
  [Parameter(Mandatory=$true)][string]$WavFile
)

$ErrorActionPreference = 'Stop'

function Fail($stage, $msg) {
  $o = [ordered]@{ engine = 'windows-sapi'; status = 'unavailable'; text = '';
                   segments = 0; mean_confidence = $null; stage = $stage; error = $msg }
  Write-Output ($o | ConvertTo-Json -Compress)
  exit 3
}

if (-not (Test-Path $WavFile)) { Fail 'input' "no such wav file: $WavFile" }

try { Add-Type -AssemblyName System.Speech }
catch { Fail 'assembly' ("System.Speech is not loadable on this host: " + $_.Exception.Message) }

try {
  $installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
} catch {
  Fail 'recognizers' ("InstalledRecognizers() raised: " + $_.Exception.Message)
}
if ($installed.Count -lt 1) {
  # `unavailable`, not `null`. No engine ran at all, which is a different act for the operator
  # than an engine that ran and heard nothing: this one is "install a recogniser", the other is
  # "re-record, the room was too loud".
  Fail 'recognizers' 'no speech recognizer is installed on this Windows host'
}

$engineId = $installed[0].Id

try {
  $eng = New-Object System.Speech.Recognition.SpeechRecognitionEngine($installed[0])
  $eng.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $eng.SetInputToWaveFile($WavFile)
} catch {
  Fail 'open' ("could not open $WavFile with $engineId : " + $_.Exception.Message)
}

$sb = New-Object System.Text.StringBuilder
$confidences = New-Object System.Collections.Generic.List[double]
$segments = 0
$partial = $false
$endOfStream = $false

try {
  while ($true) {
    $res = $eng.Recognize()
    if ($null -eq $res) { break }          # end of audio, or nothing recognisable left in it
    $segments++
    $confidences.Add([double]$res.Confidence)
    [void]$sb.Append($res.Text)
    [void]$sb.Append(' ')
  }
} catch {
  # TWO DIFFERENT THINGS ARRIVE HERE and calling them both `partial` was wrong.
  #
  # The first is the END OF THE WAV. Once the file is exhausted the engine's input is detached
  # and the next Recognize() throws "No audio input is supplied to this recognizer". That is
  # normal termination -- measured on the first real capture, 2026-08-18 -- and marking a
  # complete transcript `partial` because of it understates every good result this engine gives.
  #
  # The second is a genuine mid-file failure, and THAT is partial: whatever was recognised is
  # kept, because throwing away real speech because the tail failed loses content, and reported
  # as incomplete, because calling it whole claims coverage the engine did not deliver.
  $msg = $_.Exception.Message
  if ($msg -like '*No audio input is supplied to this recognizer*') {
    $endOfStream = $true
  } else {
    $partial = $true
    $partialError = $msg
  }
} finally {
  $eng.Dispose()
}

$text = $sb.ToString().Trim()
$mean = $null
if ($confidences.Count -gt 0) {
  $sum = 0.0
  foreach ($c in $confidences) { $sum += $c }
  $mean = [math]::Round($sum / $confidences.Count, 4)
}

if ($segments -eq 0 -or $text.Length -eq 0) {
  # The engine ran to completion and produced nothing. That is a NULL and it is reported as one.
  $o = [ordered]@{ engine = $engineId; status = 'null'; text = ''; segments = 0;
                   mean_confidence = $null; stage = 'recognize';
                   error = 'the recognizer ran and produced no words' }
  Write-Output ($o | ConvertTo-Json -Compress)
  exit 0
}

$status = if ($partial) { 'partial' } else { 'ok' }
$o = [ordered]@{ engine = $engineId; status = $status; text = $text; segments = $segments;
                 mean_confidence = $mean; stage = 'recognize';
                 error = $(if ($partial) { $partialError } else { '' }) }
Write-Output ($o | ConvertTo-Json -Compress)
exit 0
