# Build from a tarball of the linux/ directory (see README, "Install on Fedora"):
#   git archive --prefix=keyflow-%{version}/ -o ~/rpmbuild/SOURCES/keyflow-%{version}.tar.gz HEAD:linux
Name:           keyflow
Version:        1.0.0
Release:        1%{?dist}
Summary:        System-wide text expander (Linux port of KeyFlowMac)

# The repository has no LICENSE file yet; update once it does.
License:        LicenseRef-Fedora-Illegal-Unspecified
URL:            https://github.com/shingelion/KeyFlowMac
Source0:        keyflow-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros
# Needed by %%check: libxkbcommon and the layout data it compiles keymaps from.
BuildRequires:  libxkbcommon
BuildRequires:  xkeyboard-config

Requires:       libxkbcommon
Requires:       xkeyboard-config
Recommends:     wl-clipboard
Recommends:     xclip
Suggests:       setxkbmap

%description
KeyFlow replaces short triggers such as ";addr" with full snippets in any application, on
X11 and Wayland. It reads keystrokes from /dev/input and types the expansion through
/dev/uinput using your own keyboard layout. Reading keyboards requires membership of the
"input" group; see /usr/share/doc/keyflow/README.md.

%prep
%autosetup -n keyflow-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files keyflow

install -Dpm 0644 packaging/keyflow.service %{buildroot}%{_userunitdir}/keyflow.service
install -Dpm 0644 packaging/70-keyflow-uinput.rules %{buildroot}%{_udevrulesdir}/70-keyflow-uinput.rules
install -Dpm 0644 packaging/keyflow-modules-load.conf %{buildroot}%{_modulesloaddir}/keyflow.conf

%check
%{python3} -m unittest discover -s tests -t .

%post
%udev_rules_update
cat <<'MSG'
KeyFlow needs, once:
  sudo usermod -aG input $USER          (then log out and back in)
  sudo modprobe uinput
  sudo udevadm trigger /dev/uinput
then:
  systemctl --user enable --now keyflow
  keyflow doctor
MSG

%postun
%udev_rules_update

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/keyflow
%{_userunitdir}/keyflow.service
%{_udevrulesdir}/70-keyflow-uinput.rules
%{_modulesloaddir}/keyflow.conf

%changelog
* Sat Nov 14 2026 KeyFlow Maintainers <noreply@example.com> - 1.0.0-1
- Initial Fedora package
