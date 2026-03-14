const { Actor } = require('apify');
const { spawn } = require('child_process');
const path = require('path');

Actor.main(async () => {
    const input = await Actor.getInput();

    Actor.log.info('IP Intelligence Scraper starting', { input });

    const pythonPath = path.join(__dirname, 'main.py');
    const python = spawn('python3', [pythonPath], {
        env: {
            ...process.env,
            APIFY_TOKEN: process.env.APIFY_TOKEN || '',
        },
    });

    // Send input to Python via stdin
    python.stdin.write(JSON.stringify(input || {}));
    python.stdin.end();

    let output = '';
    let errorOutput = '';

    python.stdout.on('data', (data) => {
        output += data.toString();
    });

    python.stderr.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) Actor.log.info(`[Python] ${msg}`);
        errorOutput += msg;
    });

    const exitCode = await new Promise((resolve) => {
        python.on('close', resolve);
    });

    if (exitCode !== 0) {
        throw new Error(`Python process exited with code ${exitCode}. Stderr: ${errorOutput}`);
    }

    if (!output.trim()) {
        throw new Error('Python returned no output');
    }

    let patents;
    try {
        patents = JSON.parse(output);
    } catch (e) {
        throw new Error(`Failed to parse Python output as JSON: ${e.message}\nOutput: ${output.slice(0, 500)}`);
    }

    if (!Array.isArray(patents)) {
        throw new Error(`Expected array of patents, got: ${typeof patents}`);
    }

    Actor.log.info(`Received ${patents.length} patents from Python`);

    // Push in batches to avoid memory issues
    const BATCH_SIZE = 100;
    for (let i = 0; i < patents.length; i += BATCH_SIZE) {
        const batch = patents.slice(i, i + BATCH_SIZE);
        await Actor.pushData(batch);
        Actor.log.info(`Pushed batch ${Math.floor(i / BATCH_SIZE) + 1}/${Math.ceil(patents.length / BATCH_SIZE)}`);
    }

    // Webhook notification
    const webhookUrl = input?.webhook_url;
    if (webhookUrl) {
        try {
            const https = require('https');
            const http = require('http');
            const payload = JSON.stringify({
                status: 'completed',
                total_patents: patents.length,
                actor_run_id: process.env.APIFY_ACTOR_RUN_ID || 'local',
                timestamp: new Date().toISOString(),
            });

            const client = webhookUrl.startsWith('https') ? https : http;
            await new Promise((resolve, reject) => {
                const url = new URL(webhookUrl);
                const req = client.request(
                    {
                        hostname: url.hostname,
                        path: url.pathname + url.search,
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Content-Length': Buffer.byteLength(payload),
                        },
                    },
                    (res) => {
                        Actor.log.info(`Webhook responded: ${res.statusCode}`);
                        resolve();
                    }
                );
                req.on('error', reject);
                req.write(payload);
                req.end();
            });
        } catch (e) {
            Actor.log.warning(`Webhook delivery failed: ${e.message}`);
        }
    }

    Actor.log.info('Actor completed successfully', { total: patents.length });
});