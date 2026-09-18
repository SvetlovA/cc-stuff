"""Agent CLIs: finding them, reading their help, and launching them.

Nothing in this package is a closed list of blessed CLIs, and nothing in it is a
snapshot of anybody's model lineup. Both go stale the week they are written,
and the cost is paid by the user: a partner they cannot start, or a model
they are not offered because this code has not been edited since it shipped.

So the sources of truth live outside this code:

  * CLIs come from a scan of the directories this machine installs them into
    -- PATH plus the per-user bin dirs npm/bun/cargo/pipx/winget use. Naming
    a binary the scan never surfaced works too; the scan suggests, it does
    not gate.
  * Launch flags come from the CLI's own --help whenever there is no recipe
    for it, so an unknown CLI is drivable the first time it is named.
  * Model ids come from the CLI itself (a list command, its help, its own
    bundle) and from the user's config for it -- never from a table here.
  * The only claim this package still makes about a model is what its *name
    shape* implies: an "opus"/"pro"/"max" tier reasons deeper and slower than
    a "flash"/"mini"/"lite" one. That stays true for models not yet released,
    which is the whole reason it is phrased as a shape and not as an id.
"""
