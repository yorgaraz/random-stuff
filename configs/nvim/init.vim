call plug#begin('~/.local/share/nvim/site/plugged')

Plug 'preservim/nerdtree'

Plug 'sheerun/vim-polyglot'

Plug 'airblade/vim-gitgutter'

Plug 'preservim/tagbar'

Plug 'tpope/vim-fugitive'

Plug 'tpope/vim-surround'

Plug 'nvim-treesitter/nvim-treesitter'

Plug 'terryma/vim-multiple-cursors'

Plug 'tribela/vim-transparent'

Plug 'github/copilot.vim'

Plug 'danilo-augusto/vim-afterglow', { 'as': 'afterglow' }

Plug 'catppuccin/vim', { 'as': 'catppuccin' }

Plug 'prabirshrestha/vim-lsp'

Plug 'mattn/vim-lsp-settings'

call plug#end()

colorscheme catppuccin_mocha
" colorscheme afterglow

" Start NERDTree. If a file is specified, move the cursor to its window.
autocmd StdinReadPre * let s:std_in=1
autocmd VimEnter * NERDTree | if argc() > 0 || exists("s:std_in") | wincmd p | endif

" Close the tab if NERDTree is the only window remaining in it.
autocmd BufEnter * if winnr('$') == 1 && exists('b:NERDTree') && b:NERDTree.isTabTree() | quit | endif
set tabstop=4
set shiftwidth=4
set expandtab

let g:copilot_filetypes = {'markdown': v:true, 'yaml': v:true}
