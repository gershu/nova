# ============================================================
# Nova Platform .zshrc v3.1 (Performance Optimized)
# Source of Truth: ~/fleet/dotfiles/zsh/.zshrc
# ============================================================

# ---------- Fast Exit für non-interactive Shells ----------
# Beschleunigt ssh <host> <cmd>, rsync, fleetctl doctor/status.
[[ $- != *i* ]] && return

# ---------- Powerlevel10k Instant Prompt ----------
# Falls vorhanden deutlich schnellerer Prompt Start.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# ---------- Helper: PATH nur einmal ergänzen ----------
path_add_once() {
  case ":$PATH:" in
    *":$1:"*) ;;
    *) PATH="$1:$PATH" ;;
  esac
}

# ---------- Basis PATH ----------
path_add_once /usr/local/bin
path_add_once /usr/bin
path_add_once /bin
path_add_once /usr/sbin
path_add_once /sbin

# ---------- Homebrew Auto Detect ----------
[ -d /opt/homebrew/bin ] && path_add_once /opt/homebrew/bin
[ -d /usr/local/bin ] && path_add_once /usr/local/bin

# ---------- X11 optional ----------
[ -d /opt/X11/bin ] && path_add_once /opt/X11/bin

# ---------- pyenv ----------
if [ -d "$HOME/.pyenv/bin" ]; then
  path_add_once "$HOME/.pyenv/bin"
fi

export PATH

if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"
fi

# ---------- History ----------
HISTFILE=$HOME/.zsh_history
HISTSIZE=100000
SAVEHIST=100000
setopt APPEND_HISTORY
setopt SHARE_HISTORY
setopt HIST_IGNORE_DUPS
setopt HIST_IGNORE_SPACE
setopt HIST_REDUCE_BLANKS
setopt EXTENDED_HISTORY

# ---------- Theme / Prompt ----------
# powerlevel10k optional, ohne oh-my-zsh

# Node-Erkennung für Prompt
export NOVA_NODE="$(hostname -s 2>/dev/null || hostname)"
case "$NOVA_NODE" in
  nova-dev*)  export NOVA_ROLE="DEV" ;;
  nova-uat*)  export NOVA_ROLE="UAT" ;;
  nova-prod*) export NOVA_ROLE="PROD" ;;
  *)          export NOVA_ROLE="$NOVA_NODE" ;;
esac

# Fallback Prompt falls p10k nicht aktiv ist
if [[ -z "$POWERLEVEL9K_LEFT_PROMPT_ELEMENTS" ]]; then
  PROMPT='%F{cyan}[%n@${NOVA_ROLE}]%f %1~ %# '
fi

# ---------- Optional Plugins (nur interaktive Shell) ----------
# Hinweis: Dieser Block kann NICHT vollständig entfallen.
# Homebrew installiert die Plugins nur als Dateien; geladen werden sie erst per source.
# Daher conditional loading für Portabilität auf allen Nodes.
load_if_exists() {
  [ -f "$1" ] && source "$1"
}

load_if_exists /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh
load_if_exists /usr/local/share/zsh-autosuggestions/zsh-autosuggestions.zsh
load_if_exists /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
load_if_exists /usr/local/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
load_if_exists /opt/homebrew/share/powerlevel10k/powerlevel10k.zsh-theme
load_if_exists /usr/local/share/powerlevel10k/powerlevel10k.zsh-theme

# ---------- fzf optional ----------
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

# ---------- Aliases ----------
alias python='python3'
alias nf='~/fleet/scripts/fleetctl'  # nova fleet
alias fl='cd ~/fleet'
alias nd='ssh nova-dev'
alias nu='ssh nova-uat'
alias np='ssh nova-prod'
alias pip='pip3'
alias gs='git status'
alias ga='git add .'
alias gc='git commit -m'
alias gp='git push'
alias ll='ls -lah'
alias la='ls -la'
alias fl='cd ~/fleet'
alias cls='clear'

# ---------- Prompt Zusatzinfo ----------
export LANG=en_US.UTF-8
export LC_TIME=de_DE.UTF-8
unset LC_ALL

# ---------- p10k optional ----------
# In ~/.p10k.zsh kann NOVA_ROLE im Prompt genutzt werden.
[[ -f ~/.p10k.zsh ]] && source ~/.p10k.zsh

# ---------- Ende ----------
