# comms-launcher

Open communication channels in the right Google Chrome profiles on macOS.

## What it opens
- Gmail (personal)
- Gmail (work)
- Outlook
- Teams
- Google Messages
- WhatsApp

## CLI run
```bash
./open_comms_channels.sh
```

## Chrome extension button (one click)
This repo includes a Chrome extension (`chrome-extension/`) that triggers the launcher script via native messaging.

### 1) Load the extension
1. Open `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select this folder: `chrome-extension`
5. Copy the extension ID shown on the card

### 2) Install native host for that extension ID
From this repo root:
```bash
./install_native_host.sh <YOUR_EXTENSION_ID>
```

This writes:
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.jonathanmckay.comms_launcher.json`

### 3) Use it
1. Reload the extension in `chrome://extensions`
2. Pin `Comms Launcher`
3. Click the extension icon, then click `Launch`

## Customize profile/url routing
Edit `TARGETS` in `open_comms_channels.sh`.
