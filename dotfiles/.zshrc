
export ANTHROPIC_API_KEY="<set in ~/.zshrc>"
export ENABLE_CLAUDEAI_MCP_SERVERS=false

# Inkwell meeting notes
alias d359='~/vault/i447/i446/new-meeting.sh d359'
alias d358='~/vault/i447/i446/new-meeting.sh d358'

. "$HOME/.local/bin/env"

# LLM Session Tracking
alias cc="$HOME/vault/i447/i446/claude-tracked"
alias llm-stats="$HOME/vault/i447/i446/llm-stats"
alias ghcp-sync="$HOME/vault/i447/i446/ghcp-sync"

# Auto-sync Copilot CLI logs on shell startup
$HOME/vault/i447/i446/ghcp-sync --quiet &

# GitHub Copilot CLI
alias '??'='gh copilot explain'
alias 'suggest'='gh copilot suggest'

# BEGIN Agency MANAGED BLOCK
if [[ ":${PATH}:" != *":/Users/mckay/.config/agency/CurrentVersion:"* ]]; then
    export PATH="/Users/mckay/.config/agency/CurrentVersion:${PATH}"
fi
# END Agency MANAGED BLOCK

# AI Tools Dashboard
alias ai-dashboard='/Users/mckay/vault/i447/i446/ai-dashboard'

# Add ~/bin to PATH for custom scripts
export PATH="$HOME/bin:$PATH"
