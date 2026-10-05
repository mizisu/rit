# rit tmux navigation

A standalone TPM plugin for **Ctrl+h/l** navigation inside rit. Other panes keep
their existing bindings, including Vim-aware navigation. No Vim plugin is required.

## Requirements

- tmux **3.7+** on macOS or Linux.
- A POSIX shell, `awk`, and `ps` with `tty`, `pgid`, `tpgid`, `stat`, and `lstart`
  fields. On Linux, install procps/procps-ng; BusyBox `ps` alone is insufficient.
- A rit build with the `@rit_nav` pane-marker integration, installed separately.
  Older builds without this integration will not activate rit navigation.

The application-side integration is not included in this branch. Publishing this
plugin does not publish or install the corresponding rit application changes.

## Install with TPM

Load rit after any plugins or commands that bind Ctrl+h/l, before TPM's loader:

```tmux
set -g @plugin 'tmux-plugins/tpm'
# Your other plugins go here.
set -g @plugin 'mizisu/rit#tmux'
run '~/.tmux/plugins/tpm/tpm'
```

Press `prefix + I`, then restart rit. This orphan branch contains only the plugin,
this README, and LICENSE. TPM clones the branch without the application history;
it cannot select individual files from another branch.

## Behavior and removal

Only root Ctrl+h/l bindings are wrapped. Original commands, notes, and repeat
flags are preserved. Prefix and copy-mode bindings are unchanged. Backspace is
not remapped. These keys move focus inside rit, not between tmux panes.

rit restores its pane marker on exit or suspension. The plugin checks process
identity, foreground status, and TTY before forwarding, rejecting stale markers
after a crash. No personal paths or machine-specific settings are required.

Reloading the same configuration is safe. Before changing another plugin's
Ctrl+h/l bindings, uninstall rit's bindings, change the configuration, then load
rit last. Unexpected rebinding is rejected to avoid delegation cycles. Reload
from one client at a time; concurrent configuration reloads are unsupported.

Before deleting the plugin or removing its TPM entry, run inside the intended
tmux server:

```sh
~/.tmux/plugins/rit/rit.tmux uninstall
```

This restores bindings only where rit still owns them. Newer bindings are left
alone; their saved fallback is retained in case another plugin references it.
rit forwarding is disabled in either case. Do not edit `@rit_nav`, the server
options `@rit-navigation-*`, or the `rit-navigation-previous` key table manually.
Custom key-table workflows and read-only clients are outside this guarantee.

## Validation

Validated with tmux 3.7c on macOS and Alpine Linux (ARM64). The Linux checks use
procps-ng 4.0.5 and a UTF-8 locale. Tests cover binding preservation, repeat flags,
reload/removal, copy mode, multiple clients, and stale/stopped process markers.

## License

MIT. See [LICENSE](LICENSE).
