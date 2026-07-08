# ⌨  tmux cheatsheet

> **The one rule:** almost every command starts with the **prefix** —
> press `Ctrl b`, release it, then tap the key.
> Mouse clicks and status-bar clicks need no prefix.

---

## ★ Your custom bindings

| Keys | Action |
| :--- | :--- |
| 🖱 click session name | Open the session switcher |
| `prefix` `S` | Send current pane → another window |
| `prefix` `J` | Join a pane from another window → here |
| 🖱 drag | Select text → copies to clipboard (wl-copy) |
| 🖱 scroll | Scroll back through history |
| 🖱 drag border | Resize the pane you drag |
| 🖱 click `?` · `prefix` `:` `cheatsheet` | Open this cheatsheet |

## Sessions

| Keys | Action |
| :--- | :--- |
| `prefix` `s` | Switcher: pick a session |
| `prefix` `d` | Detach (leaves it running) |
| `prefix` `$` | Rename the session |
| `prefix` `(` / `)` | Previous / next session |
| `prefix` `L` | Jump to last session |

## Windows (tabs)

| Keys | Action |
| :--- | :--- |
| `prefix` `c` | Create a new window |
| `prefix` `,` | Rename the window |
| `prefix` `0`–`9` | Jump to window by number |
| `prefix` `n` / `p` | Next / previous window |
| `prefix` `w` | Switcher: window tree |
| `prefix` `f` | Find a window by text |
| `prefix` `&` | Kill the window |

## Panes

| Keys | Action |
| :--- | :--- |
| `prefix` `%` | Split left / right |
| `prefix` `"` | Split top / bottom |
| `prefix` `←` `↑` `↓` `→` | Move between panes |
| `prefix` `z` | Zoom pane fullscreen (toggle) |
| `prefix` `{` / `}` | Swap pane ← / → |
| `prefix` `!` | Break pane into its own window |
| `prefix` `q` | Show pane numbers (tap # to jump) |
| `prefix` `x` | Kill the pane |

## Resize

| Keys | Action |
| :--- | :--- |
| `prefix` `Ctrl` `←↑↓→` | Resize by 1 cell |
| `prefix` `Alt` `←↑↓→` | Resize by 5 cells |
| 🖱 drag border | Resize by dragging |

## Copy & scroll

| Keys | Action |
| :--- | :--- |
| `prefix` `[` | Enter copy / scroll mode |
| `Space` / `v` | Start selecting (in copy mode) |
| `Enter` / `y` | Copy selection → clipboard |
| `q` | Leave copy mode |
| `prefix` `]` | Paste last copy |

## Pane layouts

Cycle with `prefix` `Space`, or jump straight to one with `prefix` `Alt`+`1`–`5`.

```
 Alt+1  even-horizontal    Alt+2  even-vertical     Alt+3  main-horizontal
 ┌───┬───┬───┐             ┌───────────┐            ┌───────────┐
 │   │   │   │             ├───────────┤            │   main    │
 │   │   │   │             ├───────────┤            ├─────┬─────┤
 └───┴───┴───┘             └───────────┘            └─────┴─────┘

 Alt+4  main-vertical      Alt+5  tiled
 ┌──────┬────┐             ┌─────┬─────┐
 │      ├────┤             ├─────┼─────┤
 │ main ├────┤             │     │     │
 └──────┴────┘             └─────┴─────┘
```

`main-horizontal` = one big pane on top, the rest in a row below.
The top/left "main" pane is the lowest-numbered one — swap with `prefix` `{` / `}`.

## Help & plugins

| Keys | Action |
| :--- | :--- |
| `prefix` `?` | List *every* key binding |
| `prefix` `:` | Command prompt |
| `prefix` `t` | Big clock |
| `prefix` `Tab` | Toggle file sidebar (plugin) |
| `prefix` `I` | Install plugins (tpm) |

---

*Reload after editing `~/.tmux.conf`:* `tmux source-file ~/.tmux.conf`
*Press `q` to close.*
