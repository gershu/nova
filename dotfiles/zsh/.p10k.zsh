# ============================================================
# Nova Prompt Pro (fixed)
# Audit Ergebnis:
# - Array Append Fehler behoben
# - keine doppelten Prompt Arrays
# - Farbpriorität Umgebung > Git Branch
# - kompakte rechte Seite
# - aliases nf / fl / nd / nu / np ergänzt
# Voraussetzung: NOVA_ROLE wird in .zshrc gesetzt
# ============================================================

# Farben nach Rolle
case "$NOVA_ROLE" in
  DEV)
    ROLE_COLOR=70   ;;   # grün
  UAT)
    ROLE_COLOR=220  ;;   # klares gelb
  PROD)
    ROLE_COLOR=124  ;;   # weicheres PROD rot
  *)
    ROLE_COLOR=39   ;;
esac

# Linke Seite: Rolle + Pfad + Git
typeset -g POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(os_icon dir vcs newline prompt_char)
typeset -g POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(status command_execution_time background_jobs)

# Rolle anzeigen
typeset -g POWERLEVEL9K_OS_ICON_CONTENT_EXPANSION='${NOVA_ROLE}'
typeset -g POWERLEVEL9K_OS_ICON_FOREGROUND=$ROLE_COLOR
typeset -g POWERLEVEL9K_OS_ICON_PADDING=1

# Directory Farben
typeset -g POWERLEVEL9K_DIR_FOREGROUND=$ROLE_COLOR
typeset -g POWERLEVEL9K_DIR_MAX_LENGTH=40

# Git Branch dezenter statt grell
typeset -g POWERLEVEL9K_VCS_CLEAN_FOREGROUND=110
typeset -g POWERLEVEL9K_VCS_MODIFIED_FOREGROUND=180
typeset -g POWERLEVEL9K_VCS_LOADING_FOREGROUND=110
typeset -g POWERLEVEL9K_VCS_UNTRACKED_FOREGROUND=180
typeset -g POWERLEVEL9K_VCS_VISUAL_IDENTIFIER_COLOR=244
typeset -g POWERLEVEL9K_VCS_PREFIX='%76F %f'
typeset -g POWERLEVEL9K_VCS_BACKGROUND=236
typeset -g POWERLEVEL9K_VCS_DISABLED_WORKDIR_PATTERN='~'

# Kein unnötiges OK Symbol rechts
typeset -g POWERLEVEL9K_STATUS_OK=false
# Nur langsame Commands anzeigen (>2s)
typeset -g POWERLEVEL9K_COMMAND_EXECUTION_TIME_THRESHOLD=2
typeset -g POWERLEVEL9K_COMMAND_EXECUTION_TIME_PRECISION=0

# Prompt Charakter je Rolle
if [[ "$NOVA_ROLE" == "PROD" ]]; then
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_CONTENT_EXPANSION='❯❯'
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_FOREGROUND=124
elif [[ "$NOVA_ROLE" == "UAT" ]]; then
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_CONTENT_EXPANSION='❯'
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_FOREGROUND=220
else
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_CONTENT_EXPANSION='❯'
  typeset -g POWERLEVEL9K_PROMPT_CHAR_OK_VIINS_FOREGROUND=70
fi

# User/Host kompakt rechts + Username sichtbar
typeset -g POWERLEVEL9K_CONTEXT_TEMPLATE='%n@%m'
typeset -g POWERLEVEL9K_CONTEXT_FOREGROUND=248
typeset -g POWERLEVEL9K_CONTEXT_VISUAL_IDENTIFIER_EXPANSION=''
typeset -g POWERLEVEL9K_CONTEXT_BACKGROUND='' 
# Rechts zusätzlich Python venv / Conda falls aktiv
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS+=(virtualenv context)
