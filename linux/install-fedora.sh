#!/usr/bin/env bash
# KeyFlow installer for Fedora (and other RPM-based distributions).
#
#   ./install-fedora.sh              build the RPM from this checkout, install it, set everything up
#   ./install-fedora.sh --uninstall  remove KeyFlow (your shortcuts in ~/.config/keyflow are kept)
#   ./install-fedora.sh --build-only build keyflow-*.noarch.rpm into the current directory and stop
#   ./install-fedora.sh --dry-run    print what would be done without changing anything
#
# Run it as your normal user: it asks for your sudo password only for the steps that need it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC="$SCRIPT_DIR/packaging/fedora/keyflow.spec"
MODE=install
DRY=0
FORCE=0

usage() { sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
run()  { printf '+ %s\n' "$*"; if [ "$DRY" = 0 ]; then "$@"; fi; }

for arg in "$@"; do
    case "$arg" in
        --uninstall)  MODE=uninstall ;;
        --build-only) MODE=build ;;
        --dry-run)    DRY=1 ;;
        --force)      FORCE=1 ;;  # skip the "is this Fedora?" check
        -h|--help)    usage; exit 0 ;;
        *) usage >&2; die "unknown option: $arg" ;;
    esac
done

# -- sanity checks ------------------------------------------------------------------------------

[ "$(id -u)" -ne 0 ] || die "run this as your normal user, not root (it uses sudo where needed)"

if [ "$FORCE" = 0 ]; then
    # shellcheck disable=SC1091
    . /etc/os-release 2>/dev/null || true
    case " ${ID:-} ${ID_LIKE:-} " in
        *" fedora "*|*" rhel "*) ;;
        *) die "this installer is for Fedora/RHEL-family systems (found: ${PRETTY_NAME:-unknown}). Use --force to try anyway." ;;
    esac
fi
if [ "$DRY" = 0 ]; then
    command -v dnf >/dev/null || die "dnf not found"
    command -v sudo >/dev/null || die "sudo not found"
fi

in_group_db()      { id -nG "$USER" | tr ' ' '\n' | grep -qx input; }   # saved on the system
in_group_session() { id -nG | tr ' ' '\n' | grep -qx input; }           # active in this login

# -- uninstall ----------------------------------------------------------------------------------

if [ "$MODE" = uninstall ]; then
    say "Stopping the service"
    run systemctl --user disable --now keyflow || warn "could not stop the service (maybe it was not running)"
    say "Removing the package"
    run sudo dnf remove -y keyflow
    say "Done"
    echo "Your shortcuts and settings are still in ~/.config/keyflow (delete that folder to remove them)."
    echo "KeyFlow added you to the 'input' group. To undo that too:  sudo gpasswd -d \"$USER\" input"
    exit 0
fi

# -- 1. dependencies ----------------------------------------------------------------------------

[ -f "$SPEC" ] || die "cannot find $SPEC; run this script from inside the KeyFlow 'linux' folder"

if [ "$MODE" = install ]; then
    say "1/5  Installing build and runtime dependencies"
else
    say "1/2  Installing build dependencies"
fi
run sudo dnf install -y rpm-build python3-devel python3-setuptools python3-wheel python3-pip \
    pyproject-rpm-macros systemd-rpm-macros desktop-file-utils \
    libxkbcommon xkeyboard-config python3-evdev python3-gobject gtk4 hicolor-icon-theme

# -- 2. build the RPM -----------------------------------------------------------------------------

say "Building the package"
VERSION="$(sed -n 's/^Version:[[:space:]]*//p' "$SPEC" | head -n1)"
[ -n "$VERSION" ] || die "could not read the version from $SPEC"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/SOURCES"
tar -C "$SCRIPT_DIR" \
    --exclude=__pycache__ --exclude='*.pyc' --exclude=build --exclude=dist --exclude='*.egg-info' \
    --exclude=debian/keyflow --exclude=debian/.debhelper \
    --transform "s,^\.,keyflow-$VERSION," -czf "$WORK/SOURCES/keyflow-$VERSION.tar.gz" .
# shellcheck disable=SC2086  # RPMBUILD_ARGS is deliberately word-split (used by the test-suite)
run rpmbuild -bb ${RPMBUILD_ARGS:-} --define "_topdir $WORK" "$SPEC"

if [ "$DRY" = 1 ]; then
    RPM="$WORK/RPMS/noarch/keyflow-$VERSION-1.noarch.rpm"
else
    RPM="$(find "$WORK/RPMS" -name 'keyflow-*.noarch.rpm' | head -n1)"
    [ -n "$RPM" ] || die "the build produced no package; see the messages above"
fi

if [ "$MODE" = build ]; then
    run cp "$RPM" ./
    say "Built $(basename "$RPM") in $(pwd)"
    echo "Install it with:  sudo dnf install ./$(basename "$RPM")"
    exit 0
fi

# -- 3. install -------------------------------------------------------------------------------------

say "2/5  Installing KeyFlow"
if [ "$DRY" = 0 ] && rpm -q keyflow >/dev/null 2>&1 \
   && [ "$(rpm -q --qf '%{VERSION}-%{RELEASE}' keyflow)" = "$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$RPM")" ]; then
    run sudo dnf reinstall -y "$RPM"      # same version rebuilt: dnf install would do nothing
else
    run sudo dnf install -y "$RPM"
fi

# -- 4. one-time system setup -------------------------------------------------------------------------

say "3/5  Allowing KeyFlow to read your keyboard and type"
NEED_RELOGIN=0
if ! in_group_db; then
    run sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
elif ! in_group_session; then
    NEED_RELOGIN=1      # already a member, but this login session predates it
fi
say "4/5  Enabling /dev/uinput"
run sudo modprobe uinput
run sudo udevadm control --reload
run sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput

# -- 5. service -------------------------------------------------------------------------------------------

say "5/5  Setting up the background service"
run systemctl --user daemon-reload || true
if [ "$NEED_RELOGIN" = 1 ]; then
    # Starting now would just fail: this login session cannot read the keyboard yet.
    run systemctl --user enable keyflow || warn "could not enable the service; after logging in again run: systemctl --user enable --now keyflow"
else
    run systemctl --user enable --now keyflow || warn "could not start the service; run: systemctl --user enable --now keyflow"
fi

# -- done --------------------------------------------------------------------------------------------------

say "KeyFlow is installed"
if [ "$NEED_RELOGIN" = 1 ]; then
    cat <<MSG
One thing left: ${USER} was just added to the 'input' group, which only takes effect on a new login.

    1. Log out and log back in (or reboot).
    2. Open a terminal and run:   keyflow doctor      (everything should say [ok])
    3. Open KeyFlow from your applications menu, or run:   keyflow gui

The service starts by itself at login.
MSG
else
    if [ "$DRY" = 0 ]; then
        sleep 1
        keyflow doctor || true
    fi
    cat <<'MSG'

Open KeyFlow from your applications menu, or run:   keyflow gui
If 'keyflow doctor' above shows any [FAIL], see INSTALL-FEDORA.md ("Troubleshooting").
MSG
fi
