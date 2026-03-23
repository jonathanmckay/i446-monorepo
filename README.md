# i446-monorepo

Personal infrastructure and automation tools for productivity, property management, and data tracking.

## Overview

This monorepo contains a collection of tools and scripts for:
- **Property Management** - AppFolio integrations and leasing automation
- **Productivity** - Todoist, Toggl, and goal tracking integrations
- **AI/LLM Tools** - MCP servers and AI integrations
- **Content Management** - Reading list management and media tracking

## Projects

### Property Management (AppFolio)

- **[appfolio-mcp-server](./appfolio-mcp-server/)** - MCP server for AppFolio Property Manager Reporting API
- **[appfolio-leasing-report](./appfolio-leasing-report/)** - Daily sync of lease applications to Google Sheets
- **appfolio-attrition** - Tenant attrition analysis

### Productivity & Automation

- **[toggl_server](./toggl_server/)** - Custom Toggl MCP server with domain code mapping
- **[comms-launcher](./comms-launcher/)** - Opens communication channels in correct Chrome profiles
- **new-meeting.sh** - Creates structured meeting notes

### Content & Media

- **[x955-hcmc.2](./x955-hcmc.2/)** - "Inkwell" - Multimedia reading list collator with API access
- **classify-conversations** - Conversation classification tools

### Knowledge Management

- **[m5x2-kb](./m5x2-kb/)** - McKay Capital knowledge base (GitHub Pages)

### Utilities

- **[scripts](./scripts/)** - Collection of utility scripts

## Setup

### Prerequisites

- Python 3.9+
- Node.js 16+ (for MCP servers)
- Git

### Environment Variables

Copy `.env.example` to `.env` in each project directory and fill in your credentials:

```bash
# x954-g245.1 (OneDrive integration)
ONEDRIVE_CLIENT_ID=your_client_id
ONEDRIVE_CLIENT_SECRET=your_client_secret

# AppFolio projects
APPFOLIO_CLIENT_ID=your_client_id
APPFOLIO_CLIENT_SECRET=your_client_secret
APPFOLIO_BASE_URL=https://yourvhost.appfolio.com

# Optional integrations
TOGGL_API_TOKEN=your_token
TODOIST_API_KEY=your_key
```

### Installation

```bash
# Clone the repository
git clone git@github.com:jonathanmckay/i446-monorepo.git
cd i446-monorepo

# Install individual projects as needed
cd appfolio-mcp-server && npm install
cd ../x954-g245.1 && pip install -r requirements.txt
# etc.
```

## Usage

Each project has its own README with specific usage instructions. See individual project directories for details.

## Project Structure

```
i446-monorepo/
├── appfolio-attrition/      # Tenant attrition analysis
├── appfolio-leasing-report/ # Leasing pipeline sync
├── appfolio-mcp-server/     # AppFolio MCP integration
├── classify-conversations/  # Conversation tools
├── comms-launcher/          # Communication channel launcher
├── m5x2-kb/                 # Knowledge base
├── scripts/                 # Utility scripts
├── toggl_server/            # Custom Toggl MCP server
└── x955-hcmc.2/             # Reading list collator
```

## Security

- **Never commit** `.env` files, credentials, or API keys
- All secrets should be in environment variables
- See `.gitignore` for excluded file patterns

## Contributing

These are personal tools, but feel free to fork and adapt for your own use.

## License

MIT (or specify your preferred license)

## Related

These tools are symlinked from `~/vault/i447/i446/` in the Inkwell knowledge management system.
