#!/bin/sh
set -eu

option_prefix=@rit-navigation
previous_table=rit-navigation-previous
metadata='#{?key_repeat,-r ,}#{?#{!=:#{key_note},},-N #{q|a:key_note} ,}'

binding() {
    tmux list-keys -F "#{?#{&&:#{==:#{key_table},$1},#{==:#{key_string},$2}},$3,}"
}

server_option() {
    tmux show-options -sqv "$1"
}

active() {
    pane=$1
    marker=$(tmux show-options -pqv -t "$pane" @rit_nav)
    pid=${marker%% *}
    started=${marker#* }
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    [ "$pid" -gt 1 ] && [ "$started" != "$marker" ] || return 1
    [ "$(tmux display-message -p -t "$pane" '#{pane_in_mode}')" = 0 ] || return 1
    pane_tty=$(tmux display-message -p -t "$pane" '#{pane_tty}')
    process=$(LC_ALL=C ps -p "$pid" -o tty= -o pgid= -o tpgid= -o stat= -o lstart=) || return 1
    set -f
    set -- $process
    [ "$#" -ge 9 ] || return 1
    [ "$1" = "${pane_tty#/dev/}" ] && [ "$2" = "$3" ] || return 1
    case "$4" in *T*|*t*|*Z*|*X*) return 1 ;; esac
    shift 4
    [ "$*" = "$started" ]
}

uninstall() {
    tmux set-option -squ "$option_prefix-enabled"
    for key in C-h C-l; do
        slot="$option_prefix-$key"
        owned=$(server_option "$slot-owned")
        current=$(binding root "$key" "bind-key $metadata-T root $key #{key_command}")
        [ -n "$owned" ] && [ "$current" = "$owned" ] || continue
        original=$(server_option "$slot-original")
        if [ -n "$original" ]; then
            printf '%s\n' "$original" | tmux source-file -
        else
            tmux unbind-key -qn "$key"
        fi
        tmux unbind-key -qT "$previous_table" "$key"
        tmux set-option -squ "$slot-original"
        tmux set-option -squ "$slot-owned"
    done
    tmux set-option -squ "$option_prefix-command"
}

install() {
    version=$(tmux -V)
    version=${version#tmux }
    major=${version%%.*}
    minor=${version#*.}
    minor=${minor%%[!0-9]*}
    case "$major:$minor" in *[!0-9:]*|:*|*:) echo 'rit navigation requires tmux 3.7+'; exit 1 ;; esac
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 7 ]; }; then
        echo 'rit navigation requires tmux 3.7+'
        exit 1
    fi

    for key in C-h C-l; do
        slot="$option_prefix-$key"
        current=$(binding root "$key" "bind-key $metadata-T root $key #{key_command}")
        owned=$(server_option "$slot-owned")
        if [ -n "$owned" ] && [ "$current" != "$owned" ] && [ "$current" != "$(server_option "$slot-original")" ]; then
            echo "rit navigation: $key changed; uninstall rit before changing navigation bindings"
            exit 1
        fi
        if [ -z "$owned" ] && [ -n "$(binding "$previous_table" "$key" '#{key_command}')" ]; then
            echo "rit navigation: $previous_table $key is already in use"
            exit 1
        fi
    done

    directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    script="$directory/$(basename -- "$0")"
    quoted_script=$(printf '%s' "$script" | awk '{gsub(/\047/, "\047\\\047\047"); printf "\047%s\047", $0}')
    tmux set-option -s "$option_prefix-command" "$quoted_script active"
    check='#{@rit-navigation-command} #{q:pane_id}'
    condition='#{&&:#{==:#{@rit-navigation-enabled},1},#{&&:#{!=:#{@rit_nav},},#{!:#{pane_in_mode}}}}'

    for key in C-h C-l; do
        slot="$option_prefix-$key"
        current=$(binding root "$key" "bind-key $metadata-T root $key #{key_command}")
        owned=$(server_option "$slot-owned")
        if [ -n "$owned" ] && [ "$current" = "$owned" ]; then
            continue
        fi
        previous=$(binding root "$key" "bind-key $metadata-T $previous_table $key switch-client -T root \\; #{key_command}")
        previous=${previous:-bind-key -T $previous_table $key send-keys $key}
        printf '%s\n' "$previous" | tmux source-file -
        prefix=$(binding root "$key" "bind-key $metadata-T root $key")
        prefix=${prefix:-bind-key -T root $key}
        fallback="switch-client -T $previous_table ; send-keys -K $key"
        send="send-keys $key"
        if [ "$key" = C-h ]; then
            send='send-keys -H 1b 5b 31 30 34 3b 35 75'
        fi
        tmux set-option -s "$slot-original" "$current"
        printf '%s\n' "$prefix if-shell -F '$condition' { if-shell '$check' { $send } { $fallback } } { $fallback }" | tmux source-file -
        owned=$(binding root "$key" "bind-key $metadata-T root $key #{key_command}")
        tmux set-option -s "$slot-owned" "$owned"
    done
    tmux set-option -s "$option_prefix-enabled" 1
}

case "${1:-install}" in
    install) install ;;
    uninstall) uninstall ;;
    active) [ "$#" = 2 ] && active "$2" ;;
    *) echo 'usage: rit.tmux [install|uninstall|active PANE]' >&2; exit 2 ;;
esac
