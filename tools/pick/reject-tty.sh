#!/bin/zsh
# reject-tty.sh <task_id> — dtd alt-n: reject an agent pick with a one-line note.
# Same tty handling as dtd's edit script (dtd.sh, DTD_EDIT): ^C/^\ must arrive
# as bytes, not SIGINT for fzf's whole process group (that killed dtd), and
# cmux can leave the stale fzf frame up, so clear before prompting. intr/quit
# stay undefined for fzf's lifetime, as the edit script leaves them.
stty sane susp undef < /dev/tty 2>/dev/null
stty intr undef quit undef < /dev/tty 2>/dev/null
printf "\033[2J\033[H" > /dev/tty
/usr/bin/python3 "$HOME/i446-monorepo/tools/pick/reject.py" "$1" < /dev/tty > /dev/tty 2>&1
sleep 0.6
