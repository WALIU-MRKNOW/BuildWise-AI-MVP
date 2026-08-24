# Google Cloud free VM deployment

BuildWise uses Telegram long polling, so it needs a host that stays awake. A Google Compute Engine `e2-micro` VM can run continuously within Google's Always Free allowance when every resource stays inside the published limits.

Billing must be enabled. Google can charge for resources outside the allowance, so use the exact VM type, region, and disk listed below and set a small budget alert.

## 1. Create the VM

1. Open Google Cloud Console → **Compute Engine** → **VM instances** → **Create instance**.
2. Name: `buildwise-bot`.
3. Region: `us-central1` (Iowa), `us-west1` (Oregon), or `us-east1` (South Carolina).
4. Machine family: **E2**.
5. Machine type: **e2-micro** (2 shared vCPU, 1 GB memory).
6. Boot disk: Ubuntu 24.04 LTS, **Standard persistent disk**, 10 GB.
7. Do not add a GPU, static external IP, load balancer, or premium disk.
8. BuildWise does not need the HTTP/HTTPS firewall boxes for Telegram long polling.
9. Click **Create**.

## 2. Upload and unpack BuildWise

1. Click **SSH** beside the VM.
2. In the SSH window, use **Upload file** to upload the BuildWise ZIP.
3. Run:

```bash
sudo apt-get update
sudo apt-get install -y unzip nano
unzip BuildWise-AI-MVP-Google-Free-2026-08-14.zip
cd BuildWise-AI-MVP
cp .env.example .env
nano .env
```

Enter the four required values without quotes:

```dotenv
TELEGRAM_BOT_TOKEN=your_new_private_token
OPENAI_API_KEY=your_private_openai_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your_new_sb_secret_key
```

Leave the other defaults in place. Save in Nano with **Ctrl+O**, Enter, then exit with **Ctrl+X**. Never paste `.env` into chat, source control, screenshots, or logs.

## 3. Install and start the bot

```bash
chmod +x deploy/google-vm-setup.sh
./deploy/google-vm-setup.sh
curl http://127.0.0.1:8000/health
```

The installer creates a system service named `buildwise-ai`. It starts the bot immediately and restarts it automatically after a failure or VM reboot.

Useful checks:

```bash
sudo systemctl status buildwise-ai --no-pager
sudo journalctl -u buildwise-ai -f
```

Press **Ctrl+C** to leave the live log view; this does not stop the bot.

## 4. Test in Telegram

1. Send `/start`.
2. Send a text study question.
3. Send a question image.
4. Try **Quick Practice**.
5. Send `/history`.

Only one running copy may use the same Telegram token. Stop the local Windows copy before starting the VM copy.

## Cost controls

- Keep the VM on `e2-micro` in an eligible US region.
- Use no more than 30 GB of standard persistent disk across eligible free-tier VMs.
- Keep outbound transfer within the current free allowance.
- Open **Billing → Budgets & alerts** and create a low threshold alert. A budget alert warns you; it does not automatically stop resources.
- OpenAI API usage is billed separately from Google Cloud and ChatGPT subscriptions.
