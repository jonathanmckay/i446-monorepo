#!/bin/bash
# Start m5x2 AI Dashboard
# Run: bash start-m5x2.sh

cd ~/vault/i447/i446/ai-dashboard

echo "🏢 Starting m5x2 AI Dashboard..."
echo ""
echo "📊 Dashboard: http://localhost:5556"
echo "🔌 API:       http://localhost:5556/api/stats"
echo ""
echo "Quick commands:"
echo "  Tag current session:  python3 m5x2-tag-session.py -u jm -p r202"
echo "  List recent sessions: python3 m5x2-tag-session.py --list"
echo ""
echo "Press Ctrl+C to stop"
echo ""

python3 m5x2-dashboard.py
