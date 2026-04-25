" --- Basis ---
set nocompatible          " Nutze echte Vim-Features
syntax on                 " Syntax Highlighting
set number                " Zeilennummern anzeigen
"set relativenumber         relative Nummern (sehr praktisch)

" --- Einrückung ---
set tabstop=4             " Tab = 4 Leerzeichen
set shiftwidth=4
set expandtab             " Tabs in Spaces umwandeln
set autoindent
set smartindent

" --- Suche ---
set ignorecase            " Suche nicht case-sensitive
set smartcase             " außer Großbuchstaben genutzt werden
set incsearch             " während Tippen suchen
set hlsearch              " Treffer hervorheben

" --- Navigation ---
set scrolloff=5           " immer 5 Zeilen Kontext
set cursorline            " aktuelle Zeile hervorheben

" --- Verhalten ---
set nowrap                " kein Zeilenumbruch
set hidden                " mehrere Dateien gleichzeitig bearbeiten
set backspace=indent,eol,start

" --- Status ---
set showcmd               " zeigt eingegebene Befehle
set showmode              " zeigt Modus (INSERT etc.)

" --- Clipboard (macOS wichtig!) ---
set clipboard=unnamed     " nutzt System-Clipboard

" --- Schnelle Shortcuts ---
let mapleader=" "

nnoremap <leader>w :w<CR>       " speichern
nnoremap <leader>q :q<CR>       " schließen
nnoremap <leader>x :x<CR>       " speichern + schließen

" --- Such-Highlight schnell löschen ---
nnoremap <leader><space> :nohlsearch<CR>

nnoremap <leader>gs :Git<CR>
nnoremap <leader>gd :Gdiffsplit<CR>
nnoremap <leader>gb :Git blame<CR>
