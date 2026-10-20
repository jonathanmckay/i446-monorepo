# Local fork setup

This repo is a clone of [CryptoCultCurt/appfolio-mcp-server](https://github.com/CryptoCultCurt/appfolio-mcp-server) for use as your own fork.

## 1. Build (first time)

From this directory:

```bash
npm install
npm run build
```

## 2. Push to your GitHub repo (optional)

To make this your own fork on GitHub:

1. Create a new repo on GitHub (e.g. `appfolio-mcp-server`).
2. Add it as a remote and push:

   ```bash
   git remote rename origin upstream
   git remote add origin https://github.com/YOUR_USERNAME/appfolio-mcp-server.git
   git push -u origin main
   ```

   (Use `master` instead of `main` if that’s your default branch.)

## 3. Cursor MCP config

Cursor is configured to use this server from `~/.cursor/config/mcp.json`.  
Set your AppFolio credentials in that file (or via Cursor Settings → MCP):

- **VHOST** – Your AppFolio hostname (e.g. `yourcompany`).
- **USERNAME** – API username.
- **PASSWORD** – API password.

Then restart Cursor or reload MCP servers so the AppFolio tools appear.
