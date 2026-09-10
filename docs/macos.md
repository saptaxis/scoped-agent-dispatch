# macOS

There is no native Docker on macOS, so scad runs containers in a
[Colima](https://github.com/abiosoft/colima) VM it owns, under the profile name
`scad`, isolated from any other Docker on the machine. `install.sh` installs
Colima via Homebrew if needed and creates the profile. Pass `--no-vm` to skip
that and wire it up yourself.

Docker Desktop and Podman are not supported targets.

```bash
scad vm start      # start, creating it on first run
scad vm status     # is scad's Docker daemon reachable
scad vm info       # sizing, socket, extra mounts
scad vm stop       # stop; containers are preserved
scad vm delete     # destroy the VM and everything in it
```

`build`, `run start`, `dispatch` and `batch` start the VM if it is down, so
`scad vm start` is rarely needed by hand.

## Sizing

Set in `~/.scad/settings.yml`, defaults shown:

```yaml
colima:
  cpu: 2
  memory: 4              # GiB
  disk: 60               # GiB
  vm_type: vz            # "qemu" on macOS 12 or older
  mount_type: virtiofs   # "sshfs" on macOS 12 or older
```

Sizing applies when the VM is created. To resize: `scad vm delete && scad vm start`.

## Mounts

Host paths outside `$HOME` (external drives, `/Volumes/...`, `/data`) are not
visible to the VM by default. At `run start`, scad adds any such `mounts:` or
repo paths to the VM and restarts it, but only when the set has changed.

`scad code add` of a path outside `$HOME` cannot hot-add, because a VM mount is
only addable at restart. scad warns and offers to restart (`--restart-vm` skips
the prompt). The restart stops running sessions, and scad restarts the target
session afterwards.

## GPU

`gpu: true` is unsupported on macOS and errors, since there is no NVIDIA runtime
in a Lima VM. GPU passthrough is Linux only.
