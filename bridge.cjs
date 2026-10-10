// bridge.cjs
const { WebSocketServer } = require('ws');
const { execFile } = require('child_process');

let robot = null;
try {
    robot = require('robotjs');
    console.log('robotjs loaded for low-latency input control');
} catch (e) {
    console.log('robotjs not available, will fallback to persistent PowerShell handler for actions');
}

const { spawn } = require('child_process');
let psProcess = null;
try {
    const path = require('path');
    const script = path.join(__dirname, 'ps_handler.ps1');
    psProcess = spawn('powershell', ['-NoProfile', '-NonInteractive', '-File', script], { stdio: ['pipe', 'pipe', 'pipe'] });
    psProcess.stdout.on('data', d => console.log('[ps] ' + d.toString().trim()));
    psProcess.stderr.on('data', d => console.error('[ps-err] ' + d.toString().trim()));
    psProcess.on('exit', (code) => console.log('ps handler exited', code));
    console.log('Persistent PowerShell handler started');
} catch (e) {
    console.error('Failed to start PS handler:', e);
}

const wss = new WebSocketServer({ port: 3001 });

function sendPowerPointAction(action) {
    return new Promise((resolve, reject) => {
        if (psProcess && psProcess.stdin.writable) {
            const cmd = JSON.stringify({ cmd: 'ppt', action: action }) + "\n";
            psProcess.stdin.write(cmd, 'utf8', (err) => {
                if (err) { reject(err); return; }
                resolve();
            });
            return;
        }
        // fallback to execFile if PS handler not available
        const psScript = `
            try { $pp = [Runtime.InteropServices.Marshal]::GetActiveObject('PowerPoint.Application') } catch { Write-Host 'NoPowerPoint'; exit 3 }
            try { if ($pp.SlideShowWindows.Count -gt 0) { $view = $pp.SlideShowWindows.Item(1).View; switch ('${action}') { 'next' { $view.Next() } 'prev' { $view.Previous() } 'stop' { $view.Exit() } 'start' { $pp.ActivePresentation.SlideShowSettings.Run() } default { } } exit 0 } else { if ('${action}' -eq 'start') { if ($pp.Presentations.Count -gt 0) { $pp.ActivePresentation.SlideShowSettings.Run(); exit 0 } else { Write-Host 'NoPresentation'; exit 4 } } else { Write-Host 'NoSlideShow'; exit 4 } } } catch { Write-Host 'CmdError' $_.Exception.Message; exit 5 }
        `;
        execFile('powershell', ['-NoProfile', '-NonInteractive', '-Command', psScript], (error, stdout, stderr) => {
            const out = (stdout || '').toString();
            if (error) { reject(error); return; }
            resolve();
        });
    });
}

function setCursorPosNormalized(nx, ny) {
    return new Promise((resolve, reject) => {
        if (robot) {
            try {
                const screen = robot.getScreenSize();
                const x = Math.round(nx * screen.width);
                const y = Math.round(ny * screen.height);
                robot.moveMouse(x, y);
                resolve();
                return;
            } catch (err) {
                // fall through to ps handler
                console.error('robotjs cursor error:', err);
            }
        }

        if (psProcess && psProcess.stdin.writable) {
            const cmd = JSON.stringify({ cmd: 'cursor', x: nx, y: ny }) + "\n";
            psProcess.stdin.write(cmd, 'utf8', (err) => {
                if (err) { reject(err); return; }
                resolve();
            });
            return;
        }

        const psScript = `
            Add-Type -AssemblyName System.Windows.Forms
            $w = [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Width
            $h = [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Height
            $x = [int]([math]::Round(${nx} * $w))
            $y = [int]([math]::Round(${ny} * $h))
            Add-Type @"using System; using System.Runtime.InteropServices; public class N { [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y); }"@
            [N]::SetCursorPos($x, $y)
        `;
        execFile('powershell', ['-NoProfile', '-NonInteractive', '-Command', psScript], (error, stdout, stderr) => {
            if (error) { reject(error); return; }
            resolve();
        });
    });
}

function sendMouseWheelWithCtrl(delta) {
    return new Promise((resolve, reject) => {
        // delta: positive for up, negative for down. We'll send one or multiple notches
        if (robot) {
            try {
                const steps = Math.abs(delta) < 1 ? 1 : Math.round(Math.abs(delta));
                for (let i = 0; i < steps; i++) {
                    robot.keyToggle('control', 'down');
                    robot.scrollMouse(0, delta > 0 ? 1 : -1);
                    robot.keyToggle('control', 'up');
                }
                resolve();
                return;
            } catch (err) {
                console.error('robotjs wheel error:', err);
            }
        }

        if (psProcess && psProcess.stdin.writable) {
            const cmd = JSON.stringify({ cmd: 'wheel', delta: delta }) + "\n";
            psProcess.stdin.write(cmd, 'utf8', (err) => {
                if (err) { reject(err); return; }
                resolve();
            });
            return;
        }

        // fallback to Powershell spawn
        const wheel = delta > 0 ? 120 : -120;
        const psScript = `
            Add-Type @"using System; using System.Runtime.InteropServices; public class M { [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo); [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, int dwData, UIntPtr dwExtraInfo); }"@
            # ctrl down
            [M]::keybd_event(0x11,0,0,[UIntPtr]::Zero)
            Start-Sleep -Milliseconds 10
            # wheel event
            [M]::mouse_event(0x0800,0,0,${wheel},[UIntPtr]::Zero)
            Start-Sleep -Milliseconds 10
            # ctrl up
            [M]::keybd_event(0x11,0,2,[UIntPtr]::Zero)
        `;
        execFile('powershell', ['-NoProfile', '-NonInteractive', '-Command', psScript], (error, stdout, stderr) => {
            if (error) { reject(error); return; }
            resolve();
        });
    });
}

// Map project gesture names to PowerPoint COM actions
const GESTURE_TO_ACTION = {
    'OPEN_PALM': 'start',
    'CLOSED_FIST': 'close',
    'POINTING': 'laser',
    'NONE': null
};

wss.on('connection', (ws) => {
    console.log('Client connected to bridge');

    ws.on('message', async (message) => {
        try {
            const msg = JSON.parse(message);
            const { type } = msg;
            if (type === 'gesture') {
                const { gesture } = msg;
                console.log('Received gesture:', gesture);

                // Swipes -> next/prev
                if (gesture === 'SWIPE_RIGHT') {
                    try { await sendPowerPointAction('next'); console.log('Swipe right -> next'); } catch (e) { console.error(e); }
                } else if (gesture === 'SWIPE_LEFT') {
                    try { await sendPowerPointAction('prev'); console.log('Swipe left -> prev'); } catch (e) { console.error(e); }
                }

                // Pinch/Spread -> zoom out/in (simulate ctrl+mouse wheel)
                else if (gesture === 'PINCH') {
                    try { await sendMouseWheelWithCtrl(-1); console.log('Pinch -> zoom out'); } catch (e) { console.error(e); }
                } else if (gesture === 'SPREAD') {
                    try { await sendMouseWheelWithCtrl(1); console.log('Spread -> zoom in'); } catch (e) { console.error(e); }
                }

                // Other gestures
                else {
                    const action = GESTURE_TO_ACTION[gesture];
                    if (action) {
                        try {
                            await sendPowerPointAction(action);
                            console.log(`Processed gesture: ${gesture} -> ${action}`);
                        } catch (error) {
                            console.error(`Failed to process gesture ${gesture}:`, error && error.message ? error.message : error);
                        }
                    } else {
                        console.log('No mapped action for gesture:', gesture);
                    }
                }
            } else if (type === 'pointer') {
                // pointer message: { type: 'pointer', x: 0..1, y: 0..1 }
                const { x, y } = msg;
                try {
                    await setCursorPosNormalized(x, y);
                } catch (err) {
                    console.error('Pointer move failed:', err && err.message ? err.message : err);
                }
            }
        } catch (error) {
            console.error('Error processing message:', error);
        }
    });

    ws.on('close', () => {
        console.log('Client disconnected');
    });
});

console.log('Bridge server running on ws://localhost:3001');