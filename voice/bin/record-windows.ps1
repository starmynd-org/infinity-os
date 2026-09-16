<#
  Record one wav through the Windows MCI wave device, because WSL2 on this host has no audio
  device to record through: /dev/snd does not exist, there is no ffmpeg, no sox and no arecord,
  and sudo is closed to agents so apt is not a path. Measured 2026-08-18.

  winmm's mciSendString and nothing else. No NAudio, no third-party dll, no download: this ships
  with Windows and it is one P/Invoke. `waveInGetNumDevs` on this host returns 1 and the endpoint
  reporting Status=OK is the Qualcomm microphone array.

  IT REPORTS ONE LINE OF KEY=VALUE PER FACT AND NOTHING ELSE, and every MCI return code is on it.
  The caller parses that rather than trusting the exit code, because an MCI command that fails
  returns a non-zero code into a variable while the script goes on to exit 0 -- which is exactly
  the shape of the failure this whole lane exists to remove. `save_rc=266` with `exit 0` would
  otherwise read as a recording.

  Usage:
    record-windows.ps1 -OutFile C:\path\to\x.wav -Seconds 30 [-SampleRate 16000]
#>
param(
  [Parameter(Mandatory=$true)][string]$OutFile,
  [Parameter(Mandatory=$true)][int]$Seconds,
  [int]$SampleRate = 16000
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class VoiceMci {
  [DllImport("winmm.dll", CharSet=CharSet.Ansi)]
  private static extern int mciSendStringA(string cmd, StringBuilder ret, int retLen, IntPtr hwnd);
  [DllImport("winmm.dll", CharSet=CharSet.Ansi)]
  private static extern int mciGetErrorStringA(int err, StringBuilder buf, int bufLen);
  [DllImport("winmm.dll")]
  public static extern int waveInGetNumDevs();

  public static int Send(string cmd) { return mciSendStringA(cmd, null, 0, IntPtr.Zero); }
  public static string Err(int code) {
    StringBuilder b = new StringBuilder(256);
    mciGetErrorStringA(code, b, b.Capacity);
    return b.ToString();
  }
}
'@

function Emit($k, $v) { Write-Output ("{0}={1}" -f $k, $v) }

# The device count BEFORE anything is opened. A host with no microphone must say so in a sentence
# that names the cause, not fail four commands later with a code nobody can read.
$devs = [VoiceMci]::waveInGetNumDevs()
Emit 'wave_in_devices' $devs
if ($devs -lt 1) {
  Emit 'error' 'no wave input device on this Windows host: waveInGetNumDevs()=0'
  Emit 'ok' 'no'
  exit 3
}

$dir = Split-Path -Parent $OutFile
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
if (Test-Path $OutFile) { Remove-Item $OutFile -Force }

$alias = 'voicecap'
$bytesPerSec = $SampleRate * 2

$rcOpen   = [VoiceMci]::Send("open new type waveaudio alias $alias")
Emit 'open_rc' $rcOpen
if ($rcOpen -ne 0) {
  Emit 'error' ("mci open failed: " + [VoiceMci]::Err($rcOpen))
  Emit 'ok' 'no'
  exit 4
}

try {
  $rcSet = [VoiceMci]::Send("set $alias bitspersample 16 channels 1 samplespersec $SampleRate alignment 2 bytespersec $bytesPerSec format tag pcm")
  Emit 'set_rc' $rcSet

  $startedAt = Get-Date
  $rcRec = [VoiceMci]::Send("record $alias")
  Emit 'record_rc' $rcRec
  if ($rcRec -ne 0) {
    Emit 'error' ("mci record failed: " + [VoiceMci]::Err($rcRec))
    Emit 'ok' 'no'
    exit 5
  }

  Start-Sleep -Seconds $Seconds

  $rcStop = [VoiceMci]::Send("stop $alias")
  Emit 'stop_rc' $rcStop
  # The wall clock the microphone was actually open for, not the number that was asked for. They
  # differ, and reporting the request as though it were the result is how a truncated recording
  # gets filed as a whole one.
  Emit 'elapsed_s' ([math]::Round(((Get-Date) - $startedAt).TotalSeconds, 2))

  $rcSave = [VoiceMci]::Send("save $alias `"$OutFile`"")
  Emit 'save_rc' $rcSave
  if ($rcSave -ne 0) {
    Emit 'error' ("mci save failed: " + [VoiceMci]::Err($rcSave))
    Emit 'ok' 'no'
    exit 6
  }
}
finally {
  # Always, on every path. An MCI alias left open holds the device and the NEXT capture's `open`
  # fails with a code that names nothing.
  [void][VoiceMci]::Send("close $alias")
}

if (Test-Path $OutFile) {
  Emit 'bytes' (Get-Item $OutFile).Length
} else {
  # The save returned 0 and there is no file. This is the 2026-08-17 incident's exact shape and
  # it is reported as a failure here rather than left for the caller to discover.
  Emit 'bytes' 0
  Emit 'error' 'mci save returned 0 and wrote no file'
  Emit 'ok' 'no'
  exit 7
}

Emit 'path' $OutFile
Emit 'ok' 'yes'
exit 0
