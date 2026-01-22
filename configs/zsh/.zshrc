
# initialize autocompletion
autoload -U compinit && compinit

# history setup
setopt SHARE_HISTORY
HISTFILE=$HOME/.zhistory
SAVEHIST=10000
HISTSIZE=9999
setopt HIST_EXPIRE_DUPS_FIRST
export EDITOR=nvim
export VISUAL=nvim
export GPG_TTY=$(tty)

# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:/usr/local/bin:$PATH

# Path to your oh-my-zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time oh-my-zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
ZSH_THEME="miloshadzic"

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
CASE_SENSITIVE="false"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
zstyle ':omz:update' mode auto      # update automatically without asking
# zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(git npm cp archlinux 1password kubectx history golang git-flow-avh kubectl nvm pip python pyenv systemd sudo tmux zsh-interactive-cd zsh-navigation-tools history-substring-search)

source $ZSH/oh-my-zsh.sh

# User configuration

export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
export LANG=en_US.UTF-8
export LC_NUMERIC=el_GR.UTF-8
export LC_MONETARY=el_GR.UTF-8
export LC_PAPER=el_GR.UTF-8
export LC_NAME=el_GR.UTF-8
export LC_ADDRESS=el_GR.UTF-8
export LC_TELEPHONE=el_GR.UTF-8
export LC_IDENTIFICATION=el_GR.UTF-8
export LC_ALL=en_US.UTF-8

# Preferred editor for local and remote sessions
if [[ -n $SSH_CONNECTION ]]; then
  export EDITOR='vim'
else
  export EDITOR='nvim'
fi

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
export PYENV_ROOT="$HOME/.pyenv"
command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

#eval "$(thefuck --alias)"

alias composer81="/usr/bin/php81 /usr/lib/composer.phar"
alias composer82="/usr/bin/php82 /usr/lib/composer.phar"
alias composer83="/usr/bin/php83 /usr/lib/composer.phar"
alias composer74="/usr/bin/php74 /usr/lib/composer.phar"

if tty -s && command -v tmux &> /dev/null && [ -n "$PS1" ] && [[ ! "$TERM" =~ screen ]] && [[ ! "$TERM" =~ tmux ]] && [ -z "$TMUX" ]; then
    parent_pid=$(ps -o ppid= -p $$ | tr -d '[:space:]')
	parent_process_name=$(ps -o comm= -p $parent_pid | tr -d '[:space:]')
	session_name="$parent_process_name-$parent_pid-zrc"
	exec tmux new-session -A -s "$session_name"
fi

alias ssh='TERM=xterm-256color ssh'
alias k=kubectl
alias vim=nvim
alias vi=nvim
alias yless="jless --yaml"
fpath=(/home/yorgaraz/.oh-my-zsh/custom/completions /home/yorgaraz/.oh-my-zsh/plugins/history-substring-search /home/yorgaraz/.oh-my-zsh/plugins/zsh-navigation-tools /home/yorgaraz/.oh-my-zsh/plugins/zsh-interactive-cd /home/yorgaraz/.oh-my-zsh/plugins/tmux /home/yorgaraz/.oh-my-zsh/plugins/sudo /home/yorgaraz/.oh-my-zsh/plugins/systemd /home/yorgaraz/.oh-my-zsh/plugins/thefuck /home/yorgaraz/.oh-my-zsh/plugins/pyenv /home/yorgaraz/.oh-my-zsh/plugins/python /home/yorgaraz/.oh-my-zsh/plugins/pip /home/yorgaraz/.oh-my-zsh/plugins/nvm /home/yorgaraz/.oh-my-zsh/plugins/kubectl /home/yorgaraz/.oh-my-zsh/plugins/git-flow-avh /home/yorgaraz/.oh-my-zsh/plugins/golang /home/yorgaraz/.oh-my-zsh/plugins/history /home/yorgaraz/.oh-my-zsh/plugins/kubectx /home/yorgaraz/.oh-my-zsh/plugins/1password /home/yorgaraz/.oh-my-zsh/plugins/archlinux /home/yorgaraz/.oh-my-zsh/plugins/cp /home/yorgaraz/.oh-my-zsh/plugins/npm /home/yorgaraz/.oh-my-zsh/plugins/git /home/yorgaraz/.oh-my-zsh/functions /home/yorgaraz/.oh-my-zsh/completions /home/yorgaraz/.oh-my-zsh/cache/completions /usr/local/share/zsh/site-functions /usr/share/zsh/site-functions /usr/share/zsh/functions/Calendar /usr/share/zsh/functions/Chpwd /usr/share/zsh/functions/Completion /usr/share/zsh/functions/Completion/Base /usr/share/zsh/functions/Completion/Linux /usr/share/zsh/functions/Completion/Unix /usr/share/zsh/functions/Completion/X /usr/share/zsh/functions/Completion/Zsh /usr/share/zsh/functions/Exceptions /usr/share/zsh/functions/MIME /usr/share/zsh/functions/Math /usr/share/zsh/functions/Misc /usr/share/zsh/functions/Newuser /usr/share/zsh/functions/Prompts /usr/share/zsh/functions/TCP /usr/share/zsh/functions/VCS_Info /usr/share/zsh/functions/VCS_Info/Backends /usr/share/zsh/functions/Zftp /usr/share/zsh/functions/Zle)

export PATH="$PATH:~/bin"
export PATH="${KREW_ROOT:-$HOME/.krew}/bin:$PATH"
export PATH="~/dev_setup/maven/apache-maven-3.9.6/bin/:$PATH"

if [ -f ~/.zsh_nocorrect ]; then
    while read -r COMMAND; do
        alias $COMMAND="nocorrect $COMMAND"
    done < ~/.zsh_nocorrect
fi

alias yamlparse="python3 -c 'import sys, yaml, json; json.dump(yaml.load(sys.stdin, Loader=yaml.FullLoader), sys.stdout, indent=4)'"

# start sunshine in a different tmux session if it's not already running
#tmux has-session -t sunshine || tmux new-session -d -s sunshine 'sunshine'
# Shell-GPT integration ZSH v0.1
_sgpt_zsh() {
if [[ -n "$BUFFER" ]]; then
    _sgpt_prev_cmd=$BUFFER
    BUFFER+="⌛"
    zle -I && zle redisplay
    BUFFER=$(sgpt --shell <<< "$_sgpt_prev_cmd")
    zle end-of-line
fi
}
zle -N _sgpt_zsh
bindkey ^l _sgpt_zsh
# Shell-GPT integration ZSH v0.1
eval "$(zoxide init zsh --cmd cd)"

# pnpm
export PNPM_HOME="/home/yorgaraz/.local/share/pnpm"
case ":$PATH:" in
  *":$PNPM_HOME:"*) ;;
  *) export PATH="$PNPM_HOME:$PATH" ;;
esac
# pnpm end

bwu() {
    export BW_SESSION=$(bw unlock --raw --passwordfile ~/.bw_master_password)
    echo "Vault unlocked."
}

# Starts a secure, interactive shell, with optional support for private images.
# Usage: k-shell [image_name] [secret_name]
k-shell() {
  # Set defaults for optional arguments
  local image_name="${1:-busybox}"
  local secret_name="$2"

  # --- Standard variables ---
  local pod_name="shell-$(date +%s)"
  local current_uid=$(id -u)
  local current_gid=$(id -g)

  # --- Conditionally build the imagePullSecrets JSON ---
  local pull_secret_json=""
  if [ -n "$secret_name" ]; then
    pull_secret_json='"imagePullSecrets": [{ "name": "'"$secret_name"'" }],'
    echo "🔐 Using image pull secret: $secret_name"
  fi

  echo "🚀 Starting interactive shell for UID/GID ${current_uid}/${current_gid}..."

  # Run the pod, inserting the pull secret JSON if it was generated
  kubectl run "$pod_name" \
    --image="$image_name" \
    -i --rm --tty \
    --overrides='{
      "spec": {
        '"$pull_secret_json"'
        "securityContext": {
          "runAsNonRoot": true,
          "runAsUser": '"$current_uid"',
          "runAsGroup": '"$current_gid"',
          "seccompProfile": { "type": "RuntimeDefault" }
        },
        "containers": [
          {
            "name": "'"$pod_name"'",
            "image": "'"$image_name"'",
            "stdin": true,
            "tty": true,
            "securityContext": { "allowPrivilegeEscalation": false, "capabilities": { "drop": ["ALL"] }}
          }
        ]
      }
    }' \
    --command -- /bin/sh
}

alias lutris="PYENV_VERSION=system lutris"

alias knch="kubectl config set-context --current --namespace"

# bun completions
[ -s "/home/yorgaraz/.oh-my-zsh/completions/_bun" ] && source "/home/yorgaraz/.oh-my-zsh/completions/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"
export PATH="$HOME/.npm-global/v23.9.0/lib/bin:$PATH"
#export DISPLAY=:1

# opencode
export PATH=/home/yorgaraz/.opencode/bin:$PATH

# Added by LM Studio CLI (lms)
export PATH="$PATH:/home/yorgaraz/.lmstudio/bin"
# End of LM Studio CLI section

