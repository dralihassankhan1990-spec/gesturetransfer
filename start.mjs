// start.mjs
import { exec } from 'child_process';

console.log('Starting Vite development server...');
const vite = exec('npx vite', { 
    stdio: 'inherit',
    shell: true 
});

vite.stdout?.on('data', (data) => console.log(`Vite: ${data}`));
vite.stderr?.on('data', (data) => console.error(`Vite Error: ${data}`));

console.log('Starting WebSocket bridge server...');
const bridge = exec('node bridge.cjs', { 
    stdio: 'inherit',
    shell: true 
});

bridge.stdout?.on('data', (data) => console.log(`Bridge: ${data}`));
bridge.stderr?.on('data', (data) => console.error(`Bridge Error: ${data}`));

// Handle process termination
process.on('SIGINT', () => {
    console.log('Shutting down servers...');
    vite.kill();
    bridge.kill();
    process.exit();
});

console.log('Servers started. Press Ctrl+C to stop.');