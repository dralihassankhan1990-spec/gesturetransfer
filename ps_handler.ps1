Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Native {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, int dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

function Write-Ok($msg) { Write-Output ("OK: " + $msg) }
function Write-Err($msg) { Write-Output ("ERR: " + $msg) }

$pp = $null

function Get-PPT {
    try {
        return [Runtime.InteropServices.Marshal]::GetActiveObject('PowerPoint.Application')
    } catch {
        try {
            $newPP = New-Object -ComObject PowerPoint.Application
            $newPP.Visible = 1 # msoTrue
            return $newPP
        } catch {
            return $null
        }
    }
}

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    try {
        $obj = $line | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Err "Invalid JSON"
        continue
    }

    try {
        switch ($obj.cmd) {
            'ppt' {
                $pp = Get-PPT
                if ($null -eq $pp) { Write-Err 'NoPowerPoint'; continue }

                switch ($obj.action) {
                    'start' {
                        if ($pp.Presentations.Count -eq 0) {
                            $pp.Presentations.Add() | Out-Null
                        }
                        $pp.ActivePresentation.SlideShowSettings.Run() | Out-Null
                        Write-Ok 'started'
                    }
                    'stop' {
                        if ($pp.SlideShowWindows.Count -gt 0) {
                            $pp.SlideShowWindows.Item(1).View.Exit()
                            Write-Ok 'stopped'
                        } else {
                            Write-Err 'NoSlideShow'
                        }
                    }
                    'next' {
                        if ($pp.SlideShowWindows.Count -gt 0) {
                            $pp.SlideShowWindows.Item(1).View.Next()
                            Write-Ok 'next'
                        } else { Write-Err 'NoSlideShow' }
                    }
                    'prev' {
                        if ($pp.SlideShowWindows.Count -gt 0) {
                            $pp.SlideShowWindows.Item(1).View.Previous()
                            Write-Ok 'prev'
                        } else { Write-Err 'NoSlideShow' }
                    }
                    'close' {
                        if ($pp.Presentations.Count -gt 0) {
                            $pp.ActivePresentation.Close()
                            Write-Ok 'closed'
                        } else {
                            $pp.Quit()
                            Write-Ok 'quit'
                        }
                    }
                    'laser' {
                        # Toggle laser pointer in slideshow mode (Ctrl+L)
                        if ($pp.SlideShowWindows.Count -gt 0) {
                            [Native]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero) # Ctrl down
                            [Native]::keybd_event(0x4C, 0, 0, [UIntPtr]::Zero) # L down
                            Start-Sleep -Milliseconds 50
                            [Native]::keybd_event(0x4C, 0, 2, [UIntPtr]::Zero) # L up
                            [Native]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero) # Ctrl up
                            Write-Ok 'laser toggled'
                        } else { Write-Err 'NoSlideShow' }
                    }
                    default { Write-Err "UnknownAction $($obj.action)" }
                }
            }
            'cursor' {
                $w = [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Width
                $h = [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Height
                $x = [int]([math]::Round($obj.x * $w))
                $y = [int]([math]::Round($obj.y * $h))
                [Native]::SetCursorPos($x, $y) | Out-Null
                Write-Ok "cursor $x,$y"
            }
            'wheel' {
                [Native]::keybd_event(0x11,0,0,[UIntPtr]::Zero)
                Start-Sleep -Milliseconds 8
                $delta = $obj.delta
                $steps = [int]([math]::Max(1,[math]::Abs($delta)))
                for ($i=0;$i -lt $steps;$i++) {
                    if ($delta -gt 0) { [Native]::mouse_event(0x0800,0,0,120,[UIntPtr]::Zero) } else { [Native]::mouse_event(0x0800,0,0,-120,[UIntPtr]::Zero) }
                    Start-Sleep -Milliseconds 6
                }
                Start-Sleep -Milliseconds 8
                [Native]::keybd_event(0x11,0,2,[UIntPtr]::Zero)
                Write-Ok "wheel $delta"
            }
            default { Write-Err 'UnknownCmd' }
        }
    } catch {
        Write-Err $_.Exception.Message
    }
}

Write-Output 'PS handler exiting'
