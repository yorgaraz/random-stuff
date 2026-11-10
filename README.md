# What is this?

This is a simple bucket list of my personal configs, scripts, and random tools I use on a daily basis. If you find something useful, feel free to use it.

## Restore after a wipe

1. `git clone git@github.com:yorgaraz/random-stuff.git ~/dev/personal/random-stuff`
2. `cd ~/dev/personal/random-stuff && ./link.sh --root`
3. Secrets are not in this repo. Restore them from Bitwarden.

## Add a config

1. Move it into `configs/<app>/`.
2. Add one line to `links.conf`.
3. Run `./link.sh` (`-n` for a dry run).
