# safe-code

`safe-code` provides command-line launchers for running AI coding tools with
limited access to the host filesystem, using
[Bubblewrap](https://github.com/containers/bubblewrap) on Linux.

The launchers include **safe-opencode**, for OpenCode, and
**safe-claudecode**, for Claude Code.

This may be useful on shared or managed cluster systems where Docker is disabled
or unavailable, and where users have few other practical isolation tools. It
does not require a Docker daemon, but it does require Bubblewrap to be installed
and permitted by the system.

## Security scope

This project has **not undergone an extensive security audit**. Its settings
provide some hardening compared with running an AI coding tool without
filesystem isolation, but this should not be treated as a security boundary or
as protection for highly sensitive data.

The launcher limits which host paths are visible to the tool and can make the
project read-only by default. However, network access remains available in the
host network namespace, and the level of isolation depends on the host kernel,
Bubblewrap configuration, and local system policy. A writable mount gives the
tool permission to change files in that mounted directory. `--device` exposes
the selected host device to the tool.

Use only on systems and with data for which you are authorized. Do not mount
credentials, broad home directories, or other sensitive paths. On clusters,
follow local policy and ask administrators whether Bubblewrap and user
namespaces are supported.

## Common requirements

- Linux.
- Bubblewrap (`bwrap`), installed and usable by the current account. The host
  must allow user namespaces or provide an appropriately configured Bubblewrap
  installation.
- `bash`, `realpath`, and `readlink`.

## `safe-opencode`

`safe-opencode` targets OpenCode V2. It requires OpenCode available as
`opencode` in the host `PATH`, or a native binary selected with
`SAFE_OPENCODE_BIN`.

The script creates host OpenCode config and data directories if they do not
exist. Defaults are `${XDG_CONFIG_HOME:-$HOME/.config}/opencode` and
`${XDG_DATA_HOME:-$HOME/.local/share}/opencode`.

### Installation and minimal setup

Choose either installation method below. Bubblewrap must be installed separately
through your system's package manager or administrator. **For filesystem
isolation, the OpenCode server must be launched within `safe-opencode`, not as
a background service on the host.** Both setups disable automatic
background-service startup and stop any existing service.

#### Option 1: curl (no Node.js required)

The installer downloads a self-contained native OpenCode V2 binary:

```bash
curl -fsSL https://opencode.ai/v2/install | bash
# Reopen your shell if needed to update PATH.
opencode --version
opencode service set disabled true
opencode service stop
```

The binary is normally installed at `~/.opencode/bin/opencode`. No Node.js,
pnpm, or executable override is needed when this is the `opencode` in your
`PATH`. If another installation takes precedence, select the curl-installed
binary explicitly:

```bash
export SAFE_OPENCODE_BIN="$HOME/.opencode/bin/opencode"
```

#### Option 2: pnpm

Install a supported [Node.js LTS release](https://nodejs.org/en/download)
and [pnpm](https://pnpm.io/installation), then install OpenCode V2 on the host:

```bash
node --version
pnpm --version
pnpm add -g --allow-build=@opencode/cli @opencode/cli
opencode --version
opencode service set disabled true
opencode service stop
```

If pnpm reports that its global bin directory is missing, run `pnpm setup`
and reopen your shell before installing. The installation's build step installs
and checks the binary; it does not install a running background service.

Point the launcher at the native binary installed by pnpm. Its usual `opencode`
command is a shell shim referring to host paths that are not mounted inside the
sandbox:

```bash
export SAFE_OPENCODE_BIN="$(pnpm root -g)/@opencode/cli/bin/opencode.exe"
```

Despite its `.exe` suffix, this is the native Linux binary on Linux. Set this
variable in each shell where you use the launcher, or add the export to your
shell configuration.

#### Start the server only inside the sandbox

For either installation method, `opencode service set disabled true` disables
automatic background-service startup, and `opencode service stop` stops an
existing service. These setup commands run on the host; launch actual sessions
only through `safe-opencode` for isolation.

Configure your provider and start a session from this repository:

```bash
./safe-opencode /path/to/project -- --standalone
```

Use `/connect` in the sandboxed terminal interface to configure your model
provider. `--standalone` explicitly starts a private server inside the same
Bubblewrap environment instead of connecting to or starting the host background
service. OpenCode V2 binds this server to `127.0.0.1` on an automatically assigned
port, generates an authentication password, and stops it when the client exits.
No separate `opencode serve`, port configuration, or password setup is needed.

**Outbound network access stays enabled**, including access to remote model
providers; a local model is not required. Binding the server to localhost limits
inbound exposure, not outbound requests. The launcher shares the host network
namespace, so localhost is still reachable by other local users and processes.
Do not use `--server` to connect to a host service or start
`serve --service` inside the sandbox for this workflow.

Provider credentials are stored in OpenCode's data directory, which the launcher
exposes read/write to OpenCode. If you override `--config-dir` or
`SAFE_OPENCODE_CONFIG`, the default host service settings are not mounted;
keep using `--standalone` explicitly.

### Usage

By default, the project is mounted read-only at `/workspace`. OpenCode
configuration and data are writable and persist on the host. The host home
directory is not mounted.

```bash
./safe-opencode /path/to/project -- --standalone
./safe-opencode --rw /path/to/project -- --standalone
```

Additional host directories can be exposed explicitly. Each path must already
exist and be a directory; the script prints its sandbox path before starting
OpenCode.

```bash
./safe-opencode \
  --mount-ro /data/reference \
  --mount-rw /data/results \
  --python-env "$HOME/.venvs/project" \
  /path/to/project -- --standalone
```

GPU compute devices can be exposed individually. OpenCL setups may use `/dev/dri`;
CUDA setups commonly need the NVIDIA device nodes present on the host:

```bash
./safe-opencode --device /dev/dri /path/to/project -- --standalone

./safe-opencode \
  --device /dev/nvidia0 \
  --device /dev/nvidiactl \
  --device /dev/nvidia-uvm \
  /path/to/project -- --standalone
```

Mappings are numbered in the order supplied:

- `--mount-ro PATH` → `/mnt/host-ro/N` (read-only)
- `--mount-rw PATH` → `/mnt/host-rw/N` (read/write)
- `--python-env PATH` → the same absolute path inside the sandbox (read-only,
  with its `bin` directory prepended to `PATH`)
- `--device PATH` → the same path under `/dev` (device access)

Device paths must resolve to a character device or directory under `/dev`.
Check the device names and permissions on your system; exposing a device grants
the sandbox access to it.

The optional Python environment must contain an executable `bin/python`.
Package installation into that environment will not work because it is mounted
read-only. A virtual environment that depends on other host paths not mounted
into the sandbox may not work.

### Options

- `--rw`: make the project mount writable.
- `--mount-ro PATH`: expose an additional read-only directory.
- `--mount-rw PATH`: expose an additional read/write directory.
- `--device PATH`: expose a character device or directory under `/dev`.
- `--python-env PATH`: expose a Python environment read-only.
- `--config-dir PATH` / `--data-dir PATH`: override host OpenCode config/data
  directories.
- `--bwrap PATH`: select a Bubblewrap executable.
- `--no-service-check`: skip the OpenCode service status check.
- `--resume` / `--resume ID`: continue the latest or a selected session.

Config, data, and Bubblewrap paths can also be set with `SAFE_OPENCODE_CONFIG`,
`SAFE_OPENCODE_DATA`, and `SAFE_OPENCODE_BWRAP`.
Set `SAFE_OPENCODE_BIN` to select a native OpenCode executable instead of the
`opencode` command in `PATH`.

The launcher prints a short environment summary that can be pasted into the
opening prompt. For example:

> You are working in a Linux sandbox. The project is at `/workspace`.
> Read reference files from `/mnt/host-ro/1` and save generated results under
> `/mnt/host-rw/1`. Use the configured Python environment's `bin/python`; it is
> read-only. Do not assume other host files or paths are available.

This context is informational only; it is not automatically sent to the model.

## `safe-claudecode`

`safe-claudecode` runs Claude Code inside Bubblewrap. It requires the native
Claude Code executable (`claude`) in `PATH`, or a native executable selected
with `SAFE_CLAUDECODE_BIN`. The host home directory is not mounted. Claude Code
configuration and authentication state are instead persisted in
`$HOME/.claude`, mounted read/write in the sandbox.

### Installation

Install Bubblewrap separately through your system's package manager or ask your
administrator to install it. Install Claude Code using Anthropic's recommended
Linux native installer:

```bash
curl -fsSL https://claude.ai/install.sh | bash
# Reopen your shell if needed to update PATH.
claude --version
```

If `claude` is not found in `PATH`, or you want to select a specific native
installation, set `SAFE_CLAUDECODE_BIN` to its executable path. The deprecated
global npm installation is not recommended here.

Start Claude Code from a project directory:

```bash
./safe-claudecode /path/to/project
```

The project is mounted read-only by default. Use `--rw` when Claude Code needs
to edit project files:

```bash
./safe-claudecode --rw /path/to/project
```

The launcher sets `CLAUDE_CONFIG_DIR` to the mounted config directory so Claude
Code's settings, sessions, and authentication state persist there. Sign in
inside the sandbox on first use if needed. The config directory is writable by
Claude Code and should be treated as sensitive. You can choose a separate
directory with `--config-dir PATH` or `SAFE_CLAUDECODE_CONFIG`.

Additional host directories can be exposed explicitly:

```bash
./safe-claudecode \
  --mount-ro /data/reference \
  --mount-rw /data/results \
  --rw /path/to/project
```

Use `--device PATH` to expose an individual character device or directory from
`/dev` inside the sandbox. For example, CUDA workloads commonly need several
NVIDIA devices, while OpenCL setups may use `/dev/dri`:

```bash
./safe-claudecode --rw \
  --device /dev/nvidia0 \
  --device /dev/nvidiactl \
  --device /dev/nvidia-uvm \
  /path/to/project
```

Only devices or directories whose resolved host paths are under `/dev` are
accepted. Check your system's device names and permissions; exposing a device
grants the sandbox access to it.

Mappings, `--path`, `--library-path`, `--device`, `--python-env`, `--bwrap`,
`--resume`, and `--dry-run` work like their `safe-opencode` counterparts. Use
`--` before Claude Code arguments when needed:

```bash
./safe-claudecode --rw /path/to/project -- --resume
```

The sandbox keeps outbound network access enabled for Claude Code and shares
the host network namespace. A read/write project or mount lets Claude Code
change those files. This is lightweight filesystem isolation, not a complete
security boundary or protection for sensitive data.

### Persistent links and project-relative mounts (both launchers)

Use `--link-ro HOST_PATH TARGET` or `--link-rw HOST_PATH TARGET` to create a
persistent symlink inside the selected project and expose the source at its
canonical absolute host path inside Bubblewrap:

```bash
./safe-opencode --rw --link-ro /data/reference reference /path/to/project -- --standalone
./safe-claudecode --rw --link-rw /data/results results /path/to/project
```

For example, `/path/to/project/reference` points to `/data/reference` on the
host, and `/workspace/reference` resolves to the same mounted path in the
sandbox. `HOST_PATH` must be an existing directory separate from the project;
source symlinks are resolved before mounting. `TARGET` must be project-relative
and its parent directory must already exist inside the project. An existing
target is accepted only if it is a symlink resolving to the same source.
Files, directories, dangling symlinks, and links to other sources are never
replaced. Multiple links can share a source with the same access mode.

**Both link switches require `--rw`**, even when reusing an existing correct
symlink. **These links persist on the host**, including with `--dry-run`;
creating them requires host write permission on their parent directories.
The `ro`/`rw` choice applies only inside the sandbox, not when following the
link on the host. Source paths that conflict with built-in sandbox mounts
(such as `/usr` or `/tmp`) are rejected.

For a sandbox-only mapping without creating a host symlink, supply a relative
destination to the existing `--mount-ro` or `--mount-rw` option:

```bash
mkdir -p /path/to/project/reference /path/to/project/results
./safe-opencode --rw \
  --mount-ro /data/reference=./reference \
  --mount-rw /data/results=./results \
  /path/to/project -- --standalone
```

This mounts at `/workspace/reference` and `/workspace/results`, respectively.
Relative destinations are relative to `PROJECT_DIR`, not the caller's current
directory, and must stay inside the project. Mountpoint directories must already
exist and their paths must not contain symlinks. No host files or directories are
created or replaced by these mappings; existing contents are hidden only inside
the sandbox. Both read-only and read/write mounts below `/workspace` require
`--rw`, including absolute destinations such as `/workspace/reference`.
With the default read-only project, both link switches and all mounts below
`/workspace` are rejected before creating links or starting Bubblewrap.

Absolute destinations continue to work, for example
`--mount-ro /data/reference=/reference`. Named destinations must not overlap
one another or replace built-in sandbox mounts; existing directories below
`/workspace` are the exception to the built-in-path restriction.
Use `=./mydata` for a project-relative mount, not `=~/mydata`: `~` is shell
home-directory syntax, not a working-directory marker, and the launcher does
not expand it.

## Testing

Run test scripts
```
python3 -m unittest discover -s tests -v
```

## Limitations

- Bubblewrap is Linux-only; this script does not support macOS or Windows.
- Docker is not required, but Bubblewrap and compatible kernel/system
  configuration are.
- Network access is not isolated by the script.
- This is a lightweight, custom hardening tool, not a complete sandbox,
  container replacement, or independently audited security product.
- Some clusters restrict Bubblewrap or user namespaces. Do not try to bypass
  those restrictions; follow local policy.
